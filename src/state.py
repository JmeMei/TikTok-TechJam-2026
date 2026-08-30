"""Session state and slot tracking (Pillar II) -- owned by the `dialog` agent.

Contract for other modules:
  * `SessionState.observe(message, turn)` folds one customer message into the state.
  * `SessionState.query_terms()` yields the decayed, deduplicated term list retrieval uses.
  * `SessionState.distilled()` yields the compact structured profile the ranker sees.
    Raw dialogue history is NEVER handed to a ranker (Pillar III).

This module must not import retrieval, rerank, or any model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)

# Kept byte-identical to the starter so Stage 1 is provably behaviour-preserving.
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "some",
    "that", "the", "this", "to", "want", "with", "would", "you", "looking",
}

MAX_TERMS = 60

# The ten buckets the evaluator accepts for `ask_attribute`.
ATTRIBUTES = (
    "category", "material", "color", "size", "style",
    "brand", "budget", "feature", "use_case", "other",
)


def terms(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    ]


@dataclass
class Slot:
    """One disclosed constraint, tagged with the turn it arrived on (for decay)."""

    text: str
    bucket: str
    turn: int
    weight: float = 1.0


# Fixed boilerplate the simulator wraps around real content. Stripping it is the whole
# point of distillation: "For that, what matters is:" carries no preference information
# but does occupy ranker context and dilute the relevance signal.
BOILERPLATE = re.compile(
    r"i'm looking for|a key requirement is:?|for that,? what matters is:?"
    r"|but i'm still exploring|those options are not quite right yet"
    r"|ask me about one specific attribute|actually,? ignore my earlier preference"
    r"|what i need is:?|i don't have (?:an additional )?preference(?: for)?"
    r"|please use your judgment",
    re.I,
)


@dataclass
class DistilledContext:
    """Compact structured profile handed to rankers. Never raw history."""

    slots: dict[str, list[str]] = field(default_factory=dict)
    profile_tags: list[str] = field(default_factory=list)
    category_hint: str = ""
    disclosures: list[str] = field(default_factory=list)

    def as_query(self) -> str:
        """One-line natural rendering, for cross-encoder and LLM prompts.

        Must carry everything the customer has revealed. Retrieval searches on the whole
        accumulated transcript, so a ranker given only the opening message is reranking
        with strictly less information than the retriever used -- it will reliably undo
        good candidates rather than improve them.
        """
        parts: list[str] = []
        if self.category_hint:
            parts.append(self.category_hint)
        for bucket, values in self.slots.items():
            if values:
                parts.append(f"{bucket}: {', '.join(values)}")
        # Until Stage 5 parses typed slots, the boilerplate-stripped disclosures are the
        # distillation. Deduplicated and compacted -- not a transcript replay.
        parts.extend(self.disclosures)
        return " | ".join(p for p in parts if p)[:600]


class SessionState:
    """Per-session dialogue state.

    Stage 1 keeps behaviour identical to the starter: the transcript accumulates and
    every term feeds retrieval. Structured slot filling, override erasure and decay are
    populated by the `dialog` agent in Stage 5 -- the fields and methods they need
    already exist here so agent.py does not change when they land.
    """

    def __init__(self, session_id: str, user_profile: dict | None = None) -> None:
        self.session_id = session_id
        self.user_profile: dict = user_profile if isinstance(user_profile, dict) else {}
        self.turn = 0
        self.transcript: list[str] = []

        self.slots: dict[str, list[Slot]] = {}
        self.erased: list[str] = []          # slots dropped by an intent override
        self.disclosed_last: list[str] = []  # constraints revealed on the latest turn
        self.gained_terms: list[str] = []    # new search terms from the latest reply

        # Buckets that answered "I don't have an additional preference for X".
        self.drained: set[str] = set()
        # Buckets refused once via the boundary scenario.
        self.unavailable: set[str] = set()
        # Every ask_attribute issued, and whether it yielded anything.
        self.asks: list[tuple[str, bool]] = []

        self.override_seen = False

    # ---- ingestion -------------------------------------------------------

    def observe(self, message: str, turn: int) -> None:
        """Fold one customer message into the state.

        Stage 5 (`dialog`) extends this with constraint parsing, override erasure and
        the drained/unavailable bookkeeping. The transcript append is the behaviour the
        current 0.750401 score depends on and must be preserved.
        """
        self.turn = turn
        before = set(self.query_terms())
        self.transcript.append(message if isinstance(message, str) else "")
        self.disclosed_last = []
        # Did this reply actually tell us anything? Until Stage 5 parses constraints,
        # "new search terms arrived" is the honest observable proxy. The orchestrator
        # needs a real signal here -- deriving `yielded` from disclosed_last before it is
        # populated makes it permanently False and fires stop_asking spuriously.
        self.gained_terms = sorted(set(self.query_terms()) - before)

    # ---- retrieval interface --------------------------------------------

    def query_terms(self) -> list[str]:
        """Deduplicated term list for the lexical track.

        Preserves starter semantics exactly: dedupe by first appearance across the whole
        accumulated transcript, capped at MAX_TERMS.
        """
        return list(dict.fromkeys(terms(" ".join(self.transcript))))[:MAX_TERMS]

    def distilled(self) -> DistilledContext:
        """Compact profile for the ranking stages (Pillar III context distillation)."""
        slots = {bucket: [s.text for s in items] for bucket, items in self.slots.items() if items}
        tags = self.user_profile.get("preference_tags") or []
        return DistilledContext(
            slots=slots,
            profile_tags=[str(t) for t in tags if t],
            category_hint=self.category_hint(),
            disclosures=self.disclosures(),
        )

    def disclosures(self) -> list[str]:
        """Boilerplate-stripped, deduplicated content from every turn after the opening."""
        seen: set[str] = set()
        out: list[str] = []
        for message in self.transcript[1:]:
            cleaned = BOILERPLATE.sub(" ", message)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;,")
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                out.append(cleaned)
        return out

    def category_hint(self) -> str:
        """The opening message names the coarse category; it is the most stable signal."""
        if not self.transcript:
            return ""
        cleaned = BOILERPLATE.sub(" ", self.transcript[0])
        return re.sub(r"\s+", " ", cleaned).strip(" .;,")

    # ---- policy interface ------------------------------------------------

    def record_ask(self, attribute: str | None, yielded: bool) -> None:
        if attribute:
            self.asks.append((attribute, yielded))

    def exhausted(self) -> bool:
        """True when no attribute can plausibly reveal anything further."""
        return "other" in self.drained

    def slot_count(self) -> int:
        return sum(len(v) for v in self.slots.values())
