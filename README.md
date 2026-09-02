# Mudchute — an FPL transfer optimiser

A complete, self-running Fantasy Premier League decision system: it projects
every player's expected points (xP) over the next 8 gameweeks, runs an exact
optimiser over transfers, captaincy, free-transfer banking, hits and chips,
and publishes its reasoning to a dashboard — automatically, several times a
day, with a track record that scores every call it makes.

This repo is a **showcase snapshot** of the working system (the live copy
runs privately, because publishing your transfer plans before the deadline is
a bad idea in a mini-league). Everything needed to run it on your own team is
here.

## The shape of it

```
FPL API ──► xP engine ──► xp_matrix.csv ──► MILP solver ──► plan.json ──► dashboard
             (model)      (the contract)     (decisions)     (output)      (static site)
```

Three ideas hold it together:

- **One contract between model and solver.** The xP engine's only output is a
  player × gameweek matrix; the solver's only model input is that matrix.
  Either half can be replaced without touching the other.
- **The repo is the database.** No server: a scheduled GitHub Action pulls
  data, solves, commits the results, and a static host redeploys. Every run
  is reproducible from the commit that made it.
- **Honesty as a feature.** A hit must beat the best free plan by a margin; a
  move must beat *holding* by a calibrated bar (banked transfers have option
  value); every recommendation is shake-tested under the model's own
  uncertainty; and once a gameweek finishes, the last pre-deadline call is
  scored against reality and logged, under the model version that made it.

**[docs/architecture.html](docs/architecture.html)** is the full information
architecture — every pipeline drawn, every formula and threshold written
down. Open it in a browser; it's the best place to start reading.

## What's inside

```
src/mudchute/
  api.py         FPL API client — timestamped snapshots
  data.py        squads, selling prices, free-transfer derivation, historical seasons
  strengths.py   team attack/defence from results
  minutes.py     P(start), P(60+), expected minutes — the load-bearing model
  xp.py          per-90 rates (shrunk blends) × minutes × fixture → xP matrix
  solver.py      multi-period MILP (HiGHS): transfers, FTs, hits, chips, option value
  robustness.py  adaptive shake test (25→100 perturbed re-solves)
  pipeline.py    orchestration: solves, decision bars, timing triggers → plan.json
  schedule.py    deadline-relative timing: midweeks, doubles, blanks, reschedules
  history.py     run log + outcome settling (the track record)
  changelog.py   model versioning — every run is stamped
  due.py         "is a run warranted?" for the scheduler
  site.py        plan.json → dashboard (static HTML) + report
  backtest.py    point-in-time validation → VALIDATION.md
```

[VALIDATION.md](VALIDATION.md) is the honest backtest: where the model beats
naive baselines, where it doesn't, and the calibration experiments behind the
decision thresholds.

## Run it on your own team

Needs Python 3.12+ and [uv](https://docs.astral.sh/uv/). No login or API key
— the FPL API is public.

1. Put your team id in `config/settings.yaml` (it's the number in your team
   page URL).
2. ```
   make update    # pull fresh data (~30s)
   make solve     # model + solver + shake test (~5 min)
   make report    # build the dashboard into site/
   ```
3. Open `site/index.html`. Transfers made mid-week (invisible to the public
   API until the deadline) are declared in `config/confirmed.yaml`.

`make test` runs the solver constraint suite. For the full self-running
setup, `.github/workflows/run.yml` is the scheduler and `api/refresh.js` is
the dashboard's manual-refresh hook (set `GITHUB_DISPATCH_TOKEN` in your
host's environment — use a fine-grained PAT scoped to Actions on the one
repo).

## Reference points

The solver formulation follows
[FPL-Optimization-Tools](https://github.com/sertalpbilal/FPL-Optimization-Tools);
historical season data from
[vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League);
[OpenFPL](https://github.com/daniegr/OpenFPL) is the candidate drop-in for
the projection engine.
