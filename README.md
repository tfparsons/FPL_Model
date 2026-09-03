# FPL Model

**TL;DR** — a self-running Fantasy Premier League decision system. A
statistical model projects every player's expected points (xP) over the next
8 gameweeks; an exact optimiser turns that into a transfer, captaincy and
chip plan; a calibrated decision layer decides whether the best move is even
worth making; and a dashboard explains the whole call — with the reasoning,
the uncertainty, and the alternatives. It runs itself on a schedule, and it
scores every recommendation against reality once the gameweek settles. No
server, no login: Python + [HiGHS](https://highs.dev/), a GitHub Action, and
a static site.

The system splits cleanly in two: a **back end** that decides, and a
**front end** that explains. The hinge between them is one file — a
player × gameweek xP matrix. The model's only job is to fill it; the
solver's only model input is to read it. Either half can be replaced without
touching the other.

---

## The back end: how it decides

### 1. Expected points — the currency everything trades in

**xP** is the expected FPL score of a player in a fixture: the probability-
weighted average over how the match could go. It's built from three
independent sub-models:

- **Minutes** — the load-bearing one. P(start) blends this season's starts
  with last season's pattern (weighted by how much evidence this season has
  produced), giving P(60+ minutes) and expected minutes. Injury flags
  *discount* a player, never remove him — a 75%-flagged player keeps 75% of
  his xP and the solver weighs him against the alternatives.
- **Rates** — per-90 goal threat, assists, saves, bonus, defensive
  contribution, blended from two seasons with shrinkage toward positional
  priors so a hot fortnight doesn't read as a new true talent level.
- **Fixtures** — team attack/defence strengths turned into expected goals
  for and against each fixture, which scale the attacking rates and set
  clean-sheet odds. A double gameweek is simply two fixtures summed; a blank
  is zero.

The per-fixture combination produces nine point components (appearance,
goals, assists, clean sheets, conceded, saves, defensive contribution,
bonus, cards) — kept separate so the dashboard can say *why* a player
projects well, not just that he does.

Derivatives the system runs on: **xP/£m** (value), **decayed xP** (future
gameweeks discounted ~15% per week — the solver's actual objective, since
plans beyond next week are provisional), and **FDR** (average opponent
strength, computed from the same team-strength model rather than taken from
FPL's ratings, which are zeroed early-season).

### 2. The optimiser

A multi-period mixed-integer program over the full 8-gameweek horizon:
squad, line-up, captain and transfers per gameweek, chained through the
free-transfer ledger, the bank, and FPL's real rules (2-5-5-3 squad, ≤3 per
club, formations, banking up to 5 FTs, −4 hits, chip weeks). Solving all
eight weeks at once is what lets it bank a transfer *now* because a double
gameweek is coming *later*. Chips are evaluated as separate solves — "what
would a wildcard add this horizon?" — and reported, never auto-played.

### 3. Knowing when not to act

A naive optimiser is over-eager: it will happily spend a free transfer for
+0.1 xP, because it prices doing nothing at zero. This model prices the
option value of waiting, with thresholds calibrated by simulation (running
the planner through hundreds of noisy replans and measuring what a banked
transfer is actually worth):

- A move must beat **holding** by a bar per free transfer spent; below it,
  the call is hold and the reasoning still shows the move that was
  considered.
- A **hit** must beat the best free plan by a further margin.
- Banked transfers carry terminal value at the horizon's edge, and planned
  future moves are taxed — so the plan doesn't quietly assume free churn
  later.

The result is a deliberately reluctant adviser: fewer moves, each one
earning its place.

### 4. Dealing with uncertainty

Every projection is wrong; the question is whether the *decision* survives
being wrong. The shake test re-solves the plan dozens of times with the
model's own error distribution injected into the xP matrix, and reports:

- **Conviction** — how often the key incoming player is bought across
  scenarios (the honest metric for multi-player moves, where the exact pair
  rarely repeats under noise);
- the **scenario field** — the most common alternative moves, so a 56%
  conviction comes with the shape of the other 44%.

Sampling is adaptive: 25 re-solves normally, escalating to 100 exactly when
precision can change the read — conviction in the ambiguous 30–70% band, a
gain sitting near the decision bar, or the final pre-deadline run.

### 5. Reading the calendar, the market, and your actual team

- **Deadline-relative, always.** Nothing assumes a Saturday: midweek rounds,
  doubles, blanks, postponements and rescheduling are read off the live
  fixture list.
- **Transfer late by default** — after team news. The model argues for an
  early move only when a specific trap is forming: the recommended package
  is an exact budget fit, projected price changes are moving against it,
  and the fallback is meaningfully worse. Then it estimates the probability
  the package breaks by the deadline and names the window to act in.
- **The real game-state, never an assumption.** Free transfers are derived
  by replaying the manager's actual transfer history; selling prices are
  reconstructed; transfers made mid-week (which the public API hides until
  the deadline) are declared in one config line and ingested immediately —
  the system's advice is always about the team as it actually is.

### 6. Marking its own homework

Every run appends its full call to a log. Once a gameweek finishes, the
*last pre-deadline* call is scored against live points — intraday re-runs
can't quietly revise history. Runs are stamped with a **model version** from
a changelog, so a changed recommendation is attributable to a model change
rather than mistaken for noise, and each version accumulates its own
measured record over the season: did the tweak actually help?

---

## The front end: how it explains

The design rule the dashboard is built to: *the UI should not just present
the outcomes, but help the user understand the data.* Every recommendation
comes with its reasoning, its confidence, and the alternatives it beat.

**This week's call** leads: a verdict ("Make 2 transfers" / "Hold — bank the
FT") with a one-sentence rationale naming the target, the financing move,
and the gain against the bar. Under it:

- **Swap analysis modules** — one card per transfer: out-player vs
  in-player across xP, ownership, the in-player's scoring drivers and
  fixture run, with deltas on every row and a sparkline of the two
  trajectories. Each card ends with *Alternative Solutions*: the nearest
  substitutes, priced and scored against the pick.
- **A package ladder** — the decision rule as a picture: hold, best single,
  and the package plotted against the bar the move had to clear.
- **A look-ahead chart** — the recommended move and the shake test's
  runners-up, comparable over any horizon: the opportunity cost of the road
  not taken, kept visible even after the transfers are made.
- **Timing and price watch** — the act-early alert when the trap above is
  forming, and projected price moves across the squad.

State-awareness runs through it: once transfers are confirmed, the card
flips to *"2 transfers made"* with the decision-time rationale preserved
(gain vs bar, price risk, conviction — frozen as the record to judge the
decision by later), new signings get badges on the pitch, and the remaining
advice is phrased for the transfers actually left.

**The market view** answers the opportunity-cost question: the market's top
targets interleaved with your squad in one sortable table, an all-player xP
explorer with a horizon slider, and a fixture-difficulty ticker for every
team — with international breaks drawn in, because chip timing cares.

**The track record** is the accountability page: points per gameweek
(predicted vs the model's line-up vs the actual team), a decision record per
settled week, the full run log with model-version tags, and the model
changelog with each change's expected and measured impact.

Explanations live in ⓘ hovers and explain the *model* — what conviction
means, how xP is built — never the UI. The copy states facts.

---

## Key numbers, at a glance

| Number | What it is |
|---|---|
| **xP** | Expected points: the probability-weighted average score |
| **xP/£m** | Value — expected points per million of price |
| **Decayed xP** | Horizon total with future weeks discounted — the solver's objective |
| **Gain vs bar** | What a move adds over holding, against the threshold it must clear |
| **Conviction** | Share of shaken scenarios that buy the key player |
| **FDR** | Average opponent strength over a run of fixtures, 1–5 |

## Run it on your own team

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/); the FPL API is
public, no login or key.

1. Put your team id in `config/settings.yaml` (the number in your team page
   URL).
2. `make update && make solve && make report` (~6 minutes end to end).
3. Open `site/index.html` — it brands itself with your team's name.
   Mid-week transfers are declared in `config/confirmed.yaml`.

`make test` runs the solver constraint suite. For the self-running setup,
`.github/workflows/run.yml` is the scheduler and `api/refresh.js` powers a
manual-refresh button (`GITHUB_DISPATCH_TOKEN` in the host env — use a
fine-grained PAT scoped to Actions on the one repo).

## Going deeper

- **[docs/architecture.html](docs/architecture.html)** — the full
  information architecture: every pipeline drawn, every formula and
  threshold written down.
- **[VALIDATION.md](VALIDATION.md)** — the honest backtest: where the model
  beats naive baselines, where it doesn't, and the simulations behind the
  decision thresholds.
- Solver formulation follows
  [FPL-Optimization-Tools](https://github.com/sertalpbilal/FPL-Optimization-Tools);
  historical data from
  [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League);
  [OpenFPL](https://github.com/daniegr/OpenFPL) is the candidate drop-in
  projection engine.
