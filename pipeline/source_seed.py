"""Goal 3.1 — source a deliberately diverse seed of public Steam IDs.

The seed is the linchpin of the whole project: bias it and every cluster
downstream inherits the bias (PROJECT.md, "The bets"). So IDs are drawn at
random from the SteamID64 account-number space — never a friend graph or a
group, which would cluster around one crowd's habits and quietly invent
archetypes that are really just artifacts of who got sampled.

Random sampling alone isn't enough, though. The natural Steam population is
dominated by tiny, barely-touched accounts; take it as-is and the seed is a sea
of Dabbler-shaped players with the Obsessive and Completionist regions starved.
So candidates are lightly *stratified* over observable library shape (size,
games played, total hours): each shape gets a *cap* on how much of the seed it
may occupy, so no one common shape floods it. The run stops once the seed is
full (`target` profiles), not when every stratum brims — the rarest shapes are
genuinely uncommon, and waiting for a fixed quota of them would run for hours or
never finish. They get their honest natural share under the caps; whether that
share is thin enough to worry about is what the diversity summary is for.

The line held throughout is the one PROJECT.md's emergent-taxonomy principle
draws: we stratify on neutral, observable shape — not on completion behaviour and
not toward the five archetypes themselves. We give every region a fair chance to
appear; we don't coax it into existence. The full rationale and the biases this
still risks are written up in SEED.md.

This module only *chooses* IDs and writes them to `data/seed_ids.txt`. Fetching
each one's full behaviour and persisting it is the crawl's job (Goal 3.2).

Run:  python -m pipeline.source_seed [--target N] [--seed S]   [needs STEAM_API_KEY]
"""

import argparse
import asyncio
import json
import random
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from b4cklog.config import steam_api_key
from b4cklog.steam import OwnedGame, SteamClient, SteamError

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEED_IDS_PATH = _PROJECT_ROOT / "data" / "seed_ids.txt"
# A crash mid-run would otherwise lose every accepted ID (the final file is
# written only at the end), so the run is checkpointed here after each batch and
# resumed from it. Deleted on a clean finish.
CHECKPOINT_PATH = _PROJECT_ROOT / "data" / "seed_sourcing_checkpoint.json"

# An individual SteamID64 is this base plus the account number (the lower 32-bit
# accountid). Sampling account numbers uniformly draws every existing account
# with equal probability — the unbiased cross-section we want.
STEAM64_BASE = 76561197960265728
# A conservative upper bound on account numbers allocated as of 2026. Sampling
# above the true maximum only wastes candidates (they resolve to no account); it
# can't skew the draw. Sampling below it under-represents the newest accounts —
# a mild recency/tenure bias noted in SEED.md. Bump if the public-hit rate craters.
MAX_ACCOUNT_NUMBER = 1_700_000_000

# Steam's communityvisibilitystate for a public profile. Mirrors the client's
# own constant; kept here so the cheap batch filter doesn't reach into it.
_PUBLIC = 3

DEFAULT_TARGET = 300

# A profile must have played at least this many games to enter the seed. Below
# it the three axes are degenerate (Gini of one game is trivially even, effective
# games is ~1), and a live sourcing run showed thin accounts flooding the seed
# and faking an Obsessive cluster. Tunable; the axes need ~4+ games to mean much.
MIN_PLAYED_GAMES = 5
# Candidates resolved per GetPlayerSummaries batch — the client caps the call at
# 100, so this is the natural unit of work.
_BATCH = 100
# A ceiling on candidates sampled, so a bin the population barely contains can't
# spin the crawl forever. When it trips, the run ends short of target and the
# shortfall shows in the report.
DEFAULT_MAX_CANDIDATES = 60_000

# Consecutive batches lost entirely to rate-limit errors before the run gives up.
# When Steam is stonewalling, every call fails past its backoff; rather than burn
# the whole candidate ceiling firing into a wall, abort after a few dead batches,
# keep the checkpoint, and tell the user to try again later.
_MAX_DEAD_BATCHES = 3

# Concurrent owned-games calls per batch. Most visibility-public profiles turn
# out to hide their game details, so the great majority of these calls come back
# empty — a live smoke saw only ~6% of sampled candidates yield a readable
# library. Concurrency keeps a full run to minutes not hours, but GetOwnedGames
# is rate-limited hard: a live smoke at 10 tripped Steam's 420 within seconds.
# Kept low so the client's backoff is a safety net, not the main throttle.
DEFAULT_CONCURRENCY = 4

# How often (in batches of _BATCH candidates) to emit a progress line on a long
# run, so a silent stall or a rate-limit slowdown is visible instead of hidden.
_PROGRESS_EVERY = 10

# Seconds between request starts for the bulk crawl (shared with crawl_seed). A
# long run must stay under Steam's rolling-window rate limit (~100k/day ≈ 1.2/s
# average). Live experience: an unthrottled run tripped HTTP 429 within minutes
# and even ~1.4/s hit a hard cooldown over a sustained run, while ~1/s got
# through cleanly. So 1s — the honest sustainable rate; backoff and the resume
# path cover the occasional overage.
REQUEST_INTERVAL = 1.0


@dataclass
class Bin:
    """One stratum of the seed: a shape of library and a cap on how much of the
    seed it may hold. `cap` is a ceiling, not a quota — the run doesn't wait for
    a stratum to reach it, it only refuses to let a stratum exceed it."""

    name: str
    description: str
    cap: int
    members: list[str] = field(default_factory=list)

    @property
    def full(self) -> bool:
        return len(self.members) >= self.cap


# The strata, and the weight of the seed each may hold. Weights, not fixed counts,
# so --target scales the caps while keeping the proportions. The common middle is
# allowed the most; the rarer shapes are allowed less, so a flood of common
# accounts can't crowd them out. Every weight maps to an observable shape, and the
# archetype named beside it is only there to say *why the shape matters* — the
# placement model still discovers the taxonomy for itself (PROJECT.md).
_BIN_WEIGHTS: dict[str, tuple[float, str]] = {
    "concentrated": (0.13, "huge hours with most of the library untouched (Obsessive shape)"),
    "focused_deep": (0.15, "few games, deep wells of playtime (Depth Completionist / Passion shape)"),
    "broad_engaged": (0.20, "large library, genuinely played across it (Enthusiast / Curator shape)"),
    "broad_light": (0.15, "large library, little time in each (Dabbler shape)"),
    "modest": (0.27, "small-to-mid library, ordinary middling playtime"),
    "thin": (0.10, "small and barely-touched — new or light accounts"),
}

# Caps sum to more than `target` (this slack over 1.0), so the run can reach a
# full seed from the common shapes without stalling to fill the rare ones, while
# still capping any single shape well under the whole. At 1.8 no shape exceeds
# ~46% of the seed, and the common strata alone can supply the target.
_CAP_SLACK = 1.8


def default_bins(target: int = DEFAULT_TARGET) -> dict[str, Bin]:
    """Build the strata with per-shape caps scaled to `target` (see `_CAP_SLACK`)."""
    total_weight = sum(w for w, _ in _BIN_WEIGHTS.values())
    return {
        name: Bin(name, desc, max(1, round(target * weight / total_weight * _CAP_SLACK)))
        for name, (weight, desc) in _BIN_WEIGHTS.items()
    }


@dataclass(frozen=True)
class ShapeFeatures:
    """The cheap, achievement-free read used only to stratify a candidate.

    These are not the three behavioural axes — completion drive needs achievement
    calls the crawl makes, not the sourcer. They're the coarse library shape that
    a single owned-games call already gives, and it's enough to spread the seed.
    """

    games_owned: int
    games_played: int
    total_hours: float


def shape_of(games: Sequence[OwnedGame]) -> ShapeFeatures:
    played = [g for g in games if g.playtime_minutes > 0]
    return ShapeFeatures(
        games_owned=len(games),
        games_played=len(played),
        total_hours=sum(g.playtime_minutes for g in played) / 60.0,
    )


def classify(shape: ShapeFeatures) -> str:
    """Route a candidate to exactly one stratum by its observable shape.

    The order is deliberate: the rarer, more specific shapes are tested first so
    a deep-well or obsessive library lands in its own stratum instead of being
    absorbed by a broad one. Thresholds are heuristics for *spread*, not
    definitions of the archetypes — the model draws the real boundaries later.
    """
    owned, played, hours = shape.games_owned, shape.games_played, shape.total_hours
    played_fraction = played / owned if owned else 0.0
    hours_per_played = hours / played if played else 0.0

    if hours >= 800 and played_fraction <= 0.5:
        return "concentrated"
    if hours_per_played >= 60 and owned <= 80:
        return "focused_deep"
    if owned >= 150 and hours >= 300:
        return "broad_engaged"
    if owned >= 150:
        return "broad_light"
    if owned <= 15 and hours <= 20:
        return "thin"
    return "modest"


@dataclass
class SourcingRun:
    """The outcome of a sourcing pass: the filled strata and what it cost."""

    bins: dict[str, Bin]
    candidates_sampled: int = 0
    public_found: int = 0
    games_hidden: int = 0
    too_thin: int = 0              # readable but below the played-games floor
    rate_limited: int = 0          # calls dropped after backoff gave up
    stopped_early: bool = False
    rate_limit_aborted: bool = False  # stopped because Steam was stonewalling

    @property
    def accepted(self) -> list[str]:
        return [sid for b in self.bins.values() for sid in b.members]


def random_account_ids(rng: random.Random, count: int) -> list[str]:
    """`count` random SteamID64 strings drawn uniformly from the account space."""
    return [
        str(STEAM64_BASE + rng.randint(1, MAX_ACCOUNT_NUMBER)) for _ in range(count)
    ]


async def source_seed(
    client: SteamClient,
    *,
    target: int = DEFAULT_TARGET,
    rng: random.Random | None = None,
    max_candidates: int = DEFAULT_MAX_CANDIDATES,
    concurrency: int = DEFAULT_CONCURRENCY,
    run: SourcingRun | None = None,
    on_batch: Callable[[SourcingRun, int], None] | None = None,
) -> SourcingRun:
    """Sample, filter, and stratify candidate IDs until every stratum is full.

    Each round draws a batch of random IDs and resolves their visibility in one
    call. The public ones have their libraries read concurrently (bounded by
    `concurrency`), and each readable library is placed in a stratum by its
    shape. Games-hidden profiles (public community, private games) are counted
    and skipped, not dropped silently, as are calls rate-limited past backoff.

    `run`, if given, is a partial run to continue (resume after a crash). `on_batch`,
    if given, is called with (run, batch_number) after every batch — the caller
    uses it to checkpoint and to print progress. The run ends when `target`
    profiles are collected, the candidate ceiling is hit, or Steam rate-limits so
    hard that several batches in a row come back empty (`rate_limit_aborted`) —
    not when every stratum reaches its cap (see the module docstring on why).
    """
    rng = rng or random.Random()
    run = run or SourcingRun(bins=default_bins(target))
    semaphore = asyncio.Semaphore(concurrency)
    batch_number = 0
    dead_batches = 0  # consecutive batches lost entirely to rate limits

    while len(run.accepted) < target:
        if run.candidates_sampled >= max_candidates:
            run.stopped_early = True
            break

        candidates = random_account_ids(rng, _BATCH)
        run.candidates_sampled += len(candidates)

        try:
            summaries = await client.get_player_summaries(candidates)
        except SteamError:
            # The batch's one visibility call was rate-limited: nothing to show
            # for it. Count it dead and move on.
            run.rate_limited += 1
            dead_batches += 1
        else:
            public_ids = [s.steam_id for s in summaries if s.visibility == _PUBLIC]
            run.public_found += len(public_ids)

            # Once a couple of this batch's owned reads are refused past backoff,
            # Steam is throttling — skip the batch's remaining (and still-queued)
            # reads instantly instead of each waiting out its own backoff.
            fails = 0

            async def read_owned(steam_id: str) -> tuple[str, list[OwnedGame] | None, bool]:
                nonlocal fails
                if fails >= 2:
                    return steam_id, None, False
                async with semaphore:
                    if fails >= 2:
                        return steam_id, None, False
                    try:
                        return steam_id, await client.owned_games(steam_id), True
                    except SteamError:
                        fails += 1
                        return steam_id, None, False

            results = await asyncio.gather(*(read_owned(sid) for sid in public_ids))

            rate_limited_here = 0
            for steam_id, games, ok in results:
                if not ok:
                    run.rate_limited += 1
                    rate_limited_here += 1
                    continue
                if games is None:
                    run.games_hidden += 1
                    continue
                shape = shape_of(games)
                # A reference profile needs real behavioural signal. Below a few
                # played games the axes go degenerate — a one-game account reads
                # as perfectly "consistent" and maximally "concentrated" purely
                # for lack of data, and a seed full of them fakes an Obsessive
                # cluster out of nothing. Thin accounts are a visitor's honest
                # cold-start case, not something the reference crowd is built from.
                if shape.games_played < MIN_PLAYED_GAMES:
                    run.too_thin += 1
                    continue
                target_bin = run.bins[classify(shape)]
                # A resume that re-draws an already-accepted ID must not double-
                # count it; each ID classifies to one bin, so guarding it suffices.
                if not target_bin.full and steam_id not in target_bin.members:
                    target_bin.members.append(steam_id)

            # "Dead" only when the batch had public profiles and every one was
            # rate-limited — a batch that simply drew no public accounts is just
            # unlucky, not a sign Steam is refusing us.
            if public_ids and rate_limited_here == len(public_ids):
                dead_batches += 1
            else:
                dead_batches = 0

        batch_number += 1
        if on_batch:
            on_batch(run, batch_number)

        if dead_batches >= _MAX_DEAD_BATCHES:
            run.stopped_early = True
            run.rate_limit_aborted = True
            break

    return run


def _write_ids(ids: Sequence[str], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(ids) + "\n", encoding="utf-8")


def _print_report(run: SourcingRun) -> None:
    print(
        f"\nSampled {run.candidates_sampled} candidates -> "
        f"{run.public_found} public ({run.games_hidden} had hidden games, "
        f"{run.too_thin} too thin, {run.rate_limited} rate-limited)."
    )
    if run.stopped_early:
        print("Hit the candidate ceiling before the seed filled — it's short of target.")
    print("\nSeed spread by library shape (count / cap):")
    for b in run.bins.values():
        flag = "  << at cap" if b.full else ""
        print(f"  {len(b.members):>4}/{b.cap:<4} {b.name:<14} {b.description}{flag}")
    print(f"\n{len(run.accepted)} seed IDs written to {SEED_IDS_PATH}")


def _save_checkpoint(run: SourcingRun, path: Path = CHECKPOINT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "candidates_sampled": run.candidates_sampled,
        "public_found": run.public_found,
        "games_hidden": run.games_hidden,
        "too_thin": run.too_thin,
        "rate_limited": run.rate_limited,
        "bins": {name: b.members for name, b in run.bins.items()},
    }
    path.write_text(json.dumps(data), encoding="utf-8")


def _load_checkpoint(target: int, path: Path = CHECKPOINT_PATH) -> SourcingRun | None:
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    bins = default_bins(target)
    for name, members in data["bins"].items():
        if name in bins:  # ignore strata renamed since the checkpoint was written
            bins[name].members = list(members)
    return SourcingRun(
        bins=bins,
        candidates_sampled=data["candidates_sampled"],
        public_found=data["public_found"],
        games_hidden=data["games_hidden"],
        too_thin=data.get("too_thin", 0),
        rate_limited=data.get("rate_limited", 0),
    )


def _print_progress(run: SourcingRun) -> None:
    filled = sum(b.full for b in run.bins.values())
    print(
        f"  sampled {run.candidates_sampled}, {run.public_found} public, "
        f"{len(run.accepted)} accepted, {filled}/{len(run.bins)} strata full",
        flush=True,
    )


async def _run(target: int, seed: int | None, max_candidates: int) -> None:
    rng = random.Random(seed)
    resumed = _load_checkpoint(target)
    if resumed:
        print(
            f"Resuming: {len(resumed.accepted)} already accepted, "
            f"{resumed.candidates_sampled} candidates sampled."
        )
        # Fast-forward the RNG to where the previous run left off so the resume
        # draws fresh candidates rather than re-checking the same accounts. Only
        # possible with a fixed seed; an unseeded resume just draws anew (the
        # dedup guard in source_seed keeps that safe).
        if seed is not None:
            for _ in range(resumed.candidates_sampled):
                rng.randint(1, MAX_ACCOUNT_NUMBER)

    def on_batch(run: SourcingRun, batch_number: int) -> None:
        _save_checkpoint(run)  # every batch, so a crash loses at most one batch
        if batch_number % _PROGRESS_EVERY == 0:
            _print_progress(run)

    async with SteamClient(
        steam_api_key(), min_request_interval=REQUEST_INTERVAL
    ) as client:
        run = await source_seed(
            client, target=target, rng=rng, max_candidates=max_candidates,
            run=resumed, on_batch=on_batch,
        )

    if run.rate_limit_aborted:
        # Keep the checkpoint and leave any existing seed_ids.txt untouched, so a
        # later re-run resumes from here instead of starting over.
        print(
            f"\nAborted: Steam rate-limited {_MAX_DEAD_BATCHES} batches in a row. "
            f"{len(run.accepted)} accepted so far and checkpointed — wait for the "
            "limit to cool, then re-run to resume."
        )
        return

    _write_ids(run.accepted, SEED_IDS_PATH)
    _print_report(run)
    if not run.stopped_early:
        CHECKPOINT_PATH.unlink(missing_ok=True)  # target met — no stale resume state


def main() -> None:
    # The report carries en/em dashes; force UTF-8 so the Windows console
    # (cp1252 by default) prints them instead of mangling to '?'.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Source a diverse seed of public Steam IDs.")
    parser.add_argument("--target", type=int, default=DEFAULT_TARGET, help="seed size to aim for")
    parser.add_argument(
        "--seed", type=int, default=None,
        help="RNG seed — makes the candidate draw reproducible (live results still vary)",
    )
    parser.add_argument(
        "--max-candidates", type=int, default=DEFAULT_MAX_CANDIDATES,
        help="ceiling on candidates sampled; run a small batch first to gauge the hit rate",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.target, args.seed, args.max_candidates))


if __name__ == "__main__":
    main()
