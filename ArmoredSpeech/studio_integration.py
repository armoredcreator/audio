from __future__ import annotations

"""Prepare Speech-owned audio tracks and contract v5 packages for a complete batch.

This module does not call Studio or render video. It reads Speech-owned SQLite
decisions, prepares/validates audio inputs, and returns tracks plus the audio
filter graph for Studio's single final audiovisual render.
"""

import hashlib
import os
import re
from pathlib import Path

from .studio_bridge import export_audio_directive
from .vocal_separation import (
    VocalSeparationError,
    separate_vocal_stem,
    validate_vocal_separation_receipt,
)




def _safe_component(value: object) -> str:
    original = str(value)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", original).strip("._-") or "item"
    return f"{slug[:64]}-{hashlib.sha256(original.encode('utf-8')).hexdigest()[:10]}"


def prepare_directive_payload(
    item,
    payload: dict,
    *,
    workspace_root: Path,
    package_root: Path,
) -> dict:
    """Promote mixed PT-BR narration only after validating a real vocal stem."""
    root = Path(workspace_root).resolve(strict=True)
    package_root = Path(package_root).expanduser().resolve()
    if payload.get("audio_class") != "PT_BR_NARRATION_WITH_MUSIC":
        return payload

    language = str(payload.get("language") or "").strip().lower().replace("_", "-")
    if (
        payload.get("rvc_candidate_eligible") is not True
        or language not in {"pt", "pt-br"}
        or payload.get("speech_detected") is not True
    ):
        raise VocalSeparationError(
            f"MIXED_NARRATION_NOT_RVC_CANDIDATE: source={item.source_id} "
            f"item={item.telegram_message_id}; refusing silent effect-only fallback"
        )

    original = Path(item.original_path).resolve(strict=True)
    prior_audio = Path(str(payload.get("audio_artifact_path") or "")).resolve(strict=True)
    separation = validate_vocal_separation_receipt(
        original, prior_audio, workspace_root=root
    )
    vocal_path = prior_audio
    if separation is None:
        # Old Speech batches predate receipts. Never trust an unproven sidecar
        # or run RVC on it: re-separate from the immutable MP4 into this run's
        # isolated package namespace.
        vocal_path = (
            package_root / "_separation"
            / _safe_component(item.source_id)
            / _safe_component(item.telegram_message_id)
            / "vocals.wav"
        )
        try:
            vocal_path, separation = separate_vocal_stem(
                original,
                output_wav=vocal_path,
                workspace_root=root,
                model_filename=os.getenv(
                    "ARMORED_SPEECH_SEPARATION_MODEL",
                    "UVR-MDX-NET-Voc_FT.onnx",
                ),
            )
        except VocalSeparationError as exc:
            raise VocalSeparationError(
                f"MIXED_NARRATION_SEPARATION_FAILED for "
                f"{item.source_id}/{item.telegram_message_id}: {exc}"
            ) from exc

    prepared = dict(payload)
    prepared["source_audio_class"] = "PT_BR_NARRATION_WITH_MUSIC"
    prepared["source_audio_artifact_path"] = str(prior_audio)
    prepared["audio_artifact_path"] = str(vocal_path)
    prepared["audio_class"] = "PT_BR_NARRATION"
    prepared["music_detected"] = False
    prepared["speech_detected"] = True
    prepared["rvc_eligible"] = True
    prepared["rvc_candidate_eligible"] = True
    prepared["requires_vocal_separation"] = False
    prepared["vocal_separation"] = {
        **separation,
        "route": "SEPARATED_VOCALS",
        "rvc_authorized_after_validation": True,
    }
    return prepared


def prepare_audio_packages_for_items(
    items,
    *,
    speech_database: Path,
    workspace_root: Path,
    package_root: Path,
    effect_source_path: Path,
    visual_plans: dict[tuple[str, str], dict] | None = None,
    intro: bool = True,
    intro_position: str = "final",
) -> list[tuple[object, dict]]:
    """Prepare the complete Speech v5 batch before any Studio phase starts.

    Speech prepares voice/effect tracks and both temporal variants (embedded
    banner present/absent), including each audio graph. Studio later selects
    the appropriate already-built variant from visual evidence; it does not
    compute audio timing or filters. This function may use FFmpeg for audio
    preparation, but never calls Studio or renders a completed video.
    """
    from .audio_package import (
        preflight_audio_package,
        prepare_audio_package,
        prepare_effect_track,
        sha256_file,
    )

    root = Path(workspace_root).resolve(strict=True)
    package_root = Path(package_root).expanduser().resolve()
    try:
        package_root.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("Audio package root must stay inside the lab root") from exc
    effect_source_path = Path(effect_source_path).expanduser().resolve(strict=True)
    if not items:
        raise RuntimeError("Speech audio package batch cannot be empty")
    try:
        effect_source_path.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("Effect asset must stay inside the lab root") from exc
    effect_source_hash = sha256_file(effect_source_path)

    # Validate all decisions and prove mixed-voice stems before any RVC run.
    directives = []
    for item in items:
        directive = export_audio_directive(
            Path(speech_database),
            source_id=str(item.source_id),
            item_id=str(item.telegram_message_id),
            require_files=True,
        )
        payload = prepare_directive_payload(
            item,
            directive.to_payload(),
            workspace_root=root,
            package_root=package_root,
        )
        directives.append((item, payload))

    # Preflight all source WAVs, original hashes, executable availability, video
    # durations, effect asset, and Melody model/index before running RVC on any item.
    preflights = []
    rvc_asset_fingerprint = None
    for item, directive_payload in directives:
        preflight = preflight_audio_package(
            directive_payload,
            item,
            workspace_root=root,
            effect_source_path=effect_source_path,
            voice="melody",
            effect_source_sha256=effect_source_hash,
            rvc_asset_fingerprint=rvc_asset_fingerprint,
        )
        preflights.append(preflight)
        if preflight.get("rvc_model_path"):
            rvc_asset_fingerprint = {
                "rvc_python_path": preflight["rvc_python_path"],
                "rvc_model_path": preflight["rvc_model_path"],
                "rvc_model_sha256": preflight["rvc_model_sha256"],
                "rvc_model_size": preflight["rvc_model_size"],
                "rvc_model_mtime_ns": preflight["rvc_model_mtime_ns"],
                "rvc_model_inode": preflight["rvc_model_inode"],
                "rvc_index_path": preflight["rvc_index_path"],
                "rvc_index_sha256": preflight["rvc_index_sha256"],
                "rvc_index_size": preflight["rvc_index_size"],
                "rvc_index_mtime_ns": preflight["rvc_index_mtime_ns"],
                "rvc_index_inode": preflight["rvc_index_inode"],
            }

    # Timing is computed only after every directive/input/model preflight passed.
    # No Studio analysis is needed: Speech prepares both possible timelines now.
    from .timeline_mixer import build_audio_filter_graph, detect_voice_onset_seconds

    voice_onsets: dict[tuple[str, str], float] = {}
    for item, directive_payload in directives:
        identity = (str(item.source_id), str(item.telegram_message_id))
        source_wav = Path(str(directive_payload.get("audio_artifact_path") or ""))
        voice_onsets[identity] = detect_voice_onset_seconds(source_wav)

    # The effect is the same project asset for the entire batch. Normalize and
    # apply the approved gain once; every per-item contract refers to this shared,
    # immutable track and the Studio validator caches its SHA-256 by file identity.
    shared_effect_track = prepare_effect_track(
        effect_source_path,
        workspace_root=root,
        output_path=package_root / "_shared" / "effect.wav",
        preflight=preflights[0],
    )

    prepared: list[tuple[object, dict]] = []
    for (item, directive_payload), preflight in zip(directives, preflights, strict=True):
        output_dir = (
            package_root
            / _safe_component(item.source_id)
            / _safe_component(item.telegram_message_id)
        )
        package = prepare_audio_package(
            directive_payload,
            item,
            workspace_root=root,
            effect_source_path=effect_source_path,
            output_dir=output_dir,
            voice="melody",
            preflight=preflight,
            prepared_effect_track=shared_effect_track,
            visual_plan={"video_start": 0.0},
            audio_start_seconds=0.0,
            intro=intro,
            intro_position=intro_position,
        )

        # Both options refer to the same validated tracks, but Speech chooses
        # the exact audio graph, voice trim, video start and durations now.
        # Studio later selects a pre-built option using visual banner evidence.
        video_duration = float(preflight["video_duration_seconds"])
        voice_onset = voice_onsets[
            (str(item.source_id), str(item.telegram_message_id))
        ]
        variants: dict[str, dict] = {}
        variant_specs = (
            ("NO_BANNER", 0.0, 0.0),
            (
                "BANNER_PRESENT",
                max(1.95, voice_onset),
                max(0.0, voice_onset - 0.03),
            ),
        )
        for variant_name, video_start, audio_start in variant_specs:
            main_duration = max(0.1, video_duration - video_start)
            timeline = build_audio_filter_graph(
                visual_plan={"video_start": float(video_start)},
                audio_start_seconds=float(audio_start),
                main_duration_seconds=main_duration,
                intro=intro,
                intro_position=intro_position,
            )
            timeline_metadata = {
                key: value for key, value in timeline.items()
                if key not in {"filter_graph", "output_label"}
            }
            timeline_metadata["final_audio_duration_seconds"] = (
                main_duration + (2.0 if intro else 0.0)
            )
            variants[variant_name] = {
                "timeline_variant": variant_name,
                "audio_filter_graph": timeline["filter_graph"],
                "audio_output_label": timeline["output_label"],
                "audio_timeline": timeline_metadata,
                "prepared_video_duration_seconds": round(main_duration, 6),
            }

        # Keep the plain/no-banner graph at the contract's top level for simple
        # readers. The complete-batch runner requires and validates both variants.
        package.update(variants["NO_BANNER"])
        package["audio_timeline_variants"] = variants
        package_dir = Path(package["voice_track"]["path"]).resolve().parent
        manifest_path = package_dir / "package.json"
        temp_manifest_path = package_dir / "package.json.tmp"
        temp_manifest_path.write_text(
            json.dumps(package, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temp_manifest_path.replace(manifest_path)
        prepared.append((item, package))

    if sha256_file(effect_source_path).lower() != effect_source_hash.lower():
        raise RuntimeError("Project sound-effect asset changed during whole-batch Speech preparation")

    if rvc_asset_fingerprint is not None:
        # The full Melody/index hashes are checked once at the end of the
        # complete Speech batch. Per-item checks above use path/size/mtime/inode
        # so large model files aren't re-read for every video.
        from .rvc_engine import localizar_modelo, resolver_rvc_python

        if str(resolver_rvc_python().resolve(strict=True)) != rvc_asset_fingerprint["rvc_python_path"]:
            raise RuntimeError("RVC Python environment changed during whole-batch Speech preparation")
        model, index = localizar_modelo("melody")
        if (
            str(model.resolve(strict=True)) != rvc_asset_fingerprint["rvc_model_path"]
            or sha256_file(model).lower() != str(rvc_asset_fingerprint["rvc_model_sha256"]).lower()
        ):
            raise RuntimeError("Melody model changed during whole-batch Speech preparation")
        expected_index = rvc_asset_fingerprint.get("rvc_index_path")
        if expected_index is None:
            if index is not None:
                raise RuntimeError("Melody index appeared during whole-batch Speech preparation")
        elif (
            index is None
            or str(index.resolve(strict=True)) != str(expected_index)
            or sha256_file(index).lower() != str(rvc_asset_fingerprint["rvc_index_sha256"]).lower()
        ):
            raise RuntimeError("Melody index changed during whole-batch Speech preparation")

    # The shared normalized effect remains a validated batch input until Studio's
    # single audiovisual FFmpeg render has consumed every Speech audio graph.
    # The runner retains this package root for audit/cleanup after the batch.
    return prepared
