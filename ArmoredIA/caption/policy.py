from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
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
PRODUCT_STOP_WORDS = {
    "a", "as", "ao", "aos", "com", "da", "das", "de", "do", "dos", "e",
    "em", "para", "por", "sem", "um", "uma", "kit", "conjunto",
    "original", "novo", "nova", "olha", "esse", "essa", "isso",
}

_CONTEXT_FIELDS = (
    "productName", "category", "categoryName", "productCategory", "productType",
    "description", "usage", "useCase", "style", "appearance", "attributes",
)
_HASHTAG_CONTEXT_ALIASES = {
    "casa": {"casa", "cozinha", "bancada", "mesa", "sala", "quarto", "banheiro", "organizador", "utensilio", "decoracao"},
    "cozinha": {"cozinha", "bancada", "utensilio", "preparo", "culinaria", "cozinhar"},
    "decor": {"decor", "decoracao", "sala", "quarto", "ambiente", "casa"},
    "decoracao": {"decor", "decoracao", "sala", "quarto", "ambiente", "casa"},
    "organizacao": {"organizacao", "organizador", "gaveta", "prateleira", "armazenamento", "casa", "cozinha"},
    "moda": {"moda", "tenis", "calcado", "calcados", "sapato", "sandalia", "vestuario", "roupa", "feminino", "masculino", "casual", "camurca"},
    "beleza": {"beleza", "batom", "esmalte", "maquiagem", "cosmetico", "unha", "skincare"},
    "iluminacao": {"iluminacao", "luz", "abajur", "luminaria", "lampada"},
    "chinelo": {"calcado", "calcados", "sapato", "sandalia", "tenis", "bota", "sapatilha"},
    "sandalia": {"calcado", "calcados", "sapato", "chinelo", "tenis", "bota", "sapatilha"},
    "tenis": {"calcado", "calcados", "sapato", "sandalia", "chinelo", "bota", "sapatilha"},
    "calcado": {"calcado", "calcados", "sapato", "sandalia", "chinelo", "tenis", "bota", "sapatilha"},
}
_GENERIC_HASHTAGS = {
    "achados", "comprinhas", "viral", "fyp", "trending", "top", "legal",
    "show", "insta", "instagram", "produto", "produtos", "oficial",
}
_NEUTRAL_REACTION_WORDS = {
    "beleza", "bonito", "bonita", "lindo", "linda", "charme", "charmoso",
    "charmosa", "elegante", "visual", "estilo",
    "look", "cantinho", "canto", "olhar", "detalhe", "especial",
}
_GENERATION_REACTION_WORDS = {
    "bonito", "bonita", "lindo", "linda", "charmoso", "charmosa",
    "elegante", "especial",
}
_MAIN_CONTEXT_ALIASES = {
    "chinelo": {"calcado", "calcados", "uso", "casual"},
    "sandalia": {"calcado", "calcados", "uso", "casual"},
    "tenis": {"calcado", "calcados", "moda", "casual"},
    "calcado": {"calcado", "calcados", "moda", "casual"},
    "organizada": {"organizador", "organizacao"},
    "organizado": {"organizador", "organizacao"},
    "organizadas": {"organizador", "organizacao"},
    "organizados": {"organizador", "organizacao"},
    "moda": {"feminino", "masculino", "casual", "tenis", "calcado", "sapato", "sandalia", "roupa", "vestuario"},
    "casa": {"cozinha", "bancada", "mesa", "sala", "quarto", "banheiro", "organizador", "utensilio"},
    "cozinha": {"bancada", "utensilio", "preparo", "culinaria", "cozinhar"},
    "beleza": {"batom", "esmalte", "maquiagem", "cosmetico", "unha", "skincare"},
    "decoracao": {"decor", "sala", "quarto", "ambiente", "casa"},
    "organizacao": {"organizador", "gaveta", "prateleira", "armazenamento", "casa", "cozinha"},
    "iluminacao": {"luz", "abajur", "luminaria", "lampada"},
}


@dataclass(frozen=True)
class CaptionNormalization:
    caption: str
    corrections: tuple[str, ...]


class CaptionPolicyError(ValueError):
    pass


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()


def _tokens(value: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", _fold(value)) if len(token) >= 4]


def _product_phrases(product_name: str) -> set[str]:
    tokens = [token for token in _tokens(product_name) if token not in PRODUCT_STOP_WORDS]
    phrases: set[str] = set()
    for size in (2, 3):
        for index in range(0, max(0, len(tokens) - size + 1)):
            phrases.add(" ".join(tokens[index:index + size]))
    return phrases


def _hashtag_sequences(product_name: str) -> set[str]:
    tokens = [token for token in _tokens(product_name) if token not in PRODUCT_STOP_WORDS]
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
    # With only a product title, the first two meaningful tokens are
    # ambiguous: marketplaces commonly place a brand, line, or model before
    # the product type. Fail closed on those tokens until Vision supplies
    # structured brand/model metadata; later contextual words remain usable.
    has_structured_identity = any(
        str(context.get(key) or "").strip()
        for key in ("brand", "brandName", "model", "modelName")
    )
    if not has_structured_identity:
        category_tokens: set[str] = set()
        for key in ("category", "categoryName", "productCategory", "productType"):
            category_tokens.update(_tokens(str(context.get(key) or "")))
        title_tokens = [
            token for token in _tokens(str(context.get("productName") or ""))
            if token not in PRODUCT_STOP_WORDS
        ]
        if len(title_tokens) >= 3:
            # Clear product nouns remain usable even in legacy rows whose
            # persisted context contains no structured category.
            known_category_types: set[str] = {
                "calcado", "calcados", "chinelo", "sandalia", "sapato",
                "tenis", "bota", "sapatilha", "roupa", "vestuario",
                "luz", "abajur", "luminaria", "lampada", "batom",
                "esmalte", "maquiagem", "cosmetico", "unha", "skincare",
                "utensilio", "panela", "frigideira", "talher", "organizador",
            }
            # Older approved Vision rows may have only a product title and a
            # broad category. Recognize the category's product nouns before
            # applying the conservative first-two-token brand/model rule;
            # otherwise ordinary titles such as "Tênis Feminino ..." and
            # "Luminária de mesa ..." lose the very noun needed by the Policy.
            if category_tokens & {
                "calcado", "calcados", "calcadofeminino", "calcadomasculino",
                "moda", "vestuario",
            }:
                known_category_types.update({
                    "calcado", "calcados", "chinelo", "sandalia", "sapato",
                    "tenis", "bota", "sapatilha", "roupa", "vestuario",
                })
            if category_tokens & {"iluminacao", "luz", "lampada"}:
                known_category_types.update({"luz", "abajur", "luminaria", "lampada"})
            if category_tokens & {"beleza", "cosmetico", "cosmeticos"}:
                known_category_types.update({
                    "batom", "esmalte", "maquiagem", "cosmetico", "unha", "skincare",
                })
            if category_tokens & {"cozinha", "utensilio", "utensilios"}:
                known_category_types.update({
                    "utensilio", "panela", "frigideira", "cozinha", "talher",
                })
            leaked.update(token for token in title_tokens[:2] if token not in known_category_types)
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
    technical_terms = r"\b(?:ph|voltagem|especifica(?:cao|coes))\b"
    return bool(re.search(pattern, folded) or re.search(technical_terms, folded))


def _context_tokens(product_name: str, product_context: dict[str, Any] | None) -> set[str]:
    values = [str(product_name or "")]
    if product_context:
        values.extend(
            str(product_context.get(key) or "")
            for key in _CONTEXT_FIELDS
            if product_context.get(key) not in (None, "", [], {})
        )
    excluded = _brand_model_tokens(product_context)
    tokens: set[str] = set()
    for value in values:
        tokens.update(token for token in _tokens(value) if token not in excluded)
    return tokens


def _main_phrase_is_contextual(
    words: list[str],
    *,
    product_name: str,
    product_context: dict[str, Any] | None,
) -> bool:
    context = _context_tokens(product_name, product_context)
    grounded = False
    for raw in words:
        token = _fold(raw)
        if token in PRODUCT_STOP_WORDS or len(token) < 3:
            continue
        stem = token[:-1] if len(token) >= 5 and token[-1:] in {"a", "o", "e", "s"} else token
        if token in context or stem in {
            (candidate[:-1] if len(candidate) >= 5 and candidate[-1:] in {"a", "o", "e", "s"} else candidate)
            for candidate in context
        }:
            grounded = True
            continue
        if (_MAIN_CONTEXT_ALIASES.get(token, set()) | _MAIN_CONTEXT_ALIASES.get(stem, set())) & context:
            grounded = True
            continue
        if token in _NEUTRAL_REACTION_WORDS or (
            token.endswith("s") and token[:-1] in _NEUTRAL_REACTION_WORDS
        ):
            continue
        return False
    return grounded


def hashtag_context_score(
    hashtag: str,
    *,
    product_name: str = "",
    product_context: dict[str, Any] | None = None,
) -> int:
    """Return positive only when the hashtag has persisted product-context support."""
    tag = _fold(str(hashtag or "").lstrip("#"))
    tag_tokens = [token for token in re.findall(r"[a-z0-9]+", tag) if len(token) >= 3]
    if not tag_tokens or any(token in _GENERIC_HASHTAGS for token in tag_tokens):
        return 0
    context = _context_tokens(product_name, product_context)
    score = 0
    for token in tag_tokens:
        if token in context or any(
            len(token) >= 5
            and abs(len(token) - len(other)) <= 2
            and (token.startswith(other) or other.startswith(token))
            for other in context
        ):
            score += 2
            continue
        aliases = _HASHTAG_CONTEXT_ALIASES.get(token, set())
        if not aliases and token.endswith("s"):
            aliases = _HASHTAG_CONTEXT_ALIASES.get(token[:-1], set())
        if aliases & context:
            score += 1
            continue
        for alias, related in _HASHTAG_CONTEXT_ALIASES.items():
            if token == alias and related & context:
                score += 1
                break
    return score if score == len(tag_tokens) * 2 or score > 0 else 0


def caption_generation_vocabulary(
    product_context: dict[str, Any] | None,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Return grounded text terms and hashtags safe to constrain an IA schema."""
    context = product_context or {}
    product_name = str(context.get("productName") or "")
    excluded = _brand_model_tokens(context)
    grounded: dict[str, str] = {}
    for key in _CONTEXT_FIELDS:
        value = context.get(key)
        if value in (None, "", [], {}):
            continue
        for surface in WORD_RE.findall(str(value)):
            folded = _fold(surface)
            if (
                len(folded) < 4
                or any(char.isdigit() for char in folded)
                or folded in PRODUCT_STOP_WORDS
                or folded in excluded
                or _technical_leak(surface)
                or _commercial_or_package_match(surface)
            ):
                continue
            grounded.setdefault(folded, surface)

    text_words: list[str] = []
    seen_text: set[str] = set()
    for surface in (*grounded.values(), *_GENERATION_REACTION_WORDS):
        folded = _fold(surface)
        if folded not in seen_text:
            seen_text.add(folded)
            text_words.append(surface)
    hashtags = [
        surface
        for surface in grounded.values()
        if hashtag_context_score(
            surface,
            product_name=product_name,
            product_context=context,
        ) > 0
    ]
    return tuple(text_words[:64]), tuple(hashtags[:32])


def _center_heart(main: str) -> str:
    words = list(WORD_RE.finditer(main))
    if not words:
        return main.strip()
    middle = (len(words) - 1) // 2
    insertion = words[middle].end()
    while insertion < len(main) and not main[insertion].isspace():
        insertion += 1
    return f"{main[:insertion].rstrip()} 🧡 {main[insertion:].lstrip()}"


def normalize_caption(
    caption: str,
    *,
    product_name: str = "",
    product_context: dict[str, Any] | None = None,
) -> CaptionNormalization:
    """Apply formatting-only, auditable fixes. Never invent words or hashtags."""
    raw = str(caption or "")
    corrections: list[str] = []
    text = raw.replace("\r\n", "\n").replace("\r", "\n")
    if text != raw:
        corrections.append("line_endings_normalized")
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)
    text = re.sub(r"(?<=[,;:])(?=\S)", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text).strip()
    if text != raw.strip():
        corrections.append("whitespace_and_punctuation_normalized")

    matches = list(HASHTAG_RE.finditer(text))
    hashtags = [match.group(1) for match in matches]
    main = HASHTAG_RE.sub("", text)
    main = re.sub(r"\s+", " ", main).strip()

    unique: list[str] = []
    seen: set[str] = set()
    for tag in hashtags:
        folded = _fold(tag)
        if folded in seen:
            corrections.append("duplicate_hashtag_removed")
            continue
        seen.add(folded)
        if hashtag_context_score(
            tag, product_name=product_name, product_context=product_context,
        ) <= 0:
            corrections.append("unrelated_hashtag_removed")
            continue
        unique.append(tag)
    if len(unique) > 2:
        ranked = sorted(
            enumerate(unique),
            key=lambda entry: (
                -hashtag_context_score(
                    entry[1], product_name=product_name, product_context=product_context,
                ),
                entry[0],
            ),
        )
        keep_indices = {index for index, _tag in ranked[:2]}
        if len(unique) > 2:
            corrections.append("hashtags_limited_to_two_contextual_tags")
        unique = [tag for index, tag in enumerate(unique) if index in keep_indices]

    main_without_heart = main.replace("🧡", "")
    without_emoji = _remove_emojis(main_without_heart)
    if without_emoji != main_without_heart:
        corrections.append("excess_emojis_removed")
    without_emoji = re.sub(r"\s+", " ", without_emoji).strip()
    centered = _center_heart(without_emoji)
    raw_heart_count = raw.count("🧡")
    marker = "ARMORED_ORANGE_HEART_MARKER"
    raw_main_with_marker = main.replace("🧡", marker)
    raw_main_keep_heart = _remove_emojis(raw_main_with_marker).replace(marker, "🧡")
    raw_main_keep_heart = re.sub(r"\s+", " ", raw_main_keep_heart).strip()
    if raw_heart_count == 0:
        corrections.append("orange_heart_inserted")
    elif raw_heart_count != 1 or centered != raw_main_keep_heart:
        corrections.append("orange_heart_centered")

    normalized = centered
    if unique:
        normalized += "\n" + " ".join("#" + tag for tag in unique)
    return CaptionNormalization(
        caption=normalized,
        corrections=tuple(dict.fromkeys(corrections)),
    )


def validate_caption(
    caption: str,
    *,
    product_name: str = "",
    product_context: dict[str, Any] | None = None,
) -> str:
    text = str(caption or "")
    if not text:
        raise CaptionPolicyError("legenda vazia")
    if text.count("🧡") != 1:
        raise CaptionPolicyError("a legenda precisa ter exatamente um coração laranja")
    emoji_free = text.replace("🧡", "")
    if EMOJI_RE.search(emoji_free) or EMOJI_COMPONENT_RE.search(emoji_free):
        raise CaptionPolicyError("a legenda contém emoji além do coração laranja")
    if text != text.strip() or "\r" in text or re.search(r"[ \t]{2,}", text):
        raise CaptionPolicyError("formatação não normalizada")
    if re.search(r"\s+[,.;:!?]", text) or re.search(r"[,;:](?=\S)", text):
        raise CaptionPolicyError("pontuação não normalizada")
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

    hashtags = raw_hashtags
    if len({_fold(tag) for tag in hashtags}) != len(hashtags):
        raise CaptionPolicyError("hashtag duplicada")
    if not 1 <= len(hashtags) <= 2:
        raise CaptionPolicyError("a legenda precisa ter 1 ou 2 hashtags distintas")
    if any(len(tag) > 20 for tag in hashtags):
        raise CaptionPolicyError("hashtag longa demais")

    hard_error = _commercial_or_package_match(text)
    if hard_error:
        raise CaptionPolicyError(hard_error)

    main_text = HASHTAG_RE.sub("", text)
    main = main_text.strip()
    words = WORD_RE.findall(main.replace("🧡", ""))
    if not 2 <= len(words) <= 3:
        raise CaptionPolicyError("texto principal deve conter 2 ou 3 palavras")
    if not _main_phrase_is_contextual(
        words,
        product_name=product_name,
        product_context=product_context,
    ):
        raise CaptionPolicyError("texto principal contém termo sem suporte no contexto Vision")

    heart_source = re.sub(r"\s+", " ", main.replace("🧡", "").strip())
    heart_centered = _center_heart(heart_source)
    if heart_centered != main:
        raise CaptionPolicyError("coração laranja fora da posição central")

    if _technical_leak(text):
        raise CaptionPolicyError("legenda revela medida ou especificação técnica")

    # Check copied title phrases only against the main sentence. Separate
    # contextual hashtags can appear next to one another and must not be
    # accidentally recombined into a product-title phrase.
    main_only = HASHTAG_RE.sub("", text)
    folded_caption = _fold(_remove_emojis(main_only))
    normalized_caption_tokens = [
        token for token in WORD_RE.findall(folded_caption)
        if token.casefold() not in PRODUCT_STOP_WORDS
    ]
    normalized_caption = " ".join(normalized_caption_tokens)

    for phrase in _product_phrases(product_name):
        if re.search(rf"\b{re.escape(phrase)}\b", normalized_caption):
            raise CaptionPolicyError("legenda copia expressão distintiva do produto")

    hashtag_tokens = {_fold(tag) for tag in hashtags}
    if hashtag_tokens & _hashtag_sequences(product_name):
        raise CaptionPolicyError("hashtag recompõe explicitamente o produto")

    if _brand_model_tokens(product_context) & set(re.findall(r"[a-z0-9]+", folded_caption)):
        raise CaptionPolicyError("legenda revela marca ou modelo do contexto V1")

    for tag in hashtags:
        if hashtag_context_score(
            tag, product_name=product_name, product_context=product_context,
        ) <= 0:
            raise CaptionPolicyError("hashtag sem relação confiável com o produto")

    # Description/category overlap is deliberately allowed. With only 2–3
    # words available, rejecting ordinary contextual vocabulary causes false
    # negatives and unnecessary Gemini calls.
    if not hashtag_matches or text != f"{main}\n{' '.join('#' + tag for tag in hashtags)}":
        raise CaptionPolicyError("legenda não está na forma canonical da Policy")
    return text
