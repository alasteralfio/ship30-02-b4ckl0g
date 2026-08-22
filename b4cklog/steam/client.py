"""Steam Web API client: library, playtime, achievements, visibility.

Async httpx throughout. Two reasons it's async rather than sync: achievement
sampling fans out one call per game and wants bounded concurrency (Goal 2.2),
and the web request flow requires async Steam I/O (PROJECT.md, Phase 7). Building
it async from the start avoids a rewrite later.

The key is passed in, not read here — `config.steam_api_key()` is the single
place that resolves it and fails loudly when it's missing.
"""

import asyncio
from collections.abc import Iterable, Iterator

import httpx

from .models import (
    AchievementStats,
    Library,
    LibraryResult,
    OwnedGame,
    PlayerSummary,
    PrivateProfile,
    UnreadableReason,
)

BASE_URL = "https://api.steampowered.com"

# communityvisibilitystate for a public profile. Any other value means we can't
# read the account (private, friends-only), which is a normal outcome.
_PUBLIC = 3

# How many of the most-played games to sample achievements for. Steam serves
# achievements one call per game, so an unbounded sample means hundreds of calls
# for a large library. The most-played handful carries the completion-drive
# signal; the long tail of barely-touched games recovers nothing a hundred extra
# requests would (PROJECT.md, "Reading a player").
DEFAULT_ACHIEVEMENT_SAMPLE = 20

# Concurrent achievement calls. Bounded to stay friendly to the API rather than
# firing the whole sample at once.
DEFAULT_CONCURRENCY = 5

# GetPlayerSummaries accepts a comma-separated steamids list, capped at 100 per
# call. The seed sourcer leans on this: a batch resolves the visibility of 100
# random candidate IDs in one request, so the private/nonexistent majority costs
# almost nothing to reject (Goal 3.1).
_SUMMARIES_PER_CALL = 100

# Steam answers a burst over its rate limit with HTTP 420/429 rather than serving
# the call, and occasionally drops a call to a transient 502/503/504 under
# sustained crawl load. Both are expected on a long run, not exceptional: back
# off and retry a bounded number of times, then give up with a clear error.
# Delay doubles each attempt from the base (1s, 2s, 4s, …).
_RATE_LIMIT_STATUSES = (420, 429)
_TRANSIENT_SERVER_STATUSES = (502, 503, 504)
_RETRYABLE_STATUSES = _RATE_LIMIT_STATUSES + _TRANSIENT_SERVER_STATUSES
# Back off through a transient failure (1, 2, 4, 8, 16s ≈ 31s total) then give
# up. Not longer: when Steam is in a real cooldown or outage the call will keep
# failing for minutes, and waiting that out per call would stall a bulk run.
# Riding out a longer outage is the resume mechanism's job, not one call's.
_MAX_RATE_LIMIT_RETRIES = 5
_RATE_LIMIT_BASE_DELAY = 1.0


class SteamError(RuntimeError):
    """A Steam Web API call failed in a way the caller can't route around."""


class UnknownSteamID(SteamError):
    """The Steam ID matched no account. Distinct from a private profile: there's
    nothing to read here, not a readable thing we're being denied."""


def _chunked(items: Iterable[str], size: int) -> Iterator[list[str]]:
    """Yield successive `size`-long lists from `items`. The tail may be shorter."""
    chunk: list[str] = []
    for item in items:
        chunk.append(item)
        if len(chunk) == size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


class SteamClient:
    def __init__(
        self,
        api_key: str,
        *,
        client: httpx.AsyncClient | None = None,
        min_request_interval: float = 0.0,
    ):
        self._key = api_key
        # An injected client (tests, or a shared app-wide client) is not ours to
        # close; one we create here is.
        self._client = client or httpx.AsyncClient(base_url=BASE_URL, timeout=20.0)
        self._owns_client = client is None
        # Proactive throttle for bulk jobs: spacing request *starts* this many
        # seconds apart caps the sustained call rate regardless of concurrency,
        # which is what keeps a long crawl under Steam's rolling-window limit.
        # Off by default (0) — the live per-visitor path wants no self-imposed
        # latency; only the pipeline sets it.
        self._min_request_interval = min_request_interval
        self._next_request_time = 0.0
        self._throttle_lock = asyncio.Lock()

    async def __aenter__(self) -> "SteamClient":
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict) -> httpx.Response:
        """GET a Steam endpoint, backing off through rate-limit and transient
        server-error responses.

        On 420/429/502/503/504 it waits and retries; on giving up, or on a
        transport-level failure (timeout, connection reset), it raises a
        SteamError that names the endpoint and status but never the key. This
        is load-bearing, not cosmetic: the key rides in the query string
        (`_client.get(path, params={..., "key": self._key})` below), so
        letting httpx's own exception propagate — `HTTPStatusError` embeds the
        full request URL in its message — leaks the key into any log or
        traceback that captures it. That happened for real during a Phase 4
        seed-sourcing run (an uncaught 502 crashed the process with the key in
        the traceback); every non-2xx path is now funneled through here or
        `_raise_for_status` so it can't happen through a status code we didn't
        think to test. Other statuses pass straight back for the caller to
        handle explicitly (e.g. achievement calls treat 400/403/5xx as "no
        signal for this game", not an error).
        """
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            await self._await_throttle()
            try:
                response = await self._client.get(
                    path, params={**params, "key": self._key}
                )
            except httpx.HTTPError as e:
                if attempt == _MAX_RATE_LIMIT_RETRIES:
                    raise SteamError(
                        f"{path} failed after {_MAX_RATE_LIMIT_RETRIES} retries: "
                        f"{type(e).__name__}."
                    ) from None
                await asyncio.sleep(_RATE_LIMIT_BASE_DELAY * 2**attempt)
                continue
            if response.status_code not in _RETRYABLE_STATUSES:
                return response
            if attempt == _MAX_RATE_LIMIT_RETRIES:
                raise SteamError(
                    f"Steam kept failing {path} (HTTP {response.status_code}); "
                    f"backed off {_MAX_RATE_LIMIT_RETRIES} times and gave up."
                )
            await asyncio.sleep(_RATE_LIMIT_BASE_DELAY * 2**attempt)
        raise AssertionError("unreachable")  # loop returns or raises

    def _raise_for_status(self, response: httpx.Response, path: str) -> None:
        """Raise a key-safe SteamError for a non-2xx response.

        Used instead of `httpx.Response.raise_for_status()`, whose
        `HTTPStatusError` embeds the full request URL — key included — in its
        message (see `_get`'s docstring). Every direct status check a caller
        does before this point (achievement calls treating 400/403/5xx as "no
        signal") still runs first; this only covers what's left over.
        """
        if response.is_success:
            return
        raise SteamError(f"Steam returned HTTP {response.status_code} for {path}.")

    async def _await_throttle(self) -> None:
        """Block until this request's turn, spacing starts by the throttle interval.

        The lock is held across the wait so callers pass through one at a time,
        each taking the next slot — a hard cap on the start rate no matter how
        many coroutines are in flight. A no-op when the interval is 0.
        """
        if self._min_request_interval <= 0:
            return
        async with self._throttle_lock:
            loop = asyncio.get_running_loop()
            wait = self._next_request_time - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_request_time = loop.time() + self._min_request_interval

    async def get_player_summaries(
        self, steam_ids: Iterable[str]
    ) -> list[PlayerSummary]:
        """Visibility and persona for many IDs, in as few calls as Steam allows.

        Chunked at 100 per call (`_SUMMARIES_PER_CALL`). IDs that match no account
        are simply absent from the response — Steam returns only the ones it
        found — so a caller filtering candidates gets rejection for free and the
        result is not aligned to the input order.
        """
        summaries: list[PlayerSummary] = []
        for chunk in _chunked(steam_ids, _SUMMARIES_PER_CALL):
            path = "/ISteamUser/GetPlayerSummaries/v2/"
            r = await self._get(path, {"steamids": ",".join(chunk)})
            self._raise_for_status(r, path)
            for p in r.json()["response"]["players"]:
                summaries.append(
                    PlayerSummary(
                        steam_id=p["steamid"],
                        persona_name=p.get("personaname"),
                        visibility=p["communityvisibilitystate"],
                    )
                )
        return summaries

    async def get_player_summary(self, steam_id: str) -> PlayerSummary:
        summaries = await self.get_player_summaries([steam_id])
        if not summaries:
            raise UnknownSteamID(f"No Steam account matches ID {steam_id!r}.")
        return summaries[0]

    async def read_library(self, steam_id: str) -> LibraryResult:
        """Read a player's library, checking visibility first.

        Visibility comes first because a private profile is common and the
        system says so plainly rather than guessing around it (PROJECT.md). Even
        a public profile can hide its game details, which is a second, distinct
        can't-read state (GAMES_PRIVATE).
        """
        summary = await self.get_player_summary(steam_id)
        if summary.visibility != _PUBLIC:
            return PrivateProfile(
                steam_id, summary.persona_name, UnreadableReason.PROFILE_PRIVATE
            )
        games = await self.owned_games(steam_id)
        if games is None:
            return PrivateProfile(
                steam_id, summary.persona_name, UnreadableReason.GAMES_PRIVATE
            )
        return Library(steam_id, summary.persona_name, tuple(games))

    async def owned_games(self, steam_id: str) -> list[OwnedGame] | None:
        """Owned games with playtime, or None when game details are hidden.

        A public profile with private game details returns an empty response
        object (no `games` key) rather than an error, so the absence of the key
        is the signal, not a status code.
        """
        path = "/IPlayerService/GetOwnedGames/v1/"
        r = await self._get(
            path,
            {"steamid": steam_id, "include_appinfo": 1, "include_played_free_games": 1},
        )
        self._raise_for_status(r, path)
        resp = r.json()["response"]
        if "games" not in resp:
            return None
        return [
            OwnedGame(
                app_id=g["appid"],
                name=g.get("name", ""),
                playtime_minutes=g.get("playtime_forever", 0),
            )
            for g in resp["games"]
        ]

    async def get_player_achievements(
        self, steam_id: str, app_id: int
    ) -> AchievementStats | None:
        """Achievement progress for one game, or None when there's no signal.

        Many games have no achievements or no stats at all; Steam answers those
        with HTTP 400 (or 403 if the game's stats are private) and
        `success: false`. Some apps with a broken or absent achievement schema
        answer with a 500 instead, consistently. All of these are "no signal for
        this game", not failures: they return None so the completion-drive read
        works from whatever games did report (Goal 2.2), rather than one quirky
        app aborting a whole profile's crawl.
        """
        path = "/ISteamUserStats/GetPlayerAchievements/v1/"
        r = await self._get(path, {"steamid": steam_id, "appid": app_id})
        if r.status_code in (400, 403) or r.status_code >= 500:
            return None
        self._raise_for_status(r, path)
        stats = r.json()["playerstats"]
        achievements = stats.get("achievements")
        if not stats.get("success") or not achievements:
            return None
        unlocked = sum(1 for a in achievements if a.get("achieved"))
        return AchievementStats(app_id, unlocked, len(achievements))

    async def sample_achievements(
        self,
        library: Library,
        *,
        top_n: int = DEFAULT_ACHIEVEMENT_SAMPLE,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> dict[int, AchievementStats]:
        """Achievement stats for the player's most-played games, keyed by app ID.

        Issues exactly one call per most-played game (up to `top_n`); games with
        no achievements are simply absent from the result. Concurrency is bounded
        by a semaphore.
        """
        games = library.most_played(top_n)
        semaphore = asyncio.Semaphore(concurrency)

        async def fetch(game: OwnedGame) -> tuple[int, AchievementStats | None]:
            async with semaphore:
                stats = await self.get_player_achievements(
                    library.steam_id, game.app_id
                )
            return game.app_id, stats

        results = await asyncio.gather(*(fetch(g) for g in games))
        return {app_id: stats for app_id, stats in results if stats is not None}
