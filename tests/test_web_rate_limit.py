"""The per-IP rate limiter and the short-lived lookup cache (Goal 7.4): the
limiter allows up to its threshold and rejects past it, per IP independently,
and recovers once the window rolls past; the cache returns what was set until
its TTL expires, then forgets it."""

from b4cklog.web.rate_limit import RateLimiter, TTLCache


def test_allows_up_to_the_threshold_then_rejects():
    limiter = RateLimiter(max_requests=3, window_seconds=60.0)
    assert limiter.allow("1.2.3.4", now=0.0)
    assert limiter.allow("1.2.3.4", now=1.0)
    assert limiter.allow("1.2.3.4", now=2.0)
    assert not limiter.allow("1.2.3.4", now=3.0)


def test_ips_are_tracked_independently():
    limiter = RateLimiter(max_requests=1, window_seconds=60.0)
    assert limiter.allow("1.1.1.1", now=0.0)
    assert not limiter.allow("1.1.1.1", now=0.0)
    assert limiter.allow("2.2.2.2", now=0.0)


def test_recovers_once_the_window_rolls_past():
    limiter = RateLimiter(max_requests=1, window_seconds=60.0)
    assert limiter.allow("1.2.3.4", now=0.0)
    assert not limiter.allow("1.2.3.4", now=30.0)
    assert limiter.allow("1.2.3.4", now=61.0)


def test_cache_returns_what_was_set_until_it_expires():
    cache = TTLCache(ttl_seconds=60.0)
    cache.set("76561197960287930", "cached-result", now=0.0)
    assert cache.get("76561197960287930", now=30.0) == "cached-result"
    assert cache.get("76561197960287930", now=60.0) is None


def test_cache_miss_for_an_unseen_key():
    cache = TTLCache(ttl_seconds=60.0)
    assert cache.get("nobody-set-this") is None
