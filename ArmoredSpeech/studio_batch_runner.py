from __future__ import annotations

"""Explicit lab-only orchestrator for the completed ArmoredSpeech -> Studio batch.

This CLI accepts a reviewed manifest describing the entire approved Speech
batch. It never guesses the batch from the Speech SQLite table and never opens
a Coordinator/production database.
"""

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any

from armored_core.models import Item, State
from armored_core.storage import Storage
from ArmoredStudio.service import build as build_studio
from ArmoredStudio.speech_handoff import validate_audio_package, SpeechHandoffError
from ArmoredStudio.analysis.export_plan_validator import validar_plano_exportacao
from .timeline_mixer import visual_plan_digest
from .studio_integration import prepare_audio_packages_for_items


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
        "audio_contract_version": 0 if copy_only else 3,
        "expected_item_count": manifest["expected_item_count"],
        "audio_packages": [],
        "processed": [],
        "originals_intact": None,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
    }

    previous_copy_mode = os.environ.get("ARMORED_STUDIO_FORCE_COPY")
    previous_allow_copy = os.environ.get("ARMORED_STUDIO_ALLOW_COPY")
    os.environ["ARMORED_STUDIO_FORCE_COPY"] = "1" if copy_only else "0"
    os.environ["ARMORED_STUDIO_ALLOW_COPY"] = "1" if copy_only else "0"
    try:
        # Refuse to overwrite any existing result. A retry must use an isolated
        # destination or an explicitly reviewed cleanup; a prior valid output
        # is never deleted as a side effect of starting this batch.
        target_storage = Storage(root)
        for item in items:
            destination = Path(
                target_storage.result(
                    item.content_id,
                    item.affiliate_url,
                    item.affiliate_name,
                    source_id=item.source_id,
                )
            ).resolve()
            if not _inside(destination.parent, root):
                raise RuntimeError(f"Destino Studio fora do root do laboratório: {destination}")
            if destination == Path(item.original_path).resolve():
                raise RuntimeError(f"Destino Studio colide com o original: {destination}")
            if destination.exists():
                raise RuntimeError(
                    f"Destino já existe; lote bloqueado para preservar resultado anterior: {destination}"
                )

        studio = build_studio(root)
        if copy_only:
            # Synthetic copy-only mode tests the visual boundary without any
            # audio handoff; it does not pretend to validate Speech processing.
            results = [studio.process(item) for item in items]
        else:
            # First collect a complete, read-only visual plan for the batch.
            # Studio is not allowed to inspect/classify/extract/process audio.
            visual_analyses = {}
            visual_plans = {}
            for item in items:
                identity = (str(item.source_id), str(item.telegram_message_id))
                visual = studio.analyze_visual(item)
                visual_analyses[identity] = visual
                visual_plans[identity] = visual.plan

            # Speech now owns RVC, effect gain, timeline trimming/fades/mixing,
            # and emits one complete soundtrack for each item before any render.
            safe_batch = re.sub(r"[^A-Za-z0-9_.-]+", "_", manifest["batch_id"]).strip("._-") or "batch"
            package_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            package_root = root / "storage" / "validation" / "speech-audio-v3" / f"{safe_batch}-{package_stamp}"
            configured_effect = os.getenv("ARMORED_SPEECH_EFFECT", "").strip()
            effect_source = (
                Path(configured_effect).expanduser()
                if configured_effect
                else root / "ArmoredSpeech" / "assets" / "efeitosonoro.wav"
            )
            if not effect_source.is_file():
                raise BatchManifestError(
                    "Efeito sonoro não encontrado no ArmoredSpeech. Execute "
                    "ArmoredSpeech/scripts/migrate_audio_assets.ps1 antes do teste real."
                )
            intro = os.getenv("ARMORED_STUDIO_INTRO", "1") != "0"
            intro_position = os.getenv("ARMORED_STUDIO_INTRO_POSITION", "final")
            prepared = prepare_audio_packages_for_items(
                items,
                speech_database=speech_database,
                workspace_root=root,
                package_root=package_root,
                effect_source_path=effect_source,
                visual_plans=visual_plans,
                intro=intro,
                intro_position=intro_position,
            )
            validated_payloads = []
            track_hash_cache: dict[tuple[str, int, int, int, int], str] = {}
            for item, payload in prepared:
                try:
                    validated = validate_audio_package(
                        payload,
                        workspace_root=root,
                        expected_source_id=str(item.source_id),
                        expected_item_id=str(item.telegram_message_id),
                        expected_original_path=Path(item.original_path),
                        track_hash_cache=track_hash_cache,
                    )
                except SpeechHandoffError as exc:
                    raise BatchManifestError(
                        f"Pacote final de áudio do Speech inválido antes do Studio: {exc}"
                    ) from exc
                report["audio_packages"].append({
                    "source_id": str(item.source_id),
                    "item_id": str(item.telegram_message_id),
                    "audio_class": validated.audio_class,
                    "route": validated.route,
                    "voice_track_path": str(validated.voice_track_path) if validated.voice_track_path else None,
                    "voice_track_sha256": validated.voice_sha256,
                    "effect_track_path": str(validated.effect_track_path),
                    "effect_track_sha256": validated.effect_sha256,
                    "audio_filter_graph_sha256": validated.audio_filter_graph_sha256,
                    "visual_plan_sha256": validated.visual_plan_sha256,
                    "original_sha256": validated.original_sha256,
                })
                validated_payloads.append((item, payload))

            # The visual detector runs in Studio, but banner cut timing is
            # measured by Speech from the original audio. Apply the returned
            # visual start to the same visual plan whose digest Speech signed,
            # and validate it before the first final render.
            for item, payload in validated_payloads:
                identity = (str(item.source_id), str(item.telegram_message_id))
                effective_plan = dict(visual_plans[identity])
                try:
                    speech_video_start = float(
                        payload["audio_timeline"]["video_start_seconds"]
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise BatchManifestError(
                        f"Speech video timeline missing for {identity}"
                    ) from exc
                if speech_video_start < 0:
                    raise BatchManifestError(
                        f"Speech video timeline is negative for {identity}"
                    )
                effective_plan["video_start"] = speech_video_start
                plan_validation = validar_plano_exportacao(effective_plan)
                if not plan_validation.get("valido"):
                    raise BatchManifestError(
                        f"Plano visual com tempo Speech inválido para {identity}: "
                        + "; ".join(plan_validation.get("erros", []))
                    )
                expected_plan_hash = payload["audio_timeline"]["visual_plan_sha256"]
                if visual_plan_digest(effective_plan) != expected_plan_hash:
                    raise BatchManifestError(
                        f"Plano visual não confere com o handoff Speech para {identity}"
                    )
                visual_plans[identity] = effective_plan
                visual_result = visual_analyses[identity]
                if hasattr(visual_result, "__dataclass_fields__"):
                    visual_analyses[identity] = replace(
                        visual_result,
                        plan=effective_plan,
                        validation=plan_validation,
                    )
                else:
                    # Lightweight test doubles use mutable SimpleNamespace objects.
                    visual_result.plan = effective_plan
                    visual_result.validation = plan_validation

            if hasattr(studio, "engine"):
                studio.engine._audio_track_hash_cache = track_hash_cache

            # Recheck originals and timeline-plan correspondence before the first render.
            changed_before_studio = [
                (str(item.source_id), str(item.telegram_message_id))
                for item in items
                if _sha256(Path(item.original_path)) != original_hashes[
                    (str(item.source_id), str(item.telegram_message_id))
                ]
            ]
            if changed_before_studio:
                raise RuntimeError(f"Original mudou durante Speech; Studio bloqueado: {changed_before_studio}")

            # All soundtracks are prepared/validated before any Studio render begins.
            results = [
                studio.process(
                    item,
                    speech_payload=payload,
                    visual_analysis=visual_analyses[(str(item.source_id), str(item.telegram_message_id))],
                )
                for item, payload in validated_payloads
            ]

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
        report["report_path"] = str(report_path)
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_report(report_path, report)
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
        report["report_path"] = str(report_path)
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        _write_report(report_path, report)
        raise
    finally:
        if previous_copy_mode is None:
            os.environ.pop("ARMORED_STUDIO_FORCE_COPY", None)
        else:
            os.environ["ARMORED_STUDIO_FORCE_COPY"] = previous_copy_mode
        if previous_allow_copy is None:
            os.environ.pop("ARMORED_STUDIO_ALLOW_COPY", None)
        else:
            os.environ["ARMORED_STUDIO_ALLOW_COPY"] = previous_allow_copy


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
