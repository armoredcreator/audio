from __future__ import annotations

import hashlib
import json
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from ArmoredStudio.speech_handoff import SpeechHandoffError, validate_audio_package
from ArmoredSpeech import audio_package
from ArmoredSpeech.timeline_mixer import build_audio_filter_graph


@dataclass
class Item:
    source_id: str
    telegram_message_id: str
    content_id: str
    original_path: Path


def _write_wav(
    path: Path,
    *,
    sample_rate: int = 44100,
    channels: int = 2,
    seconds: float = 1.0,
    silence: bool = False,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(sample_rate * seconds)
    sample = b"\x00\x00" if silence else b"\x01\x00"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(sample * frames * channels)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _plan_digest(plan: dict) -> str:
    return hashlib.sha256(
        json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _fixture_payload(root: Path, *, source_id: str = "source-1", item_id: str = "item-1"):
    original = root / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    voice = root / "package" / "voice.wav"
    effect = root / "package" / "effect.wav"
    _write_wav(voice)
    _write_wav(effect)
    plan = {"video_start": 0.0}
    graph_meta = build_audio_filter_graph(
        visual_plan=plan,
        audio_start_seconds=0.0,
        main_duration_seconds=1.0,
        intro=True,
        intro_position="final",
    )
    graph = graph_meta["filter_graph"]
    timeline = {
        key: value for key, value in graph_meta.items()
        if key not in {"filter_graph", "output_label"}
    }
    timeline["final_audio_duration_seconds"] = 3.0
    original_hash = _sha(original)
    return original, voice, effect, plan, {
        "contract_version": 5,
        "status": "READY",
        "source_id": source_id,
        "item_id": item_id,
        "content_id": f"{source_id}_{item_id}",
        "original_path": str(original),
        "original_sha256_before": original_hash,
        "original_sha256_after": original_hash,
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": False,
        "confidence": 0.99,
        "route": "RVC_MELODY",
        "rvc_eligible": True,
        "voice_track": {
            "path": str(voice),
            "sha256": _sha(voice),
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 1.0,
            "size_bytes": voice.stat().st_size,
            "role": "voice",
        },
        "effect_track": {
            "path": str(effect),
            "sha256": _sha(effect),
            "source_sha256": "a" * 64,
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 1.0,
            "size_bytes": effect.stat().st_size,
            "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
        },
        "audio_filter_graph": graph,
        "audio_output_label": "[final_a]",
        "audio_timeline": timeline,
        "prepared_video_duration_seconds": 1.0,
    }


def _validate(root: Path, original: Path, payload: dict, *, source_id="source-1", item_id="item-1"):
    return validate_audio_package(
        payload,
        workspace_root=root,
        expected_source_id=source_id,
        expected_item_id=item_id,
        expected_original_path=original,
    )


def _preflight(root: Path, original: Path, source_wav: Path, effect: Path, *, rvc_allowed: bool):
    model = root / "test-models" / "melody.pth"
    index = root / "test-models" / "melody.index"
    model_hash = index_hash = None
    model_path = index_path = None
    if rvc_allowed:
        model.parent.mkdir(parents=True, exist_ok=True)
        model.write_bytes(b"synthetic-model-fingerprint")
        index.write_bytes(b"synthetic-index-fingerprint")
        model_hash = _sha(model)
        index_hash = _sha(index)
        model_path, index_path = str(model), str(index)
    return {
        "original_sha256": _sha(original),
        "source_wav_sha256": _sha(source_wav),
        "effect_source_path": str(effect.resolve()),
        "effect_source_sha256": _sha(effect),
        "rvc_allowed": rvc_allowed,
        "video_duration_seconds": 1.0,
        "ffmpeg": "mock-ffmpeg",
        "ffprobe": "mock-ffprobe",
        "rvc_model_path": model_path,
        "rvc_model_sha256": model_hash,
        "rvc_index_path": index_path,
        "rvc_index_sha256": index_hash,
    }


def _mock_preparation(monkeypatch):
    monkeypatch.setattr(audio_package, "_tool", lambda name, env_name: f"mock-{name}")
    monkeypatch.setattr(audio_package, "_media_duration", lambda path, tool: 1.0)

    def fake_run(command: list[str], label: str):
        _write_wav(Path(command[-1]), sample_rate=44100, channels=2)

    monkeypatch.setattr(audio_package, "_run", fake_run)


def _directive(original: Path, source_wav: Path, item: Item, *, audio_class="PT_BR_NARRATION", rvc=True, music=False):
    digest = _sha(original)
    return {
        "contract_version": 1,
        "source_id": item.source_id,
        "item_id": item.telegram_message_id,
        "original_path": str(original),
        "audio_artifact_path": str(source_wav),
        "audio_class": audio_class,
        "language": "pt-br" if rvc else "unknown",
        "speech_detected": bool(rvc),
        "music_detected": bool(music),
        "confidence": 0.99,
        "rvc_eligible": bool(rvc),
        "original_sha256_before": digest,
        "original_sha256_after": digest,
    }


def test_valid_v5_audio_package_validates_tracks_graph_and_identity(tmp_path):
    original, voice, effect, plan, payload = _fixture_payload(tmp_path)
    validated = _validate(tmp_path, original, payload)

    assert validated.voice_track_path == voice
    assert validated.effect_track_path == effect
    assert validated.audio_filter_graph == payload["audio_filter_graph"]
    assert validated.audio_output_label == "[final_a]"
    assert validated.visual_plan_sha256 == _plan_digest(plan)


@pytest.mark.parametrize("field,value", [
    ("status", "PROCESSING"),
    ("contract_version", 4),
    ("source_id", "wrong-source"),
    ("item_id", "wrong-item"),
])
def test_v5_package_rejects_wrong_status_contract_or_identity(tmp_path, field, value):
    original, _, _, _, payload = _fixture_payload(tmp_path, source_id="source-1", item_id="item-1")
    payload[field] = value
    with pytest.raises(SpeechHandoffError):
        _validate(tmp_path, original, payload)


def test_v5_package_rejects_mutated_track_hash(tmp_path):
    original, voice, _, _, payload = _fixture_payload(tmp_path)
    changed = bytearray(voice.read_bytes())\n    changed[-1] ^= 1\n    voice.write_bytes(changed)
    with pytest.raises(SpeechHandoffError, match="SHA-256 mismatch"):
        _validate(tmp_path, original, payload)


def test_v5_package_rejects_unsupported_audio_graph_filter(tmp_path):
    original, _, _, _, payload = _fixture_payload(tmp_path)
    payload["audio_filter_graph"] += ";[2:a]amovie=secret.wav[bad]"
    payload["audio_timeline"]["filter_graph_sha256"] = hashlib.sha256(
        payload["audio_filter_graph"].encode("utf-8")
    ).hexdigest()
    with pytest.raises(SpeechHandoffError, match="unsupported characters|unsupported filter"):
        _validate(tmp_path, original, payload)


def test_audio_package_builder_prepares_speech_tracks_and_graph_without_final_audio_render(tmp_path, monkeypatch):
    _mock_preparation(monkeypatch)
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "source.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredSpeech" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    original_hash = _sha(original)
    item = Item("source-1", "item-1", "source1_item1", original)
    directive = _directive(original, source_wav, item)
    from ArmoredSpeech import rvc_engine as rvc

    def fake_convert(source, destination, voice, item_id=None):
        _write_wav(Path(destination), sample_rate=44100, channels=2)
        return Path(destination)

    monkeypatch.setattr(rvc, "converter_voz", fake_convert)
    preflight = _preflight(tmp_path, original, source_wav, effect_source, rvc_allowed=True)
    monkeypatch.setattr(
        rvc, "localizar_modelo",
        lambda voice: (Path(preflight["rvc_model_path"]), Path(preflight["rvc_index_path"])),
    )

    output_dir = tmp_path / "storage" / "validation" / "batch1" / "source1" / "item1"
    package = audio_package.prepare_audio_package(
        directive, item,
        workspace_root=tmp_path,
        effect_source_path=effect_source,
        output_dir=output_dir,
        preflight=preflight,
        visual_plan={"video_start": 0.0},
    )

    assert package["contract_version"] == 5
    assert package["route"] == "RVC_MELODY"
    assert package["voice_track"]["path"] == str(output_dir / "voice.wav")
    assert package["effect_track"]["path"] == str(output_dir / "effect.wav")
    assert package["audio_filter_graph"].count("amix=") == 1
    assert package["audio_output_label"] == "[final_a]"
    assert (output_dir / "voice.wav").is_file()
    assert (output_dir / "effect.wav").is_file()
    assert not (output_dir / "final_audio.m4a").exists()
    assert not (output_dir / "rvc_input.wav").exists()
    assert not (output_dir / "rvc_raw.wav").exists()
    assert _sha(original) == original_hash
    assert _validate(tmp_path, original, package).audio_filter_graph == package["audio_filter_graph"]


def test_effect_only_route_builds_silence_input_and_never_calls_rvc(tmp_path, monkeypatch):
    _mock_preparation(monkeypatch)
    original = tmp_path / "music.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "music.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredSpeech" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    item = Item("source-1", "item-2", "source1_item2", original)
    directive = _directive(original, source_wav, item, audio_class="MUSIC_ONLY", rvc=False, music=True)
    from ArmoredSpeech import rvc_engine as rvc

    monkeypatch.setattr(
        rvc, "converter_voz",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("RVC must not run")),
    )
    preflight = _preflight(tmp_path, original, source_wav, effect_source, rvc_allowed=False)
    package = audio_package.prepare_audio_package(
        directive, item,
        workspace_root=tmp_path,
        effect_source_path=effect_source,
        output_dir=tmp_path / "storage" / "validation" / "batch2" / "source1" / "item2",
        preflight=preflight,
        visual_plan={"video_start": 0.0},
    )

    assert package["route"] == "EFFECT_ONLY"
    assert package["rvc_eligible"] is False
    assert package["voice_track"]["role"] == "silence"
    assert package["audio_filter_graph"].count("amix=") == 1
    assert package["voice_track"]["path"].endswith("voice.wav")
    validated = _validate(
        tmp_path, original, package, item_id="item-2"
    )
    assert validated.voice_track_path.is_file()


def test_audio_package_fails_closed_when_music_evidence_is_unknown(tmp_path):
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "source.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredSpeech" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    item = Item("source-1", "item-3", "source1_item3", original)
    directive = _directive(original, source_wav, item, rvc=True, music=False)
    directive["music_detected"] = None
    with pytest.raises(audio_package.AudioPackageError, match="unsafe RVC"):
        audio_package.prepare_audio_package(
            directive, item,
            workspace_root=tmp_path,
            effect_source_path=effect_source,
            output_dir=tmp_path / "storage" / "validation" / "batch3" / "source1" / "item3",
        )


def test_audio_package_reuses_shared_effect_track_without_copy_or_reencoding(tmp_path, monkeypatch):
    _mock_preparation(monkeypatch)
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "source.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredSpeech" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    shared_effect = tmp_path / "storage" / "validation" / "batch-shared" / "_shared" / "effect.wav"
    _write_wav(shared_effect)
    item = Item("source-1", "item-shared", "source1_item_shared", original)
    directive = _directive(original, source_wav, item)
    events = []
    from ArmoredSpeech import rvc_engine as rvc

    def fake_convert(source, destination, voice, item_id=None):
        events.append("RVC_MELODY")
        _write_wav(Path(destination))
        return Path(destination)

    monkeypatch.setattr(rvc, "converter_voz", fake_convert)
    preflight = _preflight(tmp_path, original, source_wav, effect_source, rvc_allowed=True)
    monkeypatch.setattr(
        rvc, "localizar_modelo",
        lambda voice: (Path(preflight["rvc_model_path"]), Path(preflight["rvc_index_path"])),
    )
    effect_meta = audio_package._wave_metadata(shared_effect)
    prepared_effect = {
        "path": str(shared_effect),
        "sha256": _sha(shared_effect),
        "source_sha256": _sha(effect_source),
        "format": "wav-pcm-s16le",
        "sample_rate": effect_meta.sample_rate,
        "channels": effect_meta.channels,
        "duration_seconds": effect_meta.duration_seconds,
        "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
        "_prepared_and_verified": True,
    }

    output_dir = tmp_path / "storage" / "validation" / "batch-shared" / "source1" / "item-shared"
    package = audio_package.prepare_audio_package(
        directive, item,
        workspace_root=tmp_path,
        effect_source_path=effect_source,
        output_dir=output_dir,
        preflight=preflight,
        prepared_effect_track=prepared_effect,
        visual_plan={"video_start": 0.0},
    )

    assert package["contract_version"] == 5
    assert package["effect_track"]["path"] == str(shared_effect)
    assert shared_effect.is_file()
    assert events.count("RVC_MELODY") == 1
    assert _validate(tmp_path, original, package, item_id="item-shared").effect_track_path == shared_effect


def test_batch_preflight_hashes_melody_model_and_index_only_once(tmp_path, monkeypatch):
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "source.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredSpeech" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    model_dir = tmp_path / "ArmoredSpeech" / "runtime" / "rvc" / "models" / "melody"
    model_dir.mkdir(parents=True, exist_ok=True)
    model = model_dir / "melody.pth"
    index = model_dir / "melody.index"
    model.write_bytes(b"synthetic Melody model fingerprint")
    index.write_bytes(b"synthetic Melody index fingerprint")
    python_path = tmp_path / ".venv" / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_bytes(b"python placeholder")
    item = Item("source-1", "asset-cache", "source1_asset_cache", original)
    directive = _directive(original, source_wav, item)
    from ArmoredSpeech import rvc_engine as rvc

    monkeypatch.setattr(audio_package, "_tool", lambda name, env_name: f"mock-{name}")
    monkeypatch.setattr(audio_package, "_media_duration", lambda path, tool: 1.0)
    monkeypatch.setattr(rvc, "MODELS_DIR", model_dir.parent)
    monkeypatch.setattr(rvc, "resolver_rvc_python", lambda: python_path)
    monkeypatch.setattr(rvc, "localizar_modelo", lambda voice: (model, index))
    original_sha_function = audio_package.sha256_file
    hash_counts: dict[Path, int] = {}

    def counted_sha(path: str | Path) -> str:
        resolved = Path(path).resolve()
        hash_counts[resolved] = hash_counts.get(resolved, 0) + 1
        return original_sha_function(path)

    monkeypatch.setattr(audio_package, "sha256_file", counted_sha)
    effect_hash = original_sha_function(effect_source)
    first = audio_package.preflight_audio_package(
        directive, item, workspace_root=tmp_path,
        effect_source_path=effect_source, effect_source_sha256=effect_hash,
    )
    fingerprint = {
        key: first[key] for key in (
            "rvc_python_path", "rvc_model_path", "rvc_model_sha256",
            "rvc_model_size", "rvc_model_mtime_ns", "rvc_model_inode",
            "rvc_index_path", "rvc_index_sha256",
            "rvc_index_size", "rvc_index_mtime_ns", "rvc_index_inode",
        )
    }
    second = audio_package.preflight_audio_package(
        directive, item, workspace_root=tmp_path,
        effect_source_path=effect_source, effect_source_sha256=effect_hash,
        rvc_asset_fingerprint=fingerprint,
    )
    assert first["rvc_model_sha256"] == second["rvc_model_sha256"] == original_sha_function(model)
    assert first["rvc_index_sha256"] == second["rvc_index_sha256"] == original_sha_function(index)
    assert hash_counts[model.resolve()] == 1
    assert hash_counts[index.resolve()] == 1
