from __future__ import annotations

"""Run a complete isolated Speech -> Studio test for an explicitly sized MP4 corpus.

The default target is 14 source videos. Inputs are copied byte-for-byte into a
fresh lab-only namespace; the tool creates a new experimental SQLite database,
runs Speech for every copy, and (only with --render) hands the complete READY
batch to the real Studio renderer. It never cleans previous artifacts.
"""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
from typing import Any


class CorpusBatchError(RuntimeError):
    """Corpus is incomplete, ambiguous, or unsafe to run."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve(strict=True).relative_to(Path(root).resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


def _relative(path: Path, root: Path) -> str:
    return Path(path).resolve(strict=True).relative_to(Path(root).resolve(strict=True)).as_posix()


def _read_explicit_inputs(root: Path, manifest_path: Path) -> list[Path]:
    manifest_path = Path(manifest_path).expanduser()
    if not manifest_path.is_absolute():
        manifest_path = root / manifest_path
    manifest_path = manifest_path.resolve(strict=True)
    if not _inside(manifest_path, root):
        raise CorpusBatchError("O manifesto de entradas precisa estar dentro do laboratório")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusBatchError(f"Manifesto de entradas JSON inválido: {exc}") from exc
    if not isinstance(payload, list):
        raise CorpusBatchError("O manifesto de entradas precisa ser um array JSON")
    paths: list[Path] = []
    for position, entry in enumerate(payload, start=1):
        value = entry.get("path") if isinstance(entry, dict) else entry
        if not isinstance(value, str) or not value.strip():
            raise CorpusBatchError(f"Entrada {position}: caminho ausente")
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = root / path
        try:
            path = path.resolve(strict=True)
        except OSError as exc:
            raise CorpusBatchError(f"Entrada {position}: arquivo não encontrado: {value}") from exc
        if not _inside(path, root) or not path.is_file() or path.suffix.lower() != ".mp4":
            raise CorpusBatchError(f"Entrada {position}: precisa ser um MP4 existente dentro do laboratório: {path}")
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise CorpusBatchError("O manifesto de entradas contém caminhos duplicados")
    return sorted(paths, key=lambda path: str(path).casefold())


def _is_generated_or_evidence(path: Path, root: Path, scan_root: Path) -> bool:
    path = path.resolve(strict=True)
    validation_root = (root / "storage" / "validation").resolve()
    try:
        path.relative_to(validation_root)
        inside_validation = True
    except ValueError:
        inside_validation = False
    try:
        scan_root.relative_to(validation_root)
        scanning_validation = True
    except ValueError:
        scanning_validation = False
    if inside_validation and not scanning_validation:
        return True

    # Ignore workspaces this helper created on prior runs and known outputs from
    # the v5 smoke test. The source set must be original inputs, not generated MP4s.
    if re.search(r"videos grupo_fonte_corpus\d+_", "\\".join(path.parts), re.IGNORECASE):
        return True
    name = path.name.casefold()
    if name.endswith("_finaldomeulinknovo.mp4") or re.fullmatch(r"\d+_\.mp4", name):
        return True
    return False


def discover_videos(root: Path, video_root: Path, expected_count: int) -> list[Path]:
    """Discover a precise source set or fail before creating any artifacts."""
    root = Path(root).resolve(strict=True)
    scan_root = Path(video_root).expanduser()
    if not scan_root.is_absolute():
        scan_root = root / scan_root
    try:
        scan_root = scan_root.resolve(strict=True)
        scan_root.relative_to(root)
    except (OSError, ValueError) as exc:
        raise CorpusBatchError("video-root precisa existir dentro da raiz do laboratório") from exc
    if not scan_root.is_dir():
        raise CorpusBatchError(f"video-root não é uma pasta: {scan_root}")
    if expected_count < 1:
        raise CorpusBatchError("expected-count precisa ser maior que zero")

    videos = sorted(
        (
            path.resolve()
            for path in scan_root.rglob("*")
            if path.is_file()
            and path.suffix.lower() == ".mp4"
            and not _is_generated_or_evidence(path, root, scan_root)
        ),
        key=lambda path: str(path).casefold(),
    )
    if len(videos) != expected_count:
        listing = "\n".join(f"  - {path}" for path in videos) or "  (nenhum MP4 elegível)"
        raise CorpusBatchError(
            f"Esperados exatamente {expected_count} vídeos; encontrados {len(videos)} em {scan_root}.\n"
            "Nada foi copiado nem processado. Confira esta lista ou forneça --inputs-json:\n"
            + listing
        )
    return videos


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _rows(database: Path, source_id: str) -> list[dict[str, Any]]:
    uri = f"file:{database.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                """
                SELECT source_id, item_id, video_path, wav_path, audio_class, language,
                       speech_detected, music_detected, confidence, rvc_eligible, status,
                       original_sha256_before, original_sha256_after, error
                FROM armored_speech_results
                WHERE source_id = ?
                ORDER BY item_id
                """,
                (source_id,),
            ).fetchall()
        ]


def _make_batch_manifest(
    *,
    root: Path,
    batch_id: str,
    source_id: str,
    ready_items: list[dict[str, Any]],
    approved: bool,
) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    for row in ready_items:
        item_id = str(row["item_id"])
        workspace = Path(str(row["video_path"])).resolve(strict=True).parent
        original = Path(str(row["video_path"])).resolve(strict=True)
        digest = _sha256(original)
        items.append({
            "source_id": source_id,
            "telegram_message_id": item_id,
            "content_id": f"{source_id}_{item_id}",
            "state": "IA",
            "workspace": _relative(workspace, root),
            "original_path": _relative(original, root),
            "original_sha256": digest,
            "affiliate_name": f"corpus{len(ready_items)}_{batch_id}_{item_id}",
            "affiliate_url": None,
            "original_url": None,
            "affiliate_urls": [],
            "ia_context": {
                "purpose": "isolated corpus E2E validation",
                "batch_id": batch_id,
                "source_filename": row.get("source_filename"),
                "audio_class": row["audio_class"],
            },
        })
    return {
        "schema_version": 1,
        "phase": "ARMORED_SPEECH",
        "phase_status": "COMPLETE",
        "approved": approved,
        "batch_id": f"corpus{len(items)}-{batch_id}",
        "expected_item_count": len(items),
        "items": items,
    }


def run_corpus_batch(
    *,
    root: Path,
    video_root: Path,
    expected_count: int = 14,
    inputs_json: Path | None = None,
    batch_id: str | None = None,
    model: str = "UVR-MDX-NET-Voc_FT.onnx",
    timeout_seconds: int = 7200,
    render: bool = False,
    list_only: bool = False,
) -> dict[str, Any]:
    root = Path(root).resolve(strict=True)
    if list_only:
        if inputs_json is not None or render:
            raise CorpusBatchError("--list-only não pode ser combinado com --inputs-json ou --render")
        videos = discover_videos(root, video_root, expected_count)
    else:
        if inputs_json is None:
            raise CorpusBatchError(
                "Para evitar incluir uma saída de teste como entrada, processe somente uma lista revisada: "
                "execute primeiro --list-only, confira a lista e passe o caminho por --inputs-json."
            )
        videos = _read_explicit_inputs(root, inputs_json)
        if len(videos) != expected_count:
            raise CorpusBatchError(
                f"Manifesto lista {len(videos)} vídeos; esperado exatamente {expected_count}. "
                "Nenhum artefato foi criado."
            )

    batch_id = batch_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    batch_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", batch_id).strip("._-")[:48]
    if not batch_id:
        raise CorpusBatchError("batch-id inválido")

    if list_only:
        inventory_dir = root / "storage" / "validation" / f"corpus{expected_count}_inventory_{batch_id}"
        if inventory_dir.exists():
            raise CorpusBatchError(f"Inventário já existe; nada será sobrescrito: {inventory_dir}")
        inventory_dir.mkdir(parents=True, exist_ok=False)
        inventory = [
            {
                "path": _relative(path, root),
                "filename": path.name,
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
            for path in videos
        ]
        inputs_path = inventory_dir / "inputs.json"
        inputs_path.write_text(
            json.dumps([row["path"] for row in inventory], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        inventory_report = {
            "schema_version": 1,
            "status": "INPUTS_INVENTORIED",
            "expected_count": expected_count,
            "discovered_count": len(inventory),
            "video_root": str(Path(video_root).resolve() if Path(video_root).is_absolute() else (root / video_root).resolve()),
            "inputs_manifest": str(inputs_path),
            "files": inventory,
        }
        _write_json(inventory_dir / "inventory-report.json", inventory_report)
        print(f"CORPUS_DISCOVERED={len(inventory)}/{expected_count}")
        for position, row in enumerate(inventory, start=1):
            print(f"INPUT={position:02d} FILE={row['filename']} SIZE={row['size_bytes']} SHA256={row['sha256']} PATH={row['path']}")
        print(f"INPUTS_JSON={inputs_path}")
        print(f"INVENTORY_REPORT={inventory_dir / 'inventory-report.json'}")
        return inventory_report

    source_id = f"source_corpus{expected_count}_{batch_id}"
    storage_root = root / "storage"
    workspace_root = storage_root / f"Videos GRUPO_FONTE_corpus{expected_count}_{batch_id}"
    database = storage_root / "database" / f"armored_speech_corpus{expected_count}_{batch_id}.sqlite3"
    report_dir = storage_root / "validation" / f"corpus{expected_count}_{batch_id}"
    for target, label in (
        (workspace_root, "workspace"),
        (database, "SQLite"),
        (report_dir, "pasta de relatório"),
    ):
        if target.exists():
            raise CorpusBatchError(f"{label} já existe; nada será sobrescrito: {target}")

    if render:
        effect = root / "ArmoredSpeech" / "assets" / "efeitosonoro.wav"
        if not effect.is_file() or effect.stat().st_size <= 44:
            raise CorpusBatchError(f"Ativo de efeito sonoro ausente/inválido: {effect}")

    # Resolve and hash all source files before making any copy.
    source_hashes = {path: _sha256(path) for path in videos}
    source_inventory = [
        {
            "source_path": str(path),
            "filename": path.name,
            "size_bytes": path.stat().st_size,
            "sha256_before": source_hashes[path],
        }
        for path in videos
    ]

    # Import optional model dependencies only after all path/count checks passed.
    try:
        from .batch import SpeechItem, run_bulk
        from .model_processor import ModelProcessor
    except ImportError as exc:
        raise CorpusBatchError(
            "Dependências do ArmoredSpeech ausentes. Ative o venv do laboratório e "
            "instale ArmoredSpeech/requirements-analysis.txt. "
            f"Detalhe: {exc}"
        ) from exc

    report: dict[str, Any] = {
        "schema_version": 1,
        "batch_id": batch_id,
        "expected_count": expected_count,
        "source_id": source_id,
        "source_inventory": source_inventory,
        "database": str(database),
        "workspace_root": str(workspace_root),
        "render_requested": render,
        "status": "RUNNING",
    }
    report_dir.mkdir(parents=True, exist_ok=False)
    try:
        workspace_root.mkdir(parents=True, exist_ok=False)
        storage = __import__("armored_core.storage", fromlist=["Storage"]).Storage(root)
        speech_items = []
        copy_records: dict[str, dict[str, Any]] = {}
        for position, source_path in enumerate(videos, start=1):
            item_id = f"{position:03d}"
            workspace = workspace_root / item_id
            # Match the canonical Storage workspace used by Studio output paths.
            actual_workspace = storage.workspace(item_id, source_id=source_id)
            if actual_workspace.resolve() != workspace.resolve():
                raise CorpusBatchError(
                    f"Namespace inesperado de Storage: esperado {workspace}, recebido {actual_workspace}"
                )
            original_copy = workspace / f"original_{item_id}.mp4"
            if original_copy.exists():
                raise CorpusBatchError(f"Cópia de teste já existe; bloqueando sobrescrita: {original_copy}")
            shutil.copy2(source_path, original_copy)
            copied_hash = _sha256(original_copy)
            if copied_hash != source_hashes[source_path]:
                raise CorpusBatchError(f"SHA-256 diverge na cópia de teste: {source_path}")
            speech_items.append(SpeechItem(
                source_id=source_id,
                item_id=item_id,
                video_path=original_copy,
                source_order=position,
            ))
            copy_records[item_id] = {
                "source_path": str(source_path),
                "source_filename": source_path.name,
                "source_sha256_before": source_hashes[source_path],
                "copied_path": str(original_copy),
                "copied_sha256": copied_hash,
                "workspace": str(workspace),
            }

        processor = ModelProcessor(
            separation_model=model,
            timeout_seconds=timeout_seconds,
        )
        counts = run_bulk(speech_items, database=database, processor=processor)
        result_rows = _rows(database, source_id)
        for row in result_rows:
            copy_info = copy_records.get(str(row["item_id"]), {})
            row["source_path"] = copy_info.get("source_path")
            row["source_filename"] = copy_info.get("source_filename")
            row["source_sha256_before"] = copy_info.get("source_sha256_before")

        rows_ready = len(result_rows) == expected_count and all(
            row.get("status") == "READY"
            and row.get("original_sha256_before")
            and row.get("original_sha256_before") == row.get("original_sha256_after")
            and row.get("wav_path")
            and Path(str(row["wav_path"])).is_file()
            and Path(str(row["wav_path"])).stat().st_size > 44
            for row in result_rows
        )
        unchanged_sources = all(
            path.is_file() and _sha256(path) == before
            for path, before in source_hashes.items()
        )
        report["speech_counts"] = counts
        report["speech_results"] = result_rows
        report["all_speech_rows_ready"] = rows_ready
        report["source_hashes_unchanged_after_speech"] = unchanged_sources
        _write_json(report_dir / "speech-report.json", report)

        print(f"CORPUS_INPUTS={len(videos)}")
        for row in result_rows:
            print(
                f"ITEM={row['item_id']} FILE={row.get('source_filename')} "
                f"CLASS={row['audio_class']} STATUS={row['status']} "
                f"RVC_ELIGIBLE={bool(row['rvc_eligible'])} LANGUAGE={row.get('language') or 'unknown'}"
            )
        print(f"SPEECH_READY={sum(row.get('status') == 'READY' for row in result_rows)}/{expected_count}")
        print(f"ORIGINAL_SOURCE_HASHES_UNCHANGED={unchanged_sources}")
        print(f"SPEECH_REPORT={report_dir / 'speech-report.json'}")

        if not unchanged_sources:
            raise CorpusBatchError("Pelo menos um arquivo-fonte foi alterado durante Speech; Studio bloqueado")
        if not rows_ready:
            report["status"] = "FAILED_SPEECH_NOT_READY"
            _write_json(report_dir / "speech-report.json", report)
            raise CorpusBatchError(
                f"Speech não preparou os {expected_count} itens. Nenhum render Studio foi iniciado. "
                f"Consulte {report_dir / 'speech-report.json'}"
            )

        manifest = _make_batch_manifest(
            root=root,
            batch_id=batch_id,
            source_id=source_id,
            ready_items=result_rows,
            approved=render,
        )
        manifest_path = report_dir / "studio-manifest.json"
        _write_json(manifest_path, manifest)
        report["manifest_path"] = str(manifest_path)
        report["manifest_approved"] = render

        if not render:
            report["status"] = "SPEECH_READY_STUDIO_NOT_RUN"
            _write_json(report_dir / "speech-report.json", report)
            studio_report_path = report_dir / "studio-report.json"
            print("STUDIO_RENDER=NOT_RUN; o manifesto permanece approved=false.")
            print(f"Após revisar os {expected_count} resultados, mude approved para true no manifesto e execute:")
            print(
                f'  "{sys.executable}" -m ArmoredSpeech.studio_batch_runner '
                f'--root "{root}" --manifest "{manifest_path}" --speech-database "{database}" '
                f'--report "{studio_report_path}"'
            )
            print(f"MANIFEST={manifest_path}")
            return report

        from .studio_batch_runner import run_complete_batch

        studio_report_path = report_dir / "studio-report.json"
        studio_report = run_complete_batch(
            root=root,
            manifest_path=manifest_path,
            speech_database=database,
            report_path=studio_report_path,
            copy_only=False,
        )
        source_changes = [
            str(path) for path, before in source_hashes.items()
            if not path.is_file() or _sha256(path) != before
        ]
        report["studio"] = {
            "status": studio_report.get("status"),
            "mode": studio_report.get("mode"),
            "processed_count": len(studio_report.get("processed", [])),
            "originals_intact": studio_report.get("originals_intact"),
            "report_path": str(studio_report_path),
        }
        report["source_changes_after_studio"] = source_changes
        report["status"] = (
            "SUCCESS"
            if studio_report.get("status") == "SUCCESS"
            and len(studio_report.get("processed", [])) == expected_count
            and studio_report.get("originals_intact") is True
            and not source_changes
            else "FAILED_STUDIO_OR_SOURCE_INTEGRITY"
        )
        _write_json(report_dir / "speech-report.json", report)
        print(f"STUDIO_SPEECH_BATCH={report['studio']['status']}")
        print(f"MODE={studio_report.get('mode')}")
        print(f"ITEMS={report['studio']['processed_count']}/{expected_count}")
        print(f"ORIGINALS_INTACT={studio_report.get('originals_intact')}")
        print(f"ORIGINAL_SOURCE_HASHES_UNCHANGED={not source_changes}")
        print(f"FINAL_REPORT={report_dir / 'speech-report.json'}")
        print(f"STUDIO_REPORT={studio_report_path}")
        if report["status"] != "SUCCESS":
            raise CorpusBatchError("Lote não cumpriu todos os critérios de sucesso")
        return report
    except Exception as exc:
        report["status"] = report.get("status") if report.get("status") not in {"RUNNING", "SPEECH_READY_STUDIO_NOT_RUN"} else "FAILED"
        report["error"] = f"{type(exc).__name__}: {exc}"
        if "source_inventory" in report:
            report["source_hashes_unchanged_at_failure"] = all(
                path.is_file() and _sha256(path) == before
                for path, before in source_hashes.items()
            )
        try:
            _write_json(report_dir / "speech-report.json", report)
        except OSError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Processa um corpus MP4 completo em Speech -> Studio num ambiente isolado."
    )
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", type=Path, default=repo_root, help="raiz do laboratório")
    parser.add_argument("--video-root", type=Path, default=Path("storage"),
                        help="pasta de entrada; o padrão examina storage sem artefatos de validation")
    parser.add_argument("--inputs-json", type=Path,
                        help="array JSON explícito de caminhos MP4 quando a descoberta automática não for inequívoca")
    parser.add_argument("--expected-count", type=int, default=14,
                        help="quantidade exata esperada; o padrão é 14")
    parser.add_argument("--batch-id", help="identificador novo para o lote isolado")
    parser.add_argument("--model", default="UVR-MDX-NET-Voc_FT.onnx",
                        help="modelo de separação vocal para itens fala+música")
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--list-only", action="store_true",
                        help="apenas listar/hashi-ar exatamente os vídeos candidatos e gerar inputs.json; sem análise nem cópias")
    parser.add_argument("--render", action="store_true",
                        help="após Speech READY em todos os itens da lista revisada, executar o render real do Studio")
    args = parser.parse_args(argv)
    try:
        report = run_corpus_batch(
            root=args.root,
            video_root=args.video_root,
            expected_count=args.expected_count,
            inputs_json=args.inputs_json,
            batch_id=args.batch_id,
            model=args.model,
            timeout_seconds=args.timeout,
            render=args.render,
            list_only=args.list_only,
        )
    except Exception as exc:
        print(f"CORPUS_E2E=FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if report.get("status") == "SUCCESS":
        print("CORPUS_E2E=SUCCESS")
        return 0
    if report.get("status") == "INPUTS_INVENTORIED":
        print("CORPUS_E2E=INPUTS_INVENTORIED")
        return 0
    if report.get("status") == "SPEECH_READY_STUDIO_NOT_RUN":
        print("CORPUS_E2E=PREPARED_ONLY")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
