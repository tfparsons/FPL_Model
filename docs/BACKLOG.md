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
2. **Knock-on minutes: a slot model, not a replacement multiplier.**
   Minutes belong to a club's *slots*, not to players. Design:
   - *Formation shape*: expected starters per position group per club, from
     the game logs (recency-weighted) — e.g. Liverpool start ~0.8–1.0 FWD.
   - *Slot balance*: for each club/position, the sum of candidates'
     standalone P(start) should equal the slots in expectation. When a
     regular's availability drops (injury/suspension/unknown return), the
     shortfall is redistributed to fit candidates in proportion to their
     standalone P(start) (next in line gets most), capped at 0.97. When the
     sum exceeds the slots (a returner creates a squeeze), scale down —
     symmetric, so it self-corrects when the absentee is back.
   - *Cross-position spill* (the Haaland case): if a group has fewer fit
     candidates than slots, the unfilled slot spills to the adjacent group
     (FWD -> MID), raising midfielders' P(start). Beneficiaries keep their
     own per-90 rates — a false nine doesn't inherit the striker's xG.
   - *Learned pairings* (refinement): from the logs, who actually started in
     the games X missed (last season: in the 30 games Isak didn't start,
     Ekitike started 21 of them; in the 8 Isak did, he was the lone FWD). Use
     co-absence weights instead of proportional split once >=5 absence games
     exist for that club.
   - *Guards*: only redistribute for known absences (status i/s/u or chance
     <= 25%); doubtful flags are already multiplied in. Flag beneficiaries
     orange as "covering for X" with the absentee named in the tooltip.
   - *Worked example now*: Ekitike (Achilles) and Danns are out, so Liverpool's
     one FWD slot has a single fit candidate; Isak goes from 0.80 to 0.97
     P(start), ~75 minutes, ~4.6 xP a game.
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
   listing what the model is currently adjusting for (club moves, returns,
   flags), so a surprising number is never a mystery.

## Done

- Recency-window minutes (v1.7) — per-player game logs, last-6 window
  blended 70/30 with season rate; return-from-absence window.
- Club-move rule (v1.6) — movers and arrivals rebuilt from fresh priors.
