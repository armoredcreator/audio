from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


class AudioMode:
    NO_AUDIO = "NO_AUDIO"
    MUSIC_ONLY = "MUSIC_ONLY"
    SPEECH = "SPEECH"
    SPEECH_PLUS_MUSIC = "SPEECH_PLUS_MUSIC"
    FOREIGN_SPEECH = "FOREIGN_SPEECH"
    SPEECH_UNVERIFIED = "SPEECH_UNVERIFIED"


@dataclass(frozen=True)
class AudioIntelligenceResult:
    mode: str
    confidence: float
    duration_seconds: float
    speech_ratio: float
    music_ratio: float
    method: str = "spectral-heuristic-v1"
    language: str = "unknown"
    language_confidence: float = 0.0
    speech_probability: float | None = None
    music_probability: float | None = None
    speech_activity_ratio: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "confidence": round(float(self.confidence), 3),
            "duration_seconds": round(float(self.duration_seconds), 3),
            "speech_ratio": round(float(self.speech_ratio), 3),
            "music_ratio": round(float(self.music_ratio), 3),
            "method": self.method,
            "language": self.language,
            "language_confidence": round(float(self.language_confidence), 3),
            "speech_probability": (
                round(float(self.speech_probability), 3)
                if self.speech_probability is not None else None
            ),
            "music_probability": (
                round(float(self.music_probability), 3)
                if self.music_probability is not None else None
            ),
            "speech_activity_ratio": (
                round(float(self.speech_activity_ratio), 3)
                if self.speech_activity_ratio is not None else None
            ),
        }


def _windows_hidden_kwargs() -> dict[str, Any]:
    import sys
    if sys.platform != "win32":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    return {"startupinfo": startupinfo, "creationflags": subprocess.CREATE_NO_WINDOW}


class AudioIntelligence:
    """Classify the source audio before Studio/RVC processing.

    The classifier is deliberately conservative: RVC is enabled only when
    speech evidence is strong enough. No-audio and music-only inputs never
    enter RVC. Mixed speech+music is kept as a distinct durable mode so the
    Studio path can treat it differently from pure speech.
    """

    SAMPLE_RATE = 16000
    MAX_SECONDS = 45.0
    FRAME = 400
    HOP = 160

    def __init__(self, ffmpeg: str | None = None):
        self.ffmpeg = ffmpeg or os.getenv("ARMORED_FFMPEG", "ffmpeg")

    def preflight(self) -> str:
        """Load language ID and independent speech/music models before production."""
        configured_model = os.getenv("ARMORED_AUDIO_LANGUAGE_MODEL", "tiny").strip()
        aliases = {
            "openai/whisper-tiny": "tiny",
            "openai/whisper-base": "base",
            "openai/whisper-small": "small",
            "openai/whisper-medium": "medium",
        }
        model_name = aliases.get(configured_model, configured_model)
        cache_dir = os.getenv(
            "ARMORED_AUDIO_MODEL_CACHE",
            str(Path("storage") / "models" / "audio"),
        )
        compute_type = os.getenv("ARMORED_AUDIO_COMPUTE_TYPE", "int8")
        language_provider = ""

        try:
            from faster_whisper import WhisperModel

            model = WhisperModel(
                model_name,
                device="cpu",
                compute_type=compute_type,
                download_root=cache_dir,
            )
            self._language_model_name = model_name
            self._language_model = model
            self._faster_whisper_failed_name = None
            language_provider = f"faster-whisper:{model_name}"
        except Exception as faster_exc:
            self._faster_whisper_failed_name = model_name
            hf_model_name = os.getenv(
                "ARMORED_AUDIO_LANGUAGE_MODEL_HF",
                configured_model if configured_model.startswith("openai/")
                else "openai/whisper-tiny",
            )
            try:
                from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

                processor = AutoProcessor.from_pretrained(hf_model_name)
                model = AutoModelForSpeechSeq2Seq.from_pretrained(hf_model_name)
                model.to("cpu")
                model.eval()
                self._hf_language_model_name = hf_model_name
                self._hf_language_processor = processor
                self._hf_language_model = model
                self._hf_language_failed_name = None
                language_provider = f"transformers:{hf_model_name}"
            except Exception as hf_exc:
                self._hf_language_failed_name = hf_model_name
                raise RuntimeError(
                    "Modelo de idioma indisponível; produção bloqueada. "
                    f"faster-whisper: {faster_exc}; transformers: {hf_exc}"
                ) from hf_exc

        vad_name = self._load_vad_model()
        content_model_name = self._load_content_model()
        return f"{language_provider}+silero-vad:{vad_name}+music-classifier:{content_model_name}"

    def _load_vad_model(self) -> str:
        """Load Silero VAD, a small independent voice-activity detector."""
        model_name = os.getenv("ARMORED_AUDIO_VAD_MODEL", "silero-vad").strip()
        if (
            getattr(self, "_vad_model_name", None) == model_name
            and getattr(self, "_vad_model", None) is not None
        ):
            return model_name
        from silero_vad import load_silero_vad

        model = load_silero_vad(onnx=False)
        self._vad_model_name = model_name
        self._vad_model = model
        return model_name

    def _load_content_model(self) -> str:
        """Load an independent AudioSet model for speech-versus-music evidence."""
        model_name = os.getenv(
            "ARMORED_AUDIO_CONTENT_MODEL",
            "MIT/ast-finetuned-audioset-10-10-0.4593",
        ).strip()
        if (
            getattr(self, "_content_model_name", None) == model_name
            and getattr(self, "_content_model", None) is not None
        ):
            return model_name

        from transformers import AutoFeatureExtractor, AutoModelForAudioClassification

        cache_dir = os.getenv(
            "ARMORED_AUDIO_MODEL_CACHE",
            str(Path("storage") / "models" / "audio"),
        )
        processor = AutoFeatureExtractor.from_pretrained(
            model_name,
            cache_dir=cache_dir,
        )
        model = AutoModelForAudioClassification.from_pretrained(
            model_name,
            cache_dir=cache_dir,
        )
        model.to("cpu")
        model.eval()
        self._content_model_name = model_name
        self._content_processor = processor
        self._content_model = model
        return model_name

    def _detect_speech_activity(self, samples: np.ndarray) -> float | None:
        """Return the fraction of the clip marked as voice-active by Silero VAD."""
        import logging

        if samples.size < self.SAMPLE_RATE:
            return None
        try:
            import torch
            from silero_vad import get_speech_timestamps

            self._load_vad_model()
            waveform = torch.from_numpy(np.asarray(samples, dtype=np.float32).reshape(-1))
            segments = get_speech_timestamps(
                waveform, self._vad_model, sampling_rate=self.SAMPLE_RATE,
                return_seconds=False,
            )
            active_samples = sum(
                max(0, int(segment["end"]) - int(segment["start"]))
                for segment in segments
            )
            return min(1.0, max(0.0, active_samples / max(1, waveform.numel())))
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "[AUDIO INTELLIGENCE] Silero VAD indisponível; "
                "áudio ficará bloqueado para RVC: %s", exc,
            )
            return None

    def _detect_music_probability(self, samples: np.ndarray) -> float | None:
        """Return an independent AudioSet probability for music/instruments."""
        import logging

        if samples.size < self.SAMPLE_RATE:
            return None
        try:
            import torch

            self._load_content_model()
            sample = samples[: self.SAMPLE_RATE * max(
                2, int(os.getenv("ARMORED_AUDIO_CONTENT_SECONDS", "10"))
            )]
            inputs = self._content_processor(
                sample, sampling_rate=self.SAMPLE_RATE, return_tensors="pt"
            )
            with torch.inference_mode():
                output = self._content_model(**inputs)
                probabilities = torch.softmax(output.logits[0], dim=-1).detach().cpu().numpy()

            id_to_label = getattr(self._content_model.config, "id2label", {}) or {}
            labels = [
                str(id_to_label.get(index, id_to_label.get(str(index), ""))).strip().lower()
                for index in range(len(probabilities))
            ]
            music_terms = (
                "music", "singing", "choir", "musical instrument",
                "musical ensemble", "orchestra", "piano", "guitar", "drum",
                "violin", "cello", "flute", "trumpet", "saxophone",
                "harmonica", "accordion", "harp", "banjo", "mandolin",
                "sitar", "ukulele", "xylophone", "marimba", "organ",
                "percussion", "bassoon", "oboe", "clarinet", "trombone",
                "bagpipes", "didgeridoo", "cymbal",
            )
            music_indices = [
                i for i, label in enumerate(labels)
                if any(term in label for term in music_terms)
            ]
            if not music_indices:
                raise RuntimeError("modelo de áudio não expõe classes de música")
            return min(1.0, max(0.0, float(np.sum(probabilities[music_indices]))))
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "[AUDIO INTELLIGENCE] classificador independente de música indisponível; "
                "áudio ficará bloqueado para RVC: %s", exc,
            )
            return None

    def _decode(self, source: Path) -> tuple[np.ndarray, float]:
        command = [
            self.ffmpeg, "-v", "error", "-i", str(source),
            "-vn", "-ac", "1", "-ar", str(self.SAMPLE_RATE),
            "-t", str(self.MAX_SECONDS), "-f", "s16le", "pipe:1",
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **_windows_hidden_kwargs(),
        )
        raw = result.stdout or b""
        if not raw:
            return np.zeros(0, dtype=np.float32), 0.0
        samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return samples, len(samples) / self.SAMPLE_RATE

    @classmethod
    def _classify_samples(cls, samples: np.ndarray, duration: float) -> AudioIntelligenceResult:
        if samples.size == 0 or duration <= 0:
            return AudioIntelligenceResult(AudioMode.NO_AUDIO, 1.0, duration, 0.0, 0.0)

        rms = float(np.sqrt(np.mean(np.square(samples)) + 1e-12))
        if rms < 0.006:
            return AudioIntelligenceResult(AudioMode.NO_AUDIO, 0.995, duration, 0.0, 0.0)

        frame_count = 1 + max(0, (len(samples) - cls.FRAME) // cls.HOP)
        if frame_count <= 0:
            return AudioIntelligenceResult(AudioMode.MUSIC_ONLY, 0.55, duration, 0.0, 1.0)

        window = np.hanning(cls.FRAME).astype(np.float32)
        speech_votes = 0
        music_votes = 0
        voiced_energy = 0.0
        total_energy = 0.0

        for index in range(frame_count):
            start = index * cls.HOP
            frame = samples[start:start + cls.FRAME]
            if len(frame) < cls.FRAME:
                frame = np.pad(frame, (0, cls.FRAME - len(frame)))
            frame = frame * window

            energy = float(np.mean(frame * frame))
            if energy < 1e-5:
                continue
            total_energy += energy

            spectrum = np.abs(np.fft.rfft(frame)) + 1e-8
            power = spectrum * spectrum
            freqs = np.fft.rfftfreq(cls.FRAME, 1.0 / cls.SAMPLE_RATE)
            spectral_sum = float(np.sum(power)) + 1e-12
            centroid = float(np.sum(freqs * power) / spectral_sum)
            bandwidth = float(
                np.sqrt(np.sum(((freqs - centroid) ** 2) * power) / spectral_sum)
            )
            flatness = float(np.exp(np.mean(np.log(spectrum))) / (np.mean(spectrum) + 1e-12))

            signs = np.signbit(frame)
            zcr = float(np.count_nonzero(signs[1:] != signs[:-1]) / max(1, len(frame) - 1))

            band = (freqs >= 180.0) & (freqs <= 4200.0)
            band_energy = float(np.sum(power[band]) / spectral_sum)

            # Conservative speech evidence: speech tends to occupy a broad
            # mid-band with moderate ZCR and less purely harmonic spectra.
            speech_score = (
                0.40 * (1.0 if 250.0 <= centroid <= 3200.0 else 0.0)
                + 0.20 * (1.0 if 0.025 <= zcr <= 0.22 else 0.0)
                + 0.20 * (1.0 if 0.45 <= band_energy <= 0.98 else 0.0)
                + 0.10 * (1.0 if flatness >= 0.12 else 0.0)
                + 0.10 * (1.0 if bandwidth >= 250.0 else 0.0)
            )

            # Music evidence is strongest for sustained harmonic/tonal energy.
            if bandwidth < 250.0:
                speech_score *= 0.45
            if flatness < 0.05:
                speech_score *= 0.55

            music_score = (
                0.45 * (1.0 if flatness < 0.20 else 0.0)
                + 0.30 * (1.0 if 500.0 <= centroid <= 7000.0 else 0.0)
                + 0.25 * (1.0 if band_energy >= 0.35 else 0.0)
            )

            if speech_score >= 0.62:
                speech_votes += 1
                voiced_energy += energy
            if music_score >= 0.62:
                music_votes += 1

        active_frames = max(1, frame_count)
        speech_ratio = speech_votes / active_frames
        music_ratio = music_votes / active_frames
        energy_speech_ratio = voiced_energy / max(total_energy, 1e-12)

        # Spectral music votes are not independent evidence of background
        # music: voiced speech is harmonic and can satisfy the same heuristic.
        # When speech evidence is strong, classify it as speech and keep the
        # music ratio as diagnostic metadata only. A mixed mode must come from
        # a classifier with independent music evidence, not this heuristic.
        if speech_ratio >= 0.30 and energy_speech_ratio >= 0.18:
            confidence = min(0.75, 0.55 + 0.20 * speech_ratio)
            return AudioIntelligenceResult(
                AudioMode.SPEECH,
                confidence,
                duration,
                speech_ratio,
                music_ratio,
            )

        confidence = min(0.75, 0.55 + 0.20 * max(music_ratio, 0.35))
        return AudioIntelligenceResult(
            AudioMode.MUSIC_ONLY,
            confidence,
            duration,
            speech_ratio,
            music_ratio,
        )

    def _detect_language(self, samples: np.ndarray) -> tuple[str, float]:
        """Detect language with CPU-only faster-whisper, with HF fallback.

        The speech classifier must not depend on a transformers-only model ID
        that may not exist in the local cache. faster-whisper's multilingual
        tiny model is the default and is cached locally after the first load.
        """
        import logging

        logger = logging.getLogger(__name__)
        max_seconds = max(2, int(os.getenv("ARMORED_AUDIO_LANGUAGE_SECONDS", "8")))
        sample = samples[: self.SAMPLE_RATE * max_seconds]
        if sample.size < self.SAMPLE_RATE:
            return "unknown", 0.0
        minimum = float(os.getenv("ARMORED_AUDIO_LANGUAGE_MIN_CONFIDENCE", "0.20"))
        configured_model = os.getenv("ARMORED_AUDIO_LANGUAGE_MODEL", "tiny").strip()
        # Backward compatibility: the previous Transformers default was
        # "openai/whisper-tiny"; map it to faster-whisper's equivalent model.
        faster_model_aliases = {
            "openai/whisper-tiny": "tiny",
            "openai/whisper-base": "base",
            "openai/whisper-small": "small",
            "openai/whisper-medium": "medium",
        }
        model_name = faster_model_aliases.get(configured_model, configured_model)

        try:
            if getattr(self, "_faster_whisper_failed_name", None) == model_name:
                raise RuntimeError("faster-whisper model previously unavailable")
            from faster_whisper import WhisperModel

            if (
                getattr(self, "_language_model_name", None) != model_name
                or getattr(self, "_language_model", None) is None
            ):
                cache_dir = os.getenv(
                    "ARMORED_AUDIO_MODEL_CACHE",
                    str(Path("storage") / "models" / "audio"),
                )
                compute_type = os.getenv("ARMORED_AUDIO_COMPUTE_TYPE", "int8")
                self._language_model_name = model_name
                self._language_model = WhisperModel(
                    model_name,
                    device="cpu",
                    compute_type=compute_type,
                    download_root=cache_dir,
                )
            segments, info = self._language_model.transcribe(
                sample,
                beam_size=1,
                vad_filter=False,
            )
            # Force the lazy transcription generator to execute. Language
            # metadata alone is useful, but forcing decoding makes failures
            # visible here rather than later in the Studio pipeline.
            next(iter(segments), None)
            language = str(getattr(info, "language", "unknown") or "unknown").lower()
            confidence = float(getattr(info, "language_probability", 0.0) or 0.0)
            if confidence < minimum:
                return "unknown", confidence
            return language, confidence
        except Exception as faster_exc:
            self._faster_whisper_failed_name = model_name
            logger.warning(
                "[AUDIO INTELLIGENCE] faster-whisper indisponível; tentando fallback "
                "Transformers: %s",
                faster_exc,
            )

        model_name = os.getenv(
            "ARMORED_AUDIO_LANGUAGE_MODEL_HF",
            configured_model if configured_model.startswith("openai/")
            else "openai/whisper-tiny",
        )
        if getattr(self, "_hf_language_failed_name", None) == model_name:
            return "unknown", 0.0
        try:
            import torch
            from transformers import AutoModelForSpeechSeq2Seq, AutoProcessor

            if (
                getattr(self, "_hf_language_model_name", None) != model_name
                or getattr(self, "_hf_language_model", None) is None
            ):
                processor = AutoProcessor.from_pretrained(model_name)
                model = AutoModelForSpeechSeq2Seq.from_pretrained(model_name)
                model.to("cpu")
                model.eval()
                self._hf_language_model_name = model_name
                self._hf_language_processor = processor
                self._hf_language_model = model

            processor = self._hf_language_processor
            model = self._hf_language_model
            features = processor(
                sample,
                sampling_rate=self.SAMPLE_RATE,
                return_tensors="pt",
            ).input_features
            decoder_start = int(model.config.decoder_start_token_id)
            decoder_input_ids = torch.tensor([[decoder_start]], dtype=torch.long)
            with torch.inference_mode():
                output = model(
                    input_features=features,
                    decoder_input_ids=decoder_input_ids,
                )
                logits = output.logits[0, -1]

            # Whisper's vocabulary contains task/control tokens such as
            # <|nocaptions|>, <|transcribe|> and <|notimestamps|>. Never
            # choose a language by argmax over the entire vocabulary.
            tokenizer = processor.tokenizer
            language_ids = getattr(tokenizer, "lang_code_to_id", None)
            if not isinstance(language_ids, dict) or not language_ids:
                language_ids = {}
                vocab = tokenizer.get_vocab()
                for token_text, token_id in vocab.items():
                    match = re.fullmatch(r"<\|([a-z]{2,3})\|>", str(token_text))
                    if match:
                        language_ids[match.group(1)] = int(token_id)
            if not language_ids:
                raise RuntimeError("tokenizer não expõe tokens de idioma Whisper")

            valid_ids = sorted({int(token_id) for token_id in language_ids.values()})
            valid_logits = logits[valid_ids]
            probabilities = torch.softmax(valid_logits, dim=-1)
            best_position = int(torch.argmax(probabilities).item())
            language_id = valid_ids[best_position]
            token = tokenizer.convert_ids_to_tokens(language_id)
            confidence = float(probabilities[best_position].item())
            language = str(token)[2:-2].lower() if str(token).startswith("<|") else "unknown"
            if language not in {str(code).lower() for code in language_ids}:
                language = "unknown"
            if confidence < minimum:
                return "unknown", confidence
            return language, confidence
        except Exception as exc:
            self._hf_language_failed_name = model_name
            logger.warning(
                "[AUDIO INTELLIGENCE] detecção de idioma indisponível; "
                "RVC será desativado por segurança: %s",
                exc,
            )
            return "unknown", 0.0

    def analyze(self, source: Path) -> AudioIntelligenceResult:
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(source)
        samples, duration = self._decode(source)
        spectral = self._classify_samples(samples, duration)
        if spectral.mode == AudioMode.NO_AUDIO:
            return spectral

        speech_activity_ratio = self._detect_speech_activity(samples)
        music_probability = self._detect_music_probability(samples)
        method = "silero-vad+audioset-music+whisper-language-v1"
        if speech_activity_ratio is None or music_probability is None:
            return AudioIntelligenceResult(
                mode=AudioMode.SPEECH_UNVERIFIED,
                confidence=0.0,
                duration_seconds=duration,
                speech_ratio=spectral.speech_ratio,
                music_ratio=spectral.music_ratio,
                method="audio-content-unavailable",
                language="unknown",
                language_confidence=0.0,
                speech_probability=None,
                music_probability=music_probability,
                speech_activity_ratio=speech_activity_ratio,
            )

        speech_threshold = float(
            os.getenv("ARMORED_AUDIO_VAD_MIN_ACTIVITY_RATIO", "0.08")
        )
        music_threshold = float(
            os.getenv("ARMORED_AUDIO_CONTENT_MUSIC_THRESHOLD", "0.25")
        )
        speech_present = speech_activity_ratio >= speech_threshold
        music_present = music_probability >= music_threshold

        # Do not ask Whisper to infer a language for audio independently
        # classified as music or as inconclusive: language-ID models can
        # hallucinate languages on instrumental tracks.
        if not speech_present:
            mode = AudioMode.MUSIC_ONLY if music_present else AudioMode.SPEECH_UNVERIFIED
            return AudioIntelligenceResult(
                mode=mode,
                confidence=max(speech_activity_ratio, music_probability),
                duration_seconds=duration,
                speech_ratio=spectral.speech_ratio,
                music_ratio=spectral.music_ratio,
                method=method,
                language="unknown",
                language_confidence=0.0,
                speech_probability=None,
                music_probability=music_probability,
                speech_activity_ratio=speech_activity_ratio,
            )

        language, language_confidence = self._detect_language(samples)
        minimum_language_confidence = float(
            os.getenv("ARMORED_AUDIO_LANGUAGE_MIN_CONFIDENCE", "0.20")
        )
        portuguese_verified = (
            language == "pt"
            and language_confidence >= max(minimum_language_confidence, 0.65)
        )
        foreign_verified = (
            language not in {"pt", "unknown"}
            and language_confidence >= max(minimum_language_confidence, 0.40)
        )

        if portuguese_verified:
            mode = AudioMode.SPEECH_PLUS_MUSIC if music_present else AudioMode.SPEECH
        elif foreign_verified:
            mode = AudioMode.FOREIGN_SPEECH
        else:
            mode = AudioMode.SPEECH_UNVERIFIED

        return AudioIntelligenceResult(
            mode=mode,
            confidence=max(speech_activity_ratio, music_probability),
            duration_seconds=duration,
            speech_ratio=spectral.speech_ratio,
            music_ratio=spectral.music_ratio,
            method=method,
            language=language,
            language_confidence=language_confidence,
            speech_probability=None,
            music_probability=music_probability,
            speech_activity_ratio=speech_activity_ratio,
        )
