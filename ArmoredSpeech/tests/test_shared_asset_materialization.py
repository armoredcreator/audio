from __future__ import annotations

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from armored_core.github_blob_assets import (
    fetch_verified_git_blob,
    git_blob_sha1,
    materialize_verified_asset,
)


class SharedAssetMaterializationTests(unittest.TestCase):
    def _api_response(self, data: bytes, declared_sha: str | None = None) -> bytes:
        payload = {
            "sha": declared_sha or git_blob_sha1(data),
            "encoding": "base64",
            "content": base64.b64encode(data).decode("ascii"),
        }
        return json.dumps(payload).encode("utf-8")

    def _mock_urlopen(self, response_body: bytes) -> MagicMock:
        response = MagicMock()
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        response.read.return_value = response_body
        return response

    def test_git_blob_sha1_uses_git_object_header(self) -> None:
        self.assertEqual(
            git_blob_sha1(b"hello\n"),
            "ce013625030ba8dba906f756967f9e9ca394464a",
        )

    def test_existing_verified_file_needs_no_token_or_network(self) -> None:
        data = b"already verified"
        expected_sha = git_blob_sha1(data)
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "nested" / "asset.bin"
            destination.parent.mkdir()
            destination.write_bytes(data)
            with patch.dict(os.environ, {"ARMORED_OFFICIAL_REPO_TOKEN": ""}), patch(
                "armored_core.github_blob_assets.urllib.request.urlopen"
            ) as urlopen:
                result = materialize_verified_asset(expected_sha, destination)
            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), data)
            urlopen.assert_not_called()

    def test_missing_token_fails_before_network_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "asset.bin"
            with patch.dict(os.environ, {"ARMORED_OFFICIAL_REPO_TOKEN": ""}), patch(
                "armored_core.github_blob_assets.urllib.request.urlopen"
            ) as urlopen:
                with self.assertRaisesRegex(RuntimeError, "ARMORED_OFFICIAL_REPO_TOKEN"):
                    materialize_verified_asset(
                        "a85ca5d090f1a074a22ef272f8ee7d5f683fbee2",
                        destination,
                    )
            urlopen.assert_not_called()
            self.assertFalse(destination.exists())

    def test_download_is_verified_then_written_atomically(self) -> None:
        data = b"official test asset\x00\x01"
        expected_sha = git_blob_sha1(data)
        response = self._mock_urlopen(self._api_response(data))
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "assets" / "asset.bin"
            with patch.dict(os.environ, {"ARMORED_OFFICIAL_REPO_TOKEN": "test-read-only-token"}), patch(
                "armored_core.github_blob_assets.urllib.request.urlopen",
                return_value=response,
            ) as urlopen:
                result = materialize_verified_asset(
                    expected_sha,
                    destination,
                    user_agent="Unit-Test",
                )
            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), data)
            self.assertEqual(git_blob_sha1(destination.read_bytes()), expected_sha)
            urlopen.assert_called_once()
            request = urlopen.call_args.args[0]
            self.assertTrue(request.full_url.endswith(f"/git/blobs/{expected_sha}"))
            self.assertEqual(request.get_header("Authorization"), "Bearer test-read-only-token")
            self.assertFalse(any(p.name.startswith(".asset.bin.") for p in destination.parent.iterdir()))

    def test_declared_sha_mismatch_does_not_write_destination(self) -> None:
        data = b"payload"
        expected_sha = git_blob_sha1(data)
        response = self._mock_urlopen(self._api_response(data, declared_sha="0" * 40))
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp) / "asset.bin"
            with patch.dict(os.environ, {"ARMORED_OFFICIAL_REPO_TOKEN": "test-token"}), patch(
                "armored_core.github_blob_assets.urllib.request.urlopen",
                return_value=response,
            ):
                with self.assertRaisesRegex(RuntimeError, "does not match the requested SHA"):
                    materialize_verified_asset(expected_sha, destination)
            self.assertFalse(destination.exists())

    def test_bad_expected_sha_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "40-character lowercase"):
            fetch_verified_git_blob("not-a-sha", token="test-token")


if __name__ == "__main__":
    unittest.main()
