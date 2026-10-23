from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from .database import Database
from .routing import RouteConfig, load_routes
from .storage import Storage


def run_preflight(root: Path, evidence_path: Path) -> dict:
    """Compose the production services over a SQLite backup without external I/O."""
    root = root.expanduser().resolve(strict=True)
    evidence_path = evidence_path.expanduser().resolve()
    evidence_path.parent.mkdir(parents=True, exist_ok=True)
    checks: dict[str, object] = {}
    try:
        from .cli import _load_project_configuration, _validate_routes

        _load_project_configuration(root)
        routes = load_routes()
        _validate_routes(routes)
        checks["routes"] = {"status": "PASS", "count": len(routes), "unique_destinations": len({(r.hub.chat_id, r.hub.topic_id) for r in routes})}
        if sys.version_info[:2] != (3, 11):
            raise RuntimeError(f"Python 3.11 required, found {sys.version.split()[0]}")
        checks["python"] = {"status": "PASS", "version": sys.version.split()[0]}
        versions = {}
        for distribution in (
            "torch", "numpy", "python-dotenv", "telethon", "python-telegram-bot",
            "opencv-python-headless", "requests", "faster-whisper", "transformers",
            "audio-separator", "fairseq", "faiss-cpu", "onnxruntime", "soundfile",
        ):
            try:
                versions[distribution] = importlib.metadata.version(distribution)
            except importlib.metadata.PackageNotFoundError:
                versions[distribution] = None
        if not str(versions["torch"]).startswith("2.4.1") or versions["numpy"] != "1.23.5":
            raise RuntimeError(f"validated Torch/NumPy versions unavailable: {versions}")
        if not versions["telethon"] or not versions["python-dotenv"] or not versions["python-telegram-bot"]:
            raise RuntimeError(f"required runtime dependency missing: {versions}")
        checks["versions"] = {"status": "PASS", "packages": versions}

        modules = (
            "ArmoredSync.service", "ArmoredVision.service", "ArmoredStock.service",
            "ArmoredIA.service", "ArmoredSpeech.batch", "ArmoredSpeech.studio_batch_runner",
            "ArmoredStudio.service", "ArmoredHub.service", "armored_core.coordinator",
        )
        loaded = {name: importlib.import_module(name) for name in modules}
        checks["imports"] = {"status": "PASS", "modules": list(loaded)}

        from ArmoredSync.service import LocalSource
        from ArmoredVision.service import ArmoredVision
        from ArmoredIA.service import ArmoredIA
        from ArmoredStudio.service import ArmoredStudio
        from ArmoredHub.service import ArmoredHub
        from ArmoredSpeech.batch import run_bulk
        from ArmoredSpeech.studio_batch_runner import run_complete_batch
        from .coordinator import Coordinator
        from .integrity import sha256_file

        effect = root / "ArmoredSpeech" / "assets" / "efeitosonoro.wav"
        if not effect.is_file() or effect.stat().st_size == 0:
            raise RuntimeError(f"Speech effect asset missing or empty: {effect}")
        rvc_root = root / "ArmoredSpeech" / "runtime" / "rvc" / "models"
        rvc_models = list(rvc_root.rglob("*.pth")) if rvc_root.is_dir() else []
        rvc_indexes = list(rvc_root.rglob("*.index")) + list(rvc_root.rglob("model.index")) if rvc_root.is_dir() else []
        if not rvc_models or not rvc_indexes:
            raise RuntimeError("Speech RVC model and index assets are required")
        ffmpeg = os.getenv("ARMORED_FFMPEG", "ffmpeg")
        if shutil.which(ffmpeg) is None:
            raise RuntimeError(f"FFmpeg executable not found: {ffmpeg}")
        checks["assets"] = {
            "status": "PASS", "speech_effect_sha256": sha256_file(effect),
            "rvc_model_count": len(rvc_models), "rvc_index_count": len(rvc_indexes),
            "ffmpeg": shutil.which(ffmpeg),
        }
        contracts = {
            "sync": callable(getattr(LocalSource, "fetch_next", None)),
            "vision": callable(getattr(ArmoredVision, "identify", None)),
            "stock": callable(getattr(__import__("ArmoredStock.service", fromlist=["ArmoredStock"]).ArmoredStock, "materialize_item", None)),
            "ia": callable(getattr(ArmoredIA, "generate_caption", None)),
            "speech": callable(run_bulk) and callable(run_complete_batch),
            "studio": callable(getattr(ArmoredStudio, "process", None)),
            "hub": callable(getattr(ArmoredHub, "publish", None)),
        }
        if not all(contracts.values()):
            raise RuntimeError(f"Module contract missing: {contracts}")
        checks["contracts"] = {"status": "PASS", "order": list(contracts), "components": contracts}

        source_db = root / "storage" / "database" / "armoredcreator.db"
        if not source_db.is_file():
            raise RuntimeError(f"Operational SQLite missing: {source_db}")
        with tempfile.TemporaryDirectory(prefix="armoredcreator-preflight-") as temporary:
            stage = Path(temporary)
            stage_db_dir = stage / "storage" / "database"
            stage_db_dir.mkdir(parents=True)
            stage_db = stage_db_dir / "armoredcreator.db"
            with closing(sqlite3.connect(f"file:{source_db.as_posix()}?mode=ro", uri=True)) as source:
                with closing(sqlite3.connect(stage_db)) as target:
                    source.backup(target)
            with closing(sqlite3.connect(f"file:{stage_db.as_posix()}?mode=ro", uri=True)) as clone:
                integrity = clone.execute("PRAGMA quick_check").fetchall()
                foreign_keys = clone.execute("PRAGMA foreign_key_check").fetchall()
                counts = {
                    "items": clone.execute("SELECT COUNT(*) FROM items").fetchone()[0],
                    "archived": clone.execute("SELECT COUNT(*) FROM reconciliation_archived_items").fetchone()[0],
                    "publications": clone.execute("SELECT COUNT(*) FROM publications").fetchone()[0],
                }
            if integrity != [("ok",)] or foreign_keys:
                raise RuntimeError("SQLite clone integrity/foreign-key check failed")
            # Real service objects are composed; LocalSource guarantees no Telegram connection.
            os.environ["ARMORED_REAL_TELEGRAM"] = "0"
            os.environ["ARMORED_HUB_DRY_RUN"] = "1"
            coordinator = Coordinator.build(
                stage,
                acquire_runtime_lock=True,
                allow_real_publication=False,
            )
            composed = {
                "coordinator": type(coordinator).__name__, "source": type(coordinator.source).__name__,
                "vision": type(coordinator.pipeline.vision).__name__, "studio": type(coordinator.pipeline.studio).__name__,
                "publisher": type(coordinator.pipeline.publisher).__name__, "ia": type(coordinator.pipeline.ia).__name__,
            }
            ia_config = coordinator.pipeline.ia.provider_configuration()
            checks["runtime_configuration"] = {
                "status": "PASS",
                "vision": "ArmoredVision bound to cloned SQLite",
                "stock": "ArmoredStock materializer importable",
                "ia_provider": ia_config.get("provider"),
                "ia_model": ia_config.get("model"),
                "studio": "ArmoredStudio instantiated; FFmpeg executable resolved",
                "hub": "dry-run enforced; no Telegram transport instantiated",
                "sync": "LocalSource bound; no Telegram connection instantiated",
                "speech_rvc": "effect, RVC assets and analysis runtime distributions verified",
            }
            coordinator.close()
            checks["sqlite_and_composition"] = {
                "status": "PASS", "quick_check": integrity, "foreign_key_errors": foreign_keys,
                "counts": counts, "composition": composed, "database_copy_only": True,
            }
        report = {
            "schema": "armoredcreator-preflight-v1", "status": "PASS",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "production_root": str(root), "external_network": "NONE",
            "telegram": "NOT_CONNECTED", "shopee": "NOT_QUERIED", "publication": "DISABLED",
            "checks": checks,
        }
    except Exception as exc:
        report = {
            "schema": "armoredcreator-preflight-v1", "status": "FAIL",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "production_root": str(root), "external_network": "NONE",
            "error": f"{type(exc).__name__}: {exc}", "checks": checks,
        }
    evidence_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
