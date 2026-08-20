# Sourcing the seed

The seed is the reference crowd every visitor is placed against. Get its makeup
wrong and every cluster downstream inherits the mistake, so this is the part of
the build that gets the most care and the most suspicion. This file records how
IDs are chosen, the one judgement call at the heart of it, and the biases the
method still carries. The code is `pipeline/source_seed.py`; this is the why.

## Where the IDs come from

A SteamID64 for an individual account is a fixed base plus an account number.
The sourcer draws account numbers uniformly at random and asks Steam who they
belong to. Most draws are nothing — an unused number, a private profile, an
empty library — and that's fine: `GetPlayerSummaries` resolves 100 candidates in
a single call, so rejecting the dead majority is nearly free. The public
profiles that survive get one owned-games call each and go into the seed.

Random draw is the whole point. The obvious shortcut — walk a friend graph, or
pull a Steam group — is exactly the trap PROJECT.md names: a crowd sampled from
one social circle shares that circle's habits, and the model would then
"discover" archetypes that are really just that circle showing up in the data.
A friends list is the single worst source here, however convenient. So we don't
use one, not even as a supplement to the reference crowd.

## The one judgement call: spread without coaxing

Uniform random sampling is unbiased, but the population it samples is not evenly
interesting. Steam is full of tiny, barely-touched accounts. Take the raw draw
and the seed is a sea of Dabbler-shaped libraries with almost nothing in the
Obsessive or Completionist regions — and a model fitted on that would have no
Obsessive cluster to find, because we never fed it one.

So candidates are stratified. Each public profile is sorted into one of six
strata by the shape of its library — how many games it owns, how many it has
actually played, how many hours in total — and each stratum has a *cap* on how
much of the seed it may hold. The run collects until the seed is full, and no
common shape is allowed past its cap along the way, so the result spreads across
the range instead of piling up wherever the population is densest.

Caps, not quotas, and that distinction is deliberate. An earlier design made each
stratum fill to a fixed floor before stopping, which sounds stronger but isn't:
the large-library shapes are genuinely rare among *public* profiles, so waiting
for a fixed number of them ran for hours and sometimes never finished. Caps keep
the common shapes from dominating without holding the whole run hostage to the
rarest one. The rare shapes get their honest natural share; whether that share is
too thin is exactly what the diversity summary is there to judge (see below).

Here is the line, and it matters: **we stratify on observable library shape, not
on the archetypes and not on completion behaviour.** Owning 400 games or
sinking 2,000 hours into three of them is a plain, measurable fact about a
library. Whether that person is a "Curator" is a conclusion the placement model
reaches on its own, later, from the three axes. We make sure a spread of library
*shapes* is present; we never reach in and set how many Completionists the seed
should contain. The first is ensuring coverage. The second would be manufacturing
the result, and it would make the emergent-taxonomy claim a lie. The strata are
named after the archetype each shape tends to produce (see `_BIN_WEIGHTS` in the
code), but the name is a note on intent, not a label written into any profile.

Completion drive — the third behavioural axis — sits outside the sourcing filter
entirely, because reading it means pulling achievements, which is the crawl's
expensive job, not the sourcer's. Spread along that axis is left to whatever
correlates with library shape plus the luck of the draw. That's a real gap, and
it's why the actual diversity check happens after the crawl, on the true axes,
not here on the proxy.

## What this still gets wrong

None of these are fixed. They're stated so the next person knows what they're
standing on.

- **The proxy is not the axes.** The six strata are cut on heuristic thresholds
  over library shape — reasonable guesses about where an obsessive library stops
  and a deep-completionist one starts. If a threshold sits in the wrong place,
  the seed is evenly spread across the wrong grid. The honest check is the
  post-crawl diversity summary computed on the real three axes (Checkpoint 5),
  which can catch a region the proxy declared full but the axes show thin.

- **Public profiles are a self-selected group.** We can only read public
  accounts, and people who make their profile and playtime public are not a
  random slice of players — they skew more engaged, more social, more invested.
  Whatever a fully private player's playstyle tends to be, it is absent by
  construction, and no amount of sampling fixes that.

- **The draw favours older accounts.** The account-number ceiling is set below
  the true maximum on purpose, to avoid wasting most draws on unallocated
  numbers. The cost is that the newest accounts are under-sampled, tilting the
  seed slightly toward longer-tenured players with larger libraries. Raising the
  ceiling trades hit rate for a fairer age spread.

- **The proportions are a choice.** The stratum weights decide how much of each
  shape the seed holds. That is a designed distribution, not the natural one —
  we are deciding, for instance, that thin accounts shouldn't dominate even
  though in the wild they do. This is defensible: an even spread is what lets the
  model see every region. But it means the seed's makeup is partly our decision,
  and that should be remembered whenever the cluster sizes are read as if they
  described the real Steam population. They don't. They describe the seed we
  built.

## How it's verified

Sourcing produces a list of IDs, nothing more. Whether the seed is genuinely
diverse is judged after the crawl, by the diversity summary over the three axes,
and confirmed by eye — the most important human check in the project (Checkpoint
5). If that summary shows a clump, the fix lives here: adjust the strata, the
thresholds, or the ceiling, and source again.
