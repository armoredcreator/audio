from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import requests

log = logging.getLogger(__name__)


class ShopeeAPIError(RuntimeError):
    """A non-retryable Shopee API or response error."""


class ShopeeAuthenticationError(ShopeeAPIError):
    """Credentials, signature, or permission were rejected."""


class ShopeeRateLimitError(ShopeeAPIError):
    """Shopee rejected the request due to quota/rate limiting.

    The response metadata is retained for a later, explicitly scheduled retry.
    This exception never triggers an immediate retry itself.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: str | None = None,
        status_code: int | None = None,
        response_metadata: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.status_code = status_code
        self.response_metadata = dict(response_metadata or {})


class ShopeeTransientAPIError(ShopeeAPIError):
    """A read-only request failed transiently after permitted retries."""


class ShopeeMutationOutcomeUnknownError(ShopeeAPIError):
    """A mutation may have reached Shopee, but its outcome was not confirmed.

    Callers must not blindly replay the mutation. The client deliberately does
    not retry mutations because a transport/server failure can occur after the
    remote service has already applied the change.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_metadata: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_metadata = dict(response_metadata or {})


class ShopeeProductNotFoundError(ShopeeAPIError):
    """Exact V1 lookup found no affiliate offer for the supplied IDs."""


PRODUCT_OFFER_QUERY = """
query ProductOffer($itemId: Int64, $shopId: Int64, $page: Int, $limit: Int) {
  productOfferV2(itemId: $itemId, shopId: $shopId, page: $page, limit: $limit) {
    nodes { itemId commissionRate commission price sales imageUrl productName shopName productLink offerLink ratingStar shopId }
  }
}
"""

_RETRYABLE_HTTP_STATUSES = {408, 425, 500, 502, 503, 504}
_AUTH_HTTP_STATUSES = {401, 403}
_MAX_ATTEMPTS = 5
_MAX_RETRY_DELAY_SECONDS = 60.0
_SAFE_RESPONSE_HEADERS = {
    "retry-after",
    "ratelimit-limit",
    "ratelimit-remaining",
    "ratelimit-reset",
    "x-ratelimit-limit",
    "x-ratelimit-remaining",
    "x-ratelimit-reset",
    "x-rate-limit-limit",
    "x-rate-limit-remaining",
    "x-rate-limit-reset",
    "x-request-id",
    "request-id",
    "x-api-request-id",
}
_AUTH_ERROR_CODES = {
    "AUTHENTICATION_ERROR",
    "UNAUTHENTICATED",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "INVALID_SIGNATURE",
    "INVALID_CREDENTIALS",
    "ACCESS_DENIED",
    "PERMISSION_DENIED",
}
_RATE_ERROR_CODES = {
    "RATE_LIMITED",
    "RATE_LIMIT_EXCEEDED",
    "TOO_MANY_REQUESTS",
    "THROTTLED",
    "QUOTA_EXCEEDED",
}
_TRANSIENT_ERROR_CODES = {
    "INTERNAL_SERVER_ERROR",
    "SERVICE_UNAVAILABLE",
    "TEMPORARY_UNAVAILABLE",
    "TIMEOUT",
}


def _response_metadata(headers: Any) -> dict[str, str]:
    """Keep only useful, non-secret response headers for diagnosis."""
    result: dict[str, str] = {}
    try:
        pairs = headers.items()
    except AttributeError:
        return result
    for key, value in pairs:
        normalized = str(key).strip().casefold()
        if normalized in _SAFE_RESPONSE_HEADERS:
            result[normalized] = str(value).strip()
    return result


def _retry_delay(headers: Any, attempt: int) -> float:
    """Compute a bounded delay, honoring Retry-After when it is supplied."""
    try:
        raw = str(headers.get("Retry-After", "") or "").strip()
    except AttributeError:
        raw = ""
    if raw:
        try:
            seconds = float(raw)
        except ValueError:
            try:
                seconds = (parsedate_to_datetime(raw).timestamp() - time.time())
            except (TypeError, ValueError, OverflowError):
                seconds = -1.0
        if seconds >= 0:
            return seconds

    try:
        base = max(0.0, float(os.getenv("SHOPEE_API_RETRY_BASE_SECONDS", "2")))
    except (TypeError, ValueError):
        base = 2.0
    return min(base * attempt, _MAX_RETRY_DELAY_SECONDS)


def _graphql_error_category(errors: Any) -> str:
    """Return auth, rate_limit, transient, or permanent without guessing."""
    try:
        serialized = json.dumps(errors, ensure_ascii=False, default=str).casefold()
    except (TypeError, ValueError):
        serialized = str(errors).casefold()

    codes: set[str] = set()
    if isinstance(errors, list):
        for error in errors:
            if not isinstance(error, dict):
                continue
            extensions = error.get("extensions")
            if isinstance(extensions, dict) and extensions.get("code") is not None:
                codes.add(str(extensions["code"]).strip().upper())

    if codes & _AUTH_ERROR_CODES or any(
        marker in serialized
        for marker in (
            "invalid signature",
            "invalid credential",
            "authentication failed",
            "unauthenticated",
            "unauthorized",
            "access denied",
            "permission denied",
        )
    ):
        return "auth"
    if codes & _RATE_ERROR_CODES or any(
        marker in serialized
        for marker in ("rate limit", "too many requests", "quota exceeded", "throttled")
    ):
        return "rate_limit"
    if codes & _TRANSIENT_ERROR_CODES:
        return "transient"
    return "permanent"


class ShopeeAffiliateAPI:
    """Shopee Affiliate Open API client with run-local successful-result reuse.

    Only read-only exact-product queries may be retried, and only for
    explicitly transient transport/HTTP/GraphQL failures. Authentication,
    quota, validation, and unknown GraphQL errors fail without retries.
    Mutations are never automatically retried because their remote outcome can
    be ambiguous after a timeout or server error.
    """

    def __init__(self, app_id: Optional[str] = None, secret_key: Optional[str] = None):
        self.app_id = app_id or os.getenv("SHOPEE_APP_ID")
        self.secret_key = secret_key or os.getenv("SHOPEE_SECRET_KEY")
        if not self.app_id or not self.secret_key:
            raise RuntimeError("SHOPEE_APP_ID/SHOPEE_SECRET_KEY não configurados")
        self.session = requests.Session()
        self._product_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._short_link_cache: dict[str, str] = {}
        # A timeout/5xx can occur after the remote mutation was applied. Block
        # replay for this client lifetime unless a separate reconciliation can
        # establish the mutation outcome.
        self._short_link_unknown: set[str] = set()
        self.last_response_metadata: dict[str, Any] = {}

    def close(self) -> None:
        self.session.close()

    @staticmethod
    def _close_response(response: Any) -> None:
        close = getattr(response, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                # Cleanup must not hide the API result/error being reported.
                pass

    @staticmethod
    def _max_attempts() -> int:
        try:
            configured = int(os.getenv("SHOPEE_API_MAX_RETRIES", "3"))
        except (TypeError, ValueError):
            configured = 3
        # The env var historically means total attempts, not retries-after-first.
        return min(max(1, configured), _MAX_ATTEMPTS)

    def _response_retry_delay(self, response: Any, attempt: int) -> float:
        headers = getattr(response, "headers", {})
        delay = _retry_delay(headers, attempt)
        if delay > _MAX_RETRY_DELAY_SECONDS:
            metadata = _response_metadata(headers)
            self._close_response(response)
            raise ShopeeRateLimitError(
                "Shopee requested a retry window beyond this run; stop instead of retrying early",
                retry_after=metadata.get("retry-after"),
                status_code=int(getattr(response, "status_code", 200)),
                response_metadata=metadata,
            )
        return delay

    def _post(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        allow_transient_retries: bool = True,
    ):
        body = {"query": query, "variables": variables or {}}
        payload = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        url = os.getenv(
            "SHOPEE_AFFILIATE_API_URL",
            "https://open-api.affiliate.shopee.com.br/graphql",
        )
        operation = (
            "generateShortLink" if "generateShortLink" in query
            else "productOfferV2" if "productOfferV2" in query
            else "graphql"
        )
        is_mutation = bool(re.search(r"\bmutation\b", query, re.IGNORECASE))
        # Even if a caller opts in accidentally, mutations are not retried.
        can_retry = bool(allow_transient_retries and not is_mutation)
        max_attempts = self._max_attempts()
        self.last_response_metadata = {}

        for attempt in range(1, max_attempts + 1):
            ts = int(time.time())
            signature = hashlib.sha256(
                f"{self.app_id}{ts}{payload}{self.secret_key}".encode()
            ).hexdigest()
            started = time.perf_counter()

            try:
                response = self.session.post(
                    url,
                    data=payload.encode(),
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": (
                            f"SHA256 Credential={self.app_id},Timestamp={ts},Signature={signature}"
                        ),
                    },
                    timeout=int(os.getenv("SHOPEE_API_TIMEOUT", "30")),
                )
            except requests.RequestException as exc:
                if can_retry and attempt < max_attempts:
                    delay = _retry_delay({}, attempt)
                    log.warning(
                        "[SHOPEE_API] operation=%s attempt=%s/%s transient_transport_error=%s retry_delay=%.2fs",
                        operation, attempt, max_attempts, type(exc).__name__, delay,
                    )
                    time.sleep(delay)
                    continue
                if is_mutation:
                    raise ShopeeMutationOutcomeUnknownError(
                        f"Resultado da mutação {operation} é desconhecido após falha de transporte; "
                        "não repetir automaticamente"
                    ) from exc
                if can_retry:
                    raise ShopeeTransientAPIError(
                        f"Falha transitória de transporte na operação {operation} após "
                        f"{attempt} tentativa(s)"
                    ) from exc
                raise ShopeeAPIError(
                    f"Falha de transporte na operação Shopee {operation}"
                ) from exc

            status = int(getattr(response, "status_code", 200))
            metadata = _response_metadata(getattr(response, "headers", {}))
            self.last_response_metadata = {"status_code": status, "headers": metadata}
            retry_after = metadata.get("retry-after")

            if status in _AUTH_HTTP_STATUSES:
                self._close_response(response)
                raise ShopeeAuthenticationError(
                    f"Shopee rejeitou autenticação/permissão (HTTP {status}); "
                    "nenhuma nova tentativa automática foi feita"
                )

            if status == 429:
                self._close_response(response)
                raise ShopeeRateLimitError(
                    "Shopee aplicou limite de taxa/quota; chamada interrompida sem retry automático",
                    retry_after=retry_after,
                    status_code=status,
                    response_metadata=metadata,
                )

            if status >= 500:
                if is_mutation:
                    self._close_response(response)
                    raise ShopeeMutationOutcomeUnknownError(
                        f"Resultado da mutação {operation} é desconhecido após HTTP {status}; "
                        "não repetir automaticamente",
                        status_code=status,
                        response_metadata=metadata,
                    )
                if status in _RETRYABLE_HTTP_STATUSES and can_retry and attempt < max_attempts:
                    delay = self._response_retry_delay(response, attempt)
                    self._close_response(response)
                    log.warning(
                        "[SHOPEE_API] operation=%s attempt=%s/%s transient_http_status=%s retry_delay=%.2fs",
                        operation, attempt, max_attempts, status, delay,
                    )
                    time.sleep(delay)
                    continue
                if status in _RETRYABLE_HTTP_STATUSES and can_retry:
                    self._close_response(response)
                    raise ShopeeTransientAPIError(
                        f"Falha HTTP transitória na operação {operation} após {attempt} tentativa(s) "
                        f"(HTTP {status})"
                    )
                self._close_response(response)
                raise ShopeeAPIError(
                    f"Erro HTTP Shopee na operação {operation} (HTTP {status})"
                )

            if status >= 400:
                self._close_response(response)
                raise ShopeeAPIError(
                    f"Requisição Shopee rejeitada na operação {operation} (HTTP {status}); "
                    "nenhuma nova tentativa automática foi feita"
                )

            try:
                data = response.json()
            except (ValueError, requests.RequestException) as exc:
                self._close_response(response)
                if is_mutation:
                    raise ShopeeMutationOutcomeUnknownError(
                        f"Resposta da mutação {operation} não pôde ser interpretada; "
                        "o resultado remoto é desconhecido e não será repetido automaticamente",
                        status_code=status,
                        response_metadata=metadata,
                    ) from exc
                if can_retry and attempt < max_attempts:
                    delay = self._response_retry_delay(response, attempt)
                    log.warning(
                        "[SHOPEE_API] operation=%s attempt=%s/%s invalid_json retry_delay=%.2fs",
                        operation, attempt, max_attempts, delay,
                    )
                    time.sleep(delay)
                    continue
                if can_retry:
                    raise ShopeeTransientAPIError(
                        f"Resposta JSON inválida na operação {operation} após "
                        f"{attempt} tentativa(s)"
                    ) from exc
                raise ShopeeAPIError(
                    f"Resposta inválida da operação Shopee {operation}"
                ) from exc
            self._close_response(response)

            errors = data.get("errors") if isinstance(data, dict) else None
            if errors:
                category = _graphql_error_category(errors)
                safe_summary = f"Erro GraphQL Shopee na operação {operation}"
                if category == "auth":
                    raise ShopeeAuthenticationError(
                        f"{safe_summary}: autenticação, assinatura ou permissão rejeitada"
                    )
                if category == "rate_limit":
                    raise ShopeeRateLimitError(
                        f"{safe_summary}: limite de taxa/quota; chamada interrompida sem retry automático",
                        retry_after=retry_after,
                        status_code=status,
                        response_metadata=metadata,
                    )
                if category == "transient" and can_retry and attempt < max_attempts:
                    delay = self._response_retry_delay(response, attempt)
                    log.warning(
                        "[SHOPEE_API] operation=%s attempt=%s/%s transient_graphql_error retry_delay=%.2fs",
                        operation, attempt, max_attempts, delay,
                    )
                    time.sleep(delay)
                    continue
                if category == "transient" and can_retry:
                    raise ShopeeTransientAPIError(
                        f"{safe_summary} transitório após {attempt} tentativa(s)"
                    )
                # Unknown errors and all mutation errors are conservative failures.
                raise ShopeeAPIError(f"{safe_summary}; detalhes sensíveis omitidos")

            log.debug(
                "[SHOPEE_API] operation=%s attempt=%s elapsed=%.3fs status=ok",
                operation,
                attempt,
                time.perf_counter() - started,
            )
            return data

        # The bounded loop always returns or raises. This guard is defensive.
        raise ShopeeTransientAPIError("Shopee: tentativas esgotadas sem resultado")

    def get_exact_product(self, shop_id: str, item_id: str, *, allow_transient_retries: bool = True):
        key = (str(shop_id), str(item_id))
        cached = self._product_cache.get(key)
        if cached is not None:
            log.debug("[SHOPEE_API] product cache hit shop=%s item=%s", *key)
            return dict(cached)

        data = self._post(
            PRODUCT_OFFER_QUERY,
            {"itemId": key[1], "shopId": key[0], "page": 1, "limit": 1},
            allow_transient_retries=allow_transient_retries,
        )
        envelope = data.get("data") if isinstance(data, dict) else None
        offer = envelope.get("productOfferV2") if isinstance(envelope, dict) else None
        nodes = offer.get("nodes") if isinstance(offer, dict) else None
        if not isinstance(nodes, list):
            raise ShopeeAPIError("Resposta Shopee incompleta: productOfferV2.nodes não é uma lista")
        if not nodes:
            raise ShopeeProductNotFoundError(f"Produto não encontrado: {shop_id}:{item_id}")
        product = nodes[0]
        if not isinstance(product, dict):
            raise ShopeeAPIError("Resposta Shopee incompleta: nó de produto inválido")
        if str(product.get("shopId")) != key[0] or str(product.get("itemId")) != key[1]:
            raise ShopeeAPIError("Produto Shopee divergente")
        self._product_cache[key] = dict(product)
        return dict(product)

    def affiliate_link_for_product(self, product: dict[str, Any]):
        offer = str(product.get("offerLink") or "").strip()
        if offer:
            return offer
        product_link = str(product.get("productLink") or "").strip()
        if not product_link:
            raise ShopeeAPIError("Produto não possui productLink canônico para gerar afiliação")
        return str(self.generate_short_link(product_link)["short_link"]).strip()

    def generate_short_link(self, origin_url: str):
        origin = str(origin_url or "").strip()
        cached = self._short_link_cache.get(origin)
        if cached:
            log.debug("[SHOPEE_API] short-link cache hit url=%s", origin)
            return {"short_link": cached}
        if origin in self._short_link_unknown:
            raise ShopeeMutationOutcomeUnknownError(
                "Já houve uma tentativa de resultado desconhecido para esta origem nesta "
                "instância; a mutação foi bloqueada para evitar repetição."
            )
        query = (
            "mutation { generateShortLink(input: { originUrl: "
            f"{json.dumps(origin)}"
            " }) { shortLink } }"
        )
        try:
            result = self._post(query, allow_transient_retries=False)
        except ShopeeMutationOutcomeUnknownError:
            self._short_link_unknown.add(origin)
            raise
        # A 2xx GraphQL response is not proof that the remote mutation failed
        # or succeeded. Every malformed/missing envelope must be treated as an
        # ambiguous outcome, never as a plain AttributeError that permits replay.
        envelope = result.get("data") if isinstance(result, dict) else None
        operation = envelope.get("generateShortLink") if isinstance(envelope, dict) else None
        link = operation.get("shortLink") if isinstance(operation, dict) else None
        short_link = link.strip() if isinstance(link, str) else ""
        if not short_link:
            self._short_link_unknown.add(origin)
            raise ShopeeMutationOutcomeUnknownError(
                "Shopee não confirmou shortLink; resposta ausente ou malformada. "
                "Resultado da mutação desconhecido; não repetir sem reconciliação."
            )
        self._short_link_cache[origin] = short_link
        return {"short_link": short_link}
