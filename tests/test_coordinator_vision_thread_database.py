import asyncio
from types import SimpleNamespace

import pytest

from ArmoredSync.service import SyncMessage
from ArmoredVision.service import ArmoredVision
from ArmoredVision.modules.v1.shopee_api import ShopeeAuthenticationError
from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.storage import Storage


def test_offloaded_vision_uses_thread_owned_sqlite_negative_cache(tmp_path, monkeypatch):
    storage = Storage(tmp_path)
    db = Database(storage.database / 'isolated.sqlite3')
    db.record_vision_product_lookup('123:456','prior-fixture','OFFER_ABSENT',ttl_seconds=3600)
    def forbidden(*args): raise AssertionError('Cached product must not call API')
    api = SimpleNamespace(get_exact_product=forbidden)
    vision = ArmoredVision(api=api,db=db)
    monkeypatch.setattr('ArmoredVision.service.resolve_short_url',
        lambda *args,**kwargs:SimpleNamespace(shop_id='123',item_id='456'))
    coordinator = Coordinator(db,storage,vision,None,None)
    try:
        item_id,item = asyncio.run(coordinator._vision_only_async(
            SyncMessage('77',source_id='source_test',original_url='https://shopee.com.br/product/123/456',materialize=forbidden),offload=True))
        assert item.state is State.WAITING_VISION
        assert db.get(item_id).state is State.WAITING_VISION
        assert 'NO_AFFILIATE_OFFER_CACHED' in db.last_error(item_id)
        assert vision.db is db
        assert db.conn.execute('SELECT COUNT(*) FROM vision_product_lookup_events').fetchone()[0] == 1
    finally:
        coordinator.close()


@pytest.mark.parametrize('authentication_failure', [False, True])
def test_offloaded_vision_persists_lookup_or_technical_failure_without_cross_thread_access(tmp_path, monkeypatch, authentication_failure):
    storage = Storage(tmp_path)
    db = Database(storage.database / 'isolated.sqlite3')
    calls=[]
    def lookup(shop,item):
        calls.append((shop,item))
        if authentication_failure: raise ShopeeAuthenticationError('synthetic authentication failure')
        return {'shopId':shop,'itemId':item,'productName':'isolated fixture'}
    api=SimpleNamespace(get_exact_product=lookup,affiliate_link_for_product=lambda product:'https://s.shopee.com.br/isolated-fixture')
    vision=ArmoredVision(api=api,db=db)
    monkeypatch.setattr('ArmoredVision.service.resolve_short_url',lambda *args,**kwargs:SimpleNamespace(shop_id='123',item_id='456'))
    coordinator=Coordinator(db,storage,vision,None,None)
    message=SyncMessage('78',source_id='source_test',original_url='https://shopee.com.br/product/123/456',materialize=lambda target:pytest.fail('Vision must not download'))
    try:
        if authentication_failure:
            with pytest.raises(ShopeeAuthenticationError):
                asyncio.run(coordinator._vision_only_async(message,offload=True))
            assert db.get(db.content_id_for('78','source_test')).state is State.VISION
            assert db.conn.execute('SELECT COUNT(*) FROM vision_product_lookup_events').fetchone()[0]==0
        else:
            item_id,item=asyncio.run(coordinator._vision_only_async(message,offload=True))
            assert item.state is State.RECEIVED
            assert item.affiliate_url=='https://s.shopee.com.br/isolated-fixture'
            assert not item.original_path.is_file()
            assert db.conn.execute('SELECT outcome FROM vision_product_lookup_events').fetchone()[0]=='OFFER_FOUND'
        assert calls==[('123','456')]
        assert vision.db is db
    finally:
        coordinator.close()
