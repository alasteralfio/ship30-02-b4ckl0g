"""Read a placement off the frozen model: soft label and subdivision (Goal 4.2).

The continuous position is the model's native output (PROJECT.md, "How
players are placed"). Nobody is snapped to a component — `place()` sums
component responsibilities into per-archetype probabilities and reads the
label straight off that distribution, so "sixty percent Completionist, forty
percent Dabbler" is a real read of the mixture, not a description bolted on
after a hard assignment.
"""

from dataclasses import dataclass

import numpy as np

from b4cklog.behaviour import Behaviour
from b4cklog.placement.model import AXES, UNCLASSIFIED_PREFIX, PlacementModel

# Minimum secondary-archetype probability worth naming at all, and the words
# used above it. Below the floor the read is just "most like {primary}" — a
# few stray percent spread across every other component isn't a real tendency,
# it's mixture noise, and reporting it would manufacture nuance that isn't there.
_STRENGTH_BANDS = (
    (0.40, "strong"),
    (0.25, "moderate"),
    (0.12, "a hint of"),
)

# A library needs at least this many played games before its playtime curve
# has a shape worth reading — matches the floor `_bimodality` itself enforces
# in behaviour/reduce.py, so this check should never actually fire on live
# data, but it's here rather than assumed.
_MIN_GAMES_FOR_SHAPE = 4
_HIGH_BIMODALITY = 0.55


@dataclass(frozen=True)
class Subdivision:
    name: str
    detail: str
    # False when there isn't enough signal to make the read, and it says so
    # rather than guessing (PROJECT.md, "Honesty over comfort").
    confident: bool


@dataclass(frozen=True)
class Placement:
    coordinates: tuple[float, float, float]  # standardized (depth_breadth, completion_drive, commitment_consistency)
    responsibilities: dict[str, float]        # archetype name -> summed membership probability
    primary: str
    primary_probability: float
    secondary: str | None
    secondary_probability: float | None
    # True when this point sits in territory the seed has little real data
    # about (below the 5th-percentile likelihood the seed scores itself at).
    # `responsibilities` above is still a faithful *relative* read among the
    # fitted components — this flags that the *absolute* fit is weak, which
    # predict_proba alone can't tell you (see `PlacementModel.outlier_log_likelihood`).
    outlier: bool
    soft_label: str
    subdivision: Subdivision | None


def place(model: PlacementModel, behaviour: Behaviour) -> Placement:
    """Place one player's behaviour against the frozen model.

    Deterministic: the same `behaviour` always yields the same `Placement`,
    since nothing here is fit or randomized — only the frozen model is read.
    """
    x_raw = np.array([[getattr(behaviour, axis) for axis in AXES]])
    x = model.scaler.transform(x_raw)
    component_probs = model.mixture.predict_proba(x)[0]
    outlier = bool(model.mixture.score_samples(x)[0] < model.outlier_log_likelihood)

    responsibilities: dict[str, float] = {}
    for component, prob in enumerate(component_probs):
        name = model.archetype_by_component[component]
        responsibilities[name] = responsibilities.get(name, 0.0) + float(prob)

    ranked = sorted(responsibilities.items(), key=lambda kv: -kv[1])
    primary, primary_p = ranked[0]
    secondary, secondary_p = ranked[1] if len(ranked) > 1 else (None, None)

    return Placement(
        coordinates=tuple(x[0]),
        responsibilities=responsibilities,
        primary=primary,
        primary_probability=primary_p,
        secondary=secondary,
        secondary_probability=secondary_p,
        outlier=outlier,
        soft_label=_soft_label(primary, secondary, secondary_p, outlier),
        subdivision=_subdivide(primary, behaviour.shape),
    )


def _strength_word(probability: float) -> str | None:
    for threshold, word in _STRENGTH_BANDS:
        if probability >= threshold:
            return word
    return None


def _article(name: str) -> str:
    return "an" if name[:1] in "AEIOU" else "a"


_OUTLIER_CAVEAT = (
    " Heads up: your profile sits well outside where this seed has much data, "
    "so treat this as a rough guess, not a confident read."
)


def _soft_label(primary: str, secondary: str | None, secondary_p: float | None, outlier: bool = False) -> str:
    if primary.startswith(UNCLASSIFIED_PREFIX):
        # PROJECT.md, "Emergent Taxonomy": this isn't a data problem, it's the
        # taxonomy being incomplete, and the honesty principle says to say so
        # rather than force the read onto the nearest named archetype.
        return (
            "You don't read like any of the five known archetypes — that's a "
            "real gap in the taxonomy, not a fault in your data."
        )
    if secondary is None or secondary.startswith(UNCLASSIFIED_PREFIX):
        label = f"You play most like {_article(primary)} {primary}."
    else:
        word = _strength_word(secondary_p)
        if word is None:
            label = f"You play most like {_article(primary)} {primary}."
        else:
            label = f"You play most like {_article(primary)} {primary}, with {word} {secondary} tendencies."
    return label + _OUTLIER_CAVEAT if outlier else label


def _subdivide(primary: str, shape: dict) -> Subdivision | None:
    """Dispatch to the subdivision for `primary`, or None where none applies.

    Only Completionist and Obsessive split (PROJECT.md, "Subdivisions"); the
    other three archetypes have no sub-type in the current design.
    """
    if primary == "Completionist":
        return _subdivide_completionist(shape)
    if primary == "Obsessive":
        return _subdivide_obsessive(shape)
    return None


def _subdivide_completionist(shape: dict) -> Subdivision:
    """Breadth vs. Depth, read off the playtime curve's bimodality.

    An even spread across finished games (low bimodality) is a Breadth
    Completionist filling gaps with short games between long ones; a
    split-shaped curve (high bimodality) is a Depth Completionist locking onto
    one deep well at a time (PROJECT.md, "Subdivisions"). Same feature the
    Curator region reads in `pipeline/seed_summary.py`, interpreted differently
    because it's paired here with a Completionist-level completion drive
    instead of a mid one.
    """
    if shape.get("games_played", 0) < _MIN_GAMES_FOR_SHAPE:
        return Subdivision(
            name="undetermined",
            detail="Too few played games yet for the playtime curve to have a shape.",
            confident=False,
        )
    if shape.get("bimodality", 0.0) >= _HIGH_BIMODALITY:
        return Subdivision(
            name="Depth Completionist",
            detail="Locks onto one game and exhausts it before moving to the next.",
            confident=True,
        )
    return Subdivision(
        name="Breadth Completionist",
        detail="Finishes a wide range of games, playtime spread evenly across them.",
        confident=True,
    )


def _subdivide_obsessive(shape: dict) -> Subdivision:
    """Live-service vs. Passion — deferred until Phase 5.

    Telling the two apart means knowing what the dominant game actually *is*
    (a live-service loop vs. a world-like passion project), which lives in the
    game catalog `game_profiles` builds in Phase 5 and is empty right now. Until
    that lookup exists, guessing from behaviour alone would be exactly the kind
    of confident-sounding but ungrounded read the honesty principle rules out —
    so this says plainly that the read isn't available yet rather than faking
    one from the two axes alone.
    """
    return Subdivision(
        name="undetermined",
        detail=(
            "Live-service vs. passion needs to know what the dominant game "
            "actually is — that comes from the game catalog built in Phase 5, "
            "not from playtime shape alone."
        ),
        confident=False,
    )
