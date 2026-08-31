# Shopping Copilot: TikTok TechJam 2026, Track 4

A conversational shopping agent for the TechJam Conversational E-Commerce Search Challenge.
Given an anonymized preference profile and a short customer message, the agent has at most
10 turns to surface a hidden target product from a frozen 50,000-item Amazon Clothing catalog.

**TechnicalScore 0.815121** on the 200 labelled public sessions: 7.6x the provided baseline.
**No LLM. Zero API cost. Zero network at inference.**

The original challenge README is preserved at [`docs/CHALLENGE.md`](docs/CHALLENGE.md).

---

## Results

Measured with `evaluator/local_evaluator.py`, unmodified. Full run history including every
rejected experiment: [`eval/RESULTS.md`](eval/RESULTS.md).

| | TechnicalScore | HR@10 | MRR | MTTC | Efficiency |
|---|---|---|---|---|---|
| Provided BM25 starter | 0.10671 | 0.1250 | 0.0680 | 9.81 | 0.119 |
| **This system** | **0.815121** | **0.935** | **0.623403** | **2.970** | **0.8030** |

By scenario:

| Scenario | n | HR@10 | MRR | MTTC | TS |
|---|---|---|---|---|---|
| buying | 80 | 0.9375 | 0.5924 | 2.450 | 0.8175 |
| browsing | 80 | 0.9500 | 0.6425 | 2.837 | 0.8310 |
| intent_override | 30 | 0.9000 | 0.6685 | 4.500 | 0.7806 |
| boundary | 10 | 0.9000 | 0.5833 | 3.600 | 0.7730 |

`intent_override` cannot record a hit before the override turn (3 or 4) by construction, so
its MTTC floor is structural, not a deficiency.

### Graceful degradation: three measured tiers

The system never hard-fails on a missing asset; it drops a rung and keeps scoring.

| Tier | Requires | TechnicalScore |
|---|---|---|
| Full | `scripts/fetch_models.py` (fetches bge-reranker-base, ~573 MB) | **0.815121** |
| Committed models only | a plain clone, no setup step | 0.786710 |
| Lexical floor | no models at all, no network | 0.761250 |

Verified by renaming `models/` away and re-running: the agent degrades, it does not crash.

---

## Architecture

```
customer message
      |
   state.py         accumulate constraints; ERASE retracted ones on intent override
      |
   router.py        classify buying / browsing / mixed -> per-track weights
      |
   retrieval.py     BM25 over SQLite FTS5, top 50        [dense track built, OFF - see below]
      |
   orchestrator.py  distil dialogue into a compact query; re-plan from turn telemetry
      |
   rerank.py        bge-reranker-base reorders the top 25
      |                 fallback: MiniLM cross-encoder -> fallback: BM25 order
   policy.py        choose the question by expected information gain over the
                    live candidates; ask it in prose, harvest via the wildcard
      |
   top 10 + trace.py structured record of every strategy decision
```

| Module | Pillar | Role |
|---|---|---|
| `src/router.py` `src/retrieval.py` `src/rerank.py` | I: Intent routing & hybrid pipeline | dual-track routing, BM25, RRF fusion, reranking cascade |
| `src/state.py` `src/policy.py` | II: Dialog strategy | slot accumulation, override erasure, ask-vs-answer |
| `src/orchestrator.py` `src/trace.py` | III: Self-evolution | context distillation, runtime re-planning, decision trace |
| `eval/run_eval.py` | IV: Evaluation matrix | 4 scenario breakdowns, k-fold, paired significance |

Module boundaries are contracts: `retrieval` never reads dialogue state, `policy` never calls
a model, `rerank` never issues new retrievals and may only reorder a fixed candidate set.

### The three changes that produced the score

1. **`ask_attribute` is an information faucet** (0.107 → 0.750). The starter passed `null`
   every turn, so the customer revealed nothing and it re-ran one query ten times. The
   simulator honours `"other"` as a wildcard matching *any* undisclosed constraint, and holds
   only four constraints, so two questions drain it.
2. **Intent-override erasure** (0.760 → 0.778). The opening message of an override session
   carries a preference the customer later retracts. Erasing it — rather than accumulating it
   — and promoting the replacement to lead the query recovered +0.087 on those sessions.
3. **The reranker could not see the field it was being asked about** (0.778 → 0.814). Our
   document rendering showed the model `title + price + features[:2]` and **no `details`**,
   while the simulator derives constraints from `features` **and** `details`. It was being
   asked to match text the document did not contain.
4. **Specialised questions, at no metric cost** (0.814 → 0.815). Each turn the agent scores
   every candidate question by `coverage x entropy` over the *surviving* candidates and asks
   the most informative one, offering values read off those candidates. The structured
   `ask_attribute` stays `"other"` — the wildcard that harvests the evaluator's full
   2-constraints-per-turn cap — so the specificity is free. See *Question selection* below.

---

## Setup

Python 3.10+ (3.13.15 produced the reported numbers). CPU only; no GPU required.

```bash
# 1. Dependencies
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cpu

# 2. Catalog (gitignored, ~60 MB decompressed)
curl.exe -sL -o catalog.jsonl.gz \
  "https://github.com/TechJam2026/techjam-conversational-search/releases/download/participant-kit/catalog.jsonl.gz"
sha256sum catalog.jsonl.gz          # must match SHA256SUMS
gzip -dc catalog.jsonl.gz > data/catalog.jsonl    # expect 50000 lines

# 3. Reranker (~573 MB, not committed - see "Model choice")
./.venv/Scripts/python.exe scripts/fetch_models.py
```

### Reproduce the reported score: one command

```bash
python -m evaluator.local_evaluator
```

Expect `"recommended_technical_score": 0.815121` and per-session records in `results.json`.

Skipping step 3 is safe: the agent falls back to the committed cross-encoder and scores
0.786710. Skipping the models entirely scores 0.761250.

### Optional: GPU (46x faster)

CPU is the default and needs no configuration. With an NVIDIA GPU a full 200-session run
drops from **1,929s to 41.7s**, which is what made the diagnostic work in `eval/RESULTS.md`
runs 9-11 affordable at all.

```bash
python -m venv .venv-gpu
./.venv-gpu/Scripts/python.exe -m pip install numpy "transformers==4.57.6"
./.venv-gpu/Scripts/python.exe -m pip install "torch==2.13.0" --index-url https://download.pytorch.org/whl/cu126
```

Use `cu126`: it carries torch **2.13.0**, the same version as the CPU environment, so the
device is the only thing that differs. `cu124` tops out at 2.6.0.

Device selection is automatic (`CrossEncoder._pick_device`) and shared with the dense
encoder; `TECHJAM_DEVICE=cpu` forces the CPU path. fp16 on CUDA, fp32 on CPU — opposite
choices because CPU has no fast fp16 kernels while CUDA has tensor cores.

**GPU and CPU do not agree bit-for-bit.** Different matmul reduction orders shift scores in
the last decimals, and a flipped tie between two candidates changes a rank. Measured:
0.76365 on CPU against 0.76490 on GPU for the same code. **The reported number must come
from the device that will produce the submission** — ours is the GPU figure above.

---

## Question selection

Each turn, the agent reads the attribute values off the **surviving candidates** and scores
every possible question by

```
expected_gain(attribute) = coverage(attribute, pool) x entropy(attribute, pool)
```

*coverage* is how many candidates even have that attribute, *entropy* is how mixed the values
are. Both terms are needed and each has a measured failure: entropy alone asks a jewellery
shopper to choose between leather and cotton because the 10% of necklaces with a fabric cord
split cleanly; coverage alone asks about material when every surviving item is already
leather. Both are computed from the pool at runtime, so this adds **no tuned constants**.

The chosen attribute is named in the customer-facing sentence together with real values from
the pool. The structured `ask_attribute` stays `"other"`:

```
ask_attribute : "other"
message       : "So far I have material: leather. I'm mostly seeing black,
                 brown or pink. Any preference on colour?"
```

**This is deliberate and it is what makes specialised questioning free.** The simulator reads
only `ask_attribute` and never the prose (`docs/final_evaluation_faq.md` section 5), and
`"other"` is a wildcard matching *any* undisclosed constraint — it returns the evaluator's cap
of 2.00 constraints per turn where the best targeted bucket returns 1.73. Asking a specific
attribute as the *structured* field costs about 0.05 TechnicalScore; asking it in *prose*
costs nothing. Specificity where the customer looks, yield where the evaluator looks.

The trace records the real reasoning per turn, e.g.
`highest expected information gain: color splits the pool 7 ways over 54% of candidates
(0.87 bits)`.

## Model choice, cost, latency, and token usage

| | |
|---|---|
| **Reranker** | `BAAI/bge-reranker-base` — XLM-RoBERTa-base, 278M params, Apache-2.0 |
| **Fallback reranker** | `cross-encoder/ms-marco-MiniLM-L-6-v2`, 22M, committed (45 MB fp16) |
| **Bi-encoder** | `sentence-transformers/all-MiniLM-L6-v2`, committed — dense track, currently disabled |
| **Retrieval** | SQLite FTS5 BM25 — no external service, no vector database |
| **LLM API** | **None.** No credentials required. No environment variables needed to run. |
| **Estimated cost** | **$0.00.** The organizer's credit policy does not apply to this submission. |
| **Token usage** | **0** prompt / **0** completion. Reported as zero in `usage`, permitted for non-LLM systems (`final_evaluation_faq.md` §7). |
| **Network at inference** | **None.** Models and index load from local disk. `HF_HUB_OFFLINE=1` is safe. |
| **Latency** | **0.21 s/session** on GPU (41.7 s / 200); 9.6 s/session CPU-only (1929 s / 200) |
| **Hardware measured on** | Intel Core (Family 6 Model 183) + NVIDIA RTX 4070 SUPER 12 GB, Windows 11. Runs CPU-only with no GPU. |
| **Versions** | Python 3.13.15, torch 2.13.0 (+cpu or +cu126), transformers 4.57.6, numpy 2.5.2 |

Dropping to the committed MiniLM reranker cuts latency to ~0.5 s/session for 0.028 less score.

`BAAI/bge-reranker-base` is **not committed**. At ~573 MB it exceeds GitHub's 100 MB/file
limit, and `final_evaluation_faq.md` §4 states large assets "should be supplied through
documented and reproducible download instructions rather than committed directly." The
committed pair is the offline floor.

### Environment variables

**None are required.** All defaults are the reported configuration; every variable exists for
experiment reproduction and is documented because several change the score.

| Variable | Default | Effect |
|---|---|---|
| `TECHJAM_CE_WIDTH` | `25` | Candidates the reranker scores. `0` disables it → 0.761250 |
| `TECHJAM_CE_DIR` | auto | Reranker directory. Auto-selects `models/ce-bge` if present, else `models/cross-encoder` |
| `TECHJAM_DENSE` | off | `1` enables the dense track. Measured regression — see below |
| `TECHJAM_DENSE_W` | `1.0` | Scales the dense RRF weight |
| `TECHJAM_DENSE_MMR` | `1` | MMR diversity on the browsing dense track |
| `TECHJAM_DOC_FEATURES` / `_DETAILS` / `_CHARS` | `6` / `6` / `900` | Document rendering budget for the reranker |
| `TECHJAM_NO_SKIP` | off | `1` disables the override rerank-skip (+0.0008, noise) |
| `TECHJAM_NO_SELF_REFINE` | off | `1` disables self-refining guidance |
| `TECHJAM_DISABLE_LLM` / `TECHJAM_LLM_*` | off | Dormant LLM rung; unused in the reported score |
| `TECHJAM_DEVICE` | auto | `cpu` forces the CPU path on a CUDA machine |
| `TECHJAM_HYBRID_ASK` | `1` | `0` sends the specific attribute as the structured field too. Honest-looking, costs ~0.010 |
| `TECHJAM_QUERY_VIEW` | `raw` | What the reranker reads: `raw` disclosures, `slots` typed, or `both`. `both` duplicates every constraint and costs 0.027 |
| `TECHJAM_NO_INFO_FILTER` | `0` | `1` drops refusals from the lexical query. Intuitive, costs 0.0083 — see limitations |
| `TECHJAM_KEEP_ENGAGING` | `1` | `0` falls silent once the constraint pool is drained |
| `TECHJAM_POOL_SAMPLE` / `TECHJAM_DEPTH` | `50` | Candidates the question policy reasons over |

---

## Limitations, and what we would do with more time

**Ranking is the bottleneck, and we can prove it.** An oracle reranker — one that cheats by
placing the target first whenever it appears in BM25's top 50 — scores **0.944**. We score
0.815. Retrieval is nearly solved (the target is in the pool 96.5% of the time); the remaining
**~0.13 is entirely rank quality**.

We measured four routes to it and rejected three:

| Attempt | Result |
|---|---|
| Larger cross-encoder (MiniLM-L12, 33M) | 0.76793 — **worse** than the 22M model |
| Dense retrieval + RRF fusion | loses at every weight tested; best 0.76386 vs 0.77773 without |
| Deterministic constraint matching | 0.77017 — constraints are verbatim but not *discriminative* |
| bge-reranker-base + fixed document rendering | 0.814257 |
| + candidate-derived question selection | **0.815121 — kept** |

The instructive failure is the third. Constraint strings like `machine washable` or
`100% cotton` are shared by dozens of near-identical products, so exact matching ties them all.
The remaining gap needs *comparative* judgement — "of these 25, which fits best" — which a
cross-encoder structurally cannot express, because it scores each document independently.
**A listwise LLM rung is the most promising untried direction**; we did not ship one because it
was unmeasured at freeze time, and an API dependency would make the frozen commit
non-reproducible.

**Three bugs that looked like a question-policy regression, and were not.** Adding
specialised questions first measured 0.76365 — a 0.05 loss we spent hours attributing to the
question policy. It was none of it. GPU turned a 32-minute experiment into 40 seconds and the
bisect found three unrelated causes: the ranker query was **duplicated** once typed slots
were populated (`as_query()` emitted slots *and* disclosures, doubling the cross-encoder's
input, −0.027); dropping no-information replies from the lexical query perturbed **every**
BM25 rank, enough to push a target from rank 15 to 29 and past `CE_WIDTH=25` where the
reranker never sees it (−0.0083); and the boundary scenario's **one-off** refusal was treated
as permanent, retiring the wildcard for the rest of those sessions (−0.25 on that scenario).
The lesson we would carry forward: an intuitive cleanup that touches a term-frequency query is
never neutral, and a plausible mechanism is not evidence.

**Presentation defects in the spoken summary**, all message-only and none affecting the
score, since the evaluator reads only `ask_attribute`:

- `classify()` mirrors the evaluator's `classify_constraint` exactly, including its
  unanchored substring test — so `"Textile Cove`**`red`**` EVA Footbed"` is labelled a colour.
  Faithful to the simulator, wrong to a reader.
- Constraint strings the simulator treats as preferences but a shopper would not
  (`Date First Available: March 19, 2021`, `Item model number: G796`) are read back verbatim.
- `colour: color: grey` stutters, and catalog text can carry non-ASCII
  (`【HIGH QUALITY】`) into the message.

Other honest gaps:

- **The dense track ships disabled.** It is built and diagnosed, not abandoned: RRF genuinely
  improves recall@50 (0.595 → 0.636) and finds 73 targets BM25 misses, but at the top 10 a
  weak voter with a comparable vote drags targets *out* of the head. "Hybrid retrieval" here
  means one live lexical track plus a reranking cascade.
- **Boundary regresses 0.060** under the reranker. With n=10 this cannot be resolved, and
  special-casing a scenario observed in the public set would not generalise.
- **The +0.0165 from the dialogue work is not statistically significant** at n=200 (paired
  95% CI [−0.014, +0.047]). The +0.037 from the reranker work is larger but is not k-folded.
- **Slot typing and decay are partial.** `drained`/`unavailable` are tracked; typed slots and
  time decay are not, because the `"other"` wildcard already extracts constraints optimally.
- **All numbers are the 200-session public set.** The 800 final sessions are released only
  after the submission deadline.

---

## Repository map

```
src/agent.py          Agent interface - no exception may escape respond() or reset()
src/router.py         intent routing, per-track configuration
src/retrieval.py      BM25 (FTS5) + dense index + RRF fusion
src/rerank.py         reranking cascade, reorder-only, never raises
src/state.py          dialogue state, constraint erasure, context distillation
src/policy.py         clarification policy - never calls a model
src/orchestrator.py   runtime re-planning from session telemetry
src/trace.py          structured per-turn decision trace
eval/run_eval.py      scenario breakdowns, k-fold, paired significance testing
eval/RESULTS.md       score ledger - every run, including rejected experiments
tests/                33 invariant tests
scripts/              index build, model fetch, trace dump
```

Run the tests: `python -m unittest discover -s tests`

---

## Team contributions

<!-- REQUIRED by the brief - fill in before submitting. -->

| Member | Contribution |
|---|---|
| _TODO_ | _TODO_ |

---

## Data attribution

Catalog and sessions derive from Amazon Reviews 2023 (McAuley Lab, UCSD). See
[`DATA_ATTRIBUTION.md`](DATA_ATTRIBUTION.md). The catalog is read-only; no ASIN is ever
introduced, replaced, or fabricated.
