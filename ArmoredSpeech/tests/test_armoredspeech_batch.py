import hashlib
import sqlite3
from pathlib import Path

from ArmoredSpeech.batch import SpeechItem, run_bulk, wav_path_for
from ArmoredSpeech.contracts import AudioClass, SpeechDecision


class FakeProcessor:
    def __init__(self, labels):
        self.labels = labels
        self.order = []

    def analyze(self, video_path):
        self.order.append(video_path.name)
        return self.labels[video_path.name]

    def prepare_wav(self, video_path, wav_path, decision):
        wav_path.write_bytes(b"fake-wav")


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_bulk_keeps_originals_and_prepares_sidecars_for_all_audio_classes(tmp_path):
    source1 = tmp_path / "Videos GRUPO_FONTE_1" / "291"
    source2 = tmp_path / "Videos GRUPO_FONTE_2" / "1046"
    source1.mkdir(parents=True)
    source2.mkdir(parents=True)
    music = source1 / "291_finallinkoriginal.mp4"
    narration = source2 / "1046_finallinkoriginal.mp4"
    music.write_bytes(b"original-music-video")
    narration.write_bytes(b"original-narration-video")
    hashes = {music: digest(music), narration: digest(narration)}

    labels = {
        music.name: SpeechDecision(
            AudioClass.MUSIC_ONLY, None, False, True, 0.99, "reviewed label"
        ),
        narration.name: SpeechDecision(
            AudioClass.PT_BR_NARRATION, "pt-br", True, False, 0.98, "reviewed label"
        ),
    }
    processor = FakeProcessor(labels)
    database = tmp_path / "state" / "speech.sqlite3"
    result = run_bulk(
        [
            SpeechItem("2", "1046", narration),
            SpeechItem("1", "291", music),
        ],
        database=database,
        processor=processor,
    )

    assert result == {"prepared": 2, "rvc_eligible": 1, "blocked": 1, "failed": 0}
    assert processor.order == ["291_finallinkoriginal.mp4", "1046_finallinkoriginal.mp4"]
    assert digest(music) == hashes[music]
    assert digest(narration) == hashes[narration]
    assert wav_path_for(music).name == "291_finallinkoriginal.wav"
    assert wav_path_for(narration).name == "1046_finallinkoriginal.wav"
    assert wav_path_for(music).is_file()
    assert wav_path_for(narration).is_file()

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT source_id, item_id, audio_class, rvc_eligible, status "
            "FROM armored_speech_results ORDER BY source_id, item_id"
        ).fetchall()
    assert rows == [
        ("1", "291", "MUSIC_ONLY", 0, "READY"),
        ("2", "1046", "PT_BR_NARRATION", 1, "READY"),
    ]


def test_unknown_classification_is_blocked_without_wav(tmp_path):
    video = tmp_path / "291_finallinkoriginal.mp4"
    video.write_bytes(b"original")
    processor = FakeProcessor({
        video.name: SpeechDecision(
            AudioClass.UNKNOWN, None, False, None, None, "not sure"
        )
    })
    result = run_bulk(
        [SpeechItem("1", "291", video)],
        database=tmp_path / "state.sqlite3",
        processor=processor,
    )
    assert result == {"prepared": 0, "rvc_eligible": 0, "blocked": 1, "failed": 0}
    assert not wav_path_for(video).exists()
