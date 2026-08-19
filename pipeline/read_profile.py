"""Read one Steam profile and print what we got. A diagnostic, not a build step.

Run:  python -m pipeline.read_profile <steam_id>   [needs STEAM_API_KEY in .env]

It's the manual check behind Checkpoint 3: point it at a public account and see
the library size, the top games by playtime, and the achievement pulls for the
most-played handful; point it at a private one and see the honest can't-read
message instead of a crash. It reads and prints only — nothing is persisted.
"""

import asyncio
import sys

from b4cklog.config import steam_api_key
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
