from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from ArmoredStock.service import ArmoredStock
from armored_core.models import State
from armored_core.storage import Storage


class FakeDatabase:
    def __init__(self, item) -> None:
        self.item = item
        self.repaired_paths: list[Path] = []

    def repair_original_path(self, content_id: str, path: Path) -> None:
        assert content_id == self.item.content_id
        self.repaired_paths.append(Path(path))
        self.item.original_path = Path(path)

    def get(self, content_id: str):
        assert content_id == self.item.content_id
        return self.item


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _item(path: Path):
    return SimpleNamespace(
        content_id="source1_123",
        telegram_message_id="123",
        source_id="source1",
        topic_id=None,
        topic_name=None,
        original_url="https://shopee.com.br/product/700/800",
        affiliate_url="https://affiliate.example/product",
        affiliate_name="fixture",
        state=State.RECEIVED,
        original_path=path,
        original_sha256=_sha256(path) if path.is_file() else "",
    )


def test_stock_refuses_external_original_even_when_file_exists(tmp_path):
    lab_root = tmp_path / "AudioLab"
    production_root = tmp_path / "ArmoredCreator-final"
    production_root.mkdir(parents=True)
    original = production_root / "Videos GRUPO_FONTE_1" / "123_original.mp4"
    original.parent.mkdir(parents=True)
    original.write_bytes(b"immutable-production-original")
    before = _sha256(original)

    storage = Storage(lab_root)
    item = _item(original)
    db = FakeDatabase(item)
    coordinator = SimpleNamespace(storage=storage, db=db, source=None)

    with pytest.raises(RuntimeError, match="refusing-to-repair-original-outside-storage-root"):
        asyncio.run(ArmoredStock(coordinator).materialize_item(item))

    assert original.is_file()
    assert _sha256(original) == before
    assert db.repaired_paths == []


def test_stock_refuses_external_missing_original_path(tmp_path):
    lab_root = tmp_path / "AudioLab"
    production_root = tmp_path / "ArmoredCreator-final"
    production_root.mkdir(parents=True)
    missing_external = production_root / "Videos GRUPO_FONTE_1" / "missing.mp4"

    storage = Storage(lab_root)
    # Build the item without reading the missing path.
    item = SimpleNamespace(
        content_id="source1_124",
        telegram_message_id="124",
        source_id="source1",
        topic_id=None,
        topic_name=None,
        original_url="https://shopee.com.br/product/700/801",
        affiliate_url="https://affiliate.example/product2",
        affiliate_name="fixture",
        state=State.RECEIVED,
        original_path=missing_external,
        original_sha256="",
    )
    db = FakeDatabase(item)
    coordinator = SimpleNamespace(storage=storage, db=db, source=None)

    with pytest.raises(RuntimeError, match="refusing-to-repair-original-outside-storage-root"):
        asyncio.run(ArmoredStock(coordinator).materialize_item(item))

    assert not missing_external.exists()
    assert db.repaired_paths == []


def test_stock_still_repairs_legacy_path_inside_storage_root(tmp_path):
    lab_root = tmp_path / "AudioLab"
    legacy_original = lab_root / "legacy" / "original.mp4"
    legacy_original.parent.mkdir(parents=True)
    legacy_original.write_bytes(b"immutable-lab-original")
    expected_hash = _sha256(legacy_original)

    storage = Storage(lab_root)
    item = _item(legacy_original)
    item.original_sha256 = expected_hash
    db = FakeDatabase(item)
    coordinator = SimpleNamespace(storage=storage, db=db, source=None)
    canonical = storage.original(
        item.content_id,
        ".mp4",
        original_url=item.original_url,
        source_id=item.source_id,
    ).resolve()

    outcome = asyncio.run(ArmoredStock(coordinator).materialize_item(item))

    assert outcome == "existing"
    assert not legacy_original.exists()
    assert canonical.is_file()
    assert _sha256(canonical) == expected_hash
    assert db.repaired_paths == [canonical]
