import pytest
from ArmoredSpeech import rvc_engine


def test_rvc_refuses_input_as_output_before_deleting_any_bytes(tmp_path):
    source = tmp_path / "original.wav"
    source.write_bytes(b"original source bytes")
    with pytest.raises(FileExistsError):
        rvc_engine.converter_voz(source, source)
    assert source.read_bytes() == b"original source bytes"


def test_rvc_public_and_internal_entrypoints_preserve_existing_result(tmp_path):
    source = tmp_path / "original.wav"
    result = tmp_path / "result.wav"
    source.write_bytes(b"original")
    result.write_bytes(b"prior valid result")
    with pytest.raises(FileExistsError):
        rvc_engine.converter_voz(source, result)
    with pytest.raises(FileExistsError):
        rvc_engine.converter_interno(source, result, tmp_path / "model.pth", None)
    assert source.read_bytes() == b"original"
    assert result.read_bytes() == b"prior valid result"
