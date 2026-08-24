"""The reference store: schema and a thin sqlite3 access layer.

Standard-library sqlite3, plain SQL, no ORM (CLAUDE.md). The store holds the
offline-built reference data — seed profiles, their per-game playtimes, and
game profiles. The live app only reads it; every write comes from `pipeline/`
(PROJECT.md, "How it's built"), so the write helpers below are pipeline-only
even though they live in the shared layer.

Transactions are the caller's to control: no helper commits on its own. Wrap
writes in `with conn:` so a profile and its playtimes land together or not at
all. `init_db` is the exception — its DDL auto-commits, as sqlite3 always does
around `executescript`.
"""

import json
import sqlite3
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _PROJECT_ROOT / "data" / "b4cklog.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS seed_profiles (
    steam_id                TEXT PRIMARY KEY,
    -- The three behavioural axes (PROJECT.md). Engineered measures with fixed
    -- definitions, so the same player always reduces to the same point.
    depth_breadth           REAL NOT NULL,
    completion_drive        REAL NOT NULL,
    commitment_consistency  REAL NOT NULL,
    -- Library-shape features the subdivisions need. A JSON object rather than
    -- named columns because the exact set is defined later, in behaviour/
    -- (Goal 2.3); naming columns now would be guessing.
    shape_features          TEXT NOT NULL,
    crawled_at              TEXT NOT NULL  -- ISO 8601
);

CREATE TABLE IF NOT EXISTS seed_profile_playtimes (
    steam_id        TEXT NOT NULL,
    app_id          INTEGER NOT NULL,
    minutes_played  INTEGER NOT NULL,
    PRIMARY KEY (steam_id, app_id),
    FOREIGN KEY (steam_id) REFERENCES seed_profiles (steam_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS game_profiles (
    app_id              INTEGER PRIMARY KEY,
    name                TEXT,
    -- Solid facts (from Store appdetails / global achievement percentages).
    has_achievements    INTEGER NOT NULL,   -- 0/1
    achievement_count   INTEGER,
    completability      REAL,               -- 0..1; NULL when a game has no achievements
    -- Softer, inferred demands (PROJECT.md, "What we profile a game on").
    hours_to_beat       REAL,
    hours_to_complete   REAL,
    is_finite           INTEGER,            -- 0/1; NULL when unknown
    progression_style   TEXT,
    content_shape       TEXT,
    -- Whether the game is part of the curated outside pool for discovery. Seed
    -- ownership is derivable from seed_profile_playtimes, so it isn't stored.
    in_outside_pool     INTEGER NOT NULL DEFAULT 0,
    profiled_at         TEXT
);

-- One row per profiled field: its source and whether it's an estimate or
-- solid. Kept relational, not inline, because solid-vs-estimate is load-bearing
-- (it's surfaced to users) and the field set is still settling in Phase 5.
CREATE TABLE IF NOT EXISTS game_field_provenance (
    app_id      INTEGER NOT NULL,
    field       TEXT NOT NULL,       -- matches a column name in game_profiles
    source      TEXT NOT NULL,
    is_estimate INTEGER NOT NULL,    -- 1 = soft/estimate, 0 = solid
    PRIMARY KEY (app_id, field),
    FOREIGN KEY (app_id) REFERENCES game_profiles (app_id) ON DELETE CASCADE
);
"""


def connect(db_path: Path | str = DB_PATH) -> sqlite3.Connection:
    """Open a connection with row access by name and foreign keys enforced.

    Does not create the parent directory: a missing `data/` means the
    reference data hasn't been built, which the app should fail on rather than
    paper over. Directory creation belongs to the pipeline init script.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create every table if absent. Idempotent — never drops or wipes."""
    conn.executescript(SCHEMA)


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return [r["name"] for r in rows]


def column_names(conn: sqlite3.Connection, table: str) -> list[str]:
    # PRAGMA can't be parameterized; table is a trusted internal name.
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return [r["name"] for r in rows]


# --- Seed profiles (written by pipeline/, read by app + pipeline) ---

def upsert_seed_profile(
    conn: sqlite3.Connection,
    steam_id: str,
    *,
    depth_breadth: float,
    completion_drive: float,
    commitment_consistency: float,
    shape_features: dict,
    crawled_at: str,
) -> None:
    """Insert a seed profile, or overwrite it if the ID was crawled before.

    Overwriting (not skipping) is what makes a re-crawl idempotent: the same
    ID re-crawled updates in place instead of erroring or duplicating.
    """
    conn.execute(
        """
        INSERT INTO seed_profiles
            (steam_id, depth_breadth, completion_drive,
             commitment_consistency, shape_features, crawled_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(steam_id) DO UPDATE SET
            depth_breadth = excluded.depth_breadth,
            completion_drive = excluded.completion_drive,
            commitment_consistency = excluded.commitment_consistency,
            shape_features = excluded.shape_features,
            crawled_at = excluded.crawled_at
        """,
        (
            steam_id,
            depth_breadth,
            completion_drive,
            commitment_consistency,
            json.dumps(shape_features),
            crawled_at,
        ),
    )


def get_seed_profile(conn: sqlite3.Connection, steam_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM seed_profiles WHERE steam_id = ?", (steam_id,)
    ).fetchone()
    if row is None:
        return None
    profile = dict(row)
    profile["shape_features"] = json.loads(profile["shape_features"])
    return profile


def all_seed_profiles(conn: sqlite3.Connection) -> list[dict]:
    """Every seed profile, shape features parsed. The whole reference crowd —
    read by the diversity summary now and the placement fit in Phase 4."""
    rows = conn.execute("SELECT * FROM seed_profiles").fetchall()
    profiles = []
    for row in rows:
        profile = dict(row)
        profile["shape_features"] = json.loads(profile["shape_features"])
        profiles.append(profile)
    return profiles


def replace_seed_playtimes(
    conn: sqlite3.Connection, steam_id: str, minutes_by_app: dict[int, int]
) -> None:
    """Set a player's per-game playtimes, replacing any previous set.

    Delete-then-insert keeps a re-crawl idempotent and correct even when the
    player's library shrank between crawls.
    """
    conn.execute(
        "DELETE FROM seed_profile_playtimes WHERE steam_id = ?", (steam_id,)
    )
    conn.executemany(
        "INSERT INTO seed_profile_playtimes (steam_id, app_id, minutes_played)"
        " VALUES (?, ?, ?)",
        [(steam_id, app_id, minutes) for app_id, minutes in minutes_by_app.items()],
    )


def get_seed_playtimes(conn: sqlite3.Connection, steam_id: str) -> dict[int, int]:
    rows = conn.execute(
        "SELECT app_id, minutes_played FROM seed_profile_playtimes WHERE steam_id = ?",
        (steam_id,),
    ).fetchall()
    return {r["app_id"]: r["minutes_played"] for r in rows}


def distinct_seed_app_ids(conn: sqlite3.Connection) -> set[int]:
    """Every game any seed member has played. Game profiling (Phase 5) needs
    this to know its scope: "every game in the seed libraries" (Goal 5.1)."""
    rows = conn.execute("SELECT DISTINCT app_id FROM seed_profile_playtimes").fetchall()
    return {r["app_id"] for r in rows}


def seed_minutes_for_app(conn: sqlite3.Connection, app_id: int) -> list[int]:
    """Every seed member's playtime in one game, unsorted.

    The length estimate (Goal 5.2) reads a game's demands off how long the
    seed itself actually spent in it — the SteamSpy-shaped proxy PROJECT.md
    calls for since SteamSpy itself is blocked. Empty for a game nobody in the
    seed has played (e.g. most of the curated outside pool), which the caller
    must treat as "no length signal" rather than inventing one.
    """
    rows = conn.execute(
        "SELECT minutes_played FROM seed_profile_playtimes WHERE app_id = ?",
        (app_id,),
    ).fetchall()
    return [r["minutes_played"] for r in rows]


# --- Game profiles (written by pipeline/, read by app + pipeline) ---

def upsert_game_profile(
    conn: sqlite3.Connection,
    app_id: int,
    *,
    name: str | None,
    has_achievements: bool,
    achievement_count: int | None = None,
    completability: float | None = None,
    hours_to_beat: float | None = None,
    hours_to_complete: float | None = None,
    is_finite: bool | None = None,
    progression_style: str | None = None,
    content_shape: str | None = None,
    in_outside_pool: bool = False,
    profiled_at: str | None = None,
    provenance: dict[str, tuple[str, bool]] | None = None,
) -> None:
    """Insert or overwrite a game profile and its per-field provenance.

    `provenance` maps a field name to `(source, is_estimate)`. It's kept
    alongside the values so a field can never be stored without saying where it
    came from and whether it's an estimate.
    """
    conn.execute(
        """
        INSERT INTO game_profiles
            (app_id, name, has_achievements, achievement_count, completability,
             hours_to_beat, hours_to_complete, is_finite, progression_style,
             content_shape, in_outside_pool, profiled_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(app_id) DO UPDATE SET
            name = excluded.name,
            has_achievements = excluded.has_achievements,
            achievement_count = excluded.achievement_count,
            completability = excluded.completability,
            hours_to_beat = excluded.hours_to_beat,
            hours_to_complete = excluded.hours_to_complete,
            is_finite = excluded.is_finite,
            progression_style = excluded.progression_style,
            content_shape = excluded.content_shape,
            in_outside_pool = excluded.in_outside_pool,
            profiled_at = excluded.profiled_at
        """,
        (
            app_id,
            name,
            int(has_achievements),
            achievement_count,
            completability,
            hours_to_beat,
            hours_to_complete,
            None if is_finite is None else int(is_finite),
            progression_style,
            content_shape,
            int(in_outside_pool),
            profiled_at,
        ),
    )
    for field, (source, is_estimate) in (provenance or {}).items():
        conn.execute(
            """
            INSERT INTO game_field_provenance (app_id, field, source, is_estimate)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(app_id, field) DO UPDATE SET
                source = excluded.source,
                is_estimate = excluded.is_estimate
            """,
            (app_id, field, source, int(is_estimate)),
        )


def get_game_profile(conn: sqlite3.Connection, app_id: int) -> dict | None:
    """Return a game profile with its provenance under a `provenance` key."""
    row = conn.execute(
        "SELECT * FROM game_profiles WHERE app_id = ?", (app_id,)
    ).fetchone()
    if row is None:
        return None
    profile = dict(row)
    prov_rows = conn.execute(
        "SELECT field, source, is_estimate FROM game_field_provenance WHERE app_id = ?",
        (app_id,),
    ).fetchall()
    profile["provenance"] = {
        r["field"]: {"source": r["source"], "is_estimate": bool(r["is_estimate"])}
        for r in prov_rows
    }
    return profile
