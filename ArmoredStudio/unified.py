from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import uuid
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .analysis.video import obter_informacoes_video
from .analysis.audio_intelligence import AudioIntelligence, AudioMode
from .speech_handoff import validate_handoff, SpeechHandoffError
from .analysis.blackbar import analisar_bordas_video
from .analysis.banner_analyzer import analisar_banner
from .analysis.banner import analisar_banner as analisar_corte_banner
from .analysis.veo_detector import detect_fast as detectar_veo
from .analysis.gemini_detector import detect_fast as detectar_gemini
from .analysis.export_planner import criar_plano_exportacao
from .analysis.export_plan_validator import validar_plano_exportacao


def _rvc_language_is_supported(value: object) -> bool:
    """Accept the Portuguese labels emitted by Speech and Audio Intelligence."""
    normalized = str(value or "").strip().lower().replace("_", "-")
    return normalized in {"pt", "pt-br"}


@dataclass(frozen=True)
class AnalysisResult:
    video: dict[str, Any]
    blackbar: dict[str, Any]
    banner: dict[str, Any]
    banner_cut: dict[str, Any]
    veo: dict[str, Any]
    gemini: dict[str, Any]
    plan: dict[str, Any]
    validation: dict[str, Any]
    audio: dict[str, Any] = field(default_factory=dict)


class AnalysisEngine:
    """Mandatory analysis stage. No video mutation happens here."""

    def __init__(self, audio_intelligence: AudioIntelligence | None = None):
        self.audio_intelligence = audio_intelligence or AudioIntelligence(
            os.getenv("ARMORED_FFMPEG", "ffmpeg")
        )

    def analyze(self, source: Path, audio_override: dict[str, Any] | None = None) -> AnalysisResult:
        video = obter_informacoes_video(source)
        if not video.get("sucesso"):
            raise RuntimeError(video.get("erro", "video-invalido"))

        blackbar = analisar_bordas_video(source)
        banner = analisar_banner(source)
        banner_cut = analisar_corte_banner(source) if banner.get("banner_detectado") else {}
        veo = detectar_veo(source)
        gemini = detectar_gemini(source)

        # Keep visual evidence visible in the normal Studio log. Audio routing
        # happens below and may skip only RVC; it never gates these detectors.
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

        plan = criar_plano_exportacao(
            video,
            blackbar,
            banner,
            banner_cut,
            veo,
            gemini,
        )
        validation = validar_plano_exportacao(plan)
        if not validation.get("valido"):
            raise RuntimeError("Plano de processamento inválido: " + "; ".join(validation.get("erros", [])))

        if audio_override is None:
            audio = self.audio_intelligence.analyze(source).as_dict()
        else:
            audio = dict(audio_override)
        return AnalysisResult(video, blackbar, banner, banner_cut, veo, gemini, plan, validation, audio)


class UnifiedStudio:
    """analysis + processing under one canonical Studio contract.

    The database/workspace remains outside this class. This class never creates
    storage/input, storage/output, queue or temp trees.
    """

    def __init__(self, root: Path, storage, *, analysis: AnalysisEngine | None = None):
        self.root = Path(root).resolve()
        self.storage = storage
        self.analysis = analysis or AnalysisEngine()

    def _test_copy(self, source: Path, output: Path) -> None:
        if os.getenv("ARMORED_STUDIO_ALLOW_COPY") != "1":
            raise RuntimeError("Modo de cópia de teste não habilitado")
        shutil.copy2(source, output)

    def process(self, item, speech_payload: dict[str, Any] | None = None):
        original = Path(item.original_path).resolve()
        if not original.is_file():
            raise FileNotFoundError(original)
        original_hash = hashlib.sha256(original.read_bytes()).hexdigest()
        speech_handoff = None
        if speech_payload is None and os.getenv("ARMORED_STUDIO_REQUIRE_SPEECH_HANDOFF") == "1":
            raise RuntimeError("ArmoredSpeech READY handoff obrigatório; processamento bloqueado")
        if speech_payload is not None:
            try:
                speech_handoff = validate_handoff(
                    speech_payload,
                    workspace_root=self.root,
                    expected_source_id=str(item.source_id),
                    expected_item_id=str(item.telegram_message_id),
                    expected_original_path=original,
                )
            except SpeechHandoffError as exc:
                raise RuntimeError(f"ArmoredSpeech handoff inválido: {exc}") from exc

        destination = Path(self.storage.result(item.content_id, item.affiliate_url, item.affiliate_name, source_id=item.source_id)).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        output = destination.with_name(f".{destination.stem}.tmp-{uuid.uuid4().hex}{destination.suffix or '.mp4'}")

        # The immutable ORIGINAL is the canonical Studio input. Do not create
        # a byte-identical WORKING copy merely to satisfy the old contract.
        # A WORKING artifact is used only when a durable intermediate exists.
        working = item.working_path
        source = Path(working) if working and Path(working).is_file() else original
        if not source.is_file():
            raise FileNotFoundError(source)

        # Explicit deterministic test mode preserves the contract tests without
        # pretending that arbitrary bytes are a real video.
        if os.getenv("ARMORED_STUDIO_FORCE_COPY") == "1":
            self._test_copy(source, output)
            if not output.is_file() or output.stat().st_size <= 0:
                raise RuntimeError("Studio produziu uma saída de teste inválida")
            if hashlib.sha256(original.read_bytes()).hexdigest() != original_hash:
                output.unlink(missing_ok=True)
                raise RuntimeError("O original foi alterado durante o processamento")
            output.replace(destination)
            return destination, {"mode": "test-copy", "plan": None}

        if os.getenv("ARMORED_CONSOLE_VERBOSE", "0") == "1":
            analysis = self.analysis.analyze(source, audio_override=self._speech_audio_override(speech_payload, rvc_eligible=speech_handoff.rvc_eligible) if speech_handoff is not None else None)
        else:
            with open(os.devnull, "w", encoding="utf-8") as quiet:
                with contextlib.redirect_stdout(quiet), contextlib.redirect_stderr(quiet):
                    analysis = self.analysis.analyze(source, audio_override=self._speech_audio_override(speech_payload, rvc_eligible=speech_handoff.rvc_eligible) if speech_handoff is not None else None)

        # Real Studio resources are explicit/configurable; never use another
        # checkout or machine-specific path.
        music = Path(os.getenv("ARMORED_STUDIO_MUSIC", str(self.root / "ArmoredStudio" / "assets" / "efeitosonoro.wav")))
        banner = Path(os.getenv("ARMORED_STUDIO_BANNER", str(self.root / "ArmoredStudio" / "assets" / "banner.png")))

        if not music.is_file() or not banner.is_file():
            raise FileNotFoundError(
                "Recursos do Studio ausentes. Configure ARMORED_STUDIO_MUSIC e "
                "ARMORED_STUDIO_BANNER ou instale os assets no projeto."
            )

        ffmpeg = os.getenv("ARMORED_FFMPEG", "ffmpeg")
        if not shutil.which(ffmpeg):
            raise RuntimeError("FFmpeg não encontrado")

        # Audio Intelligence is a mandatory routing stage. The original audio
        # is classified before RVC so music-only and silent videos never enter
        # the voice converter.
        audio_info = analysis.audio
        audio_mode = str(audio_info.get("mode") or AudioMode.MUSIC_ONLY)
        duration = max(0.1, float(analysis.video.get("duracao") or audio_info.get("duration_seconds") or 0.1))
        work_dir = self.root / "storage" / "studio_work" / str(item.source_id) / str(item.content_id)
        work_dir.mkdir(parents=True, exist_ok=True)
        audio_original = work_dir / "audio_original.wav"
        audio_rvc = work_dir / "audio_rvc.wav"

        try:
            language = str(audio_info.get("language") or "unknown").lower()
            try:
                language_confidence = float(audio_info.get("language_confidence") or 0.0)
                # The final RVC gate must never be weaker than the classifier's
                # conservative threshold for promoting MUSIC_ONLY to speech.
                # A lower environment value must not weaken this safety floor.
                language_minimum = max(
                    0.65,
                    float(os.getenv("ARMORED_AUDIO_LANGUAGE_MIN_CONFIDENCE", "0.65")),
                )
            except (TypeError, ValueError):
                language_confidence = 0.0
                language_minimum = 0.65

            # Strict product rule: melody is allowed only for verified
            # Portuguese narration classified as speech without background music.
            # Mixed speech/music must use the project sound effect, never RVC.
            try:
                music_ratio = float(audio_info.get("music_ratio") or 0.0)
                # RVC is unsafe when the classifier reports material music
                # evidence. This is a fail-closed gate: mixed/uncertain audio
                # uses the project's effect instead of feeding music to melody.
                maximum_music_ratio = float(
                    os.getenv("ARMORED_AUDIO_RVC_MAX_MUSIC_RATIO", "0.20")
                )
            except (TypeError, ValueError):
                music_ratio = 1.0
                maximum_music_ratio = 0.20

            rvc_allowed = (
                audio_mode == AudioMode.SPEECH
                and _rvc_language_is_supported(language)
                and language_confidence >= language_minimum
                and music_ratio < maximum_music_ratio
                and (speech_handoff.rvc_eligible if speech_handoff is not None else True)
            )

            if not rvc_allowed:
                logging.getLogger(__name__).info(
                    "[STUDIO][ITEM %s] Audio Intelligence=%s language=%s; "
                    "RVC ignorado e áudio original silenciado",
                    item.content_id,
                    audio_mode,
                    language,
                )
                subprocess.run(
                    [
                        ffmpeg, "-y",
                        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                        "-t", f"{duration:.3f}",
                        "-ac", "2", "-ar", "44100",
                        str(audio_rvc),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                )
            else:
                extract_args = [
                    ffmpeg, "-y", "-i",
                    str(speech_handoff.wav_path if speech_handoff is not None else source),
                    "-vn", "-ac", "2", "-ar", "44100",
                ]
                # Mixed speech+music gets a speech-focused preconditioning pass
                # before RVC. It is intentionally not described as perfect source
                # separation; Studio still controls the final background music.
                if audio_mode == AudioMode.SPEECH_PLUS_MUSIC:
                    extract_args.extend([
                        "-af",
                        "pan=stereo|c0=0.5*c0+0.5*c1|c1=0.5*c0+0.5*c1,highpass=f=100,lowpass=f=5000",
                    ])
                extract_args.append(str(audio_original))
                subprocess.run(
                    extract_args,
                    check=True,
                    capture_output=True,
                    text=True,
                )

                voice = os.getenv("ARMORED_STUDIO_RVC_VOICE", "melody")
                logging.getLogger(__name__).info(
                    "[STUDIO][ITEM %s] Audio Intelligence=%s; RVC iniciando voz=%s",
                    item.content_id,
                    audio_mode,
                    voice,
                )
                from .processing.rvc import converter_voz
                converter_voz(audio_original, audio_rvc, voice, item_id=item.content_id)
                logging.getLogger(__name__).info(
                    "[STUDIO][ITEM %s] RVC concluído",
                    item.content_id,
                )

            from .processing.finalizer import finalizar
            finalizar(
                source,
                audio_rvc,
                music,
                banner,
                output,
                position=os.getenv("ARMORED_STUDIO_INTRO_POSITION", "final"),
                intro=os.getenv("ARMORED_STUDIO_INTRO", "1") != "0",
                plan=analysis.plan,
            )
        finally:
            for artifact in (audio_original, audio_rvc):
                artifact.unlink(missing_ok=True)

        if not output.is_file() or output.stat().st_size <= 0:
            raise RuntimeError("Studio produziu uma saída inválida")
        if hashlib.sha256(original.read_bytes()).hexdigest() != original_hash:
            raise RuntimeError("O original foi alterado durante o processamento")
        output.replace(destination)
        return destination, analysis

    @staticmethod
    def _speech_audio_override(
        payload: dict[str, Any], *, rvc_eligible: bool
    ) -> dict[str, Any]:
        """Map the neutral Speech class without reclassification or weakening its RVC gate."""
        audio_class = payload.get("audio_class")
        language = str(payload.get("language") or "unknown").strip().lower()
        confidence = float(payload.get("confidence") or 0.0)
        mapping = {
            "PT_BR_NARRATION": (AudioMode.SPEECH, 0.0),
            "PT_BR_NARRATION_WITH_MUSIC": (AudioMode.SPEECH_PLUS_MUSIC, 1.0),
            "MUSIC_ONLY": (AudioMode.MUSIC_ONLY, 1.0),
            "NO_AUDIO": (AudioMode.NO_AUDIO, 0.0),
            "NOISE": (AudioMode.MUSIC_ONLY, 1.0),
            "NO_USABLE_NARRATION": (AudioMode.MUSIC_ONLY, 1.0),
            "FOREIGN_SPEECH": (AudioMode.FOREIGN_SPEECH, 0.0),
        }
        if audio_class not in mapping:
            raise RuntimeError("Classe Speech desconhecida; áudio bloqueado")
        mode, music_ratio = mapping[audio_class]
        # Speech's generic classifier confidence is not a language-confidence
        # score. Only its separately validated eligibility bit may open RVC.
        if audio_class == "PT_BR_NARRATION":
            music_ratio = 0.0 if rvc_eligible else 1.0
        return {
            "mode": mode,
            "language": language,
            "language_confidence": 1.0 if rvc_eligible else 0.0,
            "speech_rvc_eligible": bool(rvc_eligible),
            "music_ratio": music_ratio,
            "duration_seconds": 0.0,
            "speech_ratio": 1.0 if audio_class in {"PT_BR_NARRATION", "PT_BR_NARRATION_WITH_MUSIC", "FOREIGN_SPEECH"} else 0.0,
            "confidence": confidence,
            "method": "armoredspeech-handoff-v1",
        }
