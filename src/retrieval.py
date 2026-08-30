"""Retrieval tracks and fusion (Pillar I) -- owned by the `retrieval` agent.

Contract: this module NEVER reads dialogue state. It takes plain terms/text plus a
filter dict and returns ranked (parent_asin, score) pairs. Callers do the translation.

  BM25Index.search(terms, limit, filters)   -> lexical / "buying" precision track
  DenseIndex.search(text, limit, mmr)       -> dense / "browsing" diversity track
  rrf_fuse(rankings, weights)               -> reciprocal-rank fusion

Everything is in-process and in-memory. No vector DB server (explicitly out of scope).
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

# Field weights carried over from the starter's ORDER BY. Changing these changes the
# score, so they stay put until an experiment measures a replacement.
BM25_WEIGHTS = (0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0)


def flatten(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


class BM25Index:
    """SQLite FTS5 full-text index over the frozen catalog. Read-only, in-memory."""

    def __init__(self, catalog_path: str | Path) -> None:
        self.catalog_path = Path(catalog_path)
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        # parent_asin -> minimal record, for filtering and for building ranker prompts.
        self.meta: dict[str, dict] = {}
        self._build()

    def _build(self) -> None:
        cursor = self.connection.cursor()
        cursor.execute(
            "CREATE VIRTUAL TABLE products USING fts5("
            "parent_asin UNINDEXED, title, categories, features, details, store, description, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        batch: list[tuple[str, str, str, str, str, str, str]] = []
        with self.catalog_path.open(encoding="utf-8") as handle:
            for line in handle:
                product = json.loads(line)
                parent_asin = str(product["parent_asin"])
                batch.append((
                    parent_asin,
                    flatten(product.get("title")),
                    flatten(product.get("categories")),
                    flatten(product.get("features")),
                    flatten(product.get("details")),
                    flatten(product.get("store")),
                    flatten(product.get("description")),
                ))
                self.meta[parent_asin] = {
                    "title": product.get("title") or "",
                    "price": product.get("price"),
                    "categories": product.get("categories") or [],
                    "average_rating": product.get("average_rating"),
                    "features": product.get("features") or [],
                    "details": product.get("details") or {},
                }
                if len(batch) >= 1000:
                    cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                    batch.clear()
        if batch:
            cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
        self.connection.commit()

    def search(
        self,
        terms: list[str],
        limit: int = 10,
        filters: dict | None = None,
    ) -> list[tuple[str, float]]:
        """Rank by BM25 over an OR-expression of the given terms.

        `filters` is accepted now so the buying track's hard-constraint pre-filter
        (Stage 3) can be added without changing this signature. Unused at Stage 1.
        """
        if not terms:
            return []
        expression = " OR ".join(f'"{term}"' for term in terms)
        weights = ", ".join(str(w) for w in BM25_WEIGHTS)
        try:
            rows = self.connection.execute(
                f"SELECT parent_asin, bm25(products, {weights}) AS score FROM products "
                f"WHERE products MATCH ? ORDER BY score LIMIT ?",
                (expression, limit),
            ).fetchall()
        except sqlite3.Error:
            return []
        # FTS5 bm25() is more-negative-is-better; negate so higher is better everywhere.
        return [(str(row[0]), -float(row[1])) for row in rows]

    def __contains__(self, parent_asin: str) -> bool:
        return parent_asin in self.meta


class DenseIndex:
    """Dense vector track. Populated in Stage 3 by `scripts/build_index.py`.

    Absent index files are not an error -- `available` stays False and the caller
    skips the dense track. That is the first rung of the fallback ladder.
    """

    def __init__(self, index_dir: str | Path = "data/index") -> None:
        self.index_dir = Path(index_dir)
        self.available = False
        self.ids: list[str] = []
        self._matrix = None
        self._encoder = None
        self._load()

    def _load(self) -> None:
        matrix_path = self.index_dir / "emb.f16.npy"
        ids_path = self.index_dir / "ids.json"
        if not (matrix_path.exists() and ids_path.exists()):
            return
        try:
            import numpy as np

            self._matrix = np.load(matrix_path)
            self.ids = json.loads(ids_path.read_text(encoding="utf-8"))
            self.available = len(self.ids) == self._matrix.shape[0]
        except Exception:
            self.available = False

    def search(
        self,
        text: str,
        limit: int = 50,
        mmr: bool = False,
    ) -> list[tuple[str, float]]:
        """Cosine top-k. `mmr=True` applies diversity for the browsing track (Stage 3)."""
        if not self.available or not text:
            return []
        return []  # implemented in Stage 3


def rrf_fuse(
    rankings: list[list[str]],
    weights: list[float] | None = None,
    k: int = 60,
) -> list[str]:
    """Reciprocal Rank Fusion.

    score(d) = sum_i  w_i / (k + rank_i(d))

    Rank-based rather than score-based, so the lexical and dense tracks need no score
    normalisation between them -- which is exactly why RRF suits a hybrid pipeline.
    """
    if not rankings:
        return []
    if weights is None:
        weights = [1.0] * len(rankings)

    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights):
        for position, parent_asin in enumerate(ranking, start=1):
            scores[parent_asin] = scores.get(parent_asin, 0.0) + weight / (k + position)
    return sorted(scores, key=lambda asin: -scores[asin])
