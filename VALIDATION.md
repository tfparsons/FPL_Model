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
