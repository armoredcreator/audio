from __future__ import annotations

"""Materialize the verified official project sound effect into ArmoredSpeech/assets."""

import base64
import hashlib
import json
import os
import tempfile
import urllib.request
from pathlib import Path

EFFECT_SHA = "ca5f879c6631845e80108195af33c172858d31d2"
BLOB_API = "https://api.github.com/repos/armoredcreator/armoredcreator/git/blobs"


def git_blob_sha1(data: bytes) -> str:
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def materialize_effect(destination: Path | None = None) -> Path:
    target = Path(destination) if destination else Path(__file__).resolve().parent
    target.mkdir(parents=True, exist_ok=True)
    final_path = target / "efeitosonoro.wav"
    if final_path.is_file() and git_blob_sha1(final_path.read_bytes()) == EFFECT_SHA:
        return final_path

    token = os.getenv("ARMORED_OFFICIAL_REPO_TOKEN")
    if not token:
        raise RuntimeError(
            "A fonte oficial é privada. Defina ARMORED_OFFICIAL_REPO_TOKEN com "
            "token read-only autorizado para armoredcreator/armoredcreator. "
            "O token é lido do ambiente e nunca é salvo por este script."
        )
    request = urllib.request.Request(
        f"{BLOB_API}/{EFFECT_SHA}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "ArmoredCreator-Speech-Asset-Materializer",
            "Authorization": f"Bearer {token}",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        blob = json.loads(response.read().decode("utf-8"))
    if blob.get("sha") != EFFECT_SHA or blob.get("encoding") != "base64":
        raise RuntimeError("Resposta inesperada ao buscar o efeito sonoro oficial")
    data = base64.b64decode(blob["content"], validate=True)
    actual = git_blob_sha1(data)
    if actual != EFFECT_SHA:
        raise RuntimeError(
            f"Hash do efeito oficial inválido: esperado {EFFECT_SHA}, recebido {actual}"
        )

    fd, temp_name = tempfile.mkstemp(prefix=".efeitosonoro.wav.", dir=target)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, final_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return final_path


if __name__ == "__main__":
    print(f"[ARMORED-SPEECH-ASSETS] OK {materialize_effect()}")
