"""Read one Steam profile and print what we got. A diagnostic, not a build step.

Run:  python -m pipeline.read_profile <steam_id>   [needs STEAM_API_KEY in .env]

It's the manual check behind Checkpoint 3: point it at a public account and see
the library size, the top games by playtime, and the achievement pulls for the
most-played handful; point it at a private one and see the honest can't-read
message instead of a crash. It's also the check behind Checkpoint 6: once
`data/placement_model.pkl` exists (`python -m pipeline.fit_placement`), it
places the profile too and prints the soft label and subdivision. It reads and
prints only — nothing is persisted.
"""

import asyncio
import sys

from b4cklog.behaviour import reduce_to_behaviour
from b4cklog.config import steam_api_key
from b4cklog.placement import load, place
from b4cklog.steam import Library, PrivateProfile, SteamClient, UnknownSteamID


async def _read(steam_id: str) -> None:
    async with SteamClient(steam_api_key()) as client:
        result = await client.read_library(steam_id)

        if isinstance(result, PrivateProfile):
            who = result.persona_name or steam_id
            print(f"Can't read {who}: {result.reason.value}.")
            print("This profile (or its game details) isn't public — nothing to guess around.")
            return

        assert isinstance(result, Library)
        who = result.persona_name or steam_id
        print(f"{who} — {len(result.games)} games owned\n")

        top = result.most_played(10)
        print("Top games by playtime:")
        for game in top:
            print(f"  {game.playtime_minutes / 60:7.1f}h  {game.name}")

        print("\nAchievement pulls for the most-played games:")
        stats = await client.sample_achievements(result)
        by_playtime = {g.app_id: g.name for g in result.most_played()}
        for app_id, s in sorted(stats.items(), key=lambda kv: -kv[1].completion_rate):
            name = by_playtime.get(app_id, str(app_id))
            print(f"  {s.completion_rate:5.0%}  {s.unlocked}/{s.total}  {name}")

        behaviour = reduce_to_behaviour(result, stats)
        print("\nBehaviour (the three axes):")
        print(f"  depth <-> breadth       {behaviour.depth_breadth:+.2f}   (higher = broader)")
        print(f"  completion drive        {behaviour.completion_drive:.0%}")
        print(f"  commitment consistency  {behaviour.commitment_consistency:.0%}   (higher = more even)")
        shape = behaviour.shape
        print(
            f"  library shape: {shape['games_played']}/{shape['games_owned']} played"
            f" ({shape['played_fraction']:.0%}), bimodality {shape['bimodality']:.2f}"
        )

        try:
            model = load()
        except FileNotFoundError as e:
            print(f"\n{e}")
            return

        placement = place(model, behaviour)
        print(f"\nPlacement: {placement.soft_label}")
        print("  membership:", ", ".join(
            f"{name} {prob:.0%}"
            for name, prob in sorted(placement.responsibilities.items(), key=lambda kv: -kv[1])
        ))
        if placement.subdivision is not None:
            sub = placement.subdivision
            hedge = "" if sub.confident else "  (not confident yet)"
            print(f"  subdivision: {sub.name}{hedge} — {sub.detail}")


def main() -> None:
    # Game names carry ® / ™ and non-Latin text; force UTF-8 so the Windows
    # console (cp1252 by default) prints them instead of mangling to '?'.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) != 2:
        sys.exit("usage: python -m pipeline.read_profile <steam_id>")
    try:
        asyncio.run(_read(sys.argv[1]))
    except UnknownSteamID as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
