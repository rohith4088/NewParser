"""Parser for Linux systemd binary journals (.journal files).

In production air-gapped bundles, systemd journals contain gigabytes of binary logs
(magic header ``LPKSHHRH``). These cannot be parsed as plain text.

When running on Linux or inside the Docker container, this module uses
``journalctl --file <path> -o json`` to stream events directly into the database.
On macOS / Windows where journalctl is not present, it safely logs a diagnostic
warning and skips binary parsing without failing the scan.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import tempfile
from typing import BinaryIO, Iterator

from .event import Event, detect_issue_type, detect_level
from .timestamps import micros_to_iso

logger = logging.getLogger(__name__)

# Systemd priority mapping to normalized log levels:
# 0: emerg, 1: alert, 2: crit, 3: err -> ERROR / FATAL
# 4: warning -> WARN
# 5: notice, 6: info -> INFO
# 7: debug -> DEBUG
_PRIORITY_TO_LEVEL = {
    "0": "FATAL",
    "1": "FATAL",
    "2": "FATAL",
    "3": "ERROR",
    "4": "WARN",
    "5": "INFO",
    "6": "INFO",
    "7": "DEBUG",
}


def is_journalctl_available() -> bool:
    """Check if journalctl binary is installed on the host."""
    return shutil.which("journalctl") is not None


def parse_journal_stream(
    stream: BinaryIO,
    filename: str,
    default_node: str = "host",
    source: str = "journal",
) -> Iterator[Event]:
    """Parse a binary journal stream.

    If running inside Linux/Docker with journalctl available, dumps the stream
    to a temporary file and streams JSON output from journalctl.
    """
    if not is_journalctl_available():
        logger.debug(
            "Skipping binary journal %s: journalctl binary not found on host (supported in Docker/Linux)",
            filename,
        )
        return

    # journalctl requires a seekable file on disk
    with tempfile.NamedTemporaryFile(suffix=".journal", delete=False) as tmp:
        tmp_path = tmp.name
        try:
            shutil.copyfileobj(stream, tmp)
            tmp.flush()
            yield from parse_journal_file(tmp_path, filename=filename, default_node=default_node, source=source)
        finally:
            try:
                os.remove(tmp_path)
            except OSError:
                pass


def parse_journal_file(
    file_path: str | os.PathLike,
    filename: str = "",
    default_node: str = "host",
    source: str = "journal",
) -> Iterator[Event]:
    """Stream events from a journal file on disk using journalctl -o json."""
    if not is_journalctl_available():
        return

    cmd = ["journalctl", "--file", str(file_path), "-o", "json", "--no-pager"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        line_num = 0
        assert proc.stdout is not None
        for line in proc.stdout:
            line_num += 1
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue

            msg = data.get("MESSAGE")
            if not msg:
                continue
            if isinstance(msg, list):  # byte array representation in journalctl json
                msg = bytes(msg).decode("utf-8", errors="replace")
            else:
                msg = str(msg)

            # Extract timestamp: __REALTIME_TIMESTAMP is microsecond epoch integer
            raw_ts = data.get("__REALTIME_TIMESTAMP")
            ts_micros = int(raw_ts) if raw_ts and str(raw_ts).isdigit() else None
            ts_iso = micros_to_iso(ts_micros) if ts_micros else ""

            # Node and process identity
            node = data.get("_HOSTNAME") or default_node
            unit = data.get("_SYSTEMD_UNIT") or data.get("SYSLOG_IDENTIFIER") or data.get("_COMM") or filename

            # Priority level
            priority = str(data.get("PRIORITY", "6"))
            level = _PRIORITY_TO_LEVEL.get(priority, "INFO")
            issue_type = detect_issue_type(msg, level)

            yield Event(
                ts_micros=ts_micros,
                ts_iso=ts_iso,
                source=source,
                node=node,
                namespace="systemd",
                pod=unit,
                container="",
                level=level,
                issue_type=issue_type,
                message=msg,
                file=filename or str(file_path),
                line_num=line_num,
            )
        proc.wait()
    except Exception as exc:
        logger.warning("Error running journalctl on %s: %s", file_path, exc)
