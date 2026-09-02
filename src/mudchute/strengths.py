"""Team attack/defence strengths and per-fixture expected goals.

FPL's own strength ratings are zeroed at the start of the season, so strengths
are derived from last season's results (shrunk toward the mean), with promoted
teams given a typical promoted-side profile, then blended toward current-season
results as games accumulate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .data import Dataset, last_season_team_strengths

SHRINK = 0.7            # pull of last-season rates toward league average
PROMOTED_ATT = 0.80     # promoted sides score ~20% below average...
PROMOTED_DEF = 1.20     # ...and concede ~20% above
CURRENT_BLEND_K = 10.0  # current-season games needed for a 50% blend weight


@dataclass
class TeamStrengths:
    table: pd.DataFrame      # per current team id: att_home/away, def_home/away
    league_home_goals: float
    league_away_goals: float

    def baseline_goals(self, team_id: int) -> float:
        """A team's per-game expected goals vs an average opponent."""
        r = self.table.loc[self.table["id"] == team_id].iloc[0]
        return (r["att_home"] * self.league_home_goals
                + r["att_away"] * self.league_away_goals) / 2

    def baseline_conceded(self, team_id: int) -> float:
        r = self.table.loc[self.table["id"] == team_id].iloc[0]
        return (r["def_home"] * self.league_away_goals
                + r["def_away"] * self.league_home_goals) / 2


def build_strengths(ds: Dataset) -> TeamStrengths:
    last = last_season_team_strengths()
    l_home = float(last["gf_home"].mean())
    l_away = float(last["gf_away"].mean())
    by_code = last.set_index("code")

    rows = []
    for _, t in ds.teams.iterrows():
        code = t["code"]
        if code in by_code.index:
            r = by_code.loc[code]
            att_h = 1 + SHRINK * (r["gf_home"] / l_home - 1)
            att_a = 1 + SHRINK * (r["gf_away"] / l_away - 1)
            def_h = 1 + SHRINK * (r["ga_home"] / l_away - 1)
            def_a = 1 + SHRINK * (r["ga_away"] / l_home - 1)
        else:  # promoted side
            att_h = att_a = PROMOTED_ATT
            def_h = def_a = PROMOTED_DEF
        rows.append({"id": t["id"], "short_name": t["short_name"],
                     "att_home": att_h, "att_away": att_a,
                     "def_home": def_h, "def_away": def_a})
    table = pd.DataFrame(rows)

    # Blend toward current-season scoring rates once games accumulate.
    fx = ds.fixtures
    done = fx[fx["finished"] == True]  # noqa: E712
    if len(done) > 0:
        for i, row in table.iterrows():
            tid = row["id"]
            h = done[done["team_h"] == tid]
            a = done[done["team_a"] == tid]
            n = len(h) + len(a)
            if n == 0:
                continue
            w = n / (n + CURRENT_BLEND_K)
            if len(h):
                table.at[i, "att_home"] = (1 - w) * row["att_home"] + w * (
                    h["team_h_score"].mean() / l_home)
                table.at[i, "def_home"] = (1 - w) * row["def_home"] + w * (
                    h["team_a_score"].mean() / l_away)
            if len(a):
                table.at[i, "att_away"] = (1 - w) * row["att_away"] + w * (
                    a["team_a_score"].mean() / l_away)
                table.at[i, "def_away"] = (1 - w) * row["def_away"] + w * (
                    a["team_h_score"].mean() / l_home)

    return TeamStrengths(table=table, league_home_goals=l_home,
                         league_away_goals=l_away)


def fixture_lambdas(ds: Dataset, st: TeamStrengths) -> pd.DataFrame:
    """Expected goals for and against per team per future fixture."""
    s = st.table.set_index("id")
    rows = []
    fx = ds.fixtures
    for _, f in fx[fx["event"].notna()].iterrows():
        h, a, gw = int(f["team_h"]), int(f["team_a"]), int(f["event"])
        lam_h = st.league_home_goals * s.at[h, "att_home"] * s.at[a, "def_away"]
        lam_a = st.league_away_goals * s.at[a, "att_away"] * s.at[h, "def_home"]
        # Fixture difficulty = the OPPONENT's strength on the relevant side,
        # so a weak team's own leakiness doesn't inflate its "difficulty".
        # 1.0 = average opponent; higher = harder.
        diff_h = 0.5 * s.at[a, "att_away"] + 0.5 / s.at[a, "def_away"]
        diff_a = 0.5 * s.at[h, "att_home"] + 0.5 / s.at[h, "def_home"]
        rows.append({"gw": gw, "team": h, "opponent": a, "is_home": True,
                     "lam_for": lam_h, "lam_against": lam_a, "difficulty": diff_h})
        rows.append({"gw": gw, "team": a, "opponent": h, "is_home": False,
                     "lam_for": lam_a, "lam_against": lam_h, "difficulty": diff_a})
    return pd.DataFrame(rows)


def expected_gc_points(lam: float, max_k: int = 12) -> float:
    """E[floor(goals_conceded / 2)] under Poisson(lam) — the -1-per-2-conceded rule."""
    total, pmf = 0.0, math.exp(-lam)
    for k in range(1, max_k + 1):
        pmf_k = math.exp(-lam) * lam ** k / math.factorial(k)
        total += pmf_k * (k // 2)
    return total


def clean_sheet_prob(lam: float) -> float:
    return math.exp(-lam)
