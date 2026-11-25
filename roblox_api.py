"""Read-only client for Roblox's public avatar Marketplace catalog API.

Catalog search and thumbnail lookup are public GET requests. Resale/RAP
endpoints are intentionally separate until their current behavior is verified.
"""
from __future__ import annotations

import time
import threading
from typing import Any

import requests


CATALOG_SEARCH_URL = "https://catalog.roblox.com/v2/search/items/details"
THUMBNAILS_URL = "https://thumbnails.roblox.com/v1/assets"
ECONOMY_URL = "https://economy.roblox.com/v1/assets"
RESELLERS_URL = "https://apis.roblox.com/marketplace-sales/v1/item"
DEFAULT_TIMEOUT = 20
MAX_RETRIES = 4

# Share Roblox's rate-limit cooldown across scanner and live-search clients.
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_UNTIL = 0.0


def _wait_for_rate_limit(max_wait: float | None = None) -> bool:
    started = time.monotonic()
    while True:
        with _RATE_LIMIT_LOCK:
            delay = _RATE_LIMIT_UNTIL - time.monotonic()
        if delay <= 0:
            return True
        if max_wait is not None and time.monotonic() - started + delay > max_wait:
            return False
        time.sleep(min(delay, 5))


def _set_rate_limit(seconds: float) -> None:
    global _RATE_LIMIT_UNTIL
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_UNTIL = max(_RATE_LIMIT_UNTIL, time.monotonic() + seconds)


class RobloxAPIError(RuntimeError):
    """Raised when Roblox returns an unusable API response."""


class RobloxCatalogClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        max_retries: int = MAX_RETRIES,
        max_rate_limit_wait: float | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.max_rate_limit_wait = max_rate_limit_wait
        self.session.headers.update(
            {
                "User-Agent": "UGC-Tracker/0.2 (read-only public catalog client)",
                "Accept": "application/json",
            }
        )

    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            if not _wait_for_rate_limit(self.max_rate_limit_wait):
                raise RobloxAPIError("Roblox is rate-limiting requests. Please wait briefly and try again.")
            try:
                response = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise RobloxAPIError(f"Network request failed: {exc}") from exc
                time.sleep(min(2 ** attempt, 8))
                continue

            if response.status_code == 429 or 500 <= response.status_code < 600:
                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After")
                    try:
                        cooldown = min(float(retry_after), 120) if retry_after else 30.0
                    except ValueError:
                        cooldown = 30.0
                    _set_rate_limit(cooldown)
                if attempt >= self.max_retries:
                    raise RobloxAPIError(
                        f"Roblox API remained unavailable (HTTP {response.status_code})."
                    )
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 120) if retry_after else min(2 ** attempt, 8)
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
        """Search collectible catalog items; returns raw item dictionaries."""
        if limit not in (10, 28, 30, 60, 120):
            raise ValueError("limit must be 10, 28, 30, 60, or 120")
        if max_pages < 1:
            raise ValueError("max_pages must be at least 1")

        items: list[dict[str, Any]] = []
        cursor: str | None = None

        for _ in range(max_pages):
            params: dict[str, Any] = {
                "CategoryFilter": 0,
                "SalesTypeFilter": 2,
                "SortType": sort_type,
                "limit": limit,
            }
            if keyword.strip():
                params["Keyword"] = keyword.strip()
            if cursor:
                params["cursor"] = cursor

            payload = self._get_json(CATALOG_SEARCH_URL, params)
            page_items = payload.get("data", [])
            if not isinstance(page_items, list):
                raise RobloxAPIError("Catalog response did not contain an item list in 'data'.")
            items.extend(item for item in page_items if isinstance(item, dict))
            cursor = payload.get("nextPageCursor")
            if not cursor:
                break
        return items

    def search_collectibles_page(
        self, cursor: str | None = None, limit: int = 120, keyword: str = "", sort_type: int = 0
    ) -> dict[str, Any]:
        """Fetch one catalog page and return its pagination cursor."""
        if limit not in (10, 28, 30, 60, 120):
            raise ValueError("limit must be 10, 28, 30, 60, or 120")
        params: dict[str, Any] = {
            "CategoryFilter": 0, "SalesTypeFilter": 2, "SortType": sort_type, "limit": limit
        }
        if keyword.strip():
            params["Keyword"] = keyword.strip()
        if cursor:
            params["cursor"] = cursor
        payload = self._get_json(CATALOG_SEARCH_URL, params)
        page_items = payload.get("data", [])
        if not isinstance(page_items, list):
            raise RobloxAPIError("Catalog response did not contain an item list in 'data'.")
        return {
            "data": [item for item in page_items if isinstance(item, dict)],
            "nextPageCursor": payload.get("nextPageCursor"),
        }

    def get_resale_data(self, asset_id: int, collectible_item_id: str | None = None) -> dict[str, Any]:
        """Fetch RAP through Roblox's current collectible endpoint when possible.

        Catalog search responses do not always include collectibleItemId.
        Resolve it through economy v2 before falling back to the legacy
        asset resale endpoint (which can return null RAP for migrated items).
        """
        # Use the modern endpoint when the catalog supplied its collectible ID.
        # Do not make a second economy-details request for every missing ID:
        # that multiplies traffic and can trigger Roblox's rate limit.
        if collectible_item_id:
            data = self._get_json(f"{RESELLERS_URL}/{collectible_item_id}/resale-data", {})
            rap = data.get("recentAveragePrice")
            if isinstance(rap, (int, float)) and rap > 0:
                return data

        # Legacy limiteds and items without a collectible ID use asset ID.
        return self._get_json(f"{ECONOMY_URL}/{int(asset_id)}/resale-data", {})

    def get_asset_details(self, asset_id: int) -> dict[str, Any]:
        """Fetch public economy details, including Sales, Remaining and collectible quantity."""
        return self._get_json(f"https://economy.roblox.com/v2/assets/{int(asset_id)}/details", {})

    def get_resellers(self, collectible_item_id: str, limit: int = 10) -> dict[str, Any]:
        """Fetch currently listed copies for a collectible item."""
        if not collectible_item_id:
            return {"data": []}
        return self._get_json(
            f"{RESELLERS_URL}/{collectible_item_id}/resellers",
            {"limit": limit},
        )

    def get_asset_thumbnails(self, asset_ids: list[int]) -> dict[int, str]:
        """Return Roblox thumbnail URLs keyed by asset ID."""
        ids = [int(asset_id) for asset_id in asset_ids if asset_id]
        if not ids:
            return {}
        result: dict[int, str] = {}
        # Roblox's thumbnail endpoint accepts batches; keep requests bounded.
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            payload = self._get_json(
                THUMBNAILS_URL,
                {
                    "assetIds": ",".join(str(asset_id) for asset_id in batch),
                    "returnPolicy": "PlaceHolder",
                    "size": "420x420",
                    "format": "Png",
                    "isCircular": "false",
                },
            )
            for entry in payload.get("data", []):
                if not isinstance(entry, dict):
                    continue
                try:
                    asset_id = int(entry.get("targetId"))
                except (TypeError, ValueError):
                    continue
                image_url = entry.get("imageUrl")
                if image_url:
                    result[asset_id] = image_url
        return result


def normalize_catalog_item(item: dict[str, Any]) -> dict[str, Any]:
    """Return display-friendly fields while preserving the original response."""
    creator = item.get("creator") or {}
    if not isinstance(creator, dict):
        creator = {}
    lowest_resale = item.get("lowestResalePrice")
    lowest_limited = item.get("lowestPrice")
    catalog_price = item.get("price")
    has_resellers = item.get("hasResellers") is True
    is_for_sale = item.get("isForSale") is True or has_resellers or (
        isinstance(catalog_price, (int, float)) and catalog_price > 0
    )
    if isinstance(lowest_resale, (int, float)) and lowest_resale > 0:
        display_price, price_type = lowest_resale, "Lowest resale price"
    elif isinstance(lowest_limited, (int, float)) and lowest_limited > 0:
        display_price, price_type = lowest_limited, "Lowest resale price"
    elif isinstance(catalog_price, (int, float)) and catalog_price > 0:
        display_price, price_type = catalog_price, "Catalog price (not confirmed resale)"
    else:
        display_price, price_type = None, "No current price"
    creator_available = item.get("isForSale") is True and isinstance(catalog_price, (int, float)) and catalog_price > 0
    resale_candidates = [
        value for value in (lowest_resale, lowest_limited)
        if isinstance(value, (int, float)) and value > 0
    ]
    if creator_available:
        resale_candidates.append(catalog_price)
    lowest_available = min(resale_candidates) if resale_candidates else None
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "description": item.get("description"),
        "creator_name": creator.get("name") or item.get("creatorName"),
        "creator_id": creator.get("id") or item.get("creatorTargetId"),
        "price": catalog_price,
        "creator_price": catalog_price,
        "resale_price": lowest_resale or lowest_limited,
        "lowest_price": lowest_available,
        "display_price": display_price,
        "price_type": price_type,
        "is_limited": item.get("isLimited"),
        "is_limited_unique": item.get("isLimitedUnique"),
        "collectible_item_id": item.get("collectibleItemId"),
        "thumbnail": item.get("thumbnail"),
        "has_resellers": has_resellers,
        "is_for_sale": is_for_sale,
        "raw": item,
    }
