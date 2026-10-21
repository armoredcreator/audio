from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

log = logging.getLogger(__name__)

# This deployment handles Brazilian Shopee links. Match the hostname boundary,
# not a substring: "notshopee.com.br" and "shopee.com.br.attacker.test" are
# not Shopee hosts. Shopee's short-link domain is included for redirects.
SHOPEE_HOSTS = ("shopee.com.br", "shope.ee")
REDIRECT_STATUSES = {301, 302, 303, 307, 308}
MAX_REDIRECTS = 10

PATTERNS = (
    # Canonical Shopee product URLs use slug-i.SHOP_ID.ITEM_ID.
    re.compile(r"-i\.(\d+)\.(\d+)(?:/|$)", re.I),
    re.compile(r"/opaanlp/(\d+)/(\d+)", re.I),
    re.compile(r"/product/(\d+)/(\d+)", re.I),
    re.compile(r"/(\d+)/(\d+)(?:[/?#]|$)", re.I),
)


@dataclass(frozen=True)
class ShopeeResolvedLink:
    original_url: str
    resolved_url: str
    shop_id: str
    item_id: str


def _is_shopee_url(url: str) -> bool:
    """Accept only HTTP(S) URLs whose hostname is an approved Shopee domain."""
    try:
        parsed = urlparse(str(url or "").strip())
        host = (parsed.hostname or "").lower().rstrip(".")
        return (
            parsed.scheme.lower() in {"http", "https"}
            and bool(host)
            and any(host == domain or host.endswith("." + domain) for domain in SHOPEE_HOSTS)
            and parsed.username is None
            and parsed.password is None
        )
    except (TypeError, ValueError):
        return False


def _extract(url: str):
    # IDs in a product-shaped path are not evidence unless the host is Shopee.
    if not _is_shopee_url(url):
        return None
    path = urlparse(url).path or ""
    for pattern in PATTERNS:
        match = pattern.search(path)
        if match:
            return match.group(1), match.group(2)
    return None


def _get_shopee_redirect_chain(client, original: str, timeout: int, retries: int) -> str:
    """Follow redirects manually so each hop is checked before any request."""
    current = original
    for redirect_count in range(MAX_REDIRECTS + 1):
        last_error = None
        response = None
        for attempt in range(1, retries + 1):
            try:
                response = client.get(
                    current,
                    headers={"User-Agent": "Mozilla/5.0"},
                    allow_redirects=False,
                    timeout=timeout,
                    stream=False,
                )
                break
            except requests.exceptions.SSLError as exc:
                last_error = exc
                if "CERTIFICATE_VERIFY_FAILED" in str(exc).upper() or attempt >= retries:
                    raise
                log.warning(
                    "[SHOPEE_RESOLVER] transient TLS error attempt=%s/%s: %s",
                    attempt, retries, exc,
                )
                time.sleep(max(0.0, float(os.getenv("SHOPEE_RESOLVE_RETRY_BASE_SECONDS", "1"))) * attempt)
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= retries:
                    raise
                log.warning(
                    "[SHOPEE_RESOLVER] network error attempt=%s/%s: %s",
                    attempt, retries, exc,
                )
                time.sleep(max(0.0, float(os.getenv("SHOPEE_RESOLVE_RETRY_BASE_SECONDS", "1"))) * attempt)
        if response is None:
            raise last_error or requests.RequestException("Falha ao resolver URL Shopee")

        try:
            if response.status_code in REDIRECT_STATUSES:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Redirecionamento Shopee sem cabeçalho Location")
                next_url = urljoin(current, location)
                if not _is_shopee_url(next_url):
                    raise ValueError(
                        f"Redirecionamento para domínio não autorizado: {urlparse(next_url).hostname}"
                    )
                if redirect_count >= MAX_REDIRECTS:
                    raise ValueError("Limite de redirecionamentos Shopee excedido")
                current = next_url
                continue
            response.raise_for_status()
            # The final URL must still be a trusted Shopee URL.
            final_url = str(getattr(response, "url", "") or current)
            if not _is_shopee_url(final_url):
                raise ValueError(
                    f"URL final fora dos domínios Shopee autorizados: {urlparse(final_url).hostname}"
                )
            return final_url
        finally:
            response.close()

    raise ValueError("Limite de redirecionamentos Shopee excedido")


def resolve_short_url(
    url: str,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> ShopeeResolvedLink:
    original = str(url or "").strip()
    if not original:
        raise ValueError("URL Shopee vazia")
    if not _is_shopee_url(original):
        raise ValueError("URL fora dos domínios Shopee autorizados")

    started = time.perf_counter()
    direct = _extract(original)
    if direct:
        log.debug("[SHOPEE_RESOLVER] mode=direct elapsed=%.3fs", time.perf_counter() - started)
        return ShopeeResolvedLink(original, original, direct[0], direct[1])

    client = session or requests
    retries = max(1, min(int(os.getenv("SHOPEE_RESOLVE_MAX_RETRIES", "2")), 5))
    resolved = _get_shopee_redirect_chain(client, original, timeout, retries)

    log.debug(
        "[SHOPEE_RESOLVER] mode=redirect elapsed=%.3fs",
        time.perf_counter() - started,
    )
    extracted = _extract(resolved)
    if extracted:
        return ShopeeResolvedLink(original, resolved, extracted[0], extracted[1])

    raise ValueError(f"Não foi possível extrair shop_id/item_id: {resolved}")
