import json

import pytest
import requests

from ArmoredIA.service import ArmoredIA
from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.services import SyncService
from armored_core.storage import Storage


class Response:
    status_code = 200
    headers = {}
    def __init__(self, body, status=200):
        self.body, self.status_code = body, status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("synthetic failure", response=self)
    def json(self): return self.body
    def close(self): pass


@pytest.mark.parametrize("provider", ["gemini", "ollama"])
@pytest.mark.parametrize("failed", [False, True])
def test_selected_provider_is_persisted_without_automatic_switch(tmp_path, monkeypatch, provider, failed):
    monkeypatch.setenv("ARMORED_IA_PROVIDER", provider)
    monkeypatch.setenv("ARMORED_IA_MODEL", "synthetic-gemini-model")
    monkeypatch.setenv("ARMORED_IA_OLLAMA_MODEL", "synthetic-ollama-model")
    monkeypatch.setenv("GEMINI_API_KEY", "synthetic-test-key")
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("ARMORED_IA_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    storage = Storage(tmp_path)
    db = Database(storage.database / "test.sqlite3")
    source = tmp_path / "source.mp4"
    source.write_bytes(b"synthetic original")
    item_id = SyncService(db, storage).ingest(source, "1", source_id="synthetic")
    db.set_vision(item_id, "Example", "https://affiliate.invalid/example", ia_context={"productName": "Abajur de mesa", "shopId": "10", "itemId": "20"})
    ia = ArmoredIA()
    raw = json.dumps({"captions": ["Luz serena #casa"]})
    body = {"response": raw} if provider == "ollama" else {"candidates": [{"content": {"parts": [{"text": raw}]}}]}
    calls = []
    def request(*args, **kwargs):
        calls.append(ia.provider_configuration()["provider"])
        return Response(body, 503 if failed else 200)
    ia.caption_generator.provider.requester = request
    coordinator = Coordinator(db, storage, None, None, None, ia=ia)
    try:
        if failed:
            with pytest.raises(Exception):
                coordinator.pipeline.run(item_id, stop_after_ia=True)
            assert db.get(item_id).state is State.RECOVERY
        else:
            coordinator.pipeline.run(item_id, stop_after_ia=True)
            assert db.get(item_id).state is State.SPEECH
            assert db.get(item_id).publication_caption == "Luz 🧡 serena\n#casa"
        receipt = db.conn.execute("SELECT * FROM ia_generation_attempts WHERE content_id=?", (item_id,)).fetchone()
        assert receipt["provider"] == provider
        assert receipt["model"] == f"synthetic-{provider}-model"
        assert receipt["status"] == ("FAILED" if failed else "COMPLETE")
        assert calls == [provider]
        assert db.publication(item_id) is None
        trace = storage.root / "storage" / "logs" / "pipeline_trace.jsonl"
        records = [json.loads(line) for line in trace.read_text(encoding="utf-8").splitlines()]
        assert any(row.get("event") == "CONFIGURED" and row.get("provider") == provider for row in records)
    finally:
        coordinator.close()
