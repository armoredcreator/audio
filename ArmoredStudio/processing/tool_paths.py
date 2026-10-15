"""Portable resolution of external video tools used by ArmoredStudio V2."""

from __future__ import annotations

import os
from pathlib import Path
import shutil


def resolve_executable(
    name: str,
    *,
    configured: str | Path | None = None,
    env_var: str | None = None,
    project_root: str | Path | None = None,
) -> Path:
    """Resolve an executable without depending on a machine-specific path.

    Resolution order:
    1. Explicit configured path.
    2. Environment variable.
    3. Executable available on PATH.
    """
    project_root = Path(project_root).resolve() if project_root else None

    value = str(configured or "").strip()
    if not value and env_var:
        value = os.getenv(env_var, "").strip()

    if value:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute() and project_root:
            candidate = project_root / candidate
        candidate = candidate.resolve()
        if candidate.is_file():
            return candidate
        source = f"configuração '{value}'" if configured else f"variável {env_var}"
        raise FileNotFoundError(
            f"{name} configurado em {source} não foi encontrado:\n{candidate}"
        )

    discovered = shutil.which(name)
    if discovered:
        return Path(discovered)

    raise FileNotFoundError(
        f"{name} não foi encontrado. Configure {env_var or name.upper()} "
        "ou instale o executável no PATH."
    )
