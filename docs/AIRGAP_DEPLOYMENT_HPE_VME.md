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
*(Requires Internet access on the Jump Server - Windows, Linux, or macOS)*

Run the cross-platform Python bundler (which requires **zero Docker** on Windows):

```powershell
# On Windows PowerShell:
py tools\airgap\prepare_airgap_bundle.py qwen2.5:3b

# Or on Linux / macOS:
python3 tools/airgap/prepare_airgap_bundle.py qwen2.5:3b

# You can also bundle larger models (e.g., 27B or 32B):
py tools\airgap\prepare_airgap_bundle.py qwen2.5:3b qwen2.5:32b
```

This script will:
- Download official Docker 27.x static binaries and standalone `docker-compose`.
- Download official Linux standalone Ollama AI engine (`ollama-linux-amd64.tgz`).
- Pre-cache all requested model weights directly from `registry.ollama.ai` over HTTPS.
- Download Linux Python wheels for all dependencies (`duckdb`, `pyyaml`, `jinja2`, `rich`, etc.).
- Package everything into `onprem-analyzer-offline-bundle.tar.gz`.

---

### Step 2: Transfer to the HPE VME VM (`10.55.58.52`)

Since Ubuntu 24.04 Desktop does not install OpenSSH by default, transfer directly via local HTTP:

**On Windows Jump Server:**
```powershell
# In the directory containing onprem-analyzer-offline-bundle.tar.gz
py -m http.server 8000
```

**Inside Ubuntu VM (Morpheus HTML5 Console):**
Open **Firefox** and download:
```
http://<JUMP_SERVER_IP>:8000/onprem-analyzer-offline-bundle.tar.gz
```
Save to `/home/rohith/Downloads/`.

---

### Step 3: 1-Click Install on Ubuntu 24.04 Target VM

Open the terminal in the Ubuntu VM as root:
```bash
sudo su -
mkdir -p /home/rohith/Downloads/airgap_pkg
tar -xzf /home/rohith/Downloads/onprem-analyzer-offline-bundle.tar.gz -C /home/rohith/Downloads/airgap_pkg --strip-components=1
cd /home/rohith/Downloads/airgap_pkg

# Strip Windows line endings & run installer
tr -d '\r' < install_offline.sh > install_clean.sh
bash install_clean.sh
```

**What the installer does automatically:**
1. Extracts static Docker binaries to `/usr/local/bin/`.
2. Installs Ollama standalone engine and registers pre-cached models in `/usr/share/ollama/.ollama/`.
3. Unpacks all offline Python dependency wheels using Python's built-in `zipfile` (bypasses missing `pip`).
4. Creates and boots `ollama.service` and `onprem-analyzer.service` via systemd.
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
