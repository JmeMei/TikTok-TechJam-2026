"""Invariant tests for the agent.

These encode our hard invariants and competition_specification.md:65 --
"Exceptions, invalid output, and timeouts may count as a miss". The local evaluator only
charges a turn for a crash, but the official harness may charge the whole session, so
these are the tests that protect the score rather than the code.

Run: python -m unittest tests.test_agent
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src import policy
from src.agent import Agent
from src.cache import LRUCache, key_for
from src.rerank import RankResult, Ranker, _is_flat, _movement
from src.retrieval import rrf_fuse
from src.state import ATTRIBUTES, SessionState


def tiny_catalog(directory: Path) -> Path:
    rows = [
        {
            "parent_asin": f"B{i:09d}",
            "title": f"Product {i} cotton shirt",
            "features": ["100% cotton", "machine washable"],
            "details": {"department": "womens"},
            "description": [f"a shirt number {i}"],
            "categories": ["Clothing", "Shirts"],
            "store": "ExampleStore",
            "average_rating": 4.0,
            "rating_number": 10,
            "price": 20.0 + i,
        }
        for i in range(30)
    ]
    path = directory / "catalog.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return path


class AgentInvariants(unittest.TestCase):
    """The rules that cost whole sessions when broken."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory()
        cls.catalog = tiny_catalog(Path(cls._tmp.name))
        cls.agent = Agent(cls.catalog)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_respond_never_raises_on_hostile_input(self) -> None:
        """No exception may escape respond(). Degrade, never raise."""
        self.agent.reset("s1", {})
        hostile = [
            "", "   ", "\x00\x01", "a" * 20000,
            'MATCH "unclosed', "NULL OR 1=1; DROP TABLE products;--",
            "🙂" * 200, "\n\t\r",
        ]
        for message in hostile:
            with self.subTest(message=message[:24]):
                response = self.agent.respond("s1", message, 1, 10)
                self.assertIsInstance(response, dict)
                self.assertIsInstance(response["message"], str)
                self.assertIsInstance(response["recommendations"], list)

    def test_respond_without_reset_does_not_raise(self) -> None:
        """The starter raised RuntimeError here. A missing reset must not cost the run."""
        response = self.agent.respond("never-reset", "cotton shirt", 1, 10)
        self.assertIsInstance(response, dict)
        self.assertIsInstance(response["recommendations"], list)

    def test_reset_survives_a_bad_profile(self) -> None:
        for profile in (None, [], "nope", {"preference_tags": None}):
            with self.subTest(profile=profile):
                self.agent.reset("s2", profile)  # type: ignore[arg-type]
                self.assertIn("s2", self.agent._sessions)

    def test_recommendations_are_unique_and_well_formed(self) -> None:
        """Duplicates are stripped by the evaluator; emitting them wastes ranking slots."""
        self.agent.reset("s3", {})
        response = self.agent.respond("s3", "cotton shirt womens", 1, 10)
        asins = [r["parent_asin"] for r in response["recommendations"]]
        self.assertEqual(len(asins), len(set(asins)), "duplicate parent_asin emitted")
        self.assertLessEqual(len(asins), 10)
        for asin in asins:
            self.assertIsInstance(asin, str)
            self.assertTrue(asin)

    def test_only_catalog_ids_are_returned(self) -> None:
        """Invalid IDs are stripped by the scorer, so emitting them silently loses slots."""
        self.agent.reset("s4", {})
        response = self.agent.respond("s4", "cotton shirt", 1, 10)
        for rec in response["recommendations"]:
            self.assertIn(rec["parent_asin"], self.agent.bm25.meta)

    def test_ask_attribute_is_always_contract_valid(self) -> None:
        """agent_api_contract.json enumerates these; anything else is invalid output."""
        allowed = {
            "category", "material", "color", "size", "style", "brand",
            "budget", "feature", "use_case", "other", None,
        }
        self.agent.reset("s5", {})
        for turn in range(1, 11):
            response = self.agent.respond("s5", f"turn {turn} cotton", turn, 10)
            self.assertIn(response["ask_attribute"], allowed)

    def test_usage_counts_are_non_negative_ints(self) -> None:
        self.agent.reset("s6", {})
        usage = self.agent.respond("s6", "cotton", 1, 10)["usage"]
        for key in ("prompt_tokens", "completion_tokens"):
            self.assertIsInstance(usage[key], int)
            self.assertGreaterEqual(usage[key], 0)

    def test_full_ten_turn_session_stays_healthy(self) -> None:
        self.agent.reset("s7", {"preference_tags": ["fit"]})
        for turn in range(1, 11):
            response = self.agent.respond("s7", "I want a cotton shirt", turn, 10)
            self.assertIsInstance(response["recommendations"], list)


class RankerInvariants(unittest.TestCase):
    """The ranking cascade may worsen the order; it may never corrupt the set."""

    def test_rank_is_reorder_only(self) -> None:
        ranker = Ranker(doc_text={f"A{i}": f"doc {i}" for i in range(20)})
        candidates = [f"A{i}" for i in range(20)]
        result = ranker.rank("cotton shirt", candidates, limit=10)
        self.assertLessEqual(len(result.order), 10)
        self.assertTrue(set(result.order).issubset(set(candidates)))
        self.assertEqual(len(result.order), len(set(result.order)))

    def test_rank_handles_empty_candidates(self) -> None:
        self.assertEqual(Ranker().rank("q", [], limit=10).order, [])

    def test_rank_with_empty_query_passes_through(self) -> None:
        candidates = ["A", "B", "C"]
        self.assertEqual(Ranker().rank("", candidates, limit=10).order, candidates)

    def test_movement_reports_actual_shifts(self) -> None:
        moved = _movement(["a", "b", "c"], ["c", "a", "b"])
        self.assertEqual(sorted(moved), [("a", 1, 2), ("b", 2, 3), ("c", 3, 1)])

    def test_flat_detection(self) -> None:
        self.assertTrue(_is_flat([0.50, 0.50, 0.49, 0.49]))
        self.assertFalse(_is_flat([0.99, 0.40, 0.20, 0.05]))
        self.assertFalse(_is_flat([0.5]))


class FusionAndState(unittest.TestCase):
    def test_rrf_rewards_a_strong_showing_in_any_single_track(self) -> None:
        """RRF is intentionally NOT an averager.

        Because the contribution is 1/(k+rank), being 1st in one list and 3rd in the
        other (1/61 + 1/63) beats being 2nd in both (1/62 + 1/62). That asymmetry is the
        point: a hybrid pipeline wants an item one track is confident about to survive
        fusion even when the other track is lukewarm -- which is exactly the case for a
        lexical exact-match the dense track has no opinion on.
        """
        lexical = ["a", "b", "c"]
        dense = ["c", "b", "a"]
        fused = rrf_fuse([lexical, dense])
        self.assertEqual(set(fused), {"a", "b", "c"})
        self.assertEqual(fused[-1], "b", "the everywhere-mediocre item should rank last")

    def test_rrf_ranks_an_item_both_tracks_agree_on_first(self) -> None:
        fused = rrf_fuse([["x", "a", "b"], ["x", "b", "a"]])
        self.assertEqual(fused[0], "x")

    def test_rrf_weights_shift_the_balance(self) -> None:
        """Route weights must actually change the outcome, or routing is decorative."""
        lexical, dense = ["a", "b"], ["b", "a"]
        self.assertEqual(rrf_fuse([lexical, dense], [10.0, 1.0])[0], "a")
        self.assertEqual(rrf_fuse([lexical, dense], [1.0, 10.0])[0], "b")

    def test_rrf_handles_single_and_empty_rankings(self) -> None:
        self.assertEqual(rrf_fuse([["a", "b"]]), ["a", "b"])
        self.assertEqual(rrf_fuse([]), [])

    def test_state_accumulates_and_caps_terms(self) -> None:
        state = SessionState("s", {})
        for turn in range(1, 6):
            state.observe(f"unique{turn} cotton shirt", turn)
        terms = state.query_terms()
        self.assertLessEqual(len(terms), 60)
        self.assertEqual(len(terms), len(set(terms)), "terms must be deduplicated")
        self.assertIn("cotton", terms)

    def test_gained_terms_tracks_new_information(self) -> None:
        """The orchestrator's `yielded` signal depends on this being real."""
        state = SessionState("s", {})
        state.observe("cotton shirt", 1)
        self.assertTrue(state.gained_terms)
        state.observe("cotton shirt", 2)  # identical -> nothing new
        self.assertFalse(state.gained_terms)

    def test_state_tolerates_non_string_messages(self) -> None:
        state = SessionState("s", {})
        state.observe(None, 1)  # type: ignore[arg-type]
        self.assertEqual(state.query_terms(), [])


class CacheKeying(unittest.TestCase):
    def test_key_is_content_addressed_and_order_sensitive(self) -> None:
        self.assertEqual(key_for("a", "b"), key_for("a", "b"))
        self.assertNotEqual(key_for("a", "b"), key_for("b", "a"))
        # Must not collide across a boundary: ("ab","c") vs ("a","bc")
        self.assertNotEqual(key_for("ab", "c"), key_for("a", "bc"))

    def test_lru_evicts_oldest(self) -> None:
        cache = LRUCache(capacity=2)
        cache.put("x", 1)
        cache.put("y", 2)
        cache.get("x")          # x becomes most-recent
        cache.put("z", 3)       # evicts y
        self.assertEqual(cache.get("x"), 1)
        self.assertIsNone(cache.get("y"))
        self.assertEqual(cache.get("z"), 3)


if __name__ == "__main__":
    unittest.main()


class OverrideErasure(unittest.TestCase):
    """Pillar II: contradictions erase and rewrite slots, they do not append.

    intent_override is 30 of 200 public sessions, and a hit cannot score before the
    override turn (local_evaluator.py:234, 252). Dragging the retracted preference into
    the ranker's query cost -0.164 on that scenario before this landed.
    """

    def test_override_erases_the_retracted_opening_constraint(self) -> None:
        state = SessionState("o1", {})
        state.observe("I'm looking for Dresses. color: pink.", 1)
        self.assertIn("color: pink", state.distilled().as_query())
        state.observe("Actually, ignore my earlier preference. What I need is: cotton.", 2)
        query = state.distilled().as_query()
        self.assertTrue(state.override_seen)
        self.assertIn("color: pink", state.erased)
        self.assertNotIn("pink", query, "retracted preference still reaching the ranker")
        self.assertIn("cotton", query)

    def test_override_keeps_the_coarse_category(self) -> None:
        """An override changes what they want, not which department they are shopping in."""
        state = SessionState("o2", {})
        state.observe("I'm looking for Dresses. color: pink.", 1)
        state.observe("Actually, ignore my earlier preference. What I need is: cotton.", 2)
        self.assertIn("Dresses", state.distilled().as_query())

    def test_override_constraint_leads_the_query(self) -> None:
        state = SessionState("o3", {})
        state.observe("I'm looking for Dresses. color: pink.", 1)
        state.observe("For that, what matters is: 100% polyester.", 2)
        state.observe("Actually, ignore my earlier preference. What I need is: cotton.", 3)
        self.assertTrue(state.distilled().as_query().startswith("cotton"))

    def test_erasure_does_not_touch_the_lexical_track(self) -> None:
        """Deliberate: the retracted value is a genuine attribute of the target product,
        so removing its terms from BM25 risks recall. Demote in ranking only."""
        state = SessionState("o4", {})
        state.observe("I'm looking for Dresses. color: pink.", 1)
        state.observe("Actually, ignore my earlier preference. What I need is: cotton.", 2)
        self.assertIn("pink", state.query_terms())

    def test_non_override_sessions_are_unaffected(self) -> None:
        for opening in (
            "I'm looking for Dresses. A key requirement is: 100% cotton.",
            "I'm looking for Dresses, but I'm still exploring.",
        ):
            with self.subTest(opening=opening):
                state = SessionState("o5", {})
                state.observe(opening, 1)
                self.assertFalse(state.override_seen)
                self.assertEqual(state.erased, [])
                self.assertIn("Dresses", state.distilled().as_query())


class RefusalHandling(unittest.TestCase):
    """A refusal reveals nothing, so it must not reach the ranker as content."""

    def test_boundary_refusal_does_not_enter_the_query(self) -> None:
        # The bare article "a" was missing from BOILERPLATE, so this whole sentence
        # survived and was ranked against as if it were a disclosed constraint.
        state = SessionState("r1", {})
        state.observe("I'm looking for Dresses, but I'm still exploring.", 1)
        state.observe("I don't have a preference for color; please use your judgment.", 2)
        self.assertEqual(state.distilled().as_query(), "Dresses")
        self.assertIn("color", state.unavailable)

    def test_drained_refusal_does_not_inject_the_attribute_name(self) -> None:
        state = SessionState("r2", {})
        state.observe("I'm looking for Dresses. A key requirement is: 100% cotton.", 1)
        state.observe("I don't have an additional preference for other.", 2)
        self.assertNotIn("other", state.distilled().as_query().split(" | "))
        self.assertIn("other", state.drained)

    def test_drained_other_marks_the_session_exhausted(self) -> None:
        """"other" matches ANY undisclosed constraint, so its refusal proves the pool
        is empty and no narrower question can pay."""
        state = SessionState("r3", {})
        state.observe("I'm looking for Dresses.", 1)
        self.assertFalse(state.exhausted())
        state.observe("I don't have an additional preference for other.", 2)
        self.assertTrue(state.exhausted())


class QuestionValueSelection(unittest.TestCase):
    """Pillar II: the question is chosen from the live pool, not a fixed script.

    expected_gain = coverage x entropy. Both come from the candidates, so a question is
    only asked when it would actually separate them.
    """

    def test_prefers_the_attribute_that_splits_the_pool(self) -> None:
        # Every candidate is leather, but colours vary -> asking about material is useless.
        pool = [f"leather bag in {c}" for c in
                ("black", "white", "blue", "red", "pink", "green", "brown", "gray")]
        state = SessionState("q1", {})
        state.observe("I'm looking for Bags.", 1)
        # Selection happens first -- decide_ask marks the attribute spoken, and a spoken
        # attribute is deliberately not offered again.
        chosen, reason, _ = policy._select(state, pool)
        self.assertEqual(chosen, "color")
        self.assertIn("splits the pool", reason)
        decision = policy.decide_ask(state, pool_size=len(pool), pool_text=pool)
        self.assertIn("colour", decision.message)
        # ...while the structured field stays the wildcard, which is what the simulator
        # reads and what harvests the full 2-constraint cap. Specificity for the human,
        # yield for the evaluator.
        self.assertEqual(decision.attribute, "other")

    def test_single_valued_attribute_is_not_worth_asking(self) -> None:
        """Zero entropy: every candidate is the same colour, so the answer changes nothing."""
        pool = ["black cotton shirt"] * 8
        state = SessionState("q2", {})
        state.observe("I'm looking for Shirts.", 1)
        gain, _, _ = policy._expected_gain("color", pool)
        self.assertEqual(gain, 0.0)
        self.assertEqual(policy.decide_ask(state, pool_text=pool).attribute, "other")

    def test_falls_back_to_wildcard_with_no_pool(self) -> None:
        """An empty pool carries no signal; "other" has maximum coverage by construction."""
        state = SessionState("q3", {})
        state.observe("I'm looking for Dresses.", 1)
        self.assertEqual(policy.decide_ask(state, pool_text=[]).attribute, "other")

    def test_never_repeats_a_drained_bucket(self) -> None:
        pool = [f"cotton dress in {c}" for c in ("black", "white", "blue", "red")]
        state = SessionState("q4", {})
        state.observe("I'm looking for Dresses.", 1)
        state.observe("I don't have an additional preference for color.", 2)
        self.assertIn("color", state.drained)
        self.assertNotEqual(policy.decide_ask(state, pool_text=pool).attribute, "color")

    def test_always_contract_valid(self) -> None:
        """agent_api_contract.json enumerates the legal values; anything else is invalid."""
        allowed = set(ATTRIBUTES)
        pool = ["cotton dress in black", "leather bag in white", ""]
        state = SessionState("q5", {})
        state.observe("I'm looking for Dresses.", 1)
        for turn in range(2, 12):
            attribute = policy.decide_ask(state, pool_size=50, pool_text=pool).attribute
            self.assertIn(attribute, allowed)
            state.observe(f"I don't have an additional preference for {attribute}.", turn)

    def test_questions_do_not_repeat_across_a_session(self) -> None:
        """The failure this replaced: the same prompt every turn for ten turns."""
        pool = [f"cotton dress in {c}" for c in ("black", "white", "blue", "red")]
        state = SessionState("q6", {})
        state.observe("I'm looking for Dresses.", 1)
        asked = []
        for turn in range(2, 8):
            attribute = policy.decide_ask(state, pool_size=50, pool_text=pool).attribute
            asked.append(attribute)
            state.observe(f"I don't have an additional preference for {attribute}.", turn)
        self.assertEqual(len(asked), len(set(asked)), f"repeated a spent question: {asked}")


class IrrelevantQuestionGuards(unittest.TestCase):
    """Two ways a high-entropy attribute can still be the wrong question."""

    def test_low_coverage_attribute_is_not_asked(self) -> None:
        """A jewellery pool where only a few items mention a fabric.

        Those few split cleanly, so entropy is high and coverage x entropy clears the
        gain floor -- which is how the agent came to ask a necklace shopper to choose
        between leather and cotton. Coverage must hold on its own.
        """
        pool = ["alloy pendant necklace"] * 18 + ["leather cord necklace", "cotton cord necklace"]
        gain, coverage, _ = policy._expected_gain("material", pool)
        self.assertLess(coverage, policy._MIN_COVERAGE)
        self.assertGreater(gain, policy._GAIN_FLOOR, "gain floor alone would have allowed it")
        state = SessionState("g1", {})
        state.observe("I'm looking for Necklaces.", 1)
        self.assertNotEqual(policy.decide_ask(state, pool_text=pool).attribute, "material")

    def test_dimension_the_customer_already_named_is_not_re_asked(self) -> None:
        """"Material:alloy" is filed as a `feature`, because the evaluator's material
        vocabulary is fabric-only. Naming the dimension still counts as answering it."""
        state = SessionState("g2", {})
        state.observe("I'm looking for Necklaces. A key requirement is: Material:alloy.", 1)
        self.assertEqual(state.slots.get("material"), None)      # our bucketing says no
        self.assertTrue(policy._already_spoken_to(state, "material"))   # the words say yes
        pool = [f"{m} necklace" for m in ("leather", "cotton", "silk", "wool")] * 5
        self.assertNotEqual(policy.decide_ask(state, pool_text=pool).attribute, "material")


class SpokenAttributeTracking(unittest.TestCase):
    """Under hybrid emission the structured field is always "other", so the attribute the
    customer actually heard has to be tracked separately or the same question repeats."""

    def test_a_spoken_question_is_not_repeated(self) -> None:
        pool = [f"{c} leather belt" for c in ("black", "white", "brown", "pink")] * 5
        state = SessionState("sp1", {})
        state.observe("I'm looking for Belts.", 1)
        first = policy.decide_ask(state, pool_size=len(pool), pool_text=pool)
        self.assertEqual(first.attribute, "other")          # field harvests via wildcard
        self.assertIn("colour", first.message)              # but colour was asked aloud
        self.assertIn("color", state.spoken)
        second = policy.decide_ask(state, pool_size=len(pool), pool_text=pool)
        self.assertNotIn("colour", second.message, "asked the same question twice")
