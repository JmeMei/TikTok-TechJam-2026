---
description: Run the official evaluator and get a refereed verdict with all four breakdowns
---
Delegate to the `evaluator` subagent.

Run `make eval` (or `python3 -m evaluator.local_evaluator` if the Makefile target is not
wired yet). Report TechnicalScore, HR@10, MRR, MTTC, Efficiency, the four scenario
breakdowns (Buying / Browsing / Intent Override / Boundary), token usage, wall time, and
the delta versus the last entry in `eval/RESULTS.md`.

Apply the verdict rules: aggregate up but a breakdown collapsed = REGRESSION; under +0.01 =
NOISE; new tuned threshold = must pass `make eval-fold N=5` first. Append the run to
`eval/RESULTS.md` with the commit hash. Report crashes separately and loudly.

$ARGUMENTS
