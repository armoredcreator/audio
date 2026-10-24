import asyncio
from types import SimpleNamespace

import pytest

from ArmoredSync.service import TelegramSource


def test_telegram_download_refuses_existing_target_before_network(tmp_path):
    target=tmp_path/'original.mp4'
    target.write_bytes(b'preserved original')
    source=TelegramSource.__new__(TelegramSource)
    def forbidden(*args,**kwargs):raise AssertionError('Must not start another download')
    source.reader=SimpleNamespace(client=SimpleNamespace(iter_download=forbidden))
    with pytest.raises(FileExistsError):
        asyncio.run(source._download_to(SimpleNamespace(id=1),target))
    assert target.read_bytes()==b'preserved original'


def test_materialization_retry_also_refuses_existing_target_before_connecting(tmp_path):
    target=tmp_path/'original.mp4';target.write_bytes(b'preserved original')
    async def forbidden():raise AssertionError('Must not connect for existing target')
    source=TelegramSource(tmp_path,SimpleNamespace(connect=forbidden),source='-100123',source_id='source1')
    with pytest.raises(FileExistsError):
        asyncio.run(source.materialize_candidate_async('1',target))
    assert target.read_bytes()==b'preserved original'


def test_bounded_retry_archives_failed_download_bytes(tmp_path,monkeypatch):
    from armored_core.storage import Storage
    storage=Storage(tmp_path)
    target=storage.workspace('1')/'video.mp4.part'
    async def noop(*args):pass
    async def message(*args,**kwargs):return SimpleNamespace(id=1,video=True)
    reader=SimpleNamespace(connect=noop,disconnect=noop,client=SimpleNamespace(get_messages=message))
    source=TelegramSource(tmp_path,reader,source='-100123',source_id='source1')
    calls=[]
    async def download(message,path):
        calls.append(path)
        path.write_bytes(b'failed bytes' if len(calls)==1 else b'complete bytes')
        if len(calls)==1:raise TimeoutError('injected interruption')
    monkeypatch.setattr(source,'_download_to',download)
    monkeypatch.setattr('ArmoredSync.service.asyncio.sleep',noop)
    asyncio.run(source.materialize_candidate_async('1',target))
    assert len(calls)==2
    assert target.read_bytes()==b'complete bytes'
    assert [p.read_bytes() for p in storage.backups.rglob(target.name)]==[b'failed bytes']
