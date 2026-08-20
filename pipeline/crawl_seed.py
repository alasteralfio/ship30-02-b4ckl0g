"""Goal 3.2 — crawl the sourced seed IDs and persist their behaviour.

Reads the IDs chosen by `source_seed`, and for each one: fetches the library and
sampled achievements, reduces to the three axes, and stores the vector and shape
features in `seed_profiles` with the per-game playtimes in `seed_profile_playtimes`.
This is the offline job that turns a list of IDs into the reference crowd.

Three properties the roadmap asks for, and how they're met:

- **Idempotent and resumable.** A profile already in the store is skipped, so a
  re-run does no duplicate work and adds no duplicate rows. Kill it and restart
  and it picks up where it left off.
- **Respects rate limits.** The client backs off through Steam's 420/429; nothing
  more aggressive than the achievement sampler's bounded concurrency runs here.
- **Logs and skips, never drops silently.** A private or vanished profile is
  written to the skip log with its reason and passed over. Private profiles are
  permanent skips; transient errors are logged but retried on the next run,
  because a rate-limit blip shouldn't cost a profile forever.

Run:  python -m pipeline.crawl_seed [--limit N]   [needs STEAM_API_KEY in .env]
"""

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from b4cklog import store
from b4cklog.behaviour import reduce_to_behaviour
from b4cklog.config import steam_api_key
from b4cklog.steam import (
    Library,
    PrivateProfile,
    SteamClient,
    SteamError,
    UnknownSteamID,
    UnreadableReason,
)
from pipeline.source_seed import REQUEST_INTERVAL, SEED_IDS_PATH

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKIP_LOG_PATH = _PROJECT_ROOT / "data" / "seed_skips.jsonl"

# Skip reasons that are permanent: a private or vanished profile won't become
# readable on a re-run, so it's never retried. Anything else (a rate-limit or
# network error) is logged but left to retry next run.
_PERMANENT_REASONS = {
    UnreadableReason.PROFILE_PRIVATE.value,
    UnreadableReason.GAMES_PRIVATE.value,
    "unknown_account",
}


def _read_seed_ids(path: Path) -> list[str]:
    if not path.exists():
        sys.exit(f"No seed IDs at {path}. Run `python -m pipeline.source_seed` first.")
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _permanent_skips(path: Path) -> set[str]:
    """Steam IDs already logged as permanently unreadable — never retried."""
    if not path.exists():
        return set()
    skipped: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        entry = json.loads(line)
        if entry["reason"] in _PERMANENT_REASONS:
            skipped.add(entry["steam_id"])
    return skipped


def _log_skip(path: Path, steam_id: str, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {"steam_id": steam_id, "reason": reason, "at": _now_iso()}
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _persist(conn: sqlite3.Connection, steam_id: str, library: Library, achievements) -> None:
    """Reduce and store one profile and its played-game playtimes in one commit.

    Only games with real playtime are stored: `seed_profile_playtimes` feeds
    player-similarity, which is about time actually invested, so an owned-but-
    unplayed game carries no signal and would only bloat the table (some
    libraries run to a thousand-plus titles).
    """
    behaviour = reduce_to_behaviour(library, achievements)
    played = {g.app_id: g.playtime_minutes for g in library.games if g.playtime_minutes > 0}
    with conn:
        store.upsert_seed_profile(
            conn,
            steam_id,
            depth_breadth=behaviour.depth_breadth,
            completion_drive=behaviour.completion_drive,
            commitment_consistency=behaviour.commitment_consistency,
            shape_features=behaviour.shape,
            crawled_at=_now_iso(),
        )
        store.replace_seed_playtimes(conn, steam_id, played)


async def crawl_seed(
    steam_ids: list[str],
    conn: sqlite3.Connection,
    client: SteamClient,
    *,
    skip_log_path: Path = SKIP_LOG_PATH,
    progress=None,
) -> dict[str, int]:
    """Crawl each unseen seed ID into the store. Returns a tally of outcomes.

    `progress`, if given, is called with a short status line per ID — the CLI
    uses it to print live progress while staying testable in silence.
    """
    done = _already_crawled(conn)
    permanent = _permanent_skips(skip_log_path)
    pending = [sid for sid in steam_ids if sid not in done and sid not in permanent]
    counts = {"crawled": 0, "private": 0, "errors": 0, "skipped": len(steam_ids) - len(pending)}

    for i, steam_id in enumerate(pending, start=1):
        prefix = f"[{i}/{len(pending)}] {steam_id}"
        # One error boundary around the whole profile — read, achievements, and
        # persist. A single flaky game or transient HTTP error skips just this
        # profile (logged, retried next run), never the whole crawl.
        try:
            result = await client.read_library(steam_id)
            if isinstance(result, PrivateProfile):
                _log_skip(skip_log_path, steam_id, result.reason.value)
                counts["private"] += 1
                if progress:
                    progress(f"{prefix} — skipped ({result.reason.value})")
                continue
            achievements = await client.sample_achievements(result)
            _persist(conn, steam_id, result, achievements)
            counts["crawled"] += 1
            if progress:
                progress(f"{prefix} — {len(result.games)} games, {len(achievements)} achievement pulls")
        except UnknownSteamID:
            # Resolved when sourced but gone now — a permanent skip, not an error.
            _log_skip(skip_log_path, steam_id, "unknown_account")
            counts["private"] += 1
            if progress:
                progress(f"{prefix} — skipped (unknown_account)")
        except (SteamError, httpx.HTTPError) as e:
            _log_skip(skip_log_path, steam_id, _error_reason(e))
            counts["errors"] += 1
            if progress:
                progress(f"{prefix} — skipped (error, will retry)")

    return counts


def _error_reason(e: Exception) -> str:
    """A skip-log reason that never carries the key. Our SteamError messages are
    key-free by construction; httpx errors put the key in the URL, so those are
    reduced to a status or an error type instead of the raw message."""
    if isinstance(e, SteamError):
        return f"error: {e}"
    if isinstance(e, httpx.HTTPStatusError):
        return f"error: HTTP {e.response.status_code}"
    return f"error: {type(e).__name__}"


def _already_crawled(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT steam_id FROM seed_profiles").fetchall()
    return {r["steam_id"] for r in rows}


async def _run(limit: int | None) -> None:
    steam_ids = _read_seed_ids(SEED_IDS_PATH)
    if limit is not None:
        steam_ids = steam_ids[:limit]

    conn = store.connect()
    store.init_db(conn)  # idempotent; makes the crawl self-sufficient
    try:
        async with SteamClient(
            steam_api_key(), min_request_interval=REQUEST_INTERVAL
        ) as client:
            # flush each line: stdout is block-buffered when piped to a file, and
            # a long crawl should show live progress, not dump it all at the end.
            counts = await crawl_seed(
                steam_ids, conn, client, progress=lambda line: print(line, flush=True)
            )
    finally:
        conn.close()

    print(
        f"\nCrawled {counts['crawled']} new profiles "
        f"({counts['skipped']} already done, {counts['private']} private/gone, "
        f"{counts['errors']} errored — see {SKIP_LOG_PATH.name})."
    )


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Crawl the sourced seed IDs into the reference store.")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="crawl only the first N seed IDs — run a small batch first (Checkpoint 5)",
    )
    args = parser.parse_args()
    asyncio.run(_run(args.limit))


if __name__ == "__main__":
    main()
