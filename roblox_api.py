"""Read-only client for Roblox's public avatar Marketplace catalog API.

This first milestone deliberately implements catalog search only. Resale/RAP
endpoints are kept out until their current public behavior is verified.
"""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin

import requests


CATALOG_SEARCH_URL = "https://catalog.roblox.com/v1/search/items/details"
DEFAULT_TIMEOUT = 20
MAX_RETRIES = 4


class RobloxAPIError(RuntimeError):
    """Raised when Roblox returns an unusable API response."""


class RobloxCatalogClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.session.headers.update(
            {
                "User-Agent": "UGC-Tracker/0.1 (read-only public catalog client)",
                "Accept": "application/json",
            }
        )

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise RobloxAPIError(f"Network request failed: {exc}") from exc
                time.sleep(min(2 ** attempt, 8))
                continue

            if response.status_code == 429 or 500 <= response.status_code < 600:
                if attempt >= self.max_retries:
                    raise RobloxAPIError(
                        f"Roblox API remained unavailable (HTTP {response.status_code})."
                    )
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 30) if retry_after else min(2 ** attempt, 8)
                except ValueError:
                    delay = min(2 ** attempt, 8)
                time.sleep(delay)
                continue

            if not response.ok:
                raise RobloxAPIError(
                    f"Roblox API returned HTTP {response.status_code}: {response.text[:300]}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise RobloxAPIError("Roblox API returned invalid JSON.") from exc

            if not isinstance(payload, dict):
                raise RobloxAPIError("Roblox API returned an unexpected response shape.")
            return payload

        raise RobloxAPIError("Request failed after retries.")

    def search_collectibles(
        self,
        keyword: str = "",
        limit: int = 10,
        sort_type: int = 0,
        max_pages: int = 1,
    ) -> list[dict[str, Any]]:
        """Search collectible catalog items; returns raw item dictionaries.

        Roblox currently documents limits of 10, 28, or 30 for this endpoint.
        Pagination uses nextPageCursor returned by Roblox.
        """
        if limit not in (10, 28, 30):
            raise ValueError("limit must be 10, 28, or 30")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")

        items: list[dict[str, Any]] = []
        cursor: str | None = None

        for _ in range(max_pages):
            params: dict[str, Any] = {
                "Category": 2,  # Collectibles
                "SortType": sort_type,
                "Limit": limit,
            }
            if keyword.strip():
                params["Keyword"] = keyword.strip()
            if cursor:
                params["Cursor"] = cursor

            payload = self._get_json(CATALOG_SEARCH_URL, params)
            page_items = payload.get("data", [])
            if not isinstance(page_items, list):
                raise RobloxAPIError("Catalog response did not contain an item list in 'data'.")

            items.extend(item for item in page_items if isinstance(item, dict))
            cursor = payload.get("nextPageCursor")
            if not cursor:
                break

        return items


def normalize_catalog_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return a stable, display-friendly subset without discarding raw fields."""
    creator = item.get("creator") or {}
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "description": item.get("description"),
        "creator_name": creator.get("name") if isinstance(creator, dict) else None,
        "creator_id": creator.get("id") if isinstance(creator, dict) else None,
        "price": item.get("price"),
        "lowest_price": item.get("lowestPrice"),
        "is_limited": item.get("isLimited"),
        "is_limited_unique": item.get("isLimitedUnique"),
        "collectible_item_id": item.get("collectibleItemId"),
        "thumbnail": item.get("thumbnail"),
        "raw": item,
    }
