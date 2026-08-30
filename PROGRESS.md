# Build progress — Track 4 Shopping Copilot

Working branch: `feat/hybrid-retrieval`. Plan: `~/.claude/plans/adaptive-tumbling-lantern.md`.
Score ledger: [`eval/RESULTS.md`](eval/RESULTS.md).

**Current best: TechnicalScore 0.77773** (HR@10 0.900 / MRR 0.5701 / MTTC 3.17)
Lexical-only floor (no models, no network): **0.76125**. Baseline was 0.10671; the
pre-existing 12-line version was 0.75040.

⚠️ The +0.0165 over the lexical floor is **not significant at n=200** — paired 95% CI
[-0.014, +0.047], 3/5 folds. See `eval/RESULTS.md` runs 4-5. Treat it as the first ledger
entry to re-check against the private result rather than trust.

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

*(Both of those are now fixed — see below.)*

---

## Stage 2 — override erasure landed, and the cross-encoder is back on ✅

Run 3 predicted erasure would flip the cross-encoder positive. It did, but only together with
two further changes, and **the aggregate gain is not statistically significant.** Both halves
of that sentence matter. Full detail: `eval/RESULTS.md` runs 4-5.

| increment (CE on, full 200) | TS | intent_override |
|---|---|---|
| CE only, no erasure | 0.75262 | 0.5964 |
| + erase the retracted opening constraint | 0.76574 | 0.6838 |
| + promote the new constraint to lead the query | 0.76799 | 0.6988 |
| + adaptive `skip_rerank` on override | 0.77723 | **0.7605** |
| + refusal fix + drained bookkeeping (**shipping**) | **0.77773** | 0.7605 |

**What each piece does**

1. **Erasure** (`state.py`). The override sentence retracts what the *opening* claimed, so the
   opening is split into `opening_category` + `opening_extra` and only the latter is dropped.
   Recovered +0.087 on override.
2. **Promotion.** The new constraint leads the distilled query instead of trailing it —
   "erase and rewrite" means the new intent governs, not that it gets equal billing at the end
   of a list of older values. +0.015.
3. **Adaptive `skip_rerank`** (`orchestrator.py`). After an override the customer has one
   strong verbatim constraint and BM25 is already near-optimal on it; semantic reranking blurs
   a lexical match it cannot improve. **Verified exact: all 30 override sessions are now
   byte-identical to the lexical track (paired delta 0.00000, 0 better / 0 worse).**
4. **Refusal fix.** `BOILERPLATE` covered "an additional preference" but not the bare article,
   so `"I don't have a preference for color; please use your judgment."` entered the ranker's
   query as if it were a constraint; when it *did* match it left the attribute name behind,
   injecting `other` — the agent's own question — as content. Refusals now drop whole.
5. **`drained`/`unavailable`** are populated from the same parse, so `exhausted()` is real.
   Saves a turn (MTTC 3.41 -> 3.37).

**Erasure is confined to the ranking view by design.** `query_terms()` still spans the raw
transcript, so the lexical track is byte-identical and the CE-off control reproduces the floor
exactly. That is deliberate: the retracted value is `soft_preferences[-1]`, a *genuine*
attribute of the target product, so stripping its terms from retrieval risks recall.

### ⚠️ Read this before building on the number

Paired per-session deltas vs the same code with the CE off:

| scenario | n | mean delta | 95% CI | better/worse |
|---|---|---|---|---|
| buying | 80 | **+0.0409** | [-0.0031, +0.0849] | 32 / 16 |
| browsing | 80 | +0.0078 | [-0.0520, +0.0677] | 25 / 24 |
| intent_override | 30 | +0.0000 | exact no-op | 0 / 0 |
| boundary | 10 | -0.0602 | [-0.1330, +0.0126] | 1 / 4 |
| **aggregate** | 200 | **+0.0165** | **[-0.0135, +0.0465]** | 58 / 44 |

The CI crosses zero (1.08 sigma) and only 3/5 folds improve. **The one real effect is on
buying**; browsing is a coin flip. Kept because the point estimate is positive on the 160
sessions the ranker still touches, because these are mechanisms rather than tuned constants,
and because erasure is Pillar II-required regardless of score. The private set's 800 sessions
will settle it.

**Boundary regresses (-0.060) and is deliberately left alone** — n=10 cannot resolve a 0.06
effect, HR@10 is identical with only MRR moving, and special-casing a scenario seen in the
public set is what `CLAUDE.md` §9 forbids.

**Verified:** 25/25 tests pass; `src/` import boundary clean; with `models/` renamed away the
agent degrades to 0.76125 without crashing (the offline floor — no paid LLM required).

---

## Remaining work, in priority order

### 1. Diagnose the dense track — the only untouched *scored* lever
Still off (`TECHJAM_DENSE=1`). It is Pillar I's browsing track and the hedge against a
paraphrasing private set, so it should not stay off permanently. Measure dense-only HR@10
with no fusion first: that separates "the dense ranking is weak" from "RRF is mis-weighting a
weak vote as an equal one". Suspects in order: RRF weights, `as_query()` text the encoder was
not trained for, `truncation=50` too shallow for fusion to recover.

### 2. Slot parsing and decay (Pillar II)
`classify_constraint()` (`local_evaluator.py:137-151`) is pure, so mirroring it is correct by
construction. `drained`/`unavailable` now exist; typed `slots` and decay do not. Expect little
metric movement — the `"other"` wildcard is already optimal — but the trace needs typed slots
for the demo, and `router.py` reads `slot_count()` to pick a route.

### 3. Pillar III — `user_profile` is still ignored entirely
`reset()` receives `preference_tags`, `average_prior_rating`, `summary`; none is used. Fold in
as a weak prior that never overrides a disclosed constraint.

### 4. Validation
- k-fold the run-2 self-refinement gain — still at the noise floor, still untrusted.
- Verify the Flask demo end-to-end (`make demo`) — written, never run.
- `HF_HUB_OFFLINE=1` must produce an identical score.
- Runtime: the CE costs 22s -> 108s per 200 sessions. Fine locally; confirm the private
  harness has no per-call timeout that this could trip.

### 5. Deliverables — none started, none droppable
README / Devpost / video script / `architecture.md`, then record and upload the video and flip
the repo public. An unshipped video or a private repo is a failed submission regardless of
score. **The Pillar III trace now has real decisions to show** — `intent_override` erasure and
`skip_rerank` both emit trace records with the signal that triggered them.

## Known risks
- **The headline gain is unproven.** +0.0165 over the lexical floor, paired 95% CI
  [-0.014, +0.047], 3/5 folds improving. The single real effect is on `buying`. If the
  private result disappoints, `TECHJAM_CE_WIDTH=0` returns to the 0.76125 floor in one
  env var, and that floor is itself solid (it reproduces exactly, and survives `models/`
  being deleted).
- **Boundary regresses -0.060** and is deliberately not special-cased (n=10).
- **Runtime.** 22s per 200 sessions lexical-only, 108s with the cross-encoder. Comfortable
  locally, but the private harness may impose a per-call timeout the CE could trip; the
  fallback ladder degrades silently rather than failing, which is the right shape.
- **Private-set fragility.** `competition_specification.md:40` reserves the right to add
  "natural-language paraphrasing". The score still leans on constraints being verbatim
  substrings of the target product. The dense track is the intended hedge — built, and
  currently a measured -0.0645 regression, so the hedge is not yet available.
- **The dense track is off.** Pillar I's browsing track exists and is unused at inference.
  It is demonstrable in the trace and the demo, but "hybrid retrieval" is currently one
  live track plus a reranker, and the writeup must say so honestly.
