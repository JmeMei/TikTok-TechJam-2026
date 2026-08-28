from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path


TOKEN_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "i", "in", "is", "it", "me", "my", "of", "on", "or", "please", "some",
    "that", "the", "this", "to", "want", "with", "would", "you", "looking",
}


def _text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, dict):
        return " ".join(f"{key} {item}" for key, item in value.items())
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    return str(value)


def _terms(text: str) -> list[str]:
    return [
        token.lower()
        for token in TOKEN_RE.findall(text)
        if len(token) > 1 and token.lower() not in STOPWORDS
    ]


class Agent:
    """Editable weak baseline: stateless BM25 retrieval with no LLM dependency."""

    def __init__(self, catalog_path: str | Path = "data/catalog.jsonl") -> None:
        self.catalog_path = Path(catalog_path)
        self.connection = sqlite3.connect(":memory:")
        self._sessions: dict[str, list[str]] = {}
        self._build_index()

    def _build_index(self) -> None:
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
                batch.append(
                    (
                        str(product["parent_asin"]),
                        _text(product.get("title")),
                        _text(product.get("categories")),
                        _text(product.get("features")),
                        _text(product.get("details")),
                        _text(product.get("store")),
                        _text(product.get("description")),
                    )
                )
                if len(batch) >= 1000:
                    cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
                    batch.clear()
        if batch:
            cursor.executemany("INSERT INTO products VALUES (?, ?, ?, ?, ?, ?, ?)", batch)
        self.connection.commit()

    def reset(self, session_id: str, user_profile: dict) -> None:
        # The profile is anonymized and may be used for personalization.
        # Start an empty transcript for this session.
        self._sessions[session_id] = []

    def respond(
        self,
        session_id: str,
        user_message: str,
        turn: int,
        top_k: int,
    ) -> dict:
        if session_id not in self._sessions:
            raise RuntimeError("reset must be called before respond")
        # CHANGE 1: accumulate. Search everything the customer has said so far,
        # not just the newest sentence.
        history = self._sessions[session_id]
        history.append(user_message)
        unique_terms = list(dict.fromkeys(_terms(" ".join(history))))[:60]
        expression = " OR ".join(f'"{term}"' for term in unique_terms)
        if not expression:
            recommendations: list[dict] = []
        else:
            rows = self.connection.execute(
                "SELECT parent_asin FROM products WHERE products MATCH ? "
                "ORDER BY bm25(products, 0.0, 6.0, 4.0, 2.5, 2.5, 1.5, 1.0) LIMIT ?",
                (expression, top_k),
            ).fetchall()
            recommendations = [{"parent_asin": str(row[0])} for row in rows]
        return {
            "message": "Here are the closest matches I found. Anything else that matters?",
            # CHANGE 2: always ask. A null attribute makes the customer reveal
            # nothing at all; "other" matches any constraint it has not told us yet.
            "ask_attribute": "other",
            "recommendations": recommendations,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
        }
