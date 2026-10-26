#!/usr/bin/env bash
# ==============================================================================
# Script: install_offline.sh
# Runs on: Offline Target VM (Ubuntu 24.04 LTS at 10.55.58.52)
# Purpose: 1-click zero-dependency installer:
#          1. Installs Docker static engine to /usr/local/bin
#          2. Installs Ollama standalone engine & pre-cached AI models
#          3. Installs Analyzer Python package & dependencies from wheels/
#          4. Sets up and starts both systemd services
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
echo "  Target Host: Ubuntu 24.04 LTS (32 vCPU / 256 GB RAM)"
echo "======================================================================"

# 1. Hardware Inspection
TOTAL_RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/(1024*1024)}' /proc/meminfo 2>/dev/null || echo "16")
TOTAL_CPUS=$(nproc 2>/dev/null || echo "4")
echo "==> Hardware Detected: ${TOTAL_CPUS} CPUs, ${TOTAL_RAM_GB} GB RAM"

# 2. Install Docker static binaries & Docker Compose
echo "==> [1/4] Installing Docker static engine to /usr/local/bin..."
if [ -f "${SCRIPT_DIR}/bin/docker.tgz" ]; then
    tar -xzf "${SCRIPT_DIR}/bin/docker.tgz" --strip-components=1 -C /usr/local/bin/
    cp "${SCRIPT_DIR}/bin/docker-compose" /usr/local/bin/docker-compose 2>/dev/null || true
    chmod +x /usr/local/bin/docker*

    # Install & start docker.service
    if [ -f "${SCRIPT_DIR}/bin/docker.service" ]; then
        cp "${SCRIPT_DIR}/bin/docker.service" /etc/systemd/system/docker.service
        systemctl daemon-reload
        systemctl enable --now docker 2>/dev/null || true
    fi
    echo "    Docker engine installed."
fi

# 3. Install Ollama standalone binary
echo "==> [2/4] Installing Ollama standalone AI engine..."
if [ -f "${SCRIPT_DIR}/bin/ollama-linux-amd64.tgz" ]; then
    tar -xzf "${SCRIPT_DIR}/bin/ollama-linux-amd64.tgz" -C /usr/local/
    chmod +x /usr/local/bin/ollama
fi

# Set up Ollama models directory & user
useradd -r -s /bin/false -U -m -d /usr/share/ollama ollama 2>/dev/null || true
mkdir -p /usr/share/ollama/.ollama
if [ -d "${SCRIPT_DIR}/ollama_data/models" ]; then
    echo "    Copying pre-cached AI models into /usr/share/ollama/.ollama/..."
    cp -rn "${SCRIPT_DIR}/ollama_data/models" /usr/share/ollama/.ollama/
fi
chown -R ollama:ollama /usr/share/ollama/.ollama

# Create and start ollama.service
cat << 'EOF' > /etc/systemd/system/ollama.service
[Unit]
Description=Ollama Air-Gapped AI Service
After=network.target

[Service]
Type=exec
User=ollama
Group=ollama
ExecStart=/usr/local/bin/ollama serve
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_KEEP_ALIVE=24h"
Environment="OLLAMA_NUM_PARALLEL=4"
Environment="OLLAMA_MAX_LOADED_MODELS=2"
Restart=always
RestartSec=3
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now ollama
echo "    Ollama service enabled and started."

# 4. Install Analyzer Python dependencies & service
echo "==> [3/4] Installing Analyzer Python dependencies..."
cd "${SCRIPT_DIR}"
if [ -d "${SCRIPT_DIR}/wheels" ]; then
    python3 -m pip install --break-system-packages --no-index --find-links="${SCRIPT_DIR}/wheels" -e . || \
    python3 -m pip install --no-index --find-links="${SCRIPT_DIR}/wheels" -e . || true
fi

# Prepare working directories
mkdir -p "${SCRIPT_DIR}/data/sessions" "${SCRIPT_DIR}/data/uploads" "${SCRIPT_DIR}/bundles"
chmod -R 777 "${SCRIPT_DIR}/data" "${SCRIPT_DIR}/bundles"

# Create and start onprem-analyzer.service
cat << EOF > /etc/systemd/system/onprem-analyzer.service
[Unit]
Description=On-Prem Disconnected Support Bundle Analyzer
After=network.target ollama.service

[Service]
Type=simple
User=root
WorkingDirectory=${SCRIPT_DIR}
Environment="ANALYZER_HOST=0.0.0.0"
Environment="ANALYZER_PORT=8080"
Environment="ANALYZER_DATA_DIR=${SCRIPT_DIR}/data"
Environment="ANALYZER_BUNDLES_DIR=${SCRIPT_DIR}/bundles"
Environment="ANALYZER_RULES_DIR=${SCRIPT_DIR}/rules"
Environment="ANALYZER_TOPOLOGY_PATH=${SCRIPT_DIR}/topology/services.yaml"
Environment="ANALYZER_OLLAMA_URL=http://127.0.0.1:11434"
Environment="ANALYZER_AI_NUM_CTX=8192"
ExecStart=/usr/bin/python3 -m analyzer.cli serve --host 0.0.0.0 --port 8080
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now onprem-analyzer
echo "    Analyzer service enabled and started."

# 5. Verification
echo "==> [4/4] Verifying running services..."
sleep 4

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
    if curl -s http://127.0.0.1:11434/api/tags 2>/dev/null | grep -q 'models'; then
        OLLAMA_OK=true
        break
    fi
    sleep 2
done

MODELS_LIST=$(curl -s http://127.0.0.1:11434/api/tags 2>/dev/null | grep -o '"name":"[^"]*"' | cut -d'"' -f4 | tr '\n' ' ' || echo "Available")

echo ""
echo "======================================================================"
if [ "$HEALTH_OK" = true ] && [ "$OLLAMA_OK" = true ]; then
    echo "  [SUCCESS] Deployment complete! Both services are active & healthy."
else
    echo "  [NOTICE] Services launched. Checking initial status..."
fi
echo "======================================================================"
echo "  Web UI Dashboard:     http://${TARGET_IP}:8080"
echo "  Local Ollama LLM:     http://${TARGET_IP}:11434"
echo "  Preloaded AI Models:  ${MODELS_LIST}"
echo "  Bundle Drop Folder:   ${SCRIPT_DIR}/bundles"
echo ""
echo "  HPE VME AI Services Integration:"
echo "  - Service Type:       Ollama / OpenAI Compatible"
echo "  - Base URL:           http://${TARGET_IP}:11434"
echo "======================================================================"
