# Complete Offline Air-Gap Deployment Guide: HPE VME 9.0.1 & Ubuntu 24.04

This guide prescribes the enterprise deployment architecture for running the **Disconnected Support Bundle Analyzer** and **Ollama Local AI** in a **100% air-gapped / disconnected environment** on **HPE VME 9.0.1 (Morpheus CMP)** at IP `10.55.58.52`, while completely eliminating the dependency nightmare of downloading individual `.deb` packages.

---

## 1. Architectural Overview & Network Topology

```
+-------------------------------------------------------------------------------+
|                             CONNECTED ZONE (JUMP HOST)                       |
|  - Internet Access: YES                                                       |
|  - Internal Routing to 10.55.58.52: YES                                       |
|                                                                               |
|  Executes: tools/airgap/prepare_airgap_bundle.sh                              |
|  Output:   onprem-analyzer-offline-bundle.tar.gz (~3.5 GB with model)         |
+-------------------------------------------------------------------------------+
                                      |
                             SCP / Direct Transfer
                                      |
                                      v
+-------------------------------------------------------------------------------+
|                   AIR-GAPPED ZONE (HPE VME 9.0.1 / MORPHEUS)                  |
|                                                                               |
|  Ubuntu 24.04 LTS VM (IP: 10.55.58.52)                                        |
|  - Internet Access: NONE (Air-Gapped)                                         |
|  - Static Docker Engine (Zero .deb files, no apt required)                     |
|                                                                               |
|  +-------------------------------------------------------------------------+  |
|  | Docker Compose Stack (docker-compose.offline.yml)                       |  |
|  |                                                                         |  |
|  |   [onprem-analyzer:latest] (Port 8080)                                  |  |
|  |    * Web Dashboard & Fast DuckDB Engine                                 |  |
|  |    * Autonomous Multi-Agent Triage Pipeline                             |  |
|  |    * Drop folder for 10-30+ GB bundle tarballs                          |  |
|  |                                                                         |  |
|  |   [ollama/ollama:latest] (Port 11434)                                   |  |
|  |    * Pre-loaded Model Weights (./ollama_data)                           |  |
|  |    * Exposes local LLM REST API                                         |  |
|  +-------------------------------------------------------------------------+  |
+-------------------------------------------------------------------------------+
                                      ^
                                      | HTTP REST / JSON-RPC (MCP)
                                      |
+-------------------------------------------------------------------------------+
|                      HPE VME / MORPHEUS UI (TOOLS > AI SERVICES)              |
|  - LLM Provider: http://10.55.58.52:11434                                     |
|  - Diagnostic MCP Tool: tools/airgap/mcp_server.py                            |
|  - Morpheus Automation Tasks & Air-Gap Cluster Triage                         |
+-------------------------------------------------------------------------------+
```

---

## 2. Why Static Binaries & Containers Beat `.deb` Packages

Attempting to install Docker via loose `.deb` packages on an air-gapped Ubuntu machine inevitably triggers **dependency cascades** (`docker-ce` requires `containerd.io`, `docker-ce-cli`, `libseccomp2`, `iptables`, `pigz`, `apparmor`, etc.). Furthermore, the standard `ollama` install script calls external URLs to download binaries and models, which fails immediately without internet.

### The Solution:
1. **Docker Static Binaries**: Official `docker-27.x.tgz` contains statically linked binaries (`dockerd`, `docker`, `containerd`, `runc`). Extracting them to `/usr/local/bin` installs Docker in 5 seconds without touching `apt` or downloading a single `.deb` file.
2. **Containerized Ollama**: Ollama runs directly inside Docker Compose (`ollama/ollama:latest`), completely eliminating any host-level Ollama installation.
3. **Pre-Cached Model Weights**: The model weights (`qwen2.5:3b` or `llama3.2`) are pulled on the Jump Server directly into `./ollama_data/` and packaged into the tarball. When unpacked on the target VM, the AI is 100% operational immediately upon startup.

---

## 3. Step-by-Step Deployment Instructions

### Model Sizing & VM Hardware Allocation on HPE VME

| Model Class | Download Size | Minimum RAM | Recommended vCPUs | Best Use Case |
|---|---|---|---|---|
| **Small (e.g. `qwen2.5:3b`)** | ~2.0 GB | 8 GB | 4 - 8 vCPUs | Rapid real-time triage, low resource footprint |
| **Medium (e.g. `qwen2.5:14b`)** | ~9.0 GB | 16 - 20 GB | 8 - 12 vCPUs | Balanced reasoning, deep log analysis |
| **Large (e.g. `qwen3.8:27b` / `qwen2.5:32b`)** | ~18 - 20 GB | 32 GB+ | 12 - 16 vCPUs | Complex root cause attribution, deep multi-system correlation |

---

### Step 1: Prepare the Air-Gap Bundle on the Jump Server
*(Requires Internet access on the Jump Server)*

You can package **a single model** or **multiple models simultaneously** (e.g. a fast 3B model + a high-accuracy 27B/32B model so users can switch between them in the UI dropdown):

```bash
cd /Users/rohithr/Desktop/onpremParser/onprem-analyzer
chmod +x tools/airgap/*.sh

# Option A: Bundle a larger 27B model
./tools/airgap/prepare_airgap_bundle.sh qwen3.8:27b

# Option B: Bundle both a fast 3B model AND a 27B/32B model in one package:
./tools/airgap/prepare_airgap_bundle.sh qwen2.5:3b qwen3.8:27b qwen2.5:32b
```

This script will:
- Check for existing models in `~/.ollama` (prompting to copy rather than re-downloading 18GB).
- Download official Docker 27.x static binaries and standalone `docker-compose`.
- Build and export `onprem-analyzer:latest` and `ollama/ollama:latest` images.
- Pre-cache all requested model weights directly into `ollama_data/`.
- Package everything into `onprem-analyzer-offline-bundle.tar.gz`.

---

### Step 2: Transfer to the HPE VME VM (`10.55.58.52`)

Since the Jump Server can route to `10.55.58.52` over your management network:
```bash
scp onprem-analyzer-offline-bundle.tar.gz <username>@10.55.58.52:~/
```

---

### Step 3: 1-Click Install on Ubuntu 24.04 Target VM

SSH into the target VM and execute:
```bash
ssh <username>@10.55.58.52
tar -xzf onprem-analyzer-offline-bundle.tar.gz
cd airgap_pkg

# Run the automated installer (installs Docker, loads images, starts stack)
sudo ./install_offline.sh
```

**What the installer does in under 60 seconds:**
1. Extracts static Docker binaries to `/usr/local/bin/`.
2. Registers and starts `docker.service` with systemd.
3. Runs `docker load` for the Analyzer and Ollama images.
4. Boots `docker-compose.yml` with pre-mounted model weights.
5. Runs local health checks on ports `8080` and `11434`.

---

## 4. Verification & Endpoint Access

Once installed, verify the services from the VM or your jump workstation:

| Service | Address | Description |
|---|---|---|
| **Analyzer Web UI** | `http://10.55.58.52:8080` | Air-gapped visual log triage dashboard |
| **Analyzer API** | `http://10.55.58.52:8080/api/health` | REST API for status and telemetry queries |
| **Ollama LLM API** | `http://10.55.58.52:11434/api/tags` | Local AI inference engine |

Test connectivity from your jump server or any authorized workstation:
```bash
# Check Analyzer Health
curl -s http://10.55.58.52:8080/api/health

# Check Preloaded Models in Ollama
curl -s http://10.55.58.52:11434/api/tags
```

---

## 5. Integrating with HPE VME 9.0.1 (Tools > AI Services & MCP)

HPE VME 9.0.1 (powered by Morpheus CMP) includes integrations under **Tools > AI Services** to connect external intelligence, agents, and MCP servers.

### 5.1 Registering Ollama in HPE VME AI Services
1. Log into your **HPE VME / Morpheus UI**.
2. Navigate to **Tools > AI Services** (or **Administration > Integrations > AI Engines** depending on role permissions).
3. Click **+ Add AI Service / Integration**:
   - **Service Name**: `HPE-Onprem-Offline-LLM`
   - **Provider / Type**: `Ollama` (or `OpenAI Compatible`)
   - **API Endpoint URL**: `http://10.55.58.52:11434`
   - **Model**: `qwen2.5:3b`
   - **API Key**: Leave blank (local air-gap mode requires no key)
4. Click **Save & Test Connection**. VME will ping `http://10.55.58.52:11434/api/tags` and register the model.

### 5.2 Connecting the Model Context Protocol (MCP) Server
An enterprise MCP Server script is provided in `airgap_pkg/mcp_server.py`. It bridges VME's AI reasoning engine directly into the support bundle's DuckDB data:

- **Exposed MCP Tools**:
  - `list_sessions`: Enumerates all ingested bundle sessions.
  - `get_triage_report`: Retrieves root-cause analysis, blast radius, and Patient Zero attribution.
  - `query_critical_events`: Runs high-speed filtered queries across error logs.
  - `get_k8s_anomalies`: Inspects CrashLoopBackOff, OOMKilled, and restart spikes.

To connect this in HPE VME under **Tools > AI Services > MCP Servers**:
1. Select **Add MCP Server**.
2. Set Transport: **Command / Stdio** or **SSE Proxy**.
   - Executable: `python3 /home/<user>/airgap_pkg/mcp_server.py`
3. Save. HPE VME agents can now autonomously query the cluster support bundles as first-class diagnostic tools.

---

## 6. Daily Operations & Support Bundle Workflow

1. **Ingesting a Support Bundle**:
   Drop any large tarball (`.tar`, `.tar.gz`) directly into the shared drop folder:
   ```bash
   cp /path/to/support_bundle.tar /home/<user>/airgap_pkg/bundles/
   ```
2. **Accessing Diagnostics**:
   Navigate to `http://10.55.58.52:8080` in your browser. The bundle will appear immediately in the session list ready for 1-click parsing and autonomous agentic triage.
3. **Stopping / Restarting the Services**:
   ```bash
   cd /home/<user>/airgap_pkg
   docker-compose down
   docker-compose up -d
   ```
