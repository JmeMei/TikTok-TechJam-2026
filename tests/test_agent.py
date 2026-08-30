"""Invariant tests for the agent.

These encode the rules from CLAUDE.md section 4 and competition_specification.md:65 --
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

from src.agent import Agent
from src.cache import LRUCache, key_for
from src.rerank import RankResult, Ranker, _is_flat, _movement
from src.retrieval import rrf_fuse
from src.state import SessionState


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
