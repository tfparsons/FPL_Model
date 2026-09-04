# Backlog

Candidate improvements, ordered by expected value. Accuracy items rank by
where the backtest says the model actually loses points (VALIDATION.md);
the minutes side dominates, the fixture side is secondary.

## Accuracy

1. **Recency-window minutes** — replace season-to-date starts with a
   last-6-games window from per-player game history (`element-summary`),
   which also detects club moves precisely (team per game). The single
   highest-value upgrade per the backtest: the naive "recent points"
   baseline out-ranks the model across all players because recency encodes
   who is fit and starting. Cost: ~150 API calls per run for the solver
   pool, cached per gameweek.
2. **Bookmaker odds** — clean-sheet and goal odds beat any team-strength
   model for the fixture side; anytime-scorer odds would also inform the
   premium-captain ceiling. Needs a paid or scraped odds source.
3. **Captaincy variance** — mean xP undervalues a premium captain's ceiling;
   a distributional view (P(haul)) for the captain pick specifically.
4. **OpenFPL projections** as a drop-in for the xP matrix — the one-file
   contract makes it a swap, and a useful benchmark against the in-house
   engine.

## Evaluation

5. **Full-season solver simulation** — roll the planner through last season
   GW by GW against the actual team to measure the decision layer's value
   end to end, not just the model's ranking accuracy.

## Product

6. **Active-adjustments panel** — a read-only chip on the Weekly page
   listing what the model is currently adjusting for (club moves, flags),
   so a surprising number is never a mystery.
