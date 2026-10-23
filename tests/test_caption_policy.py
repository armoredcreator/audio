from __future__ import annotations

import json
import os

import pytest
import requests

from ArmoredIA.caption.generator import CaptionGenerator, CaptionGenerationError
from ArmoredIA.caption.policy import CaptionPolicyError, validate_caption
from ArmoredIA.providers.gemini import GeminiProvider
from ArmoredIA.providers.ollama import OllamaProvider
from ArmoredIA.providers.base import AIProviderError


def _gemini_response(captions):
    return {
        "candidates": [{
            "content": {"parts": [{"text": json.dumps({"captions": captions}, ensure_ascii=False)}]}
        }]
    }


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("ARMORED_IA_ENABLED", raising=False)
    monkeypatch.delenv("ARMORED_IA_CAPTION_ENABLED", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ARMORED_IA_MODEL", raising=False)
    monkeypatch.delenv("ARMORED_IA_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("ARMORED_IA_RETRY_DELAY", raising=False)


def test_policy_accepts_contextual_caption():
    assert validate_caption(
        "Cores organizadas 🧡\n#unhas #organizacao",
        product_name="Expositor de esmaltes com gavetas",
    ) == "Cores 🧡 organizadas\n#unhas #organizacao"


def test_policy_allows_natural_overlap_with_description():
    assert validate_caption(
        "Cantinho resistente 🧡\n#casa",
        product_name="Bancada Suspensa",
        product_context={"description": "estrutura de aço carbono resistente"},
    ) == "Cantinho 🧡 resistente\n#casa"


def test_policy_blocks_only_hard_leaks():
    invalid = (
        "Compre agora 🔥\n#oferta",
        "Batom matte ✨\n#beleza",
        "Olha isso ✨\n#batommatte",
        "Tramontina linda ✨\n#casa",
        "Pro 900 lindo ✨\n#casa",
        "Medida 10ml ✨\n#beleza",
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
        return Response()

    provider = GeminiProvider(requester=requester)
    candidates = provider.generate_candidates("caption", {
        "productName": "Batom Matte Vermelho",
    }, 10)

    assert calls["count"] == 1
    assert len(candidates) == 3


def test_caption_selects_valid_candidate_without_second_gemini_call(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")

    class Provider:
        def __init__(self):
            self.calls = 0
        def generate_candidates(self, task, context, limit):
            self.calls += 1
            return [
                "Compre agora 🔥\n#oferta",
                "Olha esse charme 🧡\n#beleza",
            ]

    provider = Provider()
    caption = CaptionGenerator(provider=provider).generate({
        "productName": "Batom Matte Vermelho",
    })

    assert caption == "Olha esse 🧡 charme\n#beleza"
    assert provider.calls == 1


def test_caption_ranks_all_valid_candidates_and_keeps_one_provider_call(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")

    class Provider:
        def __init__(self):
            self.calls = 0

        def generate_candidates(self, task, context, limit):
            self.calls += 1
            return [
                "Tudo bonito ✨\n#legal",
                "Cantinho prático 🧡\n#cozinha",
                "Visual charmoso ✨\n#casa",
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
        def generate_candidates(self, task, context, limit):
            self.calls += 1
            return [
                "Compre agora 🔥\n#oferta",
                "Batom matte ✨\n#beleza",
                "Olha embalagem linda 😍✨\n#beleza",
            ]

    provider = Provider()
    with pytest.raises(CaptionGenerationError):
        CaptionGenerator(provider=provider).generate({
            "productName": "Batom Matte Vermelho",
        })

    assert provider.calls == 2


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
            return _gemini_response(["Cantinho profissional 🧡\n#barbearia"])

    def requester(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] < 3:
            raise requests.exceptions.ReadTimeout("temporary")
        return Response()

    provider = GeminiProvider(requester=requester)
    caption = CaptionGenerator(provider=provider).generate({
        "productName": "Bancada Suspensa",
    })

    assert calls["count"] == 3
    assert caption == "Cantinho 🧡 profissional\n#barbearia"



def test_policy_forces_orange_heart_and_removes_model_emojis():
    assert validate_caption(
        "Mesa organizada 😍✨\n#casa #organizacao",
        product_name="Organizador de mesa",
    ) == "Mesa 🧡 organizada\n#casa #organizacao"


def test_policy_adds_orange_heart_when_model_returns_no_emoji():
    assert validate_caption(
        "Cantinho prático\n#cozinha",
        product_name="Bancada suspensa",
    ) == "Cantinho 🧡 prático\n#cozinha"



def test_policy_deduplicates_hashtags_case_insensitively():
    assert validate_caption(
        "Mesa organizada 😍\n#Casa #casa",
        product_name="Organizador de mesa",
    ) == "Mesa 🧡 organizada\n#Casa"



@pytest.mark.parametrize(
    "emoji",
    [
        "™️", "©️", "®️", "🇧🇷", "👩🏽‍💻", "1️⃣", "🏳️‍🌈", "🛍️",
    ],
)
def test_policy_removes_compound_and_presentation_emojis(emoji):
    result = validate_caption(
        f"Mesa bonita {emoji} 🧡\n#casa",
        product_name="Organizador",
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
            "Descascador de Legumes\n#cozinha",
            product_name="Descascador de Legumes Apontador",
        )


def test_policy_deduplicates_repeated_candidate_hashtags_and_emoji():
    assert validate_caption(
        "Mesa bonita 🛍️\n#Casa #casa",
        product_name="Organizador",
    ) == "Mesa 🧡 bonita\n#Casa"



def test_ollama_and_gemini_candidates_share_central_caption_policy(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_CAPTION_ENABLED", "1")
    monkeypatch.setenv("ARMORED_IA_PROVIDER", "ollama")
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "1")
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
    ).generate({"productName": "Bancada suspensa"})

    assert caption == "Cantinho 🧡 prático\n#cozinha"
    assert len(calls) == 1


def test_policy_keeps_exactly_one_orange_heart_when_model_returns_multiple_emojis():
    result = validate_caption(
        "Mesa bonita 🧡🧡😍✨\n#casa",
        product_name="Organizador de mesa",
    )
    assert result == "Mesa 🧡 bonita\n#casa"
    assert result.count("🧡") == 1


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
