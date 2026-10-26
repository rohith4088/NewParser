"""Model Context Protocol (MCP) Server for HPE VME / Morpheus AI Integration.

Exposes On-Prem Analyzer support bundle diagnostics, DuckDB queries, and
agentic triage reports as standard MCP tools.
"""

from __future__ import annotations

import json
import sys
import urllib.parse
import urllib.request
from typing import Any

ANALYZER_URL = "http://127.0.0.1:8080"


def _call_api(path: str, params: dict[str, Any] | None = None) -> Any:
    url = f"{ANALYZER_URL}{path}"
    if params:
        url += f"?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Onprem-MCP-Server"})
    try:
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": str(exc)}


MCP_TOOLS = [
    {
        "name": "list_sessions",
        "description": "Lists all ingested support bundle sessions and active session metadata in the analyzer.",
        "inputSchema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "get_triage_report",
        "description": "Retrieves the autonomous agentic root-cause triage report for a support bundle session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Session ID (optional; defaults to the currently active session)",
                }
            },
        },
    },
    {
        "name": "query_critical_events",
        "description": "Queries high-priority cluster error logs, fatal events, and stack traces.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "namespace": {"type": "string", "description": "Kubernetes namespace (optional)"},
                "level": {"type": "string", "enum": ["ERROR", "FATAL", "WARN", "all"], "default": "ERROR"},
                "limit": {"type": "integer", "default": 25},
            },
        },
    },
    {
        "name": "get_k8s_anomalies",
        "description": "Inspects detected Kubernetes anomalies (CrashLoopBackOff, OOMKilled, HighRestartCount).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Filter by reason (e.g. OOMKilled, CrashLoopBackOff)"},
            },
        },
    },
]


def handle_tool_call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name == "list_sessions":
        return _call_api("/api/sessions")
    elif name == "get_triage_report":
        sess_id = arguments.get("session_id")
        params = {"session_id": sess_id} if sess_id else {}
        return _call_api("/api/agentic/report", params)
    elif name == "query_critical_events":
        params = {
            "level": arguments.get("level", "ERROR"),
            "namespace": arguments.get("namespace", "all"),
            "limit": arguments.get("limit", 25),
        }
        return _call_api("/api/events", params)
    elif name == "get_k8s_anomalies":
        return _call_api("/api/findings")
    else:
        return {"error": f"Unknown tool: {name}"}


def main() -> None:
    """Standard JSON-RPC 2.0 loop for MCP clients (stdio transport)."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            req_id = req.get("id")
            method = req.get("method")

            if method == "tools/list":
                res = {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}}
            elif method == "tools/call":
                params = req.get("params", {})
                tool_name = params.get("name")
                args = params.get("arguments", {})
                tool_res = handle_tool_call(tool_name, args)
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(tool_res, indent=2)}]
                    },
                }
            elif method == "initialize":
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "onprem-analyzer-mcp", "version": "0.1.0"},
                    },
                }
            else:
                res = {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}}

            sys.stdout.write(json.dumps(res) + "\n")
            sys.stdout.flush()
        except Exception as e:
            err_res = {"jsonrpc": "2.0", "error": {"code": -32700, "message": str(e)}}
            sys.stdout.write(json.dumps(err_res) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
