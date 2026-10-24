from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ArmoredVision.query_budget import QueryBudgetError, ShopeeQueryBudget


def _expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()


def test_query_budget_is_durable_and_stops_at_authorized_limit(tmp_path: Path):
    path = tmp_path / "budget.sqlite3"
    expiry = _expiry()
    first = ShopeeQueryBudget(path)
    assert first.consume(
        budget_id="first-start", request_limit=1, evidence="operator-approved-quota",
        valid_until=expiry,
    ) == 1
    first.close()

    resumed = ShopeeQueryBudget(path)
    with pytest.raises(QueryBudgetError, match="exhausted"):
        resumed.consume(
            budget_id="first-start", request_limit=1, evidence="operator-approved-quota",
            valid_until=expiry,
        )
    resumed.close()


def test_query_budget_rejects_reconfiguration_and_expired_authority(tmp_path: Path):
    budget = ShopeeQueryBudget(tmp_path / "budget.sqlite3")
    expiry = _expiry()
    budget.consume(
        budget_id="first-start", request_limit=2, evidence="approved",
        valid_until=expiry,
    )
    with pytest.raises(QueryBudgetError, match="configuration-conflict"):
        budget.consume(
            budget_id="first-start", request_limit=3, evidence="approved",
            valid_until=expiry,
        )
    with pytest.raises(QueryBudgetError, match="expired"):
        budget.consume(
            budget_id="expired", request_limit=1, evidence="approved",
            valid_until=(datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),
        )
    budget.close()


def test_query_budget_limit_validation(tmp_path: Path):
    budget = ShopeeQueryBudget(tmp_path / "budget.sqlite3")
    with pytest.raises(QueryBudgetError, match="1-to-1000"):
        budget.consume(
            budget_id="bad", request_limit=0, evidence="approved",
            valid_until=_expiry(),
        )
    budget.close()


def test_production_vision_fails_closed_without_quota_authorization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
    from ArmoredVision.service import ArmoredVision
    from ArmoredVision.modules.v1.shopee_resolver import ShopeeResolvedLink
    from armored_core.database import Database
    from armored_core.services import VisionUnresolvedError

    db = Database(tmp_path / "items.sqlite3")
    content_id = db.reserve_item(
        "1", source_id="source", original_url="https://shopee.com.br/product/10/20",
        original_path=tmp_path / "original.mp4",
    )

    class ProductionClient(ShopeeAffiliateAPI):
        def __init__(self):
            self.session = object()
            self._product_cache = {}
            self.calls = 0

        def get_exact_product(self, shop_id, item_id):
            self.calls += 1
            raise AssertionError("query must be blocked before transport")

    client = ProductionClient()
    monkeypatch.delenv("ARMORED_SHOPEE_QUERIES_AUTHORIZED", raising=False)
    monkeypatch.setattr(
        "ArmoredVision.service.resolve_short_url",
        lambda *_args, **_kwargs: ShopeeResolvedLink(
            "https://shopee.com.br/product/10/20",
            "https://shopee.com.br/product/10/20", "10", "20",
        ),
    )
    with pytest.raises(VisionUnresolvedError, match="SHOPEE_QUERY_BUDGET_BLOCKED"):
        ArmoredVision(api=client, db=db).identify(db.get(content_id))
    assert client.calls == 0
    db.close()


def test_live_vision_charges_lookup_and_short_link_as_separate_requests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import json

    from ArmoredVision.modules.v1.shopee_api import ShopeeAffiliateAPI
    from ArmoredVision.modules.v1.shopee_resolver import ShopeeResolvedLink
    from ArmoredVision.service import ArmoredVision
    from armored_core.database import Database
    from armored_core.services import VisionUnresolvedError

    db = Database(tmp_path / "items.sqlite3")
    content_id = db.reserve_item(
        "3", source_id="source", original_url="https://shopee.com.br/product/10/20",
        original_path=tmp_path / "original.mp4",
    )
    monkeypatch.setenv("ARMORED_SHOPEE_QUERIES_AUTHORIZED", "1")
    monkeypatch.setenv("ARMORED_SHOPEE_QUERY_BUDGET_ID", "certification-small")
    monkeypatch.setenv("ARMORED_SHOPEE_QUERY_LIMIT", "1")
    monkeypatch.setenv("ARMORED_SHOPEE_QUOTA_EVIDENCE", "test-only approved budget")
    monkeypatch.setenv("ARMORED_SHOPEE_QUOTA_VALID_UNTIL", _expiry())
    monkeypatch.delenv("ARMORED_SHOPEE_QUERY_LEDGER_PATH", raising=False)
    monkeypatch.setattr(
        "ArmoredVision.service.resolve_short_url",
        lambda *_args, **_kwargs: ShopeeResolvedLink(
            "https://shopee.com.br/product/10/20",
            "https://shopee.com.br/product/10/20", "10", "20",
        ),
    )

    class Response:
        status_code = 200
        headers = {}
        def __init__(self, body):
            self.body = body
        def json(self):
            return self.body
        def close(self):
            pass

    api = ShopeeAffiliateAPI("test", "test")
    calls = []
    def post(_url, *, data, **_kwargs):
        payload = json.loads(data.decode())
        calls.append(payload["query"])
        if "productOfferV2" in payload["query"]:
            return Response({"data": {"productOfferV2": {"nodes": [{
                "shopId": "10", "itemId": "20", "productName": "Produto",
                "productLink": "https://shopee.com.br/product/10/20",
            }]}}})
        return Response({"data": {"generateShortLink": {"shortLink": "https://s.shopee.com.br/test"}}})
    monkeypatch.setattr(api.session, "post", post)

    with pytest.raises(VisionUnresolvedError, match="SHOPEE_QUERY_BUDGET_BLOCKED"):
        ArmoredVision(api=api, db=db).identify(db.get(content_id))
    assert len(calls) == 1
    with __import__("sqlite3").connect(tmp_path / "shopee-query-budget.sqlite3") as ledger:
        assert ledger.execute("SELECT used FROM budgets WHERE budget_id='certification-small'").fetchone()[0] == 1
    api.close()
    db.close()
