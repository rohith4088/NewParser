#!/usr/bin/env python3
"""Cross-platform Air-Gap Bundle Creator (Windows, macOS, Linux).

Packages Docker static binaries, container images, and Ollama model weights
into a single offline archive: onprem-analyzer-offline-bundle.tar.gz.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

DOCKER_VERSION = "27.3.1"
COMPOSE_VERSION = "v2.29.7"

DOCKER_URL = f"https://download.docker.com/linux/static/stable/x86_64/docker-{DOCKER_VERSION}.tgz"
COMPOSE_URL = f"https://github.com/docker/compose/releases/download/{COMPOSE_VERSION}/docker-compose-linux-x86_64"


import ssl

# Bypass enterprise corporate proxy SSL certificate verification
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass


def log(msg: str) -> None:
    print(f"[*] {msg}", flush=True)


def download_file(url: str, dest_path: Path) -> None:
    """Download a file with progress logging, SSL bypass, and curl fallback."""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        log(f"Cached file exists: {dest_path.name}")
        return

    log(f"Downloading: {url}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Try Python urllib with unverified SSL context (for corporate proxies)
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, context=ctx, timeout=60.0) as resp, open(dest_path, "wb") as out_f:
            total_size = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            block_size = 1024 * 1024  # 1MB chunks
            while True:
                chunk = resp.read(block_size)
                if not chunk:
                    break
                out_f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = min(100.0, downloaded * 100.0 / total_size)
                    mb = downloaded / (1024 * 1024)
                    tot_mb = total_size / (1024 * 1024)
                    sys.stdout.write(f"\r    Downloading {dest_path.name}: {pct:.1f}% ({mb:.1f}MB / {tot_mb:.1f}MB)")
                    sys.stdout.flush()
        print()
        log(f"Saved: {dest_path.name} ({dest_path.stat().st_size / (1024*1024):.1f} MB)")
        return
    except Exception as e:
        log(f"urllib download notice ({e}). Attempting Windows curl fallback...")

    # 2. Fallback to native Windows curl.exe with -k (bypasses SSL inspection)
    try:
        curl_cmd = ["curl", "-k", "-fSL", url, "-o", str(dest_path)]
        subprocess.run(curl_cmd, check=True)
        log(f"Saved via curl: {dest_path.name} ({dest_path.stat().st_size / (1024*1024):.1f} MB)")
    except Exception as curl_err:
        raise RuntimeError(f"Failed to download {url} via both Python and curl: {curl_err}")


def run_cmd(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    log(f"Running: {' '.join(cmd)}")
    return subprocess.run(cmd, check=check)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build offline air-gap deployment bundle.")
    parser.add_argument("models", nargs="*", default=["qwen2.5:3b"], help="Ollama model names to pre-cache")
    args = parser.parse_args()

    models = args.models
    primary_model = models[0]

    project_root = Path(__file__).resolve().parents[2]
    pkg_root = project_root / "airgap_pkg"
    bin_dir = pkg_root / "bin"
    img_dir = pkg_root / "images"
    ollama_dir = pkg_root / "ollama_data"

    print("=" * 70)
    print("  Creating Air-Gap Deployment Bundle (Windows / Linux / macOS)")
    print(f"  Target Models: {', '.join(models)}")
    print(f"  Primary Model: {primary_model}")
    print("=" * 70)

    # 1. Prepare directories
    bin_dir.mkdir(parents=True, exist_ok=True)
    img_dir.mkdir(parents=True, exist_ok=True)
    ollama_dir.mkdir(parents=True, exist_ok=True)

    # 2. Download Linux static binaries
    log("[1/5] Downloading Linux Docker static binaries & Docker Compose...")
    download_file(DOCKER_URL, bin_dir / "docker.tgz")
    download_file(COMPOSE_URL, bin_dir / "docker-compose")

    # 3. Docker build and save
    log("[2/5] Building & saving onprem-analyzer container image...")
    run_cmd(["docker", "build", "-t", "onprem-analyzer:latest", str(project_root)])
    analyzer_tar = img_dir / "onprem-analyzer.tar"
    log(f"Saving onprem-analyzer:latest to {analyzer_tar.name}...")
    run_cmd(["docker", "save", "onprem-analyzer:latest", "-o", str(analyzer_tar)])

    log("[3/5] Pulling & saving ollama/ollama container image...")
    run_cmd(["docker", "pull", "ollama/ollama:latest"])
    ollama_tar = img_dir / "ollama.tar"
    log(f"Saving ollama/ollama:latest to {ollama_tar.name}...")
    run_cmd(["docker", "save", "ollama/ollama:latest", "-o", str(ollama_tar)])

    # 4. Pre-cache model weights
    log(f"[4/5] Pre-caching model weights into offline bundle ({', '.join(models)})...")
    temp_container = f"airgap_fetcher_{int(time.time())}"
    run_cmd([
        "docker", "run", "-d",
        "--name", temp_container,
        "-v", f"{ollama_dir.resolve()}:/root/.ollama",
        "ollama/ollama:latest",
    ])

    try:
        # Wait for daemon
        log("Waiting for temporary Ollama container...")
        for _ in range(30):
            res = subprocess.run(["docker", "exec", temp_container, "ollama", "list"], capture_output=True)
            if res.returncode == 0:
                break
            time.sleep(1)

        for m in models:
            log(f"Pulling model: {m} (this may take several minutes for 14B/27B/32B models)...")
            run_cmd(["docker", "exec", temp_container, "ollama", "pull", m], check=False)

        log("Models successfully pre-cached inside offline storage:")
        run_cmd(["docker", "exec", temp_container, "ollama", "list"])
    finally:
        subprocess.run(["docker", "stop", temp_container], capture_output=True)
        subprocess.run(["docker", "rm", temp_container], capture_output=True)

    # 5. Copy configuration and scripts
    log("[5/5] Copying deployment scripts and configuration...")
    shutil.copy2(project_root / "tools" / "airgap" / "docker.service", bin_dir / "docker.service")
    shutil.copy2(project_root / "tools" / "airgap" / "install_offline.sh", pkg_root / "install_offline.sh")
    shutil.copy2(project_root / "tools" / "airgap" / "mcp_server.py", pkg_root / "mcp_server.py")

    # Read offline docker-compose and inject primary model
    compose_offline_src = project_root / "docker-compose.offline.yml"
    compose_text = compose_offline_src.read_text(encoding="utf-8")
    compose_text = compose_text.replace("ANALYZER_AI_MODEL:-qwen2.5:3b", f"ANALYZER_AI_MODEL:-{primary_model}")
    (pkg_root / "docker-compose.yml").write_text(compose_text, encoding="utf-8")

    # Copy rules and topology
    shutil.copytree(project_root / "rules", pkg_root / "rules", dirs_exist_ok=True)
    shutil.copytree(project_root / "topology", pkg_root / "topology", dirs_exist_ok=True)
    (pkg_root / "data" / "sessions").mkdir(parents=True, exist_ok=True)
    (pkg_root / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    (pkg_root / "bundles").mkdir(parents=True, exist_ok=True)

    # 6. Create archive using Python tarfile (native on Windows)
    archive_path = project_root / "onprem-analyzer-offline-bundle.tar.gz"
    log(f"Creating final archive: {archive_path.name} (packaging {pkg_root.name})...")

    with tarfile.open(str(archive_path), "w:gz") as tar:
        tar.add(str(pkg_root), arcname="airgap_pkg")

    size_mb = archive_path.stat().st_size / (1024 * 1024)
    print("=" * 70)
    print(f"  [SUCCESS] Offline Bundle Created: {archive_path}")
    print(f"  Size: {size_mb:.1f} MB ({size_mb/1024:.2f} GB)")
    print(f"  Models Packaged: {', '.join(models)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
