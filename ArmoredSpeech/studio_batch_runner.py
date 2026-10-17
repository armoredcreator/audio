from __future__ import annotations

"""Explicit lab-only orchestrator for the completed ArmoredSpeech -> Studio batch.

This CLI accepts a reviewed manifest describing the entire approved Speech
batch. It never guesses the batch from the Speech SQLite table and never opens
a Coordinator/production database.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from armored_core.models import Item, State
from ArmoredStudio.service import build as build_studio
from .studio_integration import process_items_with_speech


class BatchManifestError(RuntimeError):
    """The supplied batch manifest is incomplete, inconsistent, or unsafe."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _rooted_path(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise BatchManifestError(f"{label} precisa ser um caminho não vazio")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=True)


def load_complete_batch(
    root: Path,
    manifest_path: Path,
) -> tuple[dict[str, Any], list[Item], dict[tuple[str, str], str]]:
    """Load canonical Items only from an explicitly completed, approved manifest."""
    root = Path(root).resolve(strict=True)
    manifest_path = _rooted_path(root, str(manifest_path), "manifest")
    if not _inside(manifest_path, root) or not manifest_path.is_file():
        raise BatchManifestError("Manifest precisa ser um arquivo dentro do root do laboratório")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BatchManifestError(f"Manifest JSON inválido: {exc}") from exc

    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise BatchManifestError("schema_version do manifest precisa ser 1")
    if manifest.get("phase") != "ARMORED_SPEECH":
        raise BatchManifestError("O manifest não declara a fase ARMORED_SPEECH")
    if manifest.get("phase_status") != "COMPLETE":
        raise BatchManifestError("A fase Speech não está explicitamente marcada COMPLETE")
    if manifest.get("approved") is not True:
        raise BatchManifestError("O lote precisa estar explicitamente aprovado")
    batch_id = manifest.get("batch_id")
    if not isinstance(batch_id, str) or not batch_id.strip():
        raise BatchManifestError("batch_id obrigatório")
    raw_items = manifest.get("items")
    expected_count = manifest.get("expected_item_count")
    if not isinstance(raw_items, list) or not raw_items:
        raise BatchManifestError("O manifest precisa listar ao menos um item")
    if not isinstance(expected_count, int) or isinstance(expected_count, bool):
        raise BatchManifestError("expected_item_count precisa ser inteiro")
    if expected_count != len(raw_items):
        raise BatchManifestError(
            f"Lote incompleto: expected_item_count={expected_count}, itens={len(raw_items)}"
        )

    items: list[Item] = []
    original_hashes: dict[tuple[str, str], str] = {}
    identities: set[tuple[str, str]] = set()

    for position, data in enumerate(raw_items, start=1):
        if not isinstance(data, dict):
            raise BatchManifestError(f"Item {position}: entrada deve ser um objeto JSON")

        source_id = data.get("source_id")
        message_id = data.get("telegram_message_id")
        content_id = data.get("content_id")
        if not all(isinstance(value, str) and value.strip() for value in (source_id, message_id, content_id)):
            raise BatchManifestError(f"Item {position}: source_id, telegram_message_id e content_id são obrigatórios")
        identity = (source_id, message_id)
        if identity in identities:
            raise BatchManifestError(f"Identidade duplicada no manifest: {identity}")
        identities.add(identity)

        if data.get("state") != State.IA.value:
            raise BatchManifestError(
                f"Item {identity}: state precisa ser IA, pois Studio só começa após o lote Speech"
            )

        workspace = _rooted_path(root, data.get("workspace"), f"Item {identity} workspace")
        original = _rooted_path(root, data.get("original_path"), f"Item {identity} original")
        if not workspace.is_dir() or not _inside(workspace, root):
            raise BatchManifestError(f"Item {identity}: workspace deve ser um diretório dentro do root")
        if not original.is_file() or not _inside(original, workspace):
            raise BatchManifestError(f"Item {identity}: original precisa existir dentro do workspace do item")
        if original.stat().st_size <= 0:
            raise BatchManifestError(f"Item {identity}: original vazio")

        expected_hash = data.get("original_sha256")
        if not isinstance(expected_hash, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_hash):
            raise BatchManifestError(f"Item {identity}: original_sha256 precisa conter 64 caracteres hexadecimais")
        actual_hash = _sha256(original)
        if actual_hash.lower() != expected_hash.lower():
            raise BatchManifestError(f"Item {identity}: hash do original diverge do manifest")

        working_path = None
        if data.get("working_path") is not None:
            working_path = _rooted_path(root, data.get("working_path"), f"Item {identity} working_path")
            if not working_path.is_file() or not _inside(working_path, workspace):
                raise BatchManifestError(f"Item {identity}: working_path precisa existir dentro do workspace")

        try:
            state = State(data["state"])
        except (KeyError, ValueError) as exc:
            raise BatchManifestError(f"Item {identity}: state inválido") from exc

        item = Item(
            content_id=content_id,
            telegram_message_id=message_id,
            state=state,
            workspace=workspace,
            original_path=original,
            working_path=working_path,
            result_path=None,
            affiliate_name=data.get("affiliate_name"),
            affiliate_url=data.get("affiliate_url"),
            source_id=source_id,
            original_url=data.get("original_url"),
            topic_id=data.get("topic_id"),
            topic_name=data.get("topic_name"),
            original_sha256=actual_hash,
            affiliate_urls=tuple(data.get("affiliate_urls") or ()),
            ia_context=data.get("ia_context"),
        )
        items.append(item)
        original_hashes[identity] = actual_hash

    return manifest, items, original_hashes


def _write_report(report_path: Path, report: dict[str, Any]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_complete_batch(
    *,
    root: Path,
    manifest_path: Path,
    speech_database: Path,
    report_path: Path | None = None,
    copy_only: bool = False,
) -> dict[str, Any]:
    """Preflight the full manifest and Speech registry, then invoke Studio in order."""
    root = Path(root).resolve(strict=True)
    speech_database = _rooted_path(root, str(speech_database), "Speech database")
    if not speech_database.is_file() or not _inside(speech_database, root):
        raise BatchManifestError("Speech database precisa existir dentro do root do laboratório")

    manifest, items, original_hashes = load_complete_batch(root, manifest_path)
    if copy_only and manifest.get("allow_copy_only") is not True:
        raise BatchManifestError(
            "Modo copy-only bloqueado: o manifest não declara allow_copy_only=true"
        )

    if report_path is None:
        safe_batch = re.sub(r"[^A-Za-z0-9_.-]+", "_", manifest["batch_id"])
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = root / "storage" / "validation" / f"speech-studio-{safe_batch}-{stamp}.json"
    else:
        requested_report = Path(report_path).expanduser()
        if not requested_report.is_absolute():
            requested_report = root / requested_report
        report_path = requested_report.resolve()
        try:
            report_path.parent.relative_to(root)
        except ValueError as exc:
            raise BatchManifestError("O relatório precisa ser salvo dentro do root do laboratório") from exc

    report: dict[str, Any] = {
        "schema_version": 1,
        "batch_id": manifest["batch_id"],
        "phase": "ARMORED_SPEECH_TO_STUDIO",
        "status": "RUNNING",
        "mode": "COPY_ONLY_TEST" if copy_only else "REAL_STUDIO",
        "expected_item_count": manifest["expected_item_count"],
        "processed": [],
        "originals_intact": None,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    previous_copy_mode = os.environ.get("ARMORED_STUDIO_FORCE_COPY")
    os.environ["ARMORED_STUDIO_FORCE_COPY"] = "1" if copy_only else "0"
    try:
        studio = build_studio(root)
        # This function preflights every READY Speech directive before its first
        # Studio call. Missing/blocked items therefore fail the entire batch closed.
        results = process_items_with_speech(
            studio,
            items,
            speech_database=speech_database,
        )

        for item, result in zip(items, results, strict=True):
            identity = (str(item.source_id), str(item.telegram_message_id))
            output = Path(result.result_path).resolve(strict=True)
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError(f"Studio output inválido para {identity}: {output}")
            if not _inside(output, root):
                raise RuntimeError(f"Studio output saiu do root do laboratório: {output}")
            if output == Path(item.original_path).resolve(strict=True):
                raise RuntimeError(f"Studio tentou usar o original como saída: {output}")
            report["processed"].append({
                "source_id": identity[0],
                "item_id": identity[1],
                "original_path": str(item.original_path),
                "output_path": str(output),
                "output_size": output.stat().st_size,
                "original_sha256_before": original_hashes[identity],
            })

        changed = []
        for item in items:
            identity = (str(item.source_id), str(item.telegram_message_id))
            if _sha256(Path(item.original_path)) != original_hashes[identity]:
                changed.append(identity)
        report["originals_intact"] = not changed
        if changed:
            raise RuntimeError(f"Hash de original mudou durante Studio: {changed}")

        report["status"] = "SUCCESS"
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_report(report_path, report)
        report["report_path"] = str(report_path)
        return report
    except Exception as exc:
        changed = []
        for item in items:
            identity = (str(item.source_id), str(item.telegram_message_id))
            try:
                if _sha256(Path(item.original_path)) != original_hashes[identity]:
                    changed.append(identity)
            except OSError:
                changed.append(identity)
        report["status"] = "FAILED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["changed_originals"] = changed
        report["originals_intact"] = not changed
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_report(report_path, report)
        raise
    finally:
        if previous_copy_mode is None:
            os.environ.pop("ARMORED_STUDIO_FORCE_COPY", None)
        else:
            os.environ["ARMORED_STUDIO_FORCE_COPY"] = previous_copy_mode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Executa um lote Speech COMPLETE aprovado no ArmoredStudio isolado."
    )
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", type=Path, default=repo_root, help="raiz do laboratório")
    parser.add_argument("--manifest", type=Path, required=True, help="manifest JSON do lote Speech COMPLETE")
    parser.add_argument("--speech-database", type=Path, required=True, help="SQLite experimental do ArmoredSpeech")
    parser.add_argument("--report", type=Path, help="destino opcional do relatório JSON (dentro do root)")
    parser.add_argument(
        "--copy-only",
        action="store_true",
        help="teste sintético do handoff/contrato; não renderiza vídeo real",
    )
    args = parser.parse_args(argv)
    try:
        report = run_complete_batch(
            root=args.root,
            manifest_path=args.manifest,
            speech_database=args.speech_database,
            report_path=args.report,
            copy_only=args.copy_only,
        )
    except Exception as exc:
        print(f"STUDIO_SPEECH_BATCH=FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    print(f"STUDIO_SPEECH_BATCH={report['status']}")
    print(f"MODE={report['mode']}")
    print(f"ITEMS={len(report['processed'])}")
    print(f"ORIGINALS_INTACT={report['originals_intact']}")
    print(f"REPORT={report['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
