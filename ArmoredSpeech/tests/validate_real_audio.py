from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ArmoredSpeech.audio_intelligence import AudioIntelligence, AudioMode

ROOT = Path(__file__).resolve().parents[2]
CASES = [
    ("291_6VKD4FrQVJ.mp4", AudioMode.NOISE),
    ("1046_20uhmSz3VE.mp4", AudioMode.SPEECH),
    ("12008_1AyNNNzh3.mp4", AudioMode.MUSIC_ONLY),
    ("12025_7ptrS9Gsg2.mp4", AudioMode.SPEECH_PLUS_MUSIC),
    ("178482_7AeDXQWFF2.mp4", AudioMode.NO_AUDIO),
]
EXPECTED_SHA256 = {
    "291_6VKD4FrQVJ.mp4": "46a6b217222c5cd4a2423598eeb0c7d0be322ddeacd1bc68de27055eb9c59386",
    "1046_20uhmSz3VE.mp4": "0e58fcd53135e437778ee1abd787f985a5b6ebe70c638072a232eeded31f9d05",
    "12008_1AyNNNzh3.mp4": "7710fde243475a71e70aaed377e25c0b108224c4fd43fffabc9c269e5b725607",
    "12025_7ptrS9Gsg2.mp4": "e11f0578f6b6c41262556e3426215e9211f959a301046cd207c5f982cb65bda9",
    "178482_7AeDXQWFF2.mp4": "8353c7286c6ef6dfd7698084e3e88f6b0bcea5cca954d7cfdcdfee0c2f4d0861",
}


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def main() -> None:
    os.environ.setdefault("ARMORED_AUDIO_CONTENT_NOISE_THRESHOLD", "0.05")
    analyzer = AudioIntelligence()
    failures: list[str] = []
    initial_hashes: dict[str, str] = {}

    for filename, expected_mode in CASES:
        path = ROOT / filename
        actual_hash = digest(path)
        initial_hashes[filename] = actual_hash
        if actual_hash != EXPECTED_SHA256[filename]:
            failures.append(f"{filename}: uploaded SHA-256 does not match original")
            print(f"FAIL {filename}: hash mismatch {actual_hash}", flush=True)
            continue

        result = analyzer.analyze(path)
        data = result.as_dict()
        print(
            f"{'PASS' if result.mode == expected_mode else 'FAIL'} {filename}: "
            f"expected={expected_mode} actual={result.mode} "
            f"speech={data['speech_probability']} music={data['music_probability']} "
            f"noise={data['noise_probability']} vad={data['speech_activity_ratio']} "
            f"language={data['language']} language_confidence={data['language_confidence']} "
            f"method={data['method']}",
            flush=True,
        )
        if result.mode != expected_mode:
            failures.append(f"{filename}: expected {expected_mode}, got {result.mode}")
        if expected_mode in {AudioMode.NOISE, AudioMode.MUSIC_ONLY, AudioMode.NO_AUDIO}:
            if result.mode in {AudioMode.SPEECH, AudioMode.SPEECH_PLUS_MUSIC}:
                failures.append(f"{filename}: non-narration media classified as speech")

    changed = [
        filename for filename, before in initial_hashes.items()
        if digest(ROOT / filename) != before
    ]
    if changed:
        failures.append("original files changed: " + ", ".join(changed))
    print(f"ORIGINAL_HASHES_UNCHANGED={not changed}", flush=True)
    if failures:
        raise SystemExit("\n".join(failures))
    print("REAL_AUDIO_VALIDATION=PASS (5/5)", flush=True)


if __name__ == "__main__":
    main()
