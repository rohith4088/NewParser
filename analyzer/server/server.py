"""Local Threading HTTP Server for Support Bundle Analyzer."""

from __future__ import annotations

import logging
import socketserver
from http.server import ThreadingHTTPServer
from pathlib import Path

from ..analyze.correlate import EventCorrelator
from ..analyze.rules import RuleEngine
from ..config import Config
from ..topology import Topology
from .routes import RequestHandler
from .session_manager import SessionManager

logger = logging.getLogger(__name__)


class AnalyzerServer(ThreadingHTTPServer):
    """Threading HTTP server with graceful handling of client disconnects."""

    daemon_threads = True

    def handle_error(self, request: socketserver._RequestType, client_address: socketserver._AddressType) -> None:
        """Suppress noisy tracebacks for normal client disconnects (curl healthcheck, aborted fetch)."""
        import sys
        exc = sys.exc_info()[1]
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        super().handle_error(request, client_address)


def create_server(config: Config) -> AnalyzerServer:
    """Instantiate and configure the ThreadingHTTPServer with dependencies."""
    # Ensure all working directories exist
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.sessions_dir.mkdir(parents=True, exist_ok=True)
    config.uploads_dir.mkdir(parents=True, exist_ok=True)
    config.bundles_dir.mkdir(parents=True, exist_ok=True)

    session_manager = SessionManager(config.sessions_dir)
    rule_engine = RuleEngine(config.rules_dir)
    correlator = EventCorrelator()
    topology = Topology.load(config.topology_path)

    # Bind dependencies to RequestHandler class attributes
    handler_class = RequestHandler
    handler_class.config = config
    handler_class.session_manager = session_manager
    handler_class.rule_engine = rule_engine
    handler_class.correlator = correlator
    handler_class.topology = topology

    # Enable socket address reuse to prevent TIME_WAIT port conflicts
    socketserver.TCPServer.allow_reuse_address = True
    socketserver.TCPServer.request_queue_size = 128

    server = AnalyzerServer((config.host, config.port), handler_class)
    return server


def run_server(config: Config) -> None:
    """Run the analyzer web server indefinitely."""
    server = create_server(config)
    url = f"http://{config.host}:{config.port}"
    print(f"\nDisconnected Support Bundle Analyzer running at: {url}")
    print(f"Sessions store:  {config.sessions_dir}")
    print(f"Drop folder:     {config.bundles_dir}")
    print("Press Ctrl+C to stop.\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.server_close()
