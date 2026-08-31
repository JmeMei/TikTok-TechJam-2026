"""Print the literal conversation between the agent and the simulated customer.

    python scripts/dump_dialogue.py --sample public_0004
    python scripts/dump_dialogue.py --scenario boundary --limit 2

`dump_trace.py` shows the agent's *decisions*; this shows what the customer would
actually read. Both are needed: a trace can look correct while the dialogue reads badly,
which is exactly the failure this was written to catch.
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from evaluator.local_evaluator import (  # noqa: E402
    MAX_TURNS, TOP_K, catalog_index, coarse_category, customer_reply, initial_message,
    load_jsonl, materialize_hidden_fields, normalize_recommendations)
from src.agent import Agent  # noqa: E402

WIDTH = 84


def play(agent, sample, catalog_ids, categories, products) -> None:
    sid = sample["sample_id"]
    target = str(sample["ground_truth"]["parent_asin"])
    card, behaviour = materialize_hidden_fields(sample, products)
    effective = {**sample, "intent_card": card, "behavior": behaviour}
    disclosed: set[str] = set()
    boundary_used = False
    override_applied = sample["scenario_type"] != "intent_override"
    message = initial_message(
        effective, coarse_category(categories.get(target, [])), disclosed
    )
    agent.reset(sid, sample["user_profile"])

    title = str(products.get(target, {}).get("title", ""))[:70]
    print(f"\n{'=' * WIDTH}")
    print(f"{sid}  [{sample['scenario_type']}]   hidden target: {title}")
    print("=" * WIDTH)

    for turn in range(1, MAX_TURNS + 1):
        for i, line in enumerate(textwrap.wrap(message, WIDTH - 10) or [""]):
            print(("CUSTOMER  " if i == 0 else " " * 10) + line)
        response = agent.respond(sid, message, turn, TOP_K)
        for i, line in enumerate(textwrap.wrap(response["message"], WIDTH - 10) or [""]):
            print(("AGENT     " if i == 0 else " " * 10) + line)
        print(f"{' ' * 10}\033[2m[asks for: {response['ask_attribute']}]\033[0m")

        ranked = normalize_recommendations(response.get("recommendations"), catalog_ids)
        if override_applied and target in ranked:
            print(f"\n  >>> FOUND IT at rank {ranked.index(target) + 1}, turn {turn}")
            return
        if turn == MAX_TURNS:
            print("\n  >>> not found in 10 turns")
            return

        override = effective.get("behavior", {}).get("override") or {}
        if not override_applied and turn + 1 == int(override.get("turn", 3)):
            override_applied = True
            if override.get("new_value"):
                disclosed.add(str(override["new_value"]))
            message = str(override.get("message", ""))
        else:
            message, boundary_used = customer_reply(
                effective, response.get("ask_attribute"), disclosed, boundary_used
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Print agent/customer dialogue")
    parser.add_argument("--catalog", default="data/catalog.jsonl")
    parser.add_argument("--dataset", default="data/public_set.jsonl")
    parser.add_argument("--scenario", default="", help="buying|browsing|intent_override|boundary")
    parser.add_argument("--sample", default="", help="comma-separated sample_ids")
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()

    samples = load_jsonl(args.dataset)
    if args.sample:
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

    catalog_ids, categories, products = catalog_index(args.catalog)
    agent = Agent(args.catalog)
    for sample in samples:
        play(agent, sample, catalog_ids, categories, products)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
