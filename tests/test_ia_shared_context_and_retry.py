from __future__ import annotations

import json

import pytest
import requests

from ArmoredIA.caption.policy import validate_caption
from ArmoredIA.providers.base import AIProviderError
from ArmoredIA.providers.context import context_text
from ArmoredIA.providers.gemini import GeminiProvider, _context_text as gemini_context
from ArmoredIA.providers.ollama import OllamaProvider, _context_text as ollama_context
from ArmoredIA.providers.transport import retryable_failure


class Response:
    def __init__(self, status=200, invalid_json=False):
        self.status_code = status
        self.closed = False
        self.invalid_json = invalid_json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("private-url-must-not-be-persisted", response=self)

    def json(self):
        if self.invalid_json:
            raise ValueError("invalid JSON")
        return {"response": json.dumps({"captions": ["Canto bonito #casa"]})}

    def close(self):
        self.closed = True


@pytest.fixture(autouse=True)
def environment(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "3")
    monkeypatch.setenv("ARMORED_IA_RETRY_DELAY", "0")
    monkeypatch.delenv("ARMORED_IA_OLLAMA_THINK", raising=False)


def test_context_is_canonical_and_legacy_private_imports_remain_compatible():
    assert gemini_context is context_text
    assert ollama_context is context_text
    product = {
        "productName": "Example", "itemId": "20", "shopId": "10", "shopName": "Shop",
        "productCatIds": [1, 2], "priceMin": 0, "priceMax": 10, "sales": 0,
        "ratingStar": 4, "brand": "Brand", "brandName": "Alternate Brand", "model": "X1",
        "modelName": "Y1", "description": "Description", "attributes": {"color": "blue"},
        "technicalCharacteristics": ["detail"], "ignored": "must not leak",
    }
    expected = (
        "Nome interno: Example\nID do item: 20\nID da loja: 10\nLoja: Shop\n"
        "Categorias: [1, 2]\nPreço mínimo: 0\nPreço máximo: 10\nVendas: 0\n"
        "Avaliação: 4\nMarca: Brand\nMarca alternativa: Alternate Brand\n"
        "Modelo: X1\nModelo alternativo: Y1\nDescrição: Description\n"
        "Atributos: {'color': 'blue'}\nCaracterísticas: ['detail']"
    )
    assert context_text(product) == expected
    assert gemini_context(product) == ollama_context(product) == expected
    assert context_text({"productName": None, "description": "", "attributes": {}, "productCatIds": []}) == ""
    assert GeminiProvider._retryable is retryable_failure


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_ollama_rejected_requests_do_not_retry_and_close_response(status):
    response = Response(status)
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return response
    with pytest.raises(AIProviderError) as error:
        OllamaProvider(requester=request).generate_candidates("caption", {}, 2)
    assert len(calls) == 1
    assert response.closed
    assert "private-url" not in str(error.value)
    assert f"HTTP {status}" in str(error.value)


def test_ollama_transient_failure_retries_and_closes_before_next_request():
    responses = [Response(503), Response()]
    calls = []
    def request(*args, **kwargs):
        if calls:
            assert responses[0].closed
        response = responses[len(calls)]
        calls.append(kwargs)
        return response
    assert OllamaProvider(requester=request).generate_candidates("caption", {}, 1) == ["Canto bonito #casa"]
    assert len(calls) == 2
    assert all(response.closed for response in responses)


def test_ollama_invalid_json_is_bounded_and_all_responses_closed(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_MAX_ATTEMPTS", "2")
    responses = []
    def request(*args, **kwargs):
        response = Response(invalid_json=True)
        responses.append(response)
        return response
    with pytest.raises(AIProviderError, match="JSON estruturado"):
        OllamaProvider(requester=request).generate_candidates("caption", {}, 1)
    assert len(responses) == 2
    assert all(response.closed for response in responses)


@pytest.mark.parametrize(("value", "expected"), [(None, None), ("false", False), ("0", False), ("true", True), ("1", True)])
def test_ollama_thinking_is_only_changed_by_explicit_configuration(monkeypatch, value, expected):
    if value is not None:
        monkeypatch.setenv("ARMORED_IA_OLLAMA_THINK", value)
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        return Response()
    OllamaProvider(requester=request).generate_candidates("caption", {}, 1)
    if value is None:
        assert "think" not in calls[0]["json"]
    else:
        assert calls[0]["json"]["think"] is expected


def test_invalid_ollama_thinking_config_fails_before_generation(monkeypatch):
    monkeypatch.setenv("ARMORED_IA_OLLAMA_THINK", "unknown")
    def forbidden(*args, **kwargs):
        raise AssertionError("invalid config must fail before external request")
    with pytest.raises(AIProviderError, match="ARMORED_IA_OLLAMA_THINK"):
        OllamaProvider(requester=forbidden).generate_candidates("caption", {}, 1)


@pytest.mark.parametrize(("raw", "expected"), [
    ("Canto bonito #casa", "Canto 🧡 bonito\n#casa"),
    ("Canto bonito 🧡 #casa", "Canto 🧡 bonito\n#casa"),
    ("Canto 🧡 bonito #casa", "Canto 🧡 bonito\n#casa"),
    ("Olha esse charme #casa", "Olha esse 🧡 charme\n#casa"),
    ("Canto,bonito #casa", "Canto, 🧡 bonito\n#casa"),
    ("Canto, bonito! #Casa #casa", "Canto, 🧡 bonito!\n#Casa"),
])
def test_caption_heart_is_central_idempotent_and_preserves_text(raw, expected):
    caption = validate_caption(raw)
    assert caption == expected
    assert caption.count("🧡") == 1
    assert validate_caption(caption) == caption
