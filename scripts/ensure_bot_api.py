from __future__ import annotations

"""Safe local Telegram Bot API lifecycle checks for START_ALL.bat.

Credentials are read from credentials/project.env and passed to the Bot API
server through its environment. They are never printed or put in arguments.
Only a process started and recorded by this launcher may be stopped later.
"""

import argparse
import asyncio
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Callable
from urllib.parse import urlparse

from dotenv import dotenv_values


class BotApiRuntimeError(RuntimeError):
    """A safe, user-facing Bot API startup error without credential values."""


def _configuration(root: Path) -> dict[str, str]:
    env_path = root / "credentials" / "project.env"
    if not env_path.is_file():
        raise BotApiRuntimeError("credentials/project.env não encontrado")
    values = {
        str(key): str(value).strip()
        for key, value in dotenv_values(env_path).items()
        if key is not None and value is not None
    }
    token = values.get("ARMORED_CREATOR_BOT_TOKEN", "")
    api_id = values.get("TELEGRAM_API_ID", "")
    api_hash = values.get("TELEGRAM_API_HASH", "")
    if not token or not api_id or not api_hash:
        raise BotApiRuntimeError("token do bot ou credenciais do servidor ausentes")
    try:
        port = int(values.get("BOT_API_PORT", "8081"))
    except ValueError as exc:
        raise BotApiRuntimeError("BOT_API_PORT inválida") from exc
    if port != 8081:
        raise BotApiRuntimeError("Bot API operacional deve usar a porta 8081")

    base_url = values.get(
        "ARMORED_TELEGRAM_BOT_API_URL", f"http://127.0.0.1:{port}/bot"
    )
    file_url = values.get(
        "ARMORED_TELEGRAM_BOT_API_FILE_URL", f"http://127.0.0.1:{port}/file/bot"
    )
    for endpoint in (base_url, file_url):
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost"}
            or parsed.port != port
        ):
            raise BotApiRuntimeError("endpoints do Bot API devem permanecer locais na porta 8081")

    executable_raw = values.get("BOT_API_EXE", "")
    executable = Path(executable_raw).expanduser() if executable_raw else Path()
    if executable_raw and not executable.is_absolute():
        executable = root / executable
    workdir_raw = values.get("BOT_API_WORKDIR", "")
    workdir = Path(workdir_raw).expanduser() if workdir_raw else executable.parent
    if workdir_raw and not workdir.is_absolute():
        workdir = root / workdir
    return {
        "token": token,
        "api_id": api_id,
        "api_hash": api_hash,
        "port": str(port),
        "base_url": base_url,
        "file_url": file_url,
        "executable": str(executable.resolve()) if executable_raw else "",
        "workdir": str(workdir.resolve()) if workdir_raw or executable_raw else str(root),
    }


def _port_is_listening(port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def _probe_get_me(config: dict[str, str]) -> bool:
    """Authenticate to the configured local API using a read-only getMe call."""
    try:
        from telegram import Bot
        from telegram.request import HTTPXRequest

        async def probe() -> bool:
            bot = Bot(
                token=config["token"],
                request=HTTPXRequest(connect_timeout=3, read_timeout=5),
                base_url=config["base_url"],
                base_file_url=config["file_url"],
            )
            try:
                me = await bot.get_me()
                return bool(getattr(me, "id", None))
            finally:
                await bot.shutdown()

        return bool(asyncio.run(probe()))
    except Exception:
        # HTTP client exceptions can include request URLs, which contain the
        # bot token. Convert all details to a fixed result before reporting.
        return False


def _start_server(config: dict[str, str]) -> subprocess.Popen:
    executable = Path(config["executable"])
    workdir = Path(config["workdir"])
    if not executable.is_file():
        raise BotApiRuntimeError("Telegram Bot API executável não encontrado")
    if not workdir.is_dir():
        raise BotApiRuntimeError("diretório de trabalho do Bot API não encontrado")

    child_env = os.environ.copy()
    child_env["TELEGRAM_API_ID"] = config["api_id"]
    child_env["TELEGRAM_API_HASH"] = config["api_hash"]
    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
    try:
        return subprocess.Popen(
            [str(executable), "--local", f"--http-port={config['port']}"],
            cwd=workdir,
            env=child_env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creation_flags,
        )
    except OSError as exc:
        raise BotApiRuntimeError("não foi possível iniciar o Telegram Bot API local") from exc


def _process_started_at(pid: int) -> datetime | None:
    try:
        from armored_core.database import _process_started_at_utc

        return _process_started_at_utc(int(pid))
    except Exception:
        return None


def _process_image_path(pid: int) -> str | None:
    if os.name != "nt":
        return None
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = (
        wintypes.HANDLE,
        wintypes.DWORD,
        wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    )
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if not handle:
        return None
    try:
        length = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(length.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, buffer, ctypes.byref(length)
        ):
            return None
        return str(Path(buffer.value).resolve())
    finally:
        kernel32.CloseHandle(handle)


def _owner_path(root: Path) -> Path:
    return root / "storage" / "logs" / "bot-api-launcher-owner.json"


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def ensure_local_bot_api(
    root: Path,
    *,
    port_checker: Callable[[int], bool] = _port_is_listening,
    get_me_probe: Callable[[dict[str, str]], bool] = _probe_get_me,
    server_starter: Callable[[dict[str, str]], subprocess.Popen] = _start_server,
    sleep: Callable[[float], None] = time.sleep,
    timeout_seconds: float = 30.0,
) -> bool:
    """Return True only when this call started the now-authenticated service."""
    root = Path(root).expanduser().resolve()
    config = _configuration(root)
    port = int(config["port"])

    # A listening port is never treated as proof of a healthy or authenticated
    # Bot API. Do not launch a second server when a different process owns it.
    if port_checker(port):
        if not get_me_probe(config):
            raise BotApiRuntimeError(
                "porta 8081 está ocupada, mas o Bot API local não autenticou o bot; START interrompido"
            )
        return False

    process = server_starter(config)
    deadline = time.monotonic() + max(0.1, float(timeout_seconds))
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise BotApiRuntimeError(
                    "Telegram Bot API terminou antes de ficar disponível"
                )
            if port_checker(port) and get_me_probe(config):
                started_at = _process_started_at(process.pid)
                if started_at is None:
                    raise BotApiRuntimeError(
                        "não foi possível comprovar a propriedade do processo Bot API iniciado"
                    )
                owner = {
                    "pid": int(process.pid),
                    "started_at_utc": started_at.isoformat(),
                    "executable": config["executable"],
                    "port": port,
                }
                owner_path = _owner_path(root)
                owner_path.parent.mkdir(parents=True, exist_ok=True)
                temporary = owner_path.with_suffix(owner_path.suffix + ".tmp")
                temporary.write_text(
                    json.dumps(owner, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                os.replace(temporary, owner_path)
                return True
            sleep(0.25)
        raise BotApiRuntimeError(
            "Bot API local não autenticou em 30 segundos; START interrompido"
        )
    except Exception:
        _stop_process(process)
        raise


def stop_owned_bot_api(root: Path) -> bool:
    """Stop only a still-matching Bot API process started by this launcher."""
    root = Path(root).expanduser().resolve()
    owner_path = _owner_path(root)
    if not owner_path.is_file():
        return False
    try:
        owner = json.loads(owner_path.read_text(encoding="utf-8"))
        pid = int(owner["pid"])
        expected_path = str(Path(owner["executable"]).resolve())
        expected_start = datetime.fromisoformat(str(owner["started_at_utc"]))
    except Exception as exc:
        raise BotApiRuntimeError("registro de propriedade do Bot API inválido; processo preservado") from exc

    actual_start = _process_started_at(pid)
    actual_path = _process_image_path(pid)
    if actual_start is None or actual_path is None:
        raise BotApiRuntimeError("propriedade do processo Bot API não comprovada; processo preservado")
    start_matches = abs((actual_start - expected_start).total_seconds()) <= 2.0
    if not start_matches or str(Path(actual_path).resolve()).casefold() != expected_path.casefold():
        raise BotApiRuntimeError("PID do registro foi reutilizado; processo preservado")

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        raise BotApiRuntimeError("não foi possível encerrar o Bot API iniciado pelo launcher") from exc
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if _process_started_at(pid) != actual_start:
            owner_path.unlink(missing_ok=True)
            return True
        time.sleep(0.2)
    raise BotApiRuntimeError("Bot API iniciado pelo launcher não encerrou no prazo")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ensure_bot_api")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--ensure", action="store_true")
    action.add_argument("--stop-owned", action="store_true")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        if args.ensure:
            started = ensure_local_bot_api(args.root)
            print("BOT_API_READY=1")
            print(f"BOT_API_OWNED={1 if started else 0}")
        else:
            stopped = stop_owned_bot_api(args.root)
            print(f"BOT_API_STOPPED={1 if stopped else 0}")
        return 0
    except BotApiRuntimeError as exc:
        print(f"BOT_API_STARTUP=FAILED: {exc}", file=sys.stderr)
        return 2
    except Exception:
        print("BOT_API_STARTUP=FAILED: erro interno sem detalhes sensíveis", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
