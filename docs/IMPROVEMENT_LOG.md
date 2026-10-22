# Parser Accuracy Improvement Log

Running log of accuracy work on the parser. Update this file as each item lands
so work can be resumed by anyone (human or agent) if a session ends mid-way.

- Baseline before work: `main @ fc4aeeb`, `python3 -m pytest -q` -> 98 passed.
- Status legend: `[ ]` pending, `[~]` in progress, `[x]` done, `[-]` dropped.
- Item numbers match the original review (bugs #1-#12).

> [!WARNING]
> On 2026-10-21 ~23:06 all uncommitted Phase 1 edits (including this file) were
> reverted in the IDE after an API error. Commit or stash after each phase to
> avoid losing work again.

---

## Phase 1 - Bug fixes (small, high-impact) - DONE 2026-10-22 (uncommitted)

| # | Status | Item | Files |
|---|---|---|---|
| 1 | [x] | Undated lines never inherit previous timestamp (`last_ts_micros` never updated) | `parse/syslog.py` |
| 2 | [x] | Fluent parser discards stitcher output; partial `logtag: P` lines and stack traces stored as fragments. Now one stitcher per (ns, pod, container) | `parse/fluent.py`, `parse/stitch.py` |
| 5 | [x] | Pod `state.waiting.reason` read (ImagePullBackOff, CreateContainerConfigError, real CrashLoopBackOff). Synthetic "restarts > 3" fact renamed `HighRestartCount` | `parse/k8s_state.py` |
| 6 | [x] | K8s Event `count` / `series.count` stored as `occurrences`; rules SUM it. Schema migrated with `ADD COLUMN IF NOT EXISTS` | `parse/k8s_state.py`, `store/schema.sql`, `store/duck.py`, `analyze/rules.py` |
| 8 | [x] | Time-window rules keep undated `k8s_facts`/`metrics` rows | `analyze/rules.py` |
| 10 | [x] | Benign phrases scrubbed before level/issue heuristics; `_FAIL_RE` word-bounded | `parse/event.py` |
| 12 | [x] | CRI format unwrapped (runtime ts kept per line), `P` chunks joined | `parse/cri.py` (new), `parse/syslog.py`, `parse/fluent.py` |
| 13 | [x] | (found) Fluent float-epoch `date` crashed `parse_iso_micros`; new `parse_any_ts` | `parse/timestamps.py`, `parse/fluent.py` |
| 14 | [x] | (found) ISO-prefixed lines dropped the token after the ts (often the level) and all continuation lines | `parse/syslog.py` |
| 15 | [x] | (found) Once a traceback started, every non-timestamped line was appended (up to 150). Now language-aware: Python ends after the `XError:` line, Go continues only through frame lines, Java only through `at`/`Caused by`/indented lines | `parse/stitch.py` |
| 16 | [x] | (found) Single-tab-indented lines (`\t... 3 more`) not treated as continuation; Fluent `strip()` removed the tab from `\tat` frames | `parse/stitch.py`, `parse/fluent.py` |
| 17 | [x] | (found) One malformed YAML doc dropped all later docs in the file; events.k8s.io/v1 fields (`note`, `regarding`, `series`) supported | `parse/k8s_state.py` |
| T | [x] | 20 regression tests, named by bug number | `tests/test_parse_accuracy.py` (new) |

Result: `python3 -m pytest -q` -> 118 passed (98 old + 20 new).

Behaviour changes to be aware of:
- k8s fact reason `CrashLoopBackOff` now only appears for a real waiting state; high restart
  counts use `HighRestartCount` (the `crash_loop.yaml` rule still matches via `restarts >= 3`).
- `get_k8s_facts()` rows include an `occurrences` key.
- Fluent/pod-log event counts drop where fragments are now stitched into one event.

## Phase 2 - Bundle profile, year/timezone inference, per-file timestamp format detection  <- NEXT
- [ ] Bundle profile pass (collection time, nodes, per-node TZ, NTP sync, versions, per-source time range)
- [ ] Replace hard-coded `default_year=2026` with inferred year + Dec->Jan rollover handling (bug #3)
- [ ] Apply per-node timezone to naive timestamps (bug #4)
- [ ] Per-file timestamp format detection

Resume notes for Phase 2:
- Hard-coded year lives in `parse/timestamps.py` (`parse_syslog_micros`, `parse_klog_micros`)
  and `parse/syslog.py` (`default_year=2026`). `parse/pipeline.py::_handle_member` calls the
  parsers and is where a profile (year/TZ per node) should be passed in.
- Collection time candidates: archive member mtimes (`ingest/archive.py`), newest
  timestamp seen in the bundle, `date`/`timedatectl` output files in the host snapshot.
- Year rollover: when parsing a yearless file, if month goes from Dec back to Jan, bump
  year; anchor so the newest line is <= collection time.
- Profile must be computed in a cheap pre-pass (only small text files) before the main scan.

## Phase 3 - Coverage
- [ ] kubectl text-table parser (`get -o wide`, `describe`) keyed on header row (bug #7)
- [ ] Node conditions, Deployment/StatefulSet/DaemonSet unavailable replicas, PVC Pending/Lost, Job failures
- [ ] Usage: `df -i`, load/CPU, `kubectl top nodes`, real numeric values for `kubectl top pods` (bug #9), NTP drift
- [ ] Persist NETWORK / TEXT files (searchable) instead of dropping them (bug #11)
- [ ] Kernel signatures: hung task, I/O error, fs errors, NIC flaps, soft lockup

## Phase 4 - Structured field extraction
- [ ] Detect JSON (zap/logrus) and logfmt inside container logs; extract level/ts/msg + `fields`

## Phase 5 - Detection quality
- [ ] Median/MAD per-namespace baselines in correlator; implement SILENCE detection
- [ ] Rule engine: threshold/window, sequence, absence rules
- [ ] Golden-bundle accuracy test harness

## Phase 6 - Cross-bundle known-noise library
- [ ] Persist Drain templates across bundles; down-weight ubiquitous templates

---

## Session log
(newest last)

### 2026-10-21
- Created this log. Implemented Phase 1 code changes; existing 98 tests passed.
- Session ended on API error; IDE reverted all uncommitted edits (~23:06).

### 2026-10-22
- Re-applied Phase 1 from scratch.
- Writing tests exposed #15-#16 (traceback overrun, tab indentation); fixed.
- Phase 1 complete, 118 tests passing. Not committed yet; waiting for the user to
  review/commit before Phase 2.
