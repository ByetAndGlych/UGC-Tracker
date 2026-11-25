"""Small command-line entry point for the first UGC-Tracker milestone."""
from __future__ import annotations

import argparse
import json

from roblox_api import RobloxAPIError, RobloxCatalogClient, normalize_catalog_item


def main() -> int:
    parser = argparse.ArgumentParser(description="Search Roblox collectible catalog items.")
    parser.add_argument("--keyword", default="", help="Optional item search phrase")
    parser.add_argument("--limit", type=int, choices=(10, 28, 30), default=10)
    parser.add_argument("--pages", type=int, default=1, help="Maximum pages to retrieve")
    args = parser.parse_args()

    client = RobloxCatalogClient()
    try:
        results = client.search_collectibles(
            keyword=args.keyword,
            limit=args.limit,
            max_pages=args.pages,
        )
    except RobloxAPIError as exc:
        parser.exit(1, f"Roblox API error: {exc}\n")

    print(json.dumps([normalize_catalog_item(item) for item in results], indent=2))
    print(f"\nRetrieved {len(results)} catalog item(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
