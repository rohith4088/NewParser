"""HTTP Request Handler for REST API and Air-Gapped Web Dashboard."""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import urllib.parse
import urllib.request
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

from .. import __version__
from ..agentic import AgenticTriageOrchestrator
from ..analyze.correlate import EventCorrelator
from ..analyze.rules import RuleEngine
from ..config import Config
from ..parse.pipeline import parse_bundle_to_store
from ..parse.timestamps import parse_iso_micros
from ..topology import Topology
from .session_manager import SessionManager

STATIC_DIR = Path(__file__).parent / "static"


class RequestHandler(BaseHTTPRequestHandler):
    """Handles API requests and serves embedded static dashboard assets."""

    protocol_version = "HTTP/1.1"

    config: Config
    session_manager: SessionManager
    rule_engine: RuleEngine
    correlator: EventCorrelator
    topology: Topology

    def log_message(self, format: str, *args: Any) -> None:
        # Suppress noisy standard HTTP access logs
        pass

    def handle(self) -> None:
        try:
            super().handle()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def finish(self) -> None:
        try:
            super().finish()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def send_error(self, code: int, message: str | None = None, explain: str | None = None) -> None:
        try:
            super().send_error(code, message, explain)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    # --- HTTP Method Dispatchers ----------------------------------------------

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path.startswith("/api/"):
            self._handle_api_get(path, query)
        else:
            self._serve_static(path)

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/upload":
            self._handle_upload()
        elif path.startswith("/api/"):
            self._handle_api_post(path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_PATCH(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            self._handle_api_patch(path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_DELETE(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path.startswith("/api/"):
            self._handle_api_delete(path)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    # --- API GET Endpoints ----------------------------------------------------

    def _handle_api_get(self, path: str, query: dict[str, list[str]]) -> None:
        if path == "/api/health":
            self._send_json({"status": "ok", "version": __version__})
            return

        if path == "/api/sessions":
            sessions = self.session_manager.list_sessions()
            self._send_json({
                "active_session": self.session_manager.active_session_id,
                "sessions": sessions,
            })
            return

        if path == "/api/bundles":
            self._send_json({"bundles": self._list_bundles()})
            return

        if path == "/api/bundle/namespaces":
            origin = query.get("origin", [None])[0]
            store = self.session_manager.get_store()
            counts = store.count_by_namespace(origin=origin) if store else {}

            all_known = {str(ns) for ns in self.topology.known_namespaces()} if hasattr(self, "topology") and self.topology else set()
            all_known.add("systemd")
            all_known.update(str(k) for k in counts.keys() if k is not None)

            categories_map: dict[str, list[dict[str, Any]]] = {}
            for ns in sorted(all_known, key=str):
                cat = self.topology.category_of(ns) if hasattr(self, "topology") and self.topology else "Other"
                desc = self.topology.describe(ns) if hasattr(self, "topology") and self.topology else ""
                cnt = counts.get(ns, 0)
                related_raw = self.topology.get_related(ns) if hasattr(self, "topology") and self.topology else []

                related_with_status = []
                for r in related_raw:
                    r_ns = str(r["namespace"])
                    related_with_status.append({
                        **r,
                        "ingested": counts.get(r_ns, 0) > 0,
                        "event_count": counts.get(r_ns, 0),
                    })

                item = {
                    "namespace": ns,
                    "category": cat,
                    "description": desc,
                    "ingested": cnt > 0,
                    "event_count": cnt,
                    "related": related_with_status,
                }
                if cat not in categories_map:
                    categories_map[cat] = []
                categories_map[cat].append(item)

            self._send_json({
                "session_id": self.session_manager.active_session_id,
                "total_namespaces": len(all_known),
                "ingested_count": sum(1 for ns in all_known if counts.get(ns, 0) > 0),
                "categories": categories_map,
            })
            return

        if path == "/api/topology/related":
            ns = query.get("namespace", [""])[0]
            store = self.session_manager.get_store()
            counts = store.count_by_namespace() if store else {}
            related_raw = self.topology.get_related(ns) if hasattr(self, "topology") and self.topology else []
            related_with_status = []
            for r in related_raw:
                r_ns = r["namespace"]
                related_with_status.append({
                    **r,
                    "ingested": counts.get(r_ns, 0) > 0,
                    "event_count": counts.get(r_ns, 0),
                })
            self._send_json({
                "namespace": ns,
                "category": self.topology.category_of(ns) if hasattr(self, "topology") and self.topology else "Other",
                "description": self.topology.describe(ns) if hasattr(self, "topology") and self.topology else "",
                "ingested": counts.get(ns, 0) > 0,
                "event_count": counts.get(ns, 0),
                "related": related_with_status,
            })
        if path == "/api/inventory":
            sess_id = query.get("session_id", [None])[0] or self.session_manager.active_session_id
            bundle_name = query.get("bundle_name", [None])[0]
            bundle_path = None
            if bundle_name:
                bundle_path = self.config.bundles_dir / bundle_name
                if not bundle_path.exists():
                    bundle_path = self.config.uploads_dir / bundle_name
                if not bundle_path.exists():
                    bundle_path = self.config.data_dir / bundle_name
            elif sess_id:
                bundle_path = self._find_bundle_for_session(sess_id)

            if not bundle_path or not bundle_path.exists():
                self._send_json({"error": "Bundle archive not found"}, status=HTTPStatus.NOT_FOUND)
                return

            if not sess_id:
                sess_id = bundle_path.stem
                for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar", ".zip", ".tgz"):
                    if bundle_path.name.endswith(ext):
                        sess_id = bundle_path.name[: -len(ext)]
                        break

            inv = self.session_manager.get_inventory(
                sess_id,
                bundle_path=bundle_path,
                topology=self.topology,
            )
            if not inv:
                self._send_json({"error": "Failed to inspect inventory"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self._send_json(inv.to_dict())
            return

        if path == "/api/jobs":
            sess_id = query.get("session_id", [None])[0] or self.session_manager.active_session_id
            if not sess_id:
                self._send_json({"error": "No active session"}, status=HTTPStatus.NOT_FOUND)
                return
            q = self.session_manager.job_manager.find_queue(sess_id)
            if not q:
                self._send_json({
                    "session_id": sess_id,
                    "is_running": False,
                    "current_unit": None,
                    "total_queued": 0,
                    "completed_count": 0,
                    "pending_count": 0,
                    "items": [],
                })
            else:
                self._send_json(q.status())
            return

        if path == "/api/ai/status":
            self._handle_ai_status()
            return

        if path == "/api/agentic/tree":
            self._handle_agentic_tree(query)
            return

        if path == "/api/agentic/report":
            self._handle_agentic_report(query)
            return

        # Endpoints requiring an active session
        store = self.session_manager.get_store()
        if not store:
            self._send_json({"error": "No active support bundle session", "sessions": []}, status=HTTPStatus.NOT_FOUND)
            return

        from_micros = self._parse_micros(query.get("from", [""])[0])
        to_micros = self._parse_micros(query.get("to", [""])[0])

        if path == "/api/metadata":
            meta = store.get_metadata() or {}
            self._send_json(meta)

        elif path == "/api/stats":
            origin = query.get("origin", [None])[0]
            levels = store.count_by_level(from_micros=from_micros, to_micros=to_micros)
            namespaces = store.count_by_namespace(from_micros=from_micros, to_micros=to_micros, origin=origin)
            origins = store.count_by_origin(from_micros=from_micros, to_micros=to_micros)
            min_ts, max_ts = store.get_time_bounds()
            self._send_json({
                "levels": levels,
                "namespaces": namespaces,
                "origins": origins,
                "min_ts_micros": min_ts,
                "max_ts_micros": max_ts,
            })

        elif path == "/api/events":
            origin = query.get("origin", ["all"])[0]
            level = query.get("level", ["all"])[0]
            ns = query.get("namespace", ["all"])[0]
            issue_type = query.get("issue_type", ["all"])[0]
            search = query.get("search", [""])[0]
            limit = int(query.get("limit", [100])[0])
            offset = int(query.get("offset", [0])[0])

            events = store.query_events(
                from_micros=from_micros,
                to_micros=to_micros,
                origin=origin,
                level=level,
                namespace=ns,
                issue_type=issue_type,
                search=search,
                limit=limit,
                offset=offset,
            )
            self._send_json({"events": events, "limit": limit, "offset": offset, "origin": origin})

        elif path == "/api/timeline":
            # Generate histogram buckets across the time window
            min_ts, max_ts = store.get_time_bounds()
            start = from_micros if from_micros is not None else min_ts
            end = to_micros if to_micros is not None else max_ts
            num_buckets = int(query.get("buckets", [40])[0])

            buckets = self._build_timeline(store, start, end, num_buckets)
            self._send_json({"buckets": buckets, "from_micros": start, "to_micros": end})

        elif path == "/api/findings":
            findings = self.rule_engine.evaluate(store, from_micros=from_micros, to_micros=to_micros)
            self._send_json({"findings": [f.to_dict() for f in findings]})

        elif path == "/api/anomalies":
            anomalies = self.correlator.detect_spikes(store, from_micros=from_micros, to_micros=to_micros)
            self._send_json({"anomalies": [a.to_dict() for a in anomalies]})

        elif path == "/api/k8s_facts":
            reason = query.get("reason", [None])[0]
            ns = query.get("namespace", ["all"])[0]
            facts = store.get_k8s_facts(reason=reason, namespace=ns)
            self._send_json({"k8s_facts": facts})

        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    # --- API POST Endpoints ---------------------------------------------------

    def _handle_api_post(self, path: str) -> None:
        body = self._read_json_body()

        if path == "/api/sessions/switch":
            sess_id = body.get("session_id", "")
            if not sess_id or not self.session_manager.switch_session(sess_id):
                self._send_json({"error": f"Session {sess_id!r} not found"}, status=HTTPStatus.NOT_FOUND)
                return
            self._send_json({"status": "ok", "active_session": sess_id})

        elif path == "/api/bundles/analyze":
            bundle_name = body.get("bundle_name", "")
            include_namespaces = body.get("include_namespaces") or body.get("namespaces")
            if isinstance(include_namespaces, str):
                include_namespaces = [include_namespaces]
            units = body.get("units")
            if isinstance(units, str):
                units = [units]
            mode = body.get("mode")

            bundle_path = self.config.bundles_dir / bundle_name
            if not bundle_path.exists():
                bundle_path = self.config.uploads_dir / bundle_name
            if not bundle_path.exists():
                bundle_path = self.config.data_dir / bundle_name
            if not bundle_path.exists():
                self._send_json({"error": f"Bundle file {bundle_name!r} not found in bundles directory"}, status=HTTPStatus.NOT_FOUND)
                return

            sess_id = bundle_path.stem
            for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar", ".zip", ".tgz"):
                if bundle_name.endswith(ext):
                    sess_id = bundle_name[: -len(ext)]
                    break

            db_path = self.config.sessions_dir / f"{sess_id}.duckdb"
            from ..store.duck import SessionStore

            inv = self.session_manager.get_inventory(sess_id, bundle_path=bundle_path, topology=self.topology)

            if mode == "inventory":
                import time
                now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
                with SessionStore(db_path, read_only=False) as s:
                    s.set_metadata(session_id=sess_id, filename=bundle_path.name, scanned_at=now_str)
                self.session_manager.switch_session(sess_id)
                self._send_json({
                    "status": "ok",
                    "session_id": sess_id,
                    "inventory": inv.to_dict() if inv else None,
                })
                return

            with SessionStore(db_path, read_only=False) as s:
                parse_bundle_to_store(
                    bundle_path,
                    s,
                    session_id=sess_id,
                    include_namespaces=include_namespaces,
                    units=units,
                    inventory=inv,
                )

            self.session_manager.switch_session(sess_id)
            self._send_json({"status": "ok", "session_id": sess_id})

        elif path == "/api/jobs":
            sess_id = body.get("session_id") or self.session_manager.active_session_id
            if not sess_id:
                self._send_json({"error": "No active session specified"}, status=HTTPStatus.BAD_REQUEST)
                return

            unit_ids = body.get("units", [])
            if isinstance(unit_ids, str):
                unit_ids = [unit_ids]
            if not unit_ids:
                self._send_json({"error": "No units provided to enqueue"}, status=HTTPStatus.BAD_REQUEST)
                return

            bundle_path = self._find_bundle_for_session(sess_id)
            if not bundle_path or not bundle_path.exists():
                self._send_json({"error": f"Support bundle for session {sess_id} not found"}, status=HTTPStatus.NOT_FOUND)
                return

            db_path = self.config.sessions_dir / f"{sess_id}.duckdb"
            inv = self.session_manager.get_inventory(sess_id, bundle_path=bundle_path, topology=self.topology)
            if not inv:
                self._send_json({"error": "Could not inspect bundle inventory"}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                return

            self.session_manager.switch_session(sess_id)

            queue = self.session_manager.job_manager.get_queue(
                session_id=sess_id,
                bundle_path=bundle_path,
                db_path=db_path,
                inventory=inv,
            )
            queue.enqueue(unit_ids)
            self._send_json({
                "status": "ok",
                "session_id": sess_id,
                "queued_count": len(unit_ids),
                "queue": queue.status(),
            })

        elif path == "/api/sessions/ingest_namespace":
            sess_id = body.get("session_id") or self.session_manager.active_session_id
            if not sess_id:
                self._send_json({"error": "No active session"}, status=HTTPStatus.NOT_FOUND)
                return

            raw_ns = body.get("namespaces", [])
            if isinstance(raw_ns, str):
                raw_ns = [raw_ns]
            target_namespaces = [str(n).strip() for n in raw_ns if str(n).strip()]
            if not target_namespaces:
                self._send_json({"error": "No namespaces specified"}, status=HTTPStatus.BAD_REQUEST)
                return

            db_path = self.config.sessions_dir / f"{sess_id}.duckdb"
            if not db_path.exists():
                self._send_json({"error": f"Session database for {sess_id} not found"}, status=HTTPStatus.NOT_FOUND)
                return

            bundle_path = self._find_bundle_for_session(sess_id)
            if not bundle_path:
                self._send_json({"error": f"Support bundle archive for session {sess_id} not found"}, status=HTTPStatus.NOT_FOUND)
                return

            # Release any active read-only connection before modifying DuckDB
            self.session_manager.close_store(sess_id)

            from ..store.duck import SessionStore
            with SessionStore(db_path, read_only=False) as store:
                manifest = parse_bundle_to_store(
                    bundle_path,
                    store,
                    session_id=sess_id,
                    include_namespaces=target_namespaces,
                )

            # Re-open active session
            self.session_manager.switch_session(sess_id)
            self._send_json({
                "status": "ok",
                "session_id": sess_id,
                "ingested_namespaces": target_namespaces,
                "files_parsed": manifest.total_files,
            })

        elif path == "/api/ai/chat":
            self._handle_ai_chat(body)

        elif path == "/api/agentic/triage":
            self._handle_agentic_triage(body)

        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_api_patch(self, path: str) -> None:
        body = self._read_json_body()
        if path == "/api/jobs":
            sess_id = body.get("session_id") or self.session_manager.active_session_id
            if not sess_id:
                self._send_json({"error": "No active session specified"}, status=HTTPStatus.BAD_REQUEST)
                return

            q = self.session_manager.job_manager.find_queue(sess_id)
            if not q:
                self._send_json({"error": f"No active queue for session {sess_id}"}, status=HTTPStatus.NOT_FOUND)
                return

            order = body.get("order", [])
            if not isinstance(order, list):
                self._send_json({"error": "Expected 'order' list of unit IDs"}, status=HTTPStatus.BAD_REQUEST)
                return

            q.reorder(order)
            self._send_json({"status": "ok", "queue": q.status()})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def _handle_api_delete(self, path: str) -> None:
        body = self._read_json_body()
        if path == "/api/jobs":
            sess_id = body.get("session_id") or self.session_manager.active_session_id
            if not sess_id:
                self._send_json({"error": "No active session specified"}, status=HTTPStatus.BAD_REQUEST)
                return

            q = self.session_manager.job_manager.find_queue(sess_id)
            if not q:
                self._send_json({"error": f"No active queue for session {sess_id}"}, status=HTTPStatus.NOT_FOUND)
                return

            q.cancel()
            self._send_json({"status": "cancelled", "queue": q.status()})
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    # --- Chunked Streaming Upload Handler -------------------------------------

    def _handle_upload(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length > self.config.max_upload_bytes:
            self._send_json(
                {"error": f"Upload exceeds maximum limit ({self.config.max_upload_bytes // (1024**3)} GB)"},
                status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            )
            return

        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        inventory_only = (
            query.get("mode", [""])[0] == "inventory"
            or query.get("inventory_only", [""])[0] == "true"
            or self.headers.get("X-Inventory-Only") == "true"
        )

        filename = self.headers.get("X-File-Name") or "uploaded_bundle.tar"
        filename = Path(filename).name  # sanitize

        self.config.uploads_dir.mkdir(parents=True, exist_ok=True)
        upload_path = self.config.uploads_dir / filename

        # Stream directly to disk in 1MB chunks to keep memory bounded
        remaining = content_length
        chunk_size = 1024 * 1024
        with open(upload_path, "wb") as f:
            while remaining > 0:
                to_read = min(remaining, chunk_size)
                chunk = self.rfile.read(to_read)
                if not chunk:
                    break
                f.write(chunk)
                remaining -= len(chunk)

        # Session ID derived from filename stem
        stem = filename
        for ext in (".tar.gz", ".tar.bz2", ".tar.xz", ".tar", ".zip", ".tgz"):
            if stem.endswith(ext):
                stem = stem[: -len(ext)]
                break

        sess_id = stem
        db_path = self.config.sessions_dir / f"{sess_id}.duckdb"
        from ..store.duck import SessionStore

        if inventory_only:
            import time
            now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
            with SessionStore(db_path, read_only=False) as store:
                store.set_metadata(session_id=sess_id, filename=filename, scanned_at=now_str)
            inv = self.session_manager.get_inventory(sess_id, bundle_path=upload_path, topology=self.topology)
            self.session_manager.switch_session(sess_id)
            self._send_json({
                "status": "ok",
                "session_id": sess_id,
                "filename": filename,
                "inventory": inv.to_dict() if inv else None,
            })
            return

        # Default / full parse flow
        with SessionStore(db_path, read_only=False) as store:
            parse_bundle_to_store(upload_path, store, session_id=sess_id)

        self.session_manager.get_inventory(sess_id, bundle_path=upload_path, topology=self.topology)
        self.session_manager.switch_session(sess_id)
        self._send_json({"status": "ok", "session_id": sess_id, "filename": filename})

    # --- Helper Utilities -----------------------------------------------------

    def _find_bundle_for_session(self, sess_id: str) -> Path | None:
        store = self.session_manager.get_store(sess_id)
        filename = None
        if store:
            meta = store.get_metadata()
            if meta:
                filename = meta.get("filename")

        candidates: list[Path] = []
        if filename:
            candidates.extend([
                self.config.uploads_dir / filename,
                self.config.bundles_dir / filename,
                self.config.data_dir / filename,
            ])

        for ext in (".tar", ".tar.gz", ".tgz", ".zip", ".tar.bz2", ".tar.xz"):
            candidates.extend([
                self.config.uploads_dir / f"{sess_id}{ext}",
                self.config.bundles_dir / f"{sess_id}{ext}",
                self.config.data_dir / f"{sess_id}{ext}",
            ])

        for c in candidates:
            if c.exists() and c.is_file():
                return c

        for search_dir in (self.config.uploads_dir, self.config.bundles_dir, self.config.data_dir):
            if search_dir.exists():
                for p in search_dir.iterdir():
                    if p.is_file() and p.name.startswith(sess_id):
                        return p

        return None

    def _list_bundles(self) -> list[dict[str, Any]]:
        self.config.bundles_dir.mkdir(parents=True, exist_ok=True)
        results = []
        for p in sorted(self.config.bundles_dir.iterdir()):
            if p.is_file() and any(p.name.endswith(ext) for ext in (".tar", ".tar.gz", ".tgz", ".zip")):
                stat = p.stat()
                results.append({
                    "name": p.name,
                    "size_mb": round(stat.st_size / (1024 * 1024), 2),
                    "modified": stat.st_mtime,
                })
        return results

    def _parse_micros(self, val: str) -> int | None:
        if not val:
            return None
        if val.isdigit():
            return int(val)
        return parse_iso_micros(val)

    def _build_timeline(
        self, store, start_micros: int | None, end_micros: int | None, num_buckets: int
    ) -> list[dict[str, Any]]:
        if not start_micros or not end_micros or start_micros >= end_micros or num_buckets <= 0:
            return []

        span = end_micros - start_micros
        bucket_size = max(1_000_000, span // num_buckets)

        if hasattr(store, "get_timeline_data"):
            rows = store.get_timeline_data(start_micros, end_micros, bucket_size)
        else:
            sql = f"""
                SELECT
                    ((ts_micros - {start_micros}) // {bucket_size}) AS b_idx,
                    level,
                    COUNT(*)
                FROM events
                WHERE ts_micros >= {start_micros} AND ts_micros <= {end_micros}
                GROUP BY b_idx, level
                ORDER BY b_idx ASC
            """
            with store.lock:
                rows = store.conn.execute(sql).fetchall()

        # Initialize empty buckets
        buckets = [
            {
                "index": i,
                "start_micros": start_micros + i * bucket_size,
                "end_micros": start_micros + (i + 1) * bucket_size,
                "total": 0,
                "fatal": 0,
                "error": 0,
                "warn": 0,
                "info": 0,
            }
            for i in range(num_buckets + 1)
        ]

        for row in rows:
            if not isinstance(row, (tuple, list)) or len(row) != 3:
                continue
            b_idx, lvl, count = row
            if b_idx is not None and isinstance(b_idx, (int, float)) and 0 <= int(b_idx) < len(buckets):
                b = buckets[int(b_idx)]
                b["total"] += count
                lvl_lower = str(lvl).lower()
                if lvl_lower in b:
                    b[lvl_lower] += count

        return [b for b in buckets if b["index"] < num_buckets]

    def _serve_static(self, path: str) -> None:
        clean_path = path.lstrip("/")
        if not clean_path or clean_path == "index.html":
            file_path = STATIC_DIR / "index.html"
        else:
            file_path = STATIC_DIR / clean_path

        # Security: prevent directory traversal
        try:
            file_path = file_path.resolve()
            if not str(file_path).startswith(str(STATIC_DIR.resolve())):
                self.send_error(HTTPStatus.FORBIDDEN)
                return
        except Exception:
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return

        content_type, _ = mimetypes.guess_type(str(file_path))
        content_type = content_type or "application/octet-stream"
        if file_path.suffix == ".js":
            content_type = "application/javascript"
        elif file_path.suffix == ".css":
            content_type = "text/css"

        try:
            data = file_path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        try:
            raw = self.rfile.read(length)
            return json.loads(raw)
        except Exception:
            return {}

    def _send_json(self, data: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        try:
            body = json.dumps(data, indent=2).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    # --- Ollama Local AI Copilot Integration ----------------------------------

    def _find_ollama_url(self) -> str | None:
        candidates = [
            getattr(self.config, "ollama_url", None),
            os.environ.get("ANALYZER_OLLAMA_URL"),
            os.environ.get("OLLAMA_HOST"),
            "http://host.docker.internal:11434",
            "http://127.0.0.1:11434",
            "http://localhost:11434",
        ]
        for url in candidates:
            if not url:
                continue
            if not url.startswith("http"):
                url = f"http://{url}"
            try:
                req = urllib.request.Request(f"{url}/api/tags", headers={"User-Agent": "OnpremAnalyzer"})
                with urllib.request.urlopen(req, timeout=1.0) as resp:
                    if resp.status == 200:
                        return url
            except Exception:
                continue
        return None

    PREFERRED_AI_MODELS = [
        "qwen2.5:32b",
        "qwen3.8:27b",
        "qwen2.5:14b",
        "qwen2.5:7b",
        "qwen2.5:3b",
        "qwen2.5-coder:32b",
        "qwen2.5-coder:14b",
        "qwen2.5-coder:7b",
        "qwen2.5-coder:3b",
        "llama3.3:70b",
        "llama3.1:8b",
        "llama3.2:3b",
        "deepseek-r1:14b",
        "deepseek-r1:8b",
        "deepseek-r1:1.5b",
        "qwen3.5:0.8b",
    ]

    def _resolve_best_model(self, installed_models: list[str]) -> str:
        configured = getattr(self.config, "ai_model", None) or os.environ.get("ANALYZER_AI_MODEL")
        if configured and any(configured in m for m in installed_models):
            return configured

        for pref in self.PREFERRED_AI_MODELS:
            if any(pref in m for m in installed_models):
                return pref
        local_models = [m for m in installed_models if not m.endswith(":cloud")]
        if local_models:
            return local_models[0]
        if installed_models:
            return installed_models[0]
        return configured or "qwen2.5:3b"

    def _handle_ai_status(self) -> None:
        default_model = getattr(self.config, "ai_model", "qwen2.5:3b")
        ollama_url = self._find_ollama_url()
        if not ollama_url:
            self._send_json({
                "available": False,
                "error": "Ollama service not detected on localhost:11434 or host.docker.internal:11434",
                "model": default_model,
                "installed_models": [],
            })
            return
        try:
            req = urllib.request.Request(f"{ollama_url}/api/tags")
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                models = [m.get("name", "") for m in data.get("models", [])]
                best_model = self._resolve_best_model(models)
                has_model = any(best_model in m for m in models)
                self._send_json({
                    "available": True,
                    "ollama_url": ollama_url,
                    "has_model": has_model,
                    "model": best_model,
                    "installed_models": models,
                })
        except Exception as exc:
            self._send_json({
                "available": False,
                "error": str(exc),
                "model": default_model,
                "installed_models": [],
            })

    def _handle_ai_chat(self, body: dict[str, Any]) -> None:
        user_message = (body.get("message") or "").strip()
        if not user_message:
            self._send_json({"error": "Message is required"}, status=HTTPStatus.BAD_REQUEST)
            return

        ollama_url = self._find_ollama_url()
        if not ollama_url:
            self._send_json({
                "error": "Local Ollama service is not reachable. Ensure Ollama is running ('ollama run qwen2.5:3b')."
            }, status=HTTPStatus.SERVICE_UNAVAILABLE)
            return

        sess_id = body.get("session_id") or self.session_manager.active_session_id
        active_ns = body.get("namespace")
        from_micros = body.get("from")
        to_micros = body.get("to")
        history = body.get("history") or []
        requested_model = body.get("model")

        # Extract dynamic log context from DuckDB enriched with deterministic rules
        store = self.session_manager.get_store(sess_id)
        rule_findings = []
        if getattr(self, "rule_engine", None) and store:
            try:
                rule_findings = self.rule_engine.evaluate(store, from_micros=from_micros, to_micros=to_micros)
            except Exception:
                rule_findings = []

        if store:
            log_context = store.get_ai_context(
                user_query=user_message,
                namespace=active_ns,
                from_micros=from_micros,
                to_micros=to_micros,
                limit=12,
                rule_findings=rule_findings,
            )
        else:
            log_context = "No active support bundle session loaded."

        system_prompt = (
            "You are the HPE On-Prem Kubernetes & Storage Diagnostic Copilot, powered by local air-gapped intelligence.\n"
            "Analyze the verified DuckDB telemetry, incident signatures, Drain log templates, and live logs below.\n\n"
            "Structured Diagnostic Playbook Format:\n"
            "1. **Root Cause Analysis (RCA)**: Identify the primary failure trigger. If a 'Patient Zero' candidate is listed, explicitly analyze whether it is the root trigger and describe the failure mechanism.\n"
            "2. **Trigger Sequence & Cascading Impact**: Detail the chronological cascade from the initial fault to downstream services using the mined Drain templates and timestamps.\n"
            "3. **Impacted Components & Blast Radius**: Clearly summarize affected namespaces, pods, exit codes (e.g. 137 OOM, 143 SIGTERM), and restart counts in bullet or table format.\n"
            "4. **Actionable Remediation Playbook**: Provide exact, copy-pasteable CLI commands (`kubectl describe ...`, `crictl logs ...`, `systemctl status ...`, YAML config fixes) to resolve the incident.\n\n"
            "Strict Grounding Rules:\n"
            "- Ground all conclusions strictly in the provided timestamps, components, error codes, and log patterns.\n"
            "- Do not invent nonexistent resources, pods, or metrics.\n\n"
            "=== DYNAMIC LOG CONTEXT ===\n"
            f"{log_context}"
        )

        messages = [{"role": "system", "content": system_prompt}]
        for item in history[-4:]:
            if isinstance(item, dict) and item.get("role") and item.get("content"):
                messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": user_message})

        target_model = requested_model or getattr(self.config, "ai_model", "qwen2.5:3b")
        enable_think = bool(body.get("think", False))

        num_ctx = int(os.environ.get("ANALYZER_AI_NUM_CTX", 8192 if any(k in target_model for k in ("14b", "27b", "32b", "70b")) else 4096))
        ollama_payload = {
            "model": target_model,
            "messages": messages,
            "stream": True,
            "options": {
                "num_ctx": num_ctx,
                "num_predict": -1,  # -1 = Unlimited/Full-length reply until natural completion
                "temperature": 0.2,
            },
        }
        if enable_think:
            ollama_payload["think"] = True

        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()

            # Immediate initial ping so client UI activates instantly
            try:
                self.wfile.write(b"data: {\"status\": \"connected\"}\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

            req = urllib.request.Request(
                f"{ollama_url}/api/chat",
                data=json.dumps(ollama_payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            resp = urllib.request.urlopen(req, timeout=180.0)

            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                    msg = chunk.get("message") or {}
                    content_piece = msg.get("content", "")
                    thinking_piece = msg.get("thinking", "")
                    is_done = chunk.get("done", False)

                    payload = json.dumps({
                        "content": content_piece,
                        "thinking": thinking_piece,
                        "done": is_done,
                    })
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                    if is_done:
                        break
                except (BrokenPipeError, ConnectionResetError):
                    # Client disconnected early (e.g. closed drawer, navigated away)
                    break
                except Exception:
                    continue

        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            err_data = json.dumps({"error": f"Ollama generation failed: {exc}", "done": True})
            try:
                self.wfile.write(f"data: {err_data}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass

    # --- Agentic Multi-Agent Framework Integration ---------------------------

    _AGENTIC_REPORTS: dict[str, Any] = {}

    def _handle_agentic_tree(self, query: dict[str, list[str]]) -> None:
        sess_id = query.get("session_id", [None])[0] or self.session_manager.active_session_id
        store = self.session_manager.get_store(sess_id)
        if not store:
            self._send_json({"error": "No active support bundle session loaded"}, status=HTTPStatus.NOT_FOUND)
            return

        from_micros = self._parse_micros(query.get("from", [""])[0])
        to_micros = self._parse_micros(query.get("to", [""])[0])

        orchestrator = AgenticTriageOrchestrator(store)
        targets = orchestrator.discover_targets(from_micros, to_micros)
        self._send_json({
            "session_id": sess_id,
            "namespaces": targets["namespaces"],
            "host_units": targets["host_units"],
            "total_agents": len(targets["namespaces"]) + len(targets["host_units"]) + 3,
        })

    def _handle_agentic_report(self, query: dict[str, list[str]]) -> None:
        sess_id = query.get("session_id", [None])[0] or self.session_manager.active_session_id
        if not sess_id or sess_id not in self._AGENTIC_REPORTS:
            self._send_json({"report": None, "session_id": sess_id})
            return
        self._send_json({"report": self._AGENTIC_REPORTS[sess_id], "session_id": sess_id})

    def _handle_agentic_triage(self, body: dict[str, Any]) -> None:
        sess_id = body.get("session_id") or self.session_manager.active_session_id
        store = self.session_manager.get_store(sess_id)
        if not store:
            self._send_json({"error": "No active support bundle session loaded"}, status=HTTPStatus.NOT_FOUND)
            return

        from_micros = body.get("from")
        to_micros = body.get("to")
        model = body.get("model") or getattr(self.config, "ai_model", "qwen2.5:3b")
        use_llm = bool(body.get("use_llm", True))

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        orchestrator = AgenticTriageOrchestrator(store, model=model, use_llm=use_llm)
        try:
            for event in orchestrator.run_stream(session_id=sess_id or "default", from_micros=from_micros, to_micros=to_micros):
                if event.get("type") == "triage_completed" and sess_id:
                    self._AGENTIC_REPORTS[sess_id] = event.get("report")
                payload = json.dumps(event)
                self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as exc:
            err_data = json.dumps({"type": "error", "error": str(exc)})
            try:
                self.wfile.write(f"data: {err_data}\n\n".encode("utf-8"))
                self.wfile.flush()
            except Exception:
                pass
