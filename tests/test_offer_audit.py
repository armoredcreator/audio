from __future__ import annotations

import sqlite3
import time
from types import SimpleNamespace

import pytest

from ArmoredVision.offer_audit import OfferAudit
from ArmoredVision.modules.v1.shopee_api import (
    ShopeeAPIError, ShopeeAffiliateAPI, ShopeeAuthenticationError,
    ShopeeProductNotFoundError, ShopeeRateLimitError,
)
from ArmoredVision.service import ArmoredVision
from armored_core.database import Database
from armored_core.services import VisionUnresolvedError
from armored_core.storage import Storage


def rows():
    return [dict(content_id=str(i), canonical_product_identity={"shop_id": "10", "item_id": str(i)}, primary_cause="HISTORICAL") for i in (20, 21)]


class API:
    last_response_metadata = {"status_code": 200, "headers": {"Authorization": "private"}}

    def __init__(self, error):
        self.error, self.calls = error, 0

    def get_exact_product(self, *args, **kwargs):
        assert kwargs == {"allow_transient_retries": False}
        self.calls += 1
        raise self.error


def test_audit_refuses_an_existing_foreign_database(tmp_path):
    path = tmp_path / "foreign.offer-audit.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE important(value)")
    before = path.read_bytes()
    with pytest.raises(ValueError, match="not an offer audit"):
        OfferAudit(path)
    assert path.read_bytes() == before


def test_changed_manifest_identity_cannot_silently_replace_prior_record(tmp_path):
    audit = OfferAudit(tmp_path / "checks.offer-audit.sqlite3")
    audit.seed(rows())
    changed = rows()[0]
    changed["canonical_product_identity"] = {"shop_id": "10", "item_id": "999"}
    with pytest.raises(ValueError, match="identity changed"):
        audit.seed([changed])
    assert audit.summary()["identities"] == 2
    audit.close()


def test_long_retry_after_stops_instead_of_shortening_the_server_window(monkeypatch):
    api = ShopeeAffiliateAPI("test-app", "test-secret")
    response = SimpleNamespace(status_code=503, headers={"Retry-After": "300"}, close=lambda: None)
    calls, sleeps = [], []
    def post(*args, **kwargs):
        calls.append(1)
        return response
    monkeypatch.setattr(api.session, "post", post)
    monkeypatch.setattr("ArmoredVision.modules.v1.shopee_api.time.sleep", sleeps.append)
    with pytest.raises(ShopeeRateLimitError) as caught:
        api.get_exact_product("10", "20")
    assert caught.value.retry_after == "300"
    assert sleeps == []
    assert len(calls) == 1
    api.close()


def test_budget_is_persistent_and_resume_never_replays_completed_request(tmp_path):
    path = tmp_path / "checks.offer-audit.sqlite3"
    audit = OfferAudit(path)
    audit.seed(rows())
    api = API(ShopeeProductNotFoundError("valid empty nodes"))
    result = audit.run(api, budget_id="single-probe")
    assert result["stop_reason"] == "QUOTA_BUDGET_UNVERIFIED"
    assert result["summary"]["by_outcome"] == {"NOT_QUERIED": 1, "OFFER_ABSENT": 1}
    audit.close()
    resumed = OfferAudit(path)
    assert resumed.run(api, budget_id="single-probe")["requests_this_run"] == 0
    assert api.calls == 1
    assert "private" not in str(resumed.db.execute("SELECT metadata_json FROM products WHERE outcome='OFFER_ABSENT'").fetchone()[0])
    with pytest.raises(ValueError, match="larger budgets"):
        resumed.run(api, budget_id="unlimited", request_limit=100)
    resumed.close()


@pytest.mark.parametrize("error,outcome", [(ShopeeAuthenticationError("auth"), "AUTH_FAILURE"), (ShopeeRateLimitError("quota"), "QUOTA_FAILURE"), (ShopeeAPIError("incomplete"), "INVALID_RESPONSE")])
def test_technical_failures_are_not_absence_and_stop_batch(tmp_path, error, outcome):
    audit = OfferAudit(tmp_path / "checks.offer-audit.sqlite3")
    audit.seed(rows())
    api = API(error)
    result = audit.run(api, budget_id="verified", request_limit=2, verified_budget=True,
                       evidence="test-only grant", valid_until=time.time() + 60, interval_seconds=0)
    assert api.calls == 1
    assert result["summary"]["by_outcome"] == {"NOT_QUERIED": 1, outcome: 1}
    assert result["stop_reason"] == outcome
    audit.close()


def test_interrupted_request_is_charged_and_not_automatically_replayed(tmp_path):
    path = tmp_path / "checks.offer-audit.sqlite3"
    audit = OfferAudit(path)
    audit.seed(rows())
    api = API(KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        audit.run(api, budget_id="single-probe")
    audit.close()
    audit = OfferAudit(path)
    assert audit.run(api, budget_id="single-probe")["requests_this_run"] == 0
    assert audit.summary()["by_outcome"]["IN_FLIGHT"] == 1
    assert api.calls == 1
    audit.close()


@pytest.mark.parametrize("body", [{}, {"data": None}, {"data": {}}, {"data": {"productOfferV2": None}}, {"data": {"productOfferV2": {"nodes": None}}}])
def test_incomplete_product_response_does_not_mean_no_offer(monkeypatch, body):
    api = ShopeeAffiliateAPI("test-app", "test-secret")
    monkeypatch.setattr(api, "_post", lambda *a, **kw: body)
    try:
        with pytest.raises(ShopeeAPIError) as caught:
            api.get_exact_product("10", "20")
        assert not isinstance(caught.value, ShopeeProductNotFoundError)
    finally:
        api.close()


def test_explicit_empty_product_nodes_is_the_only_absence_case(monkeypatch):
    api = ShopeeAffiliateAPI("test-app", "test-secret")
    monkeypatch.setattr(api, "_post", lambda *a, **kw: {"data": {"productOfferV2": {"nodes": []}}})
    try:
        with pytest.raises(ShopeeProductNotFoundError):
            api.get_exact_product("10", "20")
    finally:
        api.close()


@pytest.mark.parametrize("error", [ShopeeAuthenticationError("auth"), ShopeeRateLimitError("quota")])
def test_vision_does_not_persist_auth_or_quota_as_negative_cache(tmp_path, monkeypatch, error):
    storage = Storage(tmp_path)
    db = Database(storage.database / "test.sqlite3")
    content_id = db.reserve_item("20", original_path=storage.original("20"), original_url="https://shopee.com.br/product/10/20")
    monkeypatch.setattr("ArmoredVision.service.resolve_short_url", lambda *a, **kw: SimpleNamespace(shop_id="10", item_id="20"))
    class FailingAPI:
        def get_exact_product(self, *args):
            raise error
    try:
        with pytest.raises(type(error)):
            ArmoredVision(api=FailingAPI(), db=db).identify(db.get(content_id))
        assert db.conn.execute("SELECT COUNT(*) FROM vision_product_lookup_events").fetchone()[0] == 0
    finally:
        db.close()
