"""Agent interface -- the single integration point.

Pipeline per turn:

    observe -> route -> [buying: filters+BM25 | browsing: dense+MMR] -> RRF
            -> cross-encoder pre-filter -> LLM semantic ranking -> top 10
            -> clarification policy -> trace

HARD INVARIANT: no exception may escape respond() or reset().
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

# Document rendering budget for the ranking stages. The cross-encoder truncates at 384
# tokens, so ~900 chars is roughly the point past which extra text is discarded anyway.
_FEATURE_LIMIT = int(os.environ.get("TECHJAM_DOC_FEATURES", "6"))
_DETAIL_LIMIT = int(os.environ.get("TECHJAM_DOC_DETAILS", "6"))
_DOC_CHARS = int(os.environ.get("TECHJAM_DOC_CHARS", "900"))

# How many candidates the clarification policy inspects when scoring question value.
# A wider sample is a better estimate of the value distribution -- entropy over 50 items
# is noisy, and a single unlucky value can swing which question gets asked. It costs only
# a regex pass per candidate, no model call.
POOL_SAMPLE = int(os.environ.get("TECHJAM_POOL_SAMPLE", "50"))

# How deep retrieval goes. The ranking cascade only ever scores CE_WIDTH of these and the
# answer is the top 10, so going deeper does not change what is ranked -- it changes how
# much evidence the question policy has to reason over.
RETRIEVAL_DEPTH = int(os.environ.get("TECHJAM_DEPTH", "0"))


def dense_enabled() -> bool:
    """RETIRED -- default OFF. Diagnosed, not merely switched off (eval/RESULTS.md run 6).

    Committing `data/index/` flipped `DenseIndex.available` to True and switched this track
    on as a silent side effect of shipping a build artifact, unmeasured. It loses:

        BM25 alone   recall@10 0.4013   TS 0.77773
        dense alone  recall@10 0.2610
        RRF w=1.0    recall@10 0.3899   TS 0.69589   (53 targets lost from the head, 46 gained)
        RRF w=0.3    recall@10 0.4307   TS 0.76386   (best tuned weight, still loses)

    RRF genuinely improves recall@50 (0.595 -> 0.636), so the index is sound -- but the
    metric scores the top 10, and giving a much weaker voter a comparable vote drags targets
    out of the head. Down-weighting repairs the damage without ever adding value.

    Root cause: the simulator copies constraints verbatim out of `features`/`details`, which
    is exactly BM25's strength and exactly what a semantic bi-encoder blurs.

    Its last remaining rationale was as a hedge against the private set paraphrasing
    constraints. `docs/competition_specification.md` now states that no undisclosed
    natural-language paraphrases are introduced, so that rationale is gone too.

    Kept in-tree, one env var away (TECHJAM_DENSE=1), as Pillar I's browsing track and a
    documented negative result. It does not ship on.
    """
    return os.environ.get("TECHJAM_DENSE", "").strip() not in ("", "0", "false")


def dense_weight_scale() -> float:
    """Multiplier on the route's dense RRF weight.

    Diagnosed (eval/RESULTS.md run 6): the dense track's recall@10 is 0.261 against BM25's
    0.401, but RRF was counting its vote as near-equal, so fusion pulled targets OUT of the
    head -- 53 lost against 46 gained per 613 turns. A weak voter needs a weak vote.
    """
    try:
        return max(0.0, float(os.environ.get("TECHJAM_DENSE_W", "1.0")))
    except ValueError:
        return 1.0


def dense_mmr_enabled() -> bool:
    """MMR trades relevance for diversity. That is the wrong trade when the score is
    decided by rank 1, so it is separately switchable for measurement."""
    return os.environ.get("TECHJAM_DENSE_MMR", "1").strip() not in ("0", "false", "")


def _doc_line(meta: dict) -> str:
    """Compact one-line product rendering for the ranking stages.

    Deliberately short: the cross-encoder truncates at 384 tokens and every extra token
    in the LLM prompt is paid 15 times per turn.
    """
    parts = [str(meta.get("title") or "")]
    price = meta.get("price")
    if price not in (None, ""):
        parts.append(f"${price}")

    # The evaluator derives every constraint from `features` + `details` (plus a material
    # and colour regex over the whole record, and price) -- intent_card, local_evaluator.py
    # L52-66. Showing the ranker only features[:2] and NO details meant that whenever a
    # constraint came from `details` or from a later feature, the model was asked to match
    # text the document did not contain. Capacity cannot fix a missing field.
    features = meta.get("features") or []
    if features:
        parts.append("; ".join(str(f) for f in features[:_FEATURE_LIMIT]))
    details = meta.get("details") or {}
    if isinstance(details, dict) and details:
        parts.append("; ".join(f"{k}: {v}" for k, v in list(details.items())[:_DETAIL_LIMIT]))
    return " | ".join(p for p in parts if p)[:_DOC_CHARS]


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

        depth = RETRIEVAL_DEPTH or plan.truncation
        with trace.timed("bm25"):
            lexical = self.bm25.search(terms, limit=depth)
        if lexical:
            rankings.append([asin for asin, _ in lexical])
            weights.append(plan.bm25_weight)
        trace.pool_sizes["bm25"] = len(lexical)

        if dense_enabled() and self.dense.available and plan.dense_weight > 0:
            with trace.timed("dense"):
                dense_hits = self.dense.search(
                    orchestrator.distill(state),
                    limit=plan.truncation,
                    mmr=(route.name == router.BROWSING and dense_mmr_enabled()),
                )
            # The dense index is a prebuilt artifact with its own ids.json; it is NOT
            # guaranteed to have been built against the catalog we were constructed with.
            # An id the catalog does not contain is stripped by the scorer, so emitting
            # one silently wastes a ranking slot. Validate at the
            # boundary -- this is the only place a foreign id can enter the pipeline.
            dense_hits = [(asin, score) for asin, score in dense_hits if asin in self.bm25.meta]
            if dense_hits:
                rankings.append([asin for asin, _ in dense_hits])
                weights.append(plan.dense_weight * dense_weight_scale())
            trace.pool_sizes["dense"] = len(dense_hits)

        # --- Pillar I: RRF fusion ---
        # One track means fusion is the identity; skip it so Stage 1 is provably
        # behaviour-preserving against the 0.750401 baseline.
        candidates = rankings[0] if len(rankings) == 1 else rrf_fuse(rankings, weights)
        trace.pool_sizes["fused"] = len(candidates)

        # --- Pillar II: over-generality cutoff, decided before paying for ranking ---
        # The policy scores each candidate question against the pool it would narrow, so it
        # needs the candidates' text. Capped: entropy over the head is the same shape as
        # entropy over all of it, and this runs every turn.
        pool_text = [self.doc_text.get(asin, "") for asin in candidates[:POOL_SAMPLE]]
        ask = policy.decide_ask(
            state,
            pool_size=len(candidates),
            flat=previous.flat,
            pool_text=pool_text,
        )

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
        # Self-refining guidance (Pillar III) used to force `ask_attribute=None` here and
        # fall silent for the rest of the session. Its measured benefit was never "asking
        # costs a turn" -- asking is free, since recommendations go out on the same turn --
        # it was that each extra reply fed more terms into an OR'd BM25 query capped at
        # MAX_TERMS. `query_terms()` now excludes replies that carry no information, which
        # removes that cost at the source.
        #
        # So the orchestrator's judgement is kept, but expressed as a change of TACTIC
        # rather than silence: stop spending questions on the wildcard and probe specific
        # buckets instead. A shopping assistant that goes quiet the moment it stops learning
        # is a worse product, and the simulator's own reply to a null attribute is
        # "Ask me about one specific attribute" -- so falling silent literally ignores a
        # direct request from the customer.
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
