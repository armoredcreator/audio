from pathlib import Path


def test_start_all_treats_bounded_and_continuous_modes_as_operational_runs():
    launcher = Path(__file__).resolve().parents[1] / "START_ALL.bat"
    text = launcher.read_text(encoding="utf-8")

    assert '"%%~A"=="--run" set "RUN_REQUESTED=1"' in text
    assert '"%%~A"=="--continuous" set "RUN_REQUESTED=1"' in text

