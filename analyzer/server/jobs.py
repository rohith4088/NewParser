"""Ordered Parse Queue and Background Worker for User-Driven Ingestion.

Executes user-selected units one-by-one in strict priority order, updating
DuckDB storage incrementally so the dashboard reflects live findings as each
service or log finishes parsing.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from ..ingest.inventory import Inventory, Unit
from ..parse.pipeline import parse_bundle_to_store
from ..store.duck import SessionStore

logger = logging.getLogger(__name__)


class JobQueue:
    """Manages an ordered queue of units to parse for a single support bundle session."""

    def __init__(
        self,
        session_id: str,
        bundle_path: Path,
        db_path: Path,
        inventory: Inventory,
        on_unit_completed: Callable[[str, int, int], None] | None = None,
    ):
        self.session_id = session_id
        self.bundle_path = bundle_path
        self.db_path = db_path
        self.inventory = inventory
        self.on_unit_completed = on_unit_completed

        self._lock = threading.Lock()
        self._items: list[dict[str, Any]] = []
        self._current_unit_id: str | None = None
        self._current_unit_files_done: int = 0
        self._current_unit_total_files: int = 0
        self._is_running: bool = False
        self._cancel_requested: bool = False
        self._worker_thread: threading.Thread | None = None

    def enqueue(self, unit_ids: list[str]) -> list[dict[str, Any]]:
        """Add units to the queue in the user-specified order."""
        with self._lock:
            existing_pending = {item["unit_id"] for item in self._items if item["status"] in ("pending", "parsing")}
            added = []
            for uid in unit_ids:
                if uid in existing_pending:
                    continue
                unit = self.inventory.find_unit(uid)
                name = unit.name if unit else uid
                part_id = unit.part_id if unit else "unknown"
                group = unit.group if unit else "other"
                item = {
                    "unit_id": uid,
                    "name": name,
                    "part_id": part_id,
                    "group": group,
                    "status": "pending",
                    "files": 0,
                    "events": 0,
                    "error": None,
                    "started_at": None,
                    "finished_at": None,
                }
                self._items.append(item)
                added.append(item)

            if not self._is_running and any(i["status"] == "pending" for i in self._items):
                self._is_running = True
                self._cancel_requested = False
                self._worker_thread = threading.Thread(target=self._run_worker, daemon=True)
                self._worker_thread.start()

            return list(self._items)

    def reorder(self, unit_ids: list[str]) -> list[dict[str, Any]]:
        """Re-arrange pending units in the queue according to new ordering."""
        with self._lock:
            # Keep finished or active items in place
            active_or_done = [item for item in self._items if item["status"] != "pending"]
            pending_map = {item["unit_id"]: item for item in self._items if item["status"] == "pending"}

            reordered_pending = []
            for uid in unit_ids:
                if uid in pending_map:
                    reordered_pending.append(pending_map.pop(uid))
            # Any remaining pending items keep their original relative order
            for item in self._items:
                if item["status"] == "pending" and item["unit_id"] in pending_map:
                    reordered_pending.append(item)

            self._items = active_or_done + reordered_pending
            return list(self._items)

    def cancel(self) -> None:
        """Request cancellation of currently pending units."""
        with self._lock:
            self._cancel_requested = True
            for item in self._items:
                if item["status"] == "pending":
                    item["status"] = "cancelled"

    def status(self) -> dict[str, Any]:
        """Return the current queue status and list of units."""
        with self._lock:
            total_items = len(self._items)
            completed_items = sum(1 for i in self._items if i["status"] == "completed")
            failed_items = sum(1 for i in self._items if i["status"] == "failed")
            pending_items = sum(1 for i in self._items if i["status"] == "pending")

            unit_done = getattr(self, "_current_unit_files_done", 0)
            unit_total = getattr(self, "_current_unit_total_files", 1) or 1
            unit_pct = min(1.0, max(0.0, unit_done / unit_total)) if self._is_running else 0.0

            if total_items > 0:
                if self._is_running:
                    overall_pct = min(99, max(1, round(((completed_items + unit_pct) / total_items) * 100)))
                else:
                    overall_pct = 100 if completed_items > 0 and (completed_items + failed_items == total_items) else 0
            else:
                overall_pct = 0

            unit_name = None
            if self._current_unit_id:
                u = self.inventory.find_unit(self._current_unit_id)
                unit_name = u.name if u else self._current_unit_id

            return {
                "session_id": self.session_id,
                "is_running": self._is_running,
                "current_unit": self._current_unit_id,
                "current_unit_name": unit_name,
                "current_unit_files_done": unit_done,
                "current_unit_total_files": unit_total,
                "overall_pct": overall_pct,
                "total_queued": total_items,
                "completed_count": completed_items,
                "failed_count": failed_items,
                "pending_count": pending_items,
                "items": [dict(i) for i in self._items],
            }

    def _run_worker(self) -> None:
        """Worker thread executing units sequentially in priority order."""
        try:
            while True:
                target_item = None
                with self._lock:
                    if self._cancel_requested:
                        break
                    for item in self._items:
                        if item["status"] == "pending":
                            target_item = item
                            target_item["status"] = "parsing"
                            target_item["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
                            self._current_unit_id = item["unit_id"]
                            break

                if target_item is None:
                    break

                uid = target_item["unit_id"]
                unit_obj = self.inventory.find_unit(uid)
                with self._lock:
                    self._current_unit_total_files = max(1, unit_obj.file_count if unit_obj else 1)
                    self._current_unit_files_done = 0

                def _on_unit_progress(count: int, path: str) -> None:
                    with self._lock:
                        self._current_unit_files_done = count
                        if target_item:
                            target_item["files"] = count

                try:
                    # Open read-write DuckDB connection for this unit's pass
                    with SessionStore(self.db_path, read_only=False) as store:
                        manifest = parse_bundle_to_store(
                            self.bundle_path,
                            store,
                            session_id=self.session_id,
                            units=[uid],
                            inventory=self.inventory,
                            on_progress=_on_unit_progress,
                        )
                        # Query parsed unit counts
                        rows = store.conn.execute(
                            "SELECT files, events FROM parsed_units WHERE unit_id = ?", [uid]
                        ).fetchone()
                        files_done = rows[0] if rows else manifest.total_files
                        events_done = rows[1] if rows else 0

                    with self._lock:
                        target_item["status"] = "completed"
                        target_item["files"] = files_done
                        target_item["events"] = events_done
                        target_item["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
                        self._current_unit_files_done = files_done

                    if self.on_unit_completed:
                        self.on_unit_completed(uid, files_done, events_done)

                except Exception as exc:
                    logger.exception("Failed to parse unit %s: %s", uid, exc)
                    with self._lock:
                        target_item["status"] = "failed"
                        target_item["error"] = str(exc)
                        target_item["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

        finally:
            with self._lock:
                self._is_running = False
                self._current_unit_id = None


class JobManager:
    """Registry of session job queues."""

    def __init__(self):
        self._queues: dict[str, JobQueue] = {}
        self._lock = threading.Lock()

    def get_queue(
        self,
        session_id: str,
        bundle_path: Path,
        db_path: Path,
        inventory: Inventory,
        on_unit_completed: Callable[[str, int, int], None] | None = None,
    ) -> JobQueue:
        with self._lock:
            if session_id not in self._queues:
                self._queues[session_id] = JobQueue(
                    session_id=session_id,
                    bundle_path=bundle_path,
                    db_path=db_path,
                    inventory=inventory,
                    on_unit_completed=on_unit_completed,
                )
            return self._queues[session_id]

    def find_queue(self, session_id: str) -> JobQueue | None:
        with self._lock:
            return self._queues.get(session_id)
