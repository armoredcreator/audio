"""Safe basename selection for the user-authenticated Telethon session."""
from __future__ import annotations

import os
import re


def telegram_session_basename() -> str:
    """Return a configured session basename without allowing path traversal.

    The default preserves existing installations. Operators can point Sync and
    Hub's historical Telethon verifier at a new sidecar session while keeping
    the original ``armoredsync`` file intact.
    """
    name = (os.getenv("ARMORED_TELEGRAM_SESSION_NAME") or "armoredsync").strip()
    if name.endswith(".session"):
        raise ValueError("ARMORED_TELEGRAM_SESSION_NAME must be a basename without .session")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name):
        raise ValueError("ARMORED_TELEGRAM_SESSION_NAME contains invalid path characters")
    return name
