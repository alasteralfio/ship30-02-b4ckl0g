"""Typed results for a player read.

A private profile is a real and common case, not an error (PROJECT.md, "Reading
a player"). So `read_library` returns either a `Library` or a `PrivateProfile`
and the caller branches on the type, rather than the read raising and every call
site wrapping it in try/except. Only a genuinely bad Steam ID raises
(`UnknownSteamID` in `client`).
"""

from dataclasses import dataclass
from enum import Enum


class UnreadableReason(str, Enum):
    """Why a profile can't be read. Both map to the same honest "can't read
    this" message for the user, but the two are distinct real states worth
    keeping apart: the whole community profile is private, versus the profile is
    public but its game details are hidden."""

    PROFILE_PRIVATE = "profile_private"
    GAMES_PRIVATE = "games_private"


@dataclass(frozen=True)
class OwnedGame:
    app_id: int
    name: str
    playtime_minutes: int  # Steam's playtime_forever, in minutes


@dataclass(frozen=True)
class PlayerSummary:
    """The visibility check from GetPlayerSummaries. `visibility` is Steam's raw
    communityvisibilitystate (3 = public; anything else is not readable)."""

    steam_id: str
    persona_name: str | None
    visibility: int


@dataclass(frozen=True)
class Library:
    steam_id: str
    persona_name: str | None
    games: tuple[OwnedGame, ...]

    def most_played(self, n: int | None = None) -> list[OwnedGame]:
        """Games with real playtime, most-played first.

        Unplayed games (0 minutes) are dropped: they carry no completion-drive
        signal and pulling achievements for them would only be locked-across-
        the-board noise and wasted calls (PROJECT.md, "Reading a player"). The
        (-playtime, app_id) sort key is a total order, so the same library
        always yields the same ranking — no ties left to chance.
        """
        played = sorted(
            (g for g in self.games if g.playtime_minutes > 0),
            key=lambda g: (-g.playtime_minutes, g.app_id),
        )
        return played[:n] if n is not None else played


@dataclass(frozen=True)
class PrivateProfile:
    steam_id: str
    persona_name: str | None
    reason: UnreadableReason


# What read_library returns: a readable library or the honest can't-read result.
LibraryResult = Library | PrivateProfile


@dataclass(frozen=True)
class AchievementStats:
    """A player's achievement progress in one game. Only constructed for games
    that actually have achievements, so `total` is always >= 1."""

    app_id: int
    unlocked: int
    total: int

    @property
    def completion_rate(self) -> float:
        return self.unlocked / self.total
