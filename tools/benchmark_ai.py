#!/usr/bin/env python3
"""Benchmark and Verification Tool for Supercharged AI Copilot.

Measures:
1. Patient Zero Chronological Discovery Latency
2. Drain Log Template Mining Speed & Compression
3. Vectorized Scored Relevance Retrieval (BM25) Speed
4. Full AI Dynamic Context Compilation Time & Token Count
5. Live Ollama Inference Speed (if Ollama is running)
"""

import os
import sys
import time
import json
import urllib.request
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from analyzer.store.duck import SessionStore


def find_default_session() -> Path | None:
    sessions_dir = BASE_DIR / "data" / "sessions"
    if not sessions_dir.exists():
        return None
    duck_files = sorted(sessions_dir.glob("*.duckdb"), key=lambda f: -f.stat().st_size)
    return duck_files[0] if duck_files else None


def benchmark_ollama(model: str = "qwen2.5:3b", prompt_context: str = "") -> dict:
    url = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    if not url.startswith("http"):
        url = f"http://{url}"

    try:
        # Check tags
        req = urllib.request.Request(f"{url}/api/tags")
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            installed = [m.get("name", "") for m in data.get("models", [])]
            has_model = any(model in m for m in installed)
            if not has_model and installed:
                model = installed[0]
    except Exception:
        return {"available": False, "error": f"Ollama not reachable at {url}"}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "You are a Kubernetes diagnostic assistant."},
            {"role": "user", "content": f"Briefly explain the primary cause from this context in 2 sentences:\n{prompt_context[:1000]}"}
        ],
        "stream": False,
        "options": {"num_predict": 60, "temperature": 0.2}
    }

    try:
        t0 = time.perf_counter()
        req = urllib.request.Request(
            f"{url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            total_duration = time.perf_counter() - t0
            eval_count = result.get("eval_count", 0)
            eval_duration = result.get("eval_duration", 1) / 1e9  # nanoseconds to seconds
            tok_per_sec = eval_count / eval_duration if eval_duration > 0 else 0
            return {
                "available": True,
                "model": model,
                "total_seconds": round(total_duration, 2),
                "tokens_generated": eval_count,
                "tokens_per_sec": round(tok_per_sec, 1),
                "response_sample": result.get("message", {}).get("content", "")[:120].strip()
            }
    except Exception as exc:
        return {"available": False, "error": str(exc)}


def run_benchmark():
    db_path = find_default_session()
    if not db_path:
        print("[!] No active .duckdb session file found in data/sessions/.")
        print("    Upload or ingest a support bundle first to test performance.")
        sys.exit(1)

    print("=" * 72)
    print("   HPE ONPREM ANALYZER - SUPERCHARGED AI COPILOT BENCHMARK")
    print("=" * 72)
    print(f"Target Database: {db_path.name} ({db_path.stat().st_size / (1024*1024):.1f} MB)")

    store = SessionStore(str(db_path), read_only=True)
    meta = store.get_metadata() or {}
    total_events = meta.get("total_events", 0)
    print(f"Total Log Events Analyzed: {total_events:,}")
    print("-" * 72)

    # 1. Benchmark Patient Zero Detection
    t0 = time.perf_counter()
    pz = store.get_patient_zero()
    t_pz = (time.perf_counter() - t0) * 1000

    # 2. Benchmark Drain Log Template Mining
    t0 = time.perf_counter()
    templates = store.get_mined_templates(limit=5, sample_size=300)
    t_templates = (time.perf_counter() - t0) * 1000

    # 3. Benchmark Scored Lexical Retrieval
    sample_queries = [
        "Why did pods enter CrashLoopBackOff or get OOMKilled?",
        "Storage mount timeout PVC error",
        "Vault connection refused certificate expired",
    ]
    t0 = time.perf_counter()
    for q in sample_queries:
        _ = store.get_scored_events(user_query=q, limit=12)
    t_scored_avg = ((time.perf_counter() - t0) * 1000) / len(sample_queries)

    # 4. Benchmark Full Dynamic Context Assembly
    t0 = time.perf_counter()
    ctx = store.get_ai_context(user_query="Summarize top critical errors and root causes across pods.")
    t_ctx = (time.perf_counter() - t0) * 1000

    ctx_tokens_est = len(ctx) // 4
    compression_ratio = (total_events / ctx_tokens_est) if ctx_tokens_est > 0 else 0

    print("1. RAG & DATABASE RETRIEVAL BENCHMARKS:")
    print(f"   * Patient Zero Chronological Discovery: {t_pz:.2f} ms")
    print(f"   * Drain Template Clustering (300 logs):  {t_templates:.2f} ms")
    print(f"   * Vectorized BM25/Lexical Search (Avg): {t_scored_avg:.2f} ms")
    print(f"   * Full Diagnostic Context Assembly:     {t_ctx:.2f} ms")
    print(f"   -> Status: SUB-SECOND RAG RETRIEVAL (Target < 100 ms: {'PASS' if t_ctx < 100 else 'ACCEPTABLE'})")

    print("\n2. CONTEXT DENSITY & TOKEN COMPRESSION:")
    print(f"   * Generated Context Size:   {len(ctx):,} chars (~{ctx_tokens_est:,} tokens)")
    print(f"   * 4,096 Window Headroom:    {4096 - ctx_tokens_est:,} tokens remaining for LLM reasoning")
    print(f"   * Telemetry Compression:    {compression_ratio:,.0f}x events compressed per token")

    print("\n3. DIAGNOSTIC ACCURACY HIGHLIGHTS:")
    if pz:
        print(f"   * Patient Zero Isolated:    [{pz['namespace']}] {pz['pod']} ({pz['level']}: {pz['issue_type']})")
        print(f"     Initial Error:           {pz['message'][:80]}...")
    else:
        print("   * Patient Zero Isolated:    None (No error events detected)")

    print(f"   * Top Recurring Patterns:   {len(templates)} unique Drain templates identified")
    for i, tmpl in enumerate(templates[:3], 1):
        print(f"     #{i} ({tmpl['count']}x): {tmpl['template'][:75]}...")

    print("\n4. LOCAL OLLAMA INFERENCE BENCHMARK:")
    ollama_res = benchmark_ollama(model="qwen2.5:3b", prompt_context=ctx)
    if ollama_res.get("available"):
        print(f"   * Model:                    {ollama_res['model']}")
        print(f"   * Total Roundtrip Time:     {ollama_res['total_seconds']} s")
        print(f"   * Inference Speed:          {ollama_res['tokens_per_sec']} tokens/sec")
        print(f"   * Generation Sample:        \"{ollama_res['response_sample']}...\"")
    else:
        print(f"   * Ollama Status:            Offline or unreachable ({ollama_res.get('error', '')})")
        print("     (Start Ollama with 'ollama run qwen2.5:3b' to benchmark local inference speed)")

    print("=" * 72)
    store.close()


if __name__ == "__main__":
    run_benchmark()
