from __future__ import annotations

"""Create a complete, fail-closed Speech-owned audio package for one item.

This module is used only by the isolated lab's whole-batch runner. It does not
publish, mutate the original video, or invoke ArmoredStudio.
"""

import hashlib
import json
import os
import shutil
import subprocess
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class AudioPackageError(RuntimeError):
    """Audio package could not be prepared or validated safely."""


@dataclass(frozen=True, slots=True)
class WaveMetadata:
    sample_rate: int
    channels: int
    frames: int
    duration_seconds: float


def sha256_file(path: Path) -> str:
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


def _tool(name: str, env_name: str) -> str:
    configured = os.getenv(env_name, "").strip()
    candidate = configured or shutil.which(name)
    if not candidate:
        raise AudioPackageError(f"{name.upper()}_NOT_FOUND")
    path = Path(candidate).expanduser()
    if path.is_absolute() and not path.is_file():
        raise AudioPackageError(f"{name.upper()}_NOT_FOUND: {path}")
    return str(path)


def _run(command: list[str], label: str) -> None:
    try:
        result = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=int(os.getenv("ARMORED_SPEECH_AUDIO_TIMEOUT", "7200")),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioPackageError(f"{label}: {exc}") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "").strip()[-1800:]
        raise AudioPackageError(f"{label} (exit={result.returncode}): {detail}")


def _wave_metadata(path: Path) -> WaveMetadata:
    try:
        with wave.open(str(path), "rb") as stream:
            channels = stream.getnchannels()
            rate = stream.getframerate()
            frames = stream.getnframes()
            width = stream.getsampwidth()
            if channels < 1 or rate < 1 or frames < 1 or width != 2:
                raise AudioPackageError(f"Invalid WAV stream: {path}")
            if not stream.readframes(1):
                raise AudioPackageError(f"Unreadable WAV data: {path}")
            return WaveMetadata(
                sample_rate=rate,
                channels=channels,
                frames=frames,
                duration_seconds=frames / rate,
            )
    except (wave.Error, EOFError, OSError) as exc:
        raise AudioPackageError(f"Expected a readable PCM WAV: {path}") from exc


def _file_stat_fingerprint(path: Path) -> dict[str, int]:
    stat = Path(path).stat()
    return {
        "size": int(stat.st_size),
        "mtime_ns": int(stat.st_mtime_ns),
        "inode": int(getattr(stat, "st_ino", 0)),
    }


def _stat_matches(path: Path, expected: dict[str, Any], prefix: str) -> bool:
    actual = _file_stat_fingerprint(path)
    return all(
        actual[field] == expected.get(f"{prefix}_{field}")
        for field in ("size", "mtime_ns", "inode")
    )


def _media_duration(path: Path, ffprobe: str) -> float:
    command = [
        ffprobe, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    try:
        result = subprocess.run(
            command, check=False, capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=int(os.getenv("ARMORED_SPEECH_AUDIO_TIMEOUT", "7200")),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioPackageError(f"FFPROBE_DURATION_FAILED: {exc}") from exc
    if result.returncode:
        raise AudioPackageError(
            "FFPROBE_DURATION_FAILED: " + (result.stderr or "").strip()[-1200:]
        )
    try:
        duration = float(result.stdout.strip())
    except (TypeError, ValueError) as exc:
        raise AudioPackageError("Invalid video duration from ffprobe") from exc
    if not (duration > 0.0):
        raise AudioPackageError("Original video duration must be positive")
    return duration


def _validate_v1_directive(
    payload: dict[str, Any],
    *,
    root: Path,
    item,
    expected_original_sha256: str | None = None,
) -> tuple[Path, Path, str, bool]:
    if not isinstance(payload, dict) or payload.get("contract_version") != 1:
        raise AudioPackageError("Expected validated Speech handoff v1 directive")
    if payload.get("status") not in (None, "READY"):
        raise AudioPackageError("Speech directive is not READY")
    if (
        str(payload.get("source_id")) != str(item.source_id)
        or str(payload.get("item_id")) != str(item.telegram_message_id)
    ):
        raise AudioPackageError("Speech directive identity does not match batch item")
    try:
        original = Path(payload["original_path"]).resolve(strict=True)
        source_wav = Path(payload["audio_artifact_path"]).resolve(strict=True)
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise AudioPackageError("Speech original/WAV path is unavailable") from exc
    expected_original = Path(item.original_path).resolve(strict=True)
    if original != expected_original or not original.is_file():
        raise AudioPackageError("Speech original does not match the canonical batch original")
    if not _inside(original, root) or not _inside(source_wav, root):
        raise AudioPackageError("Speech original and WAV must remain inside the lab root")
    if not source_wav.is_file() or source_wav.stat().st_size <= 44:
        raise AudioPackageError("Speech reference WAV is missing or empty")
    declared_before = payload.get("original_sha256_before")
    declared_after = payload.get("original_sha256_after")
    if (
        not isinstance(declared_before, str)
        or not isinstance(declared_after, str)
        or declared_before.lower() != declared_after.lower()
    ):
        raise AudioPackageError("Original SHA-256 evidence is missing or inconsistent")
    if expected_original_sha256 is None:
        actual = sha256_file(original)
    else:
        actual = str(expected_original_sha256)
    if declared_before.lower() != actual.lower():
        raise AudioPackageError("Original SHA-256 evidence is missing or inconsistent")
    _wave_metadata(source_wav)
    audio_class = str(payload.get("audio_class") or "")
    supported = {
        "PT_BR_NARRATION", "PT_BR_NARRATION_WITH_MUSIC", "MUSIC_ONLY",
        "NO_AUDIO", "NOISE", "NO_USABLE_NARRATION", "FOREIGN_SPEECH",
    }
    if audio_class not in supported:
        raise AudioPackageError(f"Unsupported audio class: {audio_class!r}")
    language = str(payload.get("language") or "").strip().lower().replace("_", "-")
    declared_rvc = payload.get("rvc_eligible") is True
    rvc_allowed = (
        audio_class == "PT_BR_NARRATION"
        and language in {"pt", "pt-br"}
        and payload.get("speech_detected") is True
        and payload.get("music_detected") is False
        and declared_rvc
    )
    if declared_rvc and not rvc_allowed:
        raise AudioPackageError("Speech declared unsafe RVC eligibility")
    return original, source_wav, actual, rvc_allowed


def preflight_audio_package(
    payload: dict[str, Any],
    item,
    *,
    workspace_root: Path,
    effect_source_path: Path,
    voice: str = "melody",
    effect_source_sha256: str | None = None,
    rvc_asset_fingerprint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate every input and required RVC asset before spending CPU on a batch.

    A whole-batch caller may pass a previously fingerprinted Melody model/index,
    preventing a large model/index file from being re-hashed for every item.
    """

    root = Path(workspace_root).resolve(strict=True)
    effect_source = Path(effect_source_path).expanduser().resolve(strict=True)
    if not effect_source.is_file() or effect_source.stat().st_size <= 44:
        raise AudioPackageError("Project sound-effect asset is missing or empty")
    if not _inside(effect_source, root):
        raise AudioPackageError("Project sound-effect asset must be inside the lab root")

    original, source_wav, original_hash, rvc_allowed = _validate_v1_directive(
        payload, root=root, item=item
    )
    ffmpeg = _tool("ffmpeg", "ARMORED_FFMPEG")
    ffprobe = _tool("ffprobe", "ARMORED_FFPROBE")
    video_duration = _media_duration(original, ffprobe)
    effect_source_hash = effect_source_sha256 or sha256_file(effect_source)
    if len(effect_source_hash) != 64:
        raise AudioPackageError("Effect source SHA-256 is invalid")

    model_sha256 = None
    index_sha256 = None
    model_path = None
    index_path = None
    rvc_python_path = None
    model_stat: dict[str, int] = {}
    index_stat: dict[str, int] = {}
    if rvc_allowed:
        if voice.strip().lower() != "melody":
            raise AudioPackageError("Only the validated Melody model is approved for this route")
        if rvc_asset_fingerprint is None:
            from .rvc_engine import localizar_modelo, resolver_rvc_python

            rvc_python_path = str(resolver_rvc_python().resolve(strict=True))
            model, index = localizar_modelo("melody")
            model_path = str(model.resolve(strict=True))
            model_sha256 = sha256_file(model)
            model_stat = _file_stat_fingerprint(model)
            if index is not None:
                index_path = str(index.resolve(strict=True))
                index_sha256 = sha256_file(index)
                index_stat = _file_stat_fingerprint(index)
        else:
            # This fingerprint was created by the first eligible item in the
            # same whole-batch preflight; don't hash the same large model/index
            # repeatedly for every historical video.
            from .rvc_engine import MODELS_DIR, resolver_rvc_python

            model_path = str(Path(rvc_asset_fingerprint["rvc_model_path"]).resolve(strict=True))
            model_sha256 = str(rvc_asset_fingerprint["rvc_model_sha256"])
            index_path = rvc_asset_fingerprint.get("rvc_index_path")
            index_sha256 = rvc_asset_fingerprint.get("rvc_index_sha256")
            rvc_python_path = str(Path(resolver_rvc_python()).resolve(strict=True))
            if rvc_python_path != rvc_asset_fingerprint.get("rvc_python_path"):
                raise AudioPackageError("RVC Python environment changed during batch preflight")
            if not _stat_matches(Path(model_path), rvc_asset_fingerprint, "rvc_model"):
                raise AudioPackageError("Melody model file changed during batch preflight")
            if index_path is not None and not _stat_matches(
                Path(index_path), rvc_asset_fingerprint, "rvc_index"
            ):
                raise AudioPackageError("Melody index changed during batch preflight")
            model_stat = _file_stat_fingerprint(Path(model_path))
            if index_path is not None:
                index_stat = _file_stat_fingerprint(Path(index_path))
            try:
                Path(model_path).relative_to(Path(MODELS_DIR).resolve(strict=True))
            except ValueError as exc:
                raise AudioPackageError("Cached Melody model path escaped the configured models directory") from exc
            if index_path is not None:
                index_path = str(Path(index_path).resolve(strict=True))
                try:
                    Path(index_path).relative_to(Path(MODELS_DIR).resolve(strict=True))
                except ValueError as exc:
                    raise AudioPackageError("Cached Melody index path escaped the configured models directory") from exc

    return {
        "original_sha256": original_hash,
        "source_wav_sha256": sha256_file(source_wav),
        "effect_source_path": str(effect_source),
        "effect_source_sha256": effect_source_hash,
        "rvc_allowed": rvc_allowed,
        "video_duration_seconds": video_duration,
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "rvc_python_path": rvc_python_path if rvc_allowed else None,
        "rvc_model_path": model_path,
        "rvc_model_sha256": model_sha256,
        "rvc_model_size": model_stat.get("size"),
        "rvc_model_mtime_ns": model_stat.get("mtime_ns"),
        "rvc_model_inode": model_stat.get("inode"),
        "rvc_index_path": index_path,
        "rvc_index_sha256": index_sha256,
        "rvc_index_size": index_stat.get("size"),
        "rvc_index_mtime_ns": index_stat.get("mtime_ns"),
        "rvc_index_inode": index_stat.get("inode"),
    }


def prepare_effect_track(
    effect_source_path: Path,
    *,
    workspace_root: Path,
    output_path: Path,
    preflight: dict[str, Any],
) -> dict[str, Any]:
    """Normalize/gain the project effect once for the whole batch, never per item."""
    root = Path(workspace_root).resolve(strict=True)
    source = Path(effect_source_path).expanduser().resolve(strict=True)
    output = Path(output_path).expanduser().resolve()
    if not _inside(source, root) or not source.is_file() or source.stat().st_size <= 44:
        raise AudioPackageError("Project sound-effect asset must be a valid file inside the lab root")
    try:
        output.relative_to(root)
    except ValueError as exc:
        raise AudioPackageError("Prepared effect track must remain inside the lab root") from exc
    output.parent.mkdir(parents=True, exist_ok=True)
    if not _inside(output.parent, root):
        raise AudioPackageError("Prepared effect output parent escaped the lab root")
    if output.exists():
        raise AudioPackageError(f"Prepared effect output already exists: {output}")

    expected_source_hash = preflight.get("effect_source_sha256")
    if not isinstance(expected_source_hash, str) or len(expected_source_hash) != 64:
        raise AudioPackageError("Effect source hash missing from batch preflight")
    actual_source_hash = sha256_file(source)
    if actual_source_hash.lower() != expected_source_hash.lower():
        raise AudioPackageError("Effect source changed after batch preflight")

    stage = output.with_name(f".{output.stem}.tmp-{uuid.uuid4().hex}{output.suffix or '.wav'}")
    ffmpeg = str(preflight["ffmpeg"])
    try:
        _run([
            ffmpeg, "-nostdin", "-y", "-i", str(source), "-vn",
            "-af", "loudnorm=I=-12:LRA=7:TP=-1,volume=0.8",
            "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(stage),
        ], "EFFECT_TRACK_PREPARATION_FAILED")
        metadata = _wave_metadata(stage)
        if metadata.sample_rate != 44100 or metadata.channels != 2:
            raise AudioPackageError("Effect track must be canonical PCM WAV 44100 Hz stereo")
        if sha256_file(source) != expected_source_hash:
            raise AudioPackageError("Project sound-effect asset changed during preparation")
        artifact_hash = sha256_file(stage)
        stage.replace(output)
        return {
            "path": str(output),
            "sha256": artifact_hash,
            "source_sha256": expected_source_hash,
            "format": "wav-pcm-s16le",
            "sample_rate": metadata.sample_rate,
            "channels": metadata.channels,
            "duration_seconds": round(metadata.duration_seconds, 6),
            "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
            "_prepared_and_verified": True,
        }
    except Exception:
        stage.unlink(missing_ok=True)
        raise


def prepare_audio_package(
    payload: dict[str, Any],
    item,
    *,
    workspace_root: Path,
    effect_source_path: Path,
    output_dir: Path,
    voice: str = "melody",
    preflight: dict[str, Any] | None = None,
    prepared_effect_track: dict[str, Any] | None = None,
    visual_plan: dict[str, Any] | None = None,
    audio_start_seconds: float = 0.0,
    intro: bool = True,
    intro_position: str = "final",
) -> dict[str, Any]:
    """Prepare validated audio tracks and a Speech-owned filter graph.

    RVC, effect gain and audio timeline rules are owned here. The prepared voice
    and effect tracks plus the exact Speech-generated audio graph are passed to
    the one final audiovisual render. This function does not run a separate
    audio-mix render.
    """
    from .timeline_mixer import build_audio_filter_graph

    root = Path(workspace_root).resolve(strict=True)
    final_dir = Path(output_dir).expanduser().resolve()
    try:
        final_dir.relative_to(root)
    except ValueError as exc:
        raise AudioPackageError("Package output must be inside the lab root") from exc
    if final_dir == root:
        raise AudioPackageError("Package output cannot replace the lab root")
    final_dir.parent.mkdir(parents=True, exist_ok=True)
    if not _inside(final_dir.parent, root):
        raise AudioPackageError("Package output parent must remain inside the lab root")
    if final_dir.exists():
        raise AudioPackageError(f"Package output already exists: {final_dir}")
    effect_source = Path(effect_source_path).resolve(strict=True)
    if preflight is None:
        preflight = preflight_audio_package(
            payload, item, workspace_root=root, effect_source_path=effect_source, voice=voice
        )
    if str(effect_source) != preflight.get("effect_source_path"):
        raise AudioPackageError("Effect source differs from the batch preflight")
    if prepared_effect_track is None:
        # Standalone path: validate locally. The whole-batch path checks the
        # source once before and once after creating its shared effect artifact.
        if sha256_file(effect_source) != preflight.get("effect_source_sha256"):
            raise AudioPackageError("Effect asset changed after batch preflight")
    else:
        if prepared_effect_track.get("_prepared_and_verified") is not True:
            raise AudioPackageError("Shared effect track has not passed preparation validation")
        if prepared_effect_track.get("source_sha256") != preflight.get("effect_source_sha256"):
            raise AudioPackageError("Shared effect track was prepared from another asset")

    original, source_wav, original_hash, rvc_allowed = _validate_v1_directive(
        payload,
        root=root,
        item=item,
        expected_original_sha256=str(preflight.get("original_sha256") or ""),
    )
    if original_hash != preflight.get("original_sha256"):
        raise AudioPackageError("Original differs from the batch preflight")
    if sha256_file(source_wav) != preflight.get("source_wav_sha256"):
        raise AudioPackageError("Speech reference WAV changed after batch preflight")
    if rvc_allowed != bool(preflight.get("rvc_allowed")):
        raise AudioPackageError("RVC route differs from the batch preflight")
    ffmpeg = str(preflight["ffmpeg"])
    ffprobe = str(preflight["ffprobe"])
    video_duration = float(preflight["video_duration_seconds"])
    effect_source_hash = str(preflight["effect_source_sha256"])

    final_dir.parent.mkdir(parents=True, exist_ok=True)
    stage = final_dir.with_name(f".{final_dir.name}.tmp-{uuid.uuid4().hex}")
    stage.mkdir(parents=False, exist_ok=False)
    try:
        voice_output = stage / "voice.wav"
        effect_output = stage / "effect.wav"
        route = "RVC_MELODY" if rvc_allowed else "EFFECT_ONLY"
        rvc_model = voice if rvc_allowed else None
        rvc_input = stage / "rvc_input.wav"
        rvc_raw = stage / "rvc_raw.wav"

        if rvc_allowed:
            # Preserve the pre-RVC conditioning used by the existing Studio path.
            _run([
                ffmpeg, "-nostdin", "-y", "-i", str(source_wav), "-vn",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(rvc_input),
            ], "RVC_INPUT_PREPARATION_FAILED")
            from .rvc_engine import converter_voz, localizar_modelo

            converter_voz(rvc_input, rvc_raw, voice, item_id=item.content_id)
            model_after, index_after = localizar_modelo(voice)
            if str(model_after.resolve(strict=True)) != preflight.get("rvc_model_path"):
                raise AudioPackageError("RVC model path changed during conversion")
            if preflight.get("rvc_model_size") is not None:
                if not _stat_matches(model_after, preflight, "rvc_model"):
                    raise AudioPackageError("RVC model file changed during conversion")
            elif sha256_file(model_after) != preflight.get("rvc_model_sha256"):
                raise AudioPackageError("RVC model hash changed during conversion")

            if preflight.get("rvc_index_path") is not None:
                if index_after is None or str(index_after.resolve(strict=True)) != preflight.get("rvc_index_path"):
                    raise AudioPackageError("RVC index path changed during conversion")
                if preflight.get("rvc_index_size") is not None:
                    if not _stat_matches(index_after, preflight, "rvc_index"):
                        raise AudioPackageError("RVC index changed during conversion")
                elif sha256_file(index_after) != preflight.get("rvc_index_sha256"):
                    raise AudioPackageError("RVC index hash changed during conversion")
            elif index_after is not None:
                raise AudioPackageError("RVC index appeared/changed after batch preflight")
            _run([
                ffmpeg, "-nostdin", "-y", "-i", str(rvc_raw), "-vn",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(voice_output),
            ], "RVC_OUTPUT_NORMALIZATION_FAILED")
        # EFFECT_ONLY deliberately has no voice file. Studio will give FFmpeg a
        # generated silence input at index 1; the source video's original audio
        # is never mapped or sent into the mix.

        # Speech owns effect preparation and gain. The complete-batch path
        # supplies one validated shared effect track, avoiding redundant FFmpeg
        # loudnorm passes for every item. Standalone calls keep a local fallback.
        if prepared_effect_track is not None:
            effect_output = Path(prepared_effect_track.get("path", "")).resolve(strict=True)
            if not effect_output.is_file() or not _inside(effect_output, root):
                raise AudioPackageError("Shared effect track is missing or outside the lab root")
            if effect_output.stat().st_size <= 44:
                raise AudioPackageError("Shared effect track is empty")
            prepared_hash = prepared_effect_track.get("sha256")
            if not isinstance(prepared_hash, str) or len(prepared_hash) != 64:
                raise AudioPackageError("Shared effect track hash is invalid")
            if (
                prepared_effect_track.get("format") != "wav-pcm-s16le"
                or prepared_effect_track.get("sample_rate") != 44100
                or prepared_effect_track.get("channels") != 2
                or prepared_effect_track.get("gain_policy") != "loudnorm-I-12-LRA7-TP-1-then-volume-0.8"
            ):
                raise AudioPackageError("Shared effect track metadata/policy is incompatible")
        else:
            _run([
                ffmpeg, "-nostdin", "-y", "-i", str(effect_source), "-vn",
                "-af", "loudnorm=I=-12:LRA=7:TP=-1,volume=0.8",
                "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le", str(effect_output),
            ], "EFFECT_TRACK_PREPARATION_FAILED")

        if sha256_file(original) != original_hash:
            raise AudioPackageError("Original SHA-256 changed while preparing audio")
        if prepared_effect_track is None and sha256_file(effect_source) != effect_source_hash:
            raise AudioPackageError("Project sound-effect asset changed during preparation")

        effect_meta = _wave_metadata(effect_output)
        if prepared_effect_track is not None and abs(
            effect_meta.duration_seconds - float(prepared_effect_track.get("duration_seconds", 0))
        ) > 0.02:
            raise AudioPackageError("Shared effect track duration metadata mismatch")
        if effect_meta.sample_rate != 44100 or effect_meta.channels != 2:
            raise AudioPackageError("Effect track must be canonical PCM WAV 44100 Hz stereo")

        voice_meta = _wave_metadata(voice_output) if rvc_allowed else None
        if voice_meta is not None and (
            voice_meta.sample_rate != 44100 or voice_meta.channels != 2
        ):
            raise AudioPackageError("Voice track must be canonical PCM WAV 44100 Hz stereo")

        video_start_seconds = float((visual_plan or {}).get("video_start", 0) or 0)
        main_duration_seconds = max(0.1, video_duration - video_start_seconds)
        audio_timeline = build_audio_filter_graph(
            visual_plan=visual_plan,
            audio_start_seconds=audio_start_seconds,
            main_duration_seconds=main_duration_seconds,
            intro=intro,
            intro_position=intro_position,
        )

        # EFFECT_ONLY still supplies a deterministic silent voice input. Speech
        # owns this artifact; Studio does not synthesize silence or infer routes.
        if not rvc_allowed:
            silence_frames = int(round(main_duration_seconds * 44100))
            with wave.open(str(voice_output), "wb") as silent:
                silent.setnchannels(2)
                silent.setsampwidth(2)
                silent.setframerate(44100)
                block = bytes(44100 * 2 * 2)
                remaining = silence_frames
                while remaining:
                    frames = min(remaining, 44100)
                    silent.writeframesraw(block[:frames * 4])
                    remaining -= frames
                silent.writeframes(b"")

        voice_meta = _wave_metadata(voice_output)
        if voice_meta.sample_rate != 44100 or voice_meta.channels != 2:
            raise AudioPackageError("Voice/silence track must be canonical PCM WAV 44100 Hz stereo")
        voice_track = {
            "path": str(final_dir / "voice.wav"),
            "sha256": sha256_file(voice_output),
            "format": "wav-pcm-s16le",
            "sample_rate": voice_meta.sample_rate,
            "channels": voice_meta.channels,
            "duration_seconds": round(voice_meta.duration_seconds, 6),
            "role": "voice" if rvc_allowed else "silence",
        }

        if prepared_effect_track is not None:
            effect_track_path = str(effect_output)
            effect_hash = str(prepared_effect_track["sha256"])
            effect_source_hash = str(prepared_effect_track["source_sha256"])
            effect_gain_policy = str(prepared_effect_track["gain_policy"])
            effect_duration = float(prepared_effect_track["duration_seconds"])
        else:
            effect_track_path = str(final_dir / "effect.wav")
            effect_hash = sha256_file(effect_output)
            effect_source_hash = str(preflight["effect_source_sha256"])
            effect_gain_policy = "loudnorm-I-12-LRA7-TP-1-then-volume-0.8"
            effect_duration = effect_meta.duration_seconds

        effect_track = {
            "path": effect_track_path,
            "sha256": effect_hash,
            "source_sha256": effect_source_hash,
            "format": "wav-pcm-s16le",
            "sample_rate": effect_meta.sample_rate,
            "channels": effect_meta.channels,
            "duration_seconds": round(effect_duration, 6),
            "gain_policy": effect_gain_policy,
        }
        audio_timeline_metadata = {
            key: value for key, value in audio_timeline.items()
            if key not in {"filter_graph", "output_label"}
        }
        audio_timeline_metadata["final_audio_duration_seconds"] = (
            main_duration_seconds + (2.0 if intro else 0.0)
        )

        package = {
            "contract_version": 5,
            "status": "READY",
            "source_id": str(item.source_id),
            "item_id": str(item.telegram_message_id),
            "content_id": str(item.content_id),
            "original_path": str(original),
            "original_sha256_before": original_hash,
            "original_sha256_after": original_hash,
            # Decision fields are evidence/debug metadata only. Studio consumes
            # the single final track and never dispatches on class/route.
            "audio_class": payload["audio_class"],
            "language": payload.get("language"),
            "speech_detected": payload.get("speech_detected") is True,
            "music_detected": payload.get("music_detected"),
            "confidence": payload.get("confidence"),
            "route": route,
            "rvc_eligible": rvc_allowed,
            "rvc_model": rvc_model,
            "rvc_model_sha256": preflight.get("rvc_model_sha256") if rvc_allowed else None,
            "rvc_index_sha256": preflight.get("rvc_index_sha256") if rvc_allowed else None,
            "voice_track": voice_track,
            "effect_track": effect_track,
            "audio_filter_graph": audio_timeline["filter_graph"],
            "audio_output_label": audio_timeline["output_label"],
            "audio_timeline": audio_timeline_metadata,
            "prepared_video_duration_seconds": round(main_duration_seconds, 6),
            "package_id": final_dir.name,
            "effect_source_sha256": effect_source_hash,
            "effect_gain_policy": effect_gain_policy,
        }

        # Keep only the two validated audio inputs that the one final audiovisual
        # FFmpeg graph consumes. Remove RVC intermediates before publishing.
        rvc_input.unlink(missing_ok=True)
        rvc_raw.unlink(missing_ok=True)
        (stage / "package.json").write_text(
            json.dumps(package, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        stage.replace(final_dir)
        return package
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
