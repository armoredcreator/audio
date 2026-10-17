from __future__ import annotations

"""Create a non-destructive E2E manifest for audio routes that must not use RVC.

The source manifest and Speech SQLite database are read-only inputs. One existing
item is selected for each available class that must route to EFFECT_ONLY:
MUSIC_ONLY, NO_AUDIO, NOISE, NO_USABLE_NARRATION, and FOREIGN_SPEECH.
Previously verified PT_BR_NARRATION_WITH_MUSIC cases are intentionally excluded.
Every selected item keeps its original identity and media path while receiving a
new content_id and batch_id so the runner cannot overwrite previous outputs.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re

from .studio_bridge import BridgeError, export_audio_directive


ROUTE_CLASSES = (
    "MUSIC_ONLY",
    "NO_AUDIO",
    "NOISE",
    "NO_USABLE_NARRATION",
    "FOREIGN_SPEECH",
)


def _safe_token(value: object, *, limit: int = 36) -> str:
    token = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value)).strip("._-")
    return (token or "item")[-limit:]


def _ensure_inside(path: Path, root: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} precisa estar dentro de {root}: {resolved}") from exc
    return resolved


def prepare_route_validation_manifest(
    *,
    root: Path,
    source_manifest: Path,
    speech_database: Path,
    output_manifest: Path,
    stamp: str | None = None,
    requested_classes: tuple[str, ...] = ROUTE_CLASSES,
) -> tuple[list[dict], list[str], dict[str, int]]:
    """Select available no-RVC classes using the read-only Speech bridge."""
    root = Path(root).expanduser().resolve(strict=True)
    source_manifest = _ensure_inside(Path(source_manifest), root, "Manifesto-fonte")
    speech_database = _ensure_inside(Path(speech_database), root, "SQLite Speech")
    output_manifest = _ensure_inside(Path(output_manifest), root, "Manifesto de saída")

    if not source_manifest.is_file():
        raise FileNotFoundError(f"Manifesto-fonte não encontrado: {source_manifest}")
    if not speech_database.is_file():
        raise FileNotFoundError(f"SQLite Speech não encontrado: {speech_database}")
    if output_manifest.exists():
        raise FileExistsError(
            f"Destino já existe; nenhum arquivo foi alterado: {output_manifest}"
        )
    if not requested_classes or len(set(requested_classes)) != len(requested_classes):
        raise ValueError("As classes pedidas precisam ser não vazias e sem duplicatas.")
    unsupported = set(requested_classes) - set(ROUTE_CLASSES)
    if unsupported:
        raise ValueError(f"Classes fora da lista segura sem RVC: {sorted(unsupported)}")

    try:
        source = json.loads(source_manifest.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Manifesto-fonte inválido: {exc}") from exc

    if not isinstance(source, dict) or not isinstance(source.get("items"), list):
        raise ValueError("Manifesto-fonte precisa ser um objeto com lista 'items'.")
    if source.get("schema_version") != 1 or source.get("approved") is not True:
        raise ValueError("Manifesto-fonte precisa ser schema_version=1 e approved=true.")
    if source.get("phase") != "ARMORED_SPEECH" or source.get("phase_status") != "COMPLETE":
        raise ValueError("Manifesto-fonte precisa declarar Speech COMPLETE.")

    selected: dict[str, dict] = {}
    source_class_counts: dict[str, int] = {}
    diagnostics: list[str] = []

    for raw_item in source["items"]:
        if not isinstance(raw_item, dict):
            diagnostics.append("ITEM_SKIPPED=manifest-item-not-object")
            continue
        source_id = str(raw_item.get("source_id") or "").strip()
        item_id = str(raw_item.get("telegram_message_id") or "").strip()
        if not source_id or not item_id:
            diagnostics.append("ITEM_SKIPPED=missing-source-or-message-id")
            continue
        try:
            directive = export_audio_directive(
                speech_database,
                source_id=source_id,
                item_id=item_id,
                require_files=True,
            )
        except BridgeError as exc:
            diagnostics.append(f"ITEM_SKIPPED={source_id}/{item_id}: {exc}")
            continue
        audio_class = str(directive.audio_class)
        source_class_counts[audio_class] = source_class_counts.get(audio_class, 0) + 1
        if audio_class in requested_classes and audio_class not in selected:
            selected[audio_class] = dict(raw_item)

    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,48}", stamp):
        raise ValueError("stamp deve conter apenas letras, números, ponto, hífen ou underscore.")

    selected_items: list[dict] = []
    for audio_class in requested_classes:
        item = selected.get(audio_class)
        if item is None:
            continue
        source_id = str(item["source_id"])
        item_id = str(item["telegram_message_id"])
        prepared = dict(item)
        prepared["content_id"] = (
            f"{_safe_token(source_id, limit=48)}_routecheck_{stamp}_"
            f"{_safe_token(item_id, limit=24)}"
        )
        selected_items.append(prepared)

    if not selected_items:
        available = ", ".join(sorted(source_class_counts)) or "(nenhuma decisão READY acessível)"
        raise RuntimeError(
            "Nenhuma classe sem RVC foi encontrada no manifesto-fonte. "
            f"Classes presentes: {available}. SQLite e manifesto não foram alterados."
        )

    target = dict(source)
    target["batch_id"] = f"audio-route-audit-{stamp}"
    target["expected_item_count"] = len(selected_items)
    target["items"] = selected_items
    output_manifest.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation prevents accidental overwrites, even if another process
    # creates the destination between the earlier check and this write.
    with output_manifest.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(target, stream, ensure_ascii=False, indent=2)
        stream.write("\n")

    missing = [audio_class for audio_class in requested_classes if audio_class not in selected]
    return selected_items, missing, source_class_counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--speech-database", type=Path, required=True)
    parser.add_argument("--output-manifest", type=Path, required=True)
    parser.add_argument("--stamp", default=None)
    args = parser.parse_args()

    try:
        selected, missing, counts = prepare_route_validation_manifest(
            root=args.root,
            source_manifest=args.source_manifest,
            speech_database=args.speech_database,
            output_manifest=args.output_manifest,
            stamp=args.stamp,
        )
    except (OSError, ValueError, RuntimeError, BridgeError) as exc:
        print(f"ROUTE_MANIFEST_ERROR={exc}")
        return 2

    print("ROUTE_VALIDATION_MANIFEST_CREATED=True")
    print(f"ITEMS={len(selected)}")
    print(f"MANIFEST={args.output_manifest.resolve()}")
    print("SOURCE_AUDIO_CLASS_COUNTS=" + json.dumps(counts, ensure_ascii=False, sort_keys=True))
    for item in selected:
        print(
            "SELECTED_CLASS="
            + str(next(
                cls for cls in ROUTE_CLASSES
                if str(item.get("telegram_message_id")) == str(item.get("telegram_message_id"))
            ))
            if False else
            f"SELECTED_ITEM={item['telegram_message_id']} SOURCE={item['source_id']} "
            f"CONTENT_ID={item['content_id']}"
        )
    print("MISSING_CLASSES=" + (",".join(missing) if missing else "none"))
    print("SQLITE_MUTATED=False")
    print("SOURCE_MANIFEST_MUTATED=False")
    print("PREVIOUS_OUTPUTS_OVERWRITTEN=False")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
