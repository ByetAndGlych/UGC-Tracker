import unittest
from unittest.mock import Mock

from roblox_api import RobloxCatalogClient, normalize_catalog_item


class FakeResponse:
    ok = True
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload
        self.text = ""

    def json(self):
        return self.payload


class RobloxCatalogClientTests(unittest.TestCase):
    def test_collectible_search_uses_v2_collectibles_filters(self):
        session = Mock()
        session.headers = {}
        session.get.return_value = FakeResponse(
            {"data": [{"id": 123, "name": "Test Hat"}], "nextPageCursor": None}
        )
        client = RobloxCatalogClient(session=session)
        items = client.search_collectibles(keyword="hat", limit=10)
        self.assertEqual(items[0]["id"], 123)
        args, kwargs = session.get.call_args
        self.assertIn("/v2/search/items/details", args[0])
        self.assertEqual(kwargs["params"]["CategoryFilter"], 2)
        self.assertEqual(kwargs["params"]["SalesTypeFilter"], 1)
        self.assertEqual(kwargs["params"]["Keyword"], "hat")

    def test_pagination_uses_next_page_cursor(self):
        session = Mock()
        session.headers = {}
        session.get.side_effect = [
            FakeResponse({"data": [{"id": 1}], "nextPageCursor": "next"}),
            FakeResponse({"data": [{"id": 2}], "nextPageCursor": None}),
        ]
        client = RobloxCatalogClient(session=session)
        items = client.search_collectibles(limit=10, max_pages=2)
        self.assertEqual([item["id"] for item in items], [1, 2])
        self.assertEqual(session.get.call_args_list[1].kwargs["params"]["cursor"], "next")

    def test_thumbnail_lookup_maps_asset_ids_to_urls(self):
        session = Mock()
        session.headers = {}
        session.get.return_value = FakeResponse(
            {"data": [{"targetId": 123, "imageUrl": "https://images.example/123.png"}]}
        )
        client = RobloxCatalogClient(session=session)
        self.assertEqual(
            client.get_asset_thumbnails([123]),
            {123: "https://images.example/123.png"},
        )
        self.assertIn("thumbnails.roblox.com/v1/assets", session.get.call_args.args[0])

    def test_normalize_item_uses_v2_creator_fields(self):
        source = {
            "id": 7,
            "name": "Hat",
            "creatorName": "Maker",
            "creatorTargetId": 9,
            "lowestResalePrice": 123,
        }
        result = normalize_catalog_item(source)
        self.assertEqual(result["creator_name"], "Maker")
        self.assertEqual(result["creator_id"], 9)
        self.assertEqual(result["lowest_price"], 123)
        self.assertEqual(result["display_price"], 123)
        self.assertEqual(result["price_type"], "Lowest resale price")
        self.assertIs(result["raw"], source)

    def test_marks_creator_sale_as_available(self):
        result = normalize_catalog_item({"id": 9, "price": 50, "isForSale": True})
        self.assertTrue(result["is_for_sale"])

    def test_marks_reseller_only_item_as_available(self):
        result = normalize_catalog_item({"id": 10, "hasResellers": True})
        self.assertTrue(result["is_for_sale"])

    def test_excludes_item_with_no_sale_or_resellers(self):
        result = normalize_catalog_item({"id": 11, "price": None, "hasResellers": False, "isForSale": False})
        self.assertFalse(result["is_for_sale"])

    def test_catalog_price_is_not_mislabeled_as_resale(self):
        result = normalize_catalog_item({"id": 8, "name": "Catalog item", "price": 6500})
        self.assertEqual(result["display_price"], 6500)
        self.assertEqual(result["price_type"], "Catalog price (not confirmed resale)")

    def test_resale_data_uses_public_economy_endpoint(self):
        session = Mock()
        session.headers = {}
        session.get.return_value = FakeResponse({"recentAveragePrice": 125, "sales": 8})
        client = RobloxCatalogClient(session=session)
        result = client.get_resale_data(123)
        self.assertEqual(result["recentAveragePrice"], 125)
        self.assertIn("economy.roblox.com/v1/assets/123/resale-data", session.get.call_args.args[0])

    def test_resellers_use_collectible_item_id(self):
        session = Mock()
        session.headers = {}
        session.get.return_value = FakeResponse({"data": [{"price": 90}]})
        client = RobloxCatalogClient(session=session)
        result = client.get_resellers("collectible-abc", limit=10)
        self.assertEqual(result["data"][0]["price"], 90)
        self.assertIn("marketplace-sales/v1/item/collectible-abc/resellers", session.get.call_args.args[0])

    def test_lowest_price_uses_lower_creator_or_resale_price(self):
        result = normalize_catalog_item({
            "id": 12, "isForSale": True, "price": 100, "lowestResalePrice": 80
        })
        self.assertEqual(result["lowest_price"], 80)

    def test_rejects_unsupported_limit(self):
        client = RobloxCatalogClient(session=Mock(headers={}))
        with self.assertRaises(ValueError):
            client.search_collectibles(limit=15)


if __name__ == "__main__":
    unittest.main()
