"""Steam Web API client: library, playtime, achievements, visibility.

Async httpx throughout. Two reasons it's async rather than sync: achievement
sampling fans out one call per game and wants bounded concurrency (Goal 2.2),
and the web request flow requires async Steam I/O (PROJECT.md, Phase 7). Building
it async from the start avoids a rewrite later.

The key is passed in, not read here — `config.steam_api_key()` is the single
place that resolves it and fails loudly when it's missing.
"""

import asyncio

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


class SteamError(RuntimeError):
    """A Steam Web API call failed in a way the caller can't route around."""


class UnknownSteamID(SteamError):
    """The Steam ID matched no account. Distinct from a private profile: there's
    nothing to read here, not a readable thing we're being denied."""


class SteamClient:
    def __init__(self, api_key: str, *, client: httpx.AsyncClient | None = None):
        self._key = api_key
        # An injected client (tests, or a shared app-wide client) is not ours to
        # close; one we create here is.
        self._client = client or httpx.AsyncClient(base_url=BASE_URL, timeout=20.0)
        self._owns_client = client is None

    async def __aenter__(self) -> "SteamClient":
        return self

    async def __aexit__(self, *exc) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _get(self, path: str, params: dict) -> httpx.Response:
        return await self._client.get(path, params={**params, "key": self._key})

    async def get_player_summary(self, steam_id: str) -> PlayerSummary:
        r = await self._get(
            "/ISteamUser/GetPlayerSummaries/v2/", {"steamids": steam_id}
        )
        r.raise_for_status()
        players = r.json()["response"]["players"]
        if not players:
            raise UnknownSteamID(f"No Steam account matches ID {steam_id!r}.")
        p = players[0]
        return PlayerSummary(
            steam_id=p["steamid"],
            persona_name=p.get("personaname"),
            visibility=p["communityvisibilitystate"],
        )

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
        games = await self._get_owned_games(steam_id)
        if games is None:
            return PrivateProfile(
                steam_id, summary.persona_name, UnreadableReason.GAMES_PRIVATE
            )
        return Library(steam_id, summary.persona_name, tuple(games))

    async def _get_owned_games(self, steam_id: str) -> list[OwnedGame] | None:
        """Owned games with playtime, or None when game details are hidden.

        A public profile with private game details returns an empty response
        object (no `games` key) rather than an error, so the absence of the key
        is the signal, not a status code.
        """
        r = await self._get(
            "/IPlayerService/GetOwnedGames/v1/",
            {"steamid": steam_id, "include_appinfo": 1, "include_played_free_games": 1},
        )
        r.raise_for_status()
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
        `success: false`. That's a normal, expected outcome, so it returns None
        rather than raising — the completion-drive read simply works from
        whatever games did report (Goal 2.2).
        """
        r = await self._get(
            "/ISteamUserStats/GetPlayerAchievements/v1/",
            {"steamid": steam_id, "appid": app_id},
        )
        if r.status_code in (400, 403):
            return None
        r.raise_for_status()
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
