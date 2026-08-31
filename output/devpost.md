# Shopping Copilot — Devpost Submission

**Track 4: Conversational E-Commerce Search**
TechnicalScore **0.814257** on the 200-session public set — 7.6x the provided baseline —
with **no LLM, no API key, and no network access at inference.**

---

## Inspiration

The provided baseline scores 0.107, and reading the evaluator explains why in one line: it
sends `ask_attribute: null` on every one of its ten turns. The simulated customer answers a
null attribute with *"Ask me about one specific attribute"* and reveals nothing. So the
baseline re-runs the same query ten times against a customer who is *willing to answer
questions it never asks*.

That reframed the problem for us. This is not a search-relevance task with a conversation
bolted on. It is an **information-acquisition** task: the score is decided by what you can get
the customer to tell you, and how well you use it before the turn budget runs out.

## What it does

A conversational shopping agent that finds a hidden target product in a frozen 50,000-item
Amazon Clothing catalog within at most 10 turns. Each turn it decides — from dialogue state,
not from a fixed script — whether to ask a clarifying question, return a ranked list, or both,
and emits a structured trace of every strategy decision it made and the signal that triggered it.

## How it addresses the problem statement

**Intent routing and a hybrid pipeline.** `router.py` classifies each turn as buying, browsing
or mixed and hands each track its own weights and truncation depth from configuration rather
than literals. A lexical BM25 track (SQLite FTS5) and a dense vector track are fused with
Reciprocal Rank Fusion, then reordered by a cross-encoder cascade that degrades silently
through three rungs rather than failing.

**Multi-turn dialogue strategy.** `state.py` accumulates disclosed constraints across turns
and — critically — **erases** them when contradicted. On an intent-override session the
customer retracts their opening preference; we detect the retraction, drop the stale
constraint, and promote the replacement to lead the query instead of appending it. `policy.py`
decides when a question can no longer pay and commits the remaining turns to ranking.

**Self-evolution / dynamic context programming.** `orchestrator.py` re-plans at runtime from
session telemetry: it compresses dialogue history into a compact structured profile each turn
(raw history is never replayed into the ranker), stops asking once two consecutive questions
yield nothing, and changes ranking tactics based on what the session has become. Every
decision lands in a structured trace.

**Evaluation matrix.** `eval/run_eval.py` reports HR@10, MRR, MTTC, Efficiency and
TechnicalScore broken out by all four scenario types, with stratified k-fold and **paired
per-session significance testing** — so we can tell a real gain from noise at n=200.

## How we built it

Measurement-first. Every change had to state a hypothesis, name the metric term it targeted,
and produce a number — or be reverted. `eval/RESULTS.md` is the ledger: **eight runs,
including four documented negative results we chose to keep rather than quietly delete.**

Three findings did most of the work:

**1. The wildcard question.** `customer_reply` matches `attribute == "other"` *or* a specific
bucket. The left side short-circuits, so `"other"` is a legal wildcard matching *any*
undisclosed constraint — and the simulator holds only four. Two questions drain it entirely.
`0.107 → 0.750`.

**2. Erase, don't accumulate.** On override sessions we were carrying the retracted preference
into every subsequent query, where it *led* the ranker's input: `Dresses. color: pink | 100%
polyester | cotton` — decoy first, real constraint last. BM25 tolerated this by diluting it
across dozens of OR'd terms; a cross-encoder concentrated on it and ranked the wrong intent.
`0.760 → 0.778`.

**3. The reranker could not see the field it was being asked about.** Our document rendering
gave the model `title + price + features[:2]` and **no `details`** — while the simulator draws
constraints from `features` *and* `details`. It was being asked to match text the document did
not contain. Adding the field: `0.778 → 0.814`.

## Challenges we ran into

**A 20-session smoke test lied to us, in sign.** It said our reranking cascade gained +0.020.
The full 200 said it *lost* 0.061. At n=20 the intent-override scenario gets four sessions —
and that was the scenario deciding the question. We now measure nothing on subsets.

**A build artifact silently changed the system.** Committing the dense index flipped
`DenseIndex.available` to `True`, switching on a retrieval track that had never been measured
and costing 0.065. It shipped as a side effect of adding a file.

**We nearly bought a bigger model to fix a missing field.** A 12.6x larger reranker bought
+0.004. Showing the *existing* 22M model one absent field bought +0.037. The cheap model on a
correct input beat the expensive model on a broken one.

## Accomplishments we're proud of

- **0.814257** — 7.6x the baseline, at **$0.00** inference cost with no network dependency.
- **Graceful degradation, measured, not asserted:** 0.814 with full setup → 0.787 on a plain
  clone → 0.761 with no models at all. Verified by deleting `models/` and re-running.
- **We know our own ceiling.** An oracle reranker over the pool we already retrieve scores
  0.944, which proves retrieval is nearly solved (the target is in the pool 96.5% of the time)
  and that the remaining 0.13 is rank quality alone.
- **Honest statistics.** We report that our dialogue gain has a paired 95% CI of
  [−0.014, +0.047] and is *not* significant at n=200, rather than claiming it.

## What we learned

Model capacity is the last thing to reach for. Two of our four ranking experiments failed
because of what the model was *shown*, not how large it was — and a bigger model in one case
scored *worse*. Read the evaluator, fix the representation, and only then consider parameters.

We also learned when a clean idea is wrong. Constraints are guaranteed verbatim, so
deterministic string matching should have been free and perfect. It scored 0.770, losing to
the neural reranker by 0.044 — because `machine washable` and `100% cotton` are shared by
dozens of near-identical products. The constraints are verbatim but **not discriminative**.

## What's next

The remaining 0.13 needs *comparative* judgement — "of these 25, which fits best" — which a
cross-encoder structurally cannot express, since it scores each document independently. A
**listwise LLM rung** is the most promising untried direction. We deliberately did not ship one:
it was unmeasured at freeze time, and an external API would make the frozen commit
non-reproducible for the final evaluation.

---

## Built with

**Languages**
- Python 3.13.15 (3.10+ supported)

**Retrieval and ranking**
- SQLite **FTS5** with the `bm25()` ranking function — lexical retrieval, in-process, stdlib
- **Reciprocal Rank Fusion (RRF)** — multi-track fusion, implemented from scratch
- **Maximal Marginal Relevance (MMR)** — diversity for the browsing track, from scratch

**Models** (all local, all permissively licensed, no API)
- **`BAAI/bge-reranker-base`** — primary reranker. XLM-RoBERTa-base, 278M params, Apache-2.0
- **`cross-encoder/ms-marco-MiniLM-L-6-v2`** — fallback reranker, 22M params, committed
- **`sentence-transformers/all-MiniLM-L6-v2`** — bi-encoder for the dense track, 384-dim

**Libraries and frameworks**
- **PyTorch** 2.13.0+cpu — CPU-only inference
- **Hugging Face Transformers** 4.57.6 — model loading and tokenization
- **Hugging Face Hub** — setup-time model download only, never at inference
- **NumPy** 2.5.2 — dense index (50000 x 384 fp16 matrix, in-memory)
- **Flask** 3.1.3 — *development only*, an optional demo walkthrough. A build check fails if
  `src/` ever imports it.
- **unittest** (stdlib) — 33 invariant tests

**APIs**
- **None.** No LLM API, no external service, no vector-database server. No credentials are
  required to run or reproduce this submission.

**Datasets and assets**
- **Amazon Reviews 2023**, `Clothing_Shoes_and_Jewelry` category (McAuley Lab, UCSD) — the
  frozen 50,000-product catalog provided by the organizer, SHA-256 verified, strictly
  read-only. See `DATA_ATTRIBUTION.md`.
- **200 labelled public sessions** provided by the organizer, used for development only.
- **Derived artifacts** (offline preprocessing, permitted by `final_evaluation_faq.md` §4): an
  in-memory FTS5 index built at startup, and a precomputed 38.4 MB dense embedding matrix.
- No external data was used to reconstruct evaluation labels.

**Tools**
- Git / GitHub, GNU Make, Hugging Face Hub CLI

---

## Disclosure (required)

| | |
|---|---|
| Model choice | `BAAI/bge-reranker-base` (278M, local) + BM25; no LLM |
| Estimated cost | **$0.00** — no paid API used at any point |
| Token usage | **0** prompt / **0** completion (non-LLM system) |
| Latency | ~7.9 s/session, ~2.7 s/turn, single-threaded CPU |
| Network dependencies | **None at inference.** Setup-time model download only |
| Fallback behavior | 3 measured tiers: 0.814 → 0.787 (no fetched model) → 0.761 (no models) |
| Hardware | Intel Core, Windows 11, CPU-only, no GPU |
| Reported results | 200-session public set. The 800 final sessions release after the deadline |

---

## Try it out

- **Repository:** <!-- TODO: public GitHub URL -->
- **Demo video:** <!-- TODO: public YouTube URL -->

```bash
python -m evaluator.local_evaluator     # -> recommended_technical_score: 0.814257
```
