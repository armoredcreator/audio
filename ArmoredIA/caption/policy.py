from __future__ import annotations

import re
import unicodedata
from typing import Any

FORBIDDEN_WORDS = {"embalagem", "tampa", "frasco", "lacre"}
SALES_WORDS = {
    "compre", "comprar", "garanta", "garantir", "imperdivel", "imperdível",
    "aproveite", "oferta", "ofertas", "promocao", "promoção", "desconto",
    "corra", "nao perca", "não perca",
}
# Broad Unicode emoji ranges plus legacy emoji-presentation symbols.
# Also remove selectors, joiners, modifiers and keycap/tag components.
EMOJI_RE = re.compile(
    "[\\U0001F1E6-\\U0001F1FF\\U0001F300-\\U0001FAFF"
    "\\U00002300-\\U000023FF\\U00002600-\\U000027BF"
    "\\U00002B00-\\U00002BFF\\U0001FC00-\\U0001FFFF"
    "\\u00A9\\u00AE\\u203C\\u2049\\u2122\\u2139\\u3030\\u303D\\u3297\\u3299]"
)
EMOJI_COMPONENT_RE = re.compile(
    "[\\uFE0E\\uFE0F\\u200D\\u20E3\\U0001F3FB-\\U0001F3FF"
    "\\U000E0020-\\U000E007F]"
)
def _remove_emojis(text: str) -> str:
    value = str(text or "")
    for key in "#*0123456789":
        value = value.replace(key + "\ufe0f\u20e3", "")
        value = value.replace(key + "\u20e3", "")
    value = EMOJI_RE.sub("", value)
    return EMOJI_COMPONENT_RE.sub("", value)
HASHTAG_RE = re.compile(r"(?<!\w)#([\wÀ-ÿ]+)", re.UNICODE)
WORD_RE = re.compile(r"[A-Za-zÀ-ÿ0-9]+(?:['-][A-Za-zÀ-ÿ0-9]+)?", re.UNICODE)


class CaptionPolicyError(ValueError):
    pass


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", _fold(value)) if len(token) >= 4]


def _product_phrases(product_name: str) -> set[str]:
    stop = {
        "a", "as", "ao", "aos", "com", "da", "das", "de", "do", "dos", "e",
        "em", "para", "por", "sem", "um", "uma", "kit", "conjunto",
        "original", "novo", "nova",
    }
    tokens = [token for token in _tokens(product_name) if token not in stop]
    phrases: set[str] = set()
    for size in (2, 3):
        for index in range(0, max(0, len(tokens) - size + 1)):
            phrases.add(" ".join(tokens[index:index + size]))
    return phrases


def _hashtag_sequences(product_name: str) -> set[str]:
    stop = {
        "a", "as", "ao", "aos", "com", "da", "das", "de", "do", "dos", "e",
        "em", "para", "por", "sem", "um", "uma", "kit", "conjunto",
        "original", "novo", "nova",
    }
    tokens = [token for token in _tokens(product_name) if token not in stop]
    sequences: set[str] = set()
    for size in range(2, min(3, len(tokens)) + 1):
        for index in range(0, len(tokens) - size + 1):
            sequences.add("".join(tokens[index:index + size]))
    return sequences


def _brand_model_tokens(context: dict[str, Any] | None) -> set[str]:
    if not context:
        return set()
    leaked: set[str] = set()
    for key in ("brand", "brandName"):
        value = context.get(key)
        if value:
            leaked.update(_tokens(str(value)))
    for key in ("model", "modelName"):
        value = context.get(key)
        if value:
            for token in re.findall(r"[a-z0-9]+", _fold(str(value))):
                if len(token) >= 2:
                    leaked.add(token)
    return leaked


def _commercial_or_package_match(text: str) -> str | None:
    folded = _fold(text)
    for word in FORBIDDEN_WORDS:
        if re.search(rf"\b{re.escape(_fold(word))}\b", folded):
            return f"palavra proibida: {word}"
    for word in SALES_WORDS:
        folded_word = _fold(word)
        if re.search(rf"(?<![a-z0-9]){re.escape(folded_word)}(?![a-z0-9])", folded):
            return f"linguagem comercial proibida: {word}"
    return None


def _technical_leak(text: str) -> bool:
    folded = _fold(text)
    pattern = r"\b\d+(?:[\.,]\d+)?\s?(?:ml|l|litros?|cm|mm|m|kg|g|v|volts?|w|watts?)\b"
    return bool(re.search(pattern, folded))


def validate_caption(
    caption: str,
    *,
    product_name: str = "",
    product_context: dict[str, Any] | None = None,
) -> str:
    text = str(caption or "").replace("\r", "").strip()
    text = re.sub(r"^[*_~\s]+|[*_~\s]+$", "", text)
    if not text:
        raise CaptionPolicyError("legenda vazia")
    if "\ufffd" in text or re.search(r"\?{2,}", text):
        raise CaptionPolicyError("resposta contém Unicode corrompido")

    hashtag_matches = list(HASHTAG_RE.finditer(text))
    raw_hashtags = [match.group(1) for match in hashtag_matches]
    if text.count("#") != len(hashtag_matches):
        raise CaptionPolicyError("hashtag malformada")
    if hashtag_matches:
        if text[hashtag_matches[-1].end():].strip():
            raise CaptionPolicyError("texto extra após as hashtags")
        for left, right in zip(hashtag_matches, hashtag_matches[1:]):
            if text[left.end():right.start()].strip():
                raise CaptionPolicyError("hashtags devem ficar juntas no final")

    hashtags: list[str] = []
    seen_hashtags: set[str] = set()
    for tag in raw_hashtags:
        folded_tag = tag.casefold()
        if folded_tag not in seen_hashtags:
            seen_hashtags.add(folded_tag)
            hashtags.append(tag)
    if not 1 <= len(hashtags) <= 2:
        raise CaptionPolicyError("a legenda precisa ter 1 ou 2 hashtags distintas")
    if any(len(tag) > 20 for tag in hashtags):
        raise CaptionPolicyError("hashtag longa demais")

    hard_error = _commercial_or_package_match(text)
    if hard_error:
        raise CaptionPolicyError(hard_error)

    main_text = HASHTAG_RE.sub("", text)
    main = re.sub(r"\s+", " ", _remove_emojis(main_text)).strip()
    words = WORD_RE.findall(_remove_emojis(main))
    if not 2 <= len(words) <= 3:
        raise CaptionPolicyError("texto principal deve conter 2 ou 3 palavras")

    if _technical_leak(text):
        raise CaptionPolicyError("legenda revela medida ou especificação técnica")

    folded_caption = _fold(_remove_emojis(text))

    for phrase in _product_phrases(product_name):
        if re.search(rf"\b{re.escape(phrase)}\b", folded_caption):
            raise CaptionPolicyError("legenda copia expressão distintiva do produto")

    hashtag_tokens = {_fold(tag) for tag in hashtags}
    if hashtag_tokens & _hashtag_sequences(product_name):
        raise CaptionPolicyError("hashtag recompõe explicitamente o produto")

    if _brand_model_tokens(product_context) & set(re.findall(r"[a-z0-9]+", folded_caption)):
        raise CaptionPolicyError("legenda revela marca ou modelo do contexto V1")

    # Description/category overlap is deliberately allowed. With only 2–3
    # words available, rejecting ordinary contextual vocabulary causes false
    # negatives and unnecessary Gemini calls.
    # One central heart: A 🧡 B for two words; A B 🧡 C for three.
    # Preserve the user's punctuation and wording instead of rebuilding words.
    matches = list(WORD_RE.finditer(main))
    middle = (len(matches) - 1) // 2
    insertion = matches[middle].end()
    next_word = matches[middle + 1].start()
    while insertion < next_word and not main[insertion].isspace():
        insertion += 1
    centered = f"{main[:insertion].rstrip()} 🧡 {main[insertion:].lstrip()}"
    return f"{centered}\n{' '.join('#' + tag for tag in hashtags)}"
