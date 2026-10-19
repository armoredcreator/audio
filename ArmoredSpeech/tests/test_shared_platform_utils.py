from __future__ import annotations

from pathlib import Path
import subprocess

import armored_core.subprocess_utils as process_utils
from armored_core.path_utils import path_is_within
from armored_core.subprocess_utils import windows_hidden_kwargs


def test_path_is_within_requires_existing_candidate_by_default(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    existing = root / "input.mp4"
    existing.write_bytes(b"source")
    missing_output = root / "work" / "stem.wav"
    outside = tmp_path / "outside.wav"

    assert path_is_within(existing, root)
    assert not path_is_within(missing_output, root)
    assert path_is_within(missing_output, root, strict_path=False)
    assert not path_is_within(outside, root)


def test_path_is_within_fails_closed_if_root_does_not_exist(tmp_path: Path) -> None:
    assert not path_is_within(tmp_path / "input.mp4", tmp_path / "missing-root")


def test_subprocess_helper_returns_empty_options_off_windows(monkeypatch) -> None:
    monkeypatch.setattr(process_utils.sys, "platform", "linux")
    assert windows_hidden_kwargs() == {}


def test_subprocess_helper_hides_windows_console(monkeypatch) -> None:
    class FakeStartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 2
            self.wShowWindow = -1

    monkeypatch.setattr(process_utils.sys, "platform", "win32")
    monkeypatch.setattr(process_utils.subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)
    monkeypatch.setattr(process_utils.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(process_utils.subprocess, "SW_HIDE", 7, raising=False)
    monkeypatch.setattr(process_utils.subprocess, "CREATE_NO_WINDOW", 99, raising=False)

    result = windows_hidden_kwargs()

    assert result["startupinfo"].dwFlags == 3
    assert result["startupinfo"].wShowWindow == 7
    assert result["creationflags"] == 99
