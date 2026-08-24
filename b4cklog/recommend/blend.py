"""Blend and split (Goal 6.3): combine the two engines by local seed density,
split candidates by ownership into the backlog and outside-pool directions
PROJECT.md keeps apart, and attach the honest catch where one exists.

This is the one place in `recommend/` that touches the store — `properties.py`
and `similarity.py` stay pure so their scoring logic is testable without a
database. This module's job is orchestration: read the candidates and the
neighborhood, score each candidate through both engines, blend, and shape the
two lists (PROJECT.md, "Recommendations and the backlog").
"""

from dataclasses import dataclass

from b4cklog import store
from b4cklog.placement import Placement, PlacementModel
from b4cklog.recommend.properties import PropertyFit, score_game_properties
from b4cklog.recommend.similarity import (
    Neighbor,
    SimilarityFit,
    nearest_neighbors,
    neighborhood_density,
    score_similarity,
)
from b4cklog.steam import Library

# The cap on how far the blend can tip toward player-similarity, however
# crowded the seed gets around a visitor. PROJECT.md is explicit that
# game-property matching "should" still carry the real weight — "Player-
# similarity sharpens that result; it isn't the load-bearing wall" — so even
# at maximum neighborhood density, similarity never outweighs game properties.
MAX_SIMILARITY_WEIGHT = 0.5

# A caveat needs at least this many engaged neighbors before it's a read worth
# printing rather than one or two data points dressed up as a pattern.
_MIN_CAVEAT_EVIDENCE = 3

# The neighbor-typical-hours has to fall below this fraction of the estimated
# full-completion time before it's worth calling out as a real mismatch — the
# "loved it, but rarely finishes it" pattern PROJECT.md's own example
# describes (eighty hours to finish, twelve typically played).
_CAVEAT_RATIO = 0.5


@dataclass(frozen=True)
class Recommendation:
    app_id: int
    name: str | None
    score: float                 # 0..1, the blended rank
    property_score: float
    property_coverage: float     # how much of property_score came from real fields, not neutral fill
    similarity_score: float
    engaged_neighbors: int       # neighbors with logged playtime in this game
    hours_to_complete: float | None
    caveat: str | None


@dataclass(frozen=True)
class RecommendationSet:
    backlog: list[Recommendation]
    outside: list[Recommendation]
    backlog_note: str | None     # set when the backlog list is empty or partial, and says why
    blend_weight: float          # 0..MAX_SIMILARITY_WEIGHT actually used, from neighborhood density
    neighbor_count: int


def _build_caveat(game: dict, similarity_fit: SimilarityFit) -> str | None:
    hours_to_complete = game["hours_to_complete"]
    if hours_to_complete is None:
        return None
    if similarity_fit.engaged_neighbors < _MIN_CAVEAT_EVIDENCE:
        return None
    if similarity_fit.weighted_hours >= hours_to_complete * _CAVEAT_RATIO:
        return None
    return (
        f"Heads up: it's around {hours_to_complete:.0f} hours to finish, and "
        f"players in your cluster typically play about {similarity_fit.weighted_hours:.0f} "
        "before moving on. Worth it for the time you'll spend, not for finishing it."
    )


def _combine(
    game: dict, property_fit: PropertyFit, similarity_fit: SimilarityFit, blend_weight: float
) -> Recommendation:
    score = blend_weight * similarity_fit.score + (1.0 - blend_weight) * property_fit.score
    return Recommendation(
        app_id=game["app_id"],
        name=game["name"],
        score=score,
        property_score=property_fit.score,
        property_coverage=property_fit.coverage,
        similarity_score=similarity_fit.score,
        engaged_neighbors=similarity_fit.engaged_neighbors,
        hours_to_complete=game["hours_to_complete"],
        caveat=_build_caveat(game, similarity_fit),
    )


def _score_candidates(
    conn,
    app_ids: list[int],
    placement: Placement,
    neighbors: list[Neighbor],
    blend_weight: float,
) -> list[Recommendation]:
    playtimes_by_steam_id = {n.steam_id: store.get_seed_playtimes(conn, n.steam_id) for n in neighbors}
    similarity_fits = score_similarity(neighbors, playtimes_by_steam_id, app_ids)

    recommendations = []
    for app_id in app_ids:
        game = store.get_game_profile(conn, app_id)
        if game is None:
            continue  # not yet profiled — an honest gap Phase 7 surfaces, not scored here
        property_fit = score_game_properties(placement, game)
        recommendations.append(_combine(game, property_fit, similarity_fits[app_id], blend_weight))

    return sorted(recommendations, key=lambda r: -r.score)


def recommend(conn, model: PlacementModel, placement: Placement, library: Library) -> RecommendationSet:
    """Score and rank both directions for one visitor.

    Reads the seed and the game catalog from `conn`; writes nothing (the live
    app only ever reads reference data — CLAUDE.md). `library` is the
    visitor's own read, not persisted anywhere by this call.
    """
    seed_profiles = store.all_seed_profiles(conn)
    neighbors = nearest_neighbors(model, placement, seed_profiles)
    blend_weight = neighborhood_density(neighbors) * MAX_SIMILARITY_WEIGHT

    owned_app_ids = {g.app_id for g in library.games}
    unplayed_app_ids = [g.app_id for g in library.games if g.playtime_minutes == 0]
    backlog_app_ids = [a for a in unplayed_app_ids if store.get_game_profile(conn, a) is not None]
    outside_app_ids = sorted(store.outside_pool_app_ids(conn) - owned_app_ids)

    backlog = _score_candidates(conn, backlog_app_ids, placement, neighbors, blend_weight)
    outside = _score_candidates(conn, outside_app_ids, placement, neighbors, blend_weight)

    backlog_note = None
    if not unplayed_app_ids:
        backlog_note = "Nothing to recommend from your own library — every game you own has some time in it."
    elif not backlog_app_ids:
        backlog_note = (
            f"{len(unplayed_app_ids)} owned-but-unplayed game(s), but none are profiled yet — "
            "nothing honest to rank them by."
        )

    return RecommendationSet(
        backlog=backlog,
        outside=outside,
        backlog_note=backlog_note,
        blend_weight=blend_weight,
        neighbor_count=len(neighbors),
    )
