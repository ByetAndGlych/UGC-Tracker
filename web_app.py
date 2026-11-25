"""Phone-friendly read-only UGC catalog gallery using Python's standard library."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from roblox_api import RobloxAPIError, RobloxCatalogClient, normalize_catalog_item
from scanner import CatalogScanner


ROOT = Path(__file__).parent
SCANNER = CatalogScanner()


class GalleryHandler(BaseHTTPRequestHandler):
    client = RobloxCatalogClient()
    item_cache: dict[int, dict] = {}

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/scanner":
            self._send(200, json.dumps(SCANNER.status()).encode(), "application/json; charset=utf-8")
            return
        if parsed.path == "/health":
            self._send(200, b"ok", "text/plain; charset=utf-8")
            return
        if parsed.path == "/":
            self._send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
            return
        if parsed.path.startswith("/api/items/"):
            try:
                asset_id = int(parsed.path.rsplit("/", 1)[-1])
                item = self.item_cache.get(asset_id)
                if not item:
                    supplied = parse_qs(parsed.query).get("item", [""])[0]
                    try:
                        candidate = json.loads(supplied) if supplied else None
                    except json.JSONDecodeError:
                        candidate = None
                    if isinstance(candidate, dict) and str(candidate.get("id")) == str(asset_id):
                        item = candidate
                    else:
                        self._send(400, b'{"error":"Item details were not included. Run a new search and try again."}', "application/json")
                        return
                detail = {
                    "item": {key: value for key, value in item.items() if key != "raw"},
                    "rap": item.get("rap"), "sales": None, "stock": None, "remaining": None,
                    "price_history": [], "volume_history": [], "resellers": [],
                }
                # Roblox's collectible resale-data endpoint supplies RAP/history,
                # but lifetime sales and stock are exposed by economy v2 asset details.
                try:
                    resale = self.client.get_resale_data(
                        asset_id, item.get("collectible_item_id")
                    )
                    detail["rap"] = resale.get("recentAveragePrice") or detail["rap"]
                    detail["price_history"] = resale.get("priceDataPoints") or []
                    detail["volume_history"] = resale.get("volumeDataPoints") or []
                    detail["sales"] = resale.get("sales")
                    detail["stock"] = resale.get("assetStock")
                    detail["remaining"] = resale.get("numberRemaining")
                except RobloxAPIError:
                    pass
                try:
                    economy = self.client.get_asset_details(asset_id)
                    collectible = economy.get("CollectiblesItemDetails") or {}
                    if economy.get("Sales") is not None:
                        detail["sales"] = economy.get("Sales")
                    if detail["remaining"] is None:
                        detail["remaining"] = economy.get("Remaining")
                    if detail["stock"] is None:
                        detail["stock"] = collectible.get("TotalQuantity")
                        if detail["stock"] is None:
                            detail["stock"] = economy.get("TotalQuantity")
                except RobloxAPIError:
                    pass
                collectible_id = item.get("collectible_item_id")
                if collectible_id:
                    try:
                        listing_data = self.client.get_resellers(str(collectible_id), limit=10)
                        detail["resellers"] = listing_data.get("data") or []
                    except RobloxAPIError:
                        pass
                self._send(200, json.dumps(detail).encode(), "application/json; charset=utf-8")
            except ValueError:
                self._send(400, b'{"error":"Invalid item ID."}', "application/json")
            return

        if parsed.path != "/api/items":
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return

        query = parse_qs(parsed.query)
        keyword = query.get("keyword", [""])[0][:80]
        try:
            limit = int(query.get("limit", ["30"])[0])
            if limit not in (10, 28, 30, 60, 120):
                raise ValueError
            if not keyword.strip():
                items = SCANNER.get_items(limit=limit)
                payload = json.dumps({"items": items, "count": len(items), "source": "continuous-scanner"}).encode()
                self._send(200, payload, "application/json; charset=utf-8")
                return
            raw_items = self.client.search_collectibles(keyword=keyword, limit=limit)
            items = [normalize_catalog_item(item) for item in raw_items]
            items = [item for item in items if item["is_for_sale"]]
            def fetch_rap(item: dict) -> tuple[int, int | None]:
                try:
                    asset_id = int(item["id"])
                    resale = self.client.get_resale_data(
                        asset_id, item.get("collectible_item_id")
                    )
                    rap = resale.get("recentAveragePrice")
                    return asset_id, rap if isinstance(rap, (int, float)) and rap > 0 else None
                except (RobloxAPIError, TypeError, ValueError):
                    return int(item.get("id") or 0), None

            with ThreadPoolExecutor(max_workers=6) as pool:
                rap_results = list(pool.map(fetch_rap, items))
            rap_by_id = dict(rap_results)
            for item in items:
                try:
                    item["rap"] = rap_by_id.get(int(item["id"]))
                    self.item_cache[int(item["id"])] = item
                except (TypeError, ValueError):
                    pass
            thumbnails = self.client.get_asset_thumbnails(
                [item["id"] for item in items if item.get("id")]
            )
            for item in items:
                item["thumbnail_url"] = thumbnails.get(item.get("id"))
                item.pop("raw", None)
            payload = json.dumps({"items": items, "count": len(items)}).encode()
            self._send(200, payload, "application/json; charset=utf-8")
        except ValueError:
            self._send(400, b'{"error":"Choose a supported result limit."}', "application/json")
        except RobloxAPIError as exc:
            payload = json.dumps({"error": str(exc)}).encode()
            self._send(502, payload, "application/json; charset=utf-8")

    def log_message(self, format: str, *args: object) -> None:
        print(f"[UGC-Tracker] {args[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the UGC-Tracker item gallery.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--check", action="store_true", help="Test live catalog and thumbnails")
    args = parser.parse_args()

    if args.check:
        import time

        client = RobloxCatalogClient()
        started = time.perf_counter()
        cursor = None
        seen_cursors = set()
        seen_ids = set()
        page_times = []
        pages = 0
        duplicates = 0
        for page_number in range(1, 101):
            page_started = time.perf_counter()
            page = client.search_collectibles_page(cursor=cursor, limit=120)
            elapsed = time.perf_counter() - page_started
            page_times.append(elapsed)
            pages += 1
            page_items = page["data"]
            for item in page_items:
                item_id = item.get("id")
                if item_id is not None:
                    if str(item_id) in seen_ids:
                        duplicates += 1
                    seen_ids.add(str(item_id))
            cursor = page.get("nextPageCursor")
            print(
                f"PAGE {page_number}: items={len(page_items)} "
                f"seconds={elapsed:.2f} unique_total={len(seen_ids)} "
                f"has_next={bool(cursor)}",
                flush=True,
            )
            if not cursor:
                print("PAGINATION_END: Roblox returned no nextPageCursor.", flush=True)
                break
            if cursor in seen_cursors:
                print("PAGINATION_LOOP: Roblox repeated a cursor.", flush=True)
                break
            seen_cursors.add(cursor)
        total = time.perf_counter() - started
        average = total / pages if pages else 0
        print(
            f"BENCHMARK_RESULT pages={pages} unique_items={len(seen_ids)} "
            f"duplicates={duplicates} elapsed_seconds={total:.2f} "
            f"average_seconds_per_page={average:.2f} "
            f"items_per_second={len(seen_ids) / total if total else 0:.2f} "
            f"projected_100_pages_seconds={average * 100:.2f}",
            flush=True,
        )
        return

    SCANNER.start()
    server = ThreadingHTTPServer((args.host, args.port), GalleryHandler)
    print(f"UGC-Tracker gallery running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        SCANNER.stop()


if __name__ == "__main__":
    main()
