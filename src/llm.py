"""Generative backend for the LLM semantic ranking stage (Pillar I).

Three backends, resolved once at construction:
  * local   -- Qwen via transformers, weights under models/qwen/ (fetch_llm.py)
  * api     -- optional, behind TECHJAM_LLM_API env var; prototyping only, never required
  * none    -- weights absent or load failed; caller falls through to the cross-encoder

CRITICAL INVARIANT (competition_specification.md:65 -- "Exceptions, invalid output, and
timeouts may count as a miss"): nothing in this module may raise to the caller, and every
call runs under a wall-clock budget. CLAUDE.md section 4 requires a *timeout*, not merely
a try/except -- a try/except catches a raise but not a hang.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path

DEFAULT_MODEL_DIR = Path("models/qwen")

# Per-call wall-clock budgets. Deliberately tight: a slow turn costs 0.02 (one turn),
# but a hung run costs everything.
POINTWISE_BUDGET_S = float(os.environ.get("TECHJAM_LLM_BUDGET", "8.0"))
LOAD_BUDGET_S = float(os.environ.get("TECHJAM_LLM_LOAD_BUDGET", "120.0"))


def llm_disabled() -> bool:
    """`make eval-fast` sets this to skip the LLM stage while iterating."""
    return os.environ.get("TECHJAM_DISABLE_LLM", "").strip() not in ("", "0", "false")


def _run_with_timeout(fn, budget_s: float, default):
    """Run `fn` on a worker thread, abandoning it if it exceeds the budget.

    The thread is a daemon: if the model hangs we stop waiting and let the process exit
    cleanly at the end of the run rather than blocking on join.
    """
    box: dict[str, object] = {}

    def target() -> None:
        try:
            box["value"] = fn()
        except Exception as exc:  # noqa: BLE001 -- degradation is the contract here
            box["error"] = exc

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(budget_s)
    if worker.is_alive() or "value" not in box:
        return default
    return box["value"]


class LLMBackend:
    """Pointwise relevance scorer.

    Scores by the logprob of the `yes` token given a compact relevance prompt -- one
    forward pass per candidate, no autoregressive decoding. That is what makes an LLM
    affordable on the default path rather than a gated escape hatch.
    """

    def __init__(self, model_dir: str | Path = DEFAULT_MODEL_DIR) -> None:
        self.model_dir = Path(model_dir)
        self.kind = "none"
        self.name = ""
        self._model = None
        self._tokenizer = None
        self._yes_id: int | None = None
        self.tokens_used = 0
        if not llm_disabled():
            self._load()

    @property
    def available(self) -> bool:
        return self.kind != "none"

    def _load(self) -> None:
        if not self.model_dir.exists():
            return

        def _do_load():
            import torch  # noqa: F401  (import cost belongs inside the budget)
            from transformers import AutoModelForCausalLM, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(str(self.model_dir), local_files_only=True)
            model = AutoModelForCausalLM.from_pretrained(
                str(self.model_dir), local_files_only=True, dtype="auto"
            )
            model.eval()
            return tokenizer, model

        loaded = _run_with_timeout(_do_load, LOAD_BUDGET_S, None)
        if loaded is None:
            return
        self._tokenizer, self._model = loaded
        try:
            ids = self._tokenizer.encode("yes", add_special_tokens=False)
            self._yes_id = ids[0] if ids else None
        except Exception:
            self._yes_id = None
        if self._yes_id is not None:
            self.kind = "local"
            self.name = self.model_dir.name

    def score(self, query: str, documents: list[str]) -> list[float] | None:
        """Return one relevance score per document, or None to fall through.

        None is the signal to use the previous stage's ordering unchanged -- never an
        exception, and never a partial list.
        """
        if not self.available or not documents:
            return None
        scores = _run_with_timeout(
            lambda: self._score_batch(query, documents), POINTWISE_BUDGET_S, None
        )
        if not isinstance(scores, list) or len(scores) != len(documents):
            return None
        return scores

    def _score_batch(self, query: str, documents: list[str]) -> list[float]:
        import torch

        prompts = [
            "You match shoppers to products. Shopper wants:\n"
            f"{query}\n\nProduct:\n{doc}\n\nIs this product a good match? Answer yes or no.\nAnswer:"
            for doc in documents
        ]
        tokenizer, model = self._tokenizer, self._model
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token

        batch = tokenizer(
            prompts, return_tensors="pt", padding=True, truncation=True, max_length=512
        )
        self.tokens_used += int(batch["input_ids"].numel())
        with torch.no_grad():
            logits = model(**batch).logits[:, -1, :]
            logprobs = torch.log_softmax(logits.float(), dim=-1)
        return logprobs[:, self._yes_id].tolist()
