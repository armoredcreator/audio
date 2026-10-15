"""ARMOREDSTUDIO V1 - EXPORT EXECUTOR."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List


def _obter_crop(plano: Dict[str, Any]) -> Dict[str, int] | None:
    crop = plano.get("crop_final")
    if not crop:
        return None
    return {"x": int(crop.get("x", 0)), "y": int(crop.get("y", 0)),
            "width": int(crop.get("width", 0)), "height": int(crop.get("height", 0))}


def _validar_crop(crop: Dict[str, int], plano: Dict[str, Any]) -> None:
    if crop["x"] < 0 or crop["y"] < 0 or crop["width"] <= 0 or crop["height"] <= 0:
        raise ValueError("crop_final inválido.")
    info = plano.get("video_info") or {}
    W = int(info.get("largura", 0) or 0)
    H = int(info.get("altura", 0) or 0)
    if W and H and (crop["x"] + crop["width"] > W or crop["y"] + crop["height"] > H):
        raise ValueError("crop_final ultrapassa as dimensões originais.")


def criar_comando_ffmpeg(plano: Dict[str, Any], caminho_video: str | Path,
                         caminho_saida: str | Path) -> Dict[str, Any]:
    entrada = str(caminho_video)
    saida = str(caminho_saida)
    crop = _obter_crop(plano)
    video_start = float(plano.get("video_start", 0) or 0)
    audio_start = float(plano.get("audio_start", 0) or 0)
    if video_start < 0 or audio_start < 0:
        raise ValueError("video_start/audio_start não podem ser negativos.")

    fv: List[str] = []
    fa: List[str] = []
    if video_start > 0:
        fv += [f"trim=start={video_start:g}", "setpts=PTS-STARTPTS"]
    if crop is not None:
        _validar_crop(crop, plano)
        # O crop pode começar em coordenada ímpar (ex.: barra lateral de 3 px).
        # Nesse caso o yuv420p não consegue fazer o crop diretamente.
        # Mantemos yuv444p SOMENTE durante o crop e voltamos para yuv420p
        # imediatamente depois, preservando a compatibilidade do arquivo final.
        crop_precisa_444 = (crop['x'] % 2 != 0 or crop['y'] % 2 != 0 or
                            crop['width'] % 2 != 0 or crop['height'] % 2 != 0)
        if crop_precisa_444:
            fv.append("format=pix_fmts=yuv444p")
        fv.append(f"crop={crop['width']}:{crop['height']}:{crop['x']}:{crop['y']}")
        if crop_precisa_444:
            fv.append("format=pix_fmts=yuv420p")

    audio_filtrado = audio_start > 0
    if audio_filtrado:
        fa += [f"atrim=start={audio_start:g}", "asetpts=PTS-STARTPTS"]

    # Sem corte temporal de áudio: copia o áudio original, evitando nova
    # compressão e mantendo a trilha o mais fiel possível.
    if fa:
        cadeia_v = ",".join(fv) if fv else "null"
        cadeia_a = ",".join(fa)
        filter_complex = f"[0:v]{cadeia_v}[v];[0:a]{cadeia_a}[a]"
        comando = ["ffmpeg", "-y", "-i", entrada, "-filter_complex", filter_complex,
                   "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18",
                   "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                   "-b:a", "192k", saida]
    else:
        cadeia_v = ",".join(fv) if fv else "null"
        filter_complex = f"[0:v]{cadeia_v}[v]"
        comando = ["ffmpeg", "-y", "-i", entrada, "-filter_complex", filter_complex,
                   "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-crf", "18",
                   "-preset", "fast", "-pix_fmt", "yuv420p", "-c:a", "copy", saida]

    return {"comando": comando,
            "filtros": {"video": fv, "audio": fa, "filter_complex": filter_complex},
            "pix_fmt": "yuv420p", "executar": False}


def _validar_arquivo_saida(caminho: str | Path) -> Dict[str, Any]:
    """Validação real do contêiner/stream antes de aceitar a exportação."""
    p = Path(caminho)
    if not p.exists() or p.stat().st_size < 1024:
        return {"valido": False, "erro": "Arquivo de saída inexistente ou vazio."}

    try:
        import cv2
        cap = cv2.VideoCapture(str(p))
        abriu = cap.isOpened()
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0) if abriu else 0
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0) if abriu else 0
        w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0) if abriu else 0
        h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0) if abriu else 0
        ok_frame, _ = cap.read() if abriu else (False, None)
        cap.release()
        if not abriu or not ok_frame or frames <= 0 or fps <= 0 or w <= 0 or h <= 0:
            return {"valido": False, "erro": "Saída MP4 não pôde ser aberta/decodificada."}
        return {"valido": True, "frames": frames, "fps": fps, "largura": w, "altura": h,
                "tamanho": p.stat().st_size}
    except Exception as exc:
        return {"valido": False, "erro": f"Falha ao validar saída: {exc}"}


def executar_ffmpeg(comando: Dict[str, Any] | List[str]) -> Dict[str, Any]:
    args = comando.get("comando") if isinstance(comando, dict) else comando
    if not args:
        raise ValueError("Comando FFmpeg vazio.")

    # Executa em temporário e só publica o arquivo se a saída for realmente válida.
    saida = Path(args[-1])
    temporario = saida.with_name(saida.stem + ".__v1tmp__.mp4")
    args = list(args)
    args[-1] = str(temporario)
    try:
        temporario.unlink(missing_ok=True)
        processo = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  text=True, encoding="utf-8", errors="replace")
        if processo.returncode != 0:
            temporario.unlink(missing_ok=True)
            return {"sucesso": False, "codigo": processo.returncode,
                    "erro": processo.stderr[-12000:] if processo.stderr else "Erro FFmpeg."}

        validacao = _validar_arquivo_saida(temporario)
        if not validacao.get("valido"):
            temporario.unlink(missing_ok=True)
            return {"sucesso": False, "codigo": processo.returncode,
                    "erro": validacao.get("erro", "Saída inválida."), "validacao": validacao}

        saida.parent.mkdir(parents=True, exist_ok=True)
        os.replace(str(temporario), str(saida))
        return {"sucesso": True, "codigo": processo.returncode,
                "erro": None, "validacao": validacao}
    except FileNotFoundError:
        temporario.unlink(missing_ok=True)
        return {"sucesso": False, "codigo": -1, "erro": "FFmpeg não encontrado no PATH."}
    except Exception as exc:
        temporario.unlink(missing_ok=True)
        return {"sucesso": False, "codigo": -1, "erro": str(exc)}
