"""
=========================================================
ArmoredStudio
Arquivo: banner.py
DescriÃ§Ã£o: Corte inteligente de banner.
=========================================================
"""

import subprocess
import numpy as np

from imageio_ffmpeg import get_ffmpeg_exe

from .logger import log


FFMPEG_PATH = get_ffmpeg_exe()


# Volume mÃ­nimo para considerar voz humana
THRESHOLD_DB = -20.0


# Trava anti-flicker do banner
TEMPO_MINIMO_CORTAR = 1.95



def detectar_inicio_voz_real_por_volume(caminho_video):

    """
    Analisa o Ã¡udio fÃ­sico do vÃ­deo
    e encontra o inÃ­cio real da voz.
    """

    comando = [

        FFMPEG_PATH,
        "-y",
        "-i",
        str(caminho_video),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-f",
        "f32le",
        "-"

    ]


    try:

        processo = subprocess.Popen(
            comando,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )


        audio_data, _ = processo.communicate()


        audio_np = np.frombuffer(
            audio_data,
            dtype=np.float32
        )


        if len(audio_np) == 0:

            return 0.0



        sample_rate = 16000

        tamanho_bloco = int(
            sample_rate * 0.02
        )


        for i in range(
            0,
            len(audio_np),
            tamanho_bloco
        ):


            bloco = audio_np[
                i:i+tamanho_bloco
            ]


            if len(bloco) < tamanho_bloco:

                continue



            rms = np.sqrt(
                np.mean(
                    bloco ** 2
                )
            )


            if rms < 1e-5:

                continue



            db = 20 * np.log10(
                rms
            )


            if db > THRESHOLD_DB:

                return i / sample_rate



        return 0.0



    except Exception as erro:

        log.error(
            f"Erro ao analisar Ã¡udio: {erro}"
        )

        return 0.0




def analisar_banner(caminho_video):

    """
    Analisa o vÃ­deo e retorna
    a decisÃ£o de corte do banner.
    """

    tempo_voz = detectar_inicio_voz_real_por_volume(
        caminho_video
    )


    corte_video = max(
        TEMPO_MINIMO_CORTAR,
        tempo_voz
    )


    corte_audio = max(
        0.0,
        tempo_voz - 0.03
    )


    resultado = {

        "banner_detectado": True
        if corte_video > 0
        else False,


        "tempo_voz": round(
            tempo_voz,
            3
        ),


        "corte_video": round(
            corte_video,
            3
        ),


        "corte_audio": round(
            corte_audio,
            3
        )

    }


    log.info(
        f"AnÃ¡lise banner: {resultado}"
    )


    return resultado


