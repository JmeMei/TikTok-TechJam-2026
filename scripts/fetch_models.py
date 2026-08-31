"""Vendor the two ranking encoders into models/, converted to fp16.

Run once at setup:  python scripts/fetch_models.py

These two are COMMITTED to the repo. They are the offline floor: submission_rules.md
warns that "organizer policy may disable network access" for final scoring, so anything
the scored path depends on must already be present. fp16 halves them to ~45MB each,
under GitHub's 100MB/file limit, so no LFS is needed.

The ranking LLM is handled separately by fetch_llm.py -- it is too large to commit and
the cascade degrades without it.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

MODELS = {
    "bi-encoder": "sentence-transformers/all-MiniLM-L6-v2",
    "cross-encoder": "cross-encoder/ms-marco-MiniLM-L-6-v2",
}

# The primary reranker. NOT committed: at ~573MB it exceeds GitHub's 100MB/file limit, and
# final_evaluation_faq.md section 4 states that "large assets should be supplied through
# documented and reproducible download instructions rather than committed directly to the
# repository. There is currently no track-specific package-size limit." So a fetch script is
# the preferred form, not a workaround.
#
# Worth +0.037 TechnicalScore over the committed MiniLM cross-encoder once the document
# representation includes `details` (eval/RESULTS.md run 7). The committed MiniLM remains the
# fallback, so the agent still runs -- lower -- if this is never fetched.
OPTIONAL = {
    "ce-bge": "BAAI/bge-reranker-base",
}
OUT = Path("models")


def fetch(name: str, repo: str) -> bool:
    target = OUT / name
    if (target / "config.json").exists():
        print(f"  {name:14} already present -> {target}")
        return True

    try:
        import torch
        from transformers import AutoModel, AutoModelForSequenceClassification, AutoTokenizer
    except ImportError as exc:
        print(f"  {name:14} FAILED: {exc}")
        return False

    print(f"  {name:14} downloading {repo} ...")
    try:
        tokenizer = AutoTokenizer.from_pretrained(repo)
        if name == "bi-encoder":
            model = AutoModel.from_pretrained(repo)
        else:
            model = AutoModelForSequenceClassification.from_pretrained(repo)

        # fp16 on disk. Inference upcasts as needed; this is purely a storage decision
        # so the weights fit in git without LFS.
        model = model.half()
        target.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(target, safe_serialization=True)
        tokenizer.save_pretrained(target)
    except Exception as exc:  # noqa: BLE001
        print(f"  {name:14} FAILED: {exc}")
        shutil.rmtree(target, ignore_errors=True)
        return False

    size_mb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1e6
    print(f"  {name:14} OK -> {target}  ({size_mb:.1f} MB)")
    if size_mb > 100 and name in MODELS:
        print(f"  {'':14} WARNING: >100MB, GitHub will reject without LFS")
    return True


def main() -> int:
    OUT.mkdir(exist_ok=True)
    print("Vendoring ranking encoders (committed to the repo -- the offline floor):")
    ok = all(fetch(name, repo) for name, repo in MODELS.items())

    print("\nFetching the primary reranker (NOT committed -- see module docstring):")
    primary = all(fetch(name, repo) for name, repo in OPTIONAL.items())
    if not primary:
        print(f"  {'':14} the agent still runs on the committed MiniLM, scoring lower")

    if ok:
        print("\nDone. The committed pair alone runs the pipeline fully offline.")
    else:
        print("\nSome downloads failed. The agent still runs -- the cascade degrades to BM25.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
