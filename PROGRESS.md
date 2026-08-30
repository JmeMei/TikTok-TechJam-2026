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
- `.venv` on Anaconda CPython **3.13.9**. The `python` on PATH is MSYS2/mingw64, whose ABI
  cannot install torch wheels — the venv is built from `C:\Users\Tharun\anaconda3\python.exe`
  instead. **Use `.venv\Scripts\python.exe`, not bare `python`.**
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

## The open problem — cross-encoder is currently a regression

First smoke test (20 sessions) with the cross-encoder live:

| | baseline | with cross-encoder |
|---|---|---|
| TechnicalScore | ~0.76 | **0.32464** |
| HR@10 | ~0.89 | **0.4500** |
| speed | ~1 s/session | **25 s/session** |

Two causes were identified and **fixes are written but not yet measured**:

1. **The ranker was scoring on less information than the retriever used.** BM25 retrieves
   over the whole accumulated transcript, but `distilled().as_query()` returned only the
   opening message, because typed slots are not populated until Stage 5. So the reranker
   was systematically undoing good candidates. Fixed by making distillation real now:
   boilerplate-stripped, deduplicated disclosures from every turn (`BOILERPLATE` regex in
   `state.py`) rather than an empty struct.
2. **fp16 on CPU is slower than fp32.** Weights are stored fp16 to fit in git, but torch
   has no fast CPU fp16 kernels and takes a slow path. Now upcast once at load.
   Also capped the cross-encoder to the top `CE_WIDTH=25` fused candidates instead of all
   50 — cost is linear in width and the tail keeps its RRF order.

**⚠️ The fixes are committed but STILL UNMEASURED.** Work was stopped before the re-run.
This is the single most important open question in the repo: **the ranking cascade is
currently switched on and has never been shown to help.** If it still loses it must ship
off by default with the negative result documented.

---

## Remaining work, in priority order

### 1. Measure the cascade — do this first, before writing any new code
```bash
.venv/Scripts/python.exe -m eval.run_eval --limit 20 --output results-smoke.json
```
Compare against the 0.76035 baseline. Three possible outcomes:
- **Wins** → run the full 200 and record in `eval/RESULTS.md`.
- **Loses** → set `CE_WIDTH=0` / disable the cross-encoder rung, keep the code, document it.
- **Too slow** (>10 min for 200 sessions) → narrow `CE_WIDTH` further or gate the cascade
  behind the over-generality signal so it fires on a minority of turns.

Nothing else should be built until this number exists. **No claim of improvement is valid
without it** (`CLAUDE.md` §8).

### 2. Pillar II — the biggest untouched pillar *(blocked on nothing)*
`src/state.py` has the fields but the logic is stubs. Needed:
- **Slot parsing** mirroring `classify_constraint()` (`local_evaluator.py:137-151`) exactly.
  It is a pure function, so a mirror is correct by construction.
- **Intent-override erasure.** All **30 `intent_override` sessions** currently drag the
  stale preference into every subsequent query. Detect
  `"Actually, ignore my earlier preference…"` and **erase**, don't append.
- **Slot decay**, and the `drained` / `unavailable` bookkeeping from the two other fixed
  reply shapes.
- **Over-generality cutoff** — `policy.decide_ask` has the hook; the thresholds are untuned.

### 3. Pillar III — `user_profile` is ignored entirely *(blocked on nothing)*
`reset()` receives `preference_tags`, `average_prior_rating`, `summary` and none of it is
used. Fold in as a **weak prior** that never overrides a disclosed constraint.

### 4. LLM semantic ranking *(blocked on the Qwen download)*
`src/llm.py` is written — pointwise logprob scoring, wall-clock budget, degrades to None.
Untested against real weights. This is Pillar I's explicitly named stage.

### 5. Validation
- **k-fold the +0.00995 self-refinement gain** — it sits exactly at the n=200 noise floor.
- k-fold every tuned knob before trusting it (`make eval-fold N=5`).
- Verify the Flask demo end-to-end (`make demo`) — written, never run.
- Offline check: `HF_HUB_OFFLINE=1` must produce an identical score.
- Degradation check: rename `models/` → must fall back to BM25 near 0.75, not crash.

### 6. Deliverables — none started, none droppable
README / Devpost / video script / `architecture.md`, then record + upload the video
(public, linked in Devpost) and flip the repo public. An unshipped video or a private repo
is a failed submission regardless of score.

---

## Known risks
- **The cascade may simply lose.** It is on right now and unproven. Measure, then decide.
- **Runtime.** Pure BM25 already takes 2m15s for 200 sessions. The first cascade smoke test
  ran at 25s/session, which would be ~84 min for a full run — unusable for iteration. The
  fp32 and `CE_WIDTH` fixes target this but are unverified.
- **Private-set fragility.** `competition_specification.md:40` reserves the right to add
  "natural-language paraphrasing". The current score leans on constraints being verbatim
  substrings of the target product. The dense track is the hedge — now built, not yet proven.
- **`intent_override` still drags stale slots** across all 30 of those sessions.
