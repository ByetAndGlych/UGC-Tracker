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
        self.assertEqual(kwargs["params"]["SalesTypeFilter"], 2)
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

    def test_catalog_price_is_not_mislabeled_as_resale(self):
        result = normalize_catalog_item({"id": 8, "name": "Catalog item", "price": 6500})
        self.assertEqual(result["display_price"], 6500)
        self.assertEqual(result["price_type"], "Catalog price (not confirmed resale)")

    def test_rejects_unsupported_limit(self):
        client = RobloxCatalogClient(session=Mock(headers={}))
        with self.assertRaises(ValueError):
            client.search_collectibles(limit=15)


if __name__ == "__main__":
    unittest.main()
