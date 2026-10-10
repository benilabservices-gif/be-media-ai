"""Limitation de débit en mémoire, par fenêtre glissante (ARCHITECTURE.md §14.1).

Suffisant tant que l'API tourne sur une seule instance. Au-delà, remplacer le stockage par
Redis derrière la même interface `hit()`.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass

from digital360.core.errors import AppError


@dataclass(frozen=True)
class Limit:
    max_hits: int
    window_seconds: float


class RateLimitedError(AppError):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            "RATE_LIMITED",
            "Trop de tentatives. Réessayez dans quelques instants.",
            status=429,
            title="Trop de requêtes",
            headers={"Retry-After": str(retry_after)},
        )
        self.retry_after = retry_after


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def hit(self, key: str, limit: Limit) -> None:
        """Enregistre une tentative ; lève RateLimitedError si la limite est dépassée."""
        now = self._clock()
        hits = self._hits[key]
        while hits and hits[0] <= now - limit.window_seconds:
            hits.popleft()
        if len(hits) >= limit.max_hits:
            retry_after = int(hits[0] + limit.window_seconds - now) + 1
            raise RateLimitedError(retry_after)
        hits.append(now)

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)
