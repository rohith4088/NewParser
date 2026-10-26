# Air-Gap Deployment Runbook
## On-Prem Support Bundle Analyzer & Ollama Local AI on HPE VME / Ubuntu 24.04 LTS

**Target Host:** Ubuntu 24.04 LTS (Air-Gapped / Disconnected, IP: `10.55.58.52`)  
**Jump Server:** Windows 10/11 or Linux/macOS (Connected to Internet & Internal VME Network)  
**Access Method:** Morpheus HTML5 Remote Desktop Console & Web Browser (Zero SSH Required)  
**Dependencies:** Zero external `.deb` packages, Zero Docker on Windows, Zero `pip` on Ubuntu.

---

## 1. Executive Summary & Architecture

This runbook documents the exact, field-tested procedure to package, transfer, deploy, and verify the **Support Bundle Analyzer** and **Ollama Local AI** into an air-gapped environment.

```
+-----------------------------------------------------------------------------------+
|                        1. WINDOWS JUMP SERVER (Connected)                        |
|  - Runs: py tools\airgap\prepare_airgap_bundle.py <model>                         |
|  - Downloads over HTTPS: Docker static binaries, Linux Ollama engine,             |
|    model weights (qwen2.5:3b / 27b / 32b), Python wheels                          |
|  - Output: onprem-analyzer-offline-bundle.tar.gz (Self-contained archive)         |
|  - Hosts transfer: py -m http.server 8000                                         |
+-----------------------------------------------------------------------------------+
                                         │
                   HTTP Transfer via Web Browser (No SSH)
                                         ▼
+-----------------------------------------------------------------------------------+
|                     2. AIR-GAPPED UBUNTU VM (10.55.58.52)                         |
|  - Firefox downloads bundle from http://<jump-server-ip>:8000                     |
|  - Extracted to /home/rohith/Downloads/airgap_pkg                                 |
|  - Clean Windows CRLF: tr -d '\r' < install_offline.sh > install_clean.sh         |
|  - Installs:                                                                      |
|      * Docker static engine (/usr/local/bin)                                      |
|      * Ollama Standalone Engine + pre-loaded models (/usr/share/ollama)           |
|      * Python dependencies unpacked via built-in zipfile (zero pip needed)        |
|      * Systemd services: ollama.service & onprem-analyzer.service                 |
+-----------------------------------------------------------------------------------+
                                         │
                    Exposed Services (Zero Internet Needed)
                                         ▼
+-----------------------------------------------------------------------------------+
|  - Web UI Dashboard:      http://10.55.58.52:8080                                 |
|  - Local Ollama AI API:   http://10.55.58.52:11434                                |
|  - HPE VME AI Services:   Integrates with Morpheus CMP as Ollama/OpenAI endpoint  |
+-----------------------------------------------------------------------------------+
```

---

## 2. Prerequisites & Hardware Planning

### Target VM Sizing (Ubuntu 24.04 LTS on HPE VME)
| Model Size | Model Name | Min RAM | Recommended vCPUs | Primary Use Case |
|---|---|---|---|---|
| **Small** | `qwen2.5:3b` | 8 GB | 4 - 8 vCPUs | Fast real-time triage, low memory footprint |
| **Medium** | `qwen2.5:14b` | 16 - 20 GB | 8 - 12 vCPUs | Balanced reasoning, deep multi-log parsing |
| **Large** | `qwen2.5:32b` or `qwen3.8:27b` | 32 GB - 64 GB | 16 - 32 vCPUs | Deep root cause analysis, complex correlations |

---

## 3. Phase 1: Build the Offline Bundle on the Jump Server

Perform this step on the **Windows Jump Server** (or any machine with Internet access).

### Step 1.1: Clone / Open Repository
Open PowerShell or Command Prompt in the repository root:
```powershell
cd C:\Users\<Username>\Desktop\NewParser
```

### Step 1.2: Run the Offline Packager
Specify the model(s) you wish to pre-cache (default is `qwen2.5:3b`):

```powershell
# Default (3B Model):
py tools\airgap\prepare_airgap_bundle.py qwen2.5:3b

# Or package multiple models (e.g., fast 3B + high-capacity 32B):
py tools\airgap\prepare_airgap_bundle.py qwen2.5:3b qwen2.5:32b
```

**What the script does automatically:**
1. Downloads official Linux Docker static binaries (`docker-27.x.tgz`) and `docker-compose`.
2. Downloads official standalone Linux Ollama engine (`ollama-linux-amd64.tgz`).
3. Downloads model weight layers directly from `registry.ollama.ai` over HTTPS.
4. Downloads Linux Python wheels (`duckdb`, `pyyaml`, `jinja2`, `orjson`, `rich`, etc.).
5. Creates a single compressed archive:
   `onprem-analyzer-offline-bundle.tar.gz`

---

## 4. Phase 2: Transfer Bundle to the Air-Gapped VM

Because Ubuntu 24.04 Desktop does not have `openssh-server` installed by default, transfer via a temporary local web server:

### Step 2.1: Start Local HTTP Server on Windows Jump Machine
In PowerShell on the Windows Jump Server:
```powershell
cd C:\Users\<Username>\Desktop\NewParser
py -m http.server 8000
```
*(Leave this window open during download)*.

### Step 2.2: Download inside the Air-Gapped Ubuntu VM
1. Open the **HPE VME / Morpheus HTML5 Console** to access the Ubuntu Desktop.
2. Open **Firefox** inside the Ubuntu VM.
3. In the address bar, type:
   ```
   http://<WINDOWS_JUMP_SERVER_IP>:8000/onprem-analyzer-offline-bundle.tar.gz
   ```
4. Save the file to `/home/rohith/Downloads/`.

---

## 5. Phase 3: Extraction & 1-Click Installation on Ubuntu

Open the **Terminal** in the Ubuntu VM and switch to root:

```bash
sudo su -
```

### Step 5.1: Extract the Bundle
```bash
mkdir -p /home/rohith/Downloads/airgap_pkg
tar -xzf /home/rohith/Downloads/onprem-analyzer-offline-bundle.tar.gz -C /home/rohith/Downloads/airgap_pkg --strip-components=1
cd /home/rohith/Downloads/airgap_pkg
```

### Step 5.2: Strip Windows CRLF Line Endings & Run Installer
Because the file was created on Windows, strip carriage return characters:

```bash
tr -d '\r' < install_offline.sh > install_clean.sh
bash install_clean.sh
```

---

## 6. What the Installer Configures Automatically

The installer operates in four automated stages with **zero external dependencies**:

1. **Static Docker Engine:** Extracts static binaries to `/usr/local/bin` and starts `docker.service`.
2. **Ollama Standalone Engine:** Installs `/usr/local/bin/ollama`, registers pre-cached models in `/usr/share/ollama/.ollama/`, and enables `ollama.service` (listening on `0.0.0.0:11434`).
3. **Pure-Python Dependency Unpacking:**
   - Automatically uses Python's built-in `zipfile` module to unpack all offline wheels directly into `/usr/local/lib/python3.12/dist-packages/`.
   - Bypasses missing `pip` completely.
   - Copies `analyzer` package into global site-packages.
4. **On-Prem Analyzer Service:** Creates and starts systemd service `/etc/systemd/system/onprem-analyzer.service` listening on `0.0.0.0:8080`.
5. **Urllib Health Checks:** Performs automated verification using Python's standard `urllib` (no `curl` required).

---

## 7. Phase 4: Verification & Usage

### 7.1 Verify Services
Check status of both background services:
```bash
systemctl status ollama --no-pager
systemctl status onprem-analyzer --no-pager
```

Verify health endpoints using Python:
```bash
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8080/api/health').read().decode())"
python3 -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:11434/api/tags').read().decode())"
```

### 7.2 Access Endpoints
- **Web UI Dashboard:**
  ```
  http://10.55.58.52:8080
  ```
- **Local Ollama REST API:**
  ```
  http://10.55.58.52:11434
  ```

### 7.3 Analyzing Support Bundles
1. **Via Web Dashboard:**
   Open `http://10.55.58.52:8080` in your Windows browser and drag-and-drop any `.tar`, `.tar.gz`, or `.zip` bundle.
2. **Via Drop Folder (Recommended for 10GB - 50GB bundles):**
   Copy the bundle directly into:
   `/home/rohith/Downloads/airgap_pkg/bundles/`
   The dashboard will automatically discover it for one-click ingestion.

---

## 8. HPE VME / Morpheus CMP AI Integration

To integrate the local offline AI with HPE VME AI Services:

1. In Morpheus CMP, navigate to **Administration > Integrations > AI Services** (or **Tools > AI Services**).
2. Add a new AI Service:
   - **Service Name:** `On-Prem Airgap LLM (Qwen)`
   - **Service Type:** `Ollama` or `OpenAI Compatible`
   - **Base URL:** `http://10.55.58.52:11434`
   - **Model Name:** `qwen2.5:3b` (or `qwen2.5:32b`)
3. Use the integrated Model Context Protocol (MCP) server for automated troubleshooting tasks:
   ```bash
   /usr/bin/python3 /home/rohith/Downloads/airgap_pkg/mcp_server.py
   ```

---

## 9. Troubleshooting & FAQ

### Issue: `sed: unknown option to 's'` or `./install_offline.sh: command not found`
- **Cause:** Windows line endings (`\r\n`) or shell syntax typo.
- **Fix:** Always run `tr -d '\r' < install_offline.sh > install_clean.sh && bash install_clean.sh`.

### Issue: `/usr/bin/python3: No module named pip`
- **Cause:** Ubuntu 24.04 desktop does not ship with `python3-pip`.
- **Fix:** Do not use `pip`. Run the built-in zipfile unloader:
  ```bash
  python3 -c "
  import glob, zipfile; from pathlib import Path
  target = Path('/usr/local/lib/python3.12/dist-packages'); target.mkdir(parents=True, exist_ok=True)
  for w in glob.glob('/home/rohith/Downloads/airgap_pkg/wheels/*.whl'):
      with zipfile.ZipFile(w) as z: z.extractall(target)
  "
  cp -r /home/rohith/Downloads/airgap_pkg/analyzer /usr/local/lib/python3.12/dist-packages/
  systemctl restart onprem-analyzer
  ```

### Issue: Firefox shows "Unable to connect" to port 8080
- **Cause:** Linux firewall (`ufw`) blocking inbound connections.
- **Fix:**
  ```bash
  ufw allow 8080/tcp
  ufw allow 11434/tcp
  ```

### Issue: Inspecting Logs
- **Analyzer Logs:**
  ```bash
  journalctl -u onprem-analyzer -f
  ```
- **Ollama Logs:**
  ```bash
  journalctl -u ollama -f
  ```
