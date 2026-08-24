"""Read a Steam profile, place it, and print recommendations. A diagnostic,
not a build step — mirrors `read_profile.py` but carries the pipeline the rest
of the way through Phase 6.

Run:  python -m pipeline.recommend_profile <steam_id>   [needs STEAM_API_KEY in .env]

It's the manual check behind Checkpoint 8: point it at your own Steam ID and
see the backlog list (owned, unplayed) and the outside-pool list (not owned),
each ranked and carrying its game-property / player-similarity split and any
honest catch. It reads and prints only — nothing is persisted.
"""

import asyncio
import sys

from b4cklog import store
from b4cklog.behaviour import reduce_to_behaviour
from b4cklog.config import steam_api_key
from b4cklog.placement import load, place
from b4cklog.recommend import recommend
from b4cklog.steam import Library, PrivateProfile, SteamClient, UnknownSteamID


def _print_list(title: str, recommendations, note: str | None) -> None:
    print(f"\n{title}:")
    if note:
        print(f"  {note}")
    for rec in recommendations[:10]:
        line = f"  {rec.score:.0%}  {rec.name or rec.app_id}"
        line += f"  (property {rec.property_score:.0%}, similarity {rec.similarity_score:.0%}"
        if rec.property_coverage < 1.0:
            line += f", {rec.property_coverage:.0%} real data"
        line += ")"
        print(line)
        if rec.caveat:
            print(f"      {rec.caveat}")


async def _recommend(steam_id: str) -> None:
    async with SteamClient(steam_api_key()) as client:
        result = await client.read_library(steam_id)

        if isinstance(result, PrivateProfile):
            who = result.persona_name or steam_id
            print(f"Can't read {who}: {result.reason.value}.")
            return

        assert isinstance(result, Library)
        who = result.persona_name or steam_id
        print(f"{who} — {len(result.games)} games owned")

        stats = await client.sample_achievements(result)
        behaviour = reduce_to_behaviour(result, stats)

        try:
            model = load()
        except FileNotFoundError as e:
            sys.exit(str(e))

        placement = place(model, behaviour)
        print(f"\nPlacement: {placement.soft_label}")
        if placement.subdivision is not None:
            print(f"  subdivision: {placement.subdivision.name}")

        conn = store.connect()
        try:
            rec_set = recommend(conn, model, placement, result)
        finally:
            conn.close()

        print(
            f"\nBlend: {rec_set.blend_weight:.0%} similarity-weighted "
            f"({rec_set.neighbor_count} neighbors found nearby)"
        )
        _print_list("Backlog (owned, unplayed)", rec_set.backlog, rec_set.backlog_note)
        _print_list("Outside pool (not owned)", rec_set.outside, None)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2:
        sys.exit("usage: python -m pipeline.recommend_profile <steam_id>")
    try:
        asyncio.run(_recommend(sys.argv[1]))
    except UnknownSteamID as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
