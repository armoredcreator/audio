from __future__ import annotations

import json
import os
import time
from typing import Any

import requests

from .base import AIProviderError


def _context_text(product: dict[str, Any]) -> str:
    fields = (
        ("Nome interno", "productName"),
        ("ID do item", "itemId"),
        ("ID da loja", "shopId"),
        ("Loja", "shopName"),
        ("Categorias", "productCatIds"),
        ("Preço mínimo", "priceMin"),
        ("Preço máximo", "priceMax"),
        ("Vendas", "sales"),
        ("Avaliação", "ratingStar"),
        ("Marca", "brand"),
        ("Marca alternativa", "brandName"),
        ("Modelo", "model"),
        ("Modelo alternativo", "modelName"),
        ("Descrição", "description"),
        ("Atributos", "attributes"),
        ("Características", "technicalCharacteristics"),
    )
    lines = []
    for label, key in fields:
        value = product.get(key)
        if value not in (None, "", [], {}):
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


class OllamaProvider:
    """Local Ollama adapter implementing the same caption-candidate contract as Gemini."""

    def __init__(self, requester=None):
        self.requester = requester or requests.post

    @staticmethod
    def _extract_candidates(data: dict[str, Any], limit: int) -> list[str]:
        try:
            raw_text = data["response"]
            payload = json.loads(str(raw_text))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise AIProviderError("Ollama não retornou JSON estruturado") from exc

        raw = payload.get("captions") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            raise AIProviderError("Ollama não retornou a lista de candidatas")

        # Match Gemini's contract: return raw, distinct candidates. The shared
        # CaptionGenerator policy is the sole authority for content validation.
        candidates: list[str] = []
        seen: set[str] = set()
        for entry in raw:
            value = entry.get("text") if isinstance(entry, dict) else entry
            caption = str(value or "").strip()
            if not caption:
                continue
            if caption in seen:
                continue
            seen.add(caption)
            candidates.append(caption)
            if len(candidates) >= max(1, min(limit, 10)):
                break
        if not candidates:
            raise AIProviderError("Ollama retornou zero candidatas não vazias")
        return candidates

    def generate_candidates(self, task: str, context: dict[str, Any], limit: int) -> list[str]:
        if task != "caption":
            raise AIProviderError(f"Ollama task não suportada: {task}")

        base_url = (os.getenv("ARMORED_IA_OLLAMA_URL") or "http://localhost:11434").strip().rstrip("/")
        model = (os.getenv("ARMORED_IA_OLLAMA_MODEL") or "qwen3:8b").strip()
        if not model:
            raise AIProviderError("ARMORED_IA_OLLAMA_MODEL está vazio")

        prompt = """
Você recebe contexto de um produto que JÁ FOI identificado pela Vision V1.
A identidade da Vision V1 é a fonte de verdade. Você NÃO identifica, corrige,
substitui ou renomeia o produto.

Gere opções de reação curta, natural e específica para este item.
Use categoria, uso, ambiente, estilo, aparência e demais sinais disponíveis.
Evite frases coringa quando o contexto permitir algo melhor.

PARA CADA OPÇÃO:
- Português do Brasil.
- Exatamente 2 ou 3 palavras no texto principal.
- NÃO inclua emoji; a política do código acrescenta sempre o coração laranja fixo 🧡.
- Exatamente 1 ou 2 hashtags.
- Hashtags podem indicar categoria, uso, ambiente ou característica.
- NÃO use embalagem, tampa, frasco ou lacre.
- NÃO use linguagem de venda, urgência, promoção ou desconto.
- NÃO escreva marca, modelo, quantidade, medida ou voltagem.
- NÃO copie uma expressão distintiva do título.
- Não use hashtags para contornar as regras.
- Uma palavra genérica da categoria é permitida.

Retorne SOMENTE um objeto JSON válido com a chave "captions" e exatamente a quantidade solicitada de legendas. Cada item deve conter apenas o texto principal e 1 ou 2 hashtags no final, sem emoji.
""".strip()
        prompt += (
            "\n\nCONTEXTO V1 — identidade já resolvida:\n"
            + _context_text(context)
            + f"\nPúblico-alvo: {os.getenv('ARMORED_IA_AUDIENCE', 'público brasileiro de descoberta e lifestyle')}"
            + f"\nGere EXATAMENTE {min(max(1, limit), 10)} opções distintas. Não retorne blocos separados nem texto fora do JSON."
        )

        schema = {
            "type": "object",
            "properties": {
                "captions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": min(max(1, limit), 10),
                    "maxItems": min(max(1, limit), 10),
                }
            },
            "required": ["captions"],
        }
        attempts = max(1, int(os.getenv("ARMORED_IA_MAX_ATTEMPTS", "3")))
        retry_delay = max(0.0, float(os.getenv("ARMORED_IA_RETRY_DELAY", "2")))
        timeout = max(1, int(os.getenv("ARMORED_IA_OLLAMA_TIMEOUT", "180")))

        for attempt in range(1, attempts + 1):
            try:
                response = self.requester(
                    f"{base_url}/api/generate",
                    json={
                        "model": model,
                        "prompt": prompt,
                        "stream": False,
                        "format": schema,
                        "options": {"temperature": 0.7},
                    },
                    timeout=timeout,
                )
                response.raise_for_status()
                return self._extract_candidates(response.json(), min(max(1, limit), 10))
            except requests.RequestException as exc:
                if attempt < attempts:
                    time.sleep(retry_delay)
                    continue
                raise AIProviderError(
                    f"Ollama indisponível após {attempt} tentativa(s): {exc}"
                ) from exc
            except AIProviderError:
                if attempt < attempts:
                    time.sleep(retry_delay)
                    continue
                raise

        raise AIProviderError("Ollama indisponível após todas as tentativas")
