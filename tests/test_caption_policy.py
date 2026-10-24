from __future__ import annotations

import json
import os

import pytest
import requests

from ArmoredIA.caption.generator import CaptionGenerator, CaptionGenerationError
from ArmoredIA.caption.policy import (
    CaptionPolicyError,
    caption_generation_vocabulary,
    normalize_caption,
    validate_caption,
)
from ArmoredIA.providers.gemini import GeminiProvider
from ArmoredIA.providers.ollama import OllamaProvider
from ArmoredIA.providers.base import AIProviderError


def _gemini_response(captions):
    return {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({"captions": captions}, ensure_ascii=False)}]}
        }]
    }


def _normalize_validate(raw, *, product_name="", product_context=None):
    result = normalize_caption(
        raw, product_name=product_name, product_context=product_context,
    )
    assert validate_caption(
        result.caption, product_name=product_name, product_context=product_context,
    ) == result.caption
    return result.caption


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("ARMORED_IA_ENABLED", raising=False)
    monkeypatch.delenv("ARMORED_IA_CAPTION_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ARMORED_IA_MODEL", raising=False)
    monkeypatch.delenv("ARMORED_IA_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("ARMORED_IA_RETRY_DELAY", raising=False)


def test_policy_accepts_contextual_caption():
    assert _normalize_validate(
        "Unhas organizadas 🧡\n#unhas #organizacao",
        product_name="Expositor de esmaltes com gavetas",
        product_context={"categoryName": "unhas beleza organização"},
    ) == "Unhas 🧡 organizadas\n#unhas #organizacao"


def test_policy_allows_natural_overlap_with_description():
    assert _normalize_validate(
        "Cantinho resistente 🧡\n#casa",
        product_name="Bancada Suspensa",
        product_context={"description": "estrutura de aço carbono resistente"},
    ) == "Cantinho 🧡 resistente\n#casa"


def test_policy_uses_vision_category_to_ground_footwear_words_and_hashtags():
    context = {
        "productName": "Chinelo feminino com detalhe em strass",
        "category": "calçados",
        "usage": "uso casual",
    }
    assert _normalize_validate(
        "Chinelo casual\n#chinelo",
        product_name=context["productName"],
        product_context=context,
    ) == "Chinelo 🧡 casual\n#chinelo"

    assert _normalize_validate(
        "Chinelos charmosos.\n#calçados #moda",
        product_name=context["productName"],
        product_context=context,
    ) == "Chinelos 🧡 charmosos.\n#calçados #moda"


def test_policy_does_not_treat_unverified_product_attributes_as_neutral_reactions():
    with pytest.raises(CaptionPolicyError, match="sem suporte"):
        validate_caption(
            "Acidificante 🧡 delicado\n#acidificante",
            product_name="Amend Essencial Acidificante Máscara Equilíbrio do PH - 250g",
            product_context={"productName": "Amend Essencial Acidificante Máscara Equilíbrio do PH - 250g"},
        )


def test_local_generation_vocabulary_excludes_brand_model_and_technical_terms():
    text_words, hashtags = caption_generation_vocabulary({
        "productName": "Amend Essencial Acidificante Máscara Equilíbrio do PH - 250g",
        "shopName": "AMEND OFICIAL",
        "category": "beleza",
    })
    folded_text = {word.casefold() for word in text_words}
    folded_tags = {word.casefold() for word in hashtags}
    assert {"acidificante", "máscara", "equilíbrio", "bonita", "elegante"} <= folded_text
    assert "delicado" not in folded_text and "delicada" not in folded_text
    assert "detalhe" not in folded_text and "visual" not in folded_text
    assert "amend" not in folded_text and "essencial" not in folded_text
    assert "ph" not in folded_text and "250g" not in folded_text
    assert {"acidificante", "máscara", "equilíbrio"} <= folded_tags
    assert "amend" not in folded_tags and "ph" not in folded_tags


def test_policy_blocks_only_hard_leaks():
    invalid = (
        "Compre agora 🔥\n#oferta",
        "Batom matte ✨\n#beleza",
        "Olha isso ✨\n#batommatte",
        "Tramontina linda ✨\n#casa",
        "Pro 900 lindo ✨\n#casa",
        "Medida 10ml ✨\n#beleza",
        "Equilíbrio do 🧡 PH\n#acidificante",
    )
    for caption in invalid:
        with pytest.raises(CaptionPolicyError):
            validate_caption(
                caption,
                product_name="Batom Matte Vermelho",
                product_context={"brand": "Tramontina", "model": "Pro 900"},
            )


def test_provider_makes_one_request_and_returns_up_to_ten(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "configured")
    calls = {"count": 0}
    prompts = []

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return _gemini_response([
                "Compre agora 🔥\n#oferta",
                "Olha esse charme 🧡\n#beleza",
                "Visual bonito 😍\n#beleza",
            ])

    def requester(*args, **kwargs):
        calls["count"] += 1
        prompts.append(kwargs["json"]["contents"][0]["parts"][0]["text"])
        return Response()

    provider = GeminiProvider(requester=requester)
    candidates = provider.generate_candidates("caption", {
        "productName": "Batom Matte Vermelho",
    }, 10)

    assert calls["count"] == 1
    assert len(candidates) == 3
    assert "Cada frase deve soar natural" in prompts[0]
    assert "não junte palavras desconexas" in prompts[0]


def test_caption_selects_valid_candidate_without_second_gemini_call(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")

    class Provider:
        def __init__(self):
            self.calls = 0
        def generate_candidates(self, task, context, limit):
            self.calls += 1
            return [
                "Compre agora 🔥\n#oferta",
                "Beleza charmosa 🧡\n#beleza",
            ]

    provider = Provider()
    caption = CaptionGenerator(provider=provider).generate({
        "productName": "Batom Matte Vermelho",
        "category": "beleza maquiagem",
    })

    assert caption == "Beleza 🧡 charmosa\n#beleza"
    assert provider.calls == 1


def test_caption_ranks_all_valid_candidates_and_keeps_one_provider_call(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")

    class Provider:
        def __init__(self):
            self.calls = 0

        def generate_candidates(self, task, context, limit):
            self.calls += 1
            return [
                    "Cozinha bonita ✨\n#cozinha",
                "Cantinho prático 🧡\n#cozinha",
                "Cozinha especial ✨\n#casa",
            ]

    provider = Provider()
    generator = CaptionGenerator(provider=provider)
    result = generator.generate_with_evidence({
        "productName": "Bancada Suspensa",
        "description": "solução prática para cozinha organizada",
    })

    assert provider.calls == 1
    assert result.selection.index == 2
    assert result.caption == "Cantinho 🧡 prático\n#cozinha"
    assert len(result.evaluations) == 3
    assert all(evaluation.policy_valid for evaluation in result.evaluations)
    assert result.evaluations[1].selected is True
    assert result.evaluations[1].score > result.evaluations[0].score


def test_all_policy_rejections_retry_once_then_recovery(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")

    class Provider:
        def __init__(self):
            self.calls = 0
            self.feedback_seen = []
            self.previous_seen = []
        def generate_candidates(self, task, context, limit):
            self.calls += 1
            self.feedback_seen.append(context.get("__policy_retry_feedback"))
            self.previous_seen.append(context.get("__policy_retry_previous_candidates"))
            if self.calls == 2:
                return [
                    "Cantinho prático 🧡\n#cozinha",
                    "Cozinha prática 🧡\n#cozinha",
                ]
            return [
                "Compre cozinha 🔥\n#cozinha",
                "Bancada suspensa ✨\n#cozinha",
                "Embalagem linda 😍✨\n#cozinha",
            ]

    provider = Provider()
    result = CaptionGenerator(provider=provider).generate_with_evidence({
        "productName": "Bancada Suspensa",
        "category": "cozinha",
        "usage": "cozinha prática",
    })

    assert provider.calls == 2
    assert provider.feedback_seen[0] is None
    assert provider.feedback_seen[1]
    assert provider.previous_seen[0] is None
    assert len(provider.previous_seen[1]) == 3
    assert any(
        "linguagem comercial" in reason or "distintiva" in reason
        for reason in provider.feedback_seen[1]
    )
    assert result.provider_requests == 2
    assert result.feedback_rounds
    assert result.caption in {
        "Cantinho 🧡 prático\n#cozinha",
        "Cozinha 🧡 prática\n#cozinha",
    }


def test_sparse_context_feedback_constrains_retry_to_grounded_name_terms():
    guidance = CaptionGenerator._feedback_guidance(
        ("texto principal contém termo sem suporte no contexto Vision", "hashtag sem relação confiável"),
        product_context={"productName": "Máscara Acidificante PH 250g"},
    )
    assert any("contém apenas o nome do produto" in item for item in guidance)
    assert any("sequência consecutiva distintiva" in item for item in guidance)
    assert any("hashtag de uma palavra" in item for item in guidance)
    assert any("palavras seguras posteriores" in item.casefold() for item in guidance)
    assert any("reação estética neutra" in item for item in guidance)
    assert any("não use alegações de desempenho" in item.casefold() for item in guidance)


def test_policy_normalization_is_idempotent_and_only_fixes_formatting():
    context = {"category": "cozinha organizacao", "productName": "Organizador de mesa"}
    first = normalize_caption(
        "  Mesa 😍   bonita 🧡✨\r\n#casa #casa #achados #organizacao  ",
        product_name="Organizador de mesa",
        product_context=context,
    )
    second = normalize_caption(
        first.caption,
        product_name="Organizador de mesa",
        product_context=context,
    )
    assert first.caption == "Mesa 🧡 bonita\n#casa #organizacao"
    assert first.corrections
    assert second.caption == first.caption
    assert second.corrections == ()


@pytest.mark.parametrize(
    "caption",
    [
        "Mesa bonita\n#casa",
        "Mesa 🧡 bonita 🧡\n#casa",
        "🧡 Mesa bonita\n#casa",
        "Mesa 🧡 bonita\n#casa texto",
        "Mesa 🧡 bonita\n#Casa #casa",
    ],
)
def test_policy_remains_strict_when_normalizer_is_bypassed(caption):
    with pytest.raises(CaptionPolicyError):
        validate_caption(
            caption,
            product_name="Organizador de mesa",
            product_context={"category": "casa cozinha organizacao"},
        )


def test_normalizer_does_not_invent_words_or_contextless_hashtags():
    result = normalize_caption("Texto alegre 😍\n#viral #achados")
    assert result.caption == "Texto 🧡 alegre"
    with pytest.raises(CaptionPolicyError):
        validate_caption(result.caption)


def test_policy_rejects_unsupported_product_attributes_even_when_hashtag_is_valid():
    with pytest.raises(CaptionPolicyError, match="sem suporte no contexto Vision"):
        validate_caption(
            "Vestido 🧡 floral\n#moda",
            product_name="Chinelo Feminino Casual",
            product_context={"productName": "Chinelo Feminino Casual"},
        )


def test_policy_does_not_treat_unrelated_long_hashtag_compounds_as_contextual():
    result = normalize_caption(
        "Estilo leve 🧡\n#veraofashion",
        product_name="Chinelo feminino verão",
    )
    assert result.caption == "Estilo 🧡 leve"
    with pytest.raises(CaptionPolicyError):
        validate_caption(result.caption, product_name="Chinelo feminino verão")


def test_sparse_title_prefix_is_reserved_for_possible_brand_or_product_line():
    context = {"productName": "Amend Essencial Acidificante Máscara Equilíbrio do PH 250g"}
    accepted = normalize_caption(
        "Máscara acidificante #acidificante",
        product_name=context["productName"],
        product_context=context,
    ).caption
    assert validate_caption(
        accepted,
        product_name=context["productName"],
        product_context=context,
    ) == "Máscara 🧡 acidificante\n#acidificante"

    unsafe = normalize_caption(
        "Máscara Essencial #essencial",
        product_name=context["productName"],
        product_context=context,
    ).caption
    with pytest.raises(CaptionPolicyError):
        validate_caption(
            unsafe,
            product_name=context["productName"],
            product_context=context,
        )


def test_provider_retries_transport_but_not_policy(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_POLICY_RETRIES", "0")
    monkeypatch.setenv("GEMINI_API_KEY", "configured")
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("ARMORED_IA_RETRY_DELAY", "0")

    calls = {"count": 0}

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return _gemini_response(["Barbearia prática 🧡\n#barbearia"])

    def requester(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise requests.exceptions.ReadTimeout("temporary")
        return Response()

    provider = GeminiProvider(requester=requester)
    caption = CaptionGenerator(provider=provider).generate({
        "productName": "Bancada Suspensa",
        "usage": "barbearia prática",
    })

    assert calls["count"] == 3
    assert caption == "Barbearia 🧡 prática\n#barbearia"



def test_policy_forces_orange_heart_and_removes_model_emojis():
    assert _normalize_validate(
        "Mesa organizada 😍✨\n#casa #organizacao",
        product_name="Organizador de mesa",
        product_context={"categoryName": "organização doméstica"},
    ) == "Mesa 🧡 organizada\n#casa #organizacao"


def test_policy_adds_orange_heart_when_model_returns_no_emoji():
    assert _normalize_validate(
        "Cantinho prático\n#cozinha",
        product_name="Bancada suspensa",
        product_context={"category": "cozinha", "usage": "prática"},
    ) == "Cantinho 🧡 prático\n#cozinha"



def test_policy_deduplicates_hashtags_case_insensitively():
    assert _normalize_validate(
        "Mesa organizada 😍\n#Casa #casa",
        product_name="Organizador de mesa",
        product_context={"categoryName": "organização doméstica"},
    ) == "Mesa 🧡 organizada\n#Casa"



@pytest.mark.parametrize(
    "emoji",
    [
        "™️", "©️", "®️", "🇧🇷", "👩🏽‍💻", "1️⃣", "🏳️‍🌈", "🛍️",
    ],
)
def test_policy_removes_compound_and_presentation_emojis(emoji):
    result = _normalize_validate(
        f"Mesa bonita {emoji} 🧡\n#casa",
        product_name="Mesa organizadora",
        product_context={"categoryName": "casa organização"},
    )
    assert result == "Mesa 🧡 bonita\n#casa"


@pytest.mark.parametrize(
    "caption",
    [
        "Mesa bonita #casa texto",
        "Mesa bonita #casa #",
        "Mesa bonita #casa##decor",
        "Mesa bonita #casa\ufffd",
        "Mesa bonita ?? #casa",
    ],
)
def test_policy_rejects_malformed_hashtags_extra_text_and_corrupted_unicode(caption):
    with pytest.raises(CaptionPolicyError):
        validate_caption(caption, product_name="Organizador")


def test_policy_detects_distinctive_phrase_when_title_stopwords_are_present():
    with pytest.raises(CaptionPolicyError, match="expressão distintiva"):
        validate_caption(
            "Descascador de 🧡 Legumes\n#cozinha",
            product_name="Descascador de Legumes Apontador",
            product_context={"categoryName": "cozinha utensílios"},
        )


def test_policy_deduplicates_repeated_candidate_hashtags_and_emoji():
    assert _normalize_validate(
        "Mesa bonita 🛍️\n#Casa #casa",
        product_name="Mesa organizadora",
        product_context={"categoryName": "casa organização"},
    ) == "Mesa 🧡 bonita\n#Casa"



def test_ollama_and_gemini_candidates_share_central_caption_policy(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_PROVIDER", "ollama")
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("ARMORED_IA_MAX_CANDIDATES", "3")
    monkeypatch.setenv("ARMORED_IA_RETRY_DELAY", "0")

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return {"response": json.dumps({"captions": [
                "Compre agora #oferta",
                "Cantinho prático 🛍️ #cozinha",
            ]}, ensure_ascii=False)}

    provider = OllamaProvider(requester=lambda *_args, **_kwargs: Response())
    caption = CaptionGenerator(provider=provider).generate({
        "productName": "Bancada suspensa",
        "category": "cozinha",
        "usage": "prática",
    })
    assert caption == "Cantinho 🧡 prático\n#cozinha"



def test_gemini_and_ollama_use_the_same_final_policy(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    monkeypatch.setenv("GEMINI_API_KEY", "configured")
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("ARMORED_IA_RETRY_DELAY", "0")
    calls = []

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return _gemini_response([
                "Compre agora 🔥 #oferta",
                "Cantinho prático ™️ #cozinha",
            ])

    def requester(*args, **kwargs):
        calls.append(1)
        return Response()

    caption = CaptionGenerator(
        provider=GeminiProvider(requester=requester)
    ).generate({"productName": "Bancada suspensa", "category": "cozinha", "usage": "prática"})

    assert caption == "Cantinho 🧡 prático\n#cozinha"
    assert len(calls) == 1


def test_policy_keeps_exactly_one_orange_heart_when_model_returns_multiple_emojis():
    result = _normalize_validate(
        "Mesa bonita 🧡🧡😍✨\n#casa",
        product_name="Organizador de mesa",
    )
    assert result == "Mesa 🧡 bonita\n#casa"
    assert result.count("🧡") == 1


def test_policy_is_strict_while_normalizer_records_safe_formatting_repairs():
    raw = "Mesa bonita 😍🧡🧡  #Casa #casa"
    context = {"categoryName": "organização doméstica"}
    with pytest.raises(CaptionPolicyError, match="exatamente um coração"):
        validate_caption(raw, product_name="Organizador de mesa", product_context=context)
    result = normalize_caption(
        raw, product_name="Organizador de mesa", product_context=context,
    )
    assert result.caption == "Mesa 🧡 bonita\n#Casa"
    assert "excess_emojis_removed" in result.corrections
    assert "duplicate_hashtag_removed" in result.corrections
    assert "orange_heart_centered" in result.corrections
    assert validate_caption(
        result.caption, product_name="Organizador de mesa", product_context=context,
    ) == result.caption


def test_policy_rejects_unrelated_generic_hashtag_and_does_not_invent_replacement():
    result = normalize_caption(
        "Mesa bonita 🧡 #viral",
        product_name="Organizador de mesa",
    )
    assert result.caption == "Mesa 🧡 bonita"
    assert result.corrections == ("unrelated_hashtag_removed", "orange_heart_centered")
    with pytest.raises(CaptionPolicyError, match="1 ou 2 hashtags"):
        validate_caption(result.caption, product_name="Organizador de mesa")


def test_normalizer_does_not_rewrite_words_or_repair_policy_content():
    result = normalize_caption(
        "Compre imediatamente 500ml 😍 #oferta #casa",
        product_name="Garrafa térmica para casa",
    )
    assert " ".join(result.caption.split("\n", 1)[0].replace("🧡", "").split()) == "Compre imediatamente 500ml"
    with pytest.raises(CaptionPolicyError, match="linguagem comercial"):
        validate_caption(result.caption, product_name="Garrafa térmica para casa")


@pytest.mark.parametrize(
    ("extract", "payload"),
    [
        (GeminiProvider._extract_candidates, {"candidates": []}),
        (OllamaProvider._extract_candidates, {"response": "{}"}),
    ],
)
def test_providers_reject_structurally_invalid_caption_payloads(extract, payload):
    with pytest.raises(AIProviderError):
        extract(payload, 10)


@pytest.mark.parametrize(
    ("extract", "payload"),
    [
        (GeminiProvider._extract_candidates, {"candidates": [{
            "content": {"parts": [{"text": json.dumps({"captions": []})}]}
        }]}),
        (OllamaProvider._extract_candidates, {"response": json.dumps({"captions": []})}),
    ],
)
def test_providers_reject_empty_caption_lists(extract, payload):
    with pytest.raises(AIProviderError):
        extract(payload, 10)
