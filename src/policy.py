"""Clarification policy (Pillar II) -- owned by the `dialog` agent.

    decide_ask(state, pool_size, flat) -> AskDecision

Contract: this module NEVER calls a model. Choosing what to ask is a decision over a
10-value enum, not a generation problem -- the evaluator reads `ask_attribute`, and only
type-checks `message` (local_evaluator.py:243).

Why "other" is the default: customer_reply (local_evaluator.py:178-181) matches
`attribute == "other" OR classify_constraint(value) == attribute`. The left side
short-circuits, so "other" is a wildcard that matches ANY undisclosed constraint, while
a specific bucket matches only its own. The simulator holds exactly four constraints
(2 hard + 2 soft) and reveals up to two per turn, so two "other" asks drain it entirely.
No information-gain heuristic beats a legal wildcard that returns everything.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.state import ATTRIBUTES

# Over-generality thresholds (Pillar II proactive guidance). Budgeted knob.
POOL_OVERLOAD = 400

# Natural-language surface for each attribute. The intelligence is in which key is
# selected, not in the sentence -- the evaluator never reads these.
PROMPTS: dict[str, str] = {
    "material": "Do you have a material preference -- cotton, leather, wool?",
    "color": "Any particular colour you're leaning toward?",
    "size": "What size or fit are you looking for?",
    "budget": "Roughly what budget did you have in mind?",
    "style": "Any particular style or cut you prefer?",
    "brand": "Is there a brand you like, or should I stay open?",
    "category": "What kind of item are you after, specifically?",
    "feature": "Any features that matter -- pockets, lining, waterproofing?",
    "use_case": "What will you mainly be using it for?",
    "other": "Anything else that matters to you?",
}
CLOSING = "Here are the closest matches I found."


@dataclass
class AskDecision:
    attribute: str | None
    message: str
    reason: str
    cutoff: bool = False   # True -> skip the expensive ranking stages this turn


def decide_ask(state, pool_size: int = 0, flat: bool = False) -> AskDecision:
    """Choose this turn's clarification.

    Order of reasoning:
      1. Exhausted? Asking yields nothing -- commit the turn to ranking.
      2. Over-general? Cut the pipeline short and ask instead. A better question is
         worth more than another retrieval call.
      3. Default to the "other" wildcard.
      4. Once "other" is drained, fall back to bucket-targeted selection.
    """
    if state.exhausted():
        return AskDecision(
            attribute=None,
            message=CLOSING,
            reason="all constraint buckets drained; further questions cannot reveal anything",
        )

    over_general = pool_size > POOL_OVERLOAD or flat
    attribute = _select(state)
    reason = (
        f"candidate pool {pool_size} over threshold {POOL_OVERLOAD}"
        if pool_size > POOL_OVERLOAD
        else "top scores did not separate; retrieval has no opinion"
        if flat
        else "default wildcard: matches any undisclosed constraint"
    )
    return AskDecision(
        attribute=attribute,
        message=PROMPTS.get(attribute or "other", PROMPTS["other"]),
        reason=reason,
        cutoff=over_general,
    )


def _select(state) -> str:
    """Pick the attribute most likely to yield a disclosure.

    Stage 5 (`dialog`) replaces the fallback branch with expected-information-gain over
    the live candidate pool. The "other"-first default stays: it is not a heuristic to be
    improved on, it is a wildcard the simulator honours.
    """
    if "other" not in state.drained:
        return "other"
    for attribute in ATTRIBUTES:
        if attribute == "other":
            continue
        if attribute in state.drained or attribute in state.unavailable:
            continue
        if attribute in state.slots and state.slots[attribute]:
            continue
        return attribute
    return "other"
