from __future__ import annotations

import argparse
import csv
import math
import os
import sys
from dataclasses import dataclass
from typing import Iterable, Optional

import cv2
import numpy as np


# ============================================================
# CONFIGURAÇÃO
# ============================================================

SAMPLE_COUNT = 30

ROI_X0 = 0.55
ROI_Y0 = 0.70

EXPECTED_X = 0.83
EXPECTED_Y = 0.91

CLUSTER_RADIUS_720 = 25.0

MATCH_THRESHOLD = 0.45

TEMPLATE_SIZE_720 = 61
SEARCH_RADIUS_720 = 55

FULL_DETECTED_PERCENT = 40.0


# ============================================================
# FAST 1 FRAME
# ============================================================
#
# A estrela Gemini está presente desde o início dos vídeos.
#
# O FAST analisa SOMENTE o frame 0.
#
# Não usa:
# - frame de fallback
# - 30 frames
# - calibração
# - confirmação entre frames
# - template aprendido do vídeo
# - gemini_star.png
#
# A posição observada da estrela é aproximadamente:
# X = 83.3%
# Y = 90.6%
#
# O detector procura diretamente uma forma geométrica
# semelhante à estrela de 4 pontas dentro de uma pequena
# região ao redor da posição esperada.
# ============================================================

FAST_ONE_FRAME_POINT = 0.00

FAST_STAR_CENTER_X = 0.833
FAST_STAR_CENTER_Y = 0.906

FAST_STAR_TEMPLATE_SIZE_720 = 61.0
FAST_STAR_SEARCH_RADIUS_720 = 35.0

FAST_STAR_MATCH_THRESHOLD = 0.43


# ============================================================
# CONFIGURAÇÃO LEGADA / FULL
# ============================================================

BASE_WIDTH = 720.0

# Descoberta de candidato usada pelo FULL.

CANDIDATE_MIN_DIM_720 = 12.0
CANDIDATE_MIN_AREA_720 = 45.0

CANDIDATE_MAX_DIM_720 = 100.0
CANDIDATE_MAX_AREA_720 = 3200.0

CANDIDATE_CENTER_X_MIN = 0.70
CANDIDATE_CENTER_X_MAX = 0.96
CANDIDATE_CENTER_Y_MIN = 0.82
CANDIDATE_CENTER_Y_MAX = 0.98

POSITION_X_MIN = 0.74
POSITION_X_MAX = 0.92
POSITION_Y_MIN = 0.85
POSITION_Y_MAX = 0.95

CANDIDATE_ASPECT_MIN = 0.35
CANDIDATE_ASPECT_MAX = 2.80

TOPHAT_KERNEL_720 = 31.0


# ============================================================
# ESTRUTURAS
# ============================================================

@dataclass
class Candidate:
    x: float
    y: float
    width: float
    height: float
    area: float
    score: float = 0.0


@dataclass
class FramePosition:
    frame_index: int
    x: float
    y: float
    candidate: Candidate


@dataclass
class Confirmation:
    frame_a: int
    frame_b: int
    score_ab: float
    score_ba: float
    score: float


# ============================================================
# UTILITÁRIOS
# ============================================================

def scale_value(
    value: float,
    width: int,
) -> float:
    return (
        value
        * float(width)
        / BASE_WIDTH
    )


def frame_distance(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    width: int,
) -> float:
    scale = (
        float(width)
        / BASE_WIDTH
    )

    dx = (
        x1 - x2
    ) / scale

    dy = (
        y1 - y2
    ) / scale

    return math.sqrt(
        dx * dx
        + dy * dy
    )


def same_position(
    p1: Candidate,
    p2: Candidate,
    width: int,
) -> bool:
    return (
        frame_distance(
            p1.x,
            p1.y,
            p2.x,
            p2.y,
            width,
        )
        <= CLUSTER_RADIUS_720
    )


def clamp(
    value: int,
    minimum: int,
    maximum: int,
) -> int:
    return max(
        minimum,
        min(
            value,
            maximum,
        ),
    )


# ============================================================
# FRAME READER
# ============================================================

def open_video(
    path: str,
) -> cv2.VideoCapture:
    cap = cv2.VideoCapture(
        path
    )

    if not cap.isOpened():
        raise RuntimeError(
            f"Não foi possível abrir o vídeo: {path}"
        )

    return cap


def get_video_info(
    cap: cv2.VideoCapture,
) -> tuple[int, int, float, int]:

    width = int(
        cap.get(
            cv2.CAP_PROP_FRAME_WIDTH
        )
    )

    height = int(
        cap.get(
            cv2.CAP_PROP_FRAME_HEIGHT
        )
    )

    fps = float(
        cap.get(
            cv2.CAP_PROP_FPS
        )
        or 0.0
    )

    frame_count = int(
        cap.get(
            cv2.CAP_PROP_FRAME_COUNT
        )
    )

    return (
        width,
        height,
        fps,
        frame_count,
    )


def read_frames_sequential(
    cap: cv2.VideoCapture,
    indices: Iterable[int],
) -> dict[int, np.ndarray]:
    """
    Lê os frames solicitados de forma sequencial.

    Não usa CAP_PROP_POS_FRAMES.
    """

    requested = sorted(
        set(
            int(index)
            for index in indices
            if int(index) >= 0
        )
    )

    if not requested:
        return {}

    frames: dict[
        int,
        np.ndarray,
    ] = {}

    target_index = 0
    current_index = 0

    while (
        target_index
        < len(requested)
    ):
        wanted = requested[
            target_index
        ]

        if current_index > wanted:
            target_index += 1
            continue

        ok, frame = cap.read()

        if not ok:
            break

        if current_index == wanted:
            frames[
                wanted
            ] = frame

            target_index += 1

        current_index += 1

    return frames


def sample_indices(
    frame_count: int,
    count: int = SAMPLE_COUNT,
) -> list[int]:

    if frame_count <= 0:
        return []

    if frame_count == 1:
        return [0]

    values = np.linspace(
        0,
        frame_count - 1,
        count,
    )

    result: list[int] = []

    for value in values:

        index = int(
            round(
                float(value)
            )
        )

        if index not in result:
            result.append(
                index
            )

    return result


def point_to_index(
    point: float,
    frame_count: int,
) -> int:

    if frame_count <= 1:
        return 0

    return int(
        round(
            point
            * (
                frame_count - 1
            )
        )
    )


# ============================================================
# DETECÇÃO DE CANDIDATOS
# ============================================================
#
# ESTA PARTE CONTINUA SENDO USADA PELO FULL.
#
# O FAST NOVO NÃO USA find_star_candidates().
# ============================================================

def find_star_candidates(
    frame: np.ndarray,
) -> list[Candidate]:

    height, width = (
        frame.shape[:2]
    )

    x0 = int(
        width * ROI_X0
    )

    y0 = int(
        height * ROI_Y0
    )

    roi = frame[
        y0:height,
        x0:width,
    ]

    if roi.size == 0:
        return []

    gray = cv2.cvtColor(
        roi,
        cv2.COLOR_BGR2GRAY,
    )

    kernel_size = int(
        round(
            scale_value(
                TOPHAT_KERNEL_720,
                width,
            )
        )
    )

    if kernel_size < 3:
        kernel_size = 3

    if kernel_size % 2 == 0:
        kernel_size += 1

    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE,
        (
            kernel_size,
            kernel_size,
        ),
    )

    top = cv2.morphologyEx(
        gray,
        cv2.MORPH_TOPHAT,
        kernel,
    )

    p995 = float(
        np.percentile(
            top,
            99.5,
        )
    )

    p992 = float(
        np.percentile(
            top,
            99.2,
        )
    )

    p985 = float(
        np.percentile(
            top,
            98.5,
        )
    )

    thresholds = [
        max(
            12.0,
            p995,
        ),
        max(
            10.0,
            p992,
        ),
        max(
            8.0,
            p985,
        ),
    ]

    candidates: list[
        Candidate
    ] = []

    min_dim = scale_value(
        CANDIDATE_MIN_DIM_720,
        width,
    )

    min_area = (
        scale_value(
            CANDIDATE_MIN_AREA_720,
            width,
        )
        ** 2
    )

    max_dim = scale_value(
        CANDIDATE_MAX_DIM_720,
        width,
    )

    max_area = (
        scale_value(
            CANDIDATE_MAX_AREA_720,
            width,
        )
        ** 2
    )

    for threshold in thresholds:

        _, binary = cv2.threshold(
            top,
            threshold,
            255,
            cv2.THRESH_BINARY,
        )

        close_kernel = (
            cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE,
                (3, 3),
            )
        )

        binary = cv2.morphologyEx(
            binary,
            cv2.MORPH_CLOSE,
            close_kernel,
        )

        (
            num_labels,
            labels,
            stats,
            centroids,
        ) = cv2.connectedComponentsWithStats(
            binary,
            connectivity=8,
        )

        for label in range(
            1,
            num_labels,
        ):

            x, y, w, h, area = (
                stats[label]
            )

            if (
                w < min_dim
                or h < min_dim
            ):
                continue

            if (
                w > max_dim
                or h > max_dim
            ):
                continue

            if (
                area < min_area
                or area > max_area
            ):
                continue

            aspect = (
                float(w)
                / float(h)
            )

            if (
                aspect
                < CANDIDATE_ASPECT_MIN
                or aspect
                > CANDIDATE_ASPECT_MAX
            ):
                continue

            (
                cx_local,
                cy_local,
            ) = centroids[label]

            cx = float(
                x0 + cx_local
            )

            cy = float(
                y0 + cy_local
            )

            nx = (
                cx
                / float(width)
            )

            ny = (
                cy
                / float(height)
            )

            if not (
                CANDIDATE_CENTER_X_MIN
                <= nx
                <= CANDIDATE_CENTER_X_MAX
            ):
                continue

            if not (
                CANDIDATE_CENTER_Y_MIN
                <= ny
                <= CANDIDATE_CENTER_Y_MAX
            ):
                continue

            distance = math.sqrt(
                (
                    (
                        nx
                        - EXPECTED_X
                    )
                    * width
                ) ** 2
                + (
                    (
                        ny
                        - EXPECTED_Y
                    )
                    * height
                ) ** 2
            )

            score = 1.0 / (
                1.0
                + distance
            )

            candidates.append(
                Candidate(
                    x=cx,
                    y=cy,
                    width=float(w),
                    height=float(h),
                    area=float(area),
                    score=score,
                )
            )

    deduped: list[
        Candidate
    ] = []

    candidates.sort(
        key=lambda candidate: (
            -candidate.score,
            -candidate.area,
        )
    )

    dedup_radius = scale_value(
        12.0,
        width,
    )

    for candidate in candidates:

        duplicate = False

        for existing in deduped:

            distance = math.sqrt(
                (
                    candidate.x
                    - existing.x
                ) ** 2
                + (
                    candidate.y
                    - existing.y
                ) ** 2
            )

            if (
                distance
                <= dedup_radius
            ):
                duplicate = True
                break

        if not duplicate:
            deduped.append(
                candidate
            )

    return deduped


# ============================================================
# DESCOBERTA DA POSIÇÃO
# ============================================================

def find_position_from_frame(
    frame: np.ndarray,
    frame_index: int = -1,
) -> Optional[
    FramePosition
]:

    height, width = (
        frame.shape[:2]
    )

    candidates = (
        find_star_candidates(
            frame
        )
    )

    if not candidates:
        return None

    expected_x = (
        EXPECTED_X
        * width
    )

    expected_y = (
        EXPECTED_Y
        * height
    )

    valid: list[
        Candidate
    ] = []

    for candidate in candidates:

        nx = (
            candidate.x
            / float(width)
        )

        ny = (
            candidate.y
            / float(height)
        )

        if not (
            POSITION_X_MIN
            <= nx
            <= POSITION_X_MAX
        ):
            continue

        if not (
            POSITION_Y_MIN
            <= ny
            <= POSITION_Y_MAX
        ):
            continue

        valid.append(
            candidate
        )

    if not valid:
        return None

    valid.sort(
        key=lambda candidate: (
            (
                (
                    candidate.x
                    - expected_x
                ) ** 2
                + (
                    candidate.y
                    - expected_y
                ) ** 2
            ),
            -candidate.area,
        )
    )

    best = valid[0]

    return FramePosition(
        frame_index=frame_index,
        x=best.x,
        y=best.y,
        candidate=best,
    )


# ============================================================
# TEMPLATE FULL
# ============================================================

def high_pass(
    gray: np.ndarray,
) -> np.ndarray:

    gray_float = (
        gray.astype(
            np.float32
        )
    )

    blur = cv2.GaussianBlur(
        gray_float,
        (0, 0),
        3.0,
    )

    result = (
        gray_float
        - blur
    )

    result = cv2.normalize(
        result,
        None,
        0,
        255,
        cv2.NORM_MINMAX,
    )

    return result.astype(
        np.uint8
    )


def extract_crop(
    frame: np.ndarray,
    center_x: float,
    center_y: float,
    size: int,
) -> Optional[
    np.ndarray
]:

    height, width = (
        frame.shape[:2]
    )

    half = size // 2

    cx = int(
        round(center_x)
    )

    cy = int(
        round(center_y)
    )

    x1 = cx - half
    y1 = cy - half

    x2 = x1 + size
    y2 = y1 + size

    if (
        x1 < 0
        or y1 < 0
        or x2 > width
        or y2 > height
    ):
        return None

    crop = frame[
        y1:y2,
        x1:x2,
    ]

    if (
        crop.shape[0] != size
        or crop.shape[1] != size
    ):
        return None

    return crop


def build_template_from_frame(
    frame: np.ndarray,
    center_x: float,
    center_y: float,
) -> Optional[
    np.ndarray
]:

    size = int(
        round(
            scale_value(
                TEMPLATE_SIZE_720,
                frame.shape[1],
            )
        )
    )

    if size % 2 == 0:
        size += 1

    crop = extract_crop(
        frame,
        center_x,
        center_y,
        size,
    )

    if crop is None:
        return None

    gray = cv2.cvtColor(
        crop,
        cv2.COLOR_BGR2GRAY,
    )

    return high_pass(
        gray
    )


# ============================================================
# TEMPLATE MATCHING FULL
# ============================================================

def match_frame(
    frame: np.ndarray,
    template: np.ndarray,
    center_x: float,
    center_y: float,
) -> tuple[
    float,
    int,
    int,
]:

    height, width = (
        frame.shape[:2]
    )

    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY,
    )

    processed = high_pass(
        gray
    )

    radius = int(
        round(
            scale_value(
                SEARCH_RADIUS_720,
                width,
            )
        )
    )

    template_h, template_w = (
        template.shape[:2]
    )

    half_w = (
        template_w // 2
    )

    half_h = (
        template_h // 2
    )

    cx = int(
        round(center_x)
    )

    cy = int(
        round(center_y)
    )

    x1 = clamp(
        cx
        - radius
        - half_w,
        0,
        width - template_w,
    )

    y1 = clamp(
        cy
        - radius
        - half_h,
        0,
        height - template_h,
    )

    x2 = clamp(
        cx
        + radius
        + half_w,
        template_w,
        width,
    )

    y2 = clamp(
        cy
        + radius
        + half_h,
        template_h,
        height,
    )

    search = processed[
        y1:y2,
        x1:x2,
    ]

    if (
        search.shape[0]
        < template_h
        or search.shape[1]
        < template_w
    ):
        return (
            -1.0,
            cx,
            cy,
        )

    result = cv2.matchTemplate(
        search,
        template,
        cv2.TM_CCOEFF_NORMED,
    )

    (
        _,
        max_value,
        _,
        max_location,
    ) = cv2.minMaxLoc(
        result
    )

    best_x = int(
        x1
        + max_location[0]
        + half_w
    )

    best_y = int(
        y1
        + max_location[1]
        + half_h
    )

    return (
        float(max_value),
        best_x,
        best_y,
    )


# ============================================================
# CONFIRMAÇÃO ENTRE FRAMES — FULL
# ============================================================

def validate_between_frames(
    frame_a: np.ndarray,
    position_a: FramePosition,
    frame_b: np.ndarray,
    position_b: FramePosition,
) -> Optional[
    Confirmation
]:

    width = (
        frame_a.shape[1]
    )

    if not same_position(
        position_a.candidate,
        position_b.candidate,
        width,
    ):
        return None

    template_a = (
        build_template_from_frame(
            frame_a,
            position_a.x,
            position_a.y,
        )
    )

    template_b = (
        build_template_from_frame(
            frame_b,
            position_b.x,
            position_b.y,
        )
    )

    if (
        template_a is None
        or template_b is None
    ):
        return None

    score_ab, _, _ = (
        match_frame(
            frame_b,
            template_a,
            position_b.x,
            position_b.y,
        )
    )

    score_ba, _, _ = (
        match_frame(
            frame_a,
            template_b,
            position_a.x,
            position_a.y,
        )
    )

    score = max(
        score_ab,
        score_ba,
    )

    if (
        score
        < MATCH_THRESHOLD
    ):
        return None

    return Confirmation(
        frame_a=position_a.frame_index,
        frame_b=position_b.frame_index,
        score_ab=score_ab,
        score_ba=score_ba,
        score=score,
    )


# ============================================================
# FAST — DETECTOR GEOMÉTRICO DE 1 FRAME
# ============================================================

def build_gemini_shape_template(
    width: int,
) -> np.ndarray:
    """
    Cria uma máscara analítica da estrela Gemini
    de quatro pontas.

    O tamanho é escalado de acordo com a largura
    do vídeo.
    """

    scale = (
        float(width)
        / BASE_WIDTH
    )

    size = int(
        round(
            FAST_STAR_TEMPLATE_SIZE_720
            * scale
        )
    )

    if size < 31:
        size = 31

    if size % 2 == 0:
        size += 1

    arm = (
        23.0
        * scale
    )

    center = (
        6.0
        * scale
    )

    base = (
        6.0
        * scale
    )

    yy, xx = np.mgrid[
        0:size,
        0:size,
    ]

    del yy

    c = (
        size - 1
    ) / 2.0

    dx = np.abs(
        xx - c
    )

    # A estrela é formada por quatro pontas.
    #
    # O eixo principal usa a maior distância ao centro.
    # O outro eixo controla a espessura das pontas.

    dy = np.abs(
        np.mgrid[
            0:size,
            0:size,
        ][0] - c
    )

    radius = np.maximum(
        dx,
        dy,
    )

    perpendicular = np.minimum(
        dx,
        dy,
    )

    mask = (
        radius <= center
    ) | (
        (
            radius > center
        )
        & (
            radius <= arm
        )
        & (
            perpendicular
            <=
            base
            * (
                arm
                - radius
            )
            / (
                arm
                - center
            )
        )
    )

    template = np.zeros(
        (
            size,
            size,
        ),
        dtype=np.float32,
    )

    template[
        mask
    ] = 1.0

    template = cv2.GaussianBlur(
        template,
        (0, 0),
        1.5 * scale,
    )

    return template


def match_gemini_shape_fast(
    frame: np.ndarray,
) -> tuple[
    float,
    int,
    int,
]:

    height, width = (
        frame.shape[:2]
    )

    expected_x = int(
        round(
            FAST_STAR_CENTER_X
            * width
        )
    )

    expected_y = int(
        round(
            FAST_STAR_CENTER_Y
            * height
        )
    )

    template = (
        build_gemini_shape_template(
            width
        )
    )

    template_h, template_w = (
        template.shape[:2]
    )

    radius = int(
        round(
            FAST_STAR_SEARCH_RADIUS_720
            * width
            / BASE_WIDTH
        )
    )

    half_w = (
        template_w // 2
    )

    half_h = (
        template_h // 2
    )

    x1 = max(
        0,
        expected_x
        - radius
        - half_w,
    )

    y1 = max(
        0,
        expected_y
        - radius
        - half_h,
    )

    x2 = min(
        width,
        expected_x
        + radius
        + half_w
        + 1,
    )

    y2 = min(
        height,
        expected_y
        + radius
        + half_h
        + 1,
    )

    gray = cv2.cvtColor(
        frame,
        cv2.COLOR_BGR2GRAY,
    )

    search = gray[
        y1:y2,
        x1:x2,
    ].astype(
        np.float32
    )

    if (
        search.shape[0]
        < template_h
        or search.shape[1]
        < template_w
    ):
        return (
            -1.0,
            expected_x,
            expected_y,
        )

    result = cv2.matchTemplate(
        search,
        template,
        cv2.TM_CCOEFF_NORMED,
    )

    (
        _,
        max_value,
        _,
        max_location,
    ) = cv2.minMaxLoc(
        result
    )

    best_x = int(
        x1
        + max_location[0]
        + half_w
    )

    best_y = int(
        y1
        + max_location[1]
        + half_h
    )

    return (
        float(max_value),
        best_x,
        best_y,
    )


def detect_fast(
    path: str,
) -> dict:
    """
    FAST GEMINI.

    Analisa EXATAMENTE UM FRAME:
    frame 0.

    Não usa o detector antigo de candidatos.
    Não usa frame 50%.
    Não usa calibração.
    Não usa confirmação temporal.
    Não usa template aprendido.
    """

    cap = open_video(
        path
    )

    try:
        (
            width,
            height,
            fps,
            frame_count,
        ) = get_video_info(
            cap
        )

        if frame_count <= 0:
            return {
                "video": path,
                "gemini": False,
                "mode": "fast",
                "reason": "video_sem_frames",
                "width": width,
                "height": height,
                "fps": fps,
                "frames": frame_count,
                "score": 0.0,
            }

        # ----------------------------------------------------
        # ÚNICO FRAME ANALISADO
        # ----------------------------------------------------

        ok, frame = cap.read()

        if (
            not ok
            or frame is None
        ):
            return {
                "video": path,
                "gemini": False,
                "mode": "fast",
                "reason": "frame_0_nao_disponivel",
                "width": width,
                "height": height,
                "fps": fps,
                "frames": frame_count,
                "score": 0.0,
            }

        score, x, y = (
            match_gemini_shape_fast(
                frame
            )
        )

        gemini = (
            score
            >= FAST_STAR_MATCH_THRESHOLD
        )

        return {
            "video": path,
            "gemini": gemini,
            "mode": "fast",
            "frame": 0,
            "score": score,
            "x": x,
            "y": y,
            "width": width,
            "height": height,
            "fps": fps,
            "frames": frame_count,
        }

    finally:
        cap.release()


# ============================================================
# CALIBRAÇÃO FULL
# ============================================================

def calibrate_full(
    cap: cv2.VideoCapture,
    frame_count: int,
) -> tuple[
    Optional[np.ndarray],
    Optional[FramePosition],
    Optional[Confirmation],
]:

    if frame_count <= 0:
        return (
            None,
            None,
            None,
        )

    indices = sample_indices(
        frame_count,
        SAMPLE_COUNT,
    )

    frames = (
        read_frames_sequential(
            cap,
            indices,
        )
    )

    positions: dict[
        int,
        FramePosition,
    ] = {}

    for index in indices:

        frame = frames.get(
            index
        )

        if frame is None:
            continue

        position = (
            find_position_from_frame(
                frame,
                index,
            )
        )

        if position is not None:
            positions[
                index
            ] = position

    if len(
        positions
    ) < 2:
        return (
            None,
            None,
            None,
        )

    ordered = sorted(
        positions.keys()
    )

    min_separation = max(
        2,
        int(
            round(
                frame_count
                * 0.03
            )
        ),
    )

    best_confirmation: Optional[
        Confirmation
    ] = None

    best_frame: Optional[
        np.ndarray
    ] = None

    best_position: Optional[
        FramePosition
    ] = None

    for i in range(
        len(ordered)
    ):

        for j in range(
            i + 1,
            len(ordered),
        ):

            a = ordered[i]
            b = ordered[j]

            if (
                abs(b - a)
                < min_separation
            ):
                continue

            confirmation = (
                validate_between_frames(
                    frames[a],
                    positions[a],
                    frames[b],
                    positions[b],
                )
            )

            if confirmation is None:
                continue

            if (
                best_confirmation is None
                or confirmation.score
                > best_confirmation.score
            ):

                best_confirmation = (
                    confirmation
                )

                best_frame = (
                    frames[a]
                )

                best_position = (
                    positions[a]
                )

    if (
        best_confirmation is None
        or best_frame is None
        or best_position is None
    ):
        return (
            None,
            None,
            None,
        )

    return (
        best_frame,
        best_position,
        best_confirmation,
    )


# ============================================================
# FULL
# ============================================================

def detect_full(
    path: str,
    csv_path: Optional[str] = None,
    output_path: Optional[str] = None,
) -> dict:

    calibration_cap = open_video(
        path
    )

    try:

        (
            width,
            height,
            fps,
            frame_count,
        ) = get_video_info(
            calibration_cap
        )

        (
            template_frame,
            position,
            confirmation,
        ) = calibrate_full(
            calibration_cap,
            frame_count,
        )

    finally:
        calibration_cap.release()

    if (
        template_frame is None
        or position is None
        or confirmation is None
    ):
        return {
            "video": path,
            "gemini": False,
            "mode": "full",
            "reason": "calibration_failed",
            "width": width,
            "height": height,
            "fps": fps,
            "frames": frame_count,
        }

    template = (
        build_template_from_frame(
            template_frame,
            position.x,
            position.y,
        )
    )

    if template is None:
        return {
            "video": path,
            "gemini": False,
            "mode": "full",
            "reason": "template_failed",
        }

    cap = open_video(
        path
    )

    try:

        detected_count = 0
        total_count = 0

        writer = None

        if output_path:

            os.makedirs(
                os.path.dirname(
                    os.path.abspath(
                        output_path
                    )
                ),
                exist_ok=True,
            )

            fourcc = (
                cv2.VideoWriter_fourcc(
                    *"mp4v"
                )
            )

            writer = cv2.VideoWriter(
                output_path,
                fourcc,
                (
                    fps
                    if fps > 0
                    else 30.0
                ),
                (
                    width,
                    height,
                ),
            )

        rows: list[
            dict
        ] = []

        while True:

            ok, frame = cap.read()

            if not ok:
                break

            frame_index = (
                total_count
            )

            (
                score,
                best_x,
                best_y,
            ) = match_frame(
                frame,
                template,
                position.x,
                position.y,
            )

            detected = (
                score
                >= MATCH_THRESHOLD
            )

            if detected:
                detected_count += 1

            total_count += 1

            rows.append(
                {
                    "frame": frame_index,
                    "score": score,
                    "detected": int(
                        detected
                    ),
                    "x": best_x,
                    "y": best_y,
                }
            )

            if writer is not None:

                annotated = (
                    frame.copy()
                )

                color = (
                    (0, 255, 0)
                    if detected
                    else (0, 0, 255)
                )

                cv2.circle(
                    annotated,
                    (
                        best_x,
                        best_y,
                    ),
                    10,
                    color,
                    2,
                )

                cv2.putText(
                    annotated,
                    (
                        f"GEMINI "
                        f"{score:.3f}"
                    ),
                    (
                        20,
                        40,
                    ),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    1.0,
                    color,
                    2,
                    cv2.LINE_AA,
                )

                writer.write(
                    annotated
                )

        if writer is not None:
            writer.release()

        detected_percent = (
            100.0
            * detected_count
            / total_count
            if total_count
            else 0.0
        )

        gemini = (
            detected_percent
            >= FULL_DETECTED_PERCENT
        )

        result = {
            "video": path,
            "gemini": gemini,
            "mode": "full",
            "detected_frames": detected_count,
            "total_frames": total_count,
            "detected_percent": detected_percent,
            "learned_x": position.x,
            "learned_y": position.y,
            "confirmation_frame_a": (
                confirmation.frame_a
            ),
            "confirmation_frame_b": (
                confirmation.frame_b
            ),
            "confirmation_score": (
                confirmation.score
            ),
            "confirmation_score_ab": (
                confirmation.score_ab
            ),
            "confirmation_score_ba": (
                confirmation.score_ba
            ),
            "width": width,
            "height": height,
            "fps": fps,
            "frames": frame_count,
        }

        if csv_path:
            write_csv(
                csv_path,
                rows,
            )

        return result

    finally:
        cap.release()


# ============================================================
# DISPATCH
# ============================================================

def detect_video(
    path: str,
    csv_path: Optional[str] = None,
    output_path: Optional[str] = None,
) -> dict:

    if (
        csv_path is not None
        or output_path is not None
    ):
        return detect_full(
            path,
            csv_path=csv_path,
            output_path=output_path,
        )

    return detect_fast(
        path
    )


# ============================================================
# CSV
# ============================================================

def write_csv(
    path: str,
    rows: Iterable[dict],
) -> None:

    rows = list(rows)

    os.makedirs(
        os.path.dirname(
            os.path.abspath(
                path
            )
        ),
        exist_ok=True,
    )

    if not rows:
        return

    fieldnames = list(
        rows[0].keys()
    )

    with open(
        path,
        "w",
        newline="",
        encoding="utf-8",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )

        writer.writeheader()
        writer.writerows(
            rows
        )


# ============================================================
# CLI
# ============================================================

def print_result(
    result: dict,
) -> None:

    video = result.get(
        "video",
        "",
    )

    print()
    print(
        "=" * 72
    )

    print(
        "ARMOREDSTUDIO - GEMINI DETECTOR"
    )

    print(
        "=" * 72
    )

    print(
        f"Vídeo      : {video}"
    )

    print(
        "Modo       : "
        f"{result.get('mode')}"
    )

    gemini = result.get(
        "gemini"
    )

    print(
        "Gemini     : "
        + (
            "SIM"
            if gemini
            else "NÃO"
        )
    )

    if (
        result.get("mode")
        == "fast"
    ):

        print(
            "Score      : "
            f"{result.get('score', 0.0):.3f}"
        )

        if gemini:

            print(
                "Frame      : "
                f"{result.get('frame')}"
            )

            print(
                "Posição    : "
                f"x={result.get('x', 0.0):.1f} "
                f"y={result.get('y', 0.0):.1f}"
            )

        print(
            "Resolução  : "
            f"{result.get('width')}x"
            f"{result.get('height')}"
        )

        print(
            "Frames     : "
            f"{result.get('frames')}"
        )

    else:

        if (
            "detected_percent"
            in result
        ):

            print(
                "Detectados : "
                f"{result.get('detected_frames')}/"
                f"{result.get('total_frames')} "
                f"("
                f"{result.get('detected_percent', 0.0):.1f}"
                f"%)"
            )

        if (
            "learned_x"
            in result
        ):

            print(
                "Posição    : "
                f"x={result.get('learned_x', 0.0):.1f} "
                f"y={result.get('learned_y', 0.0):.1f}"
            )

        if (
            "confirmation_score"
            in result
        ):

            print(
                "Confirmação: "
                f"{result.get('confirmation_frame_a')} "
                f"↔ "
                f"{result.get('confirmation_frame_b')}"
            )

            print(
                "Score      : "
                f"{result.get('confirmation_score', 0.0):.3f}"
            )

    if "reason" in result:

        print(
            "Motivo     : "
            f"{result.get('reason')}"
        )

    print(
        "=" * 72
    )


def build_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(
        description=(
            "Detector Gemini do "
            "ArmoredStudio V1."
        )
    )

    parser.add_argument(
        "videos",
        nargs="+",
        help=(
            "Um ou mais vídeos "
            "para analisar."
        ),
    )

    parser.add_argument(
        "--csv",
        help=(
            "CSV de saída. "
            "Com este argumento, "
            "o vídeo entra em FULL."
        ),
    )

    parser.add_argument(
        "--saida",
        help=(
            "Vídeo anotado de saída. "
            "Com este argumento, "
            "o vídeo entra em FULL."
        ),
    )

    return parser


def main() -> int:

    parser = build_parser()

    args = parser.parse_args()

    if (
        len(args.videos) > 1
        and (
            args.csv is not None
            or args.saida is not None
        )
    ):

        parser.error(
            "--csv e --saida só podem ser "
            "usados quando um único vídeo "
            "é informado."
        )

    for video in args.videos:

        csv_path = None
        output_path = None

        if len(
            args.videos
        ) == 1:

            csv_path = args.csv
            output_path = args.saida

        try:

            result = detect_video(
                video,
                csv_path=csv_path,
                output_path=output_path,
            )

            print_result(
                result
            )

        except Exception as exc:

            print()
            print(
                "=" * 72
            )

            print(
                "ERRO"
            )

            print(
                "=" * 72
            )

            print(
                f"Vídeo      : {video}"
            )

            print(
                f"Erro       : {exc}"
            )

            print(
                "=" * 72
            )

    return 0


if __name__ == "__main__":
    sys.exit(
        main()
    )
