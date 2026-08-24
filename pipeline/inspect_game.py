"""Look up one or more profiled games and print what we got. A diagnostic,
not a build step — the manual check behind Checkpoint 7.

Run:  python -m pipeline.inspect_game <app_id> [<app_id> ...]
      python -m pipeline.inspect_game --search "elden ring"

Point it at a few games you know well: does the length and finite read pass
the smell test, and is every soft field visibly marked as an estimate rather
than stated as fact (PROJECT.md, "Honesty over comfort")?
"""

import argparse
import sys

from b4cklog import store


def _render(profile: dict) -> str:
    lines = [f"{profile['name']}  (app {profile['app_id']})"]

    def field(label: str, key: str, fmt=str) -> str:
        value = profile[key]
        if value is None:
            return f"  {label}: unknown"
        prov = profile["provenance"].get(key)
        tag = "" if prov is None else (
            f"  [estimate, source: {prov['source']}]" if prov["is_estimate"]
            else f"  [solid, source: {prov['source']}]"
        )
        return f"  {label}: {fmt(value)}{tag}"

    if not profile["has_achievements"]:
        lines.append("  achievements: none")
    else:
        lines.append(f"  achievements: {profile['achievement_count']}")
        lines.append(field(
            "completability (avg. % of owners who unlock each achievement)",
            "completability", lambda v: f"{v:.0%}",
        ))

    # hours_to_beat/hours_to_complete are the same underlying numbers (seed
    # playtime percentiles) whether or not the game is finite, but what they
    # *mean* changes: for a finite game they're a rough completion estimate;
    # for an endless one there's no "beating" it, so they're relabeled as
    # typical engagement instead of dressed up as a length that doesn't exist.
    if profile["is_finite"] is False:
        lines.append(field("typical playtime (seed median — no fixed length, game is endless)", "hours_to_beat", lambda v: f"{v:.1f}h"))
        lines.append(field("heavier engagement (seed 75th pct)", "hours_to_complete", lambda v: f"{v:.1f}h"))
    else:
        lines.append(field("hours to beat (seed median)", "hours_to_beat", lambda v: f"{v:.1f}h"))
        lines.append(field("hours to complete (seed 75th pct)", "hours_to_complete", lambda v: f"{v:.1f}h"))
    lines.append(field("finite", "is_finite", lambda v: "yes" if v else "no"))
    lines.append(field("progression style", "progression_style"))
    lines.append(field("content shape", "content_shape"))
    lines.append(f"  in curated outside pool: {'yes' if profile['in_outside_pool'] else 'no'}")
    return "\n".join(lines)


def _search(conn, term: str) -> list[dict]:
    rows = conn.execute(
        "SELECT app_id FROM game_profiles WHERE name LIKE ? ORDER BY name LIMIT 20",
        (f"%{term}%",),
    ).fetchall()
    return [store.get_game_profile(conn, r["app_id"]) for r in rows]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Inspect one or more profiled games.")
    parser.add_argument("app_ids", nargs="*", type=int, help="app IDs to look up")
    parser.add_argument("--search", help="find games by name instead of app ID")
    args = parser.parse_args()

    if not args.app_ids and not args.search:
        sys.exit("usage: python -m pipeline.inspect_game <app_id> [...] | --search <name>")

    conn = store.connect()
    try:
        profiles = []
        if args.search:
            profiles = _search(conn, args.search)
            if not profiles:
                print(f"No profiled game matches {args.search!r}.")
        for app_id in args.app_ids:
            profile = store.get_game_profile(conn, app_id)
            if profile is None:
                print(f"App {app_id} isn't profiled (not in scope, or delisted/non-game — see game_profile_skips.jsonl).")
                continue
            profiles.append(profile)

        print("\n\n".join(_render(p) for p in profiles))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
