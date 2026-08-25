"""Build a report from already-fetched player data (Goals 7.1, 7.3).

Deliberately split from the Steam I/O: this module is pure given a `Library`
and its sampled achievements, mirroring `placement/label.py` and
`recommend/blend.py`'s own split of "fetch" from "compute" — so the honest
branching below (thin signal vs. a real read) is unit-testable without a
network call or a live key. `app.py` is the only place that awaits Steam.

Two failure states get handled *here*, not left to guesswork downstream:
a private profile is the caller's problem (it never reaches `build_report`
at all — `app.py` branches on it straight off `read_library`); a public but
completely unplayed library is this module's problem, because only after
reducing to behaviour do we know there's nothing to read.
"""

from dataclasses import dataclass

from b4cklog.behaviour import Behaviour, reduce_to_behaviour
from b4cklog.placement import Placement, PlacementModel, place
from b4cklog.recommend import RecommendationSet, recommend
from b4cklog.steam import AchievementStats, Library

# Zero played games means zero behavioural signal — `reduce_to_behaviour`
# places such a library at the neutral origin by construction (its own
# docstring), and reading a placement off that neutral point would present a
# guess as a real result. Below this, there's nothing honest to place.
MIN_GAMES_FOR_ANY_READ = 1

# Below this many played games, a placement is computed but flagged
# low-confidence rather than withheld — PROJECT.md's "cold start" resolution
# is to do the best we can with what's there and say so plainly, not to go
# silent. Matches the shape-confidence floor `placement/label.py` already
# uses for the same reason (`_MIN_GAMES_FOR_SHAPE`).
MIN_GAMES_FOR_CONFIDENT_READ = 5


@dataclass(frozen=True)
class NoSignal:
    """A public, readable library with nothing played in it. Not an error —
    a real, if unhelpful, state (PROJECT.md, "Cold start")."""

    steam_id: str
    persona_name: str | None
    games_owned: int


@dataclass(frozen=True)
class Report:
    steam_id: str
    persona_name: str | None
    games_owned: int
    games_played: int
    low_confidence: bool
    behaviour: Behaviour
    placement: Placement
    recommendations: RecommendationSet


def build_report(
    library: Library,
    achievements: dict[int, AchievementStats],
    model: PlacementModel,
    conn,
) -> Report | NoSignal:
    """Run reduce -> place -> recommend on an already-fetched library.

    `conn` is a read-only reference-store connection (recommend/'s nearest-
    neighbour search and game-catalog lookups need it); this function itself
    never writes.
    """
    behaviour = reduce_to_behaviour(library, achievements)
    games_played = behaviour.shape["games_played"]
    games_owned = behaviour.shape["games_owned"]

    if games_played < MIN_GAMES_FOR_ANY_READ:
        return NoSignal(library.steam_id, library.persona_name, games_owned)

    placement = place(model, behaviour)
    recommendations = recommend(conn, model, placement, library)

    return Report(
        steam_id=library.steam_id,
        persona_name=library.persona_name,
        games_owned=games_owned,
        games_played=games_played,
        low_confidence=games_played < MIN_GAMES_FOR_CONFIDENT_READ,
        behaviour=behaviour,
        placement=placement,
        recommendations=recommendations,
    )
