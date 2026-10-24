from __future__ import annotations

import math
import os

import requests

from .base import AIProviderError


def bounded_number(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as exc:
        raise AIProviderError(f"Configuração IA inválida: {name}") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise AIProviderError(f"Configuração IA fora dos limites: {name}")
    return value


def close_response(response) -> None:
    close = getattr(response, "close", None)
    if callable(close):
        close()


def retryable_failure(exc: requests.RequestException) -> bool:
    if isinstance(exc, (requests.exceptions.Timeout, requests.exceptions.ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) in {429, 500, 502, 503, 504}
