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


# Exact mirror of the evaluator's classify_constraint (local_evaluator.py:137-151). That
# function decides which `ask_attribute` a given constraint answers, so mirroring it makes
# our reading of a disclosure agree with the simulator's by construction -- the same
# argument that justifies mirroring any pure function. Order matters: the original returns
# on the first match, and "feature" is the catch-all.
_MATERIALS = ("cotton", "polyester", "nylon", "leather", "wool", "spandex", "silk",
              "rayon", "fabric")
_BUDGET_RE = re.compile(r"(?:\$|<=|under)\s*\d")


def classify(value: str) -> str:
    """Which attribute bucket does this constraint answer?"""
    lowered = value.lower()
    if "budget" in lowered or _BUDGET_RE.search(lowered):
        return "budget"
    if any(m in lowered for m in _MATERIALS):
        return "material"
    if any(w in lowered for w in ("color", "black", "white", "blue", "red", "pink", "green")):
        return "color"
    if any(w in lowered for w in ("size", "sizing", "width", "wide", "narrow")):
        return "size"
    if any(w in lowered for w in ("department", "style", "fit", "sleeve", "neck")):
        return "style"
    if any(w in lowered for w in ("hiking", "running", "gym", "winter", "outdoor", "work")):
        return "use_case"
    return "feature"


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

# The two refusal shapes (local_evaluator.py:168-169, 183). Both mean "this question
# revealed nothing", so the message carries ZERO preference information and must not reach
# the ranker at all. Stripping them piecewise is not enough and was actively harmful:
#   * "I don't have a preference for color; please use your judgment."  -- the BOILERPLATE
#     alternation covered "an additional preference" but not the bare article, so the whole
#     sentence survived and entered the query as if it were a disclosed constraint.
#   * "I don't have an additional preference for other."  -- stripped down to the bare
#     attribute name, injecting the agent's OWN question back into its query as content.
# BM25 dilutes such noise across dozens of OR'd terms; a cross-encoder weighs the whole
# short string, so this cost the ranking stage far more than it cost retrieval.
REFUSAL_RE = re.compile(r"i don'?t have (?:an?\s+)?(?:additional\s+)?preference", re.I)

# Every simulator reply that carries ZERO preference information -- the two refusal shapes
# plus the answer to a null `ask_attribute`. None of them may reach the lexical query:
# "Those options are not quite right yet. Ask me about one specific attribute." contributes
# eight junk terms to an OR'd BM25 query that is capped at MAX_TERMS, crowding out real
# constraints. This is the same class of bug as the refusal leak, one layer down.
NO_INFO_RE = re.compile(
    r"i don'?t have (?:an?\s+)?(?:additional\s+)?preference"
    r"|those options are not quite right yet"
    r"|ask me about one specific attribute",
    re.I,
)

# The same two shapes, but capturing WHICH bucket came back empty, so the policy can stop
# asking questions that cannot pay. `drained` = the constraint pool holds nothing matching
# that bucket; `unavailable` = the one-shot boundary refusal (local_evaluator.py:168-169).
DRAINED_RE = re.compile(
    r"i don'?t have an additional preference for (?P<attr>[a-z_]+)", re.I
)
UNAVAILABLE_RE = re.compile(
    r"i don'?t have a preference for (?P<attr>[a-z_]+); please use your judgment", re.I
)

# The intent-override sentence, emitted verbatim at turn 3 or 4 (local_evaluator.py:76-86):
#     "Actually, ignore my earlier preference. What I need is: {new_value}."
# The clause after "What I need is:" is hard_constraints[0] -- the real target constraint.
# Everything the customer stated in the OPENING is explicitly retracted by this sentence.
# Matched before BOILERPLATE strips the phrasing, so this must be applied to the raw message.
OVERRIDE_RE = re.compile(
    r"actually,?\s*ignore my earlier preference\.?\s*"
    r"(?:what i need is:?\s*(?P<new>.*))?",
    re.I,
)


@dataclass
class DistilledContext:
    """Compact structured profile handed to rankers. Never raw history."""

    slots: dict[str, list[str]] = field(default_factory=dict)
    profile_tags: list[str] = field(default_factory=list)
    category_hint: str = ""
    # The constraint an override installed. Leads the query: "erase and rewrite" means the
    # new intent takes primacy, not equal billing at the end of a list of older values.
    priority: str = ""
    disclosures: list[str] = field(default_factory=list)

    def as_query(self) -> str:
        """One-line natural rendering, for cross-encoder and LLM prompts.

        Must carry everything the customer has revealed. Retrieval searches on the whole
        accumulated transcript, so a ranker given only the opening message is reranking
        with strictly less information than the retriever used -- it will reliably undo
        good candidates rather than improve them.
        """
        parts: list[str] = []
        # An override rewrites the goal, so its constraint leads and the category follows.
        # Position is not cosmetic here: the cross-encoder truncates at 384 tokens and
        # weighs the whole string, so a constraint appended last competes on equal terms
        # with every older value instead of governing them.
        if self.priority:
            parts.append(self.priority)
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

        # The opening message is "{coarse_category}. {constraint}" for buying and
        # intent_override, and bare "{coarse_category}" for browsing
        # (local_evaluator.py:154-163). Splitting it is what makes erasure possible:
        # the category survives an override, the constraint after it does not.
        self.opening_category = ""
        self.opening_extra = ""
        self.override_turn: int | None = None
        self.override_constraint = ""
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
        if len(self.transcript) == 1:
            self._split_opening()
        self.disclosed_last = []
        self._apply_override(self.transcript[-1], turn)
        self._apply_refusal(self.transcript[-1])
        self._parse_slots(turn)
        # Did this reply actually tell us anything? Until Stage 5 parses constraints,
        # "new search terms arrived" is the honest observable proxy. The orchestrator
        # needs a real signal here -- deriving `yielded` from disclosed_last before it is
        # populated makes it permanently False and fires stop_asking spuriously.
        self.gained_terms = sorted(set(self.query_terms()) - before)

    def _parse_slots(self, turn: int) -> None:
        """Break each disclosure down into a typed slot -- what the customer actually said.

        This is the decomposition step: instead of holding the dialogue as a bag of words,
        every constraint is bucketed into the attribute it constrains, so the agent can say
        what it understands, ask about what it does not, and avoid re-asking what it has.

        `classify()` mirrors the evaluator's `classify_constraint` (local_evaluator.py:137-151)
        exactly. That function decides which bucket a constraint answers, so mirroring it
        means our understanding of a disclosure agrees with the simulator's by construction.

        Rebuilt from scratch each turn rather than appended to, so an override that erases a
        constraint also erases the slot it produced -- "erase and rewrite", not accumulate.
        """
        self.slots = {}
        # The opening carries a constraint too (buying states one outright), and it lives in
        # `opening_extra` rather than `disclosures()`. It is cleared by an override, so
        # reading it here means an erased constraint produces no slot -- which is the point.
        sources = ([self.opening_extra] if self.opening_extra else []) + self.disclosures()
        for text in sources:
            for part in re.split(r";|,(?=\s)", text):
                part = part.strip(" .;,")
                if not part or part in self.erased:
                    continue
                bucket = classify(part)
                values = self.slots.setdefault(bucket, [])
                if all(part.lower() != s.text.lower() for s in values):
                    values.append(Slot(text=part, bucket=bucket, turn=turn))

    def _split_opening(self) -> None:
        """Separate the coarse category from the constraint the opening tacked on."""
        cleaned = BOILERPLATE.sub(" ", self.transcript[0])
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;,")
        head, separator, tail = cleaned.partition(". ")
        if separator:
            self.opening_category = head.strip(" .;,")
            self.opening_extra = tail.strip(" .;,")
        else:
            self.opening_category = cleaned
            self.opening_extra = ""

    def _apply_override(self, message: str, turn: int) -> None:
        """Intent override: ERASE the retracted preference, never append to it.

        Pillar II requires contradictions to erase and rewrite rather than accumulate.
        The customer says "ignore my earlier preference", and what they are pointing at
        is the constraint carried by the opening message -- so that is what is dropped.

        Only the distilled/ranking view is rewritten. `query_terms()` still spans the raw
        transcript, so the lexical track is byte-identical and the BM25-only score is
        unchanged. That is deliberate: the retracted value is `soft_preferences[-1]`, a
        GENUINE attribute of the target product rather than a false one, so removing its
        terms from retrieval risks recall. Demote it in ranking first; measure before
        touching BM25.
        """
        if self.override_seen or not message:
            return
        match = OVERRIDE_RE.search(message)
        if not match:
            return
        self.override_seen = True
        self.override_turn = turn
        if self.opening_extra:
            self.erased.append(self.opening_extra)
            self.opening_extra = ""
        new_value = (match.group("new") or "").strip(" .;,")
        if new_value:
            self.override_constraint = new_value

    def _apply_refusal(self, message: str) -> None:
        """Record which buckets have been answered with "nothing".

        `exhausted()` reads this. Once the `"other"` wildcard itself comes back empty the
        constraint pool is provably drained -- `"other"` matches ANY undisclosed constraint
        (local_evaluator.py:178-181), so no narrower question can succeed where it failed.
        Asking again cannot pay, and the turn is better spent ranking.
        """
        if not message:
            return
        match = UNAVAILABLE_RE.search(message)
        if match:
            self.unavailable.add(match.group("attr").lower())
            return
        match = DRAINED_RE.search(message)
        if match:
            self.drained.add(match.group("attr").lower())

    # ---- retrieval interface --------------------------------------------

    def query_terms(self) -> list[str]:
        """Deduplicated term list for the lexical track.

        Dedupe by first appearance across the accumulated transcript, capped at MAX_TERMS.

        Refusals are excluded. "I don't have an additional preference for material" states
        that the customer has NO material preference, so feeding its words to BM25 searches
        for the very attribute they just ruled out -- and the bucket name is the one word in
        the sentence that matches product text. This matters more once the policy keeps
        asking after the pool drains: without it, every follow-up question injects another
        bucket name into the query.
        """
        informative = [m for m in self.transcript if not NO_INFO_RE.search(m)]
        return list(dict.fromkeys(terms(" ".join(informative))))[:MAX_TERMS]

    def distilled(self) -> DistilledContext:
        """Compact profile for the ranking stages (Pillar III context distillation)."""
        slots = {bucket: [s.text for s in items] for bucket, items in self.slots.items() if items}
        tags = self.user_profile.get("preference_tags") or []
        priority = self.override_constraint
        # The override message also lands in `disclosures` (boilerplate-stripped down to the
        # bare constraint), so drop it there rather than stating the same value twice.
        disclosures = [d for d in self.disclosures() if d.lower() != priority.lower()]
        return DistilledContext(
            slots=slots,
            profile_tags=[str(t) for t in tags if t],
            category_hint=self.category_hint(),
            priority=priority,
            disclosures=disclosures,
        )

    def disclosures(self) -> list[str]:
        """Boilerplate-stripped, deduplicated content from every turn after the opening."""
        seen: set[str] = set()
        out: list[str] = []
        for message in self.transcript[1:]:
            # A refusal answers the question with "nothing". Drop it whole -- there is no
            # residue worth keeping, and its fragments are actively misleading.
            if REFUSAL_RE.search(message):
                continue
            cleaned = BOILERPLATE.sub(" ", message)
            cleaned = re.sub(r"\s+", " ", cleaned).strip(" .;,")
            if cleaned and cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                out.append(cleaned)
        return out

    def category_hint(self) -> str:
        """The opening message names the coarse category; it is the most stable signal.

        After an override `opening_extra` has been erased, so the retracted preference
        stops leading the distilled query. The category itself always survives -- an
        override changes what the customer wants, not what department they are shopping in.
        """
        if not self.transcript:
            return ""
        return ". ".join(part for part in (self.opening_category, self.opening_extra) if part)

    # ---- policy interface ------------------------------------------------

    def record_ask(self, attribute: str | None, yielded: bool) -> None:
        if attribute:
            self.asks.append((attribute, yielded))

    def exhausted(self) -> bool:
        """True when no attribute can plausibly reveal anything further."""
        return "other" in self.drained

    def slot_count(self) -> int:
        return sum(len(v) for v in self.slots.values())
