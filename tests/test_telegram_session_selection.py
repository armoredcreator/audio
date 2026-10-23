from __future__ import annotations

import pytest

from armored_core.telegram_session import telegram_session_basename


def test_telegram_session_defaults_to_legacy_name(monkeypatch):
    monkeypatch.delenv("ARMORED_TELEGRAM_SESSION_NAME", raising=False)
    assert telegram_session_basename() == "armoredsync"


def test_telegram_session_accepts_a_sidecar_basename(monkeypatch):
    monkeypatch.setenv("ARMORED_TELEGRAM_SESSION_NAME", "armoredsync-readonly-20261023-v1")
    assert telegram_session_basename() == "armoredsync-readonly-20261023-v1"


def test_sync_and_hub_verifier_share_the_selected_sidecar(tmp_path, monkeypatch):
    monkeypatch.setenv("ARMORED_TELEGRAM_SESSION_NAME", "armoredsync-readonly-test")
    from ArmoredHub.service import ArmoredHub
    from ArmoredSync.service import TelegramReader

    reader = TelegramReader(tmp_path, 12345, "a" * 32)
    try:
        expected = tmp_path / "credentials" / "telegram" / "session" / "armoredsync-readonly-test"
        assert reader._session == expected
        assert ArmoredHub(tmp_path)._telegram_session_path() == expected
    finally:
        reader.client.session.close()


@pytest.mark.parametrize(
    "value",
    ["../armoredsync", r"..\armoredsync", "C:\\outside", "armoredsync.session"],
)
def test_telegram_session_rejects_paths_and_session_file_names(monkeypatch, value):
    monkeypatch.setenv("ARMORED_TELEGRAM_SESSION_NAME", value)
    with pytest.raises(ValueError):
        telegram_session_basename()
