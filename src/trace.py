"""Structured decision trace (Pillar III).

One TurnTrace per turn, capturing every strategy decision the agent made and why.
Two consumers: scripts/dump_trace.py (static analysis) and app/server.py (walkthrough).
Both read this schema, so it changes in one place.

Nothing here may raise. A trace is diagnostic; a failure to record must never cost a turn.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field


# Pipeline stages, in execution order. `stage_reached` records how far a turn got
# before something short-circuited it (over-generality cutoff, model unavailable,
# timeout). This is what makes the fallback ladder visible.
STAGES = ("bm25", "dense", "rrf", "cross_encoder", "llm")


@dataclass
class Decision:
    """One strategy decision, with the signal that triggered it."""

    kind: str           # e.g. "route", "over_generality_cutoff", "escalate_listwise"
    choice: str         # what was decided
    trigger: str        # the observed signal that caused it
    detail: str = ""    # human-readable rationale for the walkthrough

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class TurnTrace:
    session_id: str
    turn: int

    # --- routing (Pillar I) ---
    route: str = "unknown"
    route_weights: dict[str, float] = field(default_factory=dict)

    # --- dialogue state (Pillar II) ---
    slots: dict[str, list[str]] = field(default_factory=dict)
    slots_erased: list[str] = field(default_factory=list)
    disclosed_this_turn: list[str] = field(default_factory=list)
    drained_buckets: list[str] = field(default_factory=list)

    # --- retrieval funnel (Pillar I) ---
    pool_sizes: dict[str, int] = field(default_factory=dict)
    stage_reached: str = "bm25"
    # (parent_asin, rank_before, rank_after) for items the ranking stages moved
    rank_movement: list[tuple[str, int, int]] = field(default_factory=list)

    # --- clarification (Pillar II) ---
    ask_attribute: str | None = None
    ask_reason: str = ""

    # --- self-evolution (Pillar III) ---
    decisions: list[Decision] = field(default_factory=list)

    # --- telemetry (Feasibility) ---
    latency_ms: dict[str, float] = field(default_factory=dict)
    tokens: dict[str, int] = field(default_factory=dict)

    recommendations: list[str] = field(default_factory=list)

    def decide(self, kind: str, choice: str, trigger: str, detail: str = "") -> None:
        self.decisions.append(Decision(kind, choice, trigger, detail))

    def timed(self, label: str) -> "_Timer":
        return _Timer(self, label)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["decisions"] = [d.as_dict() for d in self.decisions]
        return payload

    def to_json(self) -> str:
        try:
            return json.dumps(self.as_dict(), ensure_ascii=False)
        except (TypeError, ValueError):
            return json.dumps({"session_id": self.session_id, "turn": self.turn})


class _Timer:
    """`with trace.timed("bm25"):` records elapsed ms into trace.latency_ms."""

    def __init__(self, trace: TurnTrace, label: str) -> None:
        self.trace = trace
        self.label = label
        self.start = 0.0

    def __enter__(self) -> "_Timer":
        self.start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> bool:
        elapsed = (time.perf_counter() - self.start) * 1000.0
        self.trace.latency_ms[self.label] = round(elapsed, 2)
        return False  # never swallow exceptions
