# TikTok TechJam 2026 — Track 4: Shopping Copilot

Conversational shopping agent over a frozen 50k-product Amazon Clothing catalog. Given a
simulated customer with a hidden target product, find it and rank it as high as possible,
as early as possible.

**Submission: 1 Sep 2026, 12:00 SGT.** Hard deadline, no late entries.
Internal freeze: 31 Aug, 18:00 SGT. Build window opens 29 Aug, 12:00 SGT.

Not bootstrapped yet? See `BOOTSTRAP.md`. Do that before anything else.

---

## 1. How this is graded — read before optimising anything

**Layer 1 — the judge rubric, which is the actual grade:**

| Criterion | Weight |
|---|---|
| Technical Execution | 35% |
| Innovation & Problem Insight | 20% |
| Impact & Relevance | 20% |
| Feasibility & Practicality | 15% |
| Presentation & Communication | 10% (final event only) |

**Layer 2 — the automated `TechnicalScore`**, computed on 800 private sessions. Objective
backbone of Technical Execution and likely the finalist gate — but roughly a third of the
grade, not the grade.

**Consequence: a submission that maxes the metric and ships a thin README loses to one that
scores slightly lower with a sharp architecture story.** README, Devpost writeup and demo
video are graded artifacts. Budget real time for them.

---

## 2. The automated metric

```
TechnicalScore = 0.50 x HR@10  +  0.30 x MRR  +  0.20 x Efficiency
Efficiency     = clip((11 - MTTC) / 10, 0, 1)
```

Efficiency is linear in MTTC, so the score decomposes per session:

```
hit at rank r, turn t  ->  0.50 + 0.30/r + 0.02 x (11 - t)
miss                   ->  0.00   (miss = turn 11 -> zero on all three terms)
```

| Change | Delta |
|---|---|
| One extra turn | **-0.02** |
| Rank 2 -> 1 | **+0.15** |
| Rank 3 -> 1 | **+0.20** (= 10 turns) |
| Miss -> even the worst hit | **+0.55** (= 27 turns) |

**Non-negotiable consequences**

1. **Accuracy dominates speed.** The 20% Efficiency weight is a trap. Never trade hit
   probability for a faster answer. Bias the policy toward asking.
2. **A hit ends the session and locks the rank.** A weak early top-10 doesn't just score
   badly, it prevents ever doing better. List length is a risk dial: returning `k` items
   caps the worst achievable rank at `k`.
3. **On turn 10, always return the full 10.** A miss is catastrophic.
4. **A crash is a miss is 0.00.**

---

## 3. The four required pillars — judges check for all of them

Implement all four visibly and log their decisions so the demo video can show them working.

### I. Intent Routing & Hybrid Pipeline — `router.py` `retrieval.py` `rerank.py`
- `route(state) -> buying | browsing | mixed`
- Buying: hard constraint filters (price, category, material) + BM25 precision track
- Browsing: dense vector retrieval, diverse, cross-category scenario matching
- Merge routes with RRF -> semantic reranking stage
- Route weights and truncation depth tunable, not hardcoded

### II. Dialog Strategy: Multi-Turn Scenario Evolution — `state.py` `policy.py`
- Information accumulation: incremental slot filling
- Intent override: contradictions **erase and rewrite** slots, they do not append
- Slot decay: older slots weigh less over turns
- Proactive guidance: on over-generality (pool overloaded or score distribution flat), cut
  retrieval short and emit a structured clarification. *A better question is worth more
  than another retrieval call.*

### III. Self-Evolution: Dynamic Context Programming — `orchestrator.py` `trace.py`
**The Innovation 20% lives here.**
- Personalized context distillation: compress dialog history into a compact structured
  profile each turn; never replay raw history into the ranker
- Adaptive orchestration: re-plan strategy at runtime from session telemetry — if the last
  clarification failed to shrink the pool, change tactic (route weights, truncation, or
  stop asking and answer)
- Emit a structured trace of every strategy decision. This is what makes the demo land.

### IV. Evaluation Matrix — `eval/`
- HR@10, MRR, MTTC, Efficiency, TechnicalScore
- **Always broken out by session type: Buying / Browsing / Intent Override / Boundary**

---

## 4. Hard invariants — breaking any of these costs whole sessions

- **No exception may escape `respond()` or `reset()`.** Wrap everything. On internal
  failure, degrade to the last known-good candidate list rather than raising.
- **Every LLM call has a timeout and a non-LLM fallback.** The rule-based + BM25 + dense
  path must work standalone. A paid LLM is explicitly not required to compete.
- **Max 10 turns.** Exceeding it forces termination and scores zero.
- **Only the first 10 unique, catalog-valid `parent_asin` values are scored.** Duplicates
  and invalid IDs are stripped. Numeric scores are ignored — order is the only signal.
- **Exact `parent_asin` matching.** No fuzzy matching.
- **Catalog is strictly read-only.** No structural mutation, no mock ASIN injection.
- **Everything runs in-memory.** `numpy` / `faiss-cpu` in-process only. No Pinecone,
  Weaviate, Qdrant, or any external vector DB server.
- **Never commit API keys or `.env`.** Secrets in git history are disqualifying.
- **The evaluator imports the submission locally.** No server, no URL, no fixed port.
- **Intent Override cannot score before the changed intent appears.** Detect the switch and
  drop stale slots, or those sessions become auto-misses.

---

## 5. Repo map and file ownership

Module boundaries are contracts: `retrieval` never reads dialogue state directly; `policy`
never calls an LLM; `rerank` never issues new retrievals. **The same boundaries are the
concurrency locks — two agents must never hold the same file.**

| Path | Pillar | Owning agent |
|---|---|---|
| `src/agent.py` | interface | **lead only** (you, in the main session) |
| `src/router.py` `src/retrieval.py` `src/rerank.py` | I | `retrieval` |
| `src/state.py` `src/policy.py` | II | `dialog` |
| `src/orchestrator.py` `src/trace.py` `src/cache.py` | III | `orchestrator` |
| `eval/run_eval.py` `eval/RESULTS.md` | IV | `evaluator` |
| `output/README.md` `output/devpost.md` `output/video-script.md` | deliverables | `scribe` |
| `data/` | — | read-only, nobody |

**Catalog fields:** `parent_asin` / `title` / `features` / `details` / `description` /
`categories` / `store` / `average_rating` / `rating_number` / `price`

---

## 6. Working with agents

Six subagents live in `.claude/agents/`. Delegate; do not do their work in the main thread.

| Agent | Use it for |
|---|---|
| `spec-oracle` | Any question about evaluator behaviour. Reads source, never guesses. Run first. |
| `retrieval` | Pillar I. Routing, BM25, dense, RRF, rerank, index build. |
| `dialog` | Pillar II. Slots, override erasure, decay, ask-vs-answer, attribute choice. |
| `orchestrator` | Pillar III. Context distillation, runtime re-planning, trace, cache. |
| `evaluator` | Pillar IV. Runs eval, reports 4 breakdowns + delta, k-fold, regression calls. |
| `scribe` | README, Devpost, video script, architecture writeup. |

**Rules for the lead session**

1. **One agent per module set, one at a time per file.** Run `retrieval` + `dialog` +
   `scribe` in parallel freely — disjoint files. Never run two agents that touch `src/agent.py`.
2. **Every implementation agent hands back a hypothesis + a diff + an eval number**, or an
   explicit "not measurable, serves pillar X because Y". Nothing else is a completed task.
3. **`evaluator` is the only agent allowed to declare an improvement.** Implementation
   agents may run `make eval-fast` to sanity-check; the number that counts comes from
   `evaluator`, with all four breakdowns.
4. **Merge conflicts are your problem, not theirs.** After parallel agents return, you
   integrate in `src/agent.py` and re-run `make eval` once on the combined state.
5. Give each agent the **pillar section and the invariants it must not break** in the
   prompt. They do not inherit your conversation.
6. If an agent burns >2 attempts without a number moving, stop it and revert. Time is the
   scarcest resource; the deadline is fixed.

---

## 7. Commands

```bash
make eval              # 200 public sessions -> score + 4 breakdowns + delta
make eval-fast         # No-LLM mode, cached only — default while iterating
make eval-fold N=5     # K-fold on the public set (the number that generalises)
make index             # Rebuild BM25 + embedding index
make check             # Lint + types + unit tests
```

Slash commands in `.claude/commands/`: `/eval` `/pillars` `/spec` `/ship`

**Score ledger lives in `eval/RESULTS.md`** — every run appended with commit hash and all
four breakdowns. Do not put running results in this file; it is loaded every turn.

**Baseline (BM25 starter):** TechnicalScore **0.10671** — HR@10 0.125 / MRR 0.068034 / MTTC 9.81 / Eff 0.119
**Current best:** `TODO — set after first local run`

---

## 8. How to make a change

1. State the hypothesis and which term it targets (HR@10, MRR, Efficiency, or a judge
   criterion).
2. Implement the smallest version that tests it.
3. Run `make eval`. Report the delta **and all four breakdowns**.
4. Keep it only if the number moved, or if it demonstrably serves a pillar judges check.
   Revert otherwise.

**Never claim an improvement without an eval number.** "This should help" is not evidence.
An aggregate that rises while one breakdown collapses is a regression.

Commit after every kept change, with the score in the message:
`feat(retrieval): RRF fusion — TS 0.312 -> 0.341 (HR 0.41, MRR 0.19)`

---

## 9. Anti-overfitting

Tuning on **200 public** sessions, graded on **800 private** ones, disjoint by both user
and target product.

- **More than ~3 tuned thresholds will not generalise.** Prefer simple policies.
- Run `make eval-fold` before trusting any threshold.
- Gains under +0.01 are noise at n=200.
- Never special-case anything observed in a specific public session.

---

## 10. Cost discipline

A full eval run is up to 200 sessions x 10 turns x 1-2 LLM calls ~ **4,000 calls**, run
dozens of times over the weekend. Organisers provide no keys or credits.

- Cache on `(session_id, turn, message_hash)`.
- `make eval-fast` for iteration; full LLM eval only to confirm.
- Log token usage per run. Feasibility (15%) rewards proportionate resource use — a cheap,
  fast system is a scoring advantage, not just a budget one.

---

## 11. Out of scope — do not build

UI/UX / training or fine-tuning foundation models / external vector DB clusters /
multimodal (text only) / real transactions / catalog mutation / production infrastructure.

**In scope:** intent detection, heterogeneous retrieval routing, slot decay, dynamic
truncation, runtime-adaptive memory, prompt strategy, local scoring logic.

---

## 12. How the simulated customer actually works — answered from `evaluator/local_evaluator.py`

The customer is **deterministic Python, not an LLM**. Read `customer_reply` (L166-185) and
`intent_card` (L52-71) before designing anything.

**The hidden target's constraints are derived from the target product itself.**
`intent_card(product)` builds `hard_constraints` = first two of [material regex hit,
`color: X`, feature/detail strings, `budget around $P`] and `soft_preferences` = the next two.
Nothing else exists.

**Opening message (L154-163)** is generated from the target:
- buying: `I'm looking for {coarse_category}. A key requirement is: {hard_constraints[0]}.`
- browsing: `I'm looking for {coarse_category}, but I'm still exploring.`
- intent_override: `I'm looking for {coarse_category}. {soft_preferences[-1]}`

`coarse_category` = last two comma-split tokens of the target's `categories` (L126-134).

**`ask_attribute` is the only information faucet (L166-185).**
- `null` -> the customer reveals **nothing**: "Ask me about one specific attribute."
- a valid attribute -> reveals up to **2** not-yet-disclosed constraints whose
  `classify_constraint()` bucket equals it (L137-151 — a pure function, so mirror it).
- no match -> "I don't have an additional preference for X." Turn wasted.

**The starter scores 0.125 because it passes `null` every turn and therefore never learns
anything.** It reruns the same query ten times. Attribute selection is the highest-leverage
code in the repo — confirmed, not assumed.

**Turn loop (L238-268)**
- Hit checked every turn; a hit **breaks immediately** and locks the rank.
- `recommendations` may be empty or shorter than 10 and the session continues. **The
  list-length risk dial is real** — returning k items caps the worst rank at k.
- **Exceptions are caught by the evaluator (L239-244)** and become an empty turn, not a lost
  session. No per-call time limit locally. Still wrap everything: a crash costs a turn
  (-0.02) and the private harness may be stricter.
- Only the first 10 unique in-catalog `parent_asin` values count (L95-109).

**intent_override (30/200):** `override_applied` starts **False** (L234) and the hit check
requires it (L252). **Hits cannot score before the override turn, which is 3 or 4** (L76-86),
seeded deterministically from `sample_id`. The override message is
`Actually, ignore my earlier preference. What I need is: {hard_constraints[0]}.` — that
sentence carries the real constraint. Erase the old slot on sight.

**boundary (10/200):** the **first** attribute question returns "I don't have a preference for
{attribute}; please use your judgment" and reveals nothing (L168-169). Exactly once. Absorb it
and keep asking.

**Public mix:** buying 80 / browsing 80 / intent_override 30 / boundary 10.
Difficulty easy 80 / medium 90 / hard 30. All 200 `clothing`.

**Highest-value open hypothesis.** The opening message is a deterministic function of the
target product, so the same function can be run over all 50k catalog items offline and the
observed message matched back. Verify on the public set before betting on it. Model the
simulator — but still build all four pillars: the rubric is 65% non-metric, and a submission
that only exploits the simulator has no architecture story to tell.

---

## 13. Working style

- Concise. Bullets over paragraphs. No preamble before tool calls.
- Show the plan before non-trivial changes; just do trivial ones.
- Ask only when the answer changes the score or is hard to reverse. Otherwise pick the
  sensible default, state the assumption, and continue — the clock is running.
- **Do not refactor during the sprint.** Working and measured beats clean and unproven.
- Cite sources when researching retrieval or ranking methods.

---

## 14. Deliverables — graded, not paperwork

- [ ] **Public GitHub repo** (private during dev, flip public before submitting)
- [ ] **README:** overview / setup / reproduction steps / limitations and what you'd
      improve with more time / team member contributions
- [ ] **Devpost description:** how it addresses the problem statement / dev tools / APIs /
      libraries and frameworks / datasets and assets — enumerate each explicitly
- [ ] **Demo video** on YouTube, public, linked in Devpost. Backend walkthrough accepted —
      show API usage, inference examples, and the Pillar III strategy trace
- [ ] Architecture explanation mapping the build to all four pillars
- [ ] No secrets in source or git history
