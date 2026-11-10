"""Authentication and rate limiting for the local API.

Why this module exists
----------------------
The device transport (:mod:`server.ws_voice`) was behind a token, but the REST
app it is mounted on was not: with ``PVAI_HOST=0.0.0.0`` — the documented way to
let a LAN device connect — anyone on that network could read every stored
transcript (``GET /api/sessions/{id}``), delete them (``DELETE``), and spend the
local LLM through ``/api/chat`` and ``/v1/chat/completions``.  ``/ready`` and
``/api/sessions`` also disclosed the data directory.

Everything here is deliberately small and dependency-free: a bearer-token check
(constant time, via :func:`server.protocol.check_token`) and a token-bucket rate
limiter per client address.  Rate limiting is not a substitute for authentication
— it exists so an authenticated but buggy client (or a retry storm after a
network glitch) cannot pin the CPU that the voice pipeline needs.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

PUBLIC_PATHS = frozenset({"/health"})
"""The only routes reachable without a token.

``/health`` stays open because a supervisor needs a liveness probe, and it
returns nothing sensitive (no paths, no transcripts).  Everything else — the
docs UI, ``/ready``, ``/api/*``, ``/v1/*`` — requires the bearer token.
"""


def _bearer_token(header: str | None) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.strip().lower() != "bearer":
        return None
    token = value.strip()
    return token or None


@dataclass
class RateLimiter:
    """Token bucket per client address.

    ``rate`` is the sustained rate per second, ``burst`` the bucket size.  A
    ``rate`` of 0 disables limiting (the caller then does not build one).  The
    bucket refills continuously, so a client that idles briefly gets its burst
    back instead of being punished for evenly spaced requests.
    """

    rate: float
    burst: int
    now: object = time.monotonic
    max_keys: int = 1024
    """Upper bound on tracked client addresses.

    The bucket map is keyed by client address, so a scanner walking the network
    (or a spoofed ``X-Forwarded-For``) would otherwise grow it without limit —
    a slow memory leak in a process that is meant to run for days.  When the
    map is full the least recently seen buckets are dropped, which only grants
    a full bucket to an address that has been idle long enough to refill anyway.
    """
    _buckets: dict[str, tuple[float, float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def check(self, key: str) -> tuple[bool, float]:
        """Consume one token for ``key``; returns ``(allowed, retry_after_s)``."""
        if self.rate <= 0:
            return True, 0.0
        now = self.now()
        capacity = float(max(1, self.burst))
        with self._lock:
            tokens, updated = self._buckets.get(key, (capacity, now))
            tokens = min(capacity, tokens + (now - updated) * self.rate)
            if tokens < 1.0:
                self._buckets[key] = (tokens, now)
                self._trim()
                missing = 1.0 - tokens
                return False, missing / self.rate
            self._buckets[key] = (tokens - 1.0, now)
            self._trim()
            return True, 0.0

    def _trim(self) -> None:
        """Keep the bucket map bounded (caller holds the lock)."""
        limit = max(1, int(self.max_keys))
        overflow = len(self._buckets) - limit
        if overflow <= 0:
            return
        # Oldest ``updated`` first: an idle address has refilled to capacity, so
        # forgetting it does not weaken the limit.
        for key in sorted(self._buckets, key=lambda item: self._buckets[item][1])[:overflow]:
            del self._buckets[key]

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


class ConnectionLimiter:
    """Concurrency and connect-rate limit for the device transport.

    Each device connection owns a Whisper model, a Piper voice and an asyncio
    pipeline, so an unbounded number of them is a memory/CPU denial of service
    even with valid tokens.  ``max_connections`` caps concurrency,
    ``connect_rate_per_minute`` stops a reconnect loop from thrashing models.
    """

    def __init__(
        self,
        max_connections: int = 4,
        connect_rate_per_minute: float = 0.0,
        *,
        now: object = time.monotonic,
    ) -> None:
        self.max_connections = max(0, int(max_connections))
        self._rate = max(0.0, float(connect_rate_per_minute)) / 60.0
        self.now = now
        self.active = 0
        self.rejected_full = 0
        self.rejected_rate = 0
        self._tokens = float(max(1, max_connections))
        self._updated = now()
        self._lock = threading.Lock()

    def acquire(self) -> tuple[bool, str]:
        """Reserve a connection slot; ``(ok, reason)`` with reason ``""`` on success."""
        with self._lock:
            if self.max_connections and self.active >= self.max_connections:
                self.rejected_full += 1
                return False, "too_many_connections"
            if self._rate > 0:
                now = self.now()
                capacity = float(max(1, self.max_connections))
                self._tokens = min(capacity, self._tokens + (now - self._updated) * self._rate)
                self._updated = now
                if self._tokens < 1.0:
                    self.rejected_rate += 1
                    return False, "connection_rate_limited"
                self._tokens -= 1.0
            self.active += 1
            return True, ""

    def release(self) -> None:
        with self._lock:
            self.active = max(0, self.active - 1)


class SessionRegistry:
    """Ownership of a session id by at most one live connection.

    Two devices resuming the same ``session_id`` each build their own
    ``Conversation`` and append to the same JSONL, with independent id and
    ``turn_id`` counters.  The result is duplicate ids and interleaved turns —
    the file is corrupted by design, not by a race.  The store's append is
    atomic per line, so this is not a data race; it is a *semantic* conflict
    that only ownership can prevent.
    """

    def __init__(self) -> None:
        self._owners: dict[str, str] = {}
        self._lock = threading.Lock()

    def claim(self, session_id: str, owner: str) -> bool:
        """Take ``session_id`` for ``owner``; ``False`` if someone else has it."""
        with self._lock:
            current = self._owners.get(session_id)
            if current is not None and current != owner:
                return False
            self._owners[session_id] = owner
            return True

    def release(self, session_id: str, owner: str) -> None:
        with self._lock:
            if self._owners.get(session_id) == owner:
                del self._owners[session_id]

    def owner(self, session_id: str) -> str | None:
        with self._lock:
            return self._owners.get(session_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._owners)
