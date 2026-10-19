from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .speech_handoff import validate_audio_package, SpeechHandoffError
from .analysis.video import obter_informacoes_video
from .analysis.blackbar import analisar_bordas_video
from .analysis.banner_analyzer import analisar_banner
from .analysis.veo_detector import detect_fast as detectar_veo
from .analysis.gemini_detector import detect_fast as detectar_gemini
from .analysis.export_planner import criar_plano_exportacao
from .analysis.export_plan_validator import validar_plano_exportacao


@dataclass(frozen=True)
class AnalysisResult:
    """Visual-only analysis used to build the export plan."""
    video: dict[str, Any]
    blackbar: dict[str, Any]
    banner: dict[str, Any]
    veo: dict[str, Any]
    gemini: dict[str, Any]
    plan: dict[str, Any]
    validation: dict[str, Any]


def _visual_plan_digest(plan: dict[str, Any] | None) -> str:
    """Hash a visual plan while encoding path-like detector metadata as paths."""
    raw = json.dumps(
        plan or {},
        default=os.fspath,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class AnalysisEngine:
    """Studio owns visual analysis only. Audio classification belongs to Speech."""

    def analyze(self, source: Path) -> AnalysisResult:
        video = obter_informacoes_video(source)
        if not video.get("sucesso"):
            raise RuntimeError(video.get("erro", "video-invalido"))

        blackbar = analisar_bordas_video(source)
        banner = analisar_banner(source)
        veo = detectar_veo(source)
        gemini = detectar_gemini(source)

        detector_results = veo.get("detectors", {})
        veo1 = detector_results.get("veo1", veo)
        veo2 = detector_results.get("veo2", {})
        logging.getLogger(__name__).info(
            "[STUDIO][ANALYSIS][VEO] file=%s found=%s selected=%s "
            "veo1_found=%s veo1_score=%.3f veo2_found=%s veo2_score=%.3f",
            Path(source).name,
            bool(veo.get("found")),
            veo.get("detector", "veo1" if veo.get("found") else "none"),
            bool(veo1.get("found")),
            float(veo1.get("best_score", 0.0) or 0.0),
            bool(veo2.get("found")),
            float(veo2.get("best_score", 0.0) or 0.0),
        )
        logging.getLogger(__name__).info(
            "[STUDIO][ANALYSIS][VISUAL] file=%s blackbars=%s gemini=%s score=%s",
            Path(source).name,
            {
                side: int(blackbar.get(side, 0) or 0)
                for side in ("esquerda", "direita", "topo", "baixo")
            },
            bool(gemini.get("gemini")),
            gemini.get("score"),
        )

        plan = criar_plano_exportacao(video, blackbar, banner, veo, gemini)
        validation = validar_plano_exportacao(plan)
        if not validation.get("valido"):
            raise RuntimeError(
                "Plano de processamento inválido: "
                + "; ".join(validation.get("erros", []))
            )
        return AnalysisResult(video, blackbar, banner, veo, gemini, plan, validation)


class UnifiedStudio:
    """Visual analysis plus final video rendering; all audio work is Speech-owned."""

    def __init__(self, root: Path, storage, *, analysis: AnalysisEngine | None = None):
        self.root = Path(root).resolve()
        self.storage = storage
        self.analysis = analysis or AnalysisEngine()
        self._audio_track_hash_cache: dict[tuple[str, int, int, int, int], str] = {}

    def _test_copy(self, source: Path, output: Path) -> None:
        if os.getenv("ARMORED_STUDIO_ALLOW_COPY") != "1":
            raise RuntimeError("Modo de cópia de teste não habilitado")
        shutil.copy2(source, output)

    def _source_for_item(self, item) -> tuple[Path, Path]:
        original = Path(item.original_path).resolve()
        if not original.is_file():
            raise FileNotFoundError(original)
        working = item.working_path
        source = Path(working).resolve() if working and Path(working).is_file() else original
        if not source.is_file():
            raise FileNotFoundError(source)
        return original, source

    def analyze_visual(self, item) -> AnalysisResult:
        """Run read-only visual analysis only after Speech has completed the batch."""
        _, source = self._source_for_item(item)
        return self.analysis.analyze(source)

    def process(
        self,
        item,
        speech_payload: dict[str, Any] | None = None,
        visual_analysis: AnalysisResult | None = None,
    ):
        original, source = self._source_for_item(item)
        original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
        destination = Path(
            self.storage.result(
                item.content_id, item.affiliate_url, item.affiliate_name,
                source_id=item.source_id,
            )
        ).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise FileExistsError(f"Studio never overwrites an existing output: {destination}")
        output = destination.with_name(
            f".{destination.stem}.tmp-{uuid.uuid4().hex}{destination.suffix or '.mp4'}"
        )

        # Explicit deterministic copy mode is test-only and does not claim that
        # any real audio processing or rendering occurred.
        if os.getenv("ARMORED_STUDIO_FORCE_COPY") == "1":
            self._test_copy(source, output)
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError("Studio produziu uma saída de teste inválida")
            if hashlib.sha256(original.read_bytes()).hexdigest() != original_hash:
                raise RuntimeError("O original foi alterado durante o processamento")
            output.replace(destination)
            return destination, {"mode": "test-copy", "plan": None}

        if not isinstance(speech_payload, dict) or speech_payload.get("contract_version") != 5:
            raise RuntimeError(
                "ArmoredSpeech audio contract v5 é obrigatório; "
                "os caminhos legados de áudio do Studio estão desabilitados."
            )
        try:
            package = validate_audio_package(
                speech_payload,
                workspace_root=self.root,
                expected_source_id=str(item.source_id),
                expected_item_id=str(item.telegram_message_id),
                expected_original_path=original,
                track_hash_cache=self._audio_track_hash_cache,
            )
        except SpeechHandoffError as exc:
            raise RuntimeError(f"ArmoredSpeech handoff inválido: {exc}") from exc

        analysis = visual_analysis or self.analysis.analyze(source)
        try:
            declared_video_start = float(package.audio_timeline["video_start_seconds"])
            visual_video_start = float(analysis.plan.get("video_start", 0) or 0)
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError("Plano temporal visual/Speech inválido; render bloqueado.") from exc
        if (
            not math.isfinite(visual_video_start)
            or abs(visual_video_start - declared_video_start) > 1e-6
        ):
            raise RuntimeError(
                "O plano visual não usa a variante temporal selecionada pelo Speech; render bloqueado."
            )
        timing_digest = _visual_plan_digest({"video_start": declared_video_start})
        if timing_digest != package.visual_plan_sha256:
            raise RuntimeError(
                "O digest temporal do pacote Speech não confere; render bloqueado."
            )

        intro = bool(package.audio_timeline.get("intro_enabled"))
        banner = Path(os.getenv(
            "ARMORED_STUDIO_BANNER", str(self.root / "ArmoredStudio" / "assets" / "banner.png")
        ))
        if intro and not banner.is_file():
            raise FileNotFoundError(
                "Banner visual do Studio ausente. Configure ARMORED_STUDIO_BANNER."
            )

        ffmpeg = os.getenv("ARMORED_FFMPEG", "ffmpeg")
        if not shutil.which(ffmpeg):
            raise RuntimeError("FFmpeg não encontrado")
        from .processing.visual_finalizer import render_video_with_speech_tracks

        render_video_with_speech_tracks(
            source,
            package.voice_track_path,
            package.effect_track_path,
            banner,
            output,
            audio_filter_graph=package.audio_filter_graph,
            audio_output_label=package.audio_output_label,
            audio_render_options=package.audio_render_options,
            position=str(package.audio_timeline.get("intro_position") or "final"),
            intro=intro,
            plan=analysis.plan,
        )

        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError("Studio produziu uma saída inválida")
        if hashlib.sha256(original.read_bytes()).hexdigest() != original_hash:
            raise RuntimeError("O original foi alterado durante o processamento")
        output.replace(destination)
        return destination, analysis
