.PHONY: eval eval-fast eval-fold index check demo help
.DEFAULT_GOAL := help

PY ?= python
N  ?= 5

help:
	@echo "eval       Full 200-session run through the official evaluator + 4 breakdowns"
	@echo "eval-fast  Iteration mode: cached, LLM ranking stage disabled"
	@echo "eval-fold  K-fold on the public set (make eval-fold N=5)"
	@echo "index      Rebuild the dense embedding index (one-time, ~minutes)"
	@echo "check      Import-boundary checks + unit tests"
	@echo "demo       Launch the Flask walkthrough (dev only, not part of scoring)"

# The official evaluator, unmodified. This is the number that counts.
eval:
	$(PY) -m evaluator.local_evaluator

# Iteration mode. Skips the LLM ranking stage and reuses cached scores.
eval-fast:
	TECHJAM_DISABLE_LLM=1 $(PY) -m evaluator.local_evaluator --output results-fast.json

# K-fold. Required before trusting any tuned threshold (CLAUDE.md section 9).
eval-fold:
	$(PY) -m eval.run_eval --folds $(N)

index:
	$(PY) scripts/build_index.py

check:
	$(PY) -m unittest discover -s tests -v
	@echo "--- import boundary: src/ must not reach app/ or flask ---"
	@! grep -rniE "\bflask\b|from app|import app" src/ || (echo "FAIL: src/ imports the demo app" && exit 1)
	@echo "OK"

demo:
	$(PY) app/server.py
