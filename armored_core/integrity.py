from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


_CHUNK_SIZE = 1024 * 1024


def sha256_file(path: str | Path) -> str:
    """Calculate a file's SHA-256 digest with bounded memory usage."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json_sha256(value: object) -> str:
    """Hash canonical JSON data (distinct from file SHA-256 and Git blob SHA-1).

    Callers that define an empty-plan fallback should normalize it before calling
    this helper. Paths are encoded with os.fspath and mappings are key-sorted so
    Speech and Studio can validate the same visual-plan digest.
    """
    raw = json.dumps(
        value,
        default=os.fspath,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()
