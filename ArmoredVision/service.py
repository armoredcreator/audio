from __future__ import annotations

from armored_core.models import Item
from armored_core.services import VisionDuplicateError, VisionResult, VisionUnresolvedError

from .modules.v1.shopee_api import (
    ShopeeAffiliateAPI,
    ShopeeAPIError,
    ShopeeProductNotFoundError,
)
from .modules.v1.shopee_resolver import resolve_short_url


class ArmoredVision:
    """Affiliate identity stage.

    It owns Shopee resolution/API concerns only. Pipeline state remains in
    armored_core and no JSON state from the legacy Vision is used.
    """

    def __init__(self, api=None, db=None, no_offer_cache_ttl_seconds: int = 21600):
        # Coordinator creates one Vision service per run. Keep the API client
        # and the SQLite-backed negative lookup cache for its lifetime so
        # repeated exact products do not consume another API call immediately.
        self.api = api
        self.db = db
        self.no_offer_cache_ttl_seconds = max(0, int(no_offer_cache_ttl_seconds))

    @staticmethod
    def _ia_context(product: dict) -> dict:
        keys = (
            "productName", "itemId", "shopId", "shopName", "productCatIds",
            "priceMin", "priceMax", "sales", "ratingStar", "brand",
            "brandName", "model", "modelName", "description", "attributes",
            "technicalCharacteristics", "imageUrl",
        )
        return {
            key: product[key]
            for key in keys
            if key in product and product[key] not in (None, "", [], {})
        }

    def identify(self, item: Item) -> VisionResult:
        original = (item.original_url or "").strip()
        if not original:
            raise VisionUnresolvedError(
                "Vision não recebeu link Shopee; vídeo preservado em WAITING_VISION"
            )

        # Resolve/classify the incoming URL before creating the authenticated
        # Affiliate API client. Non-product URLs must remain WAITING_VISION
        # even in test environments or installations without API credentials.
        api = self.api
        try:
            if api is None:
                # Classify the URL without requiring an API client or credentials.
                resolved = resolve_short_url(original)
            else:
                resolved = resolve_short_url(original, session=getattr(api, "session", None))
        except ValueError as exc:
            raise VisionUnresolvedError(
                "[VISION:RESOLUTION_UNSUPPORTED] "
                f"Vision V1 não conseguiu identificar um produto Shopee exato: {original}; "
                "item preservado em WAITING_VISION"
            ) from exc

        product_key = f"{resolved.shop_id}:{resolved.item_id}"
        if self.db is not None:
            owner = self.db.find_product_identity_owner(
                {"shopId": resolved.shop_id, "itemId": resolved.item_id}
            )
            if owner:
                raise VisionDuplicateError(owner)
        if self.db is not None and self.db.has_active_vision_no_offer(product_key):
            raise VisionUnresolvedError(
                "[VISION:NO_AFFILIATE_OFFER_CACHED] "
                f"consulta exata recente sem oferta para {product_key}; "
                "Vision manteve o item em WAITING_VISION sem repetir a consulta"
            )

        # Instantiate only after a product-shaped URL has been resolved and no
        # active exact-product negative result can short-circuit the API call.
        # The same client is retained across identify() calls.
        if api is None:
            api = ShopeeAffiliateAPI()
            self.api = api

        try:
            product = api.get_exact_product(resolved.shop_id, resolved.item_id)
        except ShopeeProductNotFoundError as exc:
            if self.db is not None:
                self.db.record_vision_product_lookup(
                    product_key,
                    item.content_id,
                    "OFFER_ABSENT",
                    ttl_seconds=self.no_offer_cache_ttl_seconds,
                )
            raise VisionUnresolvedError(
                "[VISION:NO_AFFILIATE_OFFER] "
                f"Vision V1 não encontrou oferta de afiliado para o produto Shopee "
                f"{resolved.shop_id}:{resolved.item_id}; "
                "isso não comprova que o produto não exista ou esteja indisponível; "
                "item preservado para futura recuperação"
            ) from exc
        if self.db is not None:
            self.db.record_vision_product_lookup(
                product_key,
                item.content_id,
                "OFFER_FOUND",
                ttl_seconds=0,
            )

        # The incoming URL is identification input only. The final affiliate
        # link must come from the exact V1-resolved product, never from a
        # third-party affiliate short URL supplied by the source message.
        try:
            affiliate_url = str(api.affiliate_link_for_product(product) or "").strip()
        except ShopeeAPIError as exc:
            if "não possui productLink canônico" in str(exc):
                raise VisionUnresolvedError(
                    "[VISION:PRODUCT_LINK_MISSING] "
                    "Vision encontrou o produto, mas não há link canônico "
                    "para gerar oferta de afiliado; item preservado em WAITING_VISION"
                ) from exc
            raise

        if not affiliate_url.lower().startswith(("https://", "http://")):
            raise VisionUnresolvedError(
                "[VISION:INVALID_AFFILIATE_URL] "
                "Vision não recebeu URL de afiliado válida; "
                "item preservado em WAITING_VISION"
            )

        identifier = str(
            product.get("productName")
            or product.get("itemId")
            or f"{resolved.shop_id}_{resolved.item_id}"
        )

        return VisionResult(
            identifier,
            affiliate_url,
            affiliate_urls=(affiliate_url,),
            publication_caption=None,
            ia_context=self._ia_context(product),
        )


def build(**_kwargs):
    return ArmoredVision()
