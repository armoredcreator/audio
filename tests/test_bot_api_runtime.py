from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.ensure_bot_api as bot_api


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    credentials = root / "credentials"
    credentials.mkdir(parents=True)
    (credentials / "project.env").write_text(
        "\n".join(
            (
                "ARMORED_CREATOR_BOT_TOKEN=test-token-hidden",
                "TELEGRAM_API_ID=12345",
                "TELEGRAM_API_HASH=test-hash-hidden",
                "BOT_API_PORT=8081",
                "ARMORED_TELEGRAM_BOT_API_URL=http://127.0.0.1:8081/bot",
                "ARMORED_TELEGRAM_BOT_API_FILE_URL=http://127.0.0.1:8081/file/bot",
            )
        ),
        encoding="utf-8",
    )
    return root


def test_healthy_existing_bot_api_is_reused_without_starting_duplicate(tmp_path):
    root = _root(tmp_path)
    started = []

    owns_process = bot_api.ensure_local_bot_api(
        root,
        port_checker=lambda port: port == 8081,
        get_me_probe=lambda _config: True,
        server_starter=lambda _config: started.append(True),
    )

    assert owns_process is False
    assert started == []


def test_occupied_unauthenticated_port_fails_closed_without_duplicate(tmp_path):
    root = _root(tmp_path)
    started = []

    with pytest.raises(bot_api.BotApiRuntimeError, match="porta 8081 está ocupada"):
        bot_api.ensure_local_bot_api(
            root,
            port_checker=lambda _port: True,
            get_me_probe=lambda _config: False,
            server_starter=lambda _config: started.append(True),
        )

    assert started == []


def test_absent_bot_api_starts_with_environment_credentials_and_records_owner(tmp_path, monkeypatch):
    root = _root(tmp_path)
    sequence = iter((False, True))
    child = SimpleNamespace(pid=321, poll=lambda: None)
    observed = {}
    monkeypatch.setattr(
        bot_api,
        "_process_started_at",
        lambda pid: datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc),
    )

    def start(config):
        observed["config"] = config
        return child

    owns_process = bot_api.ensure_local_bot_api(
        root,
        port_checker=lambda _port: next(sequence),
        get_me_probe=lambda _config: True,
        server_starter=start,
        sleep=lambda _seconds: None,
    )

    assert owns_process is True
    assert observed["config"]["api_id"] == "12345"
    assert observed["config"]["api_hash"] == "test-hash-hidden"
    owner = json.loads(bot_api._owner_path(root).read_text(encoding="utf-8"))
    assert owner["pid"] == 321
    assert owner["port"] == 8081
    assert "test-token-hidden" not in json.dumps(owner)
    assert "test-hash-hidden" not in json.dumps(owner)


def test_nonlocal_bot_api_endpoint_is_rejected(tmp_path):
    root = _root(tmp_path)
    env_path = root / "credentials" / "project.env"
    env_path.write_text(
        env_path.read_text(encoding="utf-8").replace(
            "http://127.0.0.1:8081/bot", "https://api.telegram.org/bot"
        ),
        encoding="utf-8",
    )

    with pytest.raises(bot_api.BotApiRuntimeError, match="endpoints do Bot API"):
        bot_api.ensure_local_bot_api(
            root,
            port_checker=lambda _port: False,
            get_me_probe=lambda _config: False,
            server_starter=lambda _config: pytest.fail("nonlocal endpoint must not start a process"),
        )


def test_stop_does_nothing_for_an_unowned_bot_api(tmp_path):
    assert bot_api.stop_owned_bot_api(_root(tmp_path)) is False
