from __future__ import annotations

"""Explicit lab-only adapter that hands a persisted READY Speech result to Studio.

The caller must provide the Speech-owned registry path; this module never guesses
or opens a Coordinator/production database.
"""

from pathlib import Path
from typing import Protocol

from .studio_bridge import export_audio_directive


class StudioProcessor(Protocol):
    def process(self, item, *, speech_payload: dict | None = None): ...


def process_item_with_speech(
    studio: StudioProcessor,
    item,
    *,
    speech_database: Path,
):
    """Load one READY Speech decision and pass it to Studio for validation.

    Identity uses the canonical source_id + Telegram message ID. Missing,
    non-READY or incompatible Speech results fail closed in the bridge; invalid
    paths, WAV bytes and hashes fail closed in Studio's handoff validator.
    """
    source_id = str(item.source_id)
    item_id = str(item.telegram_message_id)
    directive = export_audio_directive(
        Path(speech_database),
        source_id=source_id,
        item_id=item_id,
        require_files=True,
    )
    return studio.process(item, speech_payload=directive.to_payload())



def process_items_with_speech(
    studio: StudioProcessor,
    items,
    *,
    speech_database: Path,
):
    """Preflight a complete approved Speech batch before invoking Studio.

    This is the lab-only phase boundary: every supplied item must have a READY,
    file-valid Speech directive before the first Studio call is made. It does
    not discover items, mutate the Speech registry, or infer that an arbitrary
    subset is the entire historical collection; the caller must supply the
    complete, approved batch only after Speech reports its phase complete.
    """
    prepared = []
    for item in items:
        source_id = str(item.source_id)
        item_id = str(item.telegram_message_id)
        directive = export_audio_directive(
            Path(speech_database),
            source_id=source_id,
            item_id=item_id,
            require_files=True,
        )
        prepared.append((item, directive.to_payload()))

    # Deliberately separate preflight from execution: a missing/blocked item
    # must not allow earlier items in the same batch to enter Studio.
    return [
        studio.process(item, speech_payload=payload)
        for item, payload in prepared
    ]


def prepare_audio_packages_for_items(
    items,
    *,
    speech_database: Path,
    workspace_root: Path,
    package_root: Path,
    effect_source_path: Path,
) -> list[tuple[object, dict]]:
    """Preflight the entire v1 registry, then create every Speech v2 package.

    This function deliberately does not call Studio. Its caller must validate
    every returned v2 payload before making the first Studio process call.
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

    return prepared
