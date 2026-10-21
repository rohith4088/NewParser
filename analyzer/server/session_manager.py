import json
import threading
from pathlib import Path
from typing import Any

from ..ingest.inventory import Inventory, build_inventory
from ..parse.timestamps import micros_to_iso
from ..store.duck import SessionStore
from ..topology import Topology
from .jobs import JobManager


class SessionManager:
    """Thread-safe manager for multiple DuckDB session databases."""

    def __init__(self, sessions_dir: Path):
        self.sessions_dir = sessions_dir
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._active_session_id: str | None = None
        self._active_store: SessionStore | None = None
        self.job_manager = JobManager()

        # Auto-activate the most recently modified session if one exists
        self._init_default_session()

    def _init_default_session(self) -> None:
        db_files = sorted(
            self.sessions_dir.glob("*.duckdb"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if db_files:
            latest_id = db_files[0].stem
            self.switch_session(latest_id)

    @property
    def active_session_id(self) -> str | None:
        with self._lock:
            return self._active_session_id

    def get_store(self, session_id: str | None = None) -> SessionStore | None:
        """Get the active SessionStore or an explicit session store."""
        with self._lock:
            target_id = session_id or self._active_session_id
            if not target_id:
                db_files = sorted(
                    self.sessions_dir.glob("*.duckdb"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                if db_files:
                    target_id = db_files[0].stem
                    self._active_session_id = target_id
                else:
                    return None

            if target_id == self._active_session_id and self._active_store is not None:
                return self._active_store

            db_path = self.sessions_dir / f"{target_id}.duckdb"
            if not db_path.exists():
                return None
            try:
                store = SessionStore(db_path, read_only=False)
                if target_id == self._active_session_id:
                    self._active_store = store
                return store
            except Exception:
                return None

    def switch_session(self, session_id: str) -> bool:
        """Switch the globally active session."""
        with self._lock:
            db_path = self.sessions_dir / f"{session_id}.duckdb"
            if not db_path.exists():
                return False

            if self._active_store is not None:
                try:
                    self._active_store.close()
                except Exception:
                    pass

            self._active_session_id = session_id
            try:
                self._active_store = SessionStore(db_path, read_only=False)
            except Exception:
                self._active_store = None
                return False
            return True

    def close_store(self, session_id: str | None = None) -> None:
        """Close the active read-only store connection to allow write operations."""
        with self._lock:
            if self._active_store is not None:
                try:
                    self._active_store.close()
                except Exception:
                    pass
                self._active_store = None

    def list_sessions(self) -> list[dict[str, Any]]:
        """List all discovered session databases with metadata."""
        with self._lock:
            all_db_files = sorted(
                self.sessions_dir.glob("*.duckdb"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if self._active_session_id is None and all_db_files:
                self._active_session_id = all_db_files[0].stem

            sessions = []
            for db_path in all_db_files:
                stat = db_path.stat()
                sess_id = db_path.stem
                is_active = (sess_id == self._active_session_id)

                meta = None
                try:
                    # If active, query through cached store
                    if is_active and self._active_store:
                        meta = self._active_store.get_metadata()
                    else:
                        with SessionStore(db_path, read_only=False) as s:
                            meta = s.get_metadata()
                except Exception:
                    pass

                meta = meta or {}
                min_ts = meta.get("min_ts_micros")
                max_ts = meta.get("max_ts_micros")

                sessions.append({
                    "id": sess_id,
                    "is_active": is_active,
                    "filename": meta.get("filename") or db_path.name,
                    "scanned_at": meta.get("scanned_at") or "",
                    "total_events": meta.get("total_events", 0),
                    "total_files": meta.get("total_files", 0),
                    "min_ts_micros": min_ts,
                    "max_ts_micros": max_ts,
                    "min_ts_iso": micros_to_iso(min_ts) if min_ts else None,
                    "max_ts_iso": micros_to_iso(max_ts) if max_ts else None,
                    "size_bytes": stat.st_size,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "updated_at": stat.st_mtime,
                })

            return sessions

    def get_inventory(
        self,
        session_id: str,
        bundle_path: Path | None = None,
        topology: Topology | None = None,
    ) -> Inventory | None:
        """Retrieve inventory for session, building and caching it if not yet present."""
        inv_path = self.sessions_dir / f"{session_id}.inventory.json"
        inv = None
        if inv_path.exists():
            try:
                inv = Inventory.from_dict(json.loads(inv_path.read_text(encoding="utf-8")))
            except Exception:
                inv = None

        if inv is None and bundle_path and bundle_path.exists():
            inv = build_inventory(bundle_path, topology=topology)
            self.save_inventory(session_id, inv)

        if inv is not None:
            # Sync parsed status from SessionStore
            store = self.get_store(session_id)
            if store:
                parsed_list = store.get_parsed_units()
                parsed_map = {p["unit_id"]: p for p in parsed_list if p.get("status") == "completed"}
                for unit in inv.get_all_units():
                    if unit.id in parsed_map:
                        unit.parsed = True
                        unit.event_count = parsed_map[unit.id].get("events", 0)

        return inv

    def save_inventory(self, session_id: str, inv: Inventory) -> None:
        """Persist inventory JSON to the sessions directory."""
        try:
            inv_path = self.sessions_dir / f"{session_id}.inventory.json"
            inv_path.write_text(json.dumps(inv.to_dict(), indent=2), encoding="utf-8")
        except Exception:
            pass
