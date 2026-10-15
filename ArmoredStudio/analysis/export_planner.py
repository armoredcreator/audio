"""
ARMOREDSTUDIO V1 - EXPORT PLANNER

Responsabilidade única:
    Consolidar os resultados independentes dos detectores em UM plano final.

Regras V1:
    - Barras: corta somente as bordas realmente detectadas.
    - VEO: remove a caixa detectada escolhendo a maior região retangular
      contínua que não contém a marca.
    - Gemini: mesma regra geométrica de exclusão da caixa detectada.
    - Banner: mantém corte temporal de vídeo e áudio separadamente.
    - Todas as restrições espaciais são consolidadas em UM crop_final.
    - Não usa crop VEO fixo, 9:16, scale, resize ou padding.
    - Não executa FFmpeg.
    - Não faz nova detecção.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

Crop = Tuple[int, int, int, int]  # x, y, width, height
Box = Tuple[int, int, int, int]   # x, y, width, height


# ============================================================
# NORMALIZAÇÃO DOS RESULTADOS
# ============================================================

def _int(value: Any, default: int = 0) -> int:
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _dimensoes(video_info: Dict[str, Any]) -> Tuple[int, int]:
    w = _int(
        video_info.get("largura",
        video_info.get("width", 0))
    )
    h = _int(
        video_info.get("altura",
        video_info.get("height", 0))
    )

    if w <= 0 or h <= 0:
        raise ValueError("Dimensões inválidas no video_info")

    return w, h


def _extrair_bordas(resultado: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Aceita os formatos usados pelo blackbar.py V1."""
    if not resultado:
        return {"esquerda": 0, "direita": 0, "topo": 0, "baixo": 0}

    # O detector normalmente retorna diretamente as bordas.
    fonte = resultado.get("bordas", resultado)
    if not isinstance(fonte, dict):
        fonte = resultado

    return {
        "esquerda": max(0, _int(fonte.get("esquerda", fonte.get("left", 0)))),
        "direita": max(0, _int(fonte.get("direita", fonte.get("right", 0)))),
        "topo": max(0, _int(fonte.get("topo", fonte.get("top", 0)))),
        "baixo": max(0, _int(fonte.get("baixo", fonte.get("bottom", 0)))),
    }


# ============================================================
# GEOMETRIA
# ============================================================

def _clamp_crop(crop: Crop, W: int, H: int) -> Optional[Crop]:
    x, y, w, h = map(int, crop)

    x = max(0, min(x, W))
    y = max(0, min(y, H))
    w = min(w, W - x)
    h = min(h, H - y)

    if w <= 0 or h <= 0:
        return None

    return x, y, w, h


def _crop_barras(W: int, H: int, bordas: Dict[str, int]) -> Crop:
    """Remove exclusivamente as bordas detectadas."""
    esq = min(bordas["esquerda"], W)
    dir_ = min(bordas["direita"], max(0, W - esq))
    topo = min(bordas["topo"], H)
    baixo = min(bordas["baixo"], max(0, H - topo))

    w = W - esq - dir_
    h = H - topo - baixo

    # Nunca fabrica uma região inválida. Se o detector entregar algo
    # impossível, mantém o frame original para não destruir o vídeo.
    if w <= 0 or h <= 0:
        return 0, 0, W, H

    return esq, topo, w, h


def _intersect(a: Crop, b: Crop) -> Optional[Crop]:
    """Interseção geométrica de duas regiões."""
    ax, ay, aw, ah = a
    bx, by, bw, bh = b

    x1 = max(ax, bx)
    y1 = max(ay, by)
    x2 = min(ax + aw, bx + bw)
    y2 = min(ay + ah, by + bh)

    if x2 <= x1 or y2 <= y1:
        return None

    return x1, y1, x2 - x1, y2 - y1


def _box_from_center(resultado: Optional[Dict[str, Any]]) -> Optional[Box]:
    """Converte Gemini (centro/largura/altura) em caixa absoluta."""
    if not resultado or not resultado.get("gemini"):
        return None

    try:
        cx = _float(resultado.get("x"))
        cy = _float(resultado.get("y"))
        # IMPORTANTE: Gemini FAST usa width/height para as dimensoes
        # ORIGINAIS DO VIDEO. Elas NAO sao o tamanho da estrela.
        # A caixa espacial da marca usa o tamanho do template FAST,
        # escalado para a largura real do video.
        W = _int(resultado.get("width", 0))
        H = _int(resultado.get("height", 0))
        if W <= 0 or H <= 0:
            return None

        base = 61.0
        escala = float(W) / 720.0
        w = base * escala
        h = base * escala
    except (TypeError, ValueError):
        return None

    if w <= 0 or h <= 0:
        return None

    bw = max(1, int(round(w)))
    bh = max(1, int(round(h)))
    x = int(round(cx - bw / 2.0))
    y = int(round(cy - bh / 2.0))
    return x, y, bw, bh


def _box_from_veo(resultado: Optional[Dict[str, Any]]) -> Optional[Box]:
    """
    Extrai a melhor localização VEO.

    O detector V1 retorna locations como:
        [(frame, (x, y, width, height)), ...]

    Também aceitamos uma caixa direta caso uma versão compatível do detector
    a forneça. Nenhum tamanho é inventado.
    """
    if not resultado or not resultado.get("found"):
        return None

    locations = resultado.get("locations") or []

    # Formato oficial usado pelo VEO detector V1.
    for item in locations:
        try:
            if isinstance(item, dict):
                box = item.get("box") or item.get("bbox")
            else:
                _, box = item

            if box is None:
                continue

            x, y, w, h = map(_int, box)
            if w > 0 and h > 0:
                return x, y, w, h
        except (TypeError, ValueError, IndexError):
            continue

    # Fallback compatível com resultado que já venha como bbox.
    for key in ("box", "bbox"):
        box = resultado.get(key)
        if box is not None:
            try:
                x, y, w, h = map(_int, box)
                if w > 0 and h > 0:
                    return x, y, w, h
            except (TypeError, ValueError):
                pass

    return None


def _recortar_para_excluir_caixa(crop: Crop, box: Box) -> Crop:
    """
    Retorna a maior sub-região retangular de `crop` que exclui completamente
    a caixa detectada.

    Candidatos válidos:
        - tudo à esquerda da marca
        - tudo à direita
        - tudo acima
        - tudo abaixo

    Isso é deliberadamente diferente de INTERSECTAR com uma caixa VEO.
    Interseção reduziria o vídeo para a região do watermark, que é justamente
    o erro que esta implementação evita.
    """
    x, y, w, h = crop
    bx, by, bw, bh = box

    # Interseção entre a caixa da marca e o crop atual.
    ix1 = max(x, bx)
    iy1 = max(y, by)
    ix2 = min(x + w, bx + bw)
    iy2 = min(y + h, by + bh)

    # Marca já está completamente fora do crop: não há nada a remover.
    if ix1 >= ix2 or iy1 >= iy2:
        return crop

    candidatos = []

    # Esquerda
    if ix1 > x:
        candidatos.append((x, y, ix1 - x, h))

    # Direita
    if ix2 < x + w:
        candidatos.append((ix2, y, x + w - ix2, h))

    # Acima
    if iy1 > y:
        candidatos.append((x, y, w, iy1 - y))

    # Abaixo
    if iy2 < y + h:
        candidatos.append((x, iy2, w, y + h - iy2))

    candidatos = [c for c in candidatos if c[2] > 0 and c[3] > 0]

    if not candidatos:
        # A caixa ocupa toda a área disponível. Não há crop válido.
        return crop

    # Maior área = menor perda de conteúdo.
    return max(candidatos, key=lambda c: c[2] * c[3])


def _aplicar_exclusao(crop: Crop, box: Optional[Box]) -> Tuple[Crop, bool]:
    if box is None:
        return crop, False

    novo = _recortar_para_excluir_caixa(crop, box)
    return novo, novo != crop


# ============================================================
# PLANNER PÚBLICO
# ============================================================

def criar_plano_exportacao(
    video_info: Dict[str, Any],
    blackbar_result: Optional[Dict[str, Any]],
    banner_result: Optional[Dict[str, Any]],
    banner_cut_result: Optional[Dict[str, Any]] = None,
    veo_result: Optional[Dict[str, Any]] = None,
    gemini_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Cria um único plano final para a V1.

    A ordem espacial é:
        1. barras detectadas
        2. VEO detectado
        3. Gemini detectado

    Cada etapa reduz a mesma região de trabalho. Não existem crops
    sequenciais no executor.
    """
    W, H = _dimensoes(video_info)
    crop: Crop = (0, 0, W, H)
    acoes = []
    detalhes = {
        "barras": None,
        "veo": None,
        "gemini": None,
    }

    # ------------------------------------------------------------
    # 1. BARRAS
    # ------------------------------------------------------------
    bordas = _extrair_bordas(blackbar_result)
    if any(bordas.values()):
        crop = _crop_barras(W, H, bordas)
        detalhes["barras"] = dict(bordas)
        acoes.append("remover_barras")

    # ------------------------------------------------------------
    # 2. VEO
    # ------------------------------------------------------------
    # IMPORTANTE: NÃO usar _intersect(crop, veo_crop).
    # Isso seria recortar PARA A REGIÃO VEO e não remover a marca.
    veo_box = _box_from_veo(veo_result)
    if veo_box is not None:
        detalhes["veo"] = {
            "box": {
                "x": veo_box[0],
                "y": veo_box[1],
                "width": veo_box[2],
                "height": veo_box[3],
            }
        }
        novo_crop, mudou = _aplicar_exclusao(crop, veo_box)
        if mudou:
            crop = novo_crop
            acoes.append("remover_veo")

    # ------------------------------------------------------------
    # 3. GEMINI
    # ------------------------------------------------------------
    gemini_box = _box_from_center(gemini_result)
    if gemini_box is not None:
        detalhes["gemini"] = {
            "box": {
                "x": gemini_box[0],
                "y": gemini_box[1],
                "width": gemini_box[2],
                "height": gemini_box[3],
            }
        }
        novo_crop, mudou = _aplicar_exclusao(crop, gemini_box)
        if mudou:
            crop = novo_crop
            acoes.append("remover_gemini")

    # ------------------------------------------------------------
    # 4. BANNER / TEMPO
    # ------------------------------------------------------------
    video_start = 0.0
    audio_start = 0.0

    banner_detectado = bool(
        banner_result
        and (
            banner_result.get("banner_detectado")
            or banner_result.get("detectado")
        )
    )

    if banner_detectado and banner_cut_result:
        video_start = max(
            0.0,
            _float(
                banner_cut_result.get(
                    "corte_video",
                    banner_cut_result.get("video_start", 0),
                )
            ),
        )
        audio_start = max(
            0.0,
            _float(
                banner_cut_result.get(
                    "corte_audio",
                    banner_cut_result.get("audio_start", 0),
                )
            ),
        )

        if video_start > 0.0 or audio_start > 0.0:
            acoes.append("remover_banner")

    # ------------------------------------------------------------
    # 5. CROP FINAL
    # ------------------------------------------------------------
    crop = _clamp_crop(crop, W, H) or (0, 0, W, H)

    x, y, w, h = crop

    # H.264/yuv420p exige dimensões de imagem pares.
    # Ajustamos SOMENTE a borda final do crop, preservando exatamente
    # as bordas detectadas (inclusive barras finas de 1/2/3 px).
    # Não usamos yuv444p para contornar isso.
    if w % 2:
        w -= 1
    if h % 2:
        h -= 1

    if w <= 0 or h <= 0:
        x, y, w, h = 0, 0, W, H

    crop_final = None

    if (x, y, w, h) != (0, 0, W, H):
        crop_final = {
            "x": x,
            "y": y,
            "width": w,
            "height": h,
        }

    transformacao = bool(
        crop_final is not None
        or video_start > 0.0
        or audio_start > 0.0
    )

    return {
        "transformacao": transformacao,
        "crop_final": crop_final,
        "video_start": video_start,
        "audio_start": audio_start,
        "acoes": list(dict.fromkeys(acoes)),
        "video_info": video_info,
        "detectores": {
            "blackbar": blackbar_result,
            "banner": banner_result,
            "banner_cut": banner_cut_result,
            "veo": veo_result,
            "gemini": gemini_result,
        },
        "detalhes_geometria": detalhes,
    }


__all__ = ["criar_plano_exportacao"]
