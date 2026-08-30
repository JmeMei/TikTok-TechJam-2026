"""Agent interface -- the single integration point. LEAD-OWNED (CLAUDE.md section 5).

Pipeline per turn:

    observe -> route -> [buying: filters+BM25 | browsing: dense+MMR] -> RRF
            -> cross-encoder pre-filter -> LLM semantic ranking -> top 10
            -> clarification policy -> trace

HARD INVARIANT (CLAUDE.md section 4): no exception may escape respond() or reset().
On internal failure we degrade to the last known-good candidate list rather than raising.
The local evaluator catches exceptions and charges a turn (-0.02), but
competition_specification.md:65 warns the official harness may count them as a full miss.
"""

from __future__ import annotations

import os
from pathlib import Path

from src import policy, router
from src.orchestrator import Orchestrator, Telemetry
from src.rerank import Ranker
from src.retrieval import BM25Index, DenseIndex, rrf_fuse
from src.state import SessionState
from src.trace import TurnTrace

CATALOG_DEFAULT = "data/catalog.jsonl"


def dense_enabled() -> bool:
    """DEFAULT OFF -- measured, see eval/RESULTS.md run 3/3a/3b.

    Committing `data/index/` flipped `DenseIndex.available` to True and switched the dense
    track on as a silent side effect of shipping a build artifact. It had never been
    measured. RRF fusion against the weak dense ranking pushes targets out of the top 25
    and costs HR@10 directly: 0.890 -> 0.810, TechnicalScore 0.76035 -> 0.69589.

    The track stays built and one env var away (TECHJAM_DENSE=1) -- it is the hedge if the
    private set paraphrases constraints instead of quoting them verbatim, and the code is
    Pillar I's browsing track. But it does not ship on until it beats BM25 on the full 200.
    """
    return os.environ.get("TECHJAM_DENSE", "").strip() not in ("", "0", "false")


def _doc_line(meta: dict) -> str:
    """Compact one-line product rendering for the ranking stages.

    Deliberately short: the cross-encoder truncates at 384 tokens and every extra token
    in the LLM prompt is paid 15 times per turn.
    """
    parts = [str(meta.get("title") or "")]
    price = meta.get("price")
    if price not in (None, ""):
        parts.append(f"${price}")
    features = meta.get("features") or []
    if features:
        parts.append("; ".join(str(f) for f in features[:2]))
    return " | ".join(p for p in parts if p)[:400]


class Agent:
    """Conversational shopping agent over the frozen 50k-product catalog."""

    def __init__(self, catalog_path: str | Path = CATALOG_DEFAULT) -> None:
        self.catalog_path = Path(catalog_path)
        self.bm25 = BM25Index(self.catalog_path)
        self.dense = DenseIndex()
        self.doc_text = {asin: _doc_line(meta) for asin, meta in self.bm25.meta.items()}
        self.ranker = Ranker(doc_text=self.doc_text)

        self._sessions: dict[str, SessionState] = {}
        self._orchestrators: dict[str, Orchestrator] = {}
        self._last_good: dict[str, list[str]] = {}
        self._last_telemetry: dict[str, Telemetry] = {}
        self.traces: list[TurnTrace] = []

    # ---- interface -------------------------------------------------------

    def reset(self, session_id: str, user_profile: dict) -> None:
        try:
            self._sessions[session_id] = SessionState(session_id, user_profile)
            self._orchestrators[session_id] = Orchestrator()
            self._last_good[session_id] = []
            self._last_telemetry[session_id] = Telemetry()
        except Exception:
            # Even reset must not raise; a bare state keeps respond() serviceable.
            self._sessions[session_id] = SessionState(session_id, {})
            self._orchestrators[session_id] = Orchestrator()
            self._last_good[session_id] = []
            self._last_telemetry[session_id] = Telemetry()

    def respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        try:
            return self._respond(session_id, user_message, turn, top_k)
        except Exception:
            # Degrade to the last known-good list rather than losing the turn entirely.
            fallback = self._last_good.get(session_id, [])[:top_k]
            return {
                "message": policy.CLOSING,
                "ask_attribute": "other",
                "recommendations": [{"parent_asin": asin} for asin in fallback],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            }

    # ---- pipeline --------------------------------------------------------

    def _respond(self, session_id: str, user_message: str, turn: int, top_k: int) -> dict:
        state = self._sessions.get(session_id)
        if state is None:
            self.reset(session_id, {})
            state = self._sessions[session_id]
        orchestrator = self._orchestrators[session_id]
        previous = self._last_telemetry.get(session_id, Telemetry())

        was_overridden = state.override_seen
        state.observe(user_message, turn)
        trace = TurnTrace(session_id=session_id, turn=turn)
        trace.slots_erased = list(state.erased)

        # Pillar II: an override erases and rewrites. Log it on the turn it fires so the
        # walkthrough can show the retraction happening rather than just its aftermath.
        if state.override_seen and not was_overridden:
            trace.decide(
                "intent_override",
                f"erase {state.erased or ['(nothing)']}",
                "customer retracted their earlier preference",
                f"now targeting: {state.override_constraint or 'unstated'}",
            )

        # --- Pillar I: route ---
        route = router.route(state)
        trace.route = route.name
        trace.route_weights = {"bm25": route.bm25_weight, "dense": route.dense_weight}
        trace.decide("route", route.name, route.reason)

        # --- Pillar III: re-plan from last turn's telemetry ---
        plan = orchestrator.plan(state, route, previous)
        for kind, choice, trigger in plan.notes:
            trace.decide(kind, choice, trigger)

        # --- Pillar I: multi-route retrieval ---
        terms = state.query_terms()
        rankings: list[list[str]] = []
        weights: list[float] = []

        with trace.timed("bm25"):
            lexical = self.bm25.search(terms, limit=plan.truncation)
        if lexical:
            rankings.append([asin for asin, _ in lexical])
            weights.append(plan.bm25_weight)
        trace.pool_sizes["bm25"] = len(lexical)

        if dense_enabled() and self.dense.available and plan.dense_weight > 0:
            with trace.timed("dense"):
                dense_hits = self.dense.search(
                    orchestrator.distill(state),
                    limit=plan.truncation,
                    mmr=(route.name == router.BROWSING),
                )
            # The dense index is a prebuilt artifact with its own ids.json; it is NOT
            # guaranteed to have been built against the catalog we were constructed with.
            # An id the catalog does not contain is stripped by the scorer, so emitting
            # one silently wastes a ranking slot (CLAUDE.md section 4). Validate at the
            # boundary -- this is the only place a foreign id can enter the pipeline.
            dense_hits = [(asin, score) for asin, score in dense_hits if asin in self.bm25.meta]
            if dense_hits:
                rankings.append([asin for asin, _ in dense_hits])
                weights.append(plan.dense_weight)
            trace.pool_sizes["dense"] = len(dense_hits)

        # --- Pillar I: RRF fusion ---
        # One track means fusion is the identity; skip it so Stage 1 is provably
        # behaviour-preserving against the 0.750401 baseline.
        candidates = rankings[0] if len(rankings) == 1 else rrf_fuse(rankings, weights)
        trace.pool_sizes["fused"] = len(candidates)

        # --- Pillar II: over-generality cutoff, decided before paying for ranking ---
        ask = policy.decide_ask(state, pool_size=len(candidates), flat=previous.flat)

        # --- Pillar I: ranking cascade ---
        if ask.cutoff:
            order = candidates[:top_k]
            stage, movement, flat, tokens = "rrf", [], previous.flat, 0
            trace.decide(
                "over_generality_cutoff",
                "skip ranking stages",
                ask.reason,
                "a better question is worth more than another retrieval call",
            )
        else:
            with trace.timed("rank"):
                result = self.ranker.rank(
                    orchestrator.distill(state),
                    candidates,
                    limit=top_k,
                    use_cross_encoder=plan.rerank,
                )
            order, stage = result.order, result.stage_reached
            movement, flat, tokens = result.movement, result.flat, result.tokens

        trace.stage_reached = stage
        trace.rank_movement = movement
        trace.recommendations = order

        # --- Pillar II: clarification ---
        if plan.stop_asking:
            # The orchestrator already recorded this in plan.notes; don't double-log.
            attribute, message = None, policy.CLOSING
        else:
            attribute, message = ask.attribute, ask.message
        trace.ask_attribute = attribute
        trace.ask_reason = ask.reason

        # --- bookkeeping ---
        state.record_ask(attribute, yielded=False)
        telemetry = Telemetry(
            pool_size=len(candidates),
            prev_pool_size=previous.pool_size,
            flat=flat,
            asked=attribute,
            yielded=bool(state.disclosed_last or state.gained_terms),
            stage_reached=stage,
        )
        orchestrator.observe(telemetry)
        self._last_telemetry[session_id] = telemetry
        if order:
            self._last_good[session_id] = order
        self.traces.append(trace)

        return {
            "message": message,
            "ask_attribute": attribute,
            "recommendations": [{"parent_asin": asin} for asin in order],
            "usage": {"prompt_tokens": tokens, "completion_tokens": 0},
        }
