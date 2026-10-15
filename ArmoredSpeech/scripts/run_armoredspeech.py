from __future__ import annotations

import argparse
import json
from pathlib import Path

from ArmoredSpeech.batch import SpeechItem, run_bulk


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Executa o lote experimental ArmoredSpeech por fonte, com registro SQLite."
    )
    parser.add_argument("--items", type=Path, required=True,
                        help="JSON array: source_order, source_id, item_id, video_path")
    parser.add_argument("--database", type=Path, required=True,
                        help="SQLite isolado para resultados do laboratório")
    parser.add_argument("--decisions", type=Path,
                        help="Manifesto opcional de decisões revisadas; se omitido, usa classificador automático")
    parser.add_argument("--model", default="UVR-MDX-NET-Voc_FT.onnx",
                        help="Modelo UVR/MDX-Net para separar narração com música")
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()

    rows = json.loads(args.items.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        parser.error("--items precisa conter um array JSON")
    items = [
        SpeechItem(
            source_order=int(row.get("source_order", 0)),
            source_id=str(row["source_id"]),
            item_id=str(row["item_id"]),
            video_path=Path(row["video_path"]),
        )
        for row in rows
    ]

    if args.decisions:
        from ArmoredSpeech.manifest_processor import ManifestProcessor
        processor = ManifestProcessor(
            args.decisions, model=args.model, timeout_seconds=args.timeout
        )
        print("Modo: manifesto revisado (não é classificação automática)")
    else:
        try:
            from ArmoredSpeech.model_processor import ModelProcessor
        except ImportError as exc:
            parser.error(
                "Dependências do classificador ausentes. Instale "
                "ArmoredSpeech/requirements-analysis.txt no venv do laboratório. "
                f"Detalhe: {exc}"
            )
        processor = ModelProcessor(
            separation_model=args.model, timeout_seconds=args.timeout
        )
        print("Modo: classificador CPU (Whisper + Silero VAD + AudioSet/AST)")

    counts = run_bulk(items, database=args.database, processor=processor)
    print("ArmoredSpeech — resumo do lote")
    print(f"Áudios preparados para o Studio: {counts['prepared']}")
    print(f"Elegíveis para RVC: {counts['rvc_eligible']}")
    print(f"Preparados, mas bloqueados para RVC: {counts['blocked']}")
    print(f"Falhas: {counts['failed']}")
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
