"""Runtime configuration.

Replaces the hardcoded absolute paths in the legacy parser. Every value has a
safe default and can be overridden with an ``ANALYZER_*`` environment variable,
which is how the container will configure it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Project root = the directory containing the ``analyzer`` package.
PROJECT_ROOT = Path(__file__).resolve().parents[1]

_GIB = 1024**3


class ConfigError(ValueError):
    """Raised when a configuration value is invalid."""


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc
    return value


@dataclass(frozen=True)
class Config:
    """Immutable settings for one analyzer process."""

    # Where parsed sessions (one DuckDB file per bundle) are stored.
    data_dir: Path = PROJECT_ROOT / "data"
    # Drop folder: bundles copied here can be analyzed without browser upload.
    bundles_dir: Path = PROJECT_ROOT / "bundles"
    # Declarative service topology (namespace -> category/description).
    topology_path: Path = PROJECT_ROOT / "topology" / "services.yaml"
    # Directory of YAML detection rules.
    rules_dir: Path = PROJECT_ROOT / "rules"
    # The UI is local-only by design, so bind to loopback unless told otherwise.
    host: str = "127.0.0.1"
    port: int = 8080
    # Upload ceiling for the browser route. Larger bundles use the drop folder
    # or the CLI.
    max_upload_bytes: int = 8 * _GIB

    @property
    def sessions_dir(self) -> Path:
        return self.data_dir / "sessions"

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @classmethod
    def from_env(cls) -> "Config":
        """Build a Config from ``ANALYZER_*`` environment variables."""
        defaults = cls()
        port = _env_int("ANALYZER_PORT", defaults.port)
        if not 1 <= port <= 65535:
            raise ConfigError(f"ANALYZER_PORT must be 1-65535, got {port}")
        max_upload = _env_int("ANALYZER_MAX_UPLOAD_BYTES", defaults.max_upload_bytes)
        if max_upload <= 0:
            raise ConfigError("ANALYZER_MAX_UPLOAD_BYTES must be positive")
        return cls(
            data_dir=Path(os.environ.get("ANALYZER_DATA_DIR", defaults.data_dir)),
            bundles_dir=Path(os.environ.get("ANALYZER_BUNDLES_DIR", defaults.bundles_dir)),
            topology_path=Path(
                os.environ.get("ANALYZER_TOPOLOGY_PATH", defaults.topology_path)
            ),
            rules_dir=Path(os.environ.get("ANALYZER_RULES_DIR", defaults.rules_dir)),
            host=os.environ.get("ANALYZER_HOST", defaults.host),
            port=port,
            max_upload_bytes=max_upload,
        )
