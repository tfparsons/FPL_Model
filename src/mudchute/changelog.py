"""Model version + changelog.

BUMP THE VERSION whenever a change alters what the model recommends or how it
scores (xP engine, solver objective, thresholds, timing triggers) — reporting
and UI changes don't count. Two-part numbers: the first digit for major
reworks, the second for minor changes (1.4 -> 1.5, or 2.0 for an overhaul).
Every run is stamped with the version it ran
under, so the track record can attribute recommendation shifts to model
changes rather than data, and measure each version's impact once gameweeks
settle.
"""

MODEL_VERSION = "1.6"

# Newest first. "impact" = what the change was expected to do.
CHANGELOG = [
    {
        "version": "1.6",
        "date": "3 Sep 2026",
        "title": "Club-move rule",
        "detail": "Players whose evidence was earned at another club — "
                  "mid-season movers, summer signings, no-PL-record arrivals "
                  "— are rebuilt from a fresh prior: old-club starts don't "
                  "count, new-club games dominate fast, rates from elsewhere "
                  "are shrunk harder and the new club's attack uplift is "
                  "capped, until the club has played 4 games. Flagged in the "
                  "UI. The config overrides mechanic (lock/ban/force/no-hits) "
                  "is removed: adjustments belong in the model, not a text "
                  "file.",
        "impact": "A deadline-day signing starts as a genuine question mark "
                  "(~2.8 xP rather than 4.7) and two games settle it either "
                  "way, instead of a month of assumed starts.",
    },
    {
        "version": "1.5",
        "date": "1 Sep 2026",
        "title": "Adaptive robustness sampling",
        "detail": "The shake test starts at 25 perturbed solves and escalates "
                  "to 100 when conviction lands in the ambiguous 30-70% band, "
                  "the move gain sits within 1.0 of the bar, or the run is "
                  "inside the final 24h before the deadline.",
        "impact": "Conviction estimates carry ~\u00b110pt sampling noise at "
                  "25 runs and ~\u00b15pt at 100; extra precision is bought "
                  "only where it can change the read.",
    },
    {
        "version": "1.4",
        "date": "1 Sep 2026",
        "title": "Confirmed-transfer state",
        "detail": "Mid-window transfers ingested from declarations/API; runs "
                  "record the decision context (gain vs bar, price risk, "
                  "conviction, runner-ups, FTs in hand).",
        "impact": "Reporting and tracking only — no change to scoring.",
    },
    {
        "version": "1.3",
        "date": "31 Aug 2026",
        "title": "Early-transfer trigger",
        "detail": "Package feasibility vs price windows: act-early alert when "
                  "the plan is a tight fit, prices are moving against it, and "
                  "the fallback is clearly worse.",
        "impact": "Timing advice only — recommendations unchanged.",
    },
    {
        "version": "1.2",
        "date": "30 Aug 2026",
        "title": "Option-value calibration",
        "detail": "Simulated the value of banked FTs: terminal FT values, a "
                  "+1.75-per-FT bar every move must clear, churn penalty, and "
                  "a tax on planned future moves.",
        "impact": "The deterministic solver under-scored the option value of "
                  "holding; expect fewer, higher-conviction moves.",
    },
    {
        "version": "1.1",
        "date": "30 Aug 2026",
        "title": "Real game-state ledger",
        "detail": "Free transfers derived from the actual transfer history "
                  "instead of assumed; selling prices reconstructed.",
        "impact": "Recommendations use the true stock of free moves — this "
                  "run discovered 2 FTs in hand and first proposed a double.",
    },
    {
        "version": "1.0",
        "date": "29 Aug 2026",
        "title": "Launch model",
        "detail": "xP engine (minutes × per-90 rates × fixture context), "
                  "8-GW multi-period MILP solver, robustness shake test.",
        "impact": "Baseline.",
    },
]

VERSION_TITLES = {c["version"]: c["title"] for c in CHANGELOG}
