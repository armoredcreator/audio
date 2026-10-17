from __future__ import annotations

import hashlib
import wave
from dataclasses import dataclass
from pathlib import Path

import pytest

from ArmoredStudio.speech_handoff import SpeechHandoffError, validate_audio_package
from ArmoredSpeech import audio_package


@dataclass
class Item:
    source_id: str
    telegram_message_id: str
    content_id: str
    original_path: Path


def _write_wav(path: Path, *, sample_rate: int = 44100, channels: int = 2, seconds: float = 1.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = int(sample_rate * seconds)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(sample_rate)
        stream.writeframes(b"\x01\x00" * frames * channels)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _package(tmp_path: Path, **overrides):
    original = tmp_path / "original.mp4"
    original.write_bytes(b"synthetic, immutable original bytes")
    voice = tmp_path / "package" / "voice.wav"
    effect = tmp_path / "package" / "effect.wav"
    _write_wav(voice)
    _write_wav(effect)
    digest = _sha(original)
    payload = {
        "contract_version": 3,
        "status": "READY",
        "source_id": "source-1",
        "item_id": "telegram-1",
        "content_id": "source1_telegram1",
        "original_path": str(original),
        "original_sha256_before": digest,
        "original_sha256_after": digest,
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": False,
        "confidence": 0.99,
        "route": "RVC_MELODY",
        "rvc_eligible": True,
        "rvc_model": "melody",
        "rvc_model_sha256": "c" * 64,
        "rvc_index_sha256": "d" * 64,
        "voice_track": {
            "path": str(voice),
            "sha256": _sha(voice),
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 1.0,
        },
        "effect_track": {
            "path": str(effect),
            "sha256": _sha(effect),
            "source_sha256": "a" * 64,
            "format": "wav-pcm-s16le",
            "sample_rate": 44100,
            "channels": 2,
            "duration_seconds": 1.0,
            "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
        },
        "audio_timeline": __import__(
            "ArmoredSpeech.timeline_mixer", fromlist=["build_audio_filter_graph"]
        ).build_audio_filter_graph(
            visual_plan={"audio_start": 0.0}, intro=True, intro_position="final"
        ),
        "prepared_video_duration_seconds": 1.0,
    }
    payload.update(overrides)
    return original, voice, effect, payload



def _mock_final_timeline(monkeypatch) -> None:
    # Speech no longer runs a separate FFmpeg to build the final mixed WAV.
    return None


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

def _validate(
    root: Path,
    original: Path,
    payload: dict,
    *,
    expected_source_id: str = "source-1",
    expected_item_id: str = "telegram-1",
):
    return validate_audio_package(
        payload,
        workspace_root=root,
        expected_source_id=expected_source_id,
        expected_item_id=expected_item_id,
        expected_original_path=original,
    )


def test_valid_v3_audio_package_validates_final_track_hash_and_identity(tmp_path):
    original, voice, effect, payload = _package(tmp_path)

    result = _validate(tmp_path, original, payload)

    assert result.route == "RVC_MELODY"
    assert result.rvc_eligible is True
    assert result.voice_track_path == voice
    assert result.voice_sha256 == _sha(voice)
    assert result.effect_track_path == effect
    assert result.effect_sha256 == _sha(effect)
    assert "[voice_main][effect_main]amix=" in result.audio_filter_graph


@pytest.mark.parametrize("field,value", [
    ("status", "PROCESSING"),
    ("contract_version", 2),
    ("route", "UNKNOWN"),
    ("rvc_model", "another-model"),
    ("source_id", "wrong-source"),
    ("item_id", "wrong-item"),
])
def test_v3_package_rejects_wrong_status_contract_or_identity(tmp_path, field, value):
    original, _, _, payload = _package(tmp_path, **{field: value})

    with pytest.raises(SpeechHandoffError):
        _validate(tmp_path, original, payload)


def test_v3_package_rejects_mutated_final_track_hash(tmp_path):
    original, voice, _, payload = _package(tmp_path)
    voice.write_bytes(voice.read_bytes() + b"tampered")

    with pytest.raises(SpeechHandoffError, match="SHA-256 mismatch"):
        _validate(tmp_path, original, payload)


def test_v3_package_rejects_final_track_outside_workspace(tmp_path):
    original, _, _, payload = _package(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.wav"
    _write_wav(outside)
    payload["voice_track"]["path"] = str(outside)
    payload["voice_track"]["sha256"] = _sha(outside)

    with pytest.raises(SpeechHandoffError, match="inside the lab workspace"):
        _validate(tmp_path, original, payload)


def test_studio_validates_effect_only_contract_without_selecting_audio_route(tmp_path):
    original, _, effect, payload = _package(
        tmp_path,
        audio_class="MUSIC_ONLY",
        language="unknown",
        speech_detected=False,
        music_detected=True,
        route="EFFECT_ONLY",
        rvc_eligible=False,
        rvc_model=None,
        rvc_model_sha256=None,
        rvc_index_sha256=None,
        voice_track=None,
    )

    result = _validate(tmp_path, original, payload)
    assert result.route == "EFFECT_ONLY"
    assert result.rvc_eligible is False
    assert result.voice_track_path is None
    assert result.effect_track_path == effect


def test_audio_package_builder_prepares_tracks_without_changing_original(tmp_path, monkeypatch):
    _mock_final_timeline(monkeypatch)
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "source.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredStudio" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    original_hash = _sha(original)
    item = Item("source-1", "telegram-1", "source1_telegram1", original)
    directive = {
        "contract_version": 1,
        "source_id": item.source_id,
        "item_id": item.telegram_message_id,
        "original_path": str(original),
        "audio_artifact_path": str(source_wav),
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": False,
        "confidence": 0.99,
        "rvc_eligible": True,
        "original_sha256_before": original_hash,
        "original_sha256_after": original_hash,
    }

    def fake_write_wave(path: str | Path):
        _write_wav(Path(path), sample_rate=44100, channels=2)

    monkeypatch.setattr(audio_package, "_tool", lambda name, env_name: f"mock-{name}")
    monkeypatch.setattr(audio_package, "_media_duration", lambda path, tool: 1.0)

    def fake_run(command: list[str], label: str):
        fake_write_wave(command[-1])

    monkeypatch.setattr(audio_package, "_run", fake_run)

    from ArmoredSpeech import rvc_engine as rvc
    def fake_convert(source, destination, voice, item_id=None):
        fake_write_wave(destination)
        return Path(destination)
    monkeypatch.setattr(rvc, "converter_voz", fake_convert)
    preflight = _preflight(
        tmp_path, original, source_wav, effect_source, rvc_allowed=True
    )
    monkeypatch.setattr(
        rvc, "localizar_modelo",
        lambda voice: (Path(preflight["rvc_model_path"]), Path(preflight["rvc_index_path"])),
    )

    output_dir = tmp_path / "storage" / "validation" / "batch1" / "source1" / "telegram1"
    package = audio_package.prepare_audio_package(
        directive,
        item,
        workspace_root=tmp_path,
        effect_source_path=effect_source,
        output_dir=output_dir,
        preflight=preflight,
    )

    assert package["route"] == "RVC_MELODY"
    assert package["contract_version"] == 3
    assert package["voice_track"]["path"] == str(output_dir / "voice.wav")
    assert package["effect_track"]["path"] == str(output_dir / "effect.wav")
    assert package["audio_timeline"]["composition_owner"] == "ArmoredSpeech"
    assert (output_dir / "voice.wav").is_file()
    assert (output_dir / "package.json").is_file()
    assert not (output_dir / "rvc_input.wav").exists()
    assert not (output_dir / "rvc_raw.wav").exists()
    assert _sha(original) == original_hash
    assert _validate(tmp_path, original, package).route == "RVC_MELODY"


def test_audio_package_builder_routes_unapproved_audio_to_silence_plus_effect(tmp_path, monkeypatch):
    _mock_final_timeline(monkeypatch)
    original = tmp_path / "music.mp4"
    original.write_bytes(b"synthetic original")
    source_wav = tmp_path / "music.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredStudio" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    original_hash = _sha(original)
    item = Item("source-1", "telegram-2", "source1_telegram2", original)
    directive = {
        "contract_version": 1,
        "source_id": item.source_id,
        "item_id": item.telegram_message_id,
        "original_path": str(original),
        "audio_artifact_path": str(source_wav),
        "audio_class": "MUSIC_ONLY",
        "language": "unknown",
        "speech_detected": False,
        "music_detected": True,
        "confidence": 0.96,
        "rvc_eligible": False,
        "original_sha256_before": original_hash,
        "original_sha256_after": original_hash,
    }

    monkeypatch.setattr(audio_package, "_tool", lambda name, env_name: f"mock-{name}")
    monkeypatch.setattr(audio_package, "_media_duration", lambda path, tool: 1.0)
    monkeypatch.setattr(
        audio_package,
        "_run",
        lambda command, label: _write_wav(Path(command[-1]), sample_rate=44100, channels=2),
    )

    from ArmoredSpeech import rvc_engine as rvc
    def forbidden_rvc(*args, **kwargs):
        raise AssertionError("RVC must not run for MUSIC_ONLY")
    monkeypatch.setattr(rvc, "converter_voz", forbidden_rvc)
    preflight = _preflight(
        tmp_path, original, source_wav, effect_source, rvc_allowed=False
    )

    package = audio_package.prepare_audio_package(
        directive,
        item,
        workspace_root=tmp_path,
        effect_source_path=effect_source,
        output_dir=tmp_path / "storage" / "validation" / "batch2" / "source1" / "telegram2",
        preflight=preflight,
    )

    assert package["route"] == "EFFECT_ONLY"
    assert package["rvc_model"] is None
    assert package["rvc_eligible"] is False
    assert _validate(
        tmp_path, original, package, expected_item_id="telegram-2"
    ).route == "EFFECT_ONLY"


def test_audio_package_builder_fails_closed_when_music_evidence_is_unknown(tmp_path):
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "source.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredStudio" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    digest = _sha(original)
    item = Item("source-1", "telegram-3", "source1_telegram3", original)
    directive = {
        "contract_version": 1,
        "source_id": item.source_id,
        "item_id": item.telegram_message_id,
        "original_path": str(original),
        "audio_artifact_path": str(source_wav),
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": None,
        "confidence": 0.99,
        "rvc_eligible": True,
        "original_sha256_before": digest,
        "original_sha256_after": digest,
    }

    with pytest.raises(audio_package.AudioPackageError, match="unsafe RVC"):
        audio_package.prepare_audio_package(
            directive,
            item,
            workspace_root=tmp_path,
            effect_source_path=effect_source,
            output_dir=tmp_path / "storage" / "validation" / "batch3" / "source1" / "telegram3",
        )
def test_audio_package_builder_reuses_shared_batch_effect_without_reencoding(tmp_path, monkeypatch):
    _mock_final_timeline(monkeypatch)
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "source.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredStudio" / "assets" / "efeitosonoro.wav"
    _write_wav(effect_source)
    shared_effect = tmp_path / "storage" / "validation" / "shared" / "effect.wav"
    _write_wav(shared_effect)
    original_hash = _sha(original)
    item = Item("source-1", "telegram-shared", "source1_telegram_shared", original)
    directive = {
        "contract_version": 1,
        "source_id": item.source_id,
        "item_id": item.telegram_message_id,
        "original_path": str(original),
        "audio_artifact_path": str(source_wav),
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": False,
        "confidence": 0.99,
        "rvc_eligible": True,
        "original_sha256_before": original_hash,
        "original_sha256_after": original_hash,
    }
    events = []

    def fake_write_wave(path: str | Path):
        _write_wav(Path(path), sample_rate=44100, channels=2)

    monkeypatch.setattr(audio_package, "_tool", lambda name, env_name: f"mock-{name}")
    monkeypatch.setattr(audio_package, "_media_duration", lambda path, tool: 1.0)

    def fake_run(command: list[str], label: str):
        events.append(label)
        fake_write_wave(command[-1])

    monkeypatch.setattr(audio_package, "_run", fake_run)
    from ArmoredSpeech import rvc_engine as rvc

    def fake_convert(source, destination, voice, item_id=None):
        events.append("RVC_MELODY")
        fake_write_wave(destination)
        return Path(destination)

    monkeypatch.setattr(rvc, "converter_voz", fake_convert)
    preflight = _preflight(
        tmp_path, original, source_wav, effect_source, rvc_allowed=True
    )
    monkeypatch.setattr(
        rvc,
        "localizar_modelo",
        lambda voice: (Path(preflight["rvc_model_path"]), Path(preflight["rvc_index_path"])),
    )
    shared_meta = audio_package._wave_metadata(shared_effect)
    prepared_effect = {
        "path": str(shared_effect),
        "sha256": _sha(shared_effect),
        "source_sha256": _sha(effect_source),
        "format": "wav-pcm-s16le",
        "sample_rate": shared_meta.sample_rate,
        "channels": shared_meta.channels,
        "duration_seconds": shared_meta.duration_seconds,
        "gain_policy": "loudnorm-I-12-LRA7-TP-1-then-volume-0.8",
        "_prepared_and_verified": True,
    }

    output_dir = tmp_path / "storage" / "validation" / "batch-shared" / "source1" / "telegram-shared"
    package = audio_package.prepare_audio_package(
        directive,
        item,
        workspace_root=tmp_path,
        effect_source_path=effect_source,
        output_dir=output_dir,
        preflight=preflight,
        prepared_effect_track=prepared_effect,
    )

    assert package["route"] == "RVC_MELODY"
    assert package["contract_version"] == 3
    assert package["voice_track"]["path"] == str(output_dir / "voice.wav")
    assert package["effect_track"]["path"] == str(shared_effect)
    assert package["audio_timeline"]["composition_owner"] == "ArmoredSpeech"
    assert not (output_dir / "effect.wav").exists()
    assert "EFFECT_TRACK_PREPARATION_FAILED" not in events
    assert events.count("RVC_MELODY") == 1
    assert _sha(original) == original_hash
    assert _validate(
        tmp_path, original, package, expected_item_id="telegram-shared"
    ).effect_track_path == shared_effect


def test_batch_preflight_hashes_melody_model_and_index_only_once(tmp_path, monkeypatch):
    original = tmp_path / "source.mp4"
    original.write_bytes(b"synthetic immutable original")
    source_wav = tmp_path / "source.wav"
    _write_wav(source_wav, sample_rate=16000, channels=1)
    effect_source = tmp_path / "ArmoredStudio" / "assets" / "efeitosonoro.wav"
    effect_source.parent.mkdir(parents=True, exist_ok=True)
    effect_source.write_bytes(b"effect asset bytes large enough for preflight" * 2)
    model_dir = tmp_path / "ArmoredStudio" / "runtime" / "rvc" / "models" / "melody"
    model_dir.mkdir(parents=True, exist_ok=True)
    model = model_dir / "melody.pth"
    index = model_dir / "melody.index"
    model.write_bytes(b"synthetic Melody model fingerprint")
    index.write_bytes(b"synthetic Melody index fingerprint")
    python_path = tmp_path / ".venv" / "Scripts" / "python.exe"
    python_path.parent.mkdir(parents=True, exist_ok=True)
    python_path.write_bytes(b"python placeholder")

    item = Item("source-1", "telegram-asset-cache", "source1_telegram_asset_cache", original)
    digest = _sha(original)
    directive = {
        "contract_version": 1,
        "source_id": item.source_id,
        "item_id": item.telegram_message_id,
        "original_path": str(original),
        "audio_artifact_path": str(source_wav),
        "audio_class": "PT_BR_NARRATION",
        "language": "pt-br",
        "speech_detected": True,
        "music_detected": False,
        "confidence": 0.99,
        "rvc_eligible": True,
        "original_sha256_before": digest,
        "original_sha256_after": digest,
    }

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
        directive,
        item,
        workspace_root=tmp_path,
        effect_source_path=effect_source,
        effect_source_sha256=effect_hash,
    )
    fingerprint = {
        key: first[key]
        for key in (
            "rvc_python_path", "rvc_model_path", "rvc_model_sha256",
            "rvc_model_size", "rvc_model_mtime_ns", "rvc_model_inode",
            "rvc_index_path", "rvc_index_sha256",
            "rvc_index_size", "rvc_index_mtime_ns", "rvc_index_inode",
        )
    }
    second = audio_package.preflight_audio_package(
        directive,
        item,
        workspace_root=tmp_path,
        effect_source_path=effect_source,
        effect_source_sha256=effect_hash,
        rvc_asset_fingerprint=fingerprint,
    )

    assert first["rvc_model_sha256"] == second["rvc_model_sha256"] == original_sha_function(model)
    assert first["rvc_index_sha256"] == second["rvc_index_sha256"] == original_sha_function(index)
    assert hash_counts[model.resolve()] == 1
    assert hash_counts[index.resolve()] == 1



def test_shared_effect_sha256_is_cached_across_package_validation(tmp_path, monkeypatch):
    item_one = tmp_path / "item-one"
    item_two = tmp_path / "item-two"
    item_one.mkdir()
    item_two.mkdir()
    original_one, voice_one, effect_shared, payload_one = _package(item_one)
    original_two, voice_two, _, payload_two = _package(item_two)
    payload_one["original_path"] = str(original_one)
    payload_two["original_path"] = str(original_two)
    payload_two["source_id"] = "source-1"
    payload_two["item_id"] = "telegram-2"
    payload_two["original_sha256_before"] = _sha(original_two)
    payload_two["original_sha256_after"] = _sha(original_two)
    # A batch reuses the pre-normalized project effect but carries a unique
    # voice track and original per item. Studio validates both inputs.
    payload_two["effect_track"] = dict(payload_one["effect_track"])
    assert payload_two["voice_track"]["path"] != payload_one["voice_track"]["path"]
    assert payload_two["effect_track"]["path"] == payload_one["effect_track"]["path"]

    import ArmoredStudio.speech_handoff as handoff_module

    real_sha = handoff_module.sha256_file
    counted: dict[Path, int] = {}

    def count_sha(path: Path) -> str:
        resolved = Path(path).resolve()
        counted[resolved] = counted.get(resolved, 0) + 1
        return real_sha(path)

    monkeypatch.setattr(handoff_module, "sha256_file", count_sha)
    cache: dict[tuple[str, int, int, int, int], str] = {}

    first = handoff_module.validate_audio_package(
        payload_one,
        workspace_root=tmp_path,
        expected_source_id="source-1",
        expected_item_id="telegram-1",
        expected_original_path=original_one,
        track_hash_cache=cache,
    )
    second = handoff_module.validate_audio_package(
        payload_two,
        workspace_root=tmp_path,
        expected_source_id="source-1",
        expected_item_id="telegram-2",
        expected_original_path=original_two,
        track_hash_cache=cache,
    )

    assert first.effect_sha256 == second.effect_sha256 == _sha(effect_shared)
    assert first.effect_track_path == second.effect_track_path == effect_shared
    assert first.voice_sha256 == _sha(voice_one)
    assert second.voice_sha256 == _sha(voice_two)
    assert counted[effect_shared.resolve()] == 1
    assert counted[voice_one.resolve()] == 1
    assert counted[voice_two.resolve()] == 1
    assert counted[original_one.resolve()] == 1
    assert counted[original_two.resolve()] == 1
