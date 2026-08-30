# Build progress — Track 4 Shopping Copilot

Working branch: `feat/hybrid-retrieval`. Plan: `~/.claude/plans/adaptive-tumbling-lantern.md`.
Score ledger: [`eval/RESULTS.md`](eval/RESULTS.md).

**Current best: TechnicalScore 0.76035** (HR@10 0.890 / MRR 0.5452 / MTTC 3.41)
Baseline was 0.10671; the pre-existing 12-line version was 0.75040.

---

## Done

### Stage 0 — Environment unblocked ✅
- Branch `feat/hybrid-retrieval` off `main`.
- `data/catalog.jsonl` restored, **SHA-256 verified** against `SHA256SUMS`, 50,000 rows.
  It is gitignored (60MB), so **a fresh clone has no catalog** — re-fetch and verify:
  ```bash
  curl.exe -sL -o catalog.jsonl.gz "https://github.com/TechJam2026/techjam-conversational-search/releases/download/participant-kit/catalog.jsonl.gz"
  sha256sum catalog.jsonl.gz          # must match SHA256SUMS
  gzip -dc catalog.jsonl.gz > data/catalog.jsonl
  ```
- `.venv` on CPython **3.13**. The `python` on PATH is a shim (MSYS2/mingw64 on one box, the
  Windows Store alias on another) and cannot install torch wheels — build the venv from a
  real python.org or Anaconda interpreter. **Use `.venv/Scripts/python.exe`, never bare
  `python`**; bare `python` opens a REPL and will hang a non-interactive shell.
  ```bash
  "$LOCALAPPDATA/Programs/Python/Python313/python.exe" -m venv .venv
  ./.venv/Scripts/python.exe -m pip install -r requirements-dev.txt \
      --extra-index-url https://download.pytorch.org/whl/cpu
  ```
  Verified on this machine 31 Aug: torch 2.13.0+cpu / transformers 4.57.6 / numpy 2.5.2,
  25/25 tests passing, 0.76035 reproduced.
- `requirements.txt` (numpy, torch CPU, transformers) + `requirements-dev.txt` (flask).
  Installed: torch 2.13.0+cpu, transformers 4.57.6, numpy 2.5.2, flask 3.1.3.
- `Makefile` added — the targets `CLAUDE.md` §7 documented but which never existed.
- **Baseline reproduced exactly: 0.750401**, all four breakdowns matching `results_v1.json`.

### Stage 1 — Module split with interface contracts ✅
`starter/agent.py` is now a two-line shim re-exporting `src.agent`, so
`python -m evaluator.local_evaluator` runs unmodified (the evaluator hardcodes that import
and the rules forbid editing it).

| Module | Pillar | State |
|---|---|---|
| `src/agent.py` | integration | Full pipeline, nothing can escape `respond`/`reset` |
| `src/router.py` | I | Dual-track routing, per-track config (not literals) |
| `src/retrieval.py` | I | BM25 (FTS5) + DenseIndex w/ MMR + RRF fusion |
| `src/rerank.py` | I | Cascade: cross-encoder → LLM → fallbacks, reorder-only |
| `src/llm.py` | I | Pointwise logprob scorer, wall-clock budget |
| `src/state.py` | II | Transcript, slots, decay fields, distillation |
| `src/policy.py` | II | Ask selection, over-generality cutoff |
| `src/orchestrator.py` | III | Context distillation, re-planning, self-refinement |
| `src/trace.py` | III | Structured per-turn decision trace |
| `src/cache.py` | — | Content-keyed LRU (never `session_id` — it's a fresh uuid4) |
| `eval/run_eval.py` | IV | 4 breakdowns, delta, verdict, stratified k-fold |

**Refactor proven behaviour-identical**: with `TECHJAM_NO_SELF_REFINE=1` it scores
0.750401 and all **200 per-session records** match `results_v1.json` exactly.

### Stage 1b — Self-refining guidance (Pillar III) ✅ **+0.00995**
Stop asking after two consecutive asks that reveal nothing. **0.75040 → 0.76035.**
Sits right at the n=200 noise threshold — **needs k-fold before it is trusted.**

### Tests ✅ — 25 passing
`tests/test_agent.py` encodes the invariants that cost sessions: `respond()` survives
hostile input (FTS5-breaking quotes, SQL, 20k chars, emoji), respond-without-reset no
longer raises as the starter did, recommendations unique and catalog-valid,
`ask_attribute` always contract-valid, ranker is reorder-only.

### Assets ✅
- `models/bi-encoder` — all-MiniLM-L6-v2, fp16, **45MB**, committed
- `models/cross-encoder` — ms-marco-MiniLM-L-6-v2, fp16, **45MB**, committed
- Both under GitHub's 100MB/file limit, no LFS. These are the **offline floor**.

### Demo scaffolding ✅ (untested end-to-end)
`app/server.py` + template — Flask walkthrough, auto-play a public session with the
official simulator or free-chat. Dev-only; `make check` fails the build if `src/` ever
imports flask. `scripts/dump_trace.py` writes the same trace schema for static analysis.

---

### Dense index ✅ built
`data/index/emb.f16.npy` — **(50000, 384) fp16, 38.4MB** — plus `ids.json`. Committed, so
a fresh clone gets the dense track without rebuilding (the build took ~35 min on CPU).
`DenseIndex.available` is now True, so the browsing track with MMR diversity is live.

### Type scale fixed ✅
The Flask template had 8 font sizes between 10px and 15px — 0.5px steps are noise, not
hierarchy. Collapsed to three tokens (`--fs-micro:11px` / `--fs-body:14px` /
`--fs-lead:18px`, ~1.28 ratio); everything else separates by weight, colour or case.

---

## Paused

**Qwen download — PAUSED at 191MB of ~988MB.** Resume with:
```bash
.venv/Scripts/python.exe scripts/fetch_llm.py --model Qwen/Qwen2.5-0.5B-Instruct
```
HF resumes from the partial blob; nothing is lost. The link runs ~80MB/5min, so budget
about an hour of wall clock. Nothing else is blocked on it — the cascade simply runs one
tier lower (cross-encoder as final ranker) until it is present.

---

## RESOLVED — the cascade was measured. The dense track was the regression.

`PROGRESS.md` previously blamed the cross-encoder on the strength of a 20-session smoke
test. **That smoke test was wrong in sign.** At n=20 the intent_override scenario gets only
4 sessions, and it is the scenario that decides this question. Four full 200-session runs,
one variable at a time (`eval/RESULTS.md` runs 3-3c):

| config | TS | HR@10 | MRR | wall |
|---|---|---|---|---|
| BM25 only (= run 2) | **0.76035** | 0.8900 | 0.5452 | 22s |
| + dense/RRF | 0.69589 | 0.8100 | 0.5073 | 64s |
| + dense/RRF + cross-encoder | 0.69931 | 0.8050 | 0.5310 | 166s |
| + cross-encoder only | 0.75262 | 0.8800 | 0.5311 | 121s |

**1. The dense track cost -0.0645 and had never been measured.** At run 2 the index did not
exist, so `DenseIndex.available` was False and the pipeline was pure BM25. Committing
`data/index/` in `adf486e` switched the dense track on as a *silent side effect of shipping
a build artifact*. Now default-off behind `TECHJAM_DENSE=1`.

**2. The speed problem is fixed.** The fp32 upcast and `CE_WIDTH=25` took the cross-encoder
from 25s/session to ~0.6s/session. A full 200-session run is 22s without it, 121s with it.
Iteration is no longer gated on runtime.

**3. The cross-encoder is not a failed idea — it is being fed a poisoned query.**

| scenario | n | BM25 only | + cross-encoder | delta |
|---|---|---|---|---|
| buying | 80 | 0.7616 | **0.8025** | **+0.041** |
| browsing | 80 | 0.7522 | **0.7615** | **+0.009** |
| intent_override | 30 | 0.7605 | 0.5964 | **-0.164** |
| boundary | 10 | 0.8150 | 0.7509 | -0.064 (n=10, noise) |

It helps 160 of 200 sessions. It collapses on the 30 override ones because override erasure
is unimplemented. Verified directly — after an override the distilled query is:

```
'Dresses. color: pink | 100% polyester | cotton'
```

`color: pink` is the stale pre-override decoy still *leading* the query; `cotton`, the real
post-override hard constraint, is last at equal weight. BM25 survives this by diluting the
decoy across dozens of OR'd terms; a cross-encoder concentrates on it. `state.override_seen`
is never set; `state.erased` is always empty.

**Shipping state: dense OFF, cross-encoder OFF, verified back at 0.76035 with the index
present on disk.** Both are one env var from returning.

---

## Remaining work, in priority order

### 1. Override erasure (Pillar II) — now the highest-value item, and it unlocks #2
`src/state.py`. Detect `"Actually, ignore my earlier preference. What I need is: X."`,
set `override_seen`, move the opening decoy into `erased`, and rebuild `category_hint()` so
the stale value stops leading the distilled query. Keep the coarse category — that part of
the opening is still true.

One caveat worth knowing before writing it: the override `old_value` is
`soft_preferences[-1]`, which is a *genuine* attribute of the target product, not a false
one. So this is a **re-weighting problem, not a contradiction** — dropping it from the
ranker's query is right, but purging its terms from BM25 retrieval may cost recall. Erase
from the distilled/rerank query first, measure, and only then consider BM25.

### 2. Re-enable the cross-encoder and re-measure — expected ~+0.02
`TECHJAM_CE_WIDTH=25`. If the 30 override sessions merely return to their BM25-only 0.7605
while buying and browsing keep their gains, the aggregate is ~0.780. This is the single
largest measured opportunity in the repo and it is gated only on #1.

### 3. Diagnose the dense track before re-enabling it
It is Pillar I's browsing track and the hedge against a paraphrasing private set, so it
should not stay off permanently. Likely suspects, cheapest first: RRF weights treating a
weak ranking as an equal vote; the encoder seeing `as_query()` output it was not trained
for; `truncation=50` being too shallow for fusion to recover. Measure per-track HR@10
alone (dense-only, no fusion) to find out whether the ranking or the fusion is at fault.

### 4. Pillar II proper — slot parsing, decay, drained/unavailable bookkeeping
`classify_constraint()` (`local_evaluator.py:137-151`) is pure, so mirroring it is correct
by construction. Note `drained` is currently never populated, so `exhausted()` is always
False and `_select` always returns `"other"`. The `"other"` wildcard is genuinely optimal
(it matches any undisclosed constraint), so expect little metric movement here — but it is
required pillar behaviour and the trace needs it for the demo.

### 5. Pillar III — `user_profile` is still ignored entirely
`reset()` receives `preference_tags`, `average_prior_rating`, `summary`; none is used. Fold
in as a weak prior that never overrides a disclosed constraint.

### 6. Validation
- k-fold the +0.00995 self-refinement gain — still at the n=200 noise floor, still untrusted.
- Verify the Flask demo end-to-end (`make demo`) — written, never run.
- `HF_HUB_OFFLINE=1` must produce an identical score.
- Rename `models/` -> must fall back to BM25 near 0.76, not crash.

### 7. Deliverables — none started, none droppable
README / Devpost / video script / `architecture.md`, then record and upload the video and
flip the repo public. An unshipped video or a private repo is a failed submission
regardless of score.

## Known risks
- **The cascade may simply lose.** It is on right now and unproven. Measure, then decide.
- **Runtime.** Pure BM25 already takes 2m15s for 200 sessions. The first cascade smoke test
  ran at 25s/session, which would be ~84 min for a full run — unusable for iteration. The
  fp32 and `CE_WIDTH` fixes target this but are unverified.
- **Private-set fragility.** `competition_specification.md:40` reserves the right to add
  "natural-language paraphrasing". The current score leans on constraints being verbatim
  substrings of the target product. The dense track is the hedge — now built, not yet proven.
- **`intent_override` still drags stale slots** across all 30 of those sessions.
