"""Expected points engine: blended per-90 rates × minutes × fixture context.

Produces the player × gameweek xP matrix that is the solver's only model input.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PROCESSED
from .data import Dataset, last_season_player_rates
from .minutes import build_minutes
from .strengths import (TeamStrengths, build_strengths, clean_sheet_prob,
                        expected_gc_points, fixture_lambdas)

GOAL_PTS = {"GKP": 10, "DEF": 6, "MID": 5, "FWD": 4}
CS_PTS = {"GKP": 4, "DEF": 4, "MID": 1, "FWD": 0}
ASSIST_PTS = 3
LAST_SEASON_WEIGHT = 0.6     # weight on last-season minutes vs current
LAST_SEASON_MINS_CAP = 3000
PRIOR_MINS = 600             # pseudo-minutes of positional prior
BONUS_PRIOR_MINS = 900       # bonus is noisy — shrink harder
PREMIUM_PRICE = 80           # 8.0m+: use an upper-quartile prior, not the median


def _position_priors(last_rates: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Median (and 80th pct) per-90 rates by position from last season regulars."""
    pool = players.merge(last_rates, on="code", how="inner")
    pool = pool[pool["mins_last"] >= 1200]
    rows = []
    for pos, grp in pool.groupby("pos"):
        rows.append({
            "pos": pos,
            **{f"prior_{c}": grp[f"{c}_last"].median()
               for c in ["xg90", "xa90", "saves90", "bonus90", "yellow90"]},
            "prior_defcon_rate": grp["defcon_rate_last"].median(),
            **{f"prior_hi_{c}": grp[f"{c}_last"].quantile(0.8)
               for c in ["xg90", "xa90", "bonus90"]},
        })
    return pd.DataFrame(rows)


def _blend(rate_last: float, mins_last: float, rate_cur: float, mins_cur: float,
           prior: float, prior_mins: float = PRIOR_MINS) -> float:
    """Minutes-weighted blend of last season, current season, and prior."""
    w_last = min(mins_last, LAST_SEASON_MINS_CAP) * LAST_SEASON_WEIGHT \
        if not np.isnan(rate_last) else 0.0
    r_last = 0.0 if np.isnan(rate_last) else rate_last
    w_cur = mins_cur if not np.isnan(rate_cur) else 0.0
    r_cur = 0.0 if np.isnan(rate_cur) else rate_cur
    num = r_last * w_last + r_cur * w_cur + prior * prior_mins
    den = w_last + w_cur + prior_mins
    return num / den


def build_rates(ds: Dataset, last_rates: pd.DataFrame) -> pd.DataFrame:
    """Blended per-90 scoring rates per player."""
    players = ds.players.merge(last_rates, on="code", how="left")
    priors = _position_priors(last_rates, ds.players)
    players = players.merge(priors, on="pos", how="left")

    mins_cur = players["minutes"].astype(float)

    def cur90(col: str) -> pd.Series:
        with np.errstate(divide="ignore", invalid="ignore"):
            r = players[col].astype(float) / mins_cur * 90
        return r.where(mins_cur >= 45, np.nan)

    cur = {
        "xg90": cur90("expected_goals"),
        "xa90": cur90("expected_assists"),
        "saves90": cur90("saves"),
        "bonus90": cur90("bonus"),
        "yellow90": cur90("yellow_cards"),
    }

    out = players[["id", "code", "web_name", "pos", "team", "team_short",
                   "now_cost", "status", "chance_of_playing_next_round",
                   "news", "selected_by_percent"]].copy()
    premium = players["now_cost"] >= PREMIUM_PRICE
    for c in ["xg90", "xa90", "saves90", "bonus90", "yellow90"]:
        prior = players[f"prior_{c}"].copy()
        hi = f"prior_hi_{c}"
        if hi in players.columns:
            prior[premium] = players.loc[premium, hi]
        prior_mins = BONUS_PRIOR_MINS if c == "bonus90" else PRIOR_MINS
        out[c] = [
            _blend(pl_last, ml, pc, mc, pr, prior_mins)
            for pl_last, ml, pc, mc, pr in zip(
                players[f"{c}_last"], players["mins_last"].fillna(0),
                cur[c], mins_cur, prior.fillna(0))
        ]
    # Defensive contribution: P(hit threshold per full appearance), blended in
    # appearance-count space. (Current-season counts aren't cleanly exposed in
    # bootstrap yet — last season + prior carries this early in the season.)
    apps = players["apps60_last"].fillna(0)
    rate = players["defcon_rate_last"].fillna(0)
    prior_rate = players["prior_defcon_rate"].fillna(0)
    out["defcon_rate"] = (rate * apps + prior_rate * 8) / (apps + 8)
    return out


def build_xp(ds: Dataset, horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (xp_matrix wide, components long) for the next `horizon` GWs."""
    last_rates = last_season_player_rates()
    strengths = build_strengths(ds)
    lambdas = fixture_lambdas(ds, strengths)
    minutes = build_minutes(ds, last_rates)
    rates = build_rates(ds, last_rates)

    gws = list(range(ds.next_gw, min(ds.next_gw + horizon, 39)))
    lam_by_team_gw: dict[tuple[int, int], list[pd.Series]] = {}
    for _, r in lambdas[lambdas["gw"].isin(gws)].iterrows():
        lam_by_team_gw.setdefault((int(r["team"]), int(r["gw"])), []).append(r)

    baseline_goals = {int(t): strengths.baseline_goals(int(t))
                      for t in ds.teams["id"]}
    baseline_conc = {int(t): strengths.baseline_conceded(int(t))
                     for t in ds.teams["id"]}

    df = rates.merge(minutes[["id", "avail", "p_start", "p_appear", "p60", "xmins"]],
                     on="id")
    comp_rows = []
    for _, p in df.iterrows():
        pos, team = p["pos"], int(p["team"])
        for gw in gws:
            fixtures = lam_by_team_gw.get((team, gw), [])
            c = dict(appearance=0.0, goals=0.0, assists=0.0, cs=0.0, gc=0.0,
                     saves=0.0, defcon=0.0, bonus=0.0, cards=0.0)
            for f in fixtures:
                att_mult = f["lam_for"] / baseline_goals[team]
                conc_mult = f["lam_against"] / baseline_conc[team]
                share = p["xmins"] / 90.0
                c["appearance"] += p["p60"] * 2 + (p["p_appear"] - p["p60"]) * 1
                c["goals"] += share * p["xg90"] * att_mult * GOAL_PTS[pos]
                c["assists"] += share * p["xa90"] * att_mult * ASSIST_PTS
                if CS_PTS[pos]:
                    c["cs"] += p["p60"] * clean_sheet_prob(f["lam_against"]) * CS_PTS[pos]
                if pos in ("GKP", "DEF"):
                    c["gc"] -= share * expected_gc_points(f["lam_against"])
                if pos == "GKP":
                    c["saves"] += share * (p["saves90"] / 3.0) * conc_mult
                else:
                    c["defcon"] += p["p60"] * p["defcon_rate"] * 2
                c["bonus"] += share * p["bonus90"]
                c["cards"] -= share * p["yellow90"]
            total = sum(c.values())
            comp_rows.append({"id": p["id"], "gw": gw, **c, "xp": total})

    comps = pd.DataFrame(comp_rows)
    wide = comps.pivot(index="id", columns="gw", values="xp")
    wide.columns = [f"xp_gw{g}" for g in wide.columns]
    matrix = df[["id", "code", "web_name", "pos", "team", "team_short", "now_cost",
                 "status", "chance_of_playing_next_round", "news",
                 "selected_by_percent", "avail", "p_start", "p_appear", "xmins"]
                ].merge(wide.reset_index(), on="id")
    xp_cols = [c for c in matrix.columns if c.startswith("xp_gw")]
    matrix["xp_total"] = matrix[xp_cols].sum(axis=1)
    return matrix.sort_values("xp_total", ascending=False), comps


def write_outputs(matrix: pd.DataFrame, comps: pd.DataFrame) -> None:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    matrix.to_csv(PROCESSED / "xp_matrix.csv", index=False)
    comps.to_csv(PROCESSED / "xp_components.csv", index=False)
