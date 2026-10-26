#!/usr/bin/env bash
# ==============================================================================
# Script: prepare_airgap_bundle.sh
# Runs on: Jump Server (with Internet Access)
# Purpose: Pre-downloads Docker static binaries, Docker Compose, container images,
#          and one or more Ollama AI model weights (including 27B/32B models)
#          into a single zero-dependency air-gap tarball.
# ==============================================================================

set -euo pipefail

# Versions
DOCKER_VERSION="27.3.1"
COMPOSE_VERSION="v2.29.7"

# Accept multiple models as arguments, or default to qwen2.5:3b
if [ "$#" -gt 0 ]; then
    MODELS=("$@")
else
    MODELS=("qwen2.5:3b")
fi

PRIMARY_MODEL="${MODELS[0]}"
BUNDLE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PKG_ROOT="${BUNDLE_DIR}/airgap_pkg"

echo "======================================================================"
echo "  Preparing Offline Air-Gap Bundle for HPE VME / Ubuntu 24.04"
echo "  Selected Models to Pre-cache: ${MODELS[*]}"
echo "  Default Primary Model:        ${PRIMARY_MODEL}"
echo "======================================================================"

# Sizing warning for large models (14B, 27B, 32B, 70B)
for m in "${MODELS[@]}"; do
    if [[ "$m" =~ (27b|32b|70b) ]]; then
        echo ""
        echo "  [NOTICE] Large model detected ($m):"
        echo "   * Download size: ~16 - 22 GB"
        echo "   * Target VM recommendation: Allocate at least 32 GB RAM on HPE VME"
        echo "   * Ensure jump server has at least 30 GB free disk space."
        echo ""
    fi
done

# Initialize packaging directory (preserve existing ollama_data if present to avoid re-downloads)
mkdir -p "${PKG_ROOT}/bin" "${PKG_ROOT}/images" "${PKG_ROOT}/ollama_data"

# 1. Download Docker static binaries (Zero .deb files needed!)
echo "==> [1/5] Downloading Docker static binaries (${DOCKER_VERSION})..."
DOCKER_TGZ_URL="https://download.docker.com/linux/static/stable/x86_64/docker-${DOCKER_VERSION}.tgz"
if [ ! -f "${PKG_ROOT}/bin/docker.tgz" ]; then
    curl -fSL "${DOCKER_TGZ_URL}" -o "${PKG_ROOT}/bin/docker.tgz"
else
    echo "    Cached docker.tgz already present."
fi

echo "==> [2/5] Downloading Docker Compose standalone binary (${COMPOSE_VERSION})..."
COMPOSE_URL="https://github.com/docker/compose/releases/download/${COMPOSE_VERSION}/docker-compose-linux-x86_64"
if [ ! -f "${PKG_ROOT}/bin/docker-compose" ]; then
    curl -fSL "${COMPOSE_URL}" -o "${PKG_ROOT}/bin/docker-compose"
    chmod +x "${PKG_ROOT}/bin/docker-compose"
else
    echo "    Cached docker-compose already present."
fi

# 2. Build and save container images
echo "==> [3/5] Building & saving onprem-analyzer container image..."
docker build -t onprem-analyzer:latest "${BUNDLE_DIR}"
docker save onprem-analyzer:latest -o "${PKG_ROOT}/images/onprem-analyzer.tar"

echo "==> [4/5] Pulling & saving ollama/ollama container image..."
docker pull ollama/ollama:latest
docker save ollama/ollama:latest -o "${PKG_ROOT}/images/ollama.tar"

# 3. Pre-cache Ollama model weights
echo "==> [5/5] Pre-caching models into offline storage (${MODELS[*]})..."

# Check if user already has models in local host ~/.ollama
if [ -d "${HOME}/.ollama/models" ] && [ ! -d "${PKG_ROOT}/ollama_data/models" ]; then
    echo "    Found existing local ~/.ollama/models cache on jump host."
    read -r -p "    Do you want to copy existing models from ~/.ollama to save bandwidth? [y/N]: " use_local || true
    if [[ "${use_local:-n}" =~ ^[Yy]$ ]]; then
        echo "    Copying local model cache..."
        cp -rn "${HOME}/.ollama/"* "${PKG_ROOT}/ollama_data/" || true
    fi
fi

TEMP_CONTAINER_NAME="airgap_model_fetcher_$$"
docker run -d --name "${TEMP_CONTAINER_NAME}" \
    -v "${PKG_ROOT}/ollama_data:/root/.ollama" \
    ollama/ollama:latest

# Wait for container to be ready
echo "    Waiting for temporary Ollama container daemon..."
for i in {1..30}; do
    if docker exec "${TEMP_CONTAINER_NAME}" ollama list >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# Pull each requested model
for model in "${MODELS[@]}"; do
    echo "----------------------------------------------------------------------"
    echo "    Pulling model: ${model}"
    echo "----------------------------------------------------------------------"
    # Stream pull progress
    if ! docker exec -t "${TEMP_CONTAINER_NAME}" ollama pull "${model}"; then
        echo "    [WARNING] Could not pull '${model}' directly. If using custom model tag,"
        echo "              ensure it exists on ollama.com or import your GGUF file."
    fi
done

echo ""
echo "    Installed models inside offline bundle:"
docker exec "${TEMP_CONTAINER_NAME}" ollama list

# Stop and remove temporary container
docker stop "${TEMP_CONTAINER_NAME}" >/dev/null 2>&1 || true
docker rm "${TEMP_CONTAINER_NAME}" >/dev/null 2>&1 || true

# 4. Copy project configs, service files, and installation script
echo "==> Copying project files and offline compose configuration..."
cp "${BUNDLE_DIR}/tools/airgap/docker.service" "${PKG_ROOT}/bin/docker.service"
cp "${BUNDLE_DIR}/tools/airgap/install_offline.sh" "${PKG_ROOT}/install_offline.sh"
chmod +x "${PKG_ROOT}/install_offline.sh"
cp "${BUNDLE_DIR}/tools/airgap/mcp_server.py" "${PKG_ROOT}/mcp_server.py"

# Configure docker-compose.offline.yml with selected primary model
sed "s/ANALYZER_AI_MODEL:-qwen2.5:3b/ANALYZER_AI_MODEL:-${PRIMARY_MODEL}/g" \
    "${BUNDLE_DIR}/docker-compose.offline.yml" > "${PKG_ROOT}/docker-compose.yml"

mkdir -p "${PKG_ROOT}/data/sessions" "${PKG_ROOT}/data/uploads" "${PKG_ROOT}/bundles"
cp -r "${BUNDLE_DIR}/rules" "${PKG_ROOT}/rules"
cp -r "${BUNDLE_DIR}/topology" "${PKG_ROOT}/topology"

# 5. Create final self-contained archive
ARCHIVE_NAME="${BUNDLE_DIR}/onprem-analyzer-offline-bundle.tar.gz"
echo "==> Packaging complete bundle into ${ARCHIVE_NAME}..."
echo "    (This may take several minutes if large 27B/32B models are included)..."
tar -czf "${ARCHIVE_NAME}" -C "${PKG_ROOT}/.." airgap_pkg

echo "======================================================================"
echo "  [SUCCESS] Air-Gap Bundle Created: ${ARCHIVE_NAME}"
echo "  Total Package Size: $(du -sh "${ARCHIVE_NAME}" | awk '{print $1}')"
echo "  Models Pre-loaded:  ${MODELS[*]}"
echo ""
echo "  To deploy to target host (10.55.58.52):"
echo "  1. scp ${ARCHIVE_NAME} user@10.55.58.52:~/"
echo "  2. ssh user@10.55.58.52"
echo "  3. tar -xzf onprem-analyzer-offline-bundle.tar.gz && cd airgap_pkg"
echo "  4. sudo ./install_offline.sh"
echo "======================================================================"
