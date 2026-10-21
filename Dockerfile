# ==============================================================================
# Multi-Arch Production Container for Air-Gapped Support Bundle Analyzer
# Target Architecture: linux/amd64 & linux/arm64
# Base: Python 3.12 Slim (Debian Bookworm) with systemd/journalctl
# ==============================================================================

FROM --platform=$TARGETPLATFORM python:3.12-slim-bookworm AS base

# Prevent Python from writing .pyc files and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies:
# - systemd: provides journalctl for reading binary 4GB .journal files
# - curl: provides container healthcheck
# - tar, gzip, bzip2, xz-utils: native archive decompression tools
RUN apt-get update && apt-get install -y --no-install-recommends \
    systemd \
    curl \
    tar \
    gzip \
    bzip2 \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Install Python package dependencies
COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy application source code, topology, and detection rules
COPY analyzer/ ./analyzer/
COPY rules/ ./rules/
COPY topology/ ./topology/

# Create working data directories and non-root user
RUN mkdir -p /app/data/sessions /app/data/uploads /app/bundles && \
    useradd -u 1000 -m -s /bin/bash analyzer && \
    chown -R analyzer:analyzer /app

USER analyzer

# Configure analyzer environment variables
ENV ANALYZER_HOST=0.0.0.0 \
    ANALYZER_PORT=8080 \
    ANALYZER_DATA_DIR=/app/data \
    ANALYZER_BUNDLES_DIR=/app/bundles \
    ANALYZER_RULES_DIR=/app/rules \
    ANALYZER_TOPOLOGY_PATH=/app/topology/services.yaml

EXPOSE 8080

# Container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://127.0.0.1:8080/api/health || exit 1

ENTRYPOINT ["python3", "-m", "analyzer.cli"]
CMD ["serve"]
