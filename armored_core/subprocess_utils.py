from __future__ import annotations

"""Cross-platform subprocess options used to suppress console windows on Windows."""

import subprocess
import sys
from typing import Any


def windows_hidden_kwargs() -> dict[str, Any]:
    """Return Windows no-console options, or an empty mapping on other platforms."""
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}
