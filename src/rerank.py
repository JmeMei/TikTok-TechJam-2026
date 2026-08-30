"""Ranking cascade (Pillar I) -- owned by the `retrieval` agent.

    fused candidates -> cross-encoder pre-filter -> LLM semantic ranking -> top 10

Contract: this module NEVER issues new retrievals. It reorders a fixed candidate set.

The fallback ladder, applied on absent weights, timeout, or malformed output:
    LLM -> cross-encoder -> incoming (RRF) order
Each rung degrades to the next silently. A ranking stage may make the order worse; it
may never make the run fail.

HARD RULES for every stage here (competition_specification.md:65):
  * reorder only -- never introduce a parent_asin the caller did not supply
  * never return fewer items than were asked for when that many are available
  * never raise
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from src.llm import LLMBackend

CROSS_ENCODER_DIR = Path("models/cross-encoder")

# Funnel widths. Budgeted knob (CLAUDE.md section 9) -- k-fold before changing.
# CE_WIDTH is how many fused candidates the cross-encoder scores. Cost is linear in it,
# and it was the dominant term in a 25s/session smoke run at 50.
CE_WIDTH = 25
PREFILTER_WIDTH = 15


@dataclass
class RankResult:
    order: list[str]
    stage_reached: str = "rrf"
    movement: list[tuple[str, int, int]] = field(default_factory=list)
    flat: bool = False           # scores did not separate -- triggers escalation/clarify
    tokens: int = 0


def _movement(before: list[str], after: list[str]) -> list[tuple[str, int, int]]:
    positions = {asin: i + 1 for i, asin in enumerate(before)}
    moved: list[tuple[str, int, int]] = []
    for new_index, asin in enumerate(after, start=1):
        old_index = positions.get(asin)
        if old_index is not None and old_index != new_index:
            moved.append((asin, old_index, new_index))
    return moved


def _is_flat(scores: list[float], tolerance: float = 0.05) -> bool:
    """True when the top scores do not separate -- the ranker has no opinion.

    Feeds two things: the over-generality cutoff (Pillar II) and listwise escalation
    (Pillar III). Both need to know when ranking is guessing.
    """
    if len(scores) < 2:
        return False
    top = sorted(scores, reverse=True)[: min(5, len(scores))]
    spread = top[0] - top[-1]
    return spread < tolerance


class CrossEncoder:
    """Pairwise relevance pre-filter. Narrows the fused pool for the LLM stage."""

    def __init__(self, model_dir: str | Path = CROSS_ENCODER_DIR) -> None:
        self.model_dir = Path(model_dir)
        self.available = False
        self._model = None
        self._tokenizer = None
        self._load()

    def _load(self) -> None:
        if not self.model_dir.exists():
            return
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                str(self.model_dir), local_files_only=True
            )
            self._model = AutoModelForSequenceClassification.from_pretrained(
                str(self.model_dir), local_files_only=True
            )
            # Weights are stored fp16 to fit in git, but CPU has no fast fp16 kernels --
            # torch either upcasts per-op or takes a slow path, so running fp16 on CPU is
            # markedly SLOWER than fp32. Upcast once at load; disk size is unaffected.
            self._model = self._model.float()
            self._model.eval()
            self.available = True
        except Exception:
            self.available = False

    def score(self, query: str, documents: list[str]) -> list[float] | None:
        if not self.available or not documents:
            return None
        try:
            import torch

            batch = self._tokenizer(
                [query] * len(documents),
                documents,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=384,
            )
            with torch.no_grad():
                logits = self._model(**batch).logits
            return logits.squeeze(-1).tolist() if logits.shape[-1] == 1 else logits[:, -1].tolist()
        except Exception:
            return None


class Ranker:
    """The cascade. One entry point so agent.py never branches on model availability."""

    def __init__(
        self,
        doc_text: dict[str, str] | None = None,
        cross_encoder: CrossEncoder | None = None,
        llm: LLMBackend | None = None,
    ) -> None:
        self.doc_text = doc_text or {}
        self.cross_encoder = cross_encoder if cross_encoder is not None else CrossEncoder()
        self.llm = llm if llm is not None else LLMBackend()

    def _documents(self, candidates: list[str]) -> list[str]:
        return [self.doc_text.get(asin, asin) for asin in candidates]

    def rank(self, query: str, candidates: list[str], limit: int = 10) -> RankResult:
        """Reorder `candidates`, returning at most `limit`.

        Stage 1 is a passthrough: no weights are installed yet, so the RRF/BM25 order
        survives untouched and the score is unchanged. Stages 2 and 4 fill in the rungs.
        """
        if not candidates:
            return RankResult(order=[], stage_reached="rrf")

        incoming = list(candidates)
        order = incoming
        stage = "rrf"
        flat = False
        tokens = 0

        if query:
            # --- rung 1: cross-encoder over the head of the fused list ---
            # Only the head is scored: cost is linear in width, and an item the fused
            # ranking put 40th is not a plausible rank-1. The tail keeps its RRF order.
            head_in = incoming[:CE_WIDTH]
            tail = incoming[CE_WIDTH:]
            ce_scores = self.cross_encoder.score(query, self._documents(head_in))
            if ce_scores and len(ce_scores) == len(head_in):
                paired = sorted(zip(head_in, ce_scores), key=lambda p: -p[1])
                order = [asin for asin, _ in paired] + tail
                stage = "cross_encoder"
                flat = _is_flat([s for _, s in paired])

                # --- rung 2: LLM semantic ranking over the narrowed head ---
                head = order[:PREFILTER_WIDTH]
                llm_scores = self.llm.score(query, self._documents(head))
                if llm_scores and len(llm_scores) == len(head):
                    ranked_head = [
                        asin for asin, _ in sorted(zip(head, llm_scores), key=lambda p: -p[1])
                    ]
                    order = ranked_head + order[PREFILTER_WIDTH:]
                    stage = "llm"
                    flat = _is_flat(llm_scores)
                    tokens = self.llm.tokens_used

        # Invariant: reorder only. If any stage altered the candidate set, discard its
        # work and fall back to the incoming order rather than serving a corrupted list.
        # Not an assert: asserts raise (and vanish under -O), and this path must never
        # raise (competition_specification.md:65).
        if set(order) != set(incoming):
            order, stage = incoming, "rrf"

        return RankResult(
            order=order[:limit],
            stage_reached=stage,
            movement=_movement(incoming, order)[:limit],
            flat=flat,
            tokens=tokens,
        )
