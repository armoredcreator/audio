from __future__ import annotations

import unittest
from unittest.mock import Mock

import requests

from ArmoredVision.modules.v1.shopee_resolver import resolve_short_url


class ShopeeResolverSecurityTests(unittest.TestCase):
    def test_rejects_non_shopee_host_before_network_access(self):
        session = Mock()
        for url in (
            "https://notshopee.com.br/product/123/456",
            "https://shopee.com.br.attacker.test/product/123/456",
            "https://attacker.test/shopee.com.br/product/123/456",
            "file:///product/123/456",
        ):
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    resolve_short_url(url, session=session)
        session.get.assert_not_called()

    def test_accepts_canonical_product_path_only_on_shopee_host(self):
        result = resolve_short_url("https://shopee.com.br/product/123/456")
        self.assertEqual((result.shop_id, result.item_id), ("123", "456"))

    def test_rejects_redirect_to_untrusted_host_before_requesting_it(self):
        session = Mock()
        first_response = Mock()
        first_response.status_code = 302
        first_response.headers = {"Location": "https://attacker.test/product/123/456"}
        session.get.return_value = first_response

        with self.assertRaisesRegex(ValueError, "domínio não autorizado"):
            resolve_short_url("https://shope.ee/short", session=session)

        session.get.assert_called_once()
        first_response.close.assert_called_once()

    def test_follows_redirects_only_inside_allowlisted_shopee_hosts(self):
        session = Mock()
        redirect = Mock()
        redirect.status_code = 302
        redirect.headers = {"Location": "https://shopee.com.br/product/123/456"}
        final = Mock()
        final.status_code = 200
        final.url = "https://shopee.com.br/product/123/456"
        session.get.side_effect = [redirect, final]

        result = resolve_short_url("https://shope.ee/short", session=session)

        self.assertEqual((result.shop_id, result.item_id), ("123", "456"))
        self.assertEqual(session.get.call_count, 2)
        self.assertTrue(all(call.kwargs["allow_redirects"] is False for call in session.get.call_args_list))
        redirect.close.assert_called_once()
        final.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
