from __future__ import annotations

import re
from urllib.parse import urlparse


# Keep the recognized host policy in one place for Sync extraction and Vision
# resolution. Subdomain matches require a DNS label boundary, not a substring.
SHOPEE_ALLOWED_DOMAINS = (
    "shopee.com.br",
    "shopee.co",
    "shopee.ee",  # legacy source format
    "shope.ee",   # Shopee short links used by official Shopee Brazil channels
)
PRODUCT_URL_PATTERNS = (
    re.compile(r"-i\.(\d+)\.(\d+)(?:/|$)", re.IGNORECASE),
    re.compile(r"/opaanlp/(\d+)/(\d+)", re.IGNORECASE),
    re.compile(r"/product/(\d+)/(\d+)", re.IGNORECASE),
    re.compile(r"/(\d+)/(\d+)(?:[/?#]|$)", re.IGNORECASE),
)


def is_supported_shopee_url(value: object) -> bool:
    """Return True only for HTTP(S) URLs on an explicitly allowed Shopee host."""
    candidate = str(value or "").strip()
    if not candidate or any(ord(char) < 32 for char in candidate):
        return False
    try:
        parsed = urlparse(candidate)
        scheme = parsed.scheme.casefold()
        if scheme not in {"http", "https"}:
            return False
        if parsed.username is not None or parsed.password is not None:
            return False
        hostname = parsed.hostname
        if not hostname:
            return False
        hostname = hostname.encode("idna").decode("ascii").rstrip(".").casefold()
        port = parsed.port
    except (UnicodeError, ValueError):
        return False

    if not any(
        hostname == domain or hostname.endswith("." + domain)
        for domain in SHOPEE_ALLOWED_DOMAINS
    ):
        return False

    if port is not None and port != (443 if scheme == "https" else 80):
        return False
    return True


def extract_shopee_product_identity(value: object) -> str | None:
    """Extract exact shop/item IDs from a supported direct Shopee product URL."""
    candidate = str(value or "").strip()
    if not is_supported_shopee_url(candidate):
        return None
    try:
        path = urlparse(candidate).path or ""
    except ValueError:
        return None
    for pattern in PRODUCT_URL_PATTERNS:
        match = pattern.search(path)
        if match:
            return f"{match.group(1)}:{match.group(2)}"
    return None
