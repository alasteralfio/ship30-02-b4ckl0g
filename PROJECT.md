# B4CKL0G — Project Design

*A playstyle-based game recommender. Design and architecture document.*

---

## What it is

B4CKL0G takes a Steam ID, reads a player's library, playtime, and achievement data, and works out *how* that person plays rather than *what* they play. It groups players by behaviour, then recommends games based on what behaviourally similar players enjoy that you haven't touched yet.

The whole pipeline is unsupervised. Nobody hand-labels players as "completionists" or "dabblers" up front. The groupings come out of the data, and the labels we attach to them are descriptions of what emerged, not categories we forced the data into.

## The problem

Most game recommenders work off genre and tags. If you like RPGs, they show you more RPGs. If you played a roguelike, here's another roguelike. This misses the thing that actually predicts whether someone will enjoy a game: how they like to spend their time.

Take a concrete case. Someone who plays to finish things will happily sink sixty hours into Hollow Knight and then look for a three-hour puzzle game to clear before starting the next big one. A genre recommender sees "Metroidvania" and "puzzle" as unrelated and never connects them. What actually links those two games is the player, who curates a library around completion and mixes long and short titles on purpose. Genre tells you nothing about that. Playtime and completion behaviour tell you everything.

B4CKL0G is built around that gap. The premise is that playstyle is a stronger recommendation signal than genre, and that it's measurable from data Steam already exposes.

## Design principles

Two ideas run through every decision in this project.

**Honesty over comfort.** The system is built to describe players accurately, not to flatter them or to keep them engaged. If a recommendation comes with a catch, the catch gets stated. If the model isn't confident, it says so. A recommendation that makes someone happy but misleads them is a failure.

**Emergent Taxonomy.** The set of playstyles is discovered from behaviour and is allowed to grow. We start with a working set of archetypes, but they are our current best description of the data, not a fixed theory of how people play. If a real player doesn't fit any existing archetype, that isn't an error to smooth over. It's a signal that the taxonomy is incomplete and needs a new entry. The classification grows from the bottom up.

## How it works, conceptually

The pipeline has four stages, described here without the technical machinery.

**1. Read the player.** Pull the library, per-game playtime, and achievement data for a Steam ID.

**2. Reduce to behaviour.** Compress that raw data into a small number of behavioural signals that capture how someone plays. This is where genre and title fall away and only behaviour remains.

**3. Place the player.** Position them in a shared space alongside every other player the system has seen. Players who behave similarly land near each other. This space is continuous, so a player isn't forced into one bucket. They sit at a specific point that can be close to one cluster, between two, or off on their own.

**4. Recommend.** Match games to your playstyle two ways: by the demands a game makes of a player, and by what behaviourally similar players spent real time in. Surface the results, with context about how they'll fit your style. The two methods and how they combine are covered below.

## The behavioural axes

Steam gives us a limited but useful set of measurements: total playtime per game, how many owned games have actually been launched, how playtime is spread across a library, and achievement completion both per game and overall. From these, a few independent behavioural axes fall out. The archetypes are positions along these axes rather than things we invented separately.

**Depth versus breadth.** Do you pour hours into a handful of games or spread yourself across many? This is the cleanest signal we have and it separates players quickly.

**Completion drive.** Do you finish what you start and chase achievements, or do you move on once the novelty fades? Achievement rate relative to playtime is the main read here.

**Commitment consistency.** Is your playtime spread evenly, or is it dominated by a few enormous outliers surrounded by games you barely touched? A player with one 800-hour game and forty 2-hour trials behaves very differently from someone whose top games sit at forty hours each.

These three are enough to produce distinct, separable groupings. More signals may earn their place later, but starting lean keeps the archetypes defensible.

## The archetypes

Five archetypes make up the current working set. Each is defined by where it sits on the axes above, not by the games it contains.

### The Completionist
High depth, high achievement rate, consistent across chosen games. Finishes before moving on. The library may not be large, but what's in it is thoroughly played. Achievements aren't an afterthought, they're part of the point.

### The Dabbler
High breadth, low completion, a wide library of games with modest playtime each. Tries a lot, commits to little. The low achievement rate isn't disinterest. It's that they rarely stay in one game long enough to earn much.

### The Obsessive
Extreme concentration. One or a few games with enormous hour counts, the rest of the library largely untouched. Achievement rate inside the main game can be very high. These are often live-service or systems-heavy games built to absorb time indefinitely, though not always.

### The Enthusiast
Balanced across depth and breadth. Plays a healthy variety, finishes what resonates, doesn't fixate on any one thing. Statistically the most ordinary pattern, but still clearly distinct from the others, and worth naming precisely because it's easy to overlook.

### The Curator
High breadth with selective depth. Tries many games, but when one lands they commit fully. The giveaway is a split-shaped library: a lot of short-playtime games and a meaningful number of very high-playtime ones, with little in the middle.

The Enthusiast and the Curator are the two that genre systems handle worst, and they're the ones B4CKL0G exists to catch.

## Subdivisions

Archetypes aren't the finest level of description. Within several of them, players split into sub-types that share a signal shape but differ in what actually drives them. These matter because they change what a good recommendation looks like.

**The Completionist** splits along library shape:

- *Breadth Completionist.* Completes games across a wide range of lengths and genres, and deliberately uses short games to fill gaps between long ones. The library is varied but curated around finishing. Playtime is spread relatively evenly.
- *Depth Completionist.* Locks onto one game and doesn't leave until it's exhausted, then moves to the next. Fewer games, longer sessions, content-rich titles. The playtime curve is a series of deep wells rather than an even spread.

Both show high completion and high achievement rates, so the axes alone read them as the same archetype. The library shape is what separates them.

**The Obsessive** splits along motivation:

- *Live-Service Obsessive.* Held by systems, seasons, and daily loops. The game is a routine.
- *Passion Obsessive.* Thousands of hours in something like Stardew Valley or Dwarf Fortress because the game is a world unto itself. The game is a home.

Same shape on the axes, genuinely different psychology, and therefore different games worth recommending.

The point of naming subdivisions is that B4CKL0G can tune recommendations below the archetype level. Two players can both read as Completionists and still get meaningfully different suggestions because one fills gaps with short games and the other wants the next deep well.

## How players are placed and labelled

The placement space is continuous. Nobody is snapped to the nearest archetype. This is a deliberate choice, and it matters for two reasons.

First, nobody is a pure type. A player might read as sixty percent Completionist and forty percent Dabbler. Forcing them into one bucket throws away exactly the nuance the project claims to capture. Keeping the space continuous lets recommendations reflect where a player actually sits.

Second, it keeps the system stable as it grows. As more players are added, the clusters shift. A player pinned to a hard cluster would get jarring reclassifications when the boundaries move. A player who simply sits at a point in a continuous space drifts gently instead.

The cost is that a continuous position is hard to explain to a person. "You sit at these coordinates in a behavioural space" means nothing to a user. So the continuous logic stays under the hood and does the real work, while the interface shows the nearest archetype as a soft, plain-language label with its approximation made explicit. Something like:

> You play most like a **Completionist**, with strong **Dabbler** tendencies.

The label serves the reader. The position serves the recommendation.

## How recommendations are generated

B4CKL0G runs on two engines drawing on two different kinds of data.

Player data describes you. Your own library, playtime, and achievements are enough to place you. Working out that you're a Breadth Completionist doesn't need a single other person in the system. You're fully describable from your own account.

Game data describes what a game asks of a player. Independently of anyone playing it, a game can be profiled by its own properties: how long it runs, whether it ends, how achievable full completion is, what kind of effort it rewards. From that you can say what sort of player a game suits.

Those two sources feed two ways of recommending.

**Game-property matching** takes your playstyle, computed from your own library, and finds games whose demands fit it. A short game with a clean, achievable completion suits a Breadth Completionist. An endless, systems-heavy game with grind-gated achievements suits an Obsessive. A long RPG that most players never finish suits an Enthusiast who plays for the time spent rather than the credits screen. This engine works from the first player onward. It needs nobody else in the system.

**Player-similarity matching** finds games that behaviourally similar people actually spent real time in, including ones that game properties alone would never surface. This engine only means anything once enough similar players exist, and it strengthens as the player base grows.

The two are blended gradually and weighted by proximity. There's no threshold where one switches off and the other switches on. The more genuinely similar players sit near you, the more their behaviour is trusted; the fewer there are, the more the recommendation leans on game properties. The blend shifts smoothly as the data around a given player fills in.

A note on scale, in keeping with the honesty principle. This is a portfolio project and won't draw a large live audience, so player-similarity can't wait on visitors to supply the crowd — few will ever arrive. It leans instead on a seeded reference population: a batch of public profiles pulled once and kept as the space every visitor is compared against (see The first version). That makes the second engine real from the start without pretending a live crowd exists. Game-property matching still does most of the work, and it should: the playstyle placement is the real idea, and matching games to how a person plays holds up whether or not anyone else is in the system. Player-similarity sharpens that result; it isn't the load-bearing wall.

There's also a quiet learning loop between the two. Game-property profiles start as reasoned estimates from a game's metadata. Real player behaviour corrects them over time. If a game we profiled as suiting Obsessives turns out to be loved by Curators, the profile updates to match what actually happened.

### What we profile a game on

These are the properties worth capturing to give a game a playstyle profile. Where they come from technically is for a later stage to solve. What matters here is that they map a game onto the same axes we place players on.

- **Length.** Rough time to reach the end, and rough time to fully complete. Separates games that suit breadth players from ones that suit depth players.
- **Completability.** Achievement count and how achievable full completion is, including whether achievements are missable or grind-gated. The core signal for how a game serves the completion drive.
- **Finite or endless.** Does the game have an ending, or is it a loop with no natural stopping point? The clearest divider between games that suit Obsessives and games that suit finishers.
- **Progression style.** Does the game reward grinding, skill and mastery, or exploration and narrative? Shapes which archetype and subdivision a game speaks to.
- **Content shape.** Is the game focused and self-contained, or a sprawl that expects a long investment? Feeds both the length and consistency reads.

## Recommendations and the backlog

B4CKL0G recommends in two directions, and keeps them clearly separated because they mean different things to the player.

**From your own library.** Games you already own but haven't played, ranked by how well they fit your style and how much behaviourally similar players got out of them. This is the backlog the project is named for. The most satisfying recommendation is often a game already sitting unplayed in someone's account.

**From outside your library.** Games you don't own that similar players engaged with. This is where the playstyle mechanic earns its keep. The pitch isn't "people who like this genre also bought" but "people who play the way you play spent real time in this and enjoyed it."

Every recommendation carries context rather than standing alone. Because the project values enjoyment over completion, and because it's honest about fit, a recommendation to a Dabbler for a very long game says so directly:

> Players like you loved this one. Heads up: it's around eighty hours to finish, and players in your cluster typically play about twelve before moving on. Worth it for the time you'll spend, not for finishing it.

That turns a recommendation into something closer to informed consent. The player decides with the tradeoff in front of them instead of discovering it forty hours in.

## What the data can't tell us

Being honest about the model means being honest about its blind spots. These are known and accepted rather than hidden.

Playtime doesn't distinguish engagement from an idle game left running overnight. Achievement data is heavily game-dependent: some games have five achievements, some have two hundred, some hide grind-heavy or missable ones that distort completion rates. Owning a game and having launched it once look similar in the data even though they mean different things.

None of these are cleanly solvable without over-engineering the first version, and chasing them would trade a working system for a marginally cleaner one. B4CKL0G treats them as acceptable noise and states plainly that its read on a player is an informed estimate from behavioural traces, not a verdict. That honesty is part of the design, not a disclaimer bolted on afterward.

## The first version

The first version commits to the whole idea, not a safe slice of it. The playstyle read, the backlog recommendations, the outside-library recommendations, both engines, and the learning loop all ship together, because each one is thinner without the others. Below are the concrete choices that version makes.

**A seeded crowd, kept separate from its visitors.** Player-similarity has nothing to compare against until a population exists, and a portfolio project won't grow one from traffic. So the population is seeded once: a batch of public Steam profiles, pulled up front and held as a fixed behavioural space. Every visitor is placed against that space and then let go. Nobody looking themselves up needs an account, and nothing about them is stored — the read happens in the session and ends with it. The crowd is the seed; the visitor is a guest.

That split has a consequence worth stating. The learning loop that corrects game profiles from real behaviour runs over the seed and periodic re-crawls of it, not over live visitors, because there's no stored visitor to learn from. It's an offline process, not a feedback effect of using the site.

**The seed is chosen for spread, not convenience.** The emergent-taxonomy claim only holds if the seeded population actually contains the range of playstyles it's meant to discover. A sample scraped from one friend network or one subreddit would cluster around that group's habits and quietly invent archetypes that are really just artifacts of who got sampled. So the seed is sourced deliberately across library sizes, playtimes, and completion behaviour, giving all five archetypes a genuine chance to fall out of the data rather than being coaxed into it. This is the part most likely to undermine the project if done lazily, and it gets treated that way. It also means collecting a set of public profiles into a stored dataset — public data at small scale, but a real data-collection decision made on purpose rather than stumbled into.

**Recommendations draw from the player's library plus a modest pool.** The backlog direction profiles the visitor's own owned-but-unplayed games in full — that's the recommendation the project is named for and it deserves the most care. The outside-library direction draws from a moderate curated pool rather than the whole Steam catalog. Covering everything on Steam would spread the profiling thin and add noise on the long tail for little gain at this scale; a smaller, well-profiled pool makes for better reads.

**The output is a written read with one picture.** A visitor gets an honest prose description — their nearest archetype, the subdivision that sharpens it, and the soft label that admits its own approximation — followed by the two recommendation lists, kept clearly apart, each carrying the context a given game deserves. Beside it sits a single visual showing roughly where they land relative to the archetypes. The continuous coordinates stay under the hood, as described earlier; the picture gives a feel for placement without asking anyone to read a set of axes.

## How it's built

Two programs, not one. An offline pipeline builds the reference data — it crawls the seed, computes everyone's behaviour, fits the placement space, and profiles games — and writes the result to disk. A live web app reads that result and never writes back to it. The split falls straight out of the fixed-reference-crowd decision: the crowd is built once, ahead of time, and a visitor's request only reads it. Keeping the two apart also means a slow crawl or a model refit can never block or corrupt a live read.

**The stack.** Python throughout. The core of this project is behavioural math and clustering, which is Python's home ground — numpy, pandas, and scikit-learn do the reducing, the placement, and the nearest-neighbour search without anything exotic. The web layer is deliberately thin: FastAPI serving server-rendered pages through Jinja templates, because the output is an honest report with one picture, not an interactive application. There's no JavaScript framework and no build step, and the placement visual is emitted as inline SVG straight from Python. Storage is SQLite. The reference data is small — a few thousand seed profiles and a similar pool of games — read constantly and written only by the offline pipeline, which is the shape SQLite is best at. Postgres here would be ceremony. One language spans the pipeline and the site, and the dependency list stays short enough to defend line by line.

**Reading a player, reducing to behaviour.** A request starts with a Steam ID and a handful of calls to the Steam Web API: the owned games and playtimes, the profile's visibility, and per-game achievements. Visibility comes first, because a private profile is a real and common case and the system says so plainly rather than guessing around it. Per-game achievement data is the awkward part. Steam serves it one game at a time, so a large library would mean hundreds of calls, and we don't make them — achievements get pulled only for a player's most-played games, which are the ones carrying the completion-drive signal anyway. The rest contribute nothing a hundred extra requests would recover. From this raw data the three axes are computed directly. They're engineered measures with fixed definitions, not anything learned, so the same player always reduces to the same point.

**Placement.** The visitor's three measures are standardized against the seed population's distribution, which is why the seed has to be fixed: a z-score only means something against a stable reference. Placement uses a Gaussian mixture fitted on the seed. It gives soft membership across components instead of a hard assignment, which is exactly the "sixty percent Completionist, forty percent Dabbler" reading the design calls for — the continuous position is the model's native output and the soft label is read off it. Subdivisions split on library shape, the bimodal playtime curve that separates a Breadth Completionist from a Depth one, computed as extra features beyond the three axes. The fit is frozen to an artifact and loaded at startup; it is never refit on the fly. That's what stops a returning Steam ID from getting a jarringly different label because the model moved under it.

**Profiling games.** Some of a game's profile comes from solid data and some from estimate, and the two aren't dressed up as equal. Achievement counts and Steam's global completion percentages are real numbers, and so is owner playtime; those anchor completability and rough length. Whether a game is finite or endless, and how long it truly takes to finish, are softer — inferred from tags and owner-playtime shape, or from an unofficial time-to-beat source, and flagged internally as estimates. This is what the learning loop is for: profiles begin as reasoned guesses and get corrected, offline, as the seed's real behaviour contradicts them. That correction runs over the seed and periodic re-crawls, never over live visitors, because there's no stored visitor to learn from.

**Recommending.** Each candidate game gets two scores. The game-property score measures how well the game's demands fit the visitor's placement. The player-similarity score aggregates how much the visitor's nearest seed neighbours actually invested in that game, weighted by how near they sit. The two blend by proximity: land in a crowded part of the seed and the neighbours are trusted more; land alone and the read leans on game properties. Candidates split by ownership into the two directions the design keeps apart — owned-but-unplayed for the backlog, the curated outside pool for discovery — and every surfaced game carries the fit and the caveat that go with it. How fast the blend should tip toward similarity as neighbours appear is a tuning question, left open below.

**The bets, and where this can break.** Three things carry real risk and are worth naming before a line is written. The Steam Web API is the whole front door: it requires public profiles, rate-limits, and serves achievements one game at a time — the sampling above answers the last of those, but the endpoints and their exact fields need verifying against the live API before the architecture leans on them. Length and finite-versus-endless rest partly on an unofficial source or a proxy and are the softest profiles we produce; the honest move is to mark them as estimates and let the learning loop pull them toward truth. And the seed is the linchpin — get its diversity wrong and every cluster downstream inherits the bias, so sourcing it well matters more than any modelling choice made afterward.

## Open questions

Some resolved, some still open. Kept here so nothing gets lost.

**Resolved**

- **Corpus dependence.** Handled by the two-engine design. Game-property matching gives a player real recommendations even when no one else is in the system, so B4CKL0G doesn't depend on a crowd to function.
- **Cold start.** A player with a thin or brand-new library gives weak signal. B4CKL0G does the best it can with what's there and says openly when it's working from little. There isn't much more to do about this, and pretending otherwise would break the honesty principle.
- **Subdivision surfacing.** The player sees all of it, or close to it, presented so it informs rather than overwhelms. The subdivisions are much of what makes the read feel accurate, so hiding them would waste the most interesting output.

**Still open**

- **Private profiles.** Steam profiles and playtime can be hidden. When B4CKL0G can't read an account, it says so plainly. A small visual guide on making a profile public would help, though whether it makes the first version is undecided.
- **Blend tuning.** The gradual, proximity-weighted blend has the right shape, but how quickly it should shift toward player-similarity as similar players appear is a judgment call to settle later.