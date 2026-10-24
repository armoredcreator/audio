from __future__ import annotations

from typing import Any, Protocol


class AIProviderError(RuntimeError):
    """Provider transport, availability, or malformed-response failure."""

    def __init__(
        self,
        message: str,
        *,
        category: str = "PROVIDER_ERROR",
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.category = str(category)
        self.status_code = status_code


class AIProvider(Protocol):
    def generate_candidates(self, task: str, context: dict[str, Any], limit: int) -> list[str]:
        """Return model candidates for one AI task."""
