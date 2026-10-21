from __future__ import annotations

from types import SimpleNamespace

import pytest

from armored_core.shopee_urls import is_supported_shopee_url
from ArmoredSync.service import TelegramSource
from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url


class FakeResponse:
    def __init__(self, status_code, url, *, headers=None):
        self.status_code = status_code
        self.url = url
        self.headers = headers or {}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def close(self):
        self.closed = True


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if not self.responses:
            raise AssertionError("Requisição de rede inesperada")
        return self.responses.pop(0)


@pytest.mark.parametrize(
    "url",
    [
        "https://shopee.com.br/product/123/456",
        "https://s.shopee.com.br/short",
        "https://shopee.co/product/123/456",
        "https://shopee.ee/123/456",
        "https://shope.ee/short-code",
        "https://subdomain.shopee.com.br/product/123/456",
    ],
)
def test_allowed_shopee_hosts_and_subdomains(url):
    assert is_supported_shopee_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "javascript://shopee.com.br/product/1/2",
        "https://evilshopee.com.br/product/1/2",
        "https://shopee.com.br.attacker.test/product/1/2",
        "https://attacker.test/path/shopee.com.br/product/1/2",
        "https://shopee.com.br@attacker.test/product/1/2",
        "https://shopee.com.br:8443/product/1/2",
        "https://127.0.0.1/product/1/2",
        "https://shopee.com.br:invalid/product/1/2",
    ],
)
def test_rejects_spoofed_or_unsafe_shopee_hosts(url):
    assert not is_supported_shopee_url(url)


def test_sync_does_not_extract_shopee_substring_from_untrusted_host():
    fake = SimpleNamespace(
        message="Oferta https://attacker.test/path/shopee.com.br/product/1/2",
        entities=[],
    )
    assert TelegramSource._shopee_url(fake) is None


def test_sync_accepts_real_shopee_short_domain():
    url = "https://shope.ee/short-code"
    fake = SimpleNamespace(message=url, entities=[])
    assert TelegramSource._shopee_url(fake) == url


def test_vision_rejects_untrusted_product_shaped_url_before_network():
    session = FakeSession([])
    with pytest.raises(ValueError, match="domínios Shopee permitidos"):
        resolve_short_url("https://attacker.test/product/123/456", session=session)
    assert session.calls == []


def test_vision_blocks_redirect_to_untrusted_host_before_second_request():
    response = FakeResponse(
        302,
        "https://shope.ee/short-code",
        headers={"Location": "http://127.0.0.1/admin"},
    )
    session = FakeSession([response])

    with pytest.raises(ValueError, match="Redirecionamento fora"):
        resolve_short_url("https://shope.ee/short-code", session=session)

    assert len(session.calls) == 1
    assert session.calls[0][1]["allow_redirects"] is False
    assert response.closed


def test_vision_allows_redirect_chain_only_within_shopee_hosts():
    redirect = FakeResponse(
        302,
        "https://shope.ee/short-code",
        headers={"Location": "https://shopee.com.br/product/123/456"},
    )
    final = FakeResponse(200, "https://shopee.com.br/product/123/456")
    session = FakeSession([redirect, final])

    result = resolve_short_url("https://shope.ee/short-code", session=session)

    assert (result.shop_id, result.item_id) == ("123", "456")
    assert len(session.calls) == 2
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
    assert redirect.closed and final.closed
