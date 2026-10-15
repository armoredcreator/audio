"""
ARMOREDSTUDIO - VEO 1 + VEO 2 DETECTORS

VEO 1 is the preserved detector for the original watermark style.
VEO 2 is a separate supplemental detector for the newer translucent mark.

VEO 1 FAST:
- Analisa somente o frame 0.
- Não faz seeking.
- Usa a mesma comparação validada no detector anterior.
- ROI fixa proporcional à resolução.
- Template VEO embutido.
- Top-hat preprocessing.
- Threshold de decisão: 0.70.

VEO 1 FULL:
- Mantém a estratégia anterior de múltiplos frames.
- Disponível com --full.

The public FAST/FULL entry points combine both detectors without allowing VEO 2
to clear a positive result from VEO 1.
"""

import argparse
import base64
import io
from pathlib import Path

import cv2
import numpy as np


# ============================================================
# TEMPLATE VEO
# ============================================================

TEMPLATE_B64 = """iVBORw0KGgoAAAANSUhEUgAAACkAAAAUCAAAAAA170tZAAABpklEQVQoFY3BQUhTcRzA8e9/P96fvzx5NBidFMSQRhAMi0gWQihBB4WgS2AJjcC6hR06lBB0qC4Kop6UHTt5CqIuQRR0cQWGENpgOBDG6tGD4fiPN9rCvTcLRp+PGvZrMDowbAS/tBdUa7RoI4d1uhlPTVC3uP1Ow5ofwTdLW4rDGsd4KU/NJOvVoNwIAkuH61Y4zk0ZT82dqJdKBbqYZhhyxNRpkZM6FDWXqBSKxIwOiGgsIJ5DLVTXq1+rxMRphHRoLC1GEzasurJTpifthA3EoiYKPm37K8+An1Mf+Ys4TUKxqOxuhbaZR2kYfz7GvwSxoLI7Pm3e5/M+7zaXuTb56jUwn/VzdFMDByF/vHy/yvdTfNj4srJ3ky1n6ezt9AERUXTMPhm6Mz01m7kPvwZH80Pw9OIkMUVkf/DNg+3FTNH2J27duJyD6RdpOgRFJO9mRng4kqMlu3gB1k9f4oiAInI1v3kXdtffzqfPsWWXxu+NfSKmiC1slIE12V4GFs6Ej4tEBMX/MX2K3kQct2m1TSYVvRmtHRGMTip6ESQhnminj8RvnmSFdGgXGFcAAAAASUVORK5CYII="""


# ============================================================
# CONFIGURAÇÃO
# ============================================================

ROI_X0 = 0.76
ROI_Y0 = 0.84

POSITIVE_THRESHOLD = 0.70
NEGATIVE_THRESHOLD = 0.45

AMBIGUOUS_EXTRA_FRAMES = (0.10, 0.50)

BASE_WIDTH = 720.0
BASE_TEMPLATE_WIDTH = 41
BASE_TEMPLATE_HEIGHT = 20

TOPHAT_KERNEL_720 = 15

# VEO 2 is a separate supplemental detector. VEO 1 keeps its existing
# template, preprocessing, ROI and decision thresholds above unchanged.
VEO2_ROI_X0 = 850 / 1080
VEO2_ROI_Y0 = 1770 / 1920
VEO2_BASE_WIDTH = 1080
VEO2_TEMPLATE_WIDTH = 110
VEO2_TEMPLATE_HEIGHT = 60
VEO2_FRAME_POINTS = (0.0, 0.50, 0.95)
VEO2_FRAME_THRESHOLD = 0.78
VEO2_SINGLE_FRAME_THRESHOLD = 0.94
VEO2_TEMPLATE_B64 = "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"


# ============================================================
# TEMPLATE
# ============================================================

def load_template():
    data = base64.b64decode(TEMPLATE_B64)

    array = np.frombuffer(data, dtype=np.uint8)

    template = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)

    if template is None:
        raise RuntimeError("Não foi possível carregar o template VEO.")

    return template


def load_template_veo2():
    """Load the high-pass reference for the newer, translucent Veo wordmark."""
    data = base64.b64decode(VEO2_TEMPLATE_B64)
    array = np.frombuffer(data, dtype=np.uint8)
    template = cv2.imdecode(array, cv2.IMREAD_GRAYSCALE)
    if template is None:
        raise RuntimeError("Não foi possível carregar o template VEO 2.")
    return template


# ============================================================
# INFORMAÇÕES DO VÍDEO
# ============================================================

def video_info(path):
    cap = cv2.VideoCapture(str(path))

    if not cap.isOpened():
        raise RuntimeError(f"Não foi possível abrir o vídeo: {path}")

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    cap.release()

    return frame_count, fps, width, height


# ============================================================
# TOP-HAT
# ============================================================

def build_tophat_kernel(width):
    scale = width / BASE_WIDTH

    size = int(round(TOPHAT_KERNEL_720 * scale))

    if size < 3:
        size = 3

    if size % 2 == 0:
        size += 1

    return cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (size, size),
    )


def apply_tophat(gray, kernel):
    return cv2.morphologyEx(
        gray,
        cv2.MORPH_TOPHAT,
        kernel,
    )


# ============================================================
# PREPARAÇÃO DO TEMPLATE
# ============================================================

def prepare_template(template_gray, width):
    scale = width / BASE_WIDTH

    target_width = max(
        1,
        int(round(BASE_TEMPLATE_WIDTH * scale)),
    )

    target_height = max(
        1,
        int(round(BASE_TEMPLATE_HEIGHT * scale)),
    )

    resized = cv2.resize(
        template_gray,
        (target_width, target_height),
        interpolation=cv2.INTER_AREA,
    )

    kernel = build_tophat_kernel(width)

    prepared = apply_tophat(
        resized,
        kernel,
    )

    return prepared


# ============================================================
# LEITURA DE FRAME
# ============================================================

def read_frame(cap, frame_number):
    cap.set(
        cv2.CAP_PROP_POS_FRAMES,
        int(frame_number),
    )

    ok, frame = cap.read()

    if not ok or frame is None:
        return None

    return frame


# ============================================================
# SCORE
# ============================================================

def score_frame(frame, template_gray):
    height, width = frame.shape[:2]

    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY,
    )

    kernel = build_tophat_kernel(width)

    processed = apply_tophat(
        gray,
        kernel,
    )

    template = prepare_template(
        template_gray,
        width,
    )

    x0 = int(width * ROI_X0)
    y0 = int(height * ROI_Y0)

    roi = processed[
        y0:height,
        x0:width,
    ]

    template_height, template_width = template.shape[:2]

    if (
        roi.shape[1] < template_width
        or roi.shape[0] < template_height
    ):
        return -1.0, (0, 0, template_width, template_height)

    result = cv2.matchTemplate(
        roi,
        template,
        cv2.TM_CCOEFF_NORMED,
    )

    _, max_score, _, max_location = cv2.minMaxLoc(result)

    location = (
        x0 + max_location[0],
        y0 + max_location[1],
        template_width,
        template_height,
    )

    return float(max_score), location


# ============================================================
# FAST
# ============================================================

def detect_veo1_fast(path):
    template = load_template()

    cap = cv2.VideoCapture(str(path))

    if not cap.isOpened():
        raise RuntimeError(
            f"Não foi possível abrir o vídeo: {path}"
        )

    ok, frame = cap.read()

    cap.release()

    if not ok or frame is None:
        raise RuntimeError(
            f"Não foi possível ler o frame 0: {path}"
        )

    score, location = score_frame(
        frame,
        template,
    )

    return {
        "found": score >= POSITIVE_THRESHOLD,
        "best_score": score,
        "scores": [(0, score)],
        "locations": [(0, location)],
        "frames_analyzed": 1,
    }


# ============================================================
# FULL
# ============================================================

def detect_veo1_full(path):
    template = load_template()

    frame_count, fps, width, height = video_info(path)

    cap = cv2.VideoCapture(str(path))

    if not cap.isOpened():
        raise RuntimeError(
            f"Não foi possível abrir o vídeo: {path}"
        )

    scores = []
    locations = []

    def analyze(frame_number):
        frame = read_frame(
            cap,
            frame_number,
        )

        if frame is None:
            return None

        score, location = score_frame(
            frame,
            template,
        )

        scores.append(
            (frame_number, score)
        )

        locations.append(
            (frame_number, location)
        )

        return score

    # --------------------------------------------------------
    # Frame 0
    # --------------------------------------------------------

    first_score = analyze(0)

    if first_score is None:
        # Fallback exatamente no espírito do detector anterior.
        for frame_number in (1, 2, 3, 5):
            score = analyze(frame_number)

            if score is not None and score >= POSITIVE_THRESHOLD:
                break

    else:
        if first_score >= POSITIVE_THRESHOLD:
            pass

        elif first_score <= NEGATIVE_THRESHOLD:
            if frame_count > 1:
                analyze(
                    max(
                        0,
                        frame_count // 2,
                    )
                )

        else:
            for point in AMBIGUOUS_EXTRA_FRAMES:
                frame_number = int(
                    frame_count * point
                )

                if frame_number >= frame_count:
                    frame_number = frame_count - 1

                analyze(frame_number)

    cap.release()

    if not scores:
        raise RuntimeError(
            f"Nenhum frame pôde ser analisado: {path}"
        )

    best_frame, best_score = max(
        scores,
        key=lambda item: item[1],
    )

    return {
        "found": best_score >= POSITIVE_THRESHOLD,
        "best_score": best_score,
        "scores": scores,
        "locations": locations,
        "frames_analyzed": len(scores),
        "best_frame": best_frame,
        "frame_count": frame_count,
        "fps": fps,
        "width": width,
        "height": height,
    }


# ============================================================
# VEO 2 — DETECTOR COMPLEMENTAR
# ============================================================

def _veo2_sample_frames(path):
    """Read a small, evenly distributed set of frames for VEO 2."""
    frame_count, fps, width, height = video_info(path)
    if frame_count <= 0:
        raise RuntimeError(f"Vídeo sem frames válidos: {path}")

    frame_numbers = sorted({
        min(frame_count - 1, max(0, int(round((frame_count - 1) * point))))
        for point in VEO2_FRAME_POINTS
    })
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"Não foi possível abrir o vídeo: {path}")

    frames = []
    try:
        for frame_number in frame_numbers:
            frame = read_frame(cap, frame_number)
            if frame is not None:
                frames.append((frame_number, frame))
    finally:
        cap.release()

    if not frames:
        raise RuntimeError(f"Nenhum frame pôde ser analisado: {path}")
    return frames, {
        "frame_count": frame_count,
        "fps": fps,
        "width": width,
        "height": height,
    }


def score_frame_veo2(frame, template_gray):
    """Match the newer translucent Veo wordmark after local high-pass filtering."""
    height, width = frame.shape[:2]
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    x0 = int(width * VEO2_ROI_X0)
    y0 = int(height * VEO2_ROI_Y0)
    roi = gray[y0:height, x0:width]

    scale = width / VEO2_BASE_WIDTH
    target_width = max(1, int(round(VEO2_TEMPLATE_WIDTH * scale)))
    target_height = max(1, int(round(VEO2_TEMPLATE_HEIGHT * scale)))
    template = cv2.resize(
        template_gray,
        (target_width, target_height),
        interpolation=cv2.INTER_AREA,
    )
    if roi.shape[0] < target_height or roi.shape[1] < target_width:
        return -1.0, (0, 0, target_width, target_height), "none"

    sigma = max(1.0, 10.0 * scale)
    background = cv2.GaussianBlur(roi, (0, 0), sigma)
    highpass = cv2.subtract(roi, background)
    response = cv2.matchTemplate(
        highpass,
        template,
        cv2.TM_CCOEFF_NORMED,
    )
    _, score, _, location = cv2.minMaxLoc(response)
    return (
        float(score),
        (x0 + location[0], y0 + location[1], target_width, target_height),
        "highpass",
    )


def detect_veo2(path):
    """VEO 2 scans frames for the newer translucent Veo watermark style."""
    template = load_template_veo2()
    frames, metadata = _veo2_sample_frames(path)
    scores = []
    locations = []
    modes = []
    for frame_number, frame in frames:
        score, location, mode = score_frame_veo2(frame, template)
        scores.append((frame_number, score))
        locations.append((frame_number, location))
        modes.append((frame_number, mode))

    best_index = max(range(len(scores)), key=lambda index: scores[index][1])
    best_frame, best_score = scores[best_index]
    best_location = locations[best_index][1]
    supporting = [
        (frame, score)
        for frame, score in scores
        if score >= VEO2_FRAME_THRESHOLD
    ]
    # A strong single-frame match handles very short clips; otherwise require
    # the same watermark template to recur across sampled frames.
    found = (
        best_score >= VEO2_SINGLE_FRAME_THRESHOLD
        or (best_score >= VEO2_FRAME_THRESHOLD and len(supporting) >= 2)
    )
    return {
        "found": found,
        "best_score": best_score,
        "scores": scores,
        # The planner needs the box from the strongest frame, not whichever
        # sampled frame happened to appear first in the clip.
        "locations": [(best_frame, best_location)],
        "sampled_locations": locations,
        "frames_analyzed": len(scores),
        "best_frame": best_frame,
        "best_mode": modes[best_index][1],
        "supporting_frames": supporting,
        **metadata,
    }


def _combine_veo_results(veo1, veo2, mode):
    """Keep every VEO 1 positive and let VEO 2 add missed positive matches."""
    if veo1["found"]:
        selected = veo1
        detector = "veo1"
    elif veo2["found"]:
        selected = veo2
        detector = "veo2"
    else:
        selected = veo1
        detector = "none"

    combined = dict(selected)
    combined["found"] = bool(veo1["found"] or veo2["found"])
    combined["detector"] = detector
    combined["detectors"] = {"veo1": veo1, "veo2": veo2}
    combined["mode"] = mode
    return combined


def detect_fast(path):
    """Public FAST entry point: preserve VEO 1 and add VEO 2 as a second check."""
    veo1 = detect_veo1_fast(path)
    veo2 = detect_veo2(path)
    return _combine_veo_results(veo1, veo2, "fast")


def detect_full(path):
    """Public FULL entry point: run both detectors and combine their evidence."""
    veo1 = detect_veo1_full(path)
    veo2 = detect_veo2(path)
    return _combine_veo_results(veo1, veo2, "full")


# ============================================================
# IMPRESSÃO
# ============================================================

def print_result(path, result, mode):
    print(f"\n[{Path(path).name}]")

    if mode == "fast":
        print("Analisando VEO 1 no frame 0 + VEO 2 em quadros distribuídos...")

    else:
        print("Analisando VEO 1 FULL + VEO 2 em quadros distribuídos...")

    print(
        f"Frames analisados: "
        f"{result['frames_analyzed']}"
    )

    score_text = ", ".join(
        f"frame {frame}={score:.3f}"
        for frame, score in result["scores"]
    )

    print(f"Scores: {score_text}")

    best_score = result["best_score"]

    print(
        f"Melhor score: {best_score:.3f}"
    )

    if "detectors" in result:
        for detector_name, detector_result in result["detectors"].items():
            print(
                f"{detector_name.upper()}: "
                f"{'VEO encontrada' if detector_result['found'] else 'sem VEO'} "
                f"(score={detector_result['best_score']:.3f}, "
                f"frames={detector_result['frames_analyzed']})"
            )
        print(f"Detector selecionado: {result['detector']}")

    for frame, location in result["locations"]:
        if frame == result["scores"][
            max(
                range(len(result["scores"])),
                key=lambda i: result["scores"][i][1],
            )
        ][0]:
            print(
                f"Melhor localização: {location}"
            )
            break

    print(
        "RESULTADO FINAL: "
        + (
            "VEO ENCONTRADA."
            if result["found"]
            else "VEO NÃO ENCONTRADA."
        )
    )


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Detector VEO - ArmoredStudio V1"
    )

    parser.add_argument(
        "--full",
        action="store_true",
        help="Executa o detector FULL.",
    )

    parser.add_argument(
        "videos",
        nargs="+",
        help="Vídeos para analisar.",
    )

    args = parser.parse_args()

    mode = "full" if args.full else "fast"

    resultados = []

    for video in args.videos:
        path = Path(video)

        if not path.exists():
            print(
                f"\n[{path.name}]"
            )
            print(
                f"ERRO: arquivo não encontrado: {path}"
            )
            continue

        try:
            if mode == "fast":
                result = detect_fast(path)
            else:
                result = detect_full(path)

            print_result(
                path,
                result,
                mode,
            )

            resultados.append(
                (
                    path.name,
                    result["found"],
                )
            )

        except Exception as exc:
            print(
                f"\n[{path.name}]"
            )
            print(
                f"ERRO: {exc}"
            )

    print("\n==============================")

    if mode == "fast":
        print("RESUMO VEO — FAST")
    else:
        print("RESUMO VEO — FULL")

    print("==============================")

    for name, found in resultados:
        print(
            f"{name}: "
            + (
                "VEO ENCONTRADA"
                if found
                else "SEM VEO"
            )
        )


if __name__ == "__main__":
    main()
