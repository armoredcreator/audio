from __future__ import annotations

import os
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

        model_limit = max(1, int(os.getenv("ARMORED_IA_MAX_CANDIDATES", "10")))
        try:
            policy_retries = int(os.getenv("ARMORED_IA_POLICY_RETRIES", "1"))
        except ValueError as exc:
            raise CaptionGenerationError("ARMORED_IA_POLICY_RETRIES deve ser inteiro entre 0 e 2") from exc
        if policy_retries < 0 or policy_retries > 2:
            raise CaptionGenerationError("ARMORED_IA_POLICY_RETRIES deve ser inteiro entre 0 e 2")

        batch_id = uuid.uuid4().hex
        all_evaluations: list[CaptionCandidateEvaluation] = []
        feedback: tuple[str, ...] = ()
        last_error: CaptionGenerationError | None = None
        candidate_limit = min(model_limit, 10)

        for attempt in range(policy_retries + 1):
            request_context = dict(product_context)
            if feedback:
                request_context["__policy_retry_feedback"] = list(feedback)
            candidates = self.provider.generate_candidates(
                "caption", request_context, candidate_limit,
            )
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
                    ) from exc
                feedback = tuple(dict.fromkeys(
                    evaluation.rejection_reason
                    for evaluation in evaluations
                    if evaluation.rejection_reason
                ))[:8]
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
            )

        # The loop either returned a valid selection or raised with its evidence.
        raise last_error or CaptionGenerationError("caption-policy-retry-exhausted", batch_id=batch_id)

    def generate(self, product_context: dict[str, Any]) -> str:
        return self.generate_with_evidence(product_context).caption
