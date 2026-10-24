from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from armored_core.cli import main
from armored_core.coordinator import Coordinator


def test_windows_cp1252_help_does_not_crash():
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "cp1252"
    result = subprocess.run(
        [sys.executable, "run_coordinator.py", "--help"],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        capture_output=True,
        text=True,
        encoding="cp1252",
        timeout=10,
    )
    assert result.returncode == 0
    assert "CATCH-UP -> LIVE" in result.stdout


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

    def fake_build(root, *, acquire_runtime_lock, allow_real_publication, catch_up_run_id, max_catch_up_items,
                   continuous_operation_id, max_items_per_day, max_items_per_batch):
        calls.append(("build", acquire_runtime_lock, allow_real_publication, catch_up_run_id, max_catch_up_items,
                      continuous_operation_id, max_items_per_day, max_items_per_batch))
        return FakeCoordinator()

    monkeypatch.setattr(Coordinator, "build", staticmethod(fake_build))
    args = ["--root", str(tmp_path), "--run", "--catch-up-run-id", "batch-1", "--max-catch-up-items", "3"]
    assert main(args) == 0
    assert calls == [
        ("build", True, False, "batch-1", 3, None, None, None),
        ("run", 2.0, None),
        ("close",),
    ]

    calls.clear()
    assert main(args + ["--publish"]) == 0
    assert calls[0] == ("build", True, True, "batch-1", 3, None, None, None)

    calls.clear()
    continuous = [
        "--root", str(tmp_path), "--continuous", "--continuous-operation-id", "production",
        "--max-items-per-day", "20", "--max-items-per-batch", "3",
    ]
    assert main(continuous) == 0
    assert calls[0] == ("build", True, False, None, None, "production", 20, 3)


def test_run_requires_explicit_bounded_budget_and_rejects_live_cycles(tmp_path: Path, monkeypatch):
    _three_routes(monkeypatch)
    import pytest

    with pytest.raises(SystemExit) as missing:
        main(["--root", str(tmp_path), "--run"])
    assert missing.value.code == 2
    with pytest.raises(SystemExit) as live:
        main([
            "--root", str(tmp_path), "--run", "--catch-up-run-id", "batch-1",
            "--max-catch-up-items", "3", "--max-live-cycles", "1",
        ])
    assert live.value.code == 2
    with pytest.raises(SystemExit) as continuous_missing:
        main(["--root", str(tmp_path), "--continuous"])
    assert continuous_missing.value.code == 2
    with pytest.raises(SystemExit) as continuous_live_limit:
        main([
            "--root", str(tmp_path), "--continuous", "--continuous-operation-id", "production",
            "--max-items-per-day", "20", "--max-items-per-batch", "3", "--max-live-cycles", "1",
        ])
    assert continuous_live_limit.value.code == 2


def test_validate_config_rejects_duplicate_hub_destination(tmp_path: Path, monkeypatch, capsys):
    _three_routes(monkeypatch)
    monkeypatch.setenv("ARMORED_HUB_3_CHAT_ID", "-200000000001")
    monkeypatch.setenv("ARMORED_HUB_3_TOPIC_ID", "1")
    assert main(["--root", str(tmp_path), "--validate-config"]) == 2
    assert "destino Hub próprio" in capsys.readouterr().err


def test_preflight_requires_evidence_path(tmp_path: Path, monkeypatch):
    _three_routes(monkeypatch)
    import pytest

    with pytest.raises(SystemExit) as error:
        main(["--root", str(tmp_path), "--preflight"])
    assert error.value.code == 2


def test_preflight_reports_safe_clone_result_without_running(tmp_path: Path, monkeypatch, capsys):
    _three_routes(monkeypatch)
    from armored_core.preflight import run_preflight

    calls = []

    def fake_preflight(root, evidence):
        calls.append((root, evidence))
        return {"status": "PASS"}

    monkeypatch.setattr("armored_core.preflight.run_preflight", fake_preflight)
    evidence = tmp_path / "reports" / "preflight.json"
    assert main(["--root", str(tmp_path), "--preflight", "--evidence", str(evidence)]) == 0
    assert calls == [(tmp_path.resolve(), evidence)]
    assert "PREFLIGHT=PASS" in capsys.readouterr().out
