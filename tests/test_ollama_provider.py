from __future__ import annotations

import json

import pytest
import requests

from ArmoredIA.providers.base import AIProviderError
from ArmoredIA.providers.gemini import GeminiProvider
from ArmoredIA.providers.ollama import OllamaProvider


class _Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            error = requests.HTTPError(f"HTTP {self.status_code}")
            error.response = self
            raise error

    def json(self):
        return self.payload


def test_ollama_provider_returns_structured_caption_candidates(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_OLLAMA_URL", "http://localhost:11434/")
    monkeypatch.setenv("ARMORED_IA_OLLAMA_MODEL", "qwen2.5:1.5b")
    calls = []

    def requester(url, **kwargs):
        calls.append((url, kwargs))
        return _Response({"response": json.dumps({
            "captions": ["Cantinho aconchegante\n#decor #casa", "Luz suave\n#quarto"]
        })})

    provider = OllamaProvider(requester=requester)
    result = provider.generate_candidates("caption", {"productName": "Abajur"}, 2)

    assert result == ["Cantinho aconchegante\n#decor #casa", "Luz suave\n#quarto"]
    assert calls[0][0] == "http://localhost:11434/api/generate"
    assert calls[0][1]["json"]["model"] == "qwen2.5:1.5b"
    assert calls[0][1]["json"]["stream"] is False
    assert calls[0][1]["json"]["format"]["properties"]["captions"]["minItems"] == 2
    assert calls[0][1]["json"]["format"]["properties"]["captions"]["maxItems"] == 2


def test_ollama_provider_rejects_non_caption_task():
    with pytest.raises(AIProviderError, match="task não suportada"):
        OllamaProvider(requester=lambda *_args, **_kwargs: None).generate_candidates("vision", {}, 3)


def test_ollama_provider_rejects_malformed_response():
    provider = OllamaProvider(requester=lambda *_args, **_kwargs: _Response({"response": "not json"}))
    with pytest.raises(AIProviderError, match="JSON estruturado"):
        provider.generate_candidates("caption", {}, 2)


def test_caption_generator_provider_is_configurable(monkeypatch):
    from ArmoredIA.caption.generator import CaptionGenerator

    monkeypatch.delenv("ARMORED_IA_PROVIDER", raising=False)
    assert isinstance(CaptionGenerator().provider, OllamaProvider)

    monkeypatch.setenv("ARMORED_IA_PROVIDER", "gemini")
    assert isinstance(CaptionGenerator().provider, GeminiProvider)

    monkeypatch.setenv("ARMORED_IA_PROVIDER", "ollama")
    assert isinstance(CaptionGenerator().provider, OllamaProvider)

    monkeypatch.setenv("ARMORED_IA_PROVIDER", "unknown")
    with pytest.raises(AIProviderError, match="ARMORED_IA_PROVIDER inválido"):
        CaptionGenerator()


def test_structured_model_fields_keep_hashtags_without_inventing_content():
    result = OllamaProvider._extract_candidates({"response": json.dumps({"captions": [
        {"text": "Cantinho sereno", "hashtags": ["#decor", "#casa"]}
    ]})}, 1)
    assert result == ["Cantinho sereno\n#decor #casa"]



def test_ollama_returns_invalid_candidate_for_shared_policy_to_reject(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "1")
    provider = OllamaProvider(
        requester=lambda *_args, **_kwargs: _Response({
            "response": json.dumps({"captions": ["Organizador de mesa simples"]})
        })
    )
    assert provider.generate_candidates("caption", {"productName": "Organizador"}, 1) == [
        "Organizador de mesa simples"
    ]


def test_ollama_returns_partial_candidate_batch_without_retrying_content(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "1")
    provider = OllamaProvider(
        requester=lambda *_args, **_kwargs: _Response({
            "response": json.dumps({"captions": ["Mesa organizada ✨ #Escritório"]})
        })
    )
    assert provider.generate_candidates("caption", {"productName": "Organizador"}, 2) == [
        "Mesa organizada ✨ #Escritório"
    ]



def test_ollama_provider_preserves_raw_candidates_for_shared_policy(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "1")
    provider = OllamaProvider(
        requester=lambda *_args, **_kwargs: _Response({
            "response": json.dumps({
                "captions": ["Mesa organizada ✨🛍️ #Casa #casa"]
            })
        })
    )

    assert provider.generate_candidates("caption", {"productName": "Organizador"}, 1) == [
        "Mesa organizada ✨🛍️ #Casa #casa"
    ]



def test_ollama_provider_passes_corrupted_unicode_to_shared_policy(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "1")
    provider = OllamaProvider(
        requester=lambda *_args, **_kwargs: _Response({
            "response": json.dumps({
                "captions": ["Mesa organizada ?? #Casa"]
            })
        })
    )

    assert provider.generate_candidates("caption", {"productName": "Organizador"}, 1) == [
        "Mesa organizada ?? #Casa"
    ]



def test_ollama_default_model_uses_validated_local_gemma(monkeypatch):
    monkeypatch.delenv("ARMORED_IA_OLLAMA_MODEL", raising=False)
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "1")
    calls = []

    def requester(url, **kwargs):
        calls.append(kwargs["json"]["model"])
        return _Response({"response": json.dumps({
            "captions": ["Mesa organizada #casa"]
        })})

    OllamaProvider(requester=requester).generate_candidates("caption", {}, 1)
    assert calls == ["gemma3:1b"]
