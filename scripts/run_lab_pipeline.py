from __future__ import annotations

"""Run one explicitly selected local fixture batch through the isolated pipeline.

This runner never scans Telegram history and never opens the operational database.
It reserves a new SQLite under storage/validation, uses only local fixture video
files supplied by the caller, disables Hub/publication by construction, and keeps
every artifact/report if a stage fails.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable
from uuid import uuid4

from dotenv import dotenv_values

from armored_core.catch_up_stages import run_catch_up_stage
from armored_core.coordinator import Coordinator
from armored_core.database import Database
from armored_core.models import State
from armored_core.storage import Storage


VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}


class LabPipelineError(RuntimeError):
    """A closed-gate lab pipeline failure with a durable report location."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def _path_in_root(root: Path, value: object, label: str, *, must_exist: bool) -> Path:
    raw = Path(str(value)).expanduser()
    path = raw if raw.is_absolute() else root / raw
    try:
        resolved = path.resolve(strict=must_exist)
    except OSError as exc:
        raise LabPipelineError(f"{label} não pode ser resolvido: {path}") from exc
    if not _inside(resolved, root):
        raise LabPipelineError(f"{label} precisa permanecer dentro do AudioLab: {resolved}")
    return resolved


def load_fixture_manifest(root: Path, manifest_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate a user-selected, offline fixture list before creating a database."""
    root = Path(root).resolve(strict=True)
    manifest_path = _path_in_root(root, manifest_path, "manifest", must_exist=True)
    if not manifest_path.is_file():
        raise LabPipelineError("Manifest de entrada não é um arquivo.")
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LabPipelineError(f"Manifest JSON inválido: {type(exc).__name__}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise LabPipelineError("Manifest exige schema_version=1.")
    if payload.get("phase") != "ARMORED_AUDIO_LAB_INPUT":
        raise LabPipelineError("Manifest exige phase=ARMORED_AUDIO_LAB_INPUT.")

    sources = payload.get("sources")
    if (
        not isinstance(sources, list)
        or len(sources) != 3
        or any(not isinstance(item, str) or not item.strip() for item in sources)
        or len({item.strip() for item in sources}) != 3
    ):
        raise LabPipelineError("Manifest deve declarar exatamente três source labels únicos.")
    sources = [value.strip() for value in sources]

    raw_items = payload.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise LabPipelineError("Manifest deve conter pelo menos um vídeo de teste.")
    seen: set[tuple[str, str]] = set()
    items: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise LabPipelineError(f"Item {index}: esperado objeto JSON.")
        source_label = str(raw.get("source_id") or "").strip()
        message_id = str(raw.get("telegram_message_id") or "").strip()
        if source_label not in sources:
            raise LabPipelineError(f"Item {index}: source_id precisa referenciar um dos três labels.")
        if not message_id.isdigit() or int(message_id) <= 0:
            raise LabPipelineError(f"Item {index}: telegram_message_id precisa ser inteiro positivo.")
        identity = (source_label, message_id)
        if identity in seen:
            raise LabPipelineError(f"Manifest contém identidade de mensagem repetida: {identity}.")
        seen.add(identity)

        source_path = _path_in_root(
            root, raw.get("source_path") or "", f"Item {index} source_path", must_exist=True
        )
        if not source_path.is_file() or source_path.suffix.lower() not in VIDEO_SUFFIXES:
            raise LabPipelineError(f"Item {index}: source_path precisa ser um vídeo existente suportado.")
        if source_path.stat().st_size <= 0:
            raise LabPipelineError(f"Item {index}: vídeo de entrada vazio.")
        original_url_value = raw.get("original_url")
        if original_url_value is not None and not isinstance(original_url_value, str):
            raise LabPipelineError(f"Item {index}: original_url deve ser texto ou null.")
        original_url = str(original_url_value or "").strip() or None
        topic_id = raw.get("topic_id")
        if topic_id is not None and (not isinstance(topic_id, int) or isinstance(topic_id, bool)):
            raise LabPipelineError(f"Item {index}: topic_id precisa ser inteiro ou null.")
        topic_name = raw.get("topic_name")
        if topic_name is not None and not isinstance(topic_name, str):
            raise LabPipelineError(f"Item {index}: topic_name precisa ser texto ou null.")
        items.append({
            "source_label": source_label,
            "telegram_message_id": message_id,
            "source_path": source_path,
            "source_sha256": _sha256(source_path),
            "original_url": original_url,
            "topic_id": topic_id,
            "topic_name": topic_name,
            "input_index": index,
        })
    return {"manifest_path": str(manifest_path), "sources": sources, "batch_id": str(payload.get("batch_id") or "local-fixture")}, items


class LocalFixtureSource:
    """Three-source Sync adapter backed only by explicitly listed local videos."""

    def __init__(self, source_map: dict[str, str], items: list[dict[str, Any]]) -> None:
        self.source_map = dict(source_map)
        self.sources = tuple(SimpleNamespace(source_id=value) for value in source_map.values())
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._ordered_items: list[dict[str, Any]] = []
        for item in items:
            entry = dict(item)
            entry["source_id"] = self.source_map[entry.pop("source_label")]
            key = (entry["source_id"], entry["telegram_message_id"])
            self._items[key] = entry
            self._ordered_items.append(entry)
        self._historical_scan_exhausted = False
        self._historical_materialization_failed = False
        self._ingested: set[str] = set()

    @property
    def historical_collection_limited(self) -> bool:
        return False

    @property
    def historical_limit_reached(self) -> bool:
        return False

    @property
    def historical_scan_exhausted(self) -> bool:
        return self._historical_scan_exhausted

    @property
    def historical_materialization_failed(self) -> bool:
        return self._historical_materialization_failed

    async def iter_historical_candidates_async(self):
        self._historical_scan_exhausted = False
        for row in self._ordered_items:
            yield SimpleNamespace(
                telegram_message_id=row["telegram_message_id"],
                source_id=row["source_id"],
                topic_id=row["topic_id"],
                topic_name=row["topic_name"],
                original_url=row["original_url"],
                source_path=row["source_path"],
                materialize=None,
            )
        self._historical_scan_exhausted = True

    def mark_ingested(self, message_id: str) -> None:
        self._ingested.add(str(message_id))

    def mark_materialization_failed(self, *_args, **_kwargs) -> None:
        self._historical_materialization_failed = True

    def is_historical_complete(self) -> bool:
        # The fixture scan is finite and complete, but it must never flip any
        # operational Telegram source checkpoint or declare production LIVE.
        return False

    async def materialize_candidate_async(self, source_id: str, message_id: str, target: Path) -> None:
        key = (str(source_id), str(message_id))
        row = self._items.get(key)
        if row is None:
            raise FileNotFoundError(f"fixture-candidate-not-found:{source_id}/{message_id}")
        source_path = Path(row["source_path"])
        if not source_path.is_file() or _sha256(source_path) != row["source_sha256"]:
            raise RuntimeError(f"fixture-source-changed:{source_id}/{message_id}")
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"refusing-to-overwrite-stock-target:{target}")
        shutil.copy2(source_path, target)
        if _sha256(target) != row["source_sha256"]:
            raise RuntimeError(f"fixture-copy-hash-mismatch:{source_id}/{message_id}")


LAB_ENV_ALLOWLIST = frozenset({
    # Shopee lookup and IA caption generation only; never import runtime/Telegram settings.
    "SHOPEE_AFFILIATE_API_URL",
    "SHOPEE_APP_ID",
    "SHOPEE_SECRET_KEY",
    "GEMINI_API_KEY",
    "ARMORED_IA_API_TIMEOUT",
    "ARMORED_IA_AUDIENCE",
    "ARMORED_IA_CAPTION_ENABLED",
    "ARMORED_IA_ENABLED",
    "ARMORED_IA_MAX_ATTEMPTS",
    "ARMORED_IA_MAX_CANDIDATES",
    "ARMORED_IA_MODEL",
    "ARMORED_IA_PROVIDER",
    "ARMORED_IA_RETRY_DELAY",
    "ARMORED_IA_OLLAMA_URL",
    "ARMORED_IA_OLLAMA_MODEL",
    # Optional model thresholds are values only, not paths or runtime switches.
    "ARMORED_AUDIO_CONTENT_MUSIC_THRESHOLD",
    "ARMORED_AUDIO_CONTENT_SPEECH_THRESHOLD",
    "ARMORED_AUDIO_VAD_MIN_ACTIVITY_RATIO",
})


def _load_project_environment(root: Path) -> None:
    """Load only whitelisted inference settings from project files.

    credentials/project.env is copied from a production project in some setups.
    Telegram tokens/session configuration, Hub destinations, runtime flags, DB
    paths, and external filesystem paths are intentionally not imported.
    """
    loaded_from_project: set[str] = set()
    project_env = root / "credentials" / "project.env"
    if project_env.is_file():
        for key, value in dotenv_values(project_env).items():
            if key in LAB_ENV_ALLOWLIST and value is not None:
                os.environ[key] = value
                loaded_from_project.add(key)

    project_config = root / ".env"
    if project_config.is_file():
        for key, value in dotenv_values(project_config).items():
            if key in LAB_ENV_ALLOWLIST and value is not None and key not in loaded_from_project:
                os.environ.setdefault(key, value)


def _require_executable(name: str, env_key: str, default: str) -> str:
    value = (os.getenv(env_key) or default).strip()
    if not value:
        raise LabPipelineError(f"{env_key} está vazio.")
    if Path(value).is_file() or shutil.which(value):
        return value
    raise LabPipelineError(f"{name} não encontrado; verifique {env_key}.")


def _live_preflight(root: Path, ia_provider: str, speech_processor: Any | None) -> Any:
    """Check external dependencies before reserving candidates or downloading originals."""
    if not (os.getenv("SHOPEE_APP_ID") or "").strip() or not (os.getenv("SHOPEE_SECRET_KEY") or "").strip():
        raise LabPipelineError("SHOPEE_APP_ID/SHOPEE_SECRET_KEY ausentes em credentials/project.env.")
    if os.getenv("ARMORED_IA_ENABLED", "1") != "1" or os.getenv("ARMORED_IA_CAPTION_ENABLED", "1") != "1":
        raise LabPipelineError("Para este E2E, ARMORED_IA_ENABLED e ARMORED_IA_CAPTION_ENABLED devem ser 1.")

    _require_executable("FFmpeg", "ARMORED_FFMPEG", "ffmpeg")
    _require_executable("FFprobe", "ARMORED_FFPROBE", "ffprobe")
    effect = root / "ArmoredSpeech" / "assets" / "efeitosonoro.wav"
    if not effect.is_file() or effect.stat().st_size <= 0:
        raise LabPipelineError(
            "Efeito canônico ArmoredSpeech/assets/efeitosonoro.wav ausente ou vazio; não substitua o asset por outro."
        )

    if ia_provider == "ollama":
        import requests

        base_url = (os.getenv("ARMORED_IA_OLLAMA_URL") or "http://localhost:11434").strip().rstrip("/")
        model = (os.getenv("ARMORED_IA_OLLAMA_MODEL") or "qwen3:8b").strip()
        try:
            response = requests.get(f"{base_url}/api/tags", timeout=4)
            response.raise_for_status()
            available = response.json().get("models", [])
        except Exception as exc:
            raise LabPipelineError(
                f"Ollama não respondeu em {base_url}/api/tags ({type(exc).__name__}); nenhum item foi reservado."
            ) from exc
        names = {
            str(row.get("name") or "").strip()
            for row in available if isinstance(row, dict)
        }
        if model not in names:
            raise LabPipelineError(
                f"Modelo Ollama {model!r} não está instalado localmente; nenhum item foi reservado."
            )

    if speech_processor is None:
        try:
            from ArmoredSpeech.model_processor import ModelProcessor
            speech_processor = ModelProcessor()
            # Loads/checks the actual local language, VAD, and music models
            # before Sync reserves a fixture batch.
            speech_processor.intelligence.preflight()
        except Exception as exc:
            raise LabPipelineError(
                f"Preflight do classificador ArmoredSpeech falhou ({type(exc).__name__}); nenhum item foi reservado."
            ) from exc
    elif callable(getattr(getattr(speech_processor, "intelligence", None), "preflight", None)):
        speech_processor.intelligence.preflight()
    return speech_processor


def _stage_summary(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "stage": report.get("stage"),
        "processed": report.get("processed"),
        "per_source": report.get("per_source"),
        "outcomes": report.get("outcomes"),
        "errors": report.get("errors"),
        "historical_scan_exhausted": report.get("historical_scan_exhausted"),
    }


def run_lab_pipeline(
    root: Path,
    manifest_path: Path,
    *,
    ia_provider: str | None = None,
    require_live_preflight: bool = True,
    vision_factory: Callable[[], Any] | None = None,
    ia_factory: Callable[[], Any] | None = None,
    speech_processor: Any | None = None,
    studio_runner: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run Sync -> Vision -> Stock -> IA -> Speech -> Studio on an isolated DB.

    Live mode requires local credentials/assets/models. Tests inject deterministic
    service doubles and explicitly set require_live_preflight=False.
    """
    root = Path(root).resolve(strict=True)
    _load_project_environment(root)
    previous_ia_provider = os.environ.get("ARMORED_IA_PROVIDER")
    input_manifest, fixture_items = load_fixture_manifest(root, manifest_path)
    provider = (ia_provider or os.getenv("ARMORED_IA_PROVIDER") or "ollama").strip().lower()
    if provider not in {"ollama", "gemini"}:
        raise LabPipelineError("IA provider precisa ser ollama ou gemini.")
    os.environ["ARMORED_IA_PROVIDER"] = provider

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    run_token = uuid4().hex[:10]
    run_id = f"{stamp}_{run_token}"
    run_dir = root / "storage" / "validation" / f"full_pipeline_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    report_path = run_dir / "pipeline-report.json"
    report: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "status": "RUNNING",
        "input_batch_id": input_manifest["batch_id"],
        "source_mode": "EXPLICIT_LOCAL_FIXTURE_MANIFEST",
        "database": str(run_dir / "core.sqlite3"),
        "run_directory": str(run_dir),
        "report_path": str(report_path),
        "publication_disabled": True,
        "hub_loaded": False,
        "ia_provider": provider,
        "stage_order": ["sync", "vision", "stock", "ia", "speech", "studio"],
        "stages": {},
        "outputs": [],
        "fixture_originals_intact": None,
    }

    def save_report() -> None:
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    db: Database | None = None
    coordinator: Coordinator | None = None
    try:
        processor = _live_preflight(root, provider, speech_processor) if require_live_preflight else speech_processor
        if processor is None:
            from ArmoredSpeech.model_processor import ModelProcessor
            processor = ModelProcessor()

        labels = input_manifest["sources"]
        suffixes = ("A", "B", "C")
        source_map = {
            label: f"sourceLab{run_token}{suffix}"
            for label, suffix in zip(labels, suffixes, strict=True)
        }
        report["source_map"] = source_map

        # Record a unique test database under this run's validation directory;
        # Storage still uses the canonical AudioLab filesystem layout.
        db = Database(run_dir / "core.sqlite3")
        storage = Storage(root)
        source = LocalFixtureSource(source_map, fixture_items)

        if vision_factory is None:
            from ArmoredVision.service import ArmoredVision
            vision_factory = ArmoredVision
        if ia_factory is None:
            from ArmoredIA.service import ArmoredIA
            ia_factory = ArmoredIA

        # No Hub module or production publisher is constructed or imported.
        coordinator = Coordinator(
            db=db,
            storage=storage,
            vision=vision_factory(),
            studio=None,
            publisher=None,
            source=source,
            ia=ia_factory(),
        )
        report["fixture_count"] = len(fixture_items)
        report["fixture_sha256"] = [
            {"index": row["input_index"], "sha256": row["source_sha256"]}
            for row in fixture_items
        ]
        save_report()

        for stage in ("sync", "vision", "stock"):
            stage_report = asyncio.run(run_catch_up_stage(coordinator, stage))
            report["stages"][stage] = _stage_summary(stage_report)
            save_report()
            if stage_report.get("errors"):
                raise LabPipelineError(
                    f"Etapa {stage} terminou com erro(s); veja stages.{stage} no relatório."
                )
            if stage == "sync" and not stage_report.get("historical_scan_exhausted"):
                raise LabPipelineError("A varredura do manifest de fixtures não foi concluída.")
            if stage == "stock" and int(stage_report.get("outcomes", {}).get("approved_missing_original", 0)):
                raise LabPipelineError("Stock deixou originais aprovados sem materializar; IA/Speech bloqueados.")

        duplicate_count = int(db.conn.execute(
            "SELECT COUNT(*) FROM items WHERE state=?", (State.DUPLICATE.value,)
        ).fetchone()[0])
        waiting_count = int(db.conn.execute(
            "SELECT COUNT(*) FROM items WHERE state=?", (State.WAITING_VISION.value,)
        ).fetchone()[0])
        report["counts_after_vision"] = {
            "reserved_items": int(db.conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]),
            "duplicates_blocked": duplicate_count,
            "waiting_vision": waiting_count,
        }

        approved = db.pending_vision_approved_items()
        if not approved:
            raise LabPipelineError(
                "Nenhum item com aprovação Vision e ORIGINAL íntegro chegou à etapa IA; "
                "veja counts_after_vision e os erros do lote."
            )
        ia_failed: list[dict[str, str]] = []
        for item in approved:
            current = db.get(item.content_id)
            if not current.original_path.is_file():
                ia_failed.append({"content_id": current.content_id, "reason": "original-missing-after-stock"})
                continue
            try:
                coordinator.pipeline.run(current.content_id, stop_after_ia=True)
            except Exception as exc:
                ia_failed.append({"content_id": current.content_id, "reason": type(exc).__name__})
                continue
            resulting = db.get(current.content_id)
            if resulting.state is not State.SPEECH:
                ia_failed.append({
                    "content_id": current.content_id,
                    "reason": f"expected-SPEECH-got-{resulting.state.value}",
                })
        report["stages"]["ia"] = {
            "eligible": len(approved),
            "ready_for_speech": len(approved) - len(ia_failed),
            "failed_or_blocked": ia_failed,
        }
        save_report()
        if ia_failed:
            raise LabPipelineError("IA não concluiu todos os itens aprovados; Speech/Studio bloqueados.")

        speech_items = [
            db.get(str(row["content_id"]))
            for row in db.conn.execute(
                "SELECT content_id FROM items WHERE state=? ORDER BY source_id, "
                "CAST(telegram_message_id AS INTEGER), content_id",
                (State.SPEECH.value,),
            ).fetchall()
        ]
        if not speech_items:
            raise LabPipelineError("Nenhum item chegou ao estado SPEECH; Studio não será iniciado.")

        from ArmoredSpeech.batch import SpeechItem, run_bulk

        speech_database = run_dir / "speech.sqlite3"
        speech_rows = [
            SpeechItem(
                source_id=str(item.source_id),
                item_id=str(item.telegram_message_id),
                video_path=Path(item.original_path),
                source_order=index,
            )
            for index, item in enumerate(speech_items)
        ]
        speech_counts = run_bulk(
            speech_rows,
            database=speech_database,
            processor=processor,
        )
        with sqlite3.connect(f"file:{speech_database.resolve().as_posix()}?mode=ro", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            decisions = connection.execute(
                "SELECT source_id,item_id,status,audio_class,wav_path,original_sha256_before,"
                "original_sha256_after,error FROM armored_speech_results"
            ).fetchall()
        decision_map = {(str(row["source_id"]), str(row["item_id"])): row for row in decisions}
        speech_errors: list[str] = []
        for item in speech_items:
            key = (str(item.source_id), str(item.telegram_message_id))
            decision = decision_map.get(key)
            if decision is None or str(decision["status"]) != "READY":
                status = str(decision["status"]) if decision is not None else "MISSING_DECISION"
                speech_errors.append(f"{item.content_id}:{status}")
                continue
            before = str(decision["original_sha256_before"] or "")
            after = str(decision["original_sha256_after"] or "")
            if not before or before != after or _sha256(item.original_path) != before:
                speech_errors.append(f"{item.content_id}:original-hash-evidence-invalid")
            wav = Path(str(decision["wav_path"] or ""))
            if not wav.is_file() or wav.stat().st_size <= 0:
                speech_errors.append(f"{item.content_id}:speech-wav-missing")
        report["stages"]["speech"] = {
            "database": str(speech_database),
            "counts": speech_counts,
            "ready": len(speech_items) - len(speech_errors),
            "failed_or_blocked": speech_errors,
        }
        save_report()
        if speech_counts.get("failed", 0) or speech_errors:
            raise LabPipelineError("Speech não aprovou integralmente os itens; Studio bloqueado.")

        speech_manifest_path = run_dir / "speech-studio-manifest.json"
        manifest_items = []
        for item in speech_items:
            manifest_items.append({
                "source_id": str(item.source_id),
                "telegram_message_id": str(item.telegram_message_id),
                "content_id": str(item.content_id),
                "state": State.SPEECH.value,
                "workspace": str(item.workspace.resolve()),
                "original_path": str(item.original_path.resolve()),
                "original_sha256": str(item.original_sha256 or _sha256(item.original_path)),
                "working_path": str(item.working_path.resolve()) if item.working_path else None,
                "affiliate_name": item.affiliate_name,
                "affiliate_url": item.affiliate_url,
                "original_url": item.original_url,
                "topic_id": item.topic_id,
                "topic_name": item.topic_name,
                "affiliate_urls": list(item.affiliate_urls),
                "ia_context": item.ia_context or {},
            })
        speech_manifest = {
            "schema_version": 1,
            "phase": "ARMORED_SPEECH",
            "phase_status": "COMPLETE",
            "approved": True,
            "batch_id": f"lab-{run_id}",
            "expected_item_count": len(manifest_items),
            "items": manifest_items,
        }
        speech_manifest_path.write_text(
            json.dumps(speech_manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        # Fence Core items in a durable state before the external versioned
        # batch runner is entered. A process interruption cannot auto-publish.
        for item in speech_items:
            db.transition(
                item.content_id,
                State.STUDIO_BATCH,
                "speech-v5-ready-entering-isolated-studio-batch",
            )
        save_report()

        if studio_runner is None:
            from ArmoredSpeech.studio_batch_runner import run_complete_batch
            studio_runner = run_complete_batch
        studio_report = studio_runner(
            root=root,
            manifest_path=speech_manifest_path,
            speech_database=speech_database,
            report_path=run_dir / "studio-report.json",
            copy_only=False,
        )
        if studio_report.get("status") != "SUCCESS" or studio_report.get("originals_intact") is not True:
            raise LabPipelineError("Speech/Studio não confirmou SUCCESS e integridade de todos os originais.")

        processed = {
            (str(row.get("source_id")), str(row.get("item_id"))): row
            for row in studio_report.get("processed", [])
        }
        output_records: list[dict[str, Any]] = []
        for item in speech_items:
            identity = (str(item.source_id), str(item.telegram_message_id))
            result = processed.get(identity)
            if result is None:
                raise LabPipelineError(f"Studio não reportou o item esperado {identity}.")
            original = Path(item.original_path).resolve(strict=True)
            output_path = Path(str(result.get("output_path") or "")).resolve(strict=True)
            if not _inside(output_path, root) or not output_path.is_file() or output_path.stat().st_size <= 0:
                raise LabPipelineError(f"Studio output inválido ou fora do laboratório para {identity}.")
            expected_hash = str(item.original_sha256 or "")
            if not expected_hash or _sha256(original) != expected_hash:
                raise LabPipelineError(f"Original mudou depois de Studio para {identity}.")
            if str(result.get("original_sha256_before") or "").lower() != expected_hash.lower():
                raise LabPipelineError(f"Studio não comprovou o hash original para {identity}.")
            db.set_result(item.content_id, output_path)
            db.transition(item.content_id, State.STUDIO_COMPLETE, "speech-studio-v5-complete-publication-disabled")
            output_records.append({
                "source_id": item.source_id,
                "telegram_message_id": item.telegram_message_id,
                "content_id": item.content_id,
                "result_path": str(output_path),
                "result_size": output_path.stat().st_size,
                "original_sha256": expected_hash,
                "state": State.STUDIO_COMPLETE.value,
            })
        report["stages"]["studio"] = {
            "status": "SUCCESS",
            "processed": len(output_records),
            "report": str(run_dir / "studio-report.json"),
            "contract_version": 5,
            "hub_or_publication": "DISABLED",
        }
        report["outputs"] = output_records

        changed_fixtures = [
            str(row["source_path"])
            for row in fixture_items
            if not Path(row["source_path"]).is_file()
            or _sha256(Path(row["source_path"])) != row["source_sha256"]
        ]
        report["fixture_originals_intact"] = not changed_fixtures
        report["changed_fixture_originals"] = changed_fixtures
        if changed_fixtures:
            raise LabPipelineError("Um vídeo fixture foi alterado; preserve os relatórios e não repita o lote.")
        report["status"] = "SUCCESS"
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_report()
        return report
    except Exception as exc:
        report["status"] = "FAILED"
        report["error_type"] = type(exc).__name__
        report["error"] = str(exc)[:1200]
        report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        save_report()
        if isinstance(exc, LabPipelineError):
            raise LabPipelineError(f"{exc} Relatório: {report_path}") from exc
        raise LabPipelineError(
            f"{type(exc).__name__}: {str(exc)[:500]}. Relatório preservado em {report_path}"
        ) from exc
    finally:
        if db is not None:
            db.close()
        if previous_ia_provider is None:
            os.environ.pop("ARMORED_IA_PROVIDER", None)
        else:
            os.environ["ARMORED_IA_PROVIDER"] = previous_ia_provider


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Executa um lote explícito no AudioLab: Sync -> Vision -> Stock -> IA -> Speech -> Studio; sem Hub."
    )
    default_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--root", type=Path, default=default_root, help="raiz de ArmoredAudioLab")
    parser.add_argument("--manifest", type=Path, required=True, help="JSON de vídeos locais e URLs Shopee a testar")
    parser.add_argument("--ia-provider", choices=("ollama", "gemini"), default=None)
    parser.add_argument(
        "--approve-real-processing",
        action="store_true",
        required=True,
        help="confirma uso real dos serviços/modelos locais no SQLite e storage isolados do lote",
    )
    args = parser.parse_args(argv)
    try:
        report = run_lab_pipeline(args.root, args.manifest, ia_provider=args.ia_provider)
    except Exception as exc:
        print(f"ARMORED_AUDIO_LAB_PIPELINE=FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    print("ARMORED_AUDIO_LAB_PIPELINE=SUCCESS")
    print("STAGES=SYNC,VISION,STOCK,IA,SPEECH,STUDIO")
    print("HUB=DISABLED")
    print(f"ITEMS_STUDIO={len(report['outputs'])}")
    print(f"ORIGINALS_INTACT={report['fixture_originals_intact']}")
    print(f"RUN_DIR={report['run_directory']}")
    print(f"REPORT={report['report_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
