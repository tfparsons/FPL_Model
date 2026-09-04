# Backlog

Candidate improvements, ordered by expected value. Accuracy items rank by
where the backtest says the model actually loses points (VALIDATION.md):
the minutes side dominates, the fixture side is secondary.

## Accuracy

1. **Bookmaker odds for the fixture side.** Not scraping (Oddschecker is
   JavaScript-heavy, anti-bot, and its terms forbid it — a maintenance tax
   on a self-running system). Source: The Odds API (free tier 500
   requests/month; ~10/week needed) for Premier League 1X2 and over/under
   from a dozen bookmakers. Method: solve for the Poisson goal rates that
   reproduce each fixture's implied win/draw/loss and total-goals
   probabilities, use them as fixture lambdas for the next 1–2 gameweeks,
   blend back to the in-house team-strength model beyond the odds horizon.
   Payoff: sharper clean-sheet odds and attack multipliers, and bookies price
   team news within hours, so the fixture side reacts to lineups the model
   can't see. Player props (anytime scorer, clean sheet) aren't on free
   tiers — match odds first. Calibrate/backtest first against
   Football-Data.co.uk's free historical odds CSVs. Needs an API key as a
   GitHub Actions secret.
2. **Captaincy variance** — mean xP undervalues a premium captain's ceiling;
   a distributional view (P(haul)) for the captain pick specifically.
3. **OpenFPL projections** as a drop-in for the xP matrix — the one-file
   contract makes it a swap, and a useful benchmark against the in-house
   engine.

## Evaluation

4. **Full-season solver simulation** — roll the planner through last season
   GW by GW against the actual team to measure the decision layer's value
   end to end, not just the model's ranking accuracy.

## Product

5. **Active-adjustments panel** — a read-only chip on the Weekly page
   listing what the model is currently adjusting for (club moves, returns,
   flags), so a surprising number is never a mystery.

## Done

- Knock-on minutes (v1.9) — slot model: formation shape per club,
  vacancies pass to fit teammates (learned pairings when available), spill
  across positions, squeeze on oversubscription; 'covering for X' flag.

- Recency-window minutes (v1.7) — per-player game logs, last-6 window
  blended 70/30 with season rate; return-from-absence window.
- Club-move rule (v1.6) — movers and arrivals rebuilt from fresh priors.
