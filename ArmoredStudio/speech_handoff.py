from __future__ import annotations

"""Fail-closed validation for the ArmoredSpeech v5 audio render contract.

Studio validates artifact identity, PCM format and hashes. It does not classify
audio, choose RVC routes, tune gain, alter the audio graph, or prepare silence.
Speech provides all audio tracks and the exact audio filter subgraph.
"""

import hashlib
import math
import re
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class SpeechHandoffError(RuntimeError):
    """A Speech audio input or its contract is invalid."""


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


def _valid_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
        return True
    except ValueError:
        return False


@dataclass(frozen=True, slots=True)
class ValidatedAudioPackage:
    """Validated inputs and graph supplied entirely by ArmoredSpeech."""
    source_id: str
    item_id: str
    original_path: Path
    voice_track_path: Path
    voice_sha256: str
    voice_duration_seconds: float
    effect_track_path: Path
    effect_sha256: str
    audio_filter_graph: str
    audio_output_label: str
    audio_render_options: dict[str, Any]
    audio_timeline: dict[str, Any]
    original_sha256: str
    visual_plan_sha256: str


def _validate_pcm_track(
    raw: object,
    *,
    workspace: Path,
    label: str,
    hash_cache: dict[tuple[str, int, int, int, int], str] | None,
) -> tuple[Path, str, float]:
    if not isinstance(raw, dict):
        raise SpeechHandoffError(f"{label} metadata is missing")
    try:
        path = Path(raw["path"]).resolve(strict=True)
        declared_hash = raw["sha256"]
        sample_rate = int(raw["sample_rate"])
        channels = int(raw["channels"])
        duration = float(raw["duration_seconds"])
        size_bytes = int(raw["size_bytes"])
    except (KeyError, TypeError, ValueError, OSError, RuntimeError) as exc:
        raise SpeechHandoffError(f"{label} metadata/path is invalid") from exc
    if not path.is_file() or not _is_within(path, workspace):
        raise SpeechHandoffError(f"{label} must be a file inside the lab workspace")
    if path.suffix.lower() != ".wav" or raw.get("format") != "wav-pcm-s16le":
        raise SpeechHandoffError(f"{label} must be a canonical PCM WAV")
    stat = path.stat()
    if (
        stat.st_size <= 44
        or size_bytes != stat.st_size
        or sample_rate != 44100
        or channels != 2
        or not _valid_sha256(declared_hash)
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise SpeechHandoffError(f"{label} format/hash/size metadata is invalid")
    try:
        with wave.open(str(path), "rb") as stream:
            actual_rate = stream.getframerate()
            actual_channels = stream.getnchannels()
            actual_width = stream.getsampwidth()
            frames = stream.getnframes()
            actual_duration = frames / actual_rate if actual_rate else 0.0
            if (
                actual_rate != 44100 or actual_channels != 2 or actual_width != 2
                or frames < 1 or not stream.readframes(1)
            ):
                raise SpeechHandoffError(f"{label} is not readable 44.1 kHz stereo PCM")
    except (wave.Error, EOFError, OSError, ZeroDivisionError) as exc:
        raise SpeechHandoffError(f"{label} is not a readable PCM WAV") from exc
    if abs(actual_duration - duration) > 0.03:
        raise SpeechHandoffError(f"{label} duration metadata mismatch")

    cache_key = (
        str(path), int(stat.st_dev), int(stat.st_ino),
        int(stat.st_size), int(stat.st_mtime_ns),
    )
    digest = hash_cache.get(cache_key) if hash_cache is not None else None
    if digest is None:
        digest = sha256_file(path)
        if hash_cache is not None:
            hash_cache[cache_key] = digest
    if digest.lower() != declared_hash.lower():
        raise SpeechHandoffError(f"{label} SHA-256 mismatch")
    return path, digest, actual_duration


def validate_audio_package(
    payload: dict[str, Any],
    *,
    workspace_root: Path,
    expected_source_id: str,
    expected_item_id: str,
    expected_original_path: Path | None = None,
    track_hash_cache: dict[tuple[str, int, int, int, int], str] | None = None,
) -> ValidatedAudioPackage:
    """Validate v5 tracks and the Speech-owned graph; never make audio decisions."""
    if not isinstance(payload, dict) or payload.get("contract_version") != 5:
        raise SpeechHandoffError("Studio requires ArmoredSpeech audio contract v5")
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
        raise SpeechHandoffError("Original must remain inside the lab workspace")
    if expected_original_path is not None and original != Path(expected_original_path).resolve(strict=True):
        raise SpeechHandoffError("Original path does not match Studio item")

    before, after = payload.get("original_sha256_before"), payload.get("original_sha256_after")
    actual_original_hash = sha256_file(original)
    if (
        not _valid_sha256(before) or not _valid_sha256(after)
        or before.lower() != after.lower() or before.lower() != actual_original_hash
    ):
        raise SpeechHandoffError("Speech original SHA-256 evidence is missing or inconsistent")

    voice_path, voice_hash, voice_duration = _validate_pcm_track(
        payload.get("voice_track"), workspace=workspace, label="Speech voice/silence track",
        hash_cache=track_hash_cache,
    )
    effect_path, effect_hash, _ = _validate_pcm_track(
        payload.get("effect_track"), workspace=workspace, label="Speech project-effect track",
        hash_cache=track_hash_cache,
    )
    effect = payload["effect_track"]
    if (
        not _valid_sha256(effect.get("source_sha256"))
        or effect.get("gain_policy") != "loudnorm-I-12-LRA7-TP-1-then-volume-0.8"
    ):
        raise SpeechHandoffError("Speech effect-source evidence or gain-policy metadata is invalid")

    graph = payload.get("audio_filter_graph")
    output_label = payload.get("audio_output_label")
    if not isinstance(graph, str) or not graph or len(graph) > 10000:
        raise SpeechHandoffError("Speech audio filter graph is missing or oversized")
    # Restrict the handoff graph to the known audio-only filter language. In
    # particular, it cannot inject file-reading filters or arbitrary option text.
    if not re.fullmatch(r"[A-Za-z0-9_\[\];:,.=+\-]+", graph):
        raise SpeechHandoffError("Speech audio filter graph contains unsupported characters")
    allowed_filters = {
        "aresample", "aformat", "atrim", "apad", "asetpts",
        "asplit", "amix", "afade", "concat",
    }
    graph_filters = re.findall(r"(?:^|[;\],])([a-z][a-z0-9_]*)=", graph)
    if not graph_filters or any(name not in allowed_filters for name in graph_filters):
        raise SpeechHandoffError("Speech audio filter graph contains an unsupported filter")
    if "[1:a]" not in graph or "[2:a]" not in graph:
        raise SpeechHandoffError("Speech audio graph must consume the Speech voice and effect inputs")
    if output_label not in {"[main_a]", "[final_a]"} or output_label not in graph:
        raise SpeechHandoffError("Speech audio graph output label is invalid")

    timeline = payload.get("audio_timeline")
    if not isinstance(timeline, dict):
        raise SpeechHandoffError("Speech audio timeline metadata is missing")
    render_options = timeline.get("render_options")
    if not isinstance(render_options, dict):
        raise SpeechHandoffError("Speech final audio render options are missing")
    if (
        render_options.get("codec") != "aac"
        or not isinstance(render_options.get("bitrate"), str)
        or not re.fullmatch(r"[0-9]{2,3}k", render_options["bitrate"])
        or render_options.get("sample_rate") != 44100
        or render_options.get("channels") != 2
        or render_options.get("effect_input_loop") is not True
    ):
        raise SpeechHandoffError("Speech final audio render options are invalid")
    visual_plan_hash = timeline.get("visual_plan_sha256")
    graph_hash = timeline.get("filter_graph_sha256")
    if not _valid_sha256(visual_plan_hash) or not _valid_sha256(graph_hash):
        raise SpeechHandoffError("Speech audio/visual plan graph hashes are missing or invalid")
    if hashlib.sha256(graph.encode("utf-8")).hexdigest() != graph_hash.lower():
        raise SpeechHandoffError("Speech audio filter graph hash mismatch")
    if timeline.get("composition_owner") != "ArmoredSpeech":
        raise SpeechHandoffError("Audio timeline ownership must be ArmoredSpeech")

    try:
        audio_start = float(timeline["audio_start_seconds"])
        video_start = float(timeline["video_start_seconds"])
        main_duration = float(timeline["main_duration_seconds"])
        intro_duration = float(timeline["intro_duration_seconds"])
        final_duration = float(timeline["final_audio_duration_seconds"])
        intro_enabled = timeline["intro_enabled"]
    except (KeyError, TypeError, ValueError) as exc:
        raise SpeechHandoffError("Speech audio timeline metadata is invalid") from exc
    numbers = (audio_start, video_start, main_duration, intro_duration, final_duration)
    if (
        not all(math.isfinite(number) for number in numbers)
        or audio_start < 0 or video_start < 0 or main_duration <= 0
        or not isinstance(intro_enabled, bool)
        or abs(intro_duration - (2.0 if intro_enabled else 0.0)) > 0.01
        or abs(final_duration - (main_duration + intro_duration)) > 0.02
        or voice_duration < max(0.1, min(main_duration, voice_duration))
    ):
        raise SpeechHandoffError("Speech audio timeline values are invalid or inconsistent")
    position = timeline.get("intro_position")
    if (intro_enabled and position not in {"inicio", "final"}) or (not intro_enabled and position is not None):
        raise SpeechHandoffError("Speech intro timeline metadata is inconsistent")
    try:
        prepared_video_duration = float(payload["prepared_video_duration_seconds"])
    except (KeyError, TypeError, ValueError) as exc:
        raise SpeechHandoffError("Speech main video duration is invalid") from exc
    if not math.isfinite(prepared_video_duration) or abs(prepared_video_duration - main_duration) > 0.02:
        raise SpeechHandoffError("Speech main video duration does not match its timeline")

    return ValidatedAudioPackage(
        source_id=expected_source_id,
        item_id=expected_item_id,
        original_path=original,
        voice_track_path=voice_path,
        voice_sha256=voice_hash,
        voice_duration_seconds=voice_duration,
        effect_track_path=effect_path,
        effect_sha256=effect_hash,
        audio_filter_graph=graph,
        audio_output_label=output_label,
        audio_render_options=dict(render_options),
        audio_timeline=dict(timeline),
        original_sha256=actual_original_hash,
        visual_plan_sha256=visual_plan_hash.lower(),
    )
