from __future__ import annotations

"""Shared, integrity-checked materialization of official GitHub blob assets.

This module distinguishes Git's SHA-1 blob object ID from SHA-256 file
integrity hashes. It never logs credentials and writes downloaded assets
atomically only after both the API response and decoded bytes are verified.
"""

import base64
import binascii
import hashlib
import json
import os
import re
import tempfile
import urllib.request
from pathlib import Path

_GIT_SHA1_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def git_blob_sha1(data: bytes) -> str:
    """Return the Git object ID for a blob's raw bytes (not a plain SHA-1)."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    header = b"blob " + str(len(data)).encode("ascii") + b"\0"
    return hashlib.sha1(header + data).hexdigest()


def fetch_verified_git_blob(
    expected_sha: str,
    *,
    token: str,
    repository: str = "armoredcreator/armoredcreator",
    timeout: float = 30.0,
    user_agent: str = "ArmoredCreator-Lab-Asset-Materializer",
) -> bytes:
    """Fetch a private GitHub blob and validate its declared and calculated IDs."""
    if not isinstance(expected_sha, str) or not _GIT_SHA1_PATTERN.fullmatch(expected_sha):
        raise ValueError("expected_sha must be a 40-character lowercase Git SHA-1")
    if not isinstance(repository, str) or not _REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError("repository must have the form owner/repo")
    if not isinstance(token, str) or not token.strip():
        raise ValueError("A non-empty GitHub token is required")
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/git/blobs/{expected_sha}",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": user_agent,
            "Authorization": f"Bearer {token.strip()}",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        try:
            payload = json.loads(response.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("GitHub returned an invalid blob response") from exc

    if not isinstance(payload, dict):
        raise RuntimeError("GitHub returned an invalid blob response")
    if payload.get("sha") != expected_sha:
        raise RuntimeError("GitHub blob SHA does not match the requested SHA")
    if payload.get("encoding") != "base64":
        raise RuntimeError("GitHub blob response is not base64 encoded")

    encoded = payload.get("content")
    if not isinstance(encoded, str) or not encoded.strip():
        raise RuntimeError("GitHub blob response has no base64 content")
    try:
        data = base64.b64decode(re.sub(r"\s+", "", encoded), validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError("GitHub blob response contains invalid base64") from exc

    actual_sha = git_blob_sha1(data)
    if actual_sha != expected_sha:
        raise RuntimeError(
            f"GitHub blob integrity check failed: expected {expected_sha}, received {actual_sha}"
        )
    return data


def _atomic_write(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        dir=str(destination.parent),
    )
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, destination)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def materialize_verified_asset(
    expected_sha: str,
    destination: str | Path,
    *,
    token_env: str = "ARMORED_OFFICIAL_REPO_TOKEN",
    repository: str = "armoredcreator/armoredcreator",
    timeout: float = 30.0,
    user_agent: str = "ArmoredCreator-Lab-Asset-Materializer",
    missing_token_message: str | None = None,
) -> Path:
    """Materialize one asset iff its Git blob SHA-1 matches the expected ID.

    A matching existing file is accepted without network access or a token.
    A missing or mismatched file is replaced only after a verified download.
    """
    if not isinstance(expected_sha, str) or not _GIT_SHA1_PATTERN.fullmatch(expected_sha):
        raise ValueError("expected_sha must be a 40-character lowercase Git SHA-1")
    path = Path(destination).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.is_file() and git_blob_sha1(path.read_bytes()) == expected_sha:
        return path

    token = os.getenv(token_env, "").strip()
    if not token:
        raise RuntimeError(
            missing_token_message
            or f"Set {token_env} to a read-only token authorized for {repository}; "
            "the token is read from the environment and is never stored by this script."
        )

    data = fetch_verified_git_blob(
        expected_sha,
        token=token,
        repository=repository,
        timeout=timeout,
        user_agent=user_agent,
    )
    _atomic_write(path, data)
    return path
