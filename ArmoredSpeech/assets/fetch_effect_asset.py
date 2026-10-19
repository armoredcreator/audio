from __future__ import annotations

"""Materialize the verified official project sound effect into ArmoredSpeech/assets."""

from pathlib import Path

from armored_core.github_blob_assets import (
    git_blob_sha1 as _shared_git_blob_sha1,
    materialize_verified_asset,
)

EFFECT_SHA = "ca5f879c6631845e80108195af33c172858d31d2"


def git_blob_sha1(data: bytes) -> str:
    """Keep the historical helper name while sharing the implementation."""
    return _shared_git_blob_sha1(data)


def materialize_effect(destination: Path | None = None) -> Path:
    target = Path(destination) if destination else Path(__file__).resolve().parent
    return materialize_verified_asset(
        EFFECT_SHA,
        target / "efeitosonoro.wav",
        user_agent="ArmoredCreator-Speech-Asset-Materializer",
        missing_token_message=(
            "A fonte oficial é privada. Defina ARMORED_OFFICIAL_REPO_TOKEN com "
            "token read-only autorizado para armoredcreator/armoredcreator. "
            "O token é lido do ambiente e nunca é salvo por este script."
        ),
    )


if __name__ == "__main__":
    print(f"[ARMORED-SPEECH-ASSETS] OK {materialize_effect()}")
