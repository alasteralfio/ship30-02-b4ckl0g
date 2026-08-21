"""Goal 4.1 — fit and freeze the placement model on the seed.

Fits a StandardScaler and a Gaussian mixture on the seed's three axes, chooses
the component count by BIC weighed against the five working archetypes, maps
each component to the nearest archetype by where its mean sits, and freezes
the whole thing to `data/placement_model.pkl`. The live app only ever loads
this artifact — it never refits at request time (PROJECT.md, "Placement").

Run:  python -m pipeline.fit_placement
"""

import sys

from b4cklog import store
from b4cklog.placement import ARTIFACT_PATH, UNCLASSIFIED_PREFIX, fit, save


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    conn = store.connect()
    try:
        profiles = store.all_seed_profiles(conn)
    finally:
        conn.close()

    if not profiles:
        sys.exit("The seed is empty. Run `python -m pipeline.crawl_seed` first.")

    model = fit(profiles)
    save(model)

    chosen = model.mixture.n_components
    print(f"Fitted on {model.seed_size} seed profiles.\n")
    print("BIC by component count (lower is better):")
    for n, bic in sorted(model.bic_by_component_count.items()):
        flag = "  <- chosen" if n == chosen else ""
        print(f"  {n:>2}  {bic:12.1f}{flag}")

    if chosen != 5:
        print(
            f"\nBIC picked {chosen} components, not the five working archetypes — "
            "that's the emergent-taxonomy signal (PROJECT.md), not an error. "
            "See the mapping below for how the components read against the "
            "working archetype set."
        )

    print("\nComponent -> nearest archetype:")
    for component in range(chosen):
        print(f"  component {component}: {model.archetype_by_component[component]}")

    unclassified = [
        name for name in model.archetype_by_component.values()
        if name.startswith(UNCLASSIFIED_PREFIX)
    ]
    if unclassified:
        print(
            f"\n{len(unclassified)} component(s) sit too far from every archetype "
            "prototype to be named honestly — that's the emergent-taxonomy signal "
            f"(PROJECT.md), a real gap, not an error: {', '.join(unclassified)}"
        )

    unused = set(model.archetype_by_component.values()) - set(unclassified)
    missing = {"Completionist", "Dabbler", "Obsessive", "Enthusiast", "Curator"} - unused
    if missing:
        print(f"Archetypes with no nearest component: {', '.join(sorted(missing))}")

    print(f"\nSaved to {ARTIFACT_PATH}")


if __name__ == "__main__":
    main()
