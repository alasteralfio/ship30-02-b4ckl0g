"""The curated outside pool (Goal 5.1) — a moderate, hand-picked set of games
for the discovery direction, kept separate from "every game in the seed
libraries" (PROJECT.md, "Recommendations draw from the player's library plus a
modest pool").

Why curated rather than sampled: the seed's diversity comes from *not*
coaxing it (SEED.md) — stratifying by observable shape, never by taste. Games
are the opposite case. A player-similarity engine can only ever recommend what
the seed itself owns, and Phase 4 already found real gaps there (some
archetype-shaped players are thin or absent in the seed). This pool exists so
game-property matching — the engine PROJECT.md says "should" carry the real
weight — has real candidates to offer even where the seed has little to say.
Hand-picking is the honest name for what's happening: this is a deliberate
editorial choice of well-known titles spread across the demand axes (short vs.
long, finite vs. endless, easy-complete vs. grindy, narrative vs. mechanical),
not a discovered or unbiased sample.

Every ID below was verified live against Store `appdetails` before being kept
(a wrong or delisted ID is worse than a smaller pool) — see
`pipeline.profile_games`'s skip log for anything that stops resolving later.
"""

OUTSIDE_POOL_APP_IDS: tuple[int, ...] = (
    # Short, cleanly completable — the Breadth Completionist / gap-filler shape.
    400,      # Portal
    620,      # Portal 2
    504230,   # Celeste
    391540,   # Undertale
    304430,   # INSIDE
    221910,   # The Stanley Parable
    239030,   # Papers, Please
    501300,   # What Remains of Edith Finch
    383870,   # Firewatch
    753640,   # Outer Wilds
    653530,   # Return of the Obra Dinn
    837470,   # Untitled Goose Game
    1055540,  # A Short Hike
    1049410,  # Superliminal
    240720,   # Getting Over It with Bennett Foddy
    # Long, sprawling RPGs — the Enthusiast / long-haul shape few ever finish.
    1245620,  # Elden Ring
    1086940,  # Baldur's Gate 3
    292030,   # The Witcher 3: Wild Hunt
    489830,   # The Elder Scrolls V: Skyrim Special Edition
    1091500,  # Cyberpunk 2077
    1850570,  # Death Stranding (Director's Cut)
    264710,   # Subnautica
    275850,   # No Man's Sky
    294100,   # RimWorld
    1426210,  # It Takes Two
    632470,   # Disco Elysium (The Final Cut)
    # Finite-but-deep roguelikes/systems games — the Curator shape.
    1145360,  # Hades
    646570,   # Slay the Spire
    632360,   # Risk of Rain 2
    975370,   # Dwarf Fortress
    322330,   # Don't Starve Together
    # Endless / live-service — the Obsessive shape, both flavours PROJECT.md names.
    440,      # Team Fortress 2 (live-service)
    730,      # Counter-Strike 2 (live-service)
    238960,   # Path of Exile (live-service)
    1085660,  # Destiny 2 (live-service)
    1172470,  # Apex Legends (live-service)
    413150,   # Stardew Valley (passion / world-unto-itself)
    105600,   # Terraria (passion / world-unto-itself)
)
