from __future__ import annotations

from collections import deque

import pytest
import requests

from ArmoredVision.modules.v1.shopee_api import (
    ShopeeAPIError,
    ShopeeAffiliateAPI,
    ShopeeAuthenticationError,
    ShopeeMutationOutcomeUnknownError,
    ShopeeRateLimitError,
    ShopeeTransientAPIError,
)


class FakeResponse:
    def __init__(self, status_code=200, body=None, headers=None):
        self.status_code = status_code
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, outcomes):
        self.outcomes = deque(outcomes)
        self.calls = []

    def post(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        outcome = self.outcomes.popleft()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self):
        pass


def success_product_response():
    return FakeResponse(
        body={
            "data": {
                "productOfferV2": {
                    "nodes": [
                        {
                            "shopId": "10",
                            "itemId": "20",
                            "productName": "Produto teste",
                            "offerLink": "https://affiliate.example/offer",
                        }
                    ]
                }
            }
        }
    )


def make_api(monkeypatch, outcomes):
    monkeypatch.setenv("SHOPEE_API_MAX_RETRIES", "3")
    monkeypatch.setenv("SHOPEE_API_RETRY_BASE_SECONDS", "0")
    api = ShopeeAffiliateAPI(app_id="test-app", secret_key="test-secret")
    api.session = FakeSession(outcomes)
    sleeps = []
    monkeypatch.setattr("ArmoredVision.modules.v1.shopee_api.time.sleep", sleeps.append)
    return api, sleeps


def test_read_only_exact_lookup_retries_transient_503_and_honors_retry_after(monkeypatch):
    api, sleeps = make_api(
        monkeypatch,
        [
            FakeResponse(503, headers={"Retry-After": "2", "X-RateLimit-Remaining": "8"}),
            success_product_response(),
        ],
    )

    product = api.get_exact_product("10", "20")

    assert product["itemId"] == "20"
    assert len(api.session.calls) == 2
    assert sleeps == [2.0]


def test_authentication_failure_is_not_retried_and_does_not_leak_secrets(monkeypatch):
    api, sleeps = make_api(
        monkeypatch,
        [FakeResponse(401, headers={"X-Request-Id": "request-123"})],
    )

    with pytest.raises(ShopeeAuthenticationError) as exc:
        api.get_exact_product("10", "20")

    assert len(api.session.calls) == 1
    assert sleeps == []
    assert "test-secret" not in str(exc.value)
    assert "request-123" not in str(exc.value)


def test_http_429_is_not_retried_and_preserves_safe_quota_metadata(monkeypatch):
    api, sleeps = make_api(
        monkeypatch,
        [
            FakeResponse(
                429,
                headers={
                    "Retry-After": "60",
                    "X-RateLimit-Limit": "100",
                    "X-RateLimit-Remaining": "0",
                    "X-Request-Id": "request-429",
                    "Authorization": "must-not-be-retained",
                },
            )
        ],
    )

    with pytest.raises(ShopeeRateLimitError) as exc:
        api.get_exact_product("10", "20")

    assert len(api.session.calls) == 1
    assert sleeps == []
    assert exc.value.status_code == 429
    assert exc.value.retry_after == "60"
    assert exc.value.response_metadata == {
        "retry-after": "60",
        "x-ratelimit-limit": "100",
        "x-ratelimit-remaining": "0",
        "x-request-id": "request-429",
    }
    assert "Authorization" not in exc.value.response_metadata


def test_graphql_invalid_signature_is_not_retried(monkeypatch):
    api, sleeps = make_api(
        monkeypatch,
        [
            FakeResponse(
                body={
                    "errors": [
                        {
                            "message": "Invalid signature",
                            "extensions": {"code": "INVALID_SIGNATURE"},
                        }
                    ]
                }
            )
        ],
    )

    with pytest.raises(ShopeeAuthenticationError):
        api.get_exact_product("10", "20")

    assert len(api.session.calls) == 1
    assert sleeps == []


def test_graphql_quota_error_is_not_retried(monkeypatch):
    api, sleeps = make_api(
        monkeypatch,
        [
            FakeResponse(
                body={
                    "errors": [
                        {
                            "message": "Too many requests",
                            "extensions": {"code": "TOO_MANY_REQUESTS"},
                        }
                    ]
                },
                headers={"Retry-After": "30", "X-RateLimit-Remaining": "0"},
            )
        ],
    )

    with pytest.raises(ShopeeRateLimitError) as exc:
        api.get_exact_product("10", "20")

    assert len(api.session.calls) == 1
    assert sleeps == []
    assert exc.value.retry_after == "30"
    assert exc.value.response_metadata["x-ratelimit-remaining"] == "0"


def test_mutation_timeout_is_not_retried_and_outcome_is_marked_unknown(monkeypatch):
    api, sleeps = make_api(monkeypatch, [requests.Timeout("timeout after send")])

    origin = "https://shopee.com.br/product/10/20"
    with pytest.raises(ShopeeMutationOutcomeUnknownError):
        api.generate_short_link(origin)
    # A second caller in the same client cannot blindly replay an ambiguous mutation.
    with pytest.raises(ShopeeMutationOutcomeUnknownError):
        api.generate_short_link(origin)

    assert len(api.session.calls) == 1
    assert sleeps == []


def test_mutation_5xx_is_not_retried_and_outcome_is_marked_unknown(monkeypatch):
    api, sleeps = make_api(monkeypatch, [FakeResponse(503, headers={"Retry-After": "5"})])

    origin = "https://shopee.com.br/product/10/20"
    with pytest.raises(ShopeeMutationOutcomeUnknownError) as exc:
        api.generate_short_link(origin)
    with pytest.raises(ShopeeMutationOutcomeUnknownError):
        api.generate_short_link(origin)

    assert len(api.session.calls) == 1
    assert sleeps == []
    assert exc.value.status_code == 503
    assert exc.value.response_metadata["retry-after"] == "5"


def test_mutation_missing_short_link_is_not_reported_as_success(monkeypatch):
    api, sleeps = make_api(
        monkeypatch,
        [FakeResponse(body={"data": {"generateShortLink": {"shortLink": None}}})],
    )

    with pytest.raises(ShopeeMutationOutcomeUnknownError):
        api.generate_short_link("https://shopee.com.br/product/10/20")

    assert len(api.session.calls) == 1
    assert sleeps == []


def test_generic_graphql_error_is_not_retried(monkeypatch):
    api, sleeps = make_api(
        monkeypatch,
        [FakeResponse(body={"errors": [{"message": "Invalid query field"}]})],
    )

    with pytest.raises(ShopeeAPIError):
        api.get_exact_product("10", "20")
    assert len(api.session.calls) == 1
    assert sleeps == []
