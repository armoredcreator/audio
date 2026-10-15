"""
=========================================================
ArmoredStudio
Arquivo: banner_analyzer.py

ResponsÃ¡vel:
- Analisar comportamento inicial do vÃ­deo
- Identificar provÃ¡vel abertura/banner
- NÃƒO corta vÃ­deo
- NÃƒO usa FFmpeg
- NÃƒO mexe em Ã¡udio
=========================================================
"""

import cv2
import numpy as np

from .logger import log



def analisar_banner(video):


    cap = cv2.VideoCapture(
        video
    )


    if not cap.isOpened():

        return {

            "banner_detectado": False,
            "confianca": 0,
            "erro": "video_invalido"

        }



    fps = cap.get(
        cv2.CAP_PROP_FPS
    )


    tempos = [

        0,
        0.3,
        0.6,
        0.9,
        1.2,
        1.5,
        1.8,
        2.1

    ]



    frames = []



    for tempo in tempos:


        cap.set(

            cv2.CAP_PROP_POS_FRAMES,

            int(tempo * fps)

        )


        sucesso, frame = cap.read()



        if sucesso:


            frame = cv2.resize(

                frame,

                (100,100)

            )


            frames.append(
                frame
            )



    cap.release()



    if len(frames) < 4:


        return {

            "banner_detectado": False,
            "confianca": 0,
            "erro": "frames_insuficientes"

        }



    diferencas = []



    for i in range(
        len(frames)-1
    ):


        diff = cv2.absdiff(

            frames[i],

            frames[i+1]

        )


        valor = np.mean(
            diff
        )


        diferencas.append(
            float(valor)
        )



    mudanca_inicial = max(

        diferencas[:3]

    )


    estabilidade = np.mean(

        diferencas[3:]

    )



    if (

        mudanca_inicial < 5

        and

        estabilidade < 1

    ):


        resultado = {

            "banner_detectado": True,

            "confianca": 90,

            "mudanca_inicial":
                round(mudanca_inicial,2),

            "estabilidade":
                round(float(estabilidade), 2)

        }


    else:


        resultado = {

            "banner_detectado": False,

            "confianca": 80,

            "mudanca_inicial":
                round(mudanca_inicial,2),

            "estabilidade":
                round(float(estabilidade), 2)

        }



    log.info(
        f"AnÃ¡lise banner: {resultado}"
    )


    return resultado


