---
description: Audit the repo against the four required pillars and the hard invariants
---
Audit the current codebase. Do not write code.

For each pillar, state IMPLEMENTED / PARTIAL / MISSING with the file and function that
implements it:

I.   Intent routing (buying/browsing/mixed), constraint filter + BM25 track, dense track,
     RRF fusion, semantic rerank, tunable weights and truncation
II.  Slot accumulation, intent-override erasure, slot decay, over-generality detection,
     ask-vs-answer, ask_attribute selection
III. Context distillation, runtime re-planning from telemetry, structured decision trace
IV.  Eval harness with all four scenario breakdowns

Then check every hard invariant in CLAUDE.md section 4, especially: no exception escapes
respond()/reset(); every LLM call has a timeout and a non-LLM fallback; turn 10 always
returns 10 recommendations; only unique valid catalog parent_asins; no external vector DB;
no secrets in source or git history (`git log -p | grep -iE "api[_-]?key|secret|sk-"`).

Output a table and a prioritised gap list ordered by expected score impact. Name the agent
that should fix each gap.
