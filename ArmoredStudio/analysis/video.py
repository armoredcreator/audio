"""
=========================================================
ArmoredStudio
Arquivo: video.py
DescriÃ§Ã£o: Leitura e informaÃ§Ãµes bÃ¡sicas dos vÃ­deos.
=========================================================
"""

import cv2


# =========================================================
# OBTER INFORMAÃ‡Ã•ES DO VÃDEO
# =========================================================

def obter_informacoes_video(caminho_video):

    cap = cv2.VideoCapture(
        str(caminho_video)
    )


    if not cap.isOpened():

        return {
            "sucesso": False,
            "erro": "NÃ£o foi possÃ­vel abrir o vÃ­deo"
        }


    fps = cap.get(
        cv2.CAP_PROP_FPS
    )

    frames = cap.get(
        cv2.CAP_PROP_FRAME_COUNT
    )

    largura = cap.get(
        cv2.CAP_PROP_FRAME_WIDTH
    )

    altura = cap.get(
        cv2.CAP_PROP_FRAME_HEIGHT
    )


    duracao = 0

    if fps > 0:

        duracao = frames / fps


    cap.release()


    return {

        "sucesso": True,

        "duracao": round(
            duracao,
            3
        ),

        "fps": round(
            fps,
            3
        ),

        "frames": int(
            frames
        ),

        "largura": int(
            largura
        ),

        "altura": int(
            altura
        )

    }


