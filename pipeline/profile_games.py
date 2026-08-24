"""Goals 5.1/5.2 — profile every game in scope from keyless Steam data.

Scope is every game any seed member has played (`store.distinct_seed_app_ids`)
plus the curated outside pool (`pipeline/outside_pool.py`) — together, every
game the recommend/ stage (Phase 6) can ever surface. Both `appdetails` and
the global achievement-percentage endpoint are keyless
(`b4cklog/steam/gamedata.py`); this stage never touches `STEAM_API_KEY`.

Completability is solid: read straight off Steam's own global achievement
percentages. Length, finite-vs-endless, progression style, and content shape
are estimates — SteamSpy is blocked, so length leans on the seed's own
per-game playtime (`store.seed_minutes_for_app`), and the rest lean on Store
genre/category tags. Every soft field is stored with an explicit source and
an is_estimate flag (`game_field_provenance`) rather than presented as fact
(PROJECT.md, "Honesty over comfort" — applies to the model's own data, not
just its copy).

Idempotent and resumable like the seed crawl: an already-profiled game is
skipped, and delisted/non-game IDs are logged as permanent skips so a re-run
doesn't keep re-asking Steam about them.

Run:  python -m pipeline.profile_games [--limit N]
      python -m pipeline.profile_games --refresh-derived [--limit N]  # see refresh_derived_fields
"""

import argparse
import asyncio
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

from b4cklog import store
from b4cklog.steam import AchievementPercent, AppDetails, GameDataClient, GameDataError
from pipeline.outside_pool import OUTSIDE_POOL_APP_IDS

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKIP_LOG_PATH = _PROJECT_ROOT / "data" / "game_profile_skips.jsonl"

REQUEST_INTERVAL = 1.8  # appdetails is rate-limited harder than the main API

# Genre/category tags that read as "no natural stopping point" (PROJECT.md,
# "What we profile a game on"). An MMO is endless by definition; a free-to-play
# game without a single-player mode is the live-service shape PROJECT.md names
# for the Obsessive archetype specifically.
_ENDLESS_GENRES = {"Massively Multiplayer"}
_LIVE_SERVICE_GENRES = {"Free To Play"}

# First matching genre wins — ordered most-specific-shape first. A coarse,
# openly heuristic map from Store genres to PROJECT.md's progression styles;
# there's no solid source for this, so it's always stored as an estimate.
_PROGRESSION_BY_GENRE = (
    (("Massively Multiplayer", "Free To Play"), "grinding"),
    (("RPG", "Strategy", "Adventure"), "exploration_narrative"),
    (("Action", "Platformer", "Sports", "Racing"), "skill_mastery"),
)

_SPRAWLING_GENRES = {"RPG", "Strategy", "Massively Multiplayer", "Simulation"}
_FOCUSED_GENRES = {"Puzzle", "Platformer", "Casual"}
_SPRAWLING_HOURS_THRESHOLD = 15.0

# Permanent: re-fetching won't change a delisted app or turn a soundtrack into
# a game. Everything else (rate limits, transient errors) is retried next run.
_PERMANENT_REASONS = {"delisted", "not_a_game"}


def target_app_ids(conn) -> set[int]:
    """Every game Phase 6 could ever need a profile for."""
    return store.distinct_seed_app_ids(conn) | set(OUTSIDE_POOL_APP_IDS)


def _completability(percentages: list[AchievementPercent] | None) -> float | None:
    """Mean global unlock rate across achievements, in [0, 1] — solid, straight
    off Steam's own numbers. None when the game has no achievements; never
    forced to a fake number for a game that has nothing to complete."""
    if not percentages:
        return None
    return sum(p.percent for p in percentages) / len(percentages) / 100.0


def _length_estimate(minutes: list[int]) -> tuple[float | None, float | None]:
    """Rough hours-to-beat and hours-to-complete from the seed's own time in
    this game. Median stands in for "typical" playtime; the 75th percentile of
    the same distribution approximates the more committed tail's full
    completion. Both None when nobody in the seed has played it — an honest
    gap (most of the outside pool), not a guess."""
    if not minutes:
        return None, None
    hours = sorted(m / 60.0 for m in minutes)
    hours_to_beat = statistics.median(hours)
    idx = min(len(hours) - 1, int(0.75 * len(hours)))
    hours_to_complete = hours[idx]
    return hours_to_beat, hours_to_complete


def _is_finite(genres: tuple[str, ...], categories: tuple[str, ...]) -> bool | None:
    """Finite-vs-endless: always an estimate, never solid. Read purely from
    Store tags — an MMO is endless by definition, and "Free To Play" is Valve's
    own genre tag for the live-service business model (seasons, battle passes,
    persistent grind), not just "costs nothing." None when tags say nothing
    either way.

    The Free To Play rule originally also required *no* Single-player category,
    reasoning that a live-service game wouldn't offer solo play. Wrong: Warframe
    and Destiny 2 both ship single-player-capable missions on top of an
    otherwise endless live-service loop, and both carry the Single-player tag —
    so the old rule read them as finite. Genre alone is the reliable signal
    here; verified live against Warframe, Destiny 2, TF2, Apex Legends, CS2,
    and Path of Exile, which all carry "Free To Play" and none of which are
    finite.

    Playtime shape was tried as a second signal too ("one seed player's hours
    dwarf the median" => endless) and dropped: at 1500+ seed profiles there is
    almost always a completionist, replayer, or idler who blows past a normal
    finish in any popular game, so the signal flagged genuinely finite games
    like Elden Ring and Stardew Valley as endless. A confident-looking wrong
    estimate is worse than an honest unknown (PROJECT.md, "Honesty over
    comfort"), so this stays a coarse tag read rather than a clever one.
    """
    genre_set = set(genres)
    if genre_set & _ENDLESS_GENRES:
        return False
    if genre_set & _LIVE_SERVICE_GENRES:
        return False
    if "Single-player" in categories:
        return True
    return None


def _progression_style(genres: tuple[str, ...]) -> str | None:
    genre_set = set(genres)
    for keywords, style in _PROGRESSION_BY_GENRE:
        if genre_set & set(keywords):
            return style
    return None


def _content_shape(genres: tuple[str, ...], hours_to_complete: float | None) -> str | None:
    genre_set = set(genres)
    if genre_set & _SPRAWLING_GENRES:
        return "sprawling"
    if genre_set & _FOCUSED_GENRES:
        return "focused"
    if hours_to_complete is not None:
        return "sprawling" if hours_to_complete >= _SPRAWLING_HOURS_THRESHOLD else "focused"
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _log_skip(path: Path, app_id: int, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"app_id": app_id, "reason": reason, "at": _now_iso()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _permanent_skips(path: Path) -> set[int]:
    if not path.exists():
        return set()
    skipped: set[int] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry["reason"] in _PERMANENT_REASONS:
            skipped.add(entry["app_id"])
    return skipped


def _already_profiled(conn) -> set[int]:
    rows = conn.execute("SELECT app_id FROM game_profiles").fetchall()
    return {r["app_id"] for r in rows}


def _build_profile(app_id: int, details: AppDetails, minutes: list[int], percentages) -> dict:
    """Turn raw metadata into the fields `store.upsert_game_profile` wants,
    with provenance recorded for every soft field."""
    completability = _completability(percentages)
    hours_to_beat, hours_to_complete = _length_estimate(minutes)
    is_finite = _is_finite(details.genres, details.categories)
    progression_style = _progression_style(details.genres)
    content_shape = _content_shape(details.genres, hours_to_complete)

    provenance: dict[str, tuple[str, bool]] = {}
    if completability is not None:
        provenance["completability"] = ("global_achievement_pct", False)
    if hours_to_beat is not None:
        provenance["hours_to_beat"] = ("seed_playtime", True)
        provenance["hours_to_complete"] = ("seed_playtime", True)
    if is_finite is not None:
        provenance["is_finite"] = ("genre_and_category_tags", True)
    if progression_style is not None:
        provenance["progression_style"] = ("genre_tags", True)
    if content_shape is not None:
        provenance["content_shape"] = ("genre_tags_or_seed_playtime", True)

    return dict(
        name=details.name,
        has_achievements=details.achievement_count > 0,
        achievement_count=details.achievement_count or None,
        completability=completability,
        hours_to_beat=hours_to_beat,
        hours_to_complete=hours_to_complete,
        is_finite=is_finite,
        progression_style=progression_style,
        content_shape=content_shape,
        in_outside_pool=app_id in OUTSIDE_POOL_APP_IDS,
        profiled_at=_now_iso(),
        provenance=provenance,
    )


async def profile_games(
    app_ids: list[int],
    conn,
    client: GameDataClient,
    *,
    skip_log_path: Path = SKIP_LOG_PATH,
    progress=None,
) -> dict[str, int]:
    """Profile each unprofiled app ID into the store. Returns an outcome tally."""
    done = _already_profiled(conn)
    permanent = _permanent_skips(skip_log_path)
    pending = [a for a in app_ids if a not in done and a not in permanent]
    counts = {
        "profiled": 0, "no_achievements": 0, "delisted": 0,
        "errors": 0, "skipped": len(app_ids) - len(pending),
    }

    for i, app_id in enumerate(pending, start=1):
        prefix = f"[{i}/{len(pending)}] {app_id}"
        try:
            details = await client.get_app_details(app_id)
            if details is None:
                _log_skip(skip_log_path, app_id, "delisted")
                counts["delisted"] += 1
                if progress:
                    progress(f"{prefix} — skipped (delisted)")
                continue
            if details.type != "game":
                _log_skip(skip_log_path, app_id, "not_a_game")
                counts["delisted"] += 1
                if progress:
                    progress(f"{prefix} — skipped (not a game: {details.type})")
                continue

            percentages = None
            if details.achievement_count > 0:
                percentages = await client.get_global_achievement_percentages(app_id)
            else:
                counts["no_achievements"] += 1

            minutes = store.seed_minutes_for_app(conn, app_id)
            profile = _build_profile(app_id, details, minutes, percentages)
            with conn:
                store.upsert_game_profile(conn, app_id, **profile)
            counts["profiled"] += 1
            if progress:
                progress(f"{prefix} — {details.name!r}, {details.achievement_count} achievements")
        except GameDataError as e:
            _log_skip(skip_log_path, app_id, f"error: {e}")
            counts["errors"] += 1
            if progress:
                progress(f"{prefix} — skipped (error, will retry)")

    return counts


async def refresh_derived_fields(
    app_ids: list[int], conn, client: GameDataClient, *, progress=None
) -> dict[str, int]:
    """Re-fetch `appdetails` only (never the achievement-percentage call) for
    already-profiled games and recompute the tag/playtime-derived fields,
    leaving the existing achievement data untouched.

    Exists for one reason: an earlier version of `_is_finite` used playtime
    shape as a fallback signal and got it wrong at seed scale (see its
    docstring). This repairs profiles built under that version without
    re-spending the achievement-percentage calls, which were never wrong and
    haven't changed — halving the network cost of a full re-crawl.
    """
    counts = {"updated": 0, "skipped": 0, "errors": 0}
    for i, app_id in enumerate(app_ids, start=1):
        existing = store.get_game_profile(conn, app_id)
        if existing is None:
            counts["skipped"] += 1
            continue
        try:
            details = await client.get_app_details(app_id)
        except GameDataError:
            counts["errors"] += 1
            if progress:
                progress(f"[{i}/{len(app_ids)}] {app_id} — error, will retry next run")
            continue
        if details is None or details.type != "game":
            counts["skipped"] += 1
            continue

        minutes = store.seed_minutes_for_app(conn, app_id)
        hours_to_beat, hours_to_complete = _length_estimate(minutes)
        is_finite = _is_finite(details.genres, details.categories)
        progression_style = _progression_style(details.genres)
        content_shape = _content_shape(details.genres, hours_to_complete)

        provenance: dict[str, tuple[str, bool]] = {}
        if hours_to_beat is not None:
            provenance["hours_to_beat"] = ("seed_playtime", True)
            provenance["hours_to_complete"] = ("seed_playtime", True)
        if is_finite is not None:
            provenance["is_finite"] = ("genre_and_category_tags", True)
        if progression_style is not None:
            provenance["progression_style"] = ("genre_tags", True)
        if content_shape is not None:
            provenance["content_shape"] = ("genre_tags_or_seed_playtime", True)

        with conn:
            store.upsert_game_profile(
                conn, app_id,
                name=existing["name"],
                has_achievements=bool(existing["has_achievements"]),
                achievement_count=existing["achievement_count"],
                completability=existing["completability"],
                hours_to_beat=hours_to_beat,
                hours_to_complete=hours_to_complete,
                is_finite=is_finite,
                progression_style=progression_style,
                content_shape=content_shape,
                in_outside_pool=bool(existing["in_outside_pool"]),
                profiled_at=_now_iso(),
                provenance=provenance,
            )
        counts["updated"] += 1
        if progress:
            progress(f"[{i}/{len(app_ids)}] {app_id} — refreshed")

    return counts


async def _run(limit: int | None, refresh_derived: bool) -> None:
    conn = store.connect()
    store.init_db(conn)
    try:
        if refresh_derived:
            app_ids = sorted(_already_profiled(conn))
            if limit is not None:
                app_ids = app_ids[:limit]
            async with GameDataClient(min_request_interval=REQUEST_INTERVAL) as client:
                counts = await refresh_derived_fields(
                    app_ids, conn, client, progress=lambda line: print(line, flush=True)
                )
            print(
                f"\nRefreshed {counts['updated']} profiles "
                f"({counts['skipped']} skipped, {counts['errors']} errored — rerun to retry)."
            )
            return

        app_ids = sorted(target_app_ids(conn))
        if limit is not None:
            app_ids = app_ids[:limit]

        async with GameDataClient(min_request_interval=REQUEST_INTERVAL) as client:
            counts = await profile_games(
                app_ids, conn, client, progress=lambda line: print(line, flush=True)
            )
    finally:
        conn.close()

    print(
        f"\nProfiled {counts['profiled']} games "
        f"({counts['skipped']} already done, {counts['delisted']} delisted/non-game, "
        f"{counts['no_achievements']} with no achievements, "
        f"{counts['errors']} errored — see {SKIP_LOG_PATH.name})."
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Profile every game in scope from keyless Steam data.")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="profile only the first N games (sorted by app id) — run a small batch first",
    )
    parser.add_argument(
        "--refresh-derived", action="store_true",
        help="re-fetch appdetails only for already-profiled games and recompute "
        "is_finite/progression_style/content_shape (see refresh_derived_fields)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.limit, args.refresh_derived))


if __name__ == "__main__":
    main()
