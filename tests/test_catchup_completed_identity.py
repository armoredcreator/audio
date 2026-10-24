import asyncio
from types import SimpleNamespace

import pytest

from ArmoredSync.service import SyncMessage
from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.storage import Storage


@pytest.mark.parametrize('preceding_failure', [False, True])
def test_completed_rediscovery_preserves_source_identity_and_checkpoint_fence(tmp_path, preceding_failure):
    storage = Storage(tmp_path)
    db = Database(storage.database / 'isolated.sqlite3')
    item_id = db.reserve_item('42', source_id='source_test', topic_id=7)
    db.transition(item_id, State.PUBLISHED, 'isolated completed-item fixture')
    db.conn.execute('UPDATE items SET cleanup_completed=1 WHERE content_id=?', (item_id,))
    db.conn.commit()
    messages = [SyncMessage('42', source_id='source_test', topic_id=7)]
    if preceding_failure:
        messages.insert(0, SyncMessage('41', source_id='source_test', topic_id=7))
    commits, marked = [], []
    async def fetch(): return messages.pop(0) if messages else None
    source = SimpleNamespace(fetch_next_async=fetch, mark_ingested=marked.append,
        commit_live_checkpoints=commits.append, historical_scan_exhausted=True)
    coordinator = Coordinator(db, storage, None, None, None, source)
    async def failed_gate(message): raise RuntimeError('isolated predecessor failure')
    coordinator._vision_gate_and_materialize_async = failed_gate
    try:
        asyncio.run(coordinator.run_catch_up_async())
        assert marked == ['42']
        assert commits == ([] if preceding_failure else [{7: 42}])
        assert db.get(item_id).state is State.PUBLISHED
    finally:
        coordinator.close()
