from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("cv2", reason="Gemini visual detector requires OpenCV")

from ArmoredStudio.analysis import export_planner, gemini_detector


class FakeCapture:
    def isOpened(self):
        return True

    def read(self):
        return True, np.zeros((720, 720, 3), dtype=np.uint8)

    def release(self):
        pass


def test_gemini_template_supports_a_smaller_variant():
    large = gemini_detector.build_gemini_shape_template(720)
    small = gemini_detector.build_gemini_shape_template(
        720, template_size_720=gemini_detector.FAST_GEMINI2_TEMPLATE_SIZE_720
    )
    assert large.shape == (61, 61)
    assert small.shape == (41, 41)


def test_fast_detector_keeps_gemini1_and_adds_gemini2(monkeypatch):
    cap = FakeCapture()
    monkeypatch.setattr(gemini_detector, "open_video", lambda _path: cap)
    monkeypatch.setattr(gemini_detector, "get_video_info", lambda _cap: (720, 720, 30.0, 12))
    calls = []

    def fake_match(_frame, template_size_720=61.0):
        calls.append(template_size_720)
        if template_size_720 == gemini_detector.FAST_STAR_TEMPLATE_SIZE_720:
            return 0.20, 600, 650
        return 0.52, 600, 650

    monkeypatch.setattr(gemini_detector, "match_gemini_shape_fast", fake_match)
    result = gemini_detector.detect_fast("synthetic.mp4")

    assert calls == [61.0, 41.0]
    assert result["gemini"] is True
    assert result["detector"] == "gemini2"
    assert result["template_size_720"] == 41.0
    assert result["detectors"]["gemini1"]["gemini"] is False
    assert result["detectors"]["gemini2"]["gemini"] is True


def test_planner_uses_winning_gemini2_scale():
    box = export_planner._box_from_center({
        "gemini": True,
        "detector": "gemini2",
        "x": 600,
        "y": 650,
        "width": 720,
        "height": 720,
        "template_size_720": 41.0,
        "detectors": {
            "gemini1": {
                "gemini": False,
                "template_size_720": 61.0,
                "x": 600, "y": 650, "width": 720, "height": 720,
            },
            "gemini2": {
                "gemini": True,
                "template_size_720": 41.0,
                "x": 600, "y": 650, "width": 720, "height": 720,
            },
        },
    })
    assert box == (580, 630, 41, 41)
