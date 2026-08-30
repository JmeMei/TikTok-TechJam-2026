"""Evaluation matrix (Pillar IV) -- the referee.

    python -m eval.run_eval                 full 200-session run + 4 breakdowns + delta
    python -m eval.run_eval --folds 5       k-fold, for deciding whether a gain generalises
    python -m eval.run_eval --baseline X    compare against a specific results file

Wraps the OFFICIAL evaluator and never modifies it (submission_rules.md forbids that).
Everything here is reporting on top of `evaluator.local_evaluator.evaluate`.

Why k-fold matters: tuning happens on 200 public sessions and grading on 800 private ones,
disjoint by user AND target product. At n=200 a HR@10 of ~0.88 carries a standard error of
about 0.023, so a +0.01 TechnicalScore move is inside the noise. K-fold shows whether a gain
is stable across subsets or an artifact of a few sessions.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl
from starter.agent import Agent

SCENARIOS = ("buying", "browsing", "intent_override", "boundary")


def technical_score(hit_rate: float, mrr: float, mttc: float) -> float:
    efficiency = max(0.0, min(1.0, (11.0 - mttc) / 10.0))
    return 0.50 * hit_rate + 0.30 * mrr + 0.20 * efficiency


def summarize(sessions: list[dict]) -> dict:
    if not sessions:
        return {"n": 0, "hr": 0.0, "mrr": 0.0, "mttc": 11.0, "ts": 0.0}
    hit_rate = sum(int(s["hit"]) for s in sessions) / len(sessions)
    mrr = statistics.fmean(s["reciprocal_rank"] for s in sessions)
    mttc = statistics.fmean(
        s["first_hit_turn"] if s["first_hit_turn"] is not None else 11 for s in sessions
    )
    return {
        "n": len(sessions),
        "hr": hit_rate,
        "mrr": mrr,
        "mttc": mttc,
        "ts": technical_score(hit_rate, mrr, mttc),
    }


def report(result: dict, baseline: dict | None, elapsed: float) -> None:
    sessions = result["sessions"]
    overall = summarize(sessions)

    print("\n" + "=" * 66)
    print(f"  TechnicalScore  {overall['ts']:.5f}")
    print(f"  HR@10 {overall['hr']:.4f}   MRR {overall['mrr']:.4f}   "
          f"MTTC {overall['mttc']:.3f}   Eff {max(0.0, min(1.0, (11 - overall['mttc']) / 10)):.4f}")
    print("=" * 66)

    if baseline:
        previous = summarize(baseline["sessions"])
        delta = overall["ts"] - previous["ts"]
        verdict = "KEEP" if delta >= 0.01 else "NOISE" if delta > -0.01 else "REGRESSION"
        print(f"  vs baseline: {previous['ts']:.5f} -> {overall['ts']:.5f}  "
              f"({delta:+.5f})  [{verdict}]")
        if abs(delta) < 0.01:
            print("  NOTE: |delta| < 0.01 is inside noise at n=200 (CLAUDE.md section 9).")
        print("=" * 66)

    # Per-scenario breakdown. An aggregate that rises while one scenario collapses is a
    # regression, so all four are always shown.
    print(f"\n  {'scenario':<16} {'n':>4} {'HR@10':>8} {'MRR':>8} {'MTTC':>7} {'TS':>8}", end="")
    print(f" {'dTS':>8}" if baseline else "")
    print("  " + "-" * (54 + (9 if baseline else 0)))
    for name in SCENARIOS:
        group = summarize([s for s in sessions if s["scenario_type"] == name])
        if not group["n"]:
            continue
        line = (f"  {name:<16} {group['n']:>4} {group['hr']:>8.4f} {group['mrr']:>8.4f} "
                f"{group['mttc']:>7.3f} {group['ts']:>8.4f}")
        if baseline:
            before = summarize([s for s in baseline["sessions"] if s["scenario_type"] == name])
            line += f" {group['ts'] - before['ts']:>+8.4f}"
        print(line)

    usage = result.get("reported_token_usage", {})
    print(f"\n  wall clock {elapsed:.1f}s   tokens {usage.get('total_tokens', 0)}")

    if baseline:
        regressed = [
            name for name in SCENARIOS
            if summarize([s for s in sessions if s["scenario_type"] == name])["ts"]
            < summarize([s for s in baseline["sessions"] if s["scenario_type"] == name])["ts"] - 0.02
        ]
        if regressed:
            print(f"  WARNING: scenario regression in {', '.join(regressed)} "
                  f"-- an aggregate gain that hides one is still a regression.")


def kfold(sessions: list[dict], folds: int) -> None:
    """Split the public set and report per-fold spread.

    A change that only wins on some folds will not survive the private 800.
    """
    buckets: list[list[dict]] = [[] for _ in range(folds)]
    # Stratify by scenario so every fold keeps the 40/40/15/5 mix.
    for name in SCENARIOS:
        group = [s for s in sessions if s["scenario_type"] == name]
        for i, session in enumerate(group):
            buckets[i % folds].append(session)

    scores = [summarize(b)["ts"] for b in buckets if b]
    print(f"\n  {folds}-fold TechnicalScore")
    print("  " + "-" * 40)
    for i, score in enumerate(scores, start=1):
        print(f"    fold {i}   {score:.5f}   (n={len(buckets[i - 1])})")
    if len(scores) > 1:
        spread = max(scores) - min(scores)
        print(f"    mean    {statistics.fmean(scores):.5f}")
        print(f"    stdev   {statistics.stdev(scores):.5f}")
        print(f"    spread  {spread:.5f}")
        if spread > 0.05:
            print("    NOTE: wide spread -- treat any gain smaller than this as unproven.")


def main() -> int:
    parser = argparse.ArgumentParser(description="TechJam evaluation matrix")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--output", default="results.json")
    parser.add_argument("--baseline", default="results_v1.json")
    parser.add_argument("--folds", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0, help="first N sessions only (smoke test)")
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    if args.limit:
        samples = samples[: args.limit]
    catalog_ids, categories, products = catalog_index(args.catalog)

    start = time.perf_counter()
    result = evaluate(Agent(args.catalog), samples, catalog_ids, categories, products)
    elapsed = time.perf_counter() - start

    Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

    baseline = None
    baseline_path = Path(args.baseline)
    if baseline_path.exists() and not args.limit:
        try:
            baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            baseline = None

    report(result, baseline, elapsed)
    if args.folds:
        kfold(result["sessions"], args.folds)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
