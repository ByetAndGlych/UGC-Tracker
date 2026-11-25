"""Background catalog scanner with Eastern-time sleep hours and SQLite progress."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as day_time
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

from roblox_api import RobloxAPIError, RobloxCatalogClient, normalize_catalog_item

EASTERN = ZoneInfo("America/New_York")
DB_PATH = Path(os.environ.get("UGC_TRACKER_DB", "/tmp/ugc_tracker.sqlite3"))
PAGE_SIZE = 120
PAGE_PAUSE_SECONDS = 2
SLEEP_START = day_time(3, 0)
SLEEP_END = day_time(9, 0)


class CatalogScanner:
    def __init__(self, client: RobloxCatalogClient | None = None, db_path: Path = DB_PATH):
        self.client = client or RobloxCatalogClient()
        self.db_path = Path(db_path)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._init_db()

    def _connect(self):
        connection = sqlite3.connect(self.db_path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _init_db(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS scanner_state (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS catalog_items (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execute("INSERT OR IGNORE INTO scanner_state(key,value) VALUES ('cursor','')")
            db.execute("INSERT OR IGNORE INTO scanner_state(key,value) VALUES ('status','Starting')")
            db.execute("INSERT OR IGNORE INTO scanner_state(key,value) VALUES ('last_scan','')")

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.thread = threading.Thread(target=self.run_forever, name="ugc-catalog-scanner", daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def _state(self, key: str, value: str | None = None) -> str:
        with self._connect() as db:
            if value is not None:
                db.execute("INSERT INTO scanner_state(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
            row = db.execute("SELECT value FROM scanner_state WHERE key=?", (key,)).fetchone()
            return row[0] if row else ""

    def _is_sleep_time(self, now: datetime) -> bool:
        current = now.astimezone(EASTERN).time().replace(tzinfo=None)
        return current >= SLEEP_START or current < SLEEP_END

    def _save_page(self, raw_items: list[dict], next_cursor: str | None):
        items = [normalize_catalog_item(raw) for raw in raw_items]
        items = [item for item in items if item.get("is_for_sale") and item.get("id")]

        def fetch_rap(item):
            try:
                result = self.client.get_resale_data(int(item["id"]), item.get("collectible_item_id"))
                rap = result.get("recentAveragePrice")
                return str(item["id"]), rap if isinstance(rap, (int, float)) and rap > 0 else None
            except (RobloxAPIError, TypeError, ValueError):
                return str(item["id"]), None

        with ThreadPoolExecutor(max_workers=8) as pool:
            rap_values = dict(pool.map(fetch_rap, items)) if items else {}

        thumbnails = self.client.get_asset_thumbnails([int(item["id"]) for item in items])
        now = datetime.now(EASTERN).isoformat()
        with self._connect() as db:
            for item in items:
                item["rap"] = rap_values.get(str(item["id"]))
                item["thumbnail_url"] = thumbnails.get(int(item["id"]))
                item.pop("raw", None)
                db.execute(
                    "INSERT INTO catalog_items(id,payload,updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                    (str(item["id"]), json.dumps(item), now),
                )
            db.execute("UPDATE scanner_state SET value=? WHERE key='cursor'", (next_cursor or "",))
            db.execute("UPDATE scanner_state SET value=? WHERE key='last_scan'", (now,))

    def run_forever(self):
        while not self.stop_event.is_set():
            now = datetime.now(EASTERN)
            if self._is_sleep_time(now):
                self._state("status", "Sleeping — resumes at 9:00 AM Eastern")
                self.stop_event.wait(60)
                continue
            self._state("status", "Scanning Roblox catalog")
            try:
                cursor = self._state("cursor") or None
                page = self.client.search_collectibles_page(cursor=cursor, limit=PAGE_SIZE)
                self._save_page(page["data"], page.get("nextPageCursor"))
                if not page.get("nextPageCursor"):
                    self._state("status", "Catalog pass complete — restarting scan")
                    self._state("cursor", "")
                self.stop_event.wait(PAGE_PAUSE_SECONDS)
            except RobloxAPIError as exc:
                self._state("status", f"Roblox temporarily unavailable: {str(exc)[:160]}")
                self.stop_event.wait(30)
            except Exception as exc:
                self._state("status", f"Scanner error: {str(exc)[:160]}")
                self.stop_event.wait(30)

    def get_items(self, keyword: str = "", limit: int = 120) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT payload FROM catalog_items").fetchall()
        items = [json.loads(row[0]) for row in rows]
        if keyword:
            term = keyword.casefold()
            items = [item for item in items if term in str(item.get("name") or "").casefold()]
        items.sort(
            key=lambda item: (
                -((1 - float(item.get("lowest_price") or 0) / float(item.get("rap") or 0)) * 100)
                if item.get("lowest_price") and item.get("rap") else float("inf")
            )
        )
        return items[:limit]

    def status(self) -> dict:
        with self._connect() as db:
            count = db.execute("SELECT COUNT(*) FROM catalog_items").fetchone()[0]
        return {
            "status": self._state("status"),
            "items_scanned": count,
            "last_scan": self._state("last_scan") or None,
            "timezone": "America/New_York",
            "sleep_window": "3:00 AM–9:00 AM Eastern",
        }
