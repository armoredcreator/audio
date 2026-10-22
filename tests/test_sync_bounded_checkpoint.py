import asyncio
from types import SimpleNamespace

import pytest

from ArmoredSync.service import SyncMessage, TelegramSource
from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.storage import Storage


def test_bounded_live_batch_uses_canonical_candidate_checkpoint():
    source = TelegramSource.__new__(TelegramSource)
    async def one():
        return SyncMessage("11", source_id="synthetic-source"), {1: 11}
    source.fetch_live_candidate_async = one
    messages, checkpoints = asyncio.run(source.fetch_live_batch_async(limit=1))
    assert [m.telegram_message_id for m in messages] == ["11"]
    assert checkpoints == {1: 11}


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_bounded_live_batch_refuses_invalid_limit(limit):
    source = TelegramSource.__new__(TelegramSource)
    with pytest.raises(ValueError):
        asyncio.run(source.fetch_live_batch_async(limit=limit))


def test_legacy_adapter_cannot_advance_checkpoint_past_discarded_messages(tmp_path):
    storage = Storage(tmp_path)
    db = Database(storage.database / "test.sqlite3")
    committed = []
    async def old_batch():
        return [SyncMessage("11"), SyncMessage("12")], {1: 12}
    source = SimpleNamespace(fetch_live_batch_async=old_batch,
                             commit_live_checkpoints=committed.append)
    coordinator = Coordinator(db, storage, None, None, None, source)
    try:
        with pytest.raises(RuntimeError, match="at most one"):
            asyncio.run(coordinator.run_live_once_async())
        assert committed == []
        assert db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
    finally:
        coordinator.close()
