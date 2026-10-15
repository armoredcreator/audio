from __future__ import annotations

import argparse
import json
from pathlib import Path

from armoredspeech.batch import SpeechItem, run_bulk
from armoredspeech.manifest_processor import ManifestProcessor


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Executa o lote experimental ArmoredSpeech por fonte, com registro SQLite."
    )
    parser.add_argument("--items", type=Path, required=True,
                        help="JSON array: source_id, item_id, video_path")
    parser.add_argument("--decisions", type=Path, required=True,
                        help="JSON array de classificações revisadas; não é um classificador automático")
    parser.add_argument("--database", type=Path, required=True,
                        help="SQLite isolado para resultados do laboratório")
    parser.add_argument("--model", default="UVR-MDX-NET-Voc_FT.onnx")
    parser.add_argument("--timeout", type=int, default=7200)
    args = parser.parse_args()

    rows = json.loads(args.items.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        parser.error("--items precisa conter um array JSON")
    items = [
        SpeechItem(
            source_id=str(row["source_id"]),
            item_id=str(row["item_id"]),
            video_path=Path(row["video_path"]),
        )
        for row in rows
    ]
    processor = ManifestProcessor(args.decisions, model=args.model,
                                  timeout_seconds=args.timeout)
    counts = run_bulk(items, database=args.database, processor=processor)
    print("ArmoredSpeech — resumo do lote")
    print(f"Prontos para RVC: {counts['ready']}")
    print(f"Bloqueados para RVC: {counts['blocked']}")
    print(f"Falhas: {counts['failed']}")
    return 0 if counts["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
