from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.recover_telegram_original import (
    MARKER, prepare_workspace, recover_one, select_record, session_lock, verify_media, normalize_phone, request_login_code,
)


def test_fresh_workspace_has_a_new_dedicated_session_path(tmp_path):
    root = prepare_workspace(tmp_path / "recovery")
    marker = json.loads((root / MARKER).read_text())
    assert marker["session_path"] == str(root / "telegram-recovery.session")
    assert not (root / "telegram-recovery.session").exists()
    assert prepare_workspace(root) == root


def test_existing_unknown_directory_and_session_are_never_adopted(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    session = root / "telegram-recovery.session"
    session.write_bytes(b"must-not-reuse")
    with pytest.raises(ValueError, match="not a dedicated"):
        prepare_workspace(root)
    assert session.read_bytes() == b"must-not-reuse"


@pytest.mark.parametrize("value", ["", "phone", "123", "+1234567890123456"])
def test_invalid_phone_is_rejected_before_network(value):
    with pytest.raises(RuntimeError, match="INVALID_PHONE_FORMAT"):
        normalize_phone(value)


def test_phone_is_normalized_without_persisting_its_value():
    assert normalize_phone("+55 (11) 99999-1234") == "5511999991234"


def test_login_follows_one_confirmed_dc_redirect_without_logging_phone():
    from telethon.errors import PhoneMigrateError
    calls=[];sent=object()
    class Client:
        session=SimpleNamespace(dc_id=2)
        async def __call__(self,request):
            calls.append(type(request).__name__)
            if len(calls)==1:
                self.session.dc_id=1
                raise PhoneMigrateError(request=request,capture=1)
            return sent
    diagnostics={}
    assert asyncio.run(request_login_code(Client(),'5511999991234',1,'synthetic',diagnostics)) is sent
    assert len(calls)==2
    assert diagnostics=={'login_dc_before':2,'login_dc_after':1,'login_request_attempts':2}
    assert '5511999991234' not in json.dumps(diagnostics)


@pytest.mark.parametrize('failure',['timeout','authentication','flood','repeated_redirect'])
def test_login_never_repeats_ambiguous_auth_or_quota_failure(failure):
    from telethon.errors import ApiIdInvalidError,FloodWaitError,PhoneMigrateError
    calls=[]
    class Client:
        session=SimpleNamespace(dc_id=2)
        async def __call__(self,request):
            calls.append(request)
            if failure=='timeout':raise TimeoutError('synthetic timeout')
            if failure=='authentication':raise ApiIdInvalidError(request=request)
            if failure=='flood':raise FloodWaitError(request=request,capture=60)
            raise PhoneMigrateError(request=request,capture=1)
    with pytest.raises(Exception):
        asyncio.run(request_login_code(Client(),'5511999991234',1,'synthetic',{}))
    assert len(calls)==1


def test_concurrent_login_or_recovery_cannot_open_the_same_session(tmp_path):
    with session_lock(tmp_path):
        with pytest.raises(RuntimeError, match="SESSION_IN_USE"):
            with session_lock(tmp_path):
                pytest.fail("The second process must not own this session")
    with session_lock(tmp_path):
        pass


@pytest.mark.parametrize("field,value", [("source_id", "similar-name"), ("telegram_message_id", "0"), ("telegram_message_id", "20-other")])
def test_selection_requires_exact_numeric_source_and_message(tmp_path, field, value):
    row = {"content_id": "one", "source_id": "-100123", "telegram_message_id": "20"}
    row[field] = value
    manifest = tmp_path / "private.jsonl"
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        select_record(manifest, "one")


def test_duplicate_manifest_identity_is_not_selected(tmp_path):
    row = {"content_id": "one", "source_id": "-100123", "telegram_message_id": "20"}
    manifest = tmp_path / "private.jsonl"
    manifest.write_text((json.dumps(row) + "\n") * 2, encoding="utf-8")
    with pytest.raises(ValueError, match="exactly one"):
        select_record(manifest, "one")


class Client:
    def __init__(self, *, message_id=20):
        from telethon.tl.types import PeerChannel
        self.message = SimpleNamespace(id=message_id, peer_id=PeerChannel(123),
            message="Oferta https://shopee.com.br/product/10/20", entities=[],
            video=True, document=SimpleNamespace(id=500, size=6))
        self.downloads = 0
        self.message_requests = []

    async def get_input_entity(self, source_id):
        assert source_id == -1000000000123
        return "exact-source"

    async def get_messages(self, entity, *, ids):
        self.message_requests.append((entity, ids))
        return self.message

    async def download_media(self, message, *, file):
        self.downloads += 1
        file.write(b"media!")


SELECTION = {"content_id": "one", "source_id": "-1000000000123", "telegram_message_id": "20"}


def test_wrong_returned_message_never_downloads(tmp_path):
    client = Client(message_id=21)
    with pytest.raises(RuntimeError, match="IDENTITY_MISMATCH"):
        asyncio.run(recover_one(client, tmp_path, SELECTION, "unused"))
    assert client.downloads == 0


def test_exact_recovery_is_idempotent_and_preserves_original(tmp_path, monkeypatch):
    import scripts.recover_telegram_original as recovery
    from armored_core.integrity import sha256_file
    monkeypatch.setattr(recovery, "verify_media", lambda path, _: {"bytes": path.stat().st_size,
        "sha256": sha256_file(path), "format": "test", "duration": "1", "video_codecs": ["test"]})
    client = Client()
    first = asyncio.run(recover_one(client, tmp_path, SELECTION, "unused"))
    assert first["result"] == "ORIGINAL_RECOVERED_SOURCE_IDENTITY_VERIFIED"
    assert first["historical_hash_available"] is False
    assert first["source_url"] == "https://shopee.com.br/product/10/20"
    original = Path(first["original_path"])
    second = asyncio.run(recover_one(client, tmp_path, SELECTION, "unused"))
    assert second["result"] == "ORIGINAL_ALREADY_VERIFIED"
    assert client.downloads == 1
    assert original.read_bytes() == b"media!"
    original.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="MISMATCH_PRESERVED"):
        asyncio.run(recover_one(client, tmp_path, SELECTION, "unused"))
    assert original.read_bytes() == b"changed"
    assert client.downloads == 1


def test_over_budget_media_is_not_downloaded(tmp_path):
    client = Client()
    result = asyncio.run(recover_one(client, tmp_path, SELECTION, "unused", max_bytes=5))
    assert result["result"] == "MEDIA_SIZE_UNVERIFIED_OR_OVER_BUDGET"
    assert client.downloads == 0


@pytest.mark.parametrize("decode_returncode", [0, 1])
def test_media_verification_requires_full_decode(tmp_path, monkeypatch, decode_returncode):
    import scripts.recover_telegram_original as recovery
    path = tmp_path / "original.media"
    path.write_bytes(b"media")
    commands = []
    monkeypatch.setattr(recovery.shutil, "which", lambda _: "ffmpeg")
    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            return SimpleNamespace(returncode=0, stdout=json.dumps({"streams": [{"codec_type": "video", "codec_name": "h264"}], "format": {}}))
        return SimpleNamespace(returncode=decode_returncode)
    monkeypatch.setattr(recovery.subprocess, "run", run)
    if decode_returncode:
        with pytest.raises(RuntimeError, match="FULL_DECODE_FAILED"):
            verify_media(path, "ffprobe")
    else:
        assert verify_media(path, "ffprobe")["full_decode"] == "passed"
    assert commands[1][-3:] == ["-f", "null", "-"]
