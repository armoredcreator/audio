from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

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


def _extract(url: str):
    path = urlparse(url).path or ""
    for pattern in PATTERNS:
        match = pattern.search(path)
        if match:
            return match.group(1), match.group(2)
    return None


def resolve_short_url(
    url: str,
    timeout: int = 20,
    session: requests.Session | None = None,
) -> ShopeeResolvedLink:
    original = str(url or "").strip()
    if not original:
        raise ValueError("URL Shopee vazia")

    started = time.perf_counter()
    direct = _extract(original)
    if direct:
        log.debug("[SHOPEE_RESOLVER] mode=direct elapsed=%.3fs", time.perf_counter() - started)
        return ShopeeResolvedLink(original, original, direct[0], direct[1])

    client = session or requests
    retries = max(1, min(int(os.getenv("SHOPEE_RESOLVE_MAX_RETRIES", "2")), 5))
    retry_base = max(0.0, float(os.getenv("SHOPEE_RESOLVE_RETRY_BASE_SECONDS", "1")))
    response = None
    resolved = ""
    for attempt in range(1, retries + 1):
        response = None
        try:
            response = client.get(
                original,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
                timeout=timeout,
                stream=False,
            )
            response.raise_for_status()
            resolved = response.url
            break
        except requests.exceptions.SSLError as exc:
            if response is not None:
                response.close()
            # A certificate validation error is configuration/security-related;
            # retrying it cannot make the certificate trustworthy.
            if "CERTIFICATE_VERIFY_FAILED" in str(exc).upper() or attempt >= retries:
                raise
            log.warning(
                "[SHOPEE_RESOLVER] transient TLS error attempt=%s/%s: %s",
                attempt, retries, exc,
            )
            time.sleep(retry_base * attempt)
        except requests.RequestException as exc:
            if response is not None:
                response.close()
            if attempt >= retries:
                raise
            log.warning(
                "[SHOPEE_RESOLVER] network error attempt=%s/%s: %s",
                attempt, retries, exc,
            )
            time.sleep(retry_base * attempt)
    if response is None:
        raise requests.RequestException("Falha ao resolver URL Shopee")
    response.close()
    log.debug(
        "[SHOPEE_RESOLVER] mode=redirect elapsed=%.3fs",
        time.perf_counter() - started,
    )

    extracted = _extract(resolved)
    if extracted:
        return ShopeeResolvedLink(original, resolved, extracted[0], extracted[1])

    raise ValueError(f"Não foi possível extrair shop_id/item_id: {resolved}")
