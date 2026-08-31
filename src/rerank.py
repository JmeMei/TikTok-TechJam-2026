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

import os
from dataclasses import dataclass, field
from pathlib import Path

from src.llm import LLMBackend

FALLBACK_CE_DIR = Path("models/cross-encoder")   # committed; always present
PRIMARY_CE_DIR = Path("models/ce-bge")           # fetched by scripts/fetch_models.py


def _cross_encoder_dir() -> Path:
    """Prefer the fetched bge reranker; fall back to the committed MiniLM.

    bge-reranker-base is worth +0.037 TechnicalScore over MiniLM-L6 once the document
    representation includes `details` (eval/RESULTS.md run 7), but at ~573MB it is fetched
    rather than committed. Selecting by presence means a fresh clone works immediately at
    the lower score and improves after setup, with no config to get wrong.
    """
    override = os.environ.get("TECHJAM_CE_DIR", "").strip()
    if override:
        return Path(override)
    return PRIMARY_CE_DIR if (PRIMARY_CE_DIR / "config.json").exists() else FALLBACK_CE_DIR


CROSS_ENCODER_DIR = _cross_encoder_dir()

# Funnel widths. A tuned knob -- k-fold before changing; the budget is ~3 in total.
# CE_WIDTH is how many fused candidates the cross-encoder scores. Cost is linear in it,
# and it was the dominant term in a 25s/session smoke run at 50.
# DEFAULT ON at 25 -- see eval/RESULTS.md runs 3c-5. It was default-off while the distilled
# query was still poisoned by the un-erased override decoy (then -0.0077 net). With erasure,
# promotion and the adaptive skip in place it is +0.049 on buying and +0.026 on browsing,
# with intent_override held at exactly its lexical-track value by the orchestrator's
# skip_rerank gate. Aggregate 0.76125 -> 0.77773.
# Caveat kept deliberately visible: the paired 95% CI still crosses zero at n=200, and
# boundary (n=10) regresses. Set TECHJAM_CE_WIDTH=0 to fall back to the pure lexical track.
CE_WIDTH = int(os.environ.get("TECHJAM_CE_WIDTH", "25"))
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
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            self._tokenizer = AutoTokenizer.from_pretrained(
                str(self.model_dir), local_files_only=True
            )
            self._model = AutoModelForSequenceClassification.from_pretrained(
                str(self.model_dir), local_files_only=True
            )

            # Precision is a per-device decision, and the two devices want opposites.
            #
            # CPU: weights are stored fp16 to fit in git, but CPU has no fast fp16 kernels
            # -- torch upcasts per-op or takes a slow path, so fp16 on CPU is markedly
            # SLOWER than fp32. Upcast once at load.
            #
            # CUDA: fp16 runs on tensor cores and is the faster path, so keep the stored
            # precision. This is where most of the speedup comes from, not the move alone.
            self._device = self._pick_device(torch)
            if self._device == "cuda":
                self._model = self._model.half().to("cuda")
            else:
                self._model = self._model.float()
            self._model.eval()
            self.available = True
        except Exception:
            self.available = False

    @staticmethod
    def _pick_device(torch) -> str:
        """CUDA when it is present and not explicitly refused.

        TECHJAM_DEVICE=cpu forces the CPU path even on a CUDA box -- needed because GPU
        and CPU do not produce bit-identical scores (different matmul reduction orders),
        and a reported number must be reproducible on the machine that produced it.
        """
        want = os.environ.get("TECHJAM_DEVICE", "").strip().lower()
        if want in ("cpu", "cuda"):
            return want if (want == "cpu" or torch.cuda.is_available()) else "cpu"
        return "cuda" if torch.cuda.is_available() else "cpu"

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
            device = getattr(self, "_device", "cpu")
            if device == "cuda":
                batch = {key: value.to("cuda") for key, value in batch.items()}
            with torch.no_grad():
                logits = self._model(**batch).logits
            # Back to fp32 on the host before .tolist(): fp16 rounding is coarse enough to
            # create ties between candidates that are genuinely ordered, and a tie here
            # silently changes the rank the session is scored on.
            logits = logits.float().cpu()
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

    def rank(
        self,
        query: str,
        candidates: list[str],
        limit: int = 10,
        use_cross_encoder: bool = True,
    ) -> RankResult:
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

        if query and use_cross_encoder:
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
