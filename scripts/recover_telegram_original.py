"""Recover one exact historical message with a newly authenticated session.

This tool never opens a pipeline database. Session, message evidence and media
are private local artifacts. It cannot publish, join chats or scan history.
Run interactively: login secrets are requested in the local terminal only.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import getpass
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import traceback

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from armored_core.integrity import sha256_file

MARKER = ".armored-recovery-workspace.json"
SESSION_NAME = "telegram-recovery.session"


@contextmanager
def session_lock(workspace: Path):
    """Hold an OS lock across the client lifetime; never remove a stale lock file."""
    handle = (workspace / ".session.lock").open("a+b")
    handle.seek(0, os.SEEK_END)
    if handle.tell() == 0:
        handle.write(b"1")
        handle.flush()
    handle.seek(0)
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError("SESSION_IN_USE_BY_ANOTHER_PROCESS") from exc
    try:
        yield
    finally:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def normalize_phone(value: str) -> str:
    from telethon import utils
    phone = utils.parse_phone(value)
    if not phone or not phone.isascii() or not phone.isdigit() or not 7 <= len(phone) <= 15:
        raise RuntimeError("INVALID_PHONE_FORMAT_INCLUDE_COUNTRY_CODE")
    return phone


async def request_login_code(client, phone, api_id, api_hash, diagnostics):
    """No generic resend; follow at most one server-rejected DC redirect.

    Telethon already switches its session DC before raising PhoneMigrateError.
    That error rejects the first request; timeout/auth/flood errors never retry.
    """
    from telethon import functions, types
    from telethon.errors import PhoneMigrateError
    before = getattr(client.session, "dc_id", None)
    diagnostics.update(login_dc_before=before, login_request_attempts=1)
    def request():
        return functions.auth.SendCodeRequest(phone, int(api_id), api_hash, types.CodeSettings())
    try:
        return await asyncio.wait_for(client(request()), timeout=30)
    except PhoneMigrateError as exc:
        after = getattr(client.session, "dc_id", None)
        diagnostics["login_dc_after"] = after
        if after == before or after != exc.new_dc:
            raise RuntimeError("LOGIN_DC_REDIRECT_NOT_CONFIRMED") from exc
        diagnostics["login_request_attempts"] = 2
        return await asyncio.wait_for(client(request()), timeout=30)


def prepare_workspace(root: Path) -> Path:
    """Only a fresh directory or this tool's marked directory is accepted."""
    root = Path(root).absolute()
    if any(parent.is_symlink() for parent in (root, *root.parents)):
        raise ValueError("Recovery workspace cannot traverse a symlink")
    marker = root / MARKER
    if root.exists():
        if not marker.is_file():
            raise ValueError("Existing directory is not a dedicated recovery workspace")
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if payload.get("tool") != "armored-exact-message-recovery-v1":
            raise ValueError("Unknown recovery workspace marker")
        if payload.get("session_path") != str(root / SESSION_NAME):
            raise ValueError("Recovery session path does not match its marker")
    else:
        root.mkdir(parents=True)
        marker.write_text(json.dumps({"tool": "armored-exact-message-recovery-v1",
                                     "created_at_utc": utc_now(),
                                     "session_path": str(root / SESSION_NAME)}, indent=2), encoding="utf-8")
    if (root / SESSION_NAME).is_symlink():
        raise ValueError("Recovery session cannot be a symlink")
    return root


def select_record(manifest: Path, content_id: str) -> dict:
    matches = []
    with Path(manifest).open(encoding="utf-8") as source:
        for line in source:
            if line.strip():
                row = json.loads(line)
                if str(row.get("content_id")) == content_id:
                    matches.append(row)
    if len(matches) != 1:
        raise ValueError("Select exactly one record from the private audit manifest")
    row = matches[0]
    source_id = str(row.get("source_id") or "")
    message_id = str(row.get("telegram_message_id") or "")
    if not source_id.lstrip("-").isascii() or not source_id.lstrip("-").isdigit():
        raise ValueError("An exact numeric source identity is required")
    if not message_id.isascii() or not message_id.isdigit() or int(message_id) < 1:
        raise ValueError("An exact positive message identity is required")
    return {"content_id": content_id, "source_id": source_id,
            "telegram_message_id": message_id}


async def resolve_exact_source(client, source_id: int):
    from telethon import utils
    try:
        return await client.get_input_entity(source_id)
    except ValueError:
        # A new session has no access-hash cache. Discover only dialog metadata,
        # not messages; do not join a source or search chats by approximate name.
        async for dialog in client.iter_dialogs(limit=200):
            if utils.get_peer_id(dialog.entity) == source_id:
                return dialog.input_entity
    raise RuntimeError("EXACT_SOURCE_NOT_AVAILABLE_IN_AUTHORIZED_DIALOGS")


def verify_media(path: Path, ffprobe: str) -> dict:
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError("EMPTY_MEDIA")
    result = subprocess.run([ffprobe, "-v", "error", "-show_streams", "-show_format",
                             "-of", "json", str(path)], capture_output=True,
                            timeout=60, check=False, encoding="utf-8")
    if result.returncode:
        raise RuntimeError("MEDIA_PROBE_FAILED")
    data = json.loads(result.stdout)
    if not any(stream.get("codec_type") == "video" for stream in data.get("streams", [])):
        raise RuntimeError("MEDIA_HAS_NO_VIDEO")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("FFMPEG_UNAVAILABLE")
    decode = subprocess.run([ffmpeg, "-v", "error", "-xerror", "-nostdin", "-i", str(path),
                             "-map", "0:v", "-map", "0:a?", "-f", "null", "-"],
                            capture_output=True, timeout=300, check=False)
    if decode.returncode:
        raise RuntimeError("MEDIA_FULL_DECODE_FAILED")
    return {"bytes": path.stat().st_size, "sha256": sha256_file(path),
            "format": data.get("format", {}).get("format_name"),
            "duration": data.get("format", {}).get("duration"),
            "video_codecs": [s.get("codec_name") for s in data["streams"] if s.get("codec_type") == "video"],
            "full_decode": "passed"}


async def recover_one(client, workspace: Path, selection: dict, ffprobe: str,
                      max_bytes: int = 536870912) -> dict:
    from telethon import utils
    from ArmoredSync.service import TelegramSource
    source_id, message_id = int(selection["source_id"]), int(selection["telegram_message_id"])
    entity = await resolve_exact_source(client, source_id)
    message = await client.get_messages(entity, ids=message_id)
    if message is None:
        return {"result": "MESSAGE_NOT_ACCESSIBLE", **selection}
    if message.id != message_id or utils.get_peer_id(message.peer_id) != source_id:
        raise RuntimeError("EXACT_MESSAGE_IDENTITY_MISMATCH")
    evidence = {**selection, "checked_at_utc": utc_now(),
                "source_url": TelegramSource._shopee_url(message),
                "has_video": TelegramSource._is_video_message(message),
                "historical_hash_available": False}
    # Caption evidence is private and deliberately excluded from the Git tree.
    case_dir = workspace / "cases" / hashlib.sha256(f"{source_id}:{message_id}".encode()).hexdigest()[:20]
    case_dir.mkdir(parents=True, exist_ok=True)
    receipt = case_dir / "receipt.json"
    evidence_path = case_dir / ("message-evidence-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")
    evidence_path.write_text(json.dumps({**evidence, "message_text": str(message.message or "")}, ensure_ascii=False, indent=2), encoding="utf-8")
    if not evidence["has_video"]:
        return {**evidence, "result": "EXACT_MESSAGE_HAS_NO_VIDEO"}
    document = getattr(message, "document", None)
    declared_bytes = int(getattr(document, "size", 0) or 0)
    if not declared_bytes or declared_bytes > max_bytes:
        return {**evidence, "result": "MEDIA_SIZE_UNVERIFIED_OR_OVER_BUDGET"}
    document_id = str(document.id)
    original = case_dir / "original.media"
    if original.exists():
        if not receipt.is_file():
            raise RuntimeError("EXISTING_MEDIA_WITHOUT_RECEIPT_PRESERVED")
        prior = json.loads(receipt.read_text(encoding="utf-8"))
        verified = verify_media(original, ffprobe)
        if (prior.get("document_id") != document_id or prior.get("sha256") != verified["sha256"]
                or verified["bytes"] != declared_bytes or any(prior.get(k) != selection[k] for k in selection)):
            raise RuntimeError("EXISTING_MEDIA_IDENTITY_OR_HASH_MISMATCH_PRESERVED")
        return {**prior, "result": "ORIGINAL_ALREADY_VERIFIED", "rechecked_at_utc": utc_now()}
    partial = case_dir / "original.media.part"
    if partial.exists():
        raise RuntimeError("EXISTING_PARTIAL_PRESERVED_REVIEW_REQUIRED")
    # Exclusive creation prevents an accidental overwrite on retry or collision.
    with partial.open("xb") as destination:
        await client.download_media(message, file=destination)
    verified = verify_media(partial, ffprobe)
    if verified["bytes"] != declared_bytes:
        raise RuntimeError("MEDIA_BYTE_COUNT_MISMATCH_PARTIAL_PRESERVED")
    if original.exists():
        raise RuntimeError("DESTINATION_APPEARED_PARTIAL_PRESERVED")
    partial.rename(original)
    result = {**evidence, **verified, "document_id": document_id,
              "original_path": str(original), "result": "ORIGINAL_RECOVERED_SOURCE_IDENTITY_VERIFIED",
              "association_basis": "authorized exact source/message/document; current SHA-256 only"}
    with receipt.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    return result


async def interactive_run(args):
    workspace = prepare_workspace(args.workspace)
    with session_lock(workspace):
        return await _interactive_locked(args, workspace)


async def _interactive_locked(args, workspace):
    from dotenv import dotenv_values
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    values = dotenv_values(args.credentials_env)
    api_id = os.getenv("TELEGRAM_API_ID") or values.get("TELEGRAM_API_ID")
    api_hash = os.getenv("TELEGRAM_API_HASH") or values.get("TELEGRAM_API_HASH")
    if not api_id or not api_hash:
        raise RuntimeError("TELEGRAM_API_CREDENTIALS_UNAVAILABLE")
    selection = select_record(args.manifest, args.content_id)
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("FFPROBE_UNAVAILABLE")
    client = TelegramClient(str(workspace / SESSION_NAME), int(api_id), api_hash,
                            device_model="Armored isolated historical recovery",
                            request_retries=0, connection_retries=1,
                              flood_sleep_threshold=0, receive_updates=False,
                              raise_last_call_error=True)
    status = {"started_at_utc": utc_now(), "selection": selection,
              "operational_database_opened": False, "publishing_enabled": False,
              "session_origin": "new dedicated session; never copied"}
    try:
        status["stage"] = "CONNECTING_NEW_RECOVERY_SESSION"
        await asyncio.wait_for(client.connect(), timeout=30)
        status["stage"] = "CHECKING_NEW_SESSION_AUTHORIZATION"
        if not await client.is_user_authorized():
            if not args.login or not sys.stdin.isatty():
                raise RuntimeError("NEW_SESSION_LOGIN_REQUIRED_LOCAL_TERMINAL")
            print("Sessao NOVA de recuperacao. Nada sera publicado e nenhuma coleta em massa sera iniciada.")
            status["stage"] = "LOCAL_PHONE_INPUT"
            phone = normalize_phone(getpass.getpass("Telefone com codigo do pais (entrada oculta): ").strip())
            status["stage"] = "REQUESTING_ONE_LOGIN_CODE"
            # Keep the actual RPC error instead of Telethon's generic ValueError.
            # Only an explicitly rejected phone-DC redirect can make one further
            # request; ambiguous failure, quota or authentication never resends.
            from telethon import types
            sent = await request_login_code(client, phone, api_id, api_hash, status)
            if not isinstance(sent, types.auth.SentCode):
                raise RuntimeError("LOGIN_RESPONSE_REQUIRES_EXPLICIT_REVIEW")
            status["stage"] = "LOCAL_CODE_INPUT"
            code = getpass.getpass("Codigo recebido no Telegram (entrada oculta): ").strip()
            try:
                status["stage"] = "SIGNING_IN_NEW_RECOVERY_SESSION"
                await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
            except SessionPasswordNeededError:
                status["stage"] = "LOCAL_TWO_FACTOR_INPUT"
                password = getpass.getpass("Senha de verificacao em duas etapas (entrada oculta): ")
                await client.sign_in(password=password)
            del phone, code, sent
        if not await client.is_user_authorized():
            raise RuntimeError("NEW_SESSION_NOT_AUTHORIZED")
        status["stage"] = "EXACT_MESSAGE_RECOVERY"
        status.update(await recover_one(client, workspace, selection, ffprobe))
    except Exception as exc:
        # Exceptions can contain phone numbers, URLs or request arguments.
        # Store only the class and our own explicit diagnostic codes.
        status.update(result="BLOCKED", error_type=type(exc).__name__)
        status["error_stack"] = [{"file": Path(frame.filename).name, "line": frame.lineno,
                                  "function": frame.name}
                                 for frame in traceback.extract_tb(exc.__traceback__)]
        if type(exc) is RuntimeError and str(exc).isupper() and " " not in str(exc):
            status["reason"] = str(exc)
        if getattr(exc, "seconds", None) is not None:
            status["server_wait_seconds"] = exc.seconds
    finally:
        await client.disconnect()
        client.session.close()
        status["finished_at_utc"] = utc_now()
        path = workspace / ("run-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + ".json")
        with path.open("x", encoding="utf-8") as output:
            json.dump(status, output, ensure_ascii=False, indent=2)
        print("Resultado:", status.get("result"), "| Etapa:", status.get("stage"),
              "| Tipo:", status.get("error_type", "nenhum"), "| Relatorio local:", path)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--credentials-env", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--content-id", required=True)
    parser.add_argument("--login", action="store_true")
    args = parser.parse_args()
    result = asyncio.run(interactive_run(args))
    return 0 if result.get("result") in {"ORIGINAL_RECOVERED_SOURCE_IDENTITY_VERIFIED", "ORIGINAL_ALREADY_VERIFIED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
