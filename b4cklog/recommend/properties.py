"""Game-property matching (Goal 6.1): how well a game's demands fit a
player's placement, using only the game's own profile — no crowd needed
(PROJECT.md, "How recommendations are generated": "This engine works from the
first player onward. It needs nobody else in the system.").

The score is a weighted blend across *all* of a player's archetype
responsibilities, not just their primary label — a player read as 60%
Completionist / 40% Dabbler gets a fit score that's actually 60/40 blended,
matching the continuous-placement principle (PROJECT.md, "How players are
placed") instead of scoring against a single snapped archetype.

Each archetype's ideal game shape is a direct read of its PROJECT.md
description, not an invented heuristic:

- Completionist wants a game it can actually finish (`is_finite`, high
  `completability`); the two subdivisions differ only on length/shape —
  Breadth wants short, focused games to fill gaps, Depth wants long,
  sprawling ones to lock onto.
- Dabbler wants short, finite, low-commitment games and doesn't care about
  achievement chasing.
- Obsessive wants the opposite of finite: endless, grind-driven, systems-heavy
  games ("systems-heavy games built to absorb time indefinitely").
- Enthusiast is deliberately the least demanding archetype — PROJECT.md's own
  example is "a long RPG that most players never finish," so length and
  completability barely move its score.
- Curator wants the "finite-but-deep roguelike" shape: an actual ending, but a
  big gap between "beat it" and "fully complete it" — a short main path with
  deep systems worth replaying, which is what that gap measures.

A field a game has no data for (Phase 5's honest gaps — most of the outside
pool has no seed playtime, for instance) simply doesn't vote; `coverage`
reports how much of the score is backed by real fields versus a neutral
default, so the caller can hedge a thin profile rather than presenting a
confident-looking number built on guesses.
"""

from dataclasses import dataclass

from b4cklog.placement import Placement

# A missing field contributes this neutral score rather than penalizing or
# rewarding — "no opinion," not "bad fit." Kept out of `coverage` so a
# profile leaning heavily on neutral fill reads as low-confidence.
_NEUTRAL = 0.5

# hours_to_complete / hours_to_beat past this ratio marks a game whose full
# completion asks a lot more than just reaching the end — the "short main
# path, deep systems" shape Curators want. Below it, the game is mostly done
# once beaten. Chosen with headroom over a merely-longer-postgame game (~1.3x)
# so only a real systems-depth gap counts.
_CURATOR_DEPTH_RATIO = 1.6

# Below this, the estimate itself is too rough to lean on (Goal 5.2's length
# estimate needs a real distribution to read a shape from).
_MIN_HOURS_TO_BEAT = 0.1


@dataclass(frozen=True)
class PropertyFit:
    score: float           # 0..1, blended across the player's archetype mix
    coverage: float         # 0..1, how much of that blend came from real game fields, not the neutral default


def _completionist_fit(game: dict, *, wants_short: bool) -> tuple[float, bool]:
    votes = []
    if game["is_finite"] is not None:
        votes.append(1.0 if game["is_finite"] else 0.0)
    if game["completability"] is not None:
        votes.append(game["completability"])
    if game["content_shape"] is not None:
        matches = game["content_shape"] == ("focused" if wants_short else "sprawling")
        votes.append(1.0 if matches else 0.25)
    if not votes:
        return _NEUTRAL, False
    return sum(votes) / len(votes), True


def _dabbler_fit(game: dict) -> tuple[float, bool]:
    votes = []
    if game["is_finite"] is not None:
        votes.append(1.0 if game["is_finite"] else 0.0)
    if game["content_shape"] is not None:
        votes.append(1.0 if game["content_shape"] == "focused" else 0.2)
    if game["progression_style"] is not None:
        votes.append(0.2 if game["progression_style"] == "grinding" else 0.7)
    if not votes:
        return _NEUTRAL, False
    return sum(votes) / len(votes), True


def _obsessive_fit(game: dict) -> tuple[float, bool]:
    votes = []
    if game["is_finite"] is not None:
        votes.append(0.0 if game["is_finite"] else 1.0)
    if game["progression_style"] is not None:
        votes.append(1.0 if game["progression_style"] == "grinding" else 0.3)
    if game["content_shape"] is not None:
        votes.append(1.0 if game["content_shape"] == "sprawling" else 0.3)
    if not votes:
        return _NEUTRAL, False
    return sum(votes) / len(votes), True


def _enthusiast_fit(game: dict) -> tuple[float, bool]:
    # Deliberately flat: PROJECT.md's whole point about the Enthusiast is that
    # length and completability shouldn't gate the recommendation ("plays for
    # the time spent rather than the credits screen"). Only a mild nod to
    # sprawling content, the one trait PROJECT.md calls out by name.
    if game["content_shape"] is None:
        return _NEUTRAL, False
    return (0.65 if game["content_shape"] == "sprawling" else _NEUTRAL), True


def _curator_fit(game: dict) -> tuple[float, bool]:
    votes = []
    if game["is_finite"] is not None:
        votes.append(1.0 if game["is_finite"] else 0.1)
    beat, complete = game["hours_to_beat"], game["hours_to_complete"]
    if beat is not None and complete is not None and beat >= _MIN_HOURS_TO_BEAT:
        votes.append(1.0 if (complete / beat) >= _CURATOR_DEPTH_RATIO else 0.3)
    if not votes:
        return _NEUTRAL, False
    return sum(votes) / len(votes), True


# Archetype name -> fit function. Only the five working archetypes have a
# defined shape; an Unclassified-* component (PROJECT.md, "Emergent
# Taxonomy") has none by construction, so it's simply absent here rather than
# guessed at.
_FIT_BY_ARCHETYPE = {
    "Dabbler": _dabbler_fit,
    "Obsessive": _obsessive_fit,
    "Enthusiast": _enthusiast_fit,
    "Curator": _curator_fit,
}


def _completionist_fit_for(subdivision_name: str | None) -> callable:
    wants_short = subdivision_name != "Depth Completionist"
    return lambda game: _completionist_fit(game, wants_short=wants_short)


def score_game_properties(placement: Placement, game: dict) -> PropertyFit:
    """Blend `game`'s fit across every archetype in `placement.responsibilities`,
    weighted by how much of the player's continuous placement each one carries.

    `game` is a `game_profiles` row as `store.get_game_profile` returns it
    (only the profile fields are read; `provenance` is ignored here).
    """
    fit_by_archetype = dict(_FIT_BY_ARCHETYPE)
    if "Completionist" in placement.responsibilities:
        sub_name = placement.subdivision.name if placement.subdivision else None
        fit_by_archetype["Completionist"] = _completionist_fit_for(sub_name)

    weighted_sum = 0.0
    covered_weight = 0.0
    total_weight = 0.0
    for archetype, weight in placement.responsibilities.items():
        total_weight += weight
        fit_fn = fit_by_archetype.get(archetype)
        if fit_fn is None:
            # Unclassified component: no defined shape to score against.
            # Votes neutral, same as a game field with no data — "no
            # opinion," not "bad fit" — but doesn't count toward coverage.
            weighted_sum += weight * _NEUTRAL
            continue
        score, has_data = fit_fn(game)
        weighted_sum += weight * score
        if has_data:
            covered_weight += weight

    if total_weight == 0.0:
        return PropertyFit(score=_NEUTRAL, coverage=0.0)

    return PropertyFit(score=weighted_sum / total_weight, coverage=covered_weight / total_weight)
