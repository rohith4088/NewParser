#!/usr/bin/env python3
"""Cross-platform Air-Gap Bundle Creator (Windows, macOS, Linux).

Zero-Docker Required:
Downloads Linux Docker binaries, Ollama standalone engine, pre-cached model weights,
and offline Python wheels directly over HTTPS.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

# Corporate proxy SSL bypass
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except Exception:
    pass

DOCKER_VERSION = "27.3.1"
COMPOSE_VERSION = "v2.29.7"
OLLAMA_VERSION = "v0.5.1"

DOCKER_URL = f"https://download.docker.com/linux/static/stable/x86_64/docker-{DOCKER_VERSION}.tgz"
COMPOSE_URL = f"https://github.com/docker/compose/releases/download/{COMPOSE_VERSION}/docker-compose-linux-x86_64"
OLLAMA_URL = f"https://github.com/ollama/ollama/releases/download/{OLLAMA_VERSION}/ollama-linux-amd64.tgz"


def log(msg: str) -> None:
    print(f"[*] {msg}", flush=True)


def download_file(url: str, dest_path: Path) -> None:
    """Download a file with progress logging, SSL bypass, and curl fallback."""
    if dest_path.exists() and dest_path.stat().st_size > 0:
        log(f"Cached file exists: {dest_path.name}")
        return

    log(f"Downloading: {url}")
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Try Python urllib with unverified SSL context
    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=120.0) as resp, open(dest_path, "wb") as out_f:
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
        log(f"urllib notice ({e}). Trying Windows curl fallback...")

    # 2. Fallback to native Windows curl.exe with -k
    try:
        curl_cmd = ["curl", "-k", "-fSL", url, "-o", str(dest_path)]
        subprocess.run(curl_cmd, check=True)
        log(f"Saved via curl: {dest_path.name} ({dest_path.stat().st_size / (1024*1024):.1f} MB)")
    except Exception as curl_err:
        raise RuntimeError(f"Failed to download {url}: {curl_err}")


def download_ollama_model(model_tag: str, dest_dir: Path) -> None:
    """Directly fetch Ollama model manifest & blobs from registry.ollama.ai via HTTPS (No Docker needed)."""
    tag = "latest"
    raw = model_tag
    if ":" in raw:
        raw, tag = raw.split(":", 1)
    if "/" in raw:
        ns, name = raw.split("/", 1)
    else:
        ns, name = "library", raw

    log(f"Fetching Ollama registry manifest for {model_tag}...")
    manifest_url = f"https://registry.ollama.ai/v2/{ns}/{name}/manifests/{tag}"

    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(manifest_url, headers={"User-Agent": "ollama"})
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=30.0) as r:
            manifest = json.loads(r.read())
    except Exception as e:
        log(f"[WARNING] Could not fetch manifest for '{model_tag}' from registry.ollama.ai: {e}")
        return

    # Write manifest file
    manifest_file = dest_dir / "models" / "manifests" / "registry.ollama.ai" / ns / name / tag
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")

    blobs_dir = dest_dir / "models" / "blobs"
    blobs_dir.mkdir(parents=True, exist_ok=True)

    # Collect all blob digests (config + all layers)
    items_to_download = [manifest["config"]] + manifest.get("layers", [])
    total_mb = sum(item.get("size", 0) for item in items_to_download) / (1024 * 1024)
    log(f"Model '{model_tag}' total size: {total_mb:.1f} MB ({total_mb/1024:.2f} GB). Downloading layers...")

    for item in items_to_download:
        digest = item.get("digest")
        if not digest or not digest.startswith("sha256:"):
            continue
        blob_filename = f"sha256-{digest.split(':', 1)[1]}"
        blob_path = blobs_dir / blob_filename
        expected_size = item.get("size", 0)

        if blob_path.exists() and blob_path.stat().st_size == expected_size:
            log(f"Blob cached: {blob_filename} ({expected_size / (1024*1024):.1f} MB)")
            continue

        blob_url = f"https://registry.ollama.ai/v2/{ns}/{name}/blobs/{digest}"
        download_file(blob_url, blob_path)

    log(f"Model '{model_tag}' successfully cached.")


def download_python_wheels(pkg_root: Path) -> None:
    """Download offline Python 3.12 Linux wheels for all dependencies."""
    wheels_dir = pkg_root / "wheels"
    wheels_dir.mkdir(parents=True, exist_ok=True)
    log("Downloading Linux Python 3.12 wheels for offline installation...")

    pkgs = ["duckdb", "pyyaml", "jinja2", "orjson", "rich"]
    cmd = [
        sys.executable, "-m", "pip", "download",
        "--dest", str(wheels_dir),
        "--platform", "manylinux2014_x86_64",
        "--only-binary=:all:",
        "--python-version", "312",
        *pkgs,
    ]
    try:
        subprocess.run(cmd, check=True)
        log(f"Downloaded {len(list(wheels_dir.glob('*.whl')))} wheels to {wheels_dir.name}")
    except Exception as e:
        log(f"[WARNING] pip download encountered: {e}. Will fall back to standard package installation.")


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
    print("  Creating Air-Gap Deployment Bundle (Zero Docker Required on Windows)")
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

    # 3. Download standalone Linux Ollama binary
    log("[2/5] Downloading Linux Ollama standalone engine...")
    download_file(OLLAMA_URL, bin_dir / "ollama-linux-amd64.tgz")

    # 4. Pre-cache model weights directly via HTTPS
    log(f"[3/5] Pre-caching model weights into offline bundle ({', '.join(models)})...")
    for m in models:
        download_ollama_model(m, ollama_dir)

    # 5. Download Python wheels for offline execution on Ubuntu
    log("[4/5] Downloading offline Python dependencies for Ubuntu 24.04...")
    download_python_wheels(pkg_root)

    # 6. Copy configuration and scripts
    log("[5/5] Copying deployment scripts and configuration...")
    shutil.copy2(project_root / "tools" / "airgap" / "docker.service", bin_dir / "docker.service")
    shutil.copy2(project_root / "tools" / "airgap" / "install_offline.sh", pkg_root / "install_offline.sh")
    shutil.copy2(project_root / "tools" / "airgap" / "mcp_server.py", pkg_root / "mcp_server.py")

    # Copy rules, topology, analyzer, and setup pyproject
    shutil.copytree(project_root / "rules", pkg_root / "rules", dirs_exist_ok=True)
    shutil.copytree(project_root / "topology", pkg_root / "topology", dirs_exist_ok=True)
    shutil.copytree(project_root / "analyzer", pkg_root / "analyzer", dirs_exist_ok=True)
    shutil.copy2(project_root / "pyproject.toml", pkg_root / "pyproject.toml")
    shutil.copy2(project_root / "docker-compose.offline.yml", pkg_root / "docker-compose.yml")

    (pkg_root / "data" / "sessions").mkdir(parents=True, exist_ok=True)
    (pkg_root / "data" / "uploads").mkdir(parents=True, exist_ok=True)
    (pkg_root / "bundles").mkdir(parents=True, exist_ok=True)

    # 7. Create archive using Python tarfile (native on Windows)
    archive_path = project_root / "onprem-analyzer-offline-bundle.tar.gz"
    log(f"Creating final archive: {archive_path.name}...")

    with tarfile.open(str(archive_path), "w:gz") as tar:
        tar.add(str(pkg_root), arcname="airgap_pkg")

    size_mb = archive_path.stat().st_size / (1024 * 1024)
    print("=" * 70)
    print(f"  [SUCCESS] Offline Bundle Created: {archive_path}")
    print(f"  Total Size: {size_mb:.1f} MB ({size_mb/1024:.2f} GB)")
    print(f"  Models Packaged: {', '.join(models)}")
    print("=" * 70)


if __name__ == "__main__":
    main()
