"""Per-IP rate limiting and a short-lived Steam-fetch cache (Goal 7.4).

The threat is the shared Steam key, not our own server's capacity: every
lookup fans out to several Steam calls, and one caller hammering the endpoint
could exhaust the key's quota or trip Steam's own rate limits — already seen
live during the seed crawl, which hit HTTP 420 on bursts (`pipeline.crawl_seed`).
Both pieces here are in-process, no extra infrastructure, per PROJECT.md's
"minimal" deploy stance: state resets on restart and doesn't share across
worker processes, an accepted limit at this project's scale, not an oversight
(Phase 9 revisits deploy hardening, not this).
"""

import time
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    """A fixed-window per-IP limiter: at most `max_requests` lookups from one
    IP in any `window_seconds` span. A plain window is simpler than a token
    bucket and plenty for the actual threat (a caller hammering the endpoint),
    not a precision traffic-shaping tool.
    """

    max_requests: int = 5
    window_seconds: float = 60.0
    _hits: dict[str, list[float]] = field(default_factory=dict)

    def allow(self, ip: str, *, now: float | None = None) -> bool:
        now = now if now is not None else time.monotonic()
        cutoff = now - self.window_seconds
        hits = [t for t in self._hits.get(ip, []) if t > cutoff]
        if len(hits) >= self.max_requests:
            self._hits[ip] = hits
            return False
        hits.append(now)
        self._hits[ip] = hits
        return True


@dataclass
class TTLCache:
    """A short-lived cache keyed by Steam ID, holding whatever a repeated
    lookup shouldn't have to re-fetch from Steam (Goal 7.4: "a short-lived
    cache of a repeated lookup is fair game to spare the key... keep the TTL
    short"). Not a visitor record: entries expire quickly and live only in
    process memory, never written to disk — the "nothing about a visitor is
    kept" promise holds at the timescale that matters.
    """

    ttl_seconds: float = 60.0
    _entries: dict[str, tuple[float, object]] = field(default_factory=dict)

    def get(self, key: str, *, now: float | None = None):
        now = now if now is not None else time.monotonic()
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if now >= expires_at:
            del self._entries[key]
            return None
        return value

    def set(self, key: str, value, *, now: float | None = None) -> None:
        now = now if now is not None else time.monotonic()
        self._entries[key] = (now + self.ttl_seconds, value)
