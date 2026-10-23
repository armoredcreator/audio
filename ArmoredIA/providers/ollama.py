from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from .base import AIProviderError
from .context import context_text as _context_text
from .transport import bounded_number, close_response, retryable_failure


class OllamaProvider:
    """Local Ollama adapter implementing the same caption-candidate contract as Gemini."""

    def __init__(self, requester=None):
        self.requester = requester or requests.post

    @staticmethod
    def configuration() -> dict[str, str]:
        return {"provider": "ollama", "model": (os.getenv("ARMORED_IA_OLLAMA_MODEL") or "gemma3:1b").strip()}

    @staticmethod
    def _extract_candidates(data: dict[str, Any], limit: int) -> list[str]:
        try:
            raw_text = data["response"]
            payload = json.loads(str(raw_text))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AIProviderError("Ollama não retornou JSON estruturado") from exc

        raw = payload.get("captions") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            raise AIProviderError("Ollama não retornou a lista de candidatas")

        # Match Gemini's contract: return raw, distinct candidates. The shared
        # CaptionGenerator policy is the sole authority for content validation.
        candidates: list[str] = []
        seen: set[str] = set()
        for entry in raw:
            value = entry.get("text") if isinstance(entry, dict) else entry
            caption = str(value or "").strip()
            if isinstance(entry, dict) and "hashtags" in entry:
                tags = entry["hashtags"]
                if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                    raise AIProviderError("Ollama retornou hashtags sem estrutura válida")
                # Join only model-generated fields. No hashtag or caption is
                # invented locally; the common policy still validates both.
                caption += "\n" + " ".join(tags)
            if not caption:
                continue
            if caption in seen:
                continue
            seen.add(caption)
            candidates.append(caption)
            if len(candidates) >= max(1, min(limit, 10)):
                break
        if not candidates:
            raise AIProviderError("Ollama retornou zero candidatas não vazias")
        return candidates

    def generate_candidates(self, task: str, context: dict[str, Any], limit: int) -> list[str]:
        if task != "caption":
            raise AIProviderError(f"Ollama task não suportada: {task}")

        base_url = (os.getenv("ARMORED_IA_OLLAMA_URL") or "http://localhost:11434").strip().rstrip("/")
        model = self.configuration()["model"]
        if not model:
            raise AIProviderError("ARMORED_IA_OLLAMA_MODEL está vazio")

        prompt = """
Você recebe contexto de um produto que JÁ FOI identificado pela Vision V1.
A identidade da Vision V1 é a fonte de verdade. Você NÃO identifica, corrige,
substitui ou renomeia o produto.

Gere opções de reação curta, natural e específica para este item.
Use categoria, uso, ambiente, estilo, aparência e demais sinais disponíveis.
Evite frases coringa quando o contexto permitir algo melhor.

PARA CADA OPÇÃO:
- Português do Brasil.
- Exatamente 2 ou 3 palavras no texto principal.
- NÃO inclua emoji; a política do código acrescenta sempre o coração laranja fixo 🧡.
- Exatamente 1 ou 2 hashtags.
- Hashtags podem indicar categoria, uso, ambiente ou característica.
- NÃO use embalagem, tampa, frasco ou lacre.
- NÃO use linguagem de venda, urgência, promoção ou desconto.
- NÃO escreva marca, modelo, quantidade, medida ou voltagem.
- NÃO copie uma expressão distintiva do título.
- Não use hashtags para contornar as regras.
- Uma palavra genérica da categoria é permitida.

Retorne SOMENTE um objeto JSON válido com a chave "captions". Cada item é um objeto com "text" (apenas 2 ou 3 palavras, sem emoji e sem hashtag) e "hashtags" (lista obrigatória de 1 ou 2 hashtags começando com #). Gere você mesmo o texto e as hashtags; não deixe campos vazios.
""".strip()
        policy_feedback = context.get("__policy_retry_feedback")
        if isinstance(policy_feedback, list) and policy_feedback:
            reasons = "; ".join(str(value) for value in policy_feedback[:8])
            prompt += (
                "\n\nCORREÇÃO OBRIGATÓRIA DA POLICY: a rodada anterior foi rejeitada. "
                f"Motivos: {reasons}. Refaça as opções. Conte as palavras do campo text "
                "antes de responder e use exatamente 2 ou 3 palavras simples no texto "
                "principal; não coloque frase extra, subtítulo, emoji ou hashtag nesse campo. "
                "As hashtags ficam somente no campo hashtags."
            )
        prompt += (
            "\n\nCONTEXTO V1 — identidade já resolvida:\n"
            + _context_text(context)
            + f"\nPúblico-alvo: {os.getenv('ARMORED_IA_AUDIENCE', 'público brasileiro de descoberta e lifestyle')}"
            + f"\nGere EXATAMENTE {min(max(1, limit), 10)} opções distintas. Não retorne blocos separados nem texto fora do JSON."
        )

        schema = {
            "type": "object",
            "properties": {
                "captions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "minLength": 3, "maxLength": 80},
                            "hashtags": {"type": "array", "minItems": 1, "maxItems": 2,
                                         "items": {"type": "string", "pattern": "^#[A-Za-zÀ-ÿ0-9_]+$"}},
                        },
                        "required": ["text", "hashtags"],
                        "additionalProperties": False,
                    },
                    "minItems": min(max(1, limit), 10),
                    "maxItems": min(max(1, limit), 10),
                }
            },
            "required": ["captions"],
        }
        attempts_value = bounded_number("ARMORED_IA_MAX_ATTEMPTS", 3, 1, 10)
        if not attempts_value.is_integer():
            raise AIProviderError("Configuração IA inválida: ARMORED_IA_MAX_ATTEMPTS")
        attempts = int(attempts_value)
        retry_delay = bounded_number("ARMORED_IA_RETRY_DELAY", 2, 0, 60)
        timeout = bounded_number("ARMORED_IA_OLLAMA_TIMEOUT", 180, 1, 600)
        max_tokens = bounded_number("ARMORED_IA_OLLAMA_MAX_TOKENS", 512, 64, 4096)
        if not max_tokens.is_integer():
            raise AIProviderError("ARMORED_IA_OLLAMA_MAX_TOKENS deve ser inteiro")
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "format": schema,
            "options": {"temperature": 0.7, "num_predict": int(max_tokens)},
        }
        think = os.getenv("ARMORED_IA_OLLAMA_THINK")
        if think is not None:
            if think.strip().casefold() not in {"0", "1", "true", "false"}:
                raise AIProviderError("ARMORED_IA_OLLAMA_THINK deve ser 0, 1, true ou false")
            # Opt-in only. Configure values supported by the selected model's
            # /api/show response; preserve its default when this is unset.
            payload["think"] = think.strip().casefold() in {"1", "true"}

        for attempt in range(1, attempts + 1):
            response = None
            try:
                response = self.requester(
                    f"{base_url}/api/generate",
                    json=payload,
                    timeout=timeout,
                )
                response.raise_for_status()
                try:
                    return self._extract_candidates(response.json(), min(max(1, limit), 10))
                except ValueError as exc:
                    raise AIProviderError("Ollama não retornou JSON estruturado") from exc
            except requests.RequestException as exc:
                if response is None:
                    response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                if retryable_failure(exc) and attempt < attempts:
                    close_response(response)
                    response = None
                    time.sleep(retry_delay)
                    continue
                raise AIProviderError(
                    f"Ollama indisponível após {attempt} tentativa(s): "
                    + (f"HTTP {status}" if status is not None else type(exc).__name__)
                ) from None
            except AIProviderError:
                if attempt < attempts:
                    close_response(response)
                    response = None
                    time.sleep(retry_delay)
                    continue
                raise
            finally:
                close_response(response)

        raise AIProviderError("Ollama indisponível após todas as tentativas")
