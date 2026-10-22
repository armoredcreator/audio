from __future__ import annotations

import base64
import json
import math
import os
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from .base import AIProviderError
from .context import context_text as _context_text
from .transport import bounded_number, close_response, retryable_failure


class GeminiProvider:
    @staticmethod
    def configuration() -> dict[str, str]:
        return {"provider": "gemini", "model": os.getenv("ARMORED_IA_MODEL", "gemini-3.1-flash-lite")}

    def __init__(self, requester=None, image_getter=None, *, sleeper=None, clock=None, wall_clock=None):
        self.requester = requester or requests.post
        self.image_getter = image_getter or requests.get
        self.sleeper = sleeper or time.sleep
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        # Safe diagnostic receipts only: no request bodies, credentials,
        # image/product URLs or untrusted exception messages.
        self.last_attempts: list[dict[str, Any]] = []

    _number = staticmethod(bounded_number)
    _close = staticmethod(close_response)

    def _server_delay(self, response) -> float | None:
        if response is None:
            return None
        delays: list[float] = []
        headers = getattr(response, "headers", {}) or {}
        value = headers.get("Retry-After") or headers.get("retry-after")
        if value:
            try:
                seconds = float(value)
            except (TypeError, ValueError):
                try:
                    date = parsedate_to_datetime(str(value))
                    if date.tzinfo is None:
                        date = date.replace(tzinfo=timezone.utc)
                    seconds = date.timestamp() - self.wall_clock()
                except (TypeError, ValueError, OverflowError):
                    seconds = math.nan
            if math.isfinite(seconds) and seconds >= 0:
                delays.append(seconds)
        try:
            payload = response.json()
            details = payload.get("error", {}).get("details", [])
            for detail in details if isinstance(details, list) else []:
                if not isinstance(detail, dict) or detail.get("@type") != "type.googleapis.com/google.rpc.RetryInfo":
                    continue
                duration = str(detail.get("retryDelay", ""))
                try:
                    seconds = float(duration[:-1]) if duration.endswith("s") else math.nan
                except ValueError:
                    continue
                if math.isfinite(seconds) and seconds >= 0:
                    delays.append(seconds)
        except (AttributeError, TypeError, ValueError):
            pass
        # Never shorten the service's requested interval to fit a local cap.
        return max(delays) if delays else None

    @staticmethod
    def _failure(attempt: int, category: str, status: int | None = None, retry_after: float | None = None) -> AIProviderError:
        suffix = f"; HTTP {status}" if status is not None else ""
        error = AIProviderError(f"Gemini indisponível após {attempt} tentativa(s): {category}{suffix}")
        error.category = category
        error.status_code = status
        error.retry_after = retry_after
        return error

    _retryable = staticmethod(retryable_failure)

    @staticmethod
    def _extract_candidates(data: dict[str, Any], limit: int) -> list[str]:
        try:
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"]
            payload = json.loads(str(raw_text))
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AIProviderError("Gemini não retornou JSON estruturado") from exc

        raw = payload.get("captions") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            raise AIProviderError("Gemini não retornou a lista de candidatas")

        candidates: list[str] = []
        for entry in raw:
            value = entry.get("text") if isinstance(entry, dict) else entry
            text = str(value or "").strip()
            if text and text not in candidates:
                candidates.append(text)
            if len(candidates) >= max(1, min(limit, 10)):
                break
        if not candidates:
            raise AIProviderError("Gemini retornou zero candidatas")
        return candidates

    def generate_candidates(self, task: str, context: dict[str, Any], limit: int) -> list[str]:
        if task != "caption":
            raise AIProviderError(f"Gemini task não suportada: {task}")

        api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
        if not api_key:
            raise AIProviderError("GEMINI_API_KEY ausente; ArmoredIA Caption obrigatória")

        attempts_value = self._number("ARMORED_IA_MAX_ATTEMPTS", 5, 1, 10)
        if not attempts_value.is_integer():
            raise AIProviderError("Configuração Gemini inválida: ARMORED_IA_MAX_ATTEMPTS")
        attempts = int(attempts_value)
        retry_delay = self._number("ARMORED_IA_RETRY_DELAY", 2, 0, 60)
        max_delay = self._number("ARMORED_IA_MAX_RETRY_DELAY", 30, 0, 60)
        total_timeout = self._number("ARMORED_IA_TOTAL_TIMEOUT", 180, 1, 600)
        api_timeout = self._number("ARMORED_IA_API_TIMEOUT", 90, 1, 120)
        image_timeout = self._number("ARMORED_IA_IMAGE_TIMEOUT", 10, 1, 30)
        self.last_attempts = []

        model = self.configuration()["model"]
        prompt = """
Você recebe contexto de um produto que JÁ FOI identificado pela Vision V1.
A identidade da Vision V1 é a fonte de verdade. Você NÃO identifica, corrige,
substitui ou renomeia o produto.

Gere até 10 opções de reação curta, natural e específica para este item.
Use categoria, uso, ambiente, estilo, aparência e demais sinais disponíveis.
Evite frases coringa quando o contexto permitir algo melhor.

PARA CADA OPÇÃO:
- Português do Brasil.
- Exatamente 2 ou 3 palavras no texto principal.
- Não gere emoji; a política central aplica o emoji fixo.
- Exatamente 1 ou 2 hashtags.
- Hashtags podem indicar categoria, uso, ambiente ou característica.
- NÃO use embalagem, tampa, frasco ou lacre.
- NÃO use linguagem de venda, urgência, promoção ou desconto.
- NÃO escreva marca, modelo, quantidade, medida ou voltagem.
- NÃO copie uma expressão distintiva do título.
- Não use hashtags para contornar as regras.
- Uma palavra genérica da categoria é permitida.

Retorne SOMENTE JSON:
{"captions":["opção 1","opção 2","..."]}
""".strip()

        parts: list[dict[str, Any]] = [{
            "text": (
                prompt
                + "\n\nCONTEXTO V1 — identidade já resolvida:\n"
                + _context_text(context)
                + f"\nPúblico-alvo: {os.getenv('ARMORED_IA_AUDIENCE', 'público brasileiro de descoberta e lifestyle')}"
            )
        }]

        image_url = str(context.get("imageUrl") or "").strip()
        if image_url:
            image = None
            try:
                image = self.image_getter(
                    image_url,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=image_timeout,
                )
                image.raise_for_status()
                mime = image.headers.get("Content-Type", "image/jpeg").split(";", 1)[0]
                parts.append({
                    "inline_data": {
                        "mime_type": mime,
                        "data": base64.b64encode(image.content).decode("ascii"),
                    }
                })
            except requests.RequestException:
                pass
            finally:
                self._close(image)

        deadline = self.clock() + total_timeout

        for attempt in range(1, attempts + 1):
            response = None
            failure = None
            retryable = False
            delay = None
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise self._failure(attempt - 1, "RETRY_BUDGET_EXHAUSTED")
            try:
                response = self.requester(
                    f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                    headers={"x-goog-api-key": api_key, "Content-Type": "application/json"},
                    json={
                        "contents": [{"parts": parts}],
                        "generationConfig": {
                            "maxOutputTokens": 400,
                            "responseMimeType": "application/json",
                            "responseSchema": {
                                "type": "OBJECT",
                                "properties": {
                                    "captions": {
                                        "type": "ARRAY",
                                        "items": {"type": "STRING"},
                                        "maxItems": min(limit, 10),
                                    }
                                },
                                "required": ["captions"],
                            },
                        },
                    },
                    timeout=min(api_timeout, remaining),
                )
                response.raise_for_status()
                try:
                    candidates = self._extract_candidates(response.json(), min(limit, 10))
                except ValueError as exc:
                    raise AIProviderError("Gemini não retornou JSON estruturado") from exc
                self.last_attempts.append({"attempt": attempt, "status": "SUCCESS", "http_status": getattr(response, "status_code", 200)})
                return candidates
            except requests.RequestException as exc:
                if response is None:
                    response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                retryable = self._retryable(exc)
                category = (
                    "AUTHENTICATION_OR_PERMISSION" if status in {401, 403}
                    else "RATE_LIMITED" if status == 429
                    else "TRANSIENT_UNAVAILABLE" if retryable
                    else "REQUEST_REJECTED"
                )
                delay = self._server_delay(response) if retryable else None
                failure = self._failure(attempt, category, status, delay)
            except AIProviderError:
                retryable = True
                failure = self._failure(attempt, "MALFORMED_RESPONSE", getattr(response, "status_code", None))
            finally:
                self._close(response)

            assert failure is not None
            receipt = {"attempt": attempt, "status": failure.category, "http_status": failure.status_code}
            self.last_attempts.append(receipt)
            if not retryable or attempt >= attempts:
                raise failure
            if delay is None:
                delay = min(max_delay, retry_delay * (2 ** (attempt - 1)))
            if delay > max_delay or delay >= deadline - self.clock():
                receipt["retry_stopped"] = "SERVER_DELAY_EXCEEDS_BUDGET"
                raise failure
            receipt["retry_delay"] = delay
            self.sleeper(delay)

        raise AIProviderError("Gemini indisponível após todas as tentativas")
