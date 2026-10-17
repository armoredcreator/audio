from __future__ import annotations

from ArmoredStudio.processing.finalizer import criar_filtro


def _graph(*, audio_start: float) -> str:
    return criar_filtro(
        position="final",
        largura=1080,
        altura=1920,
        fps="30/1",
        plan={"audio_start": audio_start},
    )


def test_audio_start_trims_only_voice_and_keeps_main_effect_at_zero():
    graph = _graph(audio_start=1.25)

    # Current behavior: the visual plan trims/re-times the voice input only.
    assert "[1:a]atrim=start=1.25,asetpts=PTS-STARTPTS,volume=1.0[voice]" in graph
    # The main effect has its established normalization/gain, but is not trimmed
    # by audio_start. Its timeline begins at the start of the main segment.
    assert "[2:a]loudnorm=I=-12:LRA=7:TP=-1,volume=0.8[music]" in graph
    assert graph.count("atrim=start=1.25") == 1


def test_intro_effect_keeps_its_own_fade_and_two_second_window():
    graph = _graph(audio_start=1.25)

    # The intro is a distinct timeline segment; do not propagate audio_start
    # into its dedicated effect fade/trim.
    assert (
        "[2:a]afade=t=in:st=0:d=0.4,afade=t=out:st=1.5:d=0.5,"
        "loudnorm=I=-12:LRA=7:TP=-1,volume=0.8,"
        "atrim=duration=2.0,asetpts=PTS-STARTPTS[intro_a]"
    ) in graph


def test_zero_audio_start_does_not_introduce_a_temporal_trim():
    graph = _graph(audio_start=0.0)

    assert "atrim=start=0" not in graph
    assert "[1:a]volume=1.0[voice]" in graph
    assert "[2:a]loudnorm=I=-12:LRA=7:TP=-1,volume=0.8[music]" in graph
