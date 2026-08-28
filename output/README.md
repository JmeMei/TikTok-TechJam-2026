# Shopping Copilot — TikTok TechJam 2026, Track 4

A conversational shopping agent for the TechJam Conversational E-Commerce Search Challenge.
Given an anonymized preference profile and a short customer message, the agent has at most
10 turns to surface a hidden target product from a frozen 50,000-item Amazon Clothing catalog.

> **Status: work in progress.** This README documents the system as it exists today, not as
> planned. Sections marked *Not yet built* are honest gaps, not oversights.

---

## Results

Measured on the 200 labelled public sessions with `evaluator/local_evaluator.py`, unmodified.

| Run | System | TechnicalScore | HR@10 | MRR | MTTC | Efficiency |
|---|---|---|---|---|---|---|
| 0 | Provided BM25 starter (baseline) | 0.10671 | 0.1250 | 0.0680 | 9.81 | 0.119 |
| 1 | **Current** | **0.75040** | **0.8750** | **0.5400** | **3.46** | **0.755** |

By scenario:

| Scenario | n | HR@10 run 0 | HR@10 run 1 | MTTC run 0 | MTTC run 1 |
|---|---|---|---|---|---|
| buying | 80 | 0.2375 | 0.8875 | 8.63 | 2.98 |
| browsing | 80 | 0.0250 | 0.8625 | 10.75 | 3.45 |
| intent_override | 30 | 0.1333 | 0.8667 | 10.07 | 4.60 |
| boundary | 10 | 0.0000 | 0.9000 | 11.00 | 3.90 |

Every scenario improved; none regressed. Full run history: [`eval/RESULTS.md`](../eval/RESULTS.md).

**Cost: zero.** No LLM API is called. The system is pure Python standard library and runs a
full 200-session evaluation in well under a minute on a laptop. Reported token usage is 0.

---

## The core insight

The baseline scores 0.107 not because its retrieval is weak, but because **it never collects
any information**.

The session simulator in `evaluator/local_evaluator.py` gates all disclosure behind the
`ask_attribute` field. From `customer_reply` (L166-185):

- `ask_attribute: null` -> the customer replies *"Ask me about one specific attribute"* and
  discloses nothing.
- a valid attribute -> the customer discloses up to two not-yet-revealed constraints whose
  `classify_constraint()` bucket matches.

The starter sends `null` on every one of its ten turns, so it re-runs the same query against
the same handful of words from the opening message, ten times. This is catastrophic for
`browsing` sessions, whose opening message (*"I'm looking for X, but I'm still exploring"*)
contains no constraints at all — hence HR@10 of 0.025 there.

Two changes follow directly:

1. **Accumulate the transcript.** Retrieve over everything the customer has said, not only
   the newest sentence.
2. **Always ask.** Setting `ask_attribute` opens the faucet. `"other"` is used because it
   matches *any* undisclosed constraint rather than one bucket, and the simulator holds only
   four constraints total (two hard, two soft) — so a small number of questions drains it.

Diff: `starter/agent.py`, 12 insertions, 5 deletions. Result: 0.10671 -> 0.75040.

---

## Architecture against the four required pillars

| Pillar | Status | Where |
|---|---|---|
| I. Intent routing & hybrid retrieval | **Partial** — single BM25 track (SQLite FTS5) over title/categories/features/details/store/description with field weighting. No buying/browsing route split, no dense retrieval, no RRF fusion. | `starter/agent.py` |
| II. Multi-turn dialog strategy | **Partial** — per-session transcript accumulation and an unconditional clarification each turn. No structured slots, no intent-override erasure, no slot decay, no over-generality detection. | `starter/agent.py` |
| III. Self-evolution / dynamic context programming | **Not yet built** — no context distillation, no runtime re-planning, no decision trace. | — |
| IV. Evaluation matrix | **Done** — official evaluator run unmodified; all four scenario breakdowns plus a versioned score ledger with per-change deltas and verdicts. | `eval/RESULTS.md` |

Target structure (module boundaries as contracts, so `retrieval` never reads dialogue state
and `policy` never calls an LLM) is specified in [`CLAUDE.md`](../CLAUDE.md).

---

## Setup and reproduction

Python 3.10+. No API keys, no network access at runtime, no external services.

```bash
git clone <this-repo>
cd <this-repo>

# Catalog (19 MB) from the organiser participant kit release
curl -L -O https://github.com/TechJam2026/techjam-conversational-search/releases/download/participant-kit/catalog.jsonl.gz
curl -L -O https://github.com/TechJam2026/techjam-conversational-search/releases/download/participant-kit/SHA256SUMS
sha256sum -c SHA256SUMS
gzip -dk catalog.jsonl.gz && mv catalog.jsonl data/catalog.jsonl

python3 -m evaluator.local_evaluator
```

Expected: `"recommended_technical_score": 0.750401`. Full per-session output in `results.json`.
Windows PowerShell equivalents are in [`BOOTSTRAP.md`](../BOOTSTRAP.md).

The run is deterministic — the simulator seeds its RNG from `sample_id`, and retrieval is
BM25, so the score is reproducible exactly.

---

## Limitations, and what we would do with more time

Stated plainly, because these are real.

- **The current gain models the simulator rather than the shopper.** Disclosure behaviour was
  derived by reading `customer_reply`. This is legitimate — the private 800 sessions run the
  same evaluator — but it is an information-collection result, not a retrieval one, and it
  would not transfer to a human shopper who does not answer on cue.
- **Ranking, not recall, is now the bottleneck.** MRR 0.540 against HR@10 0.875 means the
  average hit lands near rank 2. Rank 2 -> 1 is worth +0.15 per session; a reranking stage
  over the retrieved top ~50 is the largest remaining lever.
- **25 of 200 sessions still miss entirely.** Each is worth 0.55. These need diagnosis, not
  tuning.
- **`ask_attribute` is unconditional.** It should be chosen to maximise expected disclosure,
  and suppressed once the constraint set is exhausted. `classify_constraint` is a pure
  function and can be mirrored to predict which attribute pays.
- **No intent-override handling.** Contradictions currently append to the transcript instead
  of erasing the stale constraint, which pollutes the query on those 30 sessions.
- **Efficiency is close to its floor.** MTTC 3.46, and override sessions cannot score before
  turn 3-4 by construction. Further speed work is not worth its opportunity cost.
- **Single retrieval track.** Dense embeddings and RRF fusion are specified but unbuilt;
  lexical BM25 alone will struggle on browsing sessions phrased without catalog vocabulary.

---

## Dev tools, libraries, datasets

- **Language:** Python 3.10+
- **Libraries:** Python standard library only — `sqlite3` (FTS5 full-text index, in-memory),
  `json`, `re`. No third-party runtime dependencies.
- **LLM APIs:** none. Zero tokens, zero cost, zero latency from external calls.
- **Vector DB:** none. Everything runs in-process.
- **Dataset:** frozen 50,000-product `Clothing_Shoes_and_Jewelry` catalog and 200 labelled
  public sessions from the organiser participant kit, derived from Amazon Reviews 2023
  (McAuley Lab, UCSD). See `DATA_ATTRIBUTION.md`. Catalog is read-only and unmodified;
  integrity verified against the published SHA-256 checksum.
- **Tooling:** git, Claude Code (agent-assisted development).

---

## Team

| Member | Contribution |
|---|---|
| _TODO_ | _TODO_ |
