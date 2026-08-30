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

## In flight

**Dense index build** — `scripts/build_index.py` running in background, ~20+ min elapsed
encoding 50k products on CPU. Writes `data/index/emb.f16.npy` (~37MB) + `ids.json`.
Until it lands, `DenseIndex.available` is False and the dense track is skipped — by design.

**Qwen download — PAUSED at 191MB of ~988MB.** Resume with:
```bash
.venv/Scripts/python.exe scripts/fetch_llm.py --model Qwen/Qwen2.5-0.5B-Instruct
```
HF resumes from the partial blob; nothing is lost. The link is slow (~80MB/5min), so this
is roughly an hour of wall clock. Nothing else is blocked on it.

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

**Next action: re-run the 20-session smoke test.** If the cross-encoder still loses, it
ships off by default and the negative result gets written up — that is a better Technical
Execution story than an unmeasured feature left switched on.

---

## Remaining

| | Stage | Blocked on |
|---|---|---|
| ☐ | Re-measure cross-encoder after the two fixes | nothing |
| ☐ | Dense track + RRF end-to-end measurement | index build |
| ☐ | LLM semantic ranking (Pillar I's named stage) | Qwen download |
| ☐ | **Pillar II proper** — slot parsing mirroring `classify_constraint()`, intent-override **erasure**, slot decay, over-generality cutoff | nothing — highest-value remaining work |
| ☐ | **Pillar III proper** — `user_profile` as a weak prior (currently ignored entirely) | nothing |
| ☐ | k-fold the self-refinement gain and every tuned knob | nothing |
| ☐ | Verify Flask demo end-to-end | index build |
| ☐ | README / Devpost / video script / architecture.md | code settling |
| ☐ | Record + upload video, flip repo public | everything |

### Known risks
- **Cross-encoder may simply lose.** Measure, then decide; do not keep it on faith.
- **Runtime.** Baseline is already 2m15s for 200 sessions on pure BM25. Adding ranking
  stages could push a full run past 30 minutes, which throttles iteration.
- **Private-set fragility.** `competition_specification.md:40` reserves the right to add
  "natural-language paraphrasing" to the simulator. Current score leans on constraints
  being verbatim substrings of the target product. The dense track is the hedge.
- **`intent_override` still drags stale slots** — 30 sessions carry the erased preference
  into every query. Pillar II work fixes this.
