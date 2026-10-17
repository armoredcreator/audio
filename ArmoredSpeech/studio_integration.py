from __future__ import annotations

"""Prepare Speech-owned audio and final AAC/M4A artifacts for a complete batch.

This module does not call Studio or render video. It reads Speech-owned SQLite
decisions, prepares/validates audio internally, and returns only final audio
artifacts for the Studio visual renderer.
"""

from pathlib import Path

from .studio_bridge import export_audio_directive


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
    """Prepare one completed AAC/M4A soundtrack per item for the whole batch.

    The caller supplies a read-only visual plan so Speech can calculate temporal
    relationships. Speech owns audio decisions, RVC, effect preparation/gain,
    synchronization, mix/fades and final audio encoding. This function does not
    call Studio or render video. The v4 payload exports one final validated audio
    file; Studio receives no component tracks or audio filter graph.
    """
    import hashlib
    import re

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

    # Read/validate all persisted decisions before spending CPU on any RVC run.
    directives = []
    for item in items:
        directive = export_audio_directive(
            Path(speech_database),
            source_id=str(item.source_id),
            item_id=str(item.telegram_message_id),
            require_files=True,
        )
        directives.append((item, directive.to_payload()))

    def safe_component(value: object) -> str:
        original = str(value)
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", original).strip("._-") or "item"
        return f"{slug[:64]}-{hashlib.sha256(original.encode('utf-8')).hexdigest()[:10]}"

    # Audio owns onset/timing analysis. Studio's input plan is visual-only;
    # Speech may provide the video cut derived from the audio onset when a visual
    # banner is present, while its audio cut remains exclusively in the audio recipe.
    from .timeline_mixer import detect_voice_onset_seconds

    effective_visual_plans: dict[tuple[str, str], dict] = {}
    audio_starts: dict[tuple[str, str], float] = {}
    for item, directive_payload in directives:
        identity = (str(item.source_id), str(item.telegram_message_id))
        plan = dict((visual_plans or {}).get(identity, {}) or {})
        detectors = plan.get("detectores") or {}
        banner = detectors.get("banner") if isinstance(detectors, dict) else None
        banner_detected = bool(
            isinstance(banner, dict)
            and (banner.get("banner_detectado") or banner.get("detectado"))
        )
        if banner_detected:
            source_wav = Path(str(directive_payload.get("audio_artifact_path") or ""))
            voice_onset = detect_voice_onset_seconds(source_wav)
            plan["video_start"] = max(1.95, voice_onset)
            audio_starts[identity] = max(0.0, voice_onset - 0.03)
        else:
            plan["video_start"] = float(plan.get("video_start", 0.0) or 0.0)
            audio_starts[identity] = 0.0
        effective_visual_plans[identity] = plan

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
            / safe_component(item.source_id)
            / safe_component(item.telegram_message_id)
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
            visual_plan=effective_visual_plans[
                (str(item.source_id), str(item.telegram_message_id))
            ],
            audio_start_seconds=audio_starts[
                (str(item.source_id), str(item.telegram_message_id))
            ],
            intro=intro,
            intro_position=intro_position,
        )
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

    # The batch has completed all final tracks successfully. The shared
    # normalized effect was an internal Speech intermediate and is not part of
    # the handoff; remove it so validation storage contains only final tracks.
    shared_path = Path(str(shared_effect_track["path"]))
    shared_path.unlink(missing_ok=True)
    try:
        shared_path.parent.rmdir()
    except OSError:
        pass

    return prepared
