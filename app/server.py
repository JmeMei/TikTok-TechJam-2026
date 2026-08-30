"""Minimal Flask walkthrough for the demo video.

    python app/server.py      (or: make demo)

DEV ONLY. UI/UX development is explicitly out of scope for this track, and the brief
accepts "a walkthrough video showing API usage, inference examples, or result analysis".
This exists to make the pipeline legible on camera, nothing more.

It is strictly outside the scored path:
  * src/ never imports anything from app/  (enforced by `make check`)
  * flask lives in requirements-dev.txt, not requirements.txt
  * the submission does not need this file to run

Two modes:
  auto  -- drive a real public session with the official simulator, so the hidden target
           is known and you can watch it climb the ranking
  chat  -- type your own messages against the live agent
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, jsonify, render_template, request  # noqa: E402

from evaluator.local_evaluator import (  # noqa: E402
    catalog_index,
    coarse_category,
    customer_reply,
    initial_message,
    load_jsonl,
    materialize_hidden_fields,
)
from src.agent import Agent  # noqa: E402

app = Flask(__name__)

CATALOG = "data/catalog.jsonl"
print("loading catalog and models (a few seconds) ...")
AGENT = Agent(CATALOG)
SAMPLES = {s["sample_id"]: s for s in load_jsonl("data/public_set.jsonl")}
CATALOG_IDS, CATEGORIES, PRODUCTS = catalog_index(CATALOG)
print(f"ready: {len(SAMPLES)} sessions, {len(CATALOG_IDS)} products")

# session_id -> simulator state for auto mode
LIVE: dict[str, dict] = {}


def _trace_payload(trace) -> dict:
    payload = trace.as_dict()
    payload["products"] = [
        {
            "parent_asin": asin,
            "title": (AGENT.bm25.meta.get(asin, {}).get("title") or "")[:110],
            "price": AGENT.bm25.meta.get(asin, {}).get("price"),
        }
        for asin in trace.recommendations
    ]
    return payload


@app.route("/")
def index():
    listing = [
        {"id": sid, "scenario": s["scenario_type"], "difficulty": s.get("difficulty_bucket", "")}
        for sid, s in list(SAMPLES.items())[:60]
    ]
    return render_template("index.html", samples=listing)


@app.post("/api/start")
def start():
    """Begin a session. With a sample_id, the official simulator plays the customer."""
    body = request.get_json(silent=True) or {}
    sample_id = body.get("sample_id")
    session_id = f"demo-{len(LIVE)}-{sample_id or 'free'}"

    if sample_id and sample_id in SAMPLES:
        sample = SAMPLES[sample_id]
        target = str(sample["ground_truth"]["parent_asin"])
        card, behavior = materialize_hidden_fields(sample, PRODUCTS)
        effective = {**sample, "intent_card": card, "behavior": behavior}
        disclosed: set[str] = set()
        opening = initial_message(effective, coarse_category(CATEGORIES.get(target, [])), disclosed)
        LIVE[session_id] = {
            "sample": effective,
            "target": target,
            "disclosed": disclosed,
            "boundary_used": False,
            "override_applied": sample["scenario_type"] != "intent_override",
            "turn": 0,
        }
        AGENT.reset(session_id, sample["user_profile"])
        return jsonify({
            "session_id": session_id,
            "message": opening,
            "target": target,
            "target_title": (PRODUCTS[target].get("title") or "")[:120],
            "scenario": sample["scenario_type"],
        })

    LIVE[session_id] = {"sample": None, "target": None, "turn": 0}
    AGENT.reset(session_id, {})
    return jsonify({"session_id": session_id, "message": "", "target": None})


@app.post("/api/turn")
def turn():
    """One turn: agent responds, then the simulator (auto mode) writes the next message."""
    body = request.get_json(silent=True) or {}
    session_id = body.get("session_id", "")
    message = body.get("message", "")
    live = LIVE.get(session_id)
    if live is None:
        return jsonify({"error": "unknown session"}), 400

    live["turn"] += 1
    response = AGENT.respond(session_id, message, live["turn"], 10)
    trace = AGENT.traces[-1] if AGENT.traces else None

    ranked = [r["parent_asin"] for r in response["recommendations"]]
    target = live.get("target")
    hit_rank = ranked.index(target) + 1 if target and target in ranked else None

    payload = {
        "agent": {
            "message": response["message"],
            "ask_attribute": response["ask_attribute"],
        },
        "trace": _trace_payload(trace) if trace else {},
        "hit_rank": hit_rank,
        "turn": live["turn"],
        "done": bool(hit_rank) or live["turn"] >= 10,
    }

    # Auto mode: the official simulator produces the customer's next line.
    if live.get("sample") and not payload["done"]:
        override = live["sample"].get("behavior", {}).get("override") or {}
        if not live["override_applied"] and live["turn"] + 1 == int(override.get("turn", 3)):
            live["override_applied"] = True
            new_value = str(override.get("new_value", ""))
            if new_value:
                live["disclosed"].add(new_value)
            payload["customer"] = str(override.get("message", ""))
            payload["override"] = True
        else:
            reply, live["boundary_used"] = customer_reply(
                live["sample"], response["ask_attribute"],
                live["disclosed"], live["boundary_used"],
            )
            payload["customer"] = reply
    return jsonify(payload)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
