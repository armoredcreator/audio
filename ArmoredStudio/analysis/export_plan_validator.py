"""Validação do plano final da V1."""

from .logger import log

ACOES_PERMITIDAS = [
    "remover_banner",
    "remover_barras",
    "remover_veo",
    "remover_gemini",
]


def validar_plano_exportacao(plano):
    resultado = {"valido": True, "erros": []}

    obrigatorios = ["crop_final", "video_start", "audio_start", "acoes"]
    for campo in obrigatorios:
        if campo not in plano:
            resultado["valido"] = False
            resultado["erros"].append(f"Campo ausente: {campo}")

    if not resultado["valido"]:
        log.warning(f"Plano inválido: {resultado}")
        return resultado

    for acao in plano["acoes"]:
        if acao not in ACOES_PERMITIDAS:
            resultado["valido"] = False
            resultado["erros"].append(f"Ação não permitida: {acao}")

    crop = plano.get("crop_final")
    if crop is not None:
        if not isinstance(crop, dict):
            resultado["valido"] = False
            resultado["erros"].append("crop_final inválido")
        else:
            campos = ("x", "y", "width", "height")
            if any(not isinstance(crop.get(c), int) for c in campos):
                resultado["valido"] = False
                resultado["erros"].append("crop_final deve conter x/y/width/height inteiros")
            else:
                if crop["x"] < 0 or crop["y"] < 0 or crop["width"] <= 0 or crop["height"] <= 0:
                    resultado["valido"] = False
                    resultado["erros"].append("Dimensões/posição de crop_final inválidas")

                info = plano.get("video_info") or {}
                largura = info.get("largura")
                altura = info.get("altura")
                if isinstance(largura, int) and isinstance(altura, int):
                    if crop["x"] + crop["width"] > largura:
                        resultado["valido"] = False
                        resultado["erros"].append("crop_final ultrapassa largura original")
                    if crop["y"] + crop["height"] > altura:
                        resultado["valido"] = False
                        resultado["erros"].append("crop_final ultrapassa altura original")

    for campo in ("video_start", "audio_start"):
        valor = plano.get(campo, 0)
        if not isinstance(valor, (int, float)) or valor < 0:
            resultado["valido"] = False
            resultado["erros"].append(f"{campo} inválido")

    if resultado["valido"]:
        log.info("Plano de exportação validado com sucesso")
    else:
        log.warning(f"Plano rejeitado: {resultado}")

    return resultado
