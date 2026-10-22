from __future__ import annotations
import os
import json
from pathlib import Path
from urllib.parse import unquote, urlparse
from uuid import uuid4
from .integrity import sha256_file

def project_root() -> Path:
    configured = os.getenv("ARMORED_ROOT")
    return Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[1]

def affiliate_tail(url: str | None, fallback: str | None = None) -> str:
    value = ""
    if url:
        parsed = urlparse(str(url).strip())
        value = unquote(parsed.path.rstrip("/").split("/")[-1])
    if not value and fallback:
        value = str(fallback).strip()
    safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in value).strip("._-")
    return safe or "unknown"

class Storage:
    """Canonical filesystem projection.

    Telegram message ID is the operational/content identifier. The SQLite
    item_id remains an internal relational key only.
    """

    def __init__(self, root: Path | None = None, *, storage_root: Path | None = None) -> None:
        # root remains the project/assets/configuration root. storage_root is an
        # optional contained destination for generated media and per-run artifacts.
        self.root = (root or project_root()).resolve()
        self.storage_root = (storage_root or self.root).resolve()
        try:
            self.storage_root.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("storage_root must stay inside project root") from exc
        self.storage = self.storage_root / "storage"
        self.database = self.storage / "database"
        self.videos = self.storage / "videos"
        self.logs = self.storage / "logs"
        self.backups = self.storage / "backups"
        for path in (self.database, self.videos, self.logs, self.backups):
            path.mkdir(parents=True, exist_ok=True)

    def preserve_artifacts(self, paths: list[Path], *, workspace: Path,
                           content_id: str | None = None, reason: str) -> None:
        """Archive caller-selected entries without traversing links or deleting bytes.

        Recovery/Sync select policy and entries; Storage owns the common,
        root-confined filesystem operation and its interruption receipt.
        """
        if not paths:
            return
        root = self.root.resolve(strict=True)
        workspace = workspace.resolve(strict=True)
        backup_root = self.backups.resolve(strict=True)
        workspace.relative_to(root)
        backup_root.relative_to(root)
        if backup_root.is_relative_to(workspace):
            raise RuntimeError("recovery-backup-directory-overlaps-workspace")
        for path in paths:
            if path.absolute().parent != workspace:
                raise RuntimeError("recovery-artifact-outside-workspace")
            if not path.is_symlink():
                path.resolve(strict=True).relative_to(workspace)
        archive = backup_root / ("recovery-" + uuid4().hex)
        archive.resolve().relative_to(backup_root)
        archive.mkdir(exist_ok=False)
        receipt = {"content_id": content_id, "reason": reason, "status": "IN_PROGRESS",
                   "workspace": str(workspace), "planned_entries": [path.name for path in paths], "entries": []}
        receipt_path = archive / "preservation.json"
        with receipt_path.open("x", encoding="utf-8") as handle:
            json.dump(receipt, handle, ensure_ascii=False, indent=2)
        for path in paths:
            destination = archive / path.name
            if destination.exists() or destination.is_symlink():
                raise FileExistsError(destination)
            digest = sha256_file(path) if path.is_file() and not path.is_symlink() else None
            path.rename(destination)
            if digest is not None and sha256_file(destination) != digest:
                raise RuntimeError("preserved-artifact-hash-mismatch")
            receipt["entries"].append({"name": path.name, "status": "PRESERVED", "sha256": digest})
            receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")
        receipt["status"] = "PRESERVED"
        receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")

    def source_workspace_root(self, source_id: str | int | None) -> Path:
        """Return the physical root assigned to one configured Telegram source."""
        value = str(source_id or "telegram").strip() or "telegram"
        index = None
        for candidate in range(1, 100):
            configured = (os.getenv(f"ARMORED_SOURCE_{candidate}_ID") or "").strip()
            configured_chat = (os.getenv(f"ARMORED_SOURCE_{candidate}_CHAT_ID") or "").strip()
            configured_key = (os.getenv(f"ARMORED_SOURCE_{candidate}_KEY") or "").strip()
            if value in {configured, configured_chat, configured_key}:
                index = candidate
                break
            if candidate > 1 and not configured and not configured_chat and not configured_key:
                break
        if index is None:
            # Preserve numbered production source roots (source1, source2, ...)
            # while allowing isolated lab namespaces such as source_teste to map
            # naturally to storage/Videos GRUPO_FONTE_teste.
            match = __import__("re").fullmatch(
                r"source([A-Za-z0-9_-]+)", value, __import__("re").IGNORECASE
            )
            # A separator after the word "source" belongs to the namespace,
            # not to the physical folder suffix: source_teste -> ..._teste.
            suffix = match.group(1).lstrip("_-") if match else ""
            index = suffix or "1"
        return self.storage / f"Videos GRUPO_FONTE_{index}"

    def _source_for_content_id(
        self,
        content_id: str | int,
        source_id: str | int | None = None,
    ) -> str | None:
        if source_id is not None and str(source_id).strip():
            return str(source_id).strip()
        value = str(content_id).strip()
        for index in range(1, 100):
            configured_values = (
                os.getenv(f"ARMORED_SOURCE_{index}_ID"),
                os.getenv(f"ARMORED_SOURCE_{index}_CHAT_ID"),
                os.getenv(f"ARMORED_SOURCE_{index}_KEY"),
            )
            for configured in (str(item or "").strip() for item in configured_values):
                if configured and value.startswith(f"{configured}_"):
                    return configured
        return None

    def _telegram_message_id(
        self,
        content_id: str | int,
        source_id: str | int | None = None,
    ) -> str:
        """Strip the source namespace from the internal SQLite key for disk names."""
        value = str(content_id).strip()
        source = self._source_for_content_id(value, source_id)
        prefix = f"{source}_"
        if source and value.startswith(prefix):
            value = value[len(prefix):]
        if not value:
            raise ValueError("telegram-message-id-required")
        return value

    def workspace(self, telegram_message_id: str | int, source_id: str | int | None = None) -> Path:
        """Return the canonical source-separated workspace keyed by Telegram message ID."""
        source = self._source_for_content_id(telegram_message_id, source_id)
        message_id = self._telegram_message_id(telegram_message_id, source)
        path = self.source_workspace_root(source) / message_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def original(
        self,
        content_id: str,
        suffix: str = ".mp4",
        original_url: str | None = None,
        source_id: str | None = None,
    ) -> Path:
        source = self._source_for_content_id(content_id, source_id)
        message_id = self._telegram_message_id(content_id, source)
        tail = affiliate_tail(original_url)
        return self.workspace(content_id, source_id=source) / f"{message_id}_{tail}{suffix}"

    def working(self, content_id: str, source_id: str | None = None) -> Path:
        source = self._source_for_content_id(content_id, source_id)
        message_id = self._telegram_message_id(content_id, source)
        return self.workspace(content_id, source_id=source) / f"{message_id}_.mp4"

    def result(
        self,
        content_id: str,
        affiliate_url: str | None = None,
        affiliate_name: str | None = None,
        source_id: str | None = None,
    ) -> Path:
        source = self._source_for_content_id(content_id, source_id)
        message_id = self._telegram_message_id(content_id, source)
        tail = affiliate_tail(affiliate_url, affiliate_name)
        return self.workspace(content_id, source_id=source) / f"{message_id}_{tail}.mp4"
