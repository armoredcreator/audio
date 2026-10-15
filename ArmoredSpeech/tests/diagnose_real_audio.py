from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import numpy as np
import torch
from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

ROOT = Path(__file__).resolve().parents[2]
FILES = [
    ("291_6VKD4FrQVJ.mp4", "NOISE"),
    ("1046_20uhmSz3VE.mp4", "SPEECH"),
    ("12008_1AyNNNzh3.mp4", "MUSIC_ONLY"),
    ("12025_7ptrS9Gsg2.mp4", "SPEECH_PLUS_MUSIC"),
    ("178482_7AeDXQWFF2.mp4", "NO_AUDIO"),
]
MODEL_NAME = "MIT/ast-finetuned-audioset-10-10-0.4593"
RATE = 16000


def resolve_video(filename: str) -> Path:
    """Resolve a single corpus video in the root or private local storage."""
    explicit_root = os.getenv("ARMORED_AUDIO_VALIDATION_VIDEO_ROOT")
    roots = [Path(explicit_root)] if explicit_root else [ROOT, ROOT / "storage"]
    matches: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        candidate = root / filename
        if candidate.is_file():
            matches.append(candidate.resolve())
        elif root == ROOT / "storage":
            matches.extend(path.resolve() for path in root.rglob(filename) if path.is_file())
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        found = ", ".join(str(path) for path in unique) or "nenhum arquivo"
        raise FileNotFoundError(
            f"Esperado exatamente um {filename}; encontrados {len(unique)}: {found}. "
            "Coloque o corpus na raiz/storage ou defina "
            "ARMORED_AUDIO_VALIDATION_VIDEO_ROOT."
        )
    return unique[0]


def decode(path: Path) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1",
         "-ar", str(RATE), "-f", "f32le", "pipe:1"],
        check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    return np.frombuffer(proc.stdout, dtype="<f4").copy()


def main() -> None:
    print(f"model={MODEL_NAME} torch={torch.__version__}", flush=True)
    extractor = AutoFeatureExtractor.from_pretrained(MODEL_NAME)
    model = AutoModelForAudioClassification.from_pretrained(MODEL_NAME)
    model.eval()
    id_to_label = model.config.id2label

    for filename, expected in FILES:
        path = resolve_video(filename)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        samples = decode(path)
        rms = float(np.sqrt(np.mean(samples * samples))) if samples.size else 0.0
        print(f"\nFILE {filename} expected={expected} bytes={path.stat().st_size} "
              f"sha256={digest} samples={samples.size} seconds={samples.size / RATE:.3f} "
              f"rms={rms:.8f}", flush=True)
        if not samples.size or rms < 0.006:
            print("SIGNAL_CLASS=NO_AUDIO (effective-silence threshold)", flush=True)
            continue

        windows: list[tuple[str, np.ndarray]] = [("full", samples[: RATE * 10])]
        window_size = RATE * 5
        step = RATE * 2
        for start in range(0, max(1, len(samples) - window_size + 1), step):
            chunk = samples[start:start + window_size]
            if len(chunk) < RATE * 2:
                continue
            windows.append((f"window_{start / RATE:.1f}s", chunk))

        for window_name, chunk in windows:
            inputs = extractor(chunk, sampling_rate=RATE, return_tensors="pt")
            with torch.inference_mode():
                logits = model(**inputs).logits[0].cpu().numpy()
            probs = 1.0 / (1.0 + np.exp(-np.clip(logits, -60, 60)))
            order = np.argsort(probs)[::-1][:20]
            top = ", ".join(
                f"{id_to_label.get(int(i), str(i))}={float(probs[i]):.3f}"
                for i in order
            )
            print(f"  {window_name}: {top}", flush=True)


if __name__ == "__main__":
    main()
