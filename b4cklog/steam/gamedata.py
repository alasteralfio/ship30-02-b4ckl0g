"""Keyless game-metadata client: Store `appdetails` and global achievement
percentages (Goal 5.1).

Both endpoints need no `STEAM_API_KEY` — this is what lets game profiling run
independently of the player-path key. A separate client from `SteamClient`
because it's a genuinely different service (the store front, not the Web API)
with its own host, its own harder rate limit, and no auth to thread through.
"""

import asyncio

import httpx
from dataclasses import dataclass

STORE_BASE_URL = "https://store.steampowered.com"
API_BASE_URL = "https://api.steampowered.com"

# appdetails is rate-limited harder than the main Web API — community reports
# put the ceiling around 200 requests per 5 minutes for an anonymous caller.
# One request every 1.5s (40/min) sits comfortably under that for a sustained
# crawl of thousands of games.
DEFAULT_REQUEST_INTERVAL = 1.5

# Transient failures worth retrying rather than failing the whole game. Same
# shape as SteamClient's retry (client.py) — a burst-limited or momentarily
# down endpoint shouldn't cost a profile forever.
_RETRYABLE_STATUSES = (429, 502, 503, 504)
_MAX_RETRIES = 5
_RETRY_BASE_DELAY = 1.0


class GameDataError(RuntimeError):
    """A game-data call failed in a way the caller can't route around."""


@dataclass(frozen=True)
class AppDetails:
    """The slice of Store `appdetails` a game profile needs."""

    app_id: int
    name: str
    type: str  # "game", "dlc", "music", ... — only "game" belongs in scope
    genres: tuple[str, ...]
    categories: tuple[str, ...]
    achievement_count: int  # 0 when the game has no achievements


@dataclass(frozen=True)
class AchievementPercent:
    name: str
    percent: float  # 0..100, share of all owners who unlocked it


class GameDataClient:
    def __init__(
        self,
        *,
        client: httpx.AsyncClient | None = None,
        min_request_interval: float = DEFAULT_REQUEST_INTERVAL,
    ):
        self._client = client or httpx.AsyncClient(timeout=20.0)
        self._owns_client = client is None
        self._min_request_interval = min_request_interval
        self._next_request_time = 0.0
        self._throttle_lock = asyncio.Lock()

    async def __aenter__(self) -> "GameDataClient":
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _await_throttle(self) -> None:
        if self._min_request_interval <= 0:
            return
        async with self._throttle_lock:
            loop = asyncio.get_running_loop()
            wait = self._next_request_time - loop.time()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_request_time = loop.time() + self._min_request_interval

    async def _get(self, url: str, params: dict) -> httpx.Response:
        """GET with the same retry-then-scrub shape as SteamClient._get.

        No key rides in these URLs, so there's no leak risk here — but a
        transient failure still shouldn't cost the whole crawl a game, and a
        raw httpx exception still shouldn't escape uncaught.
        """
        for attempt in range(_MAX_RETRIES + 1):
            await self._await_throttle()
            try:
                response = await self._client.get(url, params=params)
            except httpx.HTTPError as e:
                if attempt == _MAX_RETRIES:
                    raise GameDataError(
                        f"{url} failed after {_MAX_RETRIES} retries: {type(e).__name__}."
                    ) from None
                await asyncio.sleep(_RETRY_BASE_DELAY * 2**attempt)
                continue
            if response.status_code not in _RETRYABLE_STATUSES:
                return response
            if attempt == _MAX_RETRIES:
                raise GameDataError(
                    f"{url} kept failing (HTTP {response.status_code}); "
                    f"backed off {_MAX_RETRIES} times and gave up."
                )
            await asyncio.sleep(_RETRY_BASE_DELAY * 2**attempt)
        raise AssertionError("unreachable")  # loop returns or raises

    async def get_app_details(self, app_id: int) -> AppDetails | None:
        """One app's details, or None when Steam has nothing for this ID.

        A delisted, region-locked, or never-valid app answers with
        `success: false` and no `data` — a real, permanent case (not every
        app_id a player owns still resolves), so it's a clean None rather
        than an error.
        """
        r = await self._get(
            f"{STORE_BASE_URL}/api/appdetails",
            {"appids": app_id, "filters": "basic,genres,categories,achievements"},
        )
        if r.status_code != 200:
            raise GameDataError(f"appdetails returned HTTP {r.status_code} for {app_id}.")
        entry = r.json().get(str(app_id))
        if not entry or not entry.get("success"):
            return None
        data = entry["data"]
        achievements = data.get("achievements") or {}
        return AppDetails(
            app_id=app_id,
            name=data.get("name", ""),
            type=data.get("type", ""),
            genres=tuple(g["description"] for g in data.get("genres", [])),
            categories=tuple(c["description"] for c in data.get("categories", [])),
            achievement_count=achievements.get("total", 0),
        )

    async def get_global_achievement_percentages(
        self, app_id: int
    ) -> list[AchievementPercent] | None:
        """Global unlock rate per achievement, or None when there's no signal.

        Steam answers a game with no achievements (or no stats tracked) with
        an empty/absent achievement list rather than an error — that's "no
        signal", the same non-error absence `SteamClient` treats a stats-less
        game as for a single player (client.py).
        """
        r = await self._get(
            f"{API_BASE_URL}/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v0002/",
            {"gameid": app_id},
        )
        if r.status_code != 200:
            return None
        achievements = r.json().get("achievementpercentages", {}).get("achievements")
        if not achievements:
            return None
        return [
            AchievementPercent(name=a["name"], percent=float(a["percent"]))
            for a in achievements
        ]
