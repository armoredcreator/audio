# -*- coding: utf-8 -*-
"""
ARMOREDSTUDIO V1
BLACK BAR DETECTOR

Responsabilidade:
- Detectar barras pretas reais nas bordas do vídeo.
- Detectar barras horizontais e verticais.
- Aceitar barras extremamente finas ou largas.
- Usar análise espacial por fração de pixels escuros.
- Usar consistência temporal entre frames.
- Ser a única fonte de verdade para detecção de barras.

Este módulo NÃO:
- conhece VEO;
- conhece Gemini;
- conhece watermark;
- força 9:16;
- redimensiona vídeo;
- executa FFmpeg;
- exporta vídeo;
- altera o vídeo.

Regra:
O detector mede somente as barras realmente existentes.
Nenhum tamanho fixo é inventado.
"""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np


# ============================================================
# CONFIGURAÇÃO
# ============================================================

FRAMES_ANALISE = 24

# Pixel abaixo deste valor é considerado muito escuro.
LIMIAR_PRETO = 35

# Fração mínima da dimensão perpendicular que precisa estar preta.
FRACAO_PRETA = 0.90

# Percentual mínimo de frames que precisam sustentar a detecção.
CONSISTENCIA_MINIMA = 0.60

# Margem usada para evitar interferência de elementos localizados.
MARGEM_INTERNA = 0.08

# Uma barra pode ter inclusive 1 pixel.
MINIMO_DETECCAO = 1


Bordas = Dict[str, int]


# ============================================================
# NORMALIZAÇÃO
# ============================================================

def _normalizar_bordas(
    esquerda: int,
    direita: int,
    topo: int,
    baixo: int,
) -> Bordas:
    return {
        "esquerda": max(0, int(esquerda)),
        "direita": max(0, int(direita)),
        "topo": max(0, int(topo)),
        "baixo": max(0, int(baixo)),
    }


# ============================================================
# LEITURA DE FRAMES
# ============================================================

def extrair_frames_video(
    video: str | Path,
) -> List[np.ndarray]:
    """
    Extrai até FRAMES_ANALISE frames distribuídos pelo vídeo.
    """

    caminho = str(video)

    captura = cv2.VideoCapture(caminho)

    if not captura.isOpened():
        raise RuntimeError(
            f"Não foi possível abrir o vídeo: {caminho}"
        )

    try:
        total = int(
            captura.get(cv2.CAP_PROP_FRAME_COUNT)
        )

        if total <= 0:
            return []

        quantidade = min(
            FRAMES_ANALISE,
            total
        )

        indices = np.linspace(
            0,
            total - 1,
            quantidade,
            dtype=int
        )

        indices = np.unique(indices)

        frames: List[np.ndarray] = []

        for indice in indices:
            captura.set(
                cv2.CAP_PROP_POS_FRAMES,
                int(indice)
            )

            ok, frame = captura.read()

            if ok and frame is not None:
                frames.append(frame)

        return frames

    finally:
        captura.release()


# ============================================================
# PERFIL LATERAL
# ============================================================

def perfil_lateral(
    gray: np.ndarray,
    lado: str
) -> np.ndarray:
    """
    Para cada coluna, calcula a fração de pixels muito escuros.

    A análise ignora uma margem de 8% no topo e no fundo.
    """

    altura, largura = gray.shape

    if altura <= 0 or largura <= 0:
        return np.array([], dtype=float)

    y0 = int(altura * MARGEM_INTERNA)
    y1 = int(altura * (1.0 - MARGEM_INTERNA))

    y0 = max(0, min(y0, altura))
    y1 = max(y0 + 1, min(y1, altura))

    regiao = gray[y0:y1, :]

    escuro = regiao <= LIMIAR_PRETO

    perfil = escuro.mean(axis=0)

    if lado == "esquerda":
        return perfil

    if lado == "direita":
        return perfil[::-1]

    raise ValueError(
        f"Lado lateral inválido: {lado}"
    )


# ============================================================
# PERFIL VERTICAL
# ============================================================

def perfil_vertical(
    gray: np.ndarray,
    lado: str
) -> np.ndarray:
    """
    Para cada linha, calcula a fração de pixels muito escuros.

    A análise ignora uma margem de 8% nas laterais.
    """

    altura, largura = gray.shape

    if altura <= 0 or largura <= 0:
        return np.array([], dtype=float)

    x0 = int(largura * MARGEM_INTERNA)
    x1 = int(largura * (1.0 - MARGEM_INTERNA))

    x0 = max(0, min(x0, largura))
    x1 = max(x0 + 1, min(x1, largura))

    regiao = gray[:, x0:x1]

    escuro = regiao <= LIMIAR_PRETO

    perfil = escuro.mean(axis=1)

    if lado == "topo":
        return perfil

    if lado == "baixo":
        return perfil[::-1]

    raise ValueError(
        f"Lado vertical inválido: {lado}"
    )


# ============================================================
# DETECÇÃO DE PREFIXO PRETO
# ============================================================

def detectar_prefixo_preto(
    perfil: np.ndarray
) -> int:
    """
    Mede quantos pixels consecutivos existem desde a borda externa.

    Não existe tamanho máximo.

    Uma barra de 1 px pode retornar 1.
    Uma barra de 3 px pode retornar 3.
    Uma barra de 200 px pode retornar 200.
    """

    n = len(perfil)

    if n == 0:
        return 0

    # Pequena tolerância espacial contra ruído de compressão.
    perfil_suave = np.convolve(
        perfil,
        np.ones(3) / 3.0,
        mode="same"
    )

    quantidade = 0

    for i in range(n):
        if (
            perfil[i] >= FRACAO_PRETA
            or perfil_suave[i] >= FRACAO_PRETA
        ):
            quantidade = i + 1
        else:
            break

    return quantidade


# ============================================================
# DETECÇÃO DE UM FRAME
# ============================================================

def detectar_frame(
    frame: np.ndarray
) -> Bordas:
    """
    Detecta barras nos quatro lados de um frame.
    """

    if frame is None or frame.size == 0:
        return _normalizar_bordas(
            0,
            0,
            0,
            0
        )

    if len(frame.shape) == 3:
        gray = cv2.cvtColor(
            frame,
            cv2.COLOR_BGR2GRAY
        )
    else:
        gray = frame

    perfis = {
        "esquerda": perfil_lateral(
            gray,
            "esquerda"
        ),
        "direita": perfil_lateral(
            gray,
            "direita"
        ),
        "topo": perfil_vertical(
            gray,
            "topo"
        ),
        "baixo": perfil_vertical(
            gray,
            "baixo"
        )
    }

    return _normalizar_bordas(
        esquerda=detectar_prefixo_preto(
            perfis["esquerda"]
        ),
        direita=detectar_prefixo_preto(
            perfis["direita"]
        ),
        topo=detectar_prefixo_preto(
            perfis["topo"]
        ),
        baixo=detectar_prefixo_preto(
            perfis["baixo"]
        )
    )


# ============================================================
# ESTATÍSTICA
# ============================================================

def mediana_valores(
    valores: List[int]
) -> int:
    valores = [
        int(v)
        for v in valores
        if v is not None
    ]

    if not valores:
        return 0

    return int(
        round(
            float(
                np.median(valores)
            )
        )
    )


def percentil75(
    valores: List[int]
) -> int:
    valores = [
        int(v)
        for v in valores
        if v is not None
    ]

    if not valores:
        return 0

    return int(
        round(
            float(
                np.percentile(
                    valores,
                    75
                )
            )
        )
    )


# ============================================================
# CONSOLIDAÇÃO TEMPORAL
# ============================================================

def consolidar(
    deteccoes: List[Bordas]
) -> Dict[str, object]:
    """
    Consolida as detecções dos vários frames.

    A mediana determina a espessura.

    Depois verifica quantos frames possuem uma detecção de pelo
    menos metade dessa espessura.

    Se o suporte temporal for >= CONSISTENCIA_MINIMA,
    a barra é considerada real.
    """

    resultado: Dict[str, object] = {}

    lados = (
        "esquerda",
        "direita",
        "topo",
        "baixo"
    )

    if not deteccoes:
        for lado in lados:
            resultado[lado] = 0
            resultado[
                f"{lado}_consistencia"
            ] = 0.0

        resultado["detectado"] = False

        return resultado

    for lado in lados:
        valores = [
            int(d.get(lado, 0))
            for d in deteccoes
        ]

        mediana = mediana_valores(
            valores
        )

        if mediana <= 0:
            resultado[lado] = 0
            resultado[
                f"{lado}_consistencia"
            ] = 0.0
            continue

        limite_suporte = max(
            1,
            int(
                round(
                    mediana * 0.5
                )
            )
        )

        suporte = sum(
            1
            for valor in valores
            if valor >= limite_suporte
        )

        consistencia = (
            suporte /
            max(1, len(valores))
        )

        if consistencia >= CONSISTENCIA_MINIMA:
            valor_final = mediana
        else:
            valor_final = 0

        resultado[lado] = int(
            valor_final
        )

        resultado[
            f"{lado}_consistencia"
        ] = round(
            consistencia,
            3
        )

    resultado["detectado"] = any(
        int(resultado[lado]) >= MINIMO_DETECCAO
        for lado in lados
    )

    return resultado


# ============================================================
# ANÁLISE COMPLETA DO VÍDEO
# ============================================================

def analisar_bordas_video(
    video: str | Path
) -> Dict[str, object]:
    """
    Analisa o vídeo através de frames distribuídos.

    Retorna:
        detectado
        esquerda
        direita
        topo
        baixo
        consistência de cada lado
    """

    frames = extrair_frames_video(
        video
    )

    if not frames:
        return {
            "detectado": False,
            "esquerda": 0,
            "direita": 0,
            "topo": 0,
            "baixo": 0,
            "esquerda_consistencia": 0.0,
            "direita_consistencia": 0.0,
            "topo_consistencia": 0.0,
            "baixo_consistencia": 0.0
        }

    deteccoes: List[Bordas] = []

    largura_video = 0
    altura_video = 0

    for frame in frames:
        altura, largura = frame.shape[:2]

        altura_video = altura
        largura_video = largura

        deteccoes.append(
            detectar_frame(frame)
        )

    resultado = consolidar(
        deteccoes
    )

    # Segurança: nunca permitir que uma detecção
    # ultrapasse as dimensões reais do vídeo.

    if largura_video > 0:
        esquerda = min(
            int(resultado["esquerda"]),
            largura_video
        )

        direita = min(
            int(resultado["direita"]),
            largura_video
        )

        if esquerda + direita >= largura_video:
            esquerda = 0
            direita = 0

        resultado["esquerda"] = esquerda
        resultado["direita"] = direita

    if altura_video > 0:
        topo = min(
            int(resultado["topo"]),
            altura_video
        )

        baixo = min(
            int(resultado["baixo"]),
            altura_video
        )

        if topo + baixo >= altura_video:
            topo = 0
            baixo = 0

        resultado["topo"] = topo
        resultado["baixo"] = baixo

    resultado["detectado"] = any(
        int(resultado[lado]) >= MINIMO_DETECCAO
        for lado in (
            "esquerda",
            "direita",
            "topo",
            "baixo"
        )
    )

    return resultado


# ============================================================
# ÁREA SEM BARRAS
# ============================================================

def calcular_area_sem_barras(
    largura: int,
    altura: int,
    bordas: Bordas
) -> Tuple[int, int, int, int]:
    """
    Calcula a área restante depois de remover somente
    as barras detectadas.

    Retorna:
        x, y, largura, altura
    """

    esquerda = max(
        0,
        int(bordas.get("esquerda", 0))
    )

    direita = max(
        0,
        int(bordas.get("direita", 0))
    )

    topo = max(
        0,
        int(bordas.get("topo", 0))
    )

    baixo = max(
        0,
        int(bordas.get("baixo", 0))
    )

    esquerda = min(
        esquerda,
        largura
    )

    direita = min(
        direita,
        largura
    )

    topo = min(
        topo,
        altura
    )

    baixo = min(
        baixo,
        altura
    )

    largura_limpa = (
        largura
        - esquerda
        - direita
    )

    altura_limpa = (
        altura
        - topo
        - baixo
    )

    if largura_limpa <= 0:
        return (
            0,
            0,
            largura,
            altura
        )

    if altura_limpa <= 0:
        return (
            0,
            0,
            largura,
            altura
        )

    return (
        esquerda,
        topo,
        largura_limpa,
        altura_limpa
    )


# ============================================================
# FILTRO CROP
# ============================================================

def gerar_filtro_crop(
    bordas: Bordas
) -> str | None:
    """
    Gera somente o filtro crop das barras detectadas.

    Não faz:
    - scale;
    - pad;
    - 9:16;
    - resize;
    - reposicionamento;
    """

    esquerda = max(
        0,
        int(bordas.get("esquerda", 0))
    )

    direita = max(
        0,
        int(bordas.get("direita", 0))
    )

    topo = max(
        0,
        int(bordas.get("topo", 0))
    )

    baixo = max(
        0,
        int(bordas.get("baixo", 0))
    )

    if not any(
        valor >= MINIMO_DETECCAO
        for valor in (
            esquerda,
            direita,
            topo,
            baixo
        )
    ):
        return None

    largura = (
        f"iw-{esquerda + direita}"
    )

    altura = (
        f"ih-{topo + baixo}"
    )

    return (
        f"crop={largura}:"
        f"{altura}:"
        f"{esquerda}:"
        f"{topo}"
    )


# ============================================================
# COMPATIBILIDADE
# ============================================================

def detectar(
    video: str | Path
) -> Dict[str, object]:
    return analisar_bordas_video(
        video
    )


__all__ = [
    "FRAMES_ANALISE",
    "LIMIAR_PRETO",
    "FRACAO_PRETA",
    "CONSISTENCIA_MINIMA",
    "MARGEM_INTERNA",
    "MINIMO_DETECCAO",
    "extrair_frames_video",
    "perfil_lateral",
    "perfil_vertical",
    "detectar_prefixo_preto",
    "detectar_frame",
    "mediana_valores",
    "percentil75",
    "consolidar",
    "analisar_bordas_video",
    "calcular_area_sem_barras",
    "gerar_filtro_crop",
    "detectar",
]