from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ArmoredSpeech.audio_intelligence import AudioIntelligence, AudioMode

EXPECTED = {
    "291_6VKD4FrQVJ.mp4": (AudioMode.NOISE, "46a6b217222c5cd4a2423598eeb0c7d0be322ddeacd1bc68de27055eb9c59386"),
    "1046_20uhmSz3VE.mp4": (AudioMode.SPEECH, "0e58fcd53135e437778ee1abd787f985a5b6ebe70c638072a232eeded31f9d05"),
    "12008_1AyNNNzh3.mp4": (AudioMode.MUSIC_ONLY, "7710fde243475a71e70aaed377e25c0b108224c4fd43fffabc9c269e5b725607"),
    "12025_7ptrS9Gsg2.mp4": (AudioMode.SPEECH_PLUS_MUSIC, "e11f0578f6b6c41262556e3426215e9211f959a301046cd207c5f982cb65bda9"),
    "178482_7AeDXQWFF2.mp4": (AudioMode.NO_AUDIO, "8353c7286c6ef6dfd7698084e3e88f6b0bcea5cca954d7cfdcdfee0c2f4d0861"),
}


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def discover_corpus() -> list[Path]:
    explicit_root = os.getenv("ARMORED_AUDIO_VALIDATION_VIDEO_ROOT")
    corpus_root = Path(explicit_root) if explicit_root else ROOT / "storage"
    if not corpus_root.is_dir():
        raise FileNotFoundError(
            f"Corpus não encontrado em {corpus_root}. "
            "Defina ARMORED_AUDIO_VALIDATION_VIDEO_ROOT para a pasta dos vídeos."
        )
    videos = sorted(path.resolve() for path in corpus_root.rglob("*.mp4") if path.is_file())
    if not videos:
        raise FileNotFoundError(f"Nenhum MP4 encontrado em {corpus_root}")
    return videos


def main() -> None:
    try:
        import silero_vad  # noqa: F401
    except ImportError as exc:
        raise SystemExit(
            "DEPENDÊNCIA AUSENTE: silero-vad. Use o Python do ambiente virtual "
            "do laboratório e instale ArmoredSpeech/requirements-analysis.txt; "
            "não aprove classificações SPEECH_UNVERIFIED."
        ) from exc

    os.environ.setdefault("ARMORED_AUDIO_CONTENT_NOISE_THRESHOLD", "0.05")
    videos = discover_corpus()
    before = {path: digest(path) for path in videos}
    analyzer = AudioIntelligence()
    failures: list[str] = []
    observed = 0
    reviewed = 0

    print(f"CORPUS_COUNT={len(videos)}", flush=True)
    for path in videos:
        expected_case = EXPECTED.get(path.name)
        expected_mode = expected_case[0] if expected_case else None
        expected_hash = expected_case[1] if expected_case else None
        if expected_hash and before[path] != expected_hash:
            failures.append(f"{path.name}: SHA-256 não corresponde ao original de referência")
            print(f"FAIL {path.name}: HASH_MISMATCH sha256={before[path]}", flush=True)
            continue

        result = analyzer.analyze(path)
        data = result.as_dict()
        label = "PASS" if expected_mode and result.mode == expected_mode else (
            "FAIL" if expected_mode else "REVIEW"
        )
        print(
            f"{label} {path.name}: "
            f"expected={expected_mode or 'MANUAL_REVIEW'} actual={result.mode} "
            f"speech={data['speech_probability']} music={data['music_probability']} "
            f"noise={data['noise_probability']} vad={data['speech_activity_ratio']} "
            f"language={data['language']} language_confidence={data['language_confidence']} "
            f"method={data['method']} sha256={before[path]}",
            flush=True,
        )
        observed += 1
        if expected_mode:
            if result.mode != expected_mode:
                failures.append(f"{path.name}: esperado {expected_mode}, recebido {result.mode}")
        else:
            reviewed += 1
        if result.mode == AudioMode.SPEECH_UNVERIFIED:
            failures.append(f"{path.name}: classificação não verificada; requer investigação")

    changed = [str(path) for path, initial in before.items()
               if not path.is_file() or digest(path) != initial]
    if changed:
        failures.append("Arquivos originais alterados: " + ", ".join(changed))

    print(f"FILES_ANALYZED={observed}/{len(videos)}", flush=True)
    print(f"ADDITIONAL_FILES_REQUIRING_LABEL_REVIEW={reviewed}", flush=True)
    print(f"ORIGINAL_HASHES_UNCHANGED={not changed}", flush=True)
    if failures:
        raise SystemExit("\n".join(failures))
    print(f"FULL_CORPUS_ANALYSIS=PASS ({observed} files; {reviewed} require label review)", flush=True)


if __name__ == "__main__":
    main()
