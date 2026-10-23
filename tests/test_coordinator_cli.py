from __future__ import annotations

from pathlib import Path

from armored_core.cli import main
from armored_core.coordinator import Coordinator


def _three_routes(monkeypatch):
    monkeypatch.setenv("ARMORED_REAL_TELEGRAM", "1")
    monkeypatch.setenv("TELEGRAM_API_ID", "12345")
    monkeypatch.setenv("TELEGRAM_API_HASH", "secret-test-value")
    monkeypatch.setenv("ARMORED_REQUIRED_SOURCE_COUNT", "3")
    for index in range(1, 4):
        monkeypatch.setenv(f"ARMORED_SOURCE_{index}_CHAT_ID", f"-10000000000{index}")
        monkeypatch.setenv(f"ARMORED_SOURCE_{index}_ID", f"source-{index}")
        monkeypatch.setenv(f"ARMORED_HUB_{index}_CHAT_ID", f"-20000000000{index}")
        monkeypatch.setenv(f"ARMORED_HUB_{index}_TOPIC_ID", str(index))


def test_validate_config_uses_three_routes_without_opening_database_or_telegram(tmp_path: Path, monkeypatch, capsys):
    _three_routes(monkeypatch)
    assert main(["--root", str(tmp_path), "--validate-config"]) == 0
    output = capsys.readouterr().out
    assert "CONFIGURATION=VALID" in output
    assert "SOURCE_COUNT=3" in output
    assert "secret-test-value" not in output
    assert not (tmp_path / "storage").exists()


def test_run_defaults_to_closed_hub_and_requires_explicit_publish_flag(tmp_path: Path, monkeypatch):
    _three_routes(monkeypatch)
    calls = []

    class FakeCoordinator:
        def run_forever(self, *, poll_seconds, max_cycles):
            calls.append(("run", poll_seconds, max_cycles))

        def close(self):
            calls.append(("close",))

    def fake_build(root, *, acquire_runtime_lock, allow_real_publication):
        calls.append(("build", acquire_runtime_lock, allow_real_publication))
        return FakeCoordinator()

    monkeypatch.setattr(Coordinator, "build", staticmethod(fake_build))
    assert main(["--root", str(tmp_path), "--run", "--max-live-cycles", "2"]) == 0
    assert calls == [("build", True, False), ("run", 2.0, 2), ("close",)]

    calls.clear()
    assert main(["--root", str(tmp_path), "--run", "--publish"]) == 0
    assert calls[0] == ("build", True, True)


def test_validate_config_rejects_duplicate_hub_destination(tmp_path: Path, monkeypatch, capsys):
    _three_routes(monkeypatch)
    monkeypatch.setenv("ARMORED_HUB_3_CHAT_ID", "-200000000001")
    monkeypatch.setenv("ARMORED_HUB_3_TOPIC_ID", "1")
    assert main(["--root", str(tmp_path), "--validate-config"]) == 2
    assert "destino Hub próprio" in capsys.readouterr().err
