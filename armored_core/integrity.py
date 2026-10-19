from __future__ import annotations

import hashlib
from pathlib import Path


_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: str | Path) -> str:
    """Calculate a file's SHA-256 digest with bounded memory usage."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()