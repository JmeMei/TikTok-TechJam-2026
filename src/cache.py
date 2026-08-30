"""Content-keyed memoisation.

CRITICAL: never key on session_id. local_evaluator.py:227 mints a fresh uuid4() for every
session, so a session-keyed cache has a 0% hit rate. Key on the hash of the actual
content being scored -- that repeats constantly across turns and across sessions, because
many sessions converge on similar constraint sets.
"""

from __future__ import annotations

import hashlib
from collections import OrderedDict


def key_for(*parts: object) -> str:
    digest = hashlib.blake2b(digest_size=16)
    for part in parts:
        digest.update(str(part).encode("utf-8", "replace"))
        digest.update(b"\x00")
    return digest.hexdigest()


class LRUCache:
    """Bounded LRU. Bounded because an 800-session private run must not grow without limit."""

    def __init__(self, capacity: int = 20000) -> None:
        self.capacity = capacity
        self._store: OrderedDict[str, object] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, key: str, default=None):
        if key in self._store:
            self._store.move_to_end(key)
            self.hits += 1
            return self._store[key]
        self.misses += 1
        return default

    def put(self, key: str, value: object) -> None:
        self._store[key] = value
        self._store.move_to_end(key)
        while len(self._store) > self.capacity:
            self._store.popitem(last=False)

    def stats(self) -> dict[str, int | float]:
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hits / total, 4) if total else 0.0,
            "size": len(self._store),
        }
