"""Dashboard sessions: the browser keeps an opaque cookie, not the admin key.

The key is only ever exchanged once, at sign-in, over the same origin; everything
else rides on a random session token. Sessions are in memory, so a restart signs
everyone out — which is the right default for a single-admin panel.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass

COOKIE_NAME = "muse2api_session"

# Long enough for a working day, short enough that a forgotten tab expires.
SESSION_TTL = 12 * 3600.0


@dataclass
class Session:
    token: str
    created_at: float
    last_seen: float


class SessionStore:
    def __init__(self, ttl: float = SESSION_TTL, max_sessions: int = 20) -> None:
        self.ttl = ttl
        self.max_sessions = max_sessions
        self._items: dict[str, Session] = {}

    def create(self) -> Session:
        self._prune()
        token = secrets.token_urlsafe(32)
        now = time.time()
        session = Session(token=token, created_at=now, last_seen=now)
        self._items[token] = session
        while len(self._items) > self.max_sessions:
            oldest = min(self._items, key=lambda key: self._items[key].last_seen)
            self._items.pop(oldest, None)
        return session

    def get(self, token: str | None) -> Session | None:
        """Return a live session and slide its expiry; an expired token is forgotten."""
        if not token:
            return None
        session = self._items.get(token)
        if session is None:
            return None
        if session.last_seen + self.ttl < time.time():
            self._items.pop(token, None)
            return None
        session.last_seen = time.time()
        return session

    def drop(self, token: str | None) -> None:
        if token:
            self._items.pop(token, None)

    def _prune(self) -> None:
        cutoff = time.time() - self.ttl
        for token in [t for t, s in self._items.items() if s.last_seen < cutoff]:
            self._items.pop(token, None)

    def __len__(self) -> int:
        return len(self._items)
