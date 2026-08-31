"""Dynamic context programming (Pillar III) -- owned by the `orchestrator` agent.

Two jobs:

  1. Personalized context distillation. Compress dialogue history into a compact
     structured profile each turn. Raw history is NEVER replayed into a ranker -- that
     is both a pillar requirement and what keeps the LLM prompt small enough to afford.

  2. Runtime re-orchestration. Re-plan strategy from session telemetry: if the last
     clarification failed to shrink the pool, change tactic rather than repeat it.

Every decision emits a trace record with the signal that triggered it. Judges must be
able to SEE the system re-planning itself; an undocumented re-plan is worth nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def self_refine_enabled() -> bool:
    """Self-refining guidance logic (stop asking once asks stop paying).

    Toggleable so the Stage 1 refactor can be proven behaviour-identical to the 0.750401
    starter baseline with it off, and the feature can then be measured on its own.
    """
    return os.environ.get("TECHJAM_NO_SELF_REFINE", "").strip() in ("", "0", "false")


@dataclass
class Telemetry:
    """What the last turn actually did. The input to re-planning."""

    pool_size: int = 0
    prev_pool_size: int = 0
    flat: bool = False
    asked: str | None = None
    yielded: bool = False
    stage_reached: str = "bm25"

    def pool_shrank(self) -> bool:
        if not self.prev_pool_size:
            return True
        return self.pool_size < self.prev_pool_size


@dataclass
class Plan:
    """The strategy adjustments for this turn."""

    bm25_weight: float = 1.0
    dense_weight: float = 1.0
    truncation: int = 50
    escalate_listwise: bool = False
    stop_asking: bool = False
    rerank: bool = True
    profile_terms: list[str] = field(default_factory=list)
    notes: list[tuple[str, str, str]] = field(default_factory=list)  # (kind, choice, trigger)


class Orchestrator:
    """Per-session strategy planner."""

    def __init__(self) -> None:
        self.history: list[Telemetry] = []

    def observe(self, telemetry: Telemetry) -> None:
        self.history.append(telemetry)

    def plan(self, state, route, telemetry: Telemetry) -> Plan:
        """Re-plan from telemetry. Stage 1 returns the route unchanged.

        Stage 6 (`orchestrator`) fills in:
          * profile terms from user_profile as a weak prior (never overriding a slot)
          * route-weight shifts when a clarification failed to shrink the pool
          * listwise escalation when pointwise scores stay flat
          * self-refining guidance -- demote asks that returned nothing twice
        """
        plan = Plan(
            bm25_weight=route.bm25_weight,
            dense_weight=route.dense_weight,
            truncation=route.truncation,
        )

        # Adaptive orchestration: choose the ranking stage from what the session has become,
        # not from a fixed pipeline. After an override the customer has one strong, verbatim
        # constraint, and BM25 exact matching is already near-optimal on it -- semantic
        # reranking blurs a lexical match it cannot improve. Measured across three runs
        # (eval/RESULTS.md 3c/4/4a): the cross-encoder is +0.041 buying and +0.026 browsing
        # but negative on every override configuration, even with a fully cleaned query.
        # NOTE: calibrated against the weak 22M MiniLM reranker. A stronger ranker may well
        # beat BM25 on these sessions too -- re-measure before trusting it (TECHJAM_NO_SKIP=1).
        if getattr(state, "override_seen", False) and not os.environ.get("TECHJAM_NO_SKIP"):
            plan.rerank = False
            plan.notes.append((
                "skip_rerank",
                "trust the lexical track",
                "override installed a single verbatim constraint; BM25 is already precise",
            ))

        # Self-refining guidance logic: two consecutive empty asks means asking is spent.
        recent = self.history[-2:]
        if self_refine_enabled() and len(recent) == 2 and all(t.asked and not t.yielded for t in recent):
            plan.stop_asking = True
            plan.notes.append((
                "stop_asking",
                "commit to ranking",
                "two consecutive asks returned no disclosure",
            ))

        # A clarification that did not shrink the pool means the tactic is not working.
        # The response depends on what is actually available: shifting weight toward the
        # dense track is only a real decision when that track is live. It is currently
        # retired (a measured regression), so claiming the shift would make the trace
        # describe a re-plan that cannot happen -- and an untrue trace is worse than none.
        if telemetry.asked and telemetry.yielded and not telemetry.pool_shrank():
            dense_live = os.environ.get("TECHJAM_DENSE", "").strip() not in ("", "0", "false")
            choice = (
                "shift toward dense track" if dense_live
                else "no reweight available; dense track retired"
            )
            plan.notes.append((
                "reweight",
                choice,
                f"pool did not shrink after disclosure ({telemetry.prev_pool_size} -> {telemetry.pool_size})",
            ))

        if telemetry.flat:
            plan.escalate_listwise = True
            plan.notes.append((
                "escalate_listwise",
                "listwise LLM pass",
                "pointwise scores did not separate",
            ))

        return plan

    def distill(self, state) -> str:
        """Compact structured context for the ranking stages. Never raw history."""
        return state.distilled().as_query()
