from armoredspeech.contracts import AudioClass, SpeechDecision


def decision(audio_class, language, speech, music=None):
    return SpeechDecision(audio_class, language, speech, music, 0.95, "test")


def test_only_portuguese_narration_is_rvc_eligible():
    assert decision(AudioClass.PT_BR_NARRATION, "pt", True).rvc_eligible
    assert decision(AudioClass.PT_BR_NARRATION_WITH_MUSIC, "pt-br", True, True).rvc_eligible


def test_music_silence_noise_and_foreign_speech_are_not_rvc_eligible():
    for audio_class, language, speech in [
        (AudioClass.MUSIC_ONLY, None, False),
        (AudioClass.NO_AUDIO, None, False),
        (AudioClass.NO_USABLE_NARRATION, None, False),
        (AudioClass.FOREIGN_SPEECH, "en", True),
        (AudioClass.UNKNOWN, None, False),
    ]:
        assert not decision(audio_class, language, speech).rvc_eligible


def test_foreign_speech_cannot_be_mislabeled_as_portuguese():
    item = decision(AudioClass.FOREIGN_SPEECH, "pt", True)
    try:
        item.validate()
    except ValueError:
        pass
    else:
        raise AssertionError("expected inconsistent label to be rejected")
