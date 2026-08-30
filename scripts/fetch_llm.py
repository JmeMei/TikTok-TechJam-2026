"""Download the ranking LLM into models/qwen/.

Run once at setup:  python scripts/fetch_llm.py [--model Qwen/Qwen2.5-0.5B-Instruct]

NOT committed -- ~1-3GB is beyond what belongs in a git repo, and submission_rules.md
explicitly provides for "dependency installation steps" that the organizer runs anyway
to install torch. If these weights are absent the ranking cascade drops one tier to the
cross-encoder, which IS vendored. The worst case is a lower score, never a crash.

Default is the 1.5B; pass --model for the 0.5B if CPU inference is too slow.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

DEFAULT_MODEL = "Qwen/Qwen2.5-1.5B-Instruct"
OUT = Path("models/qwen")


def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch the ranking LLM")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--out", default=str(OUT))
    args = parser.parse_args()

    target = Path(args.out)
    if (target / "config.json").exists():
        print(f"already present -> {target}")
        return 0

    try:
        from transformers import AutoModelForCausalLM, AutoTokenizer
    except ImportError as exc:
        print(f"FAILED: {exc}\nInstall requirements.txt first.")
        return 1

    print(f"downloading {args.model} -> {target}")
    print("(this is a multi-GB download; the agent works without it, one tier lower)")
    try:
        tokenizer = AutoTokenizer.from_pretrained(args.model)
        model = AutoModelForCausalLM.from_pretrained(args.model, dtype="auto")
        target.mkdir(parents=True, exist_ok=True)
        model.save_pretrained(target, safe_serialization=True)
        tokenizer.save_pretrained(target)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED: {exc}")
        shutil.rmtree(target, ignore_errors=True)
        return 1

    size_mb = sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1e6
    print(f"OK -> {target}  ({size_mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
