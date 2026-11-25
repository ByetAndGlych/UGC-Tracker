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

# Cooldowns are isolated by Roblox API host so a resale 429 does not block
# catalog and thumbnail traffic.
_RATE_LIMIT_LOCK = threading.Lock()
_RATE_LIMIT_UNTIL: dict[str, float] = {}
_RATE_LIMIT_STREAK: dict[str, int] = {}


def _host(url: str) -> str:
    from urllib.parse import urlsplit
    return urlsplit(url).netloc.lower()


def _cooldown_left(host: str) -> float:
    with _RATE_LIMIT_LOCK:
        return max(0.0, _RATE_LIMIT_UNTIL.get(host, 0.0) - time.monotonic())


def _set_rate_limit(host: str, retry_after: str | None = None) -> float:
    with _RATE_LIMIT_LOCK:
        streak = _RATE_LIMIT_STREAK.get(host, 0) + 1
        _RATE_LIMIT_STREAK[host] = streak
        try:
            requested = float(retry_after) if retry_after else 0.0
        except (TypeError, ValueError):
            requested = 0.0
        # Respect Roblox's Retry-After while applying bounded exponential
        # backoff when the header is missing or asks for a shorter delay.
        delay = min(120.0, max(requested, min(30.0 * (2 ** min(streak - 1, 2)), 120.0)))
        _RATE_LIMIT_UNTIL[host] = max(_RATE_LIMIT_UNTIL.get(host, 0.0), time.monotonic() + delay)
        return delay


def _clear_rate_limit(host: str) -> None:
    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_STREAK[host] = 0


class RobloxAPIError(RuntimeError):
    """Raised when Roblox returns an unusable API response."""

    def __init__(self, message: str, status_code: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class RobloxBusy(RobloxAPIError):
    """A request was declined because waiting would exceed its budget."""


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

    def _get_json(
        self,
        url: str,
        params: dict[str, Any],
        deadline: float | None = None,
    ) -> dict[str, Any]:
        host = _host(url)
        for attempt in range(self.max_retries + 1):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise RobloxBusy("Roblox request timed out waiting for its request budget.", retry_after=_cooldown_left(host))

            cooldown = _cooldown_left(host)
            if cooldown > 0:
                if self.max_rate_limit_wait == 0 or (remaining is not None and cooldown >= remaining):
                    raise RobloxBusy(
                        "Roblox is rate-limiting this endpoint. Try again shortly.",
                        status_code=429,
                        retry_after=cooldown,
                    )
                wait_budget = cooldown if remaining is None else min(cooldown, remaining)
                if self.max_rate_limit_wait is not None:
                    wait_budget = min(wait_budget, self.max_rate_limit_wait)
                if wait_budget < cooldown:
                    raise RobloxBusy("Roblox endpoint is temporarily busy.", status_code=429, retry_after=cooldown)
                time.sleep(wait_budget)
                continue

            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise RobloxBusy("Roblox request deadline exceeded.")
            timeout = self.timeout if remaining is None else max(0.1, min(float(self.timeout), remaining))
            try:
                response = self.session.get(url, params=params, timeout=timeout)
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise RobloxAPIError(f"Network request failed: {exc}") from exc
                delay = min(2 ** attempt, 8)
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= delay:
                        raise RobloxBusy("Roblox request deadline exceeded.") from exc
                    delay = min(delay, remaining)
                time.sleep(delay)
                continue

            if response.status_code == 429:
                delay = _set_rate_limit(host, response.headers.get("Retry-After"))
                # Web requests are fail-fast. Background callers may retry on
                # the next loop, but never sleep and retry inside a web request.
                if self.max_rate_limit_wait == 0 or deadline is not None:
                    raise RobloxBusy(
                        "Roblox is rate-limiting this endpoint. Try again shortly.",
                        status_code=429,
                        retry_after=delay,
                    )
                if attempt >= self.max_retries:
                    raise RobloxAPIError("Roblox API remained rate-limited.", status_code=429, retry_after=delay)
                continue

            if 500 <= response.status_code < 600:
                if attempt >= self.max_retries:
                    raise RobloxAPIError(
                        f"Roblox API remained unavailable (HTTP {response.status_code}).",
                        status_code=response.status_code,
                    )
                delay = min(2 ** attempt, 8)
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= delay:
                        raise RobloxBusy("Roblox request deadline exceeded.", status_code=response.status_code)
                    delay = min(delay, remaining)
                time.sleep(delay)
                continue

            if not response.ok:
                raise RobloxAPIError(
                    f"Roblox API returned HTTP {response.status_code}: {response.text[:300]}",
                    status_code=response.status_code,
                )

            _clear_rate_limit(host)
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
        """Fetch RAP from exactly one endpoint selected by item identifier."""
        if collectible_item_id:
            return self._get_json(f"{RESELLERS_URL}/{collectible_item_id}/resale-data", {})
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
        "is_limited": item.get("isLimited") is True or "Limited" in (item.get("itemRestrictions") or []),
        "is_limited_unique": item.get("isLimitedUnique") is True or "LimitedUnique" in (item.get("itemRestrictions") or []),
        "total_quantity": item.get("totalQuantity"),
        "units_available": item.get("unitsAvailableForConsumption"),
        "collectible_item_id": item.get("collectibleItemId"),
        "thumbnail": item.get("thumbnail"),
        "has_resellers": has_resellers,
        "is_for_sale": is_for_sale,
        "raw": item,
    }
