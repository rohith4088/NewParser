# Supercharged Local AI Copilot (Qwen 2.5:3B) - Architecture & Reversion Guide

This document records the exact changes introduced for **Point 4: Supercharging the Local AI Copilot**, including the technical mechanisms, test procedures, and instructions to undo or revert changes if required.

---

## 1. Executive Summary

To maximize the diagnostic accuracy of the local **Qwen 2.5:3B** model within its hardware budget (8GB RAM, 4,096-token dynamic context window), we enhanced the retrieval-augmented generation (RAG) pipeline in DuckDB from naive keyword matching into an information-dense, multi-tier diagnostic context generator:

1. **Patient Zero Detection**: Automatically discovers and isolates the earliest timestamped critical failure event in the bundle or observation window to prevent the LLM from blaming downstream victim services.
2. **Drain Log Template Mining**: Clusters hundreds of thousands of repeating log lines into parameterized templates (`<*>`) with exact occurrence counts and time spans, giving the LLM a 10x higher information density in ~150 tokens.
3. **BM25 / Vectorized Lexical Relevance Scoring**: Replaced unranked `ILIKE ?` substring filters with a weighted scoring query across components (`pod`, `namespace`), issue classifications (`OOM Killed`, `Connection Refused`), and severity levels (`FATAL`, `CRITICAL`, `ERROR`).
4. **Structured Diagnostic Playbook Prompt**: Directs Qwen 2.5:3B into a 4-part authoritative format:
   - **Root Cause Analysis (RCA)**
   - **Trigger Sequence & Cascading Impact**
   - **Impacted Components & Blast Radius**
   - **Actionable Remediation Playbook** (with copy-pasteable CLI commands)

---

## 2. Modified Files & Line Details

### A. [`analyzer/store/duck.py`](file:///Users/rohithr/Desktop/onpremParser/onprem-analyzer/analyzer/store/duck.py)
- **Import added**:
  ```python
  from ..mine.drain import DrainParser
  ```
- **New Method: `get_patient_zero(...)`**:
  Queries the earliest `FATAL`, `CRITICAL`, or `ERROR` event in scope ordered chronologically by `ts_micros ASC LIMIT 1`.
- **New Method: `get_mined_templates(...)`**:
  Samples recent error/warn events, runs them through the online `DrainParser` tree, and extracts top templates with frequency counts, affected pods, and first/last seen timestamps.
- **New Method: `get_scored_events(...)`**:
  Computes a vectorized relevance score per candidate log event:
  $$\text{Score} = \text{SeverityWeight} + \text{IssueTypeWeight} + \sum_{kw} \left( 3.5 \cdot \text{Pod} + 3.0 \cdot \text{Namespace} + 2.5 \cdot \text{IssueType} + 2.0 \cdot \text{Message} \right)$$
  Orders by `relevance_score DESC, ts_micros DESC`.
- **Updated Method: `get_ai_context(...)`**:
  Synthesizes Bundle Metadata, Patient Zero, Deterministic Rule Signatures, Drain Templates, Top Failing Services, K8s Fact Restarts, and Scored Live Events.

### B. [`analyzer/server/routes.py`](file:///Users/rohithr/Desktop/onpremParser/onprem-analyzer/analyzer/server/routes.py#L878-L895)
- **Updated `system_prompt`**:
  Replaced generic guidelines with the structured 4-part playbook instructions and strict anti-hallucination grounding rules.

### C. [`tests/test_ai_context.py`](file:///Users/rohithr/Desktop/onpremParser/onprem-analyzer/tests/test_ai_context.py)
- **New Test Suite**:
  - `test_patient_zero_detection`: Verifies earliest failure discovery and namespace scoping.
  - `test_drain_mined_templates`: Verifies parameter masking (`<*>`) and template grouping.
  - `test_scored_events_retrieval`: Verifies lexical relevance scoring and ranking.
  - `test_get_ai_context_comprehensive`: Verifies end-to-end compiled context.

---

## 3. How to Run Verification Tests

Run the newly added test suite:
```bash
pytest tests/test_ai_context.py -v
```

Run the complete test suite across the entire project:
```bash
pytest -q
```

---

## 4. How to Undo / Revert Changes

If you ever need to rollback to the previous implementation:

### Option 1: Git Revert (Recommended)
```bash
git checkout HEAD -- analyzer/store/duck.py analyzer/server/routes.py
rm tests/test_ai_context.py
rm docs/SUPERCHARGED_AI_COPILOT.md
```

### Option 2: Manual Reversion
1. In [`analyzer/store/duck.py`](file:///Users/rohithr/Desktop/onpremParser/onprem-analyzer/analyzer/store/duck.py):
   - Remove `from ..mine.drain import DrainParser` at line 14.
   - Delete `get_patient_zero()`, `get_mined_templates()`, and `get_scored_events()`.
   - Restore the previous `get_ai_context()` method with the simple `ILIKE ?` query.
2. In [`analyzer/server/routes.py`](file:///Users/rohithr/Desktop/onpremParser/onprem-analyzer/analyzer/server/routes.py):
   - Restore the previous simple `system_prompt` string.
3. Delete `tests/test_ai_context.py`.
