from pathlib import Path
import asyncio
import pytest

from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.recovery import Recovery
from armored_core.storage import Storage
from armored_core.services import IngestMessage, SyncService


def test_rebuild_archives_previous_results_and_partial_directories_idempotently(tmp_path):
    storage=Storage(tmp_path)
    db=Database(storage.database/'isolated.sqlite3')
    original=storage.original('1')
    original.write_bytes(b'immutable original')
    item_id=db.create_item('1',original)
    derived=original.parent/'previous.wav';derived.write_bytes(b'previous valid result')
    partial=original.parent/'partial-stage';partial.mkdir();(partial/'stem.wav').write_bytes(b'partial stem')
    recovery=Recovery(db,storage,None,None,None)
    try:
        recovery._reset_processing_artifacts(item_id)
        assert original.read_bytes()==b'immutable original'
        assert not derived.exists() and not partial.exists()
        assert [p.read_bytes() for p in storage.backups.rglob('previous.wav')]==[b'previous valid result']
        assert [p.read_bytes() for p in storage.backups.rglob('stem.wav')]==[b'partial stem']
        receipts=list(storage.backups.rglob('preservation.json'))
        assert len(receipts)==1
        recovery._reset_processing_artifacts(item_id)
        assert list(storage.backups.rglob('preservation.json'))==receipts
    finally:db.close()


def test_startup_preserves_interrupted_download_before_rediscovery(tmp_path):
    storage=Storage(tmp_path)
    db=Database(storage.database/'isolated.sqlite3')
    original=storage.original('2')
    item_id=db.reserve_item('2',original_path=original)
    partial=original.with_suffix('.mp4.part');partial.write_bytes(b'partial download')
    coordinator=Coordinator(db,storage,None,None,None)
    try:
        coordinator.recover_pending()
        assert not partial.exists()
        assert [p.read_bytes() for p in storage.backups.rglob(partial.name)]==[b'partial download']
        assert not original.exists()
        assert db.get(item_id).state.value=='RECEIVED'
    finally:coordinator.close()


@pytest.mark.parametrize('asynchronous',[False,True])
def test_sync_preserves_previous_and_failed_partial_downloads(tmp_path,asynchronous):
    storage=Storage(tmp_path)
    db=Database(storage.database/'isolated.sqlite3')
    original=storage.original('3')
    db.reserve_item('3',original_path=original)
    partial=original.with_suffix('.mp4.part');partial.write_bytes(b'previous interruption')
    def materialize(target):
        target.write_bytes(b'new interrupted download')
        raise RuntimeError('injected download interruption')
    service=SyncService(db,storage)
    message=IngestMessage('3',materialize=materialize)
    try:
        with pytest.raises(RuntimeError,match='injected download interruption'):
            if asynchronous:asyncio.run(service.ingest_message_async(message))
            else:service.ingest_message(message)
        contents=sorted(p.read_bytes() for p in storage.backups.rglob(partial.name))
        assert contents==sorted([b'previous interruption',b'new interrupted download'])
        assert not original.exists()
    finally:db.close()
