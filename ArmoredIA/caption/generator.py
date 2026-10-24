from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, replace
from typing import Any

from ..providers.base import AIProvider, AIProviderError
from ..providers.gemini import GeminiProvider
from ..providers.ollama import OllamaProvider
from .selector import (
    CaptionCandidateEvaluation,
    CaptionGenerationError,
    CaptionSelection,
    CaptionSelector,
)


def _configured_provider() -> AIProvider:
    name = (os.getenv("ARMORED_IA_PROVIDER") or "ollama").strip().casefold()
    if name == "gemini":
        return GeminiProvider()
    if name == "ollama":
        return OllamaProvider()
    raise AIProviderError(
        f"ARMORED_IA_PROVIDER inválido: {name!r}; use 'gemini' ou 'ollama'"
    )


@dataclass(frozen=True)
class CaptionGenerationResult:
    caption: str
    selection: CaptionSelection
    evaluations: tuple[CaptionCandidateEvaluation, ...]
    batch_id: str
    provider_requests: int = 1
    feedback_rounds: tuple[tuple[str, ...], ...] = ()
    provider_attempts: tuple[dict[str, Any], ...] = ()


class CaptionGenerator:
    """One AI-provider request -> all candidates -> local Policy/ranking."""

    def __init__(self, provider: AIProvider | None = None, selector: CaptionSelector | None = None):
        self.provider = provider or _configured_provider()
        self.selector = selector or CaptionSelector()

    def generate_with_evidence(
        self,
        product_context: dict[str, Any],
    ) -> CaptionGenerationResult:
        if os.getenv("ARMORED_IA_CAPTION_ENABLED", "1") != "1":
            raise CaptionGenerationError("gerador de legenda desativado")

        provider_configuration = getattr(self.provider, "configuration", None)
        provider_name = ""
        if callable(provider_configuration):
            try:
                provider_name = str((provider_configuration() or {}).get("provider") or "").casefold()
            except Exception:
                provider_name = ""
        candidate_setting = (
            "ARMORED_IA_OLLAMA_MAX_CANDIDATES"
            if provider_name == "ollama"
            else "ARMORED_IA_MAX_CANDIDATES"
        )
        default_candidate_limit = "1" if provider_name == "ollama" else "3"
        model_limit = max(1, int(os.getenv(candidate_setting, default_candidate_limit)))
        try:
            policy_retries = int(os.getenv("ARMORED_IA_POLICY_RETRIES", "1"))
        except ValueError as exc:
            raise CaptionGenerationError("ARMORED_IA_POLICY_RETRIES deve ser inteiro entre 0 e 2") from exc
        if policy_retries < 0 or policy_retries > 2:
            raise CaptionGenerationError("ARMORED_IA_POLICY_RETRIES deve ser inteiro entre 0 e 2")

        batch_id = uuid.uuid4().hex
        all_evaluations: list[CaptionCandidateEvaluation] = []
        feedback: tuple[str, ...] = ()
        feedback_rounds: list[tuple[str, ...]] = []
        provider_attempts: list[dict[str, Any]] = []
        previous_candidates: list[str] = []
        provider_requests = 0
        last_error: CaptionGenerationError | None = None
        candidate_limit = min(model_limit, 10)

        for attempt in range(policy_retries + 1):
            request_context = dict(product_context)
            if feedback:
                request_context["__policy_retry_feedback"] = list(feedback)
                request_context["__policy_retry_previous_candidates"] = list(previous_candidates[-10:])
            try:
                candidates = self.provider.generate_candidates(
                    "caption", request_context, candidate_limit,
                )
            except AIProviderError as exc:
                attempts = [
                    {"policy_round": attempt + 1, **receipt}
                    for receipt in (getattr(self.provider, "last_attempts", ()) or ())
                    if isinstance(receipt, dict)
                ]
                exc.provider_requests = provider_requests + 1
                exc.request_count = max(0, int(getattr(self.provider, "last_request_count", 0) or 0))
                exc.feedback_rounds = tuple(feedback_rounds)
                exc.provider_attempts = tuple((*provider_attempts, *attempts))
                raise
            provider_requests += 1
            for receipt in getattr(self.provider, "last_attempts", ()) or ():
                if isinstance(receipt, dict):
                    provider_attempts.append({"policy_round": attempt + 1, **receipt})
            evaluations = self.selector.evaluate(
                candidates,
                product_name=str(product_context.get("productName") or ""),
                product_context=product_context,
            )
            offset = len(all_evaluations)
            indexed = tuple(
                replace(evaluation, index=offset + evaluation.index)
                for evaluation in evaluations
            )
            try:
                selection, marked = self.selector.choose(evaluations)
            except CaptionGenerationError as exc:
                all_evaluations.extend(indexed)
                last_error = exc
                if attempt >= policy_retries:
                    raise CaptionGenerationError(
                        str(exc), batch_id=batch_id, evaluations=tuple(all_evaluations),
                        provider_requests=provider_requests,
                        feedback_rounds=tuple(feedback_rounds),
                        provider_attempts=tuple(provider_attempts),
                    ) from exc
                rejection_reasons = tuple(dict.fromkeys(
                    evaluation.rejection_reason
                    for evaluation in evaluations
                    if evaluation.rejection_reason
                ))[:4]
                actionable = self._feedback_guidance(
                    rejection_reasons,
                    product_context=product_context,
                )
                feedback = rejection_reasons
                feedback = tuple(dict.fromkeys((*feedback, *actionable)))[:8]
                previous_candidates.extend(
                    evaluation.raw_caption or evaluation.caption
                    for evaluation in evaluations
                    if (evaluation.raw_caption or evaluation.caption).strip()
                )
                feedback_rounds.append(feedback)
                continue

            selected_index = offset + selection.index
            all_evaluations.extend(
                replace(
                    evaluation,
                    index=offset + evaluation.index,
                    selected=(offset + evaluation.index) == selected_index,
                )
                for evaluation in marked
            )
            return CaptionGenerationResult(
                caption=selection.caption,
                selection=replace(selection, index=selected_index),
                evaluations=tuple(all_evaluations),
                batch_id=batch_id,
                provider_requests=provider_requests,
                feedback_rounds=tuple(feedback_rounds),
                provider_attempts=tuple(provider_attempts),
            )

        # The loop either returned a valid selection or raised with its evidence.
        raise last_error or CaptionGenerationError("caption-policy-retry-exhausted", batch_id=batch_id)

    @staticmethod
    def _feedback_guidance(
        reasons: tuple[str, ...],
        *,
        product_context: dict[str, Any] | None = None,
    ) -> tuple[str, ...]:
        """Turn Policy reasons into concise, actionable model feedback."""
        text = " ".join(reasons).casefold()
        guidance: list[str] = []
        if "2 ou 3 palavras" in text or "palavras" in text:
            guidance.append("Reescreva o texto principal com exatamente duas ou três palavras.")
        if "coração" in text or "emoji" in text:
            guidance.append("Não produza emoji; o sistema centraliza o coração laranja na normalização.")
        if "hashtag" in text:
            guidance.append("Use hashtags únicas e relacionadas ao contexto persistido do produto.")
        if "marca" in text or "modelo" in text:
            guidance.append("Retire marcas e modelos do texto principal e das hashtags.")
        if "medida" in text or "técnica" in text:
            guidance.append("Não use números, unidades, medidas ou especificações técnicas.")
        if "distintiva" in text or "produto" in text:
            guidance.append("Evite repetir sequências do título; expresse uma reação curta sem inventar benefícios.")
        if "vazia" in text or "nenhuma candidata" in text:
            guidance.append("Retorne opções não vazias no formato JSON solicitado.")
        if "sem suporte no contexto vision" in text:
            guidance.append(
                "Remova palavras descritivas ausentes do nome, categoria, uso ou descrição Vision; "
                "não invente atributo, benefício ou cenário."
            )
        if "hashtag" in text:
            guidance.append(
                "Prefira uma hashtag simples que esteja literalmente no contexto aprovado; "
                "não concatene palavras com sufixos genéricos nem use marca/modelo."
            )
        context = product_context or {}
        has_richer_context = any(
            context.get(key) not in (None, "", [], {})
            for key in (
                "category", "categoryName", "productCategory", "productType",
                "description", "usage", "useCase", "style", "appearance", "attributes",
            )
        )
        if not has_richer_context and any(
            marker in text for marker in ("contexto vision", "hashtag", "suporte")
        ):
            title = str(context.get("productName") or "")
            terms = re.findall(r"[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ-]*", title)
            stop = {"a", "as", "ao", "aos", "com", "da", "das", "de", "do", "dos", "e", "em", "para", "por", "sem", "um", "uma"}
            meaningful = [term for term in terms if term.casefold() not in stop]
            safe_terms = []
            forbidden = {"ph", "voltagem", "medida", "especificacao", "especificações", "ml", "cm", "mm", "kg", "litros", "volts", "watts"}
            for term in meaningful[2:]:
                folded = term.casefold()
                if len(term) < 4:
                    continue
                if folded in forbidden or any(char.isdigit() for char in term):
                    continue
                if term != term.lower() and term != term.capitalize():
                    continue
                if folded not in {value.casefold() for value in safe_terms}:
                    safe_terms.append(term)
            allowed = ", ".join(safe_terms[:12]) if safe_terms else "nenhum termo seguro disponível"
            guidance.append(
                "O contexto Vision contém apenas o nome do produto: escolha duas ou três "
                "palavras não técnicas que já apareçam nesse nome, sem marca/modelo e sem "
                "usar os dois primeiros termos do título, que podem ser marca/linha/modelo; "
                "não repita uma sequência consecutiva distintiva. Use somente as palavras "
                "seguras listadas abaixo; combine palavras que não sejam adjacentes no título "
                "para não copiar uma expressão do produto. Escolha uma hashtag de uma palavra "
                "dessa mesma lista, sem repetir marca, linha, modelo ou medida. Palavras "
                f"seguras posteriores do título: {allowed}. Para uma frase natural sem "
                "atributos de uso comprovados, combine um termo seguro do produto com uma "
                "reação estética neutra permitida pela Policy, como bonito/bonita, lindo/linda, "
                "charmoso/charmosa, elegante ou especial. Não use alegações de desempenho "
                "como potente, eficaz, ajustado ou equilibrado sem comprovação explícita."
            )
        return tuple(guidance)

    def generate(self, product_context: dict[str, Any]) -> str:
        return self.generate_with_evidence(product_context).caption
