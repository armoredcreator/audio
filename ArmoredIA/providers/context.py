from __future__ import annotations

from typing import Any


def context_text(product: dict[str, Any]) -> str:
    """Render the same persisted Vision context for every caption provider."""
    fields = (
        ("Nome interno", "productName"),
        ("Categoria", "category"),
        ("Nome da categoria", "categoryName"),
        ("Categoria do produto", "productCategory"),
        ("Tipo de produto", "productType"),
        ("Uso", "usage"),
        ("Caso de uso", "useCase"),
        ("Estilo", "style"),
        ("Aparência", "appearance"),
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
