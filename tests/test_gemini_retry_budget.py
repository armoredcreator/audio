from __future__ import annotations

import json
from email.utils import formatdate

import pytest
import requests

from ArmoredIA.providers.base import AIProviderError
from ArmoredIA.providers.gemini import GeminiProvider


class Response:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self.payload = payload if payload is not None else {"candidates": [{
            "content": {"parts": [{"text": json.dumps({"captions": ["Canto bonito #casa"]})}]}
        }]}
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("secret-key-and-product-url-must-not-leak", response=self)

    def json(self):
        return self.payload

    def close(self):
        self.closed = True


class Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    for key in (
        "ARMORED_IA_MAX_ATTEMPTS", "ARMORED_IA_RETRY_DELAY", "ARMORED_IA_MAX_RETRY_DELAY",
        "ARMORED_IA_TOTAL_TIMEOUT", "ARMORED_IA_API_TIMEOUT", "ARMORED_IA_IMAGE_TIMEOUT",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "private-key")


def provider(responses, clock=None):
    clock = clock or Clock()
    receipts = []
    iterator = iter(responses)

    def request(*args, **kwargs):
        receipts.append(kwargs)
        response = next(iterator)
        if isinstance(response, BaseException):
            raise response
        return response

    return GeminiProvider(requester=request, sleeper=clock.sleep, clock=clock, wall_clock=lambda: 1000.0), clock, receipts


def test_503_uses_exponential_backoff_and_closes_every_response():
    responses = [Response(503), Response(503), Response()]
    client, clock, calls = provider(responses)
    assert client.generate_candidates("caption", {}, 10) == ["Canto bonito #casa"]
    assert clock.sleeps == [2.0, 4.0]
    assert len(calls) == 3
    assert all(response.closed for response in responses)
    assert [attempt["status"] for attempt in client.last_attempts] == ["TRANSIENT_UNAVAILABLE", "TRANSIENT_UNAVAILABLE", "SUCCESS"]


@pytest.mark.parametrize("header", ["7", formatdate(1007, usegmt=True)])
def test_retry_after_seconds_and_http_date_are_respected(header):
    client, clock, _ = provider([Response(503, headers={"Retry-After": header}), Response()])
    client.generate_candidates("caption", {}, 10)
    assert clock.sleeps == [7.0]


def test_google_retry_info_does_not_shorten_retry_after_header():
    payload = {"error": {"details": [{
        "@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "9.5s"
    }]}}
    client, clock, _ = provider([Response(429, payload, {"Retry-After": "7"}), Response()])
    client.generate_candidates("caption", {}, 10)
    assert clock.sleeps == [9.5]


def test_malformed_retry_detail_does_not_hide_later_valid_service_delay():
    payload = {"error": {"details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "invalids"},
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "12s"},
    ]}}
    client, clock, _ = provider([Response(503, payload), Response()])
    client.generate_candidates("caption", {}, 10)
    assert clock.sleeps == [12.0]


def test_long_server_delay_stops_instead_of_retrying_early():
    client, clock, calls = provider([Response(503, headers={"Retry-After": "120"})])
    with pytest.raises(AIProviderError) as error:
        client.generate_candidates("caption", {}, 10)
    assert error.value.retry_after == 120
    assert len(calls) == 1
    assert clock.sleeps == []
    assert client.last_attempts[0]["retry_stopped"] == "SERVER_DELAY_EXCEEDS_BUDGET"


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_auth_configuration_and_unsupported_model_fail_without_retry(status):
    client, clock, calls = provider([Response(status)])
    with pytest.raises(AIProviderError) as error:
        client.generate_candidates("caption", {}, 10)
    assert error.value.status_code == status
    assert "secret-key" not in str(error.value)
    assert len(calls) == 1
    assert clock.sleeps == []


def test_max_attempts_and_delay_cap_are_enforced(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "4")
    monkeypatch.setenv("ARMORED_IA_MAX_RETRY_DELAY", "3")
    client, clock, calls = provider([Response(503) for _ in range(4)])
    with pytest.raises(AIProviderError, match="4 tentativa"):
        client.generate_candidates("caption", {}, 10)
    assert clock.sleeps == [2.0, 3.0, 3.0]
    assert len(calls) == 4


def test_remaining_budget_caps_request_timeout_and_stops_wait(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_TOTAL_TIMEOUT", "5")
    clock = Clock()
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs)
        clock.now += 4
        return Response(503)

    client = GeminiProvider(requester=request, sleeper=clock.sleep, clock=clock)
    with pytest.raises(AIProviderError):
        client.generate_candidates("caption", {}, 10)
    assert calls[0]["timeout"] == 5
    assert len(calls) == 1
    assert clock.sleeps == []


def test_keyboard_interrupt_is_not_retried():
    client, clock, calls = provider([KeyboardInterrupt()])
    with pytest.raises(KeyboardInterrupt):
        client.generate_candidates("caption", {}, 10)
    assert len(calls) == 1
    assert clock.sleeps == []


def test_invalid_json_is_bounded_and_cannot_become_caption(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "2")
    client, clock, calls = provider([Response(payload=[]), Response(payload={"candidates": []})])
    with pytest.raises(AIProviderError) as error:
        client.generate_candidates("caption", {}, 10)
    assert error.value.category == "MALFORMED_RESPONSE"
    assert len(calls) == 2
    assert clock.sleeps == [2.0]


@pytest.mark.parametrize(("key", "value"), [
    ("ARMORED_IA_MAX_ATTEMPTS", "1000000"), ("ARMORED_IA_MAX_ATTEMPTS", "1.5"),
    ("ARMORED_IA_TOTAL_TIMEOUT", "nan"), ("ARMORED_IA_RETRY_DELAY", "inf"),
    ("ARMORED_IA_API_TIMEOUT", "-1"), ("ARMORED_IA_MAX_RETRY_DELAY", "invalid"),
])
def test_invalid_budget_fails_before_network(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    client, _, calls = provider([])
    with pytest.raises(AIProviderError):
        client.generate_candidates("caption", {}, 10)
    assert calls == []


def test_503_failure_then_restart_preserves_vision_original_and_state(tmp_path, monkeypatch):
    from ArmoredIA.caption.generator import CaptionGenerator
    from ArmoredIA.service import ArmoredIA
    from armored_core.database import Database
    from armored_core.models import State
    from armored_core.pipeline import Pipeline
    from armored_core.recovery import Recovery
    from armored_core.services import SyncService
    from armored_core.storage import Storage

    class Forbidden:
        def __getattr__(self, name):
            raise AssertionError("Unexpected Vision/Stock/Studio/Hub operation: " + name)

    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "2")
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    storage = Storage(tmp_path)
    database_path = storage.database / "isolated.sqlite3"
    db = Database(database_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"preserved historical original")
    item_id = SyncService(db, storage).ingest(source, "100", source_id="source-test")
    db.set_vision(item_id, "Product", "https://affiliate.invalid/product", ia_context={"productName": "Product", "shopId": "10", "itemId": "20"})
    original_path = db.get(item_id).original_path
    client, _, calls = provider([Response(503), Response(503), Response()])
    service = ArmoredIA(CaptionGenerator(provider=client))
    pipeline = Pipeline(db, storage, Forbidden(), Forbidden(), Forbidden(), service)
    with pytest.raises(AIProviderError):
        pipeline.run(item_id, stop_after_ia=True)
    assert db.get(item_id).state is State.RECOVERY
    assert not db.get(item_id).publication_caption
    assert db.publication(item_id) is None
    db.close()

    db = Database(database_path)
    speech_boundary = []
    try:
        recovery = Recovery(db, storage, Forbidden(), Forbidden(), Forbidden(), service, speech_studio_runner=speech_boundary.append)
        recovery.reconcile(item_id)
        item = db.get(item_id)
        assert item.state is State.SPEECH
        assert item.publication_caption == "Canto 🧡 bonito\n#casa"
        assert speech_boundary == [item_id]
        assert len(calls) == 3
        assert item.original_path == original_path
        assert original_path.read_bytes() == b"preserved historical original"
        assert db.publication(item_id) is None
        assert item.ia_context["itemId"] == "20"
    finally:
        db.close()
