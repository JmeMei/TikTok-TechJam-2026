"""Dual-track intent routing (Pillar I) -- owned by the `retrieval` agent.

  route(state, config) -> Route

The brief: "Instantly detect the user's underlying intent -- triggering a high-precision
filter track for targeted Buying to lock hard constraints, and a diverse dense retrieval
track for open-ended Browsing to unlock cross-category scenario matching."

Route weights and truncation depth come from CONFIG, never from literals at the call
site (CLAUDE.md section 3.I: "tunable, not hardcoded").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

BUYING = "buying"
BROWSING = "browsing"
MIXED = "mixed"

# The three tuned knobs live here. CLAUDE.md section 9 budgets ~3 total, so additions
# to this dict are a deliberate cost, not a free parameter.
CONFIG: dict[str, dict] = {
    BUYING: {"bm25_weight": 1.0, "dense_weight": 0.4, "truncation": 50, "hard_filters": True},
    BROWSING: {"bm25_weight": 0.5, "dense_weight": 1.0, "truncation": 50, "hard_filters": False},
    MIXED: {"bm25_weight": 1.0, "dense_weight": 1.0, "truncation": 50, "hard_filters": False},
}

# The browsing opening is a fixed sentence in the simulator; an exploring customer
# discloses no constraint at all.
_EXPLORING = re.compile(r"still exploring|just (?:browsing|looking)|not sure", re.I)
_BUYING_SIGNAL = re.compile(r"key requirement|what i need is|must (?:be|have)|i need", re.I)


@dataclass
class Route:
    name: str
    bm25_weight: float
    dense_weight: float
    truncation: int
    hard_filters: bool
    reason: str = ""
    filters: dict = field(default_factory=dict)


def route(state, config: dict | None = None) -> Route:
    """Classify the session's current intent and emit the track configuration.

    Stage 1 keeps the lexical track dominant so behaviour is unchanged. Stage 3 gives
    each branch a genuinely different pipeline.
    """
    cfg = config or CONFIG
    latest = state.transcript[-1] if state.transcript else ""
    slot_count = state.slot_count()

    if _EXPLORING.search(latest) and slot_count == 0:
        name, reason = BROWSING, "opening signals exploration and no constraints are known"
    elif _BUYING_SIGNAL.search(latest) or slot_count >= 2:
        name, reason = BUYING, "explicit requirement language or >=2 known constraints"
    elif slot_count == 0:
        name, reason = BROWSING, "no constraints disclosed yet"
    else:
        name, reason = MIXED, "partial constraints; hedge across both tracks"

    settings = cfg.get(name, cfg[MIXED])
    return Route(
        name=name,
        bm25_weight=float(settings["bm25_weight"]),
        dense_weight=float(settings["dense_weight"]),
        truncation=int(settings["truncation"]),
        hard_filters=bool(settings["hard_filters"]),
        reason=reason,
    )
