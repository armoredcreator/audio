from __future__ import annotations

"""Materialize the exact official visual Studio banner into this disposable lab checkout."""

from pathlib import Path

from armored_core.github_blob_assets import (
    git_blob_sha1 as _shared_git_blob_sha1,
    materialize_verified_asset,
)

ASSETS = {
    "banner.png": "a85ca5d090f1a074a22ef272f8ee7d5f683fbee2",
}


def git_blob_sha1(data: bytes) -> str:
    """Keep the historical helper name while sharing the implementation."""
    return _shared_git_blob_sha1(data)


def materialize_assets(destination: Path | None = None) -> list[Path]:
    target = Path(destination) if destination else Path(__file__).resolve().parent
    return [
        materialize_verified_asset(
            expected_sha,
            target / name,
            user_agent="ArmoredCreator-Studio-Lab-Asset-Materializer",
            missing_token_message=(
                "The official source repository is private. Set "
                "ARMORED_OFFICIAL_REPO_TOKEN to a read-only token authorized for "
                "armoredcreator/armoredcreator; the token is read from the environment "
                "and is never stored by this script."
            ),
        )
        for name, expected_sha in ASSETS.items()
    ]


if __name__ == "__main__":
    for path in materialize_assets():
        print(f"[ARMOREDSTUDIO-ASSETS] OK {path.name}")
