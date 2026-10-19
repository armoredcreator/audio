from __future__ import annotations

"""Shared path-boundary validation for isolated AudioLab workspaces."""

from pathlib import Path


def path_is_within(
    path: str | Path,
    root: str | Path,
    *,
    strict_path: bool = True,
    strict_root: bool = True,
) -> bool:
    """Return whether path resolves beneath root, failing closed on path errors.

    strict_path=False supports validation of an output path that is not created
    yet; the workspace root is strict by default and should already exist.
    """
    try:
        candidate = Path(path).resolve(strict=strict_path)
        base = Path(root).resolve(strict=strict_root)
        candidate.relative_to(base)
        return True
    except (OSError, RuntimeError, ValueError):
        return False
