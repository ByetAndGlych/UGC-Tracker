"""Phone-friendly read-only UGC catalog gallery using Python's standard library."""
from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from roblox_api import RobloxAPIError, RobloxCatalogClient, normalize_catalog_item


ROOT = Path(__file__).parent


class GalleryHandler(BaseHTTPRequestHandler):
    client = RobloxCatalogClient()

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, (ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
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
            raw_items = self.client.search_collectibles(keyword=keyword, limit=limit)
            items = [normalize_catalog_item(item) for item in raw_items]
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
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), GalleryHandler)
    print(f"UGC-Tracker gallery running at http://{args.host}:{args.port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
