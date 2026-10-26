#!/usr/bin/env bash
# ==============================================================================
# Script: install_offline.sh
# Runs on: Offline Target VM (Ubuntu 24.04 LTS at 10.55.58.52)
# Purpose: 1-click installer: sets up Docker without package managers/debs,
#          loads container images, and starts Analyzer + Ollama stack.
# ==============================================================================

set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
    echo "[ERROR] This installer must be run as root or with sudo:"
    echo "        sudo ./install_offline.sh"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_IP="10.55.58.52"

echo "======================================================================"
echo "  Installing On-Prem Analyzer & Ollama (Complete Offline Mode)"
echo "  Target Host: Ubuntu 24.04 LTS (${TARGET_IP})"
echo "======================================================================"

# 1. Install Docker static binaries (Zero .deb files)
echo "==> [1/4] Installing Docker static engine to /usr/local/bin..."
if ! command -v dockerd >/dev/null 2>&1; then
    tar -xzf "${SCRIPT_DIR}/bin/docker.tgz" --strip-components=1 -C /usr/local/bin/
    cp "${SCRIPT_DIR}/bin/docker-compose" /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker*

    # Install & start systemd service
    cp "${SCRIPT_DIR}/bin/docker.service" /etc/systemd/system/docker.service
    systemctl daemon-reload
    systemctl enable --now docker
    echo "    Docker daemon installed and started successfully."
else
    echo "    Docker engine is already installed."
fi

# Ensure docker-compose is available
if ! command -v docker-compose >/dev/null 2>&1; then
    cp "${SCRIPT_DIR}/bin/docker-compose" /usr/local/bin/docker-compose
    chmod +x /usr/local/bin/docker-compose
fi

# Wait for Docker socket
echo "==> Verifying Docker daemon status..."
for i in {1..15}; do
    if docker info >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# 2. Check system RAM & hardware
TOTAL_RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/(1024*1024)}' /proc/meminfo 2>/dev/null || echo "16")
echo "==> Detected System RAM: ${TOTAL_RAM_GB} GB"
if [ "${TOTAL_RAM_GB}" -lt 28 ]; then
    echo "    [NOTE] If running large models (e.g. 27B / 32B), 32 GB RAM or more is recommended on HPE VME."
fi

# 3. Load pre-cached Container Images
echo "==> [2/4] Loading container images from offline archives..."
docker load -i "${SCRIPT_DIR}/images/onprem-analyzer.tar"
docker load -i "${SCRIPT_DIR}/images/ollama.tar"

# 4. Prepare working data directories & permissions
echo "==> [3/4] Preparing directories and model weights..."
mkdir -p "${SCRIPT_DIR}/data/sessions" "${SCRIPT_DIR}/data/uploads" "${SCRIPT_DIR}/bundles"
chmod -R 777 "${SCRIPT_DIR}/data" "${SCRIPT_DIR}/bundles"
chmod -R 755 "${SCRIPT_DIR}/ollama_data"

# 5. Launch Docker Compose stack
echo "==> [4/4] Launching Analyzer + Ollama stack..."
cd "${SCRIPT_DIR}"
docker-compose down 2>/dev/null || true
docker-compose up -d

echo ""
echo "==> Waiting for services to become healthy..."
sleep 5

# Health verification
HEALTH_OK=false
for i in {1..20}; do
    if curl -s http://127.0.0.1:8080/api/health | grep -q '"status": "ok"'; then
        HEALTH_OK=true
        break
    fi
    sleep 2
done

OLLAMA_OK=false
for i in {1..20}; do
    if curl -s http://127.0.0.1:11434/api/tags | grep -q 'models'; then
        OLLAMA_OK=true
        break
    fi
    sleep 2
done

MODELS_LIST=$(curl -s http://127.0.0.1:11434/api/tags 2>/dev/null | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | tr '\n' ' ' || echo "Available")

echo ""
echo "======================================================================"
if [ "$HEALTH_OK" = true ] && [ "$OLLAMA_OK" = true ]; then
    echo "  [SUCCESS] Deployment complete! Both services are healthy and running."
else
    echo "  [NOTICE] Services launched. Final health checks in progress."
fi
echo "======================================================================"
echo "  Web UI Dashboard:     http://${TARGET_IP}:8080"
echo "  Local Ollama LLM:     http://${TARGET_IP}:11434"
echo "  Preloaded Models:     ${MODELS_LIST}"
echo "  Bundle Drop Folder:   ${SCRIPT_DIR}/bundles"
echo ""
echo "  HPE VME AI Services Integration:"
echo "  - Service Type:       Ollama / OpenAI Compatible"
echo "  - Base URL:           http://${TARGET_IP}:11434"
echo "  - Model Selection:    Select from preloaded models in dropdown"
echo "======================================================================"
