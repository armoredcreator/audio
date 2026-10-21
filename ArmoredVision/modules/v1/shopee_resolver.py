from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

from armored_core.shopee_urls import is_supported_shopee_url

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
    # IDs inside an arbitrary URL path are not evidence that the host is Shopee.
    if not is_supported_shopee_url(url):
        return None
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
    if not is_supported_shopee_url(original):
        raise ValueError("URL fora dos domínios Shopee permitidos")

    started = time.perf_counter()
    direct = _extract(original)
    if direct:
        log.debug("[SHOPEE_RESOLVER] mode=direct elapsed=%.3fs", time.perf_counter() - started)
        return ShopeeResolvedLink(original, original, direct[0], direct[1])

    client = session or requests
    retries = max(1, min(int(os.getenv("SHOPEE_RESOLVE_MAX_RETRIES", "2")), 5))
    retry_base = max(0.0, float(os.getenv("SHOPEE_RESOLVE_RETRY_BASE_SECONDS", "1")))
    redirect_statuses = {301, 302, 303, 307, 308}
    response = None
    resolved = ""
    for attempt in range(1, retries + 1):
        response = None
        resolved = ""
        current = original
        try:
            # Do not let requests follow an unchecked redirect. Each hop must
            # remain on an explicitly allowed Shopee hostname before it is fetched.
            for hop in range(6):
                if not is_supported_shopee_url(current):
                    raise ValueError("Redirecionamento fora dos domínios Shopee permitidos")
                response = client.get(
                    current,
                    headers={"User-Agent": "Mozilla/5.0"},
                    allow_redirects=False,
                    timeout=timeout,
                    stream=False,
                )
                status = int(getattr(response, "status_code", 200))
                if status in redirect_statuses:
                    location = str(getattr(response, "headers", {}).get("Location", "") or "").strip()
                    response.close()
                    response = None
                    if not location:
                        raise ValueError("Redirecionamento Shopee sem cabeçalho Location")
                    next_url = urljoin(current, location)
                    if not is_supported_shopee_url(next_url):
                        raise ValueError("Redirecionamento fora dos domínios Shopee permitidos")
                    if hop == 5:
                        raise ValueError("Redirecionamentos Shopee excederam o limite seguro")
                    current = next_url
                    continue

                response.raise_for_status()
                resolved = str(getattr(response, "url", "") or current).strip()
                if not is_supported_shopee_url(resolved):
                    raise ValueError("URL final fora dos domínios Shopee permitidos")
                response.close()
                response = None
                break

            if not resolved:
                raise requests.RequestException("URL Shopee não chegou a uma resposta final")
            break
        except ValueError:
            if response is not None:
                response.close()
            raise
        except requests.exceptions.SSLError as exc:
            if response is not None:
                response.close()
            # Retrying certificate verification failures cannot make them safe.
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

    log.debug(
        "[SHOPEE_RESOLVER] mode=redirect elapsed=%.3fs",
        time.perf_counter() - started,
    )
    extracted = _extract(resolved)
    if extracted:
        return ShopeeResolvedLink(original, resolved, extracted[0], extracted[1])
    raise ValueError(f"Não foi possível extrair shop_id/item_id: {resolved}")
