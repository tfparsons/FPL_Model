# Validation: xP engine backtest on 2025-26

Method: for every player-fixture from GW6 onwards, predict points using only pre-gameweek information (2024-25 rates as priors, cumulative current-season stats, blended team strengths — the same formulas the live engine runs). Baselines: season points-per-game to date, and mean points over the last 4 GWs.

## Headline numbers

| Scope | n | RMSE model | RMSE PPG | RMSE form | ρ model | ρ PPG | ρ form |
|---|---|---|---|---|---|---|---|
| All players | 26163 | **2.00** | 2.17 | 2.11 | **0.646** | 0.645 | 0.727 |
| Played (mins>0) | 9972 | **3.01** | 2.97 | 3.22 | **0.303** | 0.276 | 0.268 |

ρ = Spearman rank correlation with actual points, averaged per GW — the number that matters for picking between players.

## By position (players who played)

| Pos | n | RMSE model | RMSE PPG | Mean bias |
|---|---|---|---|---|
| DEF | 3425 | 3.19 | 3.17 | -0.82 |
| FWD | 1269 | 3.11 | 3.07 | -0.78 |
| GKP | 666 | 2.81 | 2.86 | -0.34 |
| MID | 4612 | 2.86 | 2.80 | -0.81 |

## Honest caveats

- **No availability flags in the historical data**: the backtest can't discount players who were injured or suspended that week. This is why the form baseline out-ranks the model on the all-players scope — recent points implicitly encode who is fit and playing. Live, the engine reads status flags before every deadline, so real performance sits between the two scopes shown above.
- Among players who actually played, the model is the best ranker of the three — that's the scope closest to the transfer decisions the solver makes between viable players.
- Defensive-contribution rates had no prior season (rule introduced 2025-26), so early-season defcon estimates lean on position averages.
- "Would the solver have beaten Tim's actual 2025-26 season?" needs a full 38-GW rolling solver simulation with point-in-time squads and prices; deferred to Phase 2 rather than done badly here.

## Option-value calibration (simulation, 29 Aug 2026)

Two experiments on the live GW3 state (16 trials each, noise = the robustness model's):

- **Planning value of a banked FT** (same scenario solved with 0-3 starting FTs): 1st +8.44, 2nd +7.68, 3rd +6.14 decayed xP over an 8-GW horizon (se ≤ 0.53). Discounted to the horizon edge these set `ft_end_values` = 2.3 / 2.1 / 1.7 — the solver now values FTs it carries out of the window, which kills spurious late-horizon churn.
- **Reactive premium** (decide first, reveal news, re-solve the remainder; 16 + 32 trials, two seeds): the deterministic +2.7 edge of the recommended two-FT package over holding fell out-of-sample to −0.77 (se 0.84) and −1.65 (se 0.57); pooled −1.37 ± 0.47. Implied option value ≈ 2.0 xP per FT spent; `move_threshold` is set at 1.75 per transfer used.

Consequence: recommendations are deliberately reluctant — a one-FT move must beat holding by +1.75 plan value, a two-FT package by +3.5, and hits still need +2.0 over the best free plan on top.

## Where improvement effort should go, in order

1. **Minutes model recency**: the form baseline's advantage comes from recent-window information. A last-6-GW start-rate window (needs per-player `element-summary` pulls) is the single highest-value upgrade.
2. **Adopt OpenFPL's published models** (Phase 2 in the brief) once this pipeline has a few gameweeks of live history to compare against.
3. The solver needs nothing — it is exact given its inputs.
## Minutes evidence: recency window vs season-to-date (2025-26 game logs)

Question: which better predicts whether a player starts his next game — his
season-to-date start rate, or a decayed window over his club's last 6 games?
Scored from GW8 onward for every player with a start by GW7 (11,088
player-gameweeks), Brier score (lower is better):

| Evidence term | Brier | log-loss |
|---|---|---|
| Season-to-date | 0.191 | 0.570 |
| Last-6 window, decay 0.8 | 0.168 | 0.548 |
| 70% window + 30% season | **0.163** | **0.511** |

On the 2,247 cases where the two disagree by more than 0.3, season-to-date
scores 0.282 and the window 0.190 — a third less error exactly where it
matters (benchings, injuries, role changes). The model uses the 70/30 blend
as its evidence term (v1.7).

## Minutes model: prior fade, in-season minutes per start, and a binned sole-striker rule (2025-26 game logs, 10 Sep 2026)

Three candidate changes were replayed over every player-fixture of 2025-26
(27,154 player-games, 2024-25 as the prior) with the live model's formulas,
before any code changed. Two were adopted (v1.12), one was binned.

**1. Sole-striker rule — binned.** The idea: when a club fields exactly one
recognised forward and his recent minutes share is ≥ 70%, lift P(start) to
his window rate. It fired for only 7 forwards (68 player-games; five clubs
ran a lone striker for any stretch). Where it fired the model was already
calibrated — those forwards started 90% and the model said 90% — and the
rule pushed them to 95%, slightly worse (Brier 0.0919 → 0.0935). The Barry-
type case existed (Igor Thiago, new to Brentford, lone striker all season):
the model had him at 88% by GW4 and 93% by GW7 on its own; he started 28 of
29. The false-9 risk was real but rare (4 of 344 lifts: a lone striker on
the bench with nobody up top); the other 18 misses were absences the live
availability flag would catch. The stage-2 minutes check did its job,
blocking 20 cases that started only half the time (Gyökeres' GW12–16 slump,
Ekitiké once Isak returned). Its only real gains were new number-one
keepers with a backup prior, which the prior fade below recovers anyway.

**2. Prior fade — adopted.** The confidence in this season's evidence now
grows with the club's game count (n) instead of being capped at the
six-game window (n ≤ 3.7 decayed games, which left the prior a fifth of
the say all season); the rate itself stays 70/30 window/season.

| Slice | Brier before | after |
|---|---|---|
| All player-games | 0.0934 | 0.0922 |
| GW ≤ 10 | 0.0955 | 0.0951 |
| GW 11–25 | 0.0943 | 0.0933 |
| GW 26–38 | 0.0913 | 0.0896 |
| Incumbent GK/FWD, 6 games after a rival starter emerges | 0.2839 | 0.2808 |
| Backup-prior strikers now starting (window ≥ 80%), GW10+: mean P(start) vs actual 0.88 | 0.83 | 0.87 |

Not worse anywhere, including the recency-sensitive shock slice. Caveat
from that slice: after a rival starter emerges the model still gives the
displaced incumbent ~0.60 when he starts 0.35 — it is slow to drop players.
Neither version fixes that; it is the next thing to look at.

**3. Minutes per start from this season — adopted with shrinkage.** Last
season only (the old model) vs the recency window's starts vs a blend where
last season counts as k starts, MAE in minutes on 7,205 starts:

| Predictor | All starts | Last season and window disagree by > 12 min (n=448) |
|---|---|---|
| Last season only (old) | 9.2 | 12.2 |
| Window only | 7.3 | 15.2 |
| Blend, k = 3 | 7.7 | 11.6 |
| Blend, k = 5 (adopted) | 8.0 | 11.3 |

The window alone is best overall but worst when it disagrees with last
season, because that is usually one to three starts of data (median 3).
k = 5 beats the old model in both slices.
