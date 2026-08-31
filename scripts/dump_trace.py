"""Dump per-turn strategy traces for the demo walkthrough and for analysis.

    python scripts/dump_trace.py --limit 12 --out traces/trace.json
    python scripts/dump_trace.py --scenario intent_override --limit 5 --print

The brief accepts "a walkthrough video showing API usage, inference examples, or result
analysis" in place of a front end, and requires "one demonstrated multi-turn session".
This produces exactly that, in the same TurnTrace schema the Flask app renders -- one
schema, two consumers.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Running a file inside scripts/ puts scripts/ on sys.path, not the repo root, so the
# `evaluator` and `src` packages are invisible. Prepend the root so both
# `python scripts/dump_trace.py` and `python -m scripts.dump_trace` work.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluator.local_evaluator import catalog_index, evaluate, load_jsonl  # noqa: E402
from src.agent import Agent  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Dump agent decision traces")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--out", default="traces/trace.json")
    parser.add_argument("--limit", type=int, default=12)
    parser.add_argument("--scenario", default="", help="buying|browsing|intent_override|boundary")
    parser.add_argument(
        "--sample",
        default="",
        help="trace specific sample_id(s), comma-separated, e.g. public_0004. "
             "Overrides --scenario and --limit; output follows the order given.",
    )
    parser.add_argument("--print", action="store_true", dest="show")
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    if args.sample:
        # Named sessions, in the order requested -- so a demo can land on one clean
        # session instead of scrolling past the ones that happen to precede it.
        wanted = [s.strip() for s in args.sample.split(",") if s.strip()]
        index = {s["sample_id"]: s for s in samples}
        missing = [w for w in wanted if w not in index]
        if missing:
            print(f"unknown sample_id(s): {', '.join(missing)}")
            return 1
        samples = [index[w] for w in wanted]
    else:
        if args.scenario:
            samples = [s for s in samples if s["scenario_type"] == args.scenario]
        samples = samples[: args.limit]
    if not samples:
        print("no matching sessions")
        return 1

    catalog_ids, categories, products = catalog_index(args.catalog)
    agent = Agent(args.catalog)
    result = evaluate(agent, samples, catalog_ids, categories, products)

    by_session: dict[str, list[dict]] = {}
    for trace in agent.traces:
        by_session.setdefault(trace.session_id, []).append(trace.as_dict())

    # Sessions are keyed by a fresh uuid4 in the evaluator, so pair them up positionally
    # with the per-session results in order.
    ordered = list(by_session.values())
    payload = []
    for index, session in enumerate(result["sessions"]):
        payload.append({
            "sample_id": session["sample_id"],
            "scenario_type": session["scenario_type"],
            "hit": session["hit"],
            "first_hit_turn": session["first_hit_turn"],
            "best_rank": session["best_rank"],
            "target": str(samples[index]["ground_truth"]["parent_asin"]),
            "turns": ordered[index] if index < len(ordered) else [],
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"wrote {len(payload)} sessions -> {out}")

    if args.show:
        for entry in payload:
            status = (
                f"HIT rank {entry['best_rank']} turn {entry['first_hit_turn']}"
                if entry["hit"] else "MISS"
            )
            print(f"\n=== {entry['sample_id']} [{entry['scenario_type']}] {status}")
            for turn in entry["turns"]:
                decisions = " ".join(f"{d['kind']}={d['choice']}" for d in turn["decisions"])
                print(
                    f"  t{turn['turn']:<2} route={turn['route']:<9} "
                    f"pools={turn['pool_sizes']} stage={turn['stage_reached']:<13} "
                    f"ask={str(turn['ask_attribute']):<6} {decisions}"
                )
    return 0


if __name__ == "__main__":
    sys.exit(main())
