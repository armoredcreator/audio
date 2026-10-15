from __future__ import annotations

"""Materialize the exact official Studio assets into this disposable lab checkout.

No credentials are used. Downloads are accepted only when their Git blob SHA-1
matches the immutable blob IDs from armoredcreator/armoredcreator:main.
"""

import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

ASSETS = {
    "banner.png": "a85ca5d090f1a074a22ef272f8ee7d5f683fbee2",
    "efeitosonoro.wav": "ca5f879c6631845e80108195af33c172858d31d2",
}
RAW_BASE = "https://raw.githubusercontent.com/armoredcreator/armoredcreator/main/ArmoredStudio/assets"


def git_blob_sha1(data: bytes) -> str:
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def materialize_assets(destination: Path | None = None) -> list[Path]:
    target = Path(destination) if destination else Path(__file__).resolve().parent
    target.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, expected_sha in ASSETS.items():
        final_path = target / name
        if final_path.is_file() and git_blob_sha1(final_path.read_bytes()) == expected_sha:
            written.append(final_path)
            continue

        request = urllib.request.Request(
            f"{RAW_BASE}/{name}",
            headers={"User-Agent": "ArmoredCreator-Studio-Lab-Asset-Materializer"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read()
        actual_sha = git_blob_sha1(data)
        if actual_sha != expected_sha:
            raise RuntimeError(
                f"Official asset verification failed for {name}: "
                f"expected {expected_sha}, received {actual_sha}"
            )
        fd, temp_name = tempfile.mkstemp(prefix=f".{name}.", dir=target)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, final_path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        written.append(final_path)
    return written


if __name__ == "__main__":
    for path in materialize_assets():
        print(f"[ARMOREDSTUDIO-ASSETS] OK {path.name}")
