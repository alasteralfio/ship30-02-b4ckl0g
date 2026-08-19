"""Reduce a player's raw Steam data to the three behavioural axes.

This is where genre and title fall away and only behaviour remains (PROJECT.md,
"How it works"). The three axes are engineered measures with fixed definitions,
not anything learned, so the same input always reduces to the same point — the
determinism the placement stage depends on. Each measure below is documented
with the reason it's shaped the way it is.

Standardization against the seed happens later, in placement/. Here the axes are
raw, interpretable numbers on their own scales; the mixture is what centers and
compares them.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np

from b4cklog.steam import AchievementStats, Library


@dataclass(frozen=True)
class Behaviour:
    """A player as a point on the three axes, plus the library-shape features
    the subdivisions read (kept in `shape`, which the store persists as JSON)."""

    depth_breadth: float          # higher = broader; ln of effective games played
    completion_drive: float       # 0..1; mean achievement completion where invested
    commitment_consistency: float # 0..1; higher = playtime spread more evenly
    shape: dict


def reduce_to_behaviour(
    library: Library, achievements: Mapping[int, AchievementStats]
) -> Behaviour:
    """Compute the three axes and library-shape features for one player.

    `achievements` is the sampled most-played games (Goal 2.2); games without
    achievements are simply absent from it, and contribute nothing to the
    completion read rather than being counted as zero.
    """
    played = [g.playtime_minutes for g in library.games if g.playtime_minutes > 0]
    games_owned = len(library.games)
    games_played = len(played)

    if games_played == 0:
        # No playtime means no behavioural signal at all — an empty or wholly
        # unplayed library. Rather than invent axes from nothing, place the
        # player at the neutral origin and let games_played carry the thinness
        # downstream, where the honesty principle says to admit weak signal
        # plainly (PROJECT.md, "Cold start"). No division happens on this path.
        return Behaviour(
            depth_breadth=0.0,
            completion_drive=0.0,
            commitment_consistency=0.0,
            shape=_shape(games_owned, games_played, played),
        )

    return Behaviour(
        # Effective number of games played (inverse-Simpson), log-compressed.
        # One game gives ln(1)=0 (pure depth); a wide, evenly played library
        # gives a large value (breadth). The log tames playtime's heavy right
        # tail so the mixture's Gaussian components sit on a roughly symmetric
        # spread instead of a few whales dragging the scale.
        depth_breadth=math.log(_effective_games(played)),
        # Mean completion across the games the player actually invested in. The
        # sampled games are the most-played, so this reads "when you commit to a
        # game, do you finish it and chase its achievements". Unweighted: a
        # 5-achievement game and a 200-achievement one count the same, which
        # over-credits trivial achievement sets but avoids letting one big game
        # define the whole read. Games with no achievements don't enter.
        completion_drive=_completion_drive(achievements),
        # 1 - Gini of playtime across played games. Gini 0 is a perfectly even
        # spread (consistent); Gini near 1 is one game dwarfing the rest (the
        # "800-hour game and forty 2-hour trials" shape). Inverted so higher
        # reads as more consistent, matching the axis name.
        commitment_consistency=1.0 - _gini(played),
        shape=_shape(games_owned, games_played, played),
    )


def _shape(games_owned: int, games_played: int, played: list[int]) -> dict:
    """Library-shape features beyond the three axes.

    `bimodality` is the one the subdivisions need directly: the split-shaped
    playtime curve — many short games and a few very long ones with little in
    between — is what separates a Breadth Completionist (even spread) from a
    Depth one (a series of deep wells), and it's the Curator's giveaway too. The
    counts and total exist to surface a thin or barely-touched library, which
    the report is meant to flag rather than read confidently.
    """
    return {
        "games_owned": games_owned,
        "games_played": games_played,
        "played_fraction": games_played / games_owned if games_owned else 0.0,
        "total_playtime_minutes": int(sum(played)),
        "bimodality": _bimodality(played),
    }


def _effective_games(minutes: list[int]) -> float:
    """Inverse-Simpson effective count: 1 / sum of squared playtime shares.

    Answers "how many games' worth of engagement is this really spread over",
    weighting by time rather than by mere ownership. One dominant game gives ~1;
    a hundred equally played games give ~100.
    """
    arr = np.asarray(minutes, dtype=float)
    shares = arr / arr.sum()
    return float(1.0 / np.square(shares).sum())


def _gini(minutes: list[int]) -> float:
    """Gini coefficient of playtime across played games, in [0, 1).

    A single played game gives 0 (trivially even); a distribution ruled by one
    outlier approaches 1. Computed from the sorted values, no binning.
    """
    arr = np.sort(np.asarray(minutes, dtype=float))
    n = arr.size
    rank = np.arange(1, n + 1)
    return float(np.sum((2 * rank - n - 1) * arr) / (n * arr.sum()))


def _completion_drive(achievements: Mapping[int, AchievementStats]) -> float:
    """Mean per-game achievement completion over the sampled games, in [0, 1].

    Zero when no sampled game had achievements — a real, common case (many games
    have none), reported as no observed completion drive rather than a NaN.
    """
    rates = [stats.completion_rate for stats in achievements.values()]
    return float(np.mean(rates)) if rates else 0.0


def _bimodality(minutes: list[int]) -> float:
    """Sarle's bimodality coefficient of log10(playtime), in (0, 1].

    Log scale because the short-vs-long split lives on orders of magnitude, not
    raw minutes. Values toward 1 mean a two-humped curve (the Depth Completionist
    and Curator shape); a single smooth hump sits lower. Needs at least four
    games to have a shape worth measuring, and needs some variance — a library
    of identical playtimes has no curve, so both degenerate cases return 0.
    """
    n = len(minutes)
    if n < 4:
        return 0.0
    x = np.log10(np.asarray(minutes, dtype=float))
    deviation = x - x.mean()
    m2 = float(np.mean(deviation**2))
    if m2 == 0.0:
        return 0.0
    m3 = float(np.mean(deviation**3))
    m4 = float(np.mean(deviation**4))
    skew = m3 / m2**1.5
    excess_kurtosis = m4 / m2**2 - 3.0
    # Sample-corrected skewness and kurtosis, so the coefficient isn't biased on
    # the small game counts a library realistically has.
    g1 = skew * math.sqrt(n * (n - 1)) / (n - 2)
    g2 = (n - 1) / ((n - 2) * (n - 3)) * ((n + 1) * excess_kurtosis + 6)
    return float((g1**2 + 1.0) / (g2 + 3.0 * (n - 1) ** 2 / ((n - 2) * (n - 3))))
