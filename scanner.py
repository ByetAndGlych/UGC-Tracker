"""Always-on background catalog scanner with durable database progress."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import threading
import time

from database import connect_database
from roblox_api import RobloxAPIError, RobloxCatalogClient, normalize_catalog_item

DB_PATH = Path(os.environ.get("UGC_TRACKER_DB", "/tmp/ugc_tracker.sqlite3"))
PAGE_SIZE = 120
PAGE_PAUSE_SECONDS = 4


class CatalogScanner:
    def __init__(self, client: RobloxCatalogClient | None = None, db_path: Path = DB_PATH):
        self.client = client or RobloxCatalogClient()
        self.db_path = Path(db_path)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._init_db()

    def _connect(self):
        return connect_database()

    def _init_db(self):
        with self._connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS scanner_state (key TEXT PRIMARY KEY, value TEXT)")
            db.execute("CREATE TABLE IF NOT EXISTS catalog_items (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL)")
            db.execute("INSERT INTO scanner_state(key,value) VALUES ('cursor','') ON CONFLICT(key) DO NOTHING")
            db.execute("INSERT INTO scanner_state(key,value) VALUES ('status','Starting') ON CONFLICT(key) DO NOTHING")
            db.execute("INSERT INTO scanner_state(key,value) VALUES ('last_scan','') ON CONFLICT(key) DO NOTHING")

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

    def _save_item(self, item: dict) -> None:
        """Commit one item's latest enrichment immediately."""
        with self._connect() as db:
            db.execute(
                "UPDATE catalog_items SET payload=?, updated_at=? WHERE id=?",
                (
                    json.dumps(item),
                    datetime.now().astimezone().isoformat(),
                    str(item["id"]),
                ),
            )

    def _enrich_items(self, items: list[dict]) -> None:
        """Enrich items and commit each RAP result as soon as it arrives."""
        if not items:
            return

        def fetch_rap(item):
            try:
                result = self.client.get_resale_data(
                    int(item["id"]), item.get("collectible_item_id")
                )
                rap = result.get("recentAveragePrice")
                return str(item["id"]), rap if isinstance(rap, (int, float)) and rap > 0 else None
            except (RobloxAPIError, TypeError, ValueError):
                return str(item["id"]), None

        now = datetime.now().astimezone()
        rap_candidates = [
            item for item in items
            if not isinstance(item.get("rap"), (int, float))
            and (
                not item.get("rap_retry_after")
                or item["rap_retry_after"] <= now.isoformat()
            )
        ]

        # Save every completed RAP lookup separately. An interruption only loses
        # the requests that were still running, not the whole page's enrichment.
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(fetch_rap, item): item for item in rap_candidates}
            for future in as_completed(futures):
                item = futures[future]
                _, rap = future.result()
                if rap is not None:
                    item["rap"] = rap
                    item["rap_retry_after"] = None
                else:
                    attempts = int(item.get("rap_attempts") or 0) + 1
                    item["rap_attempts"] = attempts
                    delay = min(3600, 30 * (2 ** min(attempts - 1, 7)))
                    item["rap_retry_after"] = (
                        datetime.now().astimezone() + timedelta(seconds=delay)
                    ).isoformat()
                self._save_item(item)

        # Thumbnail failures must not undo RAP progress. Retry missing thumbnails
        # on a later pass, with a backoff so a Roblox outage is not hammered.
        thumb_candidates = [
            item for item in items
            if not item.get("thumbnail_url")
            and (
                not item.get("thumbnail_retry_after")
                or item["thumbnail_retry_after"] <= datetime.now().astimezone().isoformat()
            )
        ]
        if thumb_candidates:
            try:
                thumbnails = self.client.get_asset_thumbnails(
                    [int(item["id"]) for item in thumb_candidates]
                )
            except RobloxAPIError:
                thumbnails = {}
            for item in thumb_candidates:
                thumbnail = thumbnails.get(int(item["id"]))
                if thumbnail:
                    item["thumbnail_url"] = thumbnail
                    item["thumbnail_retry_after"] = None
                else:
                    attempts = int(item.get("thumbnail_attempts") or 0) + 1
                    item["thumbnail_attempts"] = attempts
                    delay = min(3600, 30 * (2 ** min(attempts - 1, 7)))
                    item["thumbnail_retry_after"] = (
                        datetime.now().astimezone() + timedelta(seconds=delay)
                    ).isoformat()
                self._save_item(item)

    def _enrich_pending(self, limit: int = PAGE_SIZE) -> None:
        """Resume unfinished RAP/thumbnail lookups left by an interrupted run."""
        with self._connect() as db:
            rows = db.execute(
                "SELECT payload FROM catalog_items ORDER BY updated_at ASC"
            ).fetchall()
        now = datetime.now().astimezone().isoformat()
        pending = []
        for (payload,) in rows:
            item = json.loads(payload)
            rap_due = (
                not isinstance(item.get("rap"), (int, float))
                and (not item.get("rap_retry_after") or item["rap_retry_after"] <= now)
            )
            thumbnail_due = (
                not item.get("thumbnail_url")
                and (not item.get("thumbnail_retry_after") or item["thumbnail_retry_after"] <= now)
            )
            if rap_due or thumbnail_due:
                pending.append(item)
            if len(pending) >= limit:
                break
        self._enrich_items(pending)

    def _save_page(self, raw_items: list[dict], next_cursor: str | None):
        items = [normalize_catalog_item(raw) for raw in raw_items]
        items = [item for item in items if item.get("is_for_sale") and item.get("id")]
        now = datetime.now().astimezone().isoformat()

        # Save the complete page and its next cursor in one transaction. If the
        # process stops before this commit, the same page is fetched again.
        with self._connect() as db:
            for item in items:
                old_row = db.execute(
                    "SELECT payload FROM catalog_items WHERE id=?", (str(item["id"]),)
                ).fetchone()
                old = json.loads(old_row[0]) if old_row else {}
                item["rap"] = old.get("rap")
                item["thumbnail_url"] = old.get("thumbnail_url")
                for key in (
                    "rap_attempts", "rap_retry_after",
                    "thumbnail_attempts", "thumbnail_retry_after",
                ):
                    if key in old:
                        item[key] = old[key]
                item.pop("raw", None)
                db.execute(
                    "INSERT INTO catalog_items(id,payload,updated_at) VALUES (?,?,?) "
                    "ON CONFLICT(id) DO UPDATE SET payload=excluded.payload, updated_at=excluded.updated_at",
                    (str(item["id"]), json.dumps(item), now),
                )
            db.execute("UPDATE scanner_state SET value=? WHERE key='cursor'", (next_cursor or "",))
            db.execute("UPDATE scanner_state SET value=? WHERE key='last_scan'", (now,))

        self._enrich_items(items)

    def run_forever(self):
        while not self.stop_event.is_set():
            self._state("status", "Scanning Roblox catalog — continuous mode")
            try:
                # Finish work left behind by a previous interrupted scan before
                # moving farther through the catalog.
                self._enrich_pending()
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
        # Only show items with a known RAP, a known price, and a non-zero discount.
        # Hide above-RAP, equal-to-RAP (0.0%), and missing-RAP items.
        items = [
            item for item in items
            if isinstance(item.get("lowest_price"), (int, float))
            and isinstance(item.get("rap"), (int, float))
            and item["rap"] > 0
            and 0 < item["lowest_price"] < item["rap"]
            and round((1 - item["lowest_price"] / item["rap"]) * 100, 1) > 0
        ]
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
            "scan_mode": "continuous — no scheduled sleep window",
        }
