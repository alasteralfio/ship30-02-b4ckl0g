"""Reference store: schema stands up, init is idempotent, and rows round-trip
through the access layer."""

import sqlite3

import pytest

from b4cklog import store


@pytest.fixture
def conn():
    c = store.connect(":memory:")
    store.init_db(c)
    yield c
    c.close()


def test_all_tables_created(conn):
    assert set(store.table_names(conn)) == {
        "seed_profiles",
        "seed_profile_playtimes",
        "game_profiles",
        "game_field_provenance",
    }


def test_intended_columns(conn):
    assert store.column_names(conn, "seed_profiles") == [
        "steam_id",
        "depth_breadth",
        "completion_drive",
        "commitment_consistency",
        "shape_features",
        "crawled_at",
    ]
    assert store.column_names(conn, "seed_profile_playtimes") == [
        "steam_id",
        "app_id",
        "minutes_played",
    ]


def test_reinit_is_safe_and_preserves_data(conn):
    store.upsert_seed_profile(
        conn,
        "76561197960287930",
        depth_breadth=0.5,
        completion_drive=0.8,
        commitment_consistency=0.3,
        shape_features={"bimodality": 0.7},
        crawled_at="2026-08-19T00:00:00Z",
    )
    store.init_db(conn)  # re-running must not drop or wipe
    assert store.get_seed_profile(conn, "76561197960287930") is not None
    assert len(store.table_names(conn)) == 4


def test_seed_profile_round_trip(conn):
    store.upsert_seed_profile(
        conn,
        "abc",
        depth_breadth=0.5,
        completion_drive=0.8,
        commitment_consistency=0.3,
        shape_features={"bimodality": 0.7, "top_share": 0.4},
        crawled_at="2026-08-19T00:00:00Z",
    )
    profile = store.get_seed_profile(conn, "abc")
    assert profile["completion_drive"] == 0.8
    # shape_features round-trips as a dict, not a JSON string
    assert profile["shape_features"] == {"bimodality": 0.7, "top_share": 0.4}


def test_upsert_overwrites_not_duplicates(conn):
    for drive in (0.2, 0.9):
        store.upsert_seed_profile(
            conn,
            "abc",
            depth_breadth=0.5,
            completion_drive=drive,
            commitment_consistency=0.3,
            shape_features={},
            crawled_at="2026-08-19T00:00:00Z",
        )
    count = conn.execute("SELECT COUNT(*) FROM seed_profiles").fetchone()[0]
    assert count == 1
    assert store.get_seed_profile(conn, "abc")["completion_drive"] == 0.9


def test_playtimes_replace_is_idempotent(conn):
    store.upsert_seed_profile(
        conn,
        "abc",
        depth_breadth=0.0,
        completion_drive=0.0,
        commitment_consistency=0.0,
        shape_features={},
        crawled_at="2026-08-19T00:00:00Z",
    )
    store.replace_seed_playtimes(conn, "abc", {440: 1200, 570: 30})
    store.replace_seed_playtimes(conn, "abc", {440: 1500})  # re-crawl, shrunk
    assert store.get_seed_playtimes(conn, "abc") == {440: 1500}


def test_distinct_seed_app_ids_spans_every_profile(conn):
    for steam_id, playtimes in (("a", {440: 100, 570: 50}), ("b", {570: 30, 620: 10})):
        store.upsert_seed_profile(
            conn, steam_id, depth_breadth=0.0, completion_drive=0.0,
            commitment_consistency=0.0, shape_features={}, crawled_at="2026-08-19T00:00:00Z",
        )
        store.replace_seed_playtimes(conn, steam_id, playtimes)
    assert store.distinct_seed_app_ids(conn) == {440, 570, 620}


def test_seed_minutes_for_app_pools_every_player(conn):
    for steam_id, playtimes in (("a", {440: 100}), ("b", {440: 250}), ("c", {620: 10})):
        store.upsert_seed_profile(
            conn, steam_id, depth_breadth=0.0, completion_drive=0.0,
            commitment_consistency=0.0, shape_features={}, crawled_at="2026-08-19T00:00:00Z",
        )
        store.replace_seed_playtimes(conn, steam_id, playtimes)
    assert sorted(store.seed_minutes_for_app(conn, 440)) == [100, 250]
    assert store.seed_minutes_for_app(conn, 999) == []  # nobody played it — no signal, not an error


def test_game_profile_round_trip_with_provenance(conn):
    store.upsert_game_profile(
        conn,
        620,
        name="Portal 2",
        has_achievements=True,
        achievement_count=51,
        completability=0.34,
        hours_to_beat=8.5,
        is_finite=True,
        in_outside_pool=True,
        profiled_at="2026-08-19T00:00:00Z",
        provenance={
            "completability": ("global_achievement_pct", False),
            "hours_to_beat": ("seed_playtime", True),
        },
    )
    profile = store.get_game_profile(conn, 620)
    assert profile["name"] == "Portal 2"
    assert profile["has_achievements"] == 1
    assert bool(profile["in_outside_pool"]) is True
    # solid vs estimate is recorded per field
    assert profile["provenance"]["completability"]["is_estimate"] is False
    assert profile["provenance"]["hours_to_beat"] == {
        "source": "seed_playtime",
        "is_estimate": True,
    }


def test_playtimes_require_a_known_profile(conn):
    # Foreign keys are enforced: an orphan playtime is rejected, not silently
    # accepted, so the store can't hold playtimes for a profile that isn't there.
    with pytest.raises(sqlite3.IntegrityError):
        store.replace_seed_playtimes(conn, "no_such_player", {440: 10})
