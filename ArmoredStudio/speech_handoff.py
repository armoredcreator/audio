from __future__ import annotations

"""Studio-owned structural validation for the Speech v3 audio-render handoff."""

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SpeechHandoffError(RuntimeError):
    """Speech decision or artifact cannot safely be consumed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
        return True
    except (OSError, ValueError):
        return False


@dataclass(frozen=True, slots=True)
class ValidatedAudioPackage:
    """Validated Speech-owned audio recipe for the single final FFmpeg render."""
    source_id: str
    item_id: str
    original_path: Path
    voice_track_path: Path | None
    voice_sha256: str | None
    effect_track_path: Path
    effect_sha256: str
    audio_class: str
    language: str
    route: str
    rvc_eligible: bool
    original_sha256: str
    video_duration_seconds: float
    visual_plan_sha256: str
    audio_timeline: dict[str, Any]
    audio_inputs: tuple[dict[str, Any], ...]
    audio_filter_graph: str
    audio_filter_graph_sha256: str


def _validate_audio_track(raw, *, workspace, label, track_hash_cache=None):
    if not isinstance(raw, dict):
        raise SpeechHandoffError(f"{label} metadata missing or invalid")
    try:
        path = Path(raw["path"]).resolve(strict=True)
        expected = raw["sha256"]
        rate = int(raw["sample_rate"])
        channels = int(raw["channels"])
        duration = float(raw["duration_seconds"])
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        raise SpeechHandoffError(f"{label} metadata or path unavailable") from exc
    if not path.is_file() or not _is_within(path, workspace):
        raise SpeechHandoffError(f"{label} must be a file inside the lab workspace")
    if path.stat().st_size <= 44 or raw.get("format") != "wav-pcm-s16le":
        raise SpeechHandoffError(f"{label} format or size is invalid")
    if rate != 44100 or channels != 2 or len(str(expected)) != 64 or duration <= 0:
        raise SpeechHandoffError(f"{label} technical metadata is invalid")
    try:
        with wave.open(str(path), "rb") as stream:
            actual_rate, actual_channels = stream.getframerate(), stream.getnchannels()
            frames, width = stream.getnframes(), stream.getsampwidth()
            if frames < 1 or width != 2 or not stream.readframes(1):
                raise SpeechHandoffError(f"{label} WAV is unreadable")
            actual_duration = frames / actual_rate
    except (wave.Error, EOFError, OSError, ZeroDivisionError) as exc:
        raise SpeechHandoffError(f"{label} WAV is unreadable") from exc
    if (actual_rate, actual_channels) != (rate, channels) or abs(actual_duration-duration) > .02:
        raise SpeechHandoffError(f"{label} technical metadata mismatch")
    stat = path.stat()
    key = (str(path), int(stat.st_dev), int(stat.st_ino), int(stat.st_size), int(stat.st_mtime_ns))
    digest = track_hash_cache.get(key) if track_hash_cache is not None else None
    if digest is None:
        digest = sha256_file(path)
        if track_hash_cache is not None:
            track_hash_cache[key] = digest
    if digest.lower() != str(expected).lower():
        raise SpeechHandoffError(f"{label} SHA-256 mismatch")
    return path, digest, raw


def _valid_hex_digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False


def validate_audio_package(
    payload: dict[str, Any],
    *,
    workspace_root: Path,
    expected_source_id: str,
    expected_item_id: str,
    expected_original_path: Path | None = None,
    track_hash_cache: dict[tuple[str, int, int, int, int], str] | None = None,
) -> ValidatedAudioPackage:
    """Validate Speech's decision, prepared inputs, and audio graph recipe.

    Studio does not classify audio or choose the route. It validates the
    producer's explicit route and artifacts before using the supplied audio
    graph in the final FFmpeg render.
    """
    if not isinstance(payload, dict) or payload.get("contract_version") != 3:
        raise SpeechHandoffError("Studio requires ArmoredSpeech audio-render contract v3")
    if payload.get("status") != "READY":
        raise SpeechHandoffError("Speech audio package is not READY")
    if (
        str(payload.get("source_id")) != expected_source_id
        or str(payload.get("item_id")) != expected_item_id
    ):
        raise SpeechHandoffError("Speech package identity does not match Studio item")

    try:
        workspace = Path(workspace_root).resolve(strict=True)
        original = Path(payload["original_path"]).resolve(strict=True)
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise SpeechHandoffError("Original media unavailable in Speech package") from exc
    if not original.is_file() or not _is_within(original, workspace):
        raise SpeechHandoffError("Original must be a file inside the lab workspace")
    if expected_original_path is not None and original != Path(expected_original_path).resolve(strict=True):
        raise SpeechHandoffError("Original path does not match Studio item")

    before = payload.get("original_sha256_before")
    after = payload.get("original_sha256_after")
    actual_original_hash = sha256_file(original)
    if (
        not isinstance(before, str)
        or not isinstance(after, str)
        or before.lower() != after.lower()
        or before.lower() != actual_original_hash
    ):
        raise SpeechHandoffError("Speech original SHA-256 evidence is missing or inconsistent")

    audio_class = str(payload.get("audio_class") or "")
    supported_classes = {
        "PT_BR_NARRATION", "PT_BR_NARRATION_WITH_MUSIC", "MUSIC_ONLY",
        "NO_AUDIO", "NOISE", "NO_USABLE_NARRATION", "FOREIGN_SPEECH",
    }
    if audio_class not in supported_classes:
        raise SpeechHandoffError("Speech audio class is missing or unsupported")
    language = str(payload.get("language") or "").strip().lower().replace("_", "-")
    route = payload.get("route")
    rvc_eligible = payload.get("rvc_eligible") is True
    model = payload.get("rvc_model")

    voice_path = None
    voice_hash = None
    if route == "RVC_MELODY":
        safe_rvc = (
            audio_class == "PT_BR_NARRATION"
            and language in {"pt", "pt-br"}
            and payload.get("speech_detected") is True
            and payload.get("music_detected") is False
            and rvc_eligible
            and str(model).strip().lower() == "melody"
            and _valid_hex_digest(payload.get("rvc_model_sha256"))
            and (
                payload.get("rvc_index_sha256") is None
                or _valid_hex_digest(payload.get("rvc_index_sha256"))
            )
        )
        if not safe_rvc:
            raise SpeechHandoffError("Speech RVC_MELODY handoff is unsafe or inconsistent")
        voice_path, voice_hash, _ = _validate_audio_track(
            payload.get("voice_track"),
            workspace=workspace,
            label="Speech RVC voice track",
            track_hash_cache=track_hash_cache,
        )
    elif route == "EFFECT_ONLY":
        if (
            rvc_eligible
            or model is not None
            or payload.get("rvc_model_sha256") is not None
            or payload.get("rvc_index_sha256") is not None
            or payload.get("voice_track") is not None
        ):
            raise SpeechHandoffError("Speech EFFECT_ONLY handoff conflicts with RVC metadata")
    else:
        raise SpeechHandoffError("Speech audio route is unsupported")

    effect_path, effect_hash, effect_data = _validate_audio_track(
        payload.get("effect_track"),
        workspace=workspace,
        label="Speech project effect track",
        track_hash_cache=track_hash_cache,
    )
    if effect_data.get("gain_policy") != "loudnorm-I-12-LRA7-TP-1-then-volume-0.8":
        raise SpeechHandoffError("Speech effect gain policy is missing or incompatible")
    if not _valid_hex_digest(effect_data.get("source_sha256")):
        raise SpeechHandoffError("Speech effect source SHA-256 is missing or invalid")

    try:
        video_duration_seconds = float(payload.get("prepared_video_duration_seconds"))
    except (TypeError, ValueError) as exc:
        raise SpeechHandoffError("Speech prepared video duration is invalid") from exc
    if not video_duration_seconds > 0:
        raise SpeechHandoffError("Speech prepared video duration must be positive")

    # Validate the declarative FFmpeg input plan produced by Speech. Studio's
    # renderer only materializes these allowlisted input types; it never decides
    # whether the first audio input should be voice or generated silence.
    raw_inputs = payload.get("audio_inputs")
    if not isinstance(raw_inputs, list) or len(raw_inputs) != 2:
        raise SpeechHandoffError("Speech renderer must declare exactly two audio inputs")
    first_input, effect_input = raw_inputs
    normalized_inputs: list[dict[str, Any]] = []

    if route == "RVC_MELODY":
        if not isinstance(first_input, dict) or first_input.get("kind") != "file" or first_input.get("role") != "voice":
            raise SpeechHandoffError("RVC route must declare its Speech-prepared voice input")
        try:
            declared_voice_path = Path(first_input["path"]).resolve(strict=True)
        except (KeyError, TypeError, OSError, RuntimeError) as exc:
            raise SpeechHandoffError("Speech voice input path is unavailable") from exc
        if (
            declared_voice_path != voice_path
            or first_input.get("sha256") != voice_hash
        ):
            raise SpeechHandoffError("Declared voice input differs from validated Speech voice track")
        normalized_inputs.append({
            "kind": "file", "role": "voice", "path": str(voice_path),
            "sha256": voice_hash,
        })
    else:
        if (
            not isinstance(first_input, dict)
            or first_input.get("kind") != "lavfi"
            or first_input.get("role") != "silence"
            or first_input.get("source") != "anullsrc=r=44100:cl=stereo"
        ):
            raise SpeechHandoffError("EFFECT_ONLY route must declare the approved silence input")
        try:
            silence_duration = float(first_input.get("duration_seconds"))
        except (TypeError, ValueError) as exc:
            raise SpeechHandoffError("Speech silence input duration is invalid") from exc
        if not (silence_duration > 0) or abs(silence_duration - video_duration_seconds) > 0.02:
            raise SpeechHandoffError("Speech silence input duration does not match the timeline")
        normalized_inputs.append({
            "kind": "lavfi", "role": "silence",
            "source": "anullsrc=r=44100:cl=stereo",
            "duration_seconds": round(silence_duration, 6),
        })

    if (
        not isinstance(effect_input, dict)
        or effect_input.get("kind") != "loop_file"
        or effect_input.get("role") != "effect"
    ):
        raise SpeechHandoffError("Speech must declare the looping project-effect input")
    try:
        declared_effect_path = Path(effect_input["path"]).resolve(strict=True)
    except (KeyError, TypeError, OSError, RuntimeError) as exc:
        raise SpeechHandoffError("Speech effect input path is unavailable") from exc
    if declared_effect_path != effect_path or effect_input.get("sha256") != effect_hash:
        raise SpeechHandoffError("Declared effect input differs from validated Speech effect track")
    normalized_inputs.append({
        "kind": "loop_file", "role": "effect", "path": str(effect_path),
        "sha256": effect_hash,
    })

    timeline = payload.get("audio_timeline")
    if not isinstance(timeline, dict):
        raise SpeechHandoffError("Speech audio timeline recipe missing")
    plan_hash = timeline.get("visual_plan_sha256")
    graph = timeline.get("filter_graph")
    graph_hash = timeline.get("filter_graph_sha256")
    if not _valid_hex_digest(plan_hash):
        raise SpeechHandoffError("Visual plan SHA-256 is missing or invalid")
    if (
        not isinstance(graph, str)
        or not graph.strip()
        or len(graph) > 10000
        or not _valid_hex_digest(graph_hash)
        or hashlib.sha256(graph.encode("utf-8")).hexdigest() != graph_hash.lower()
    ):
        raise SpeechHandoffError("Speech audio filter graph is missing or invalid")
    try:
        audio_start = float(timeline.get("audio_start_seconds"))
        video_start = float(timeline.get("video_start_seconds"))
        main_duration = float(timeline.get("main_duration_seconds"))
        intro_enabled = timeline.get("intro_enabled")
        intro_duration = float(timeline.get("intro_duration_seconds"))
        prepared_video_duration = float(payload.get("prepared_video_duration_seconds"))
    except (TypeError, ValueError) as exc:
        raise SpeechHandoffError("Speech audio/video timeline metadata is invalid") from exc
    if (
        audio_start < 0
        or video_start < 0
        or main_duration <= 0
        or prepared_video_duration <= 0
        or not isinstance(intro_enabled, bool)
        or abs(main_duration - prepared_video_duration) > 0.02
    ):
        raise SpeechHandoffError("Speech audio/video timeline fields are invalid or inconsistent")
    position = timeline.get("intro_position")
    output_label = timeline.get("output_label")
    if intro_enabled:
        if position not in {"inicio", "final"} or abs(intro_duration - 2.0) > 0.01:
            raise SpeechHandoffError("Speech intro timeline policy is invalid")
        if output_label != "[final_a]" or "[intro_a]" not in graph or "concat=n=2:v=0:a=1[final_a]" not in graph:
            raise SpeechHandoffError("Speech intro graph is inconsistent with its timeline metadata")
    elif position is not None or abs(intro_duration) > 0.01 or output_label != "[main_a]":
        raise SpeechHandoffError("Speech no-intro timeline metadata is inconsistent")
    if "[1:a]" not in graph or "[2:a]" not in graph:
        raise SpeechHandoffError("Speech audio graph does not use the declared renderer input contract")
    if any(token in graph for token in ("[0:v]", "[3:v]", "movie=", "amovie=")):
        raise SpeechHandoffError("Speech audio graph contains a visual or external-media input")
    if timeline.get("composition_owner") != "ArmoredSpeech":
        raise SpeechHandoffError("Audio timeline policy must be produced by ArmoredSpeech")
    if timeline.get("effect_gain_policy") != effect_data.get("gain_policy"):
        raise SpeechHandoffError("Audio graph effect policy differs from the prepared effect track")

    return ValidatedAudioPackage(
        source_id=expected_source_id,
        item_id=expected_item_id,
        original_path=original,
        voice_track_path=voice_path,
        voice_sha256=voice_hash,
        effect_track_path=effect_path,
        effect_sha256=effect_hash,
        audio_class=audio_class,
        language=language,
        route=str(route),
        rvc_eligible=rvc_eligible,
        original_sha256=actual_original_hash,
        video_duration_seconds=video_duration_seconds,
        visual_plan_sha256=plan_hash.lower(),
        audio_timeline=dict(timeline),
        audio_inputs=tuple(normalized_inputs),
        audio_filter_graph=graph,
        audio_filter_graph_sha256=graph_hash.lower(),
    )
