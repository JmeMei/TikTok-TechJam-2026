"""Embed the 50k-product catalog once -> data/index/emb.f16.npy + ids.json.

Run once at setup:  python scripts/build_index.py   (or: make index)

The matrix is fp16 (50000 x 384 = ~37MB) and IS committed, so scoring never has to
rebuild it and a fresh clone gets the dense track for free. Only the query side needs
the encoder at runtime.

The catalog is strictly read-only (CLAUDE.md section 4). This writes to data/index/,
a new directory; it never touches data/catalog.jsonl.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

CATALOG = Path("data/catalog.jsonl")
OUT_DIR = Path("data/index")
ENCODER_DIR = Path("models/bi-encoder")
BATCH = 128
MAX_LEN = 192


def product_text(product: dict) -> str:
    """What we embed. Title carries most of the signal; a couple of features add context.

    Deliberately not the whole record -- long, boilerplate-heavy descriptions dilute the
    embedding and slow encoding by several times for no retrieval benefit.
    """
    parts = [str(product.get("title") or "")]
    categories = product.get("categories") or []
    if categories:
        parts.append(" ".join(str(c) for c in categories[-2:]))
    features = product.get("features") or []
    if features:
        parts.append(" ".join(str(f) for f in features[:3]))
    return " ".join(p for p in parts if p)[:600]


def main() -> int:
    if not CATALOG.exists():
        print(f"missing {CATALOG} -- see BOOTSTRAP.md")
        return 1
    if not (ENCODER_DIR / "config.json").exists():
        print(f"missing {ENCODER_DIR} -- run scripts/fetch_models.py first")
        return 1

    import numpy as np
    import torch
    from transformers import AutoModel, AutoTokenizer

    print("loading catalog ...")
    ids: list[str] = []
    texts: list[str] = []
    with CATALOG.open(encoding="utf-8") as handle:
        for line in handle:
            product = json.loads(line)
            ids.append(str(product["parent_asin"]))
            texts.append(product_text(product))
    print(f"  {len(ids)} products")

    tokenizer = AutoTokenizer.from_pretrained(str(ENCODER_DIR), local_files_only=True)
    model = AutoModel.from_pretrained(str(ENCODER_DIR), local_files_only=True).float()
    model.eval()

    vectors: list["np.ndarray"] = []
    total = (len(texts) + BATCH - 1) // BATCH
    for index in range(0, len(texts), BATCH):
        chunk = texts[index : index + BATCH]
        batch = tokenizer(
            chunk, padding=True, truncation=True, max_length=MAX_LEN, return_tensors="pt"
        )
        with torch.no_grad():
            output = model(**batch).last_hidden_state
            # Mean-pool over real tokens only -- padding must not drag the vector.
            mask = batch["attention_mask"].unsqueeze(-1).float()
            pooled = (output * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
            # L2-normalise so cosine similarity is a plain dot product at query time.
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
        vectors.append(pooled.cpu().numpy().astype("float16"))
        step = index // BATCH + 1
        if step % 25 == 0 or step == total:
            print(f"  {step}/{total} batches")

    matrix = np.vstack(vectors)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    np.save(OUT_DIR / "emb.f16.npy", matrix)
    (OUT_DIR / "ids.json").write_text(json.dumps(ids), encoding="utf-8")
    size_mb = (OUT_DIR / "emb.f16.npy").stat().st_size / 1e6
    print(f"wrote {matrix.shape} fp16 -> {OUT_DIR}/emb.f16.npy ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
