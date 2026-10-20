from __future__ import annotations

import unittest
from unittest.mock import patch

import requests

from ArmoredVision.service import ArmoredVision
from ArmoredVision.modules.v1.shopee_api import ShopeeProductNotFoundError
from armored_core.services import VisionUnresolvedError


class _NoOfferAPI:
    def get_exact_product(self, shop_id, item_id):
        raise ShopeeProductNotFoundError("empty nodes")


class VisionFailureClassificationTests(unittest.TestCase):
    def test_unrecognized_product_url_is_classified_separately(self):
        item = type("ItemStub", (), {"original_url": "https://s.shopee.com.br/example"})()
        with patch(
            "ArmoredVision.service.resolve_short_url",
            side_effect=ValueError("unsupported destination"),
        ):
            with self.assertRaises(VisionUnresolvedError) as caught:
                ArmoredVision(api=_NoOfferAPI()).identify(item)
        self.assertIn("[VISION:RESOLUTION_UNSUPPORTED]", str(caught.exception))
        self.assertIn("WAITING_VISION", str(caught.exception))

    def test_zero_affiliate_offers_does_not_claim_product_is_invalid(self):
        item = type("ItemStub", (), {"original_url": "https://shopee.com.br/item"})()
        resolved = type("Resolved", (), {"shop_id": "123", "item_id": "456"})()
        with patch("ArmoredVision.service.resolve_short_url", return_value=resolved):
            with self.assertRaises(VisionUnresolvedError) as caught:
                ArmoredVision(api=_NoOfferAPI()).identify(item)
        message = str(caught.exception)
        self.assertIn("[VISION:NO_AFFILIATE_OFFER]", message)
        self.assertIn("não comprova que o produto não exista", message)
        self.assertIn("futura recuperação", message)

    def test_resolver_timeout_remains_technical_error(self):
        item = type("ItemStub", (), {"original_url": "https://s.shopee.com.br/example"})()
        with patch(
            "ArmoredVision.service.resolve_short_url",
            side_effect=requests.Timeout("network timeout"),
        ):
            with self.assertRaises(requests.Timeout):
                ArmoredVision(api=_NoOfferAPI()).identify(item)


if __name__ == "__main__":
    unittest.main()
