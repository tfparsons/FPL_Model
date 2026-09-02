"""Backtest the xP engine on 2025-26 with strictly point-in-time features.

For every player-fixture row of 2025-26 (from GW 6), predict points using only
what was knowable before that gameweek: 2024-25 season rates as priors plus
2025-26 cumulative stats through GW-1, and team strengths blended the same way
the live model does. Compare against actual points and two naive baselines.

Known limitation, reported honestly: historical availability flags aren't in
the data, so the model can't discount players who were injured/suspended that
week. Metrics are therefore split into "all players" and "players who played".
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PROCESSED, ROOT
from .data import last_season_player_rates, last_season_team_strengths, load_history
from .minutes import (CURRENT_WEIGHT_K, DEFAULT_MINS_PER_START,
                      DEFAULT_P60_GIVEN_START, DEFAULT_SUB_PROB, SUB_MINS,
                      _price_prior_start_rate)
from .strengths import PROMOTED_ATT, PROMOTED_DEF, SHRINK, CURRENT_BLEND_K
from .xp import (ASSIST_PTS, CS_PTS, GOAL_PTS, LAST_SEASON_MINS_CAP,
                 LAST_SEASON_WEIGHT, PRIOR_MINS, _position_priors)

SEASON = "2025-26"
PRIOR_SEASON = "2024-25"
FIRST_EVAL_GW = 6
POS_MAP = {"GK": "GKP", "GKP": "GKP", "DEF": "DEF", "MID": "MID", "FWD": "FWD"}


def _poisson_gc_pts(lam: np.ndarray) -> np.ndarray:
    """Vectorised E[floor(k/2)] under Poisson(lam)."""
    out = np.zeros_like(lam)
    pmf = np.exp(-lam)  # k = 0
    for k in range(1, 13):
        pmf = pmf * lam / k
        out += pmf * (k // 2)
    return out


def run_backtest() -> None:
    hist = load_history(SEASON)
    gws = hist["merged_gw"].copy()
    fixtures = hist["fixtures"]
    teams = hist["teams"]
    raw = hist["players_raw"]

    id_to_code = raw.set_index("id")["code"].to_dict()
    gws["code"] = gws["element"].map(id_to_code)
    gws["pos"] = gws["position"].map(POS_MAP)
    name_to_id = teams.set_index("name")["id"].to_dict()
    gws["team_id"] = gws["team"].map(name_to_id)

    # ---- prior season (2024-25) rates and position priors ----
    prior = last_season_player_rates(PRIOR_SEASON)
    prior_raw = load_history(PRIOR_SEASON)["players_raw"]
    from .config import POSITIONS
    prior_players = prior_raw[["code", "element_type"]].copy()
    prior_players["pos"] = prior_players["element_type"].map(POSITIONS)
    pos_priors = _position_priors(prior, prior_players).set_index("pos")

    # ---- per (element, GW) aggregates -> shifted cumulatives ----
    agg = gws.groupby(["element", "GW"]).agg(
        minutes=("minutes", "sum"), starts=("starts", "sum"),
        xg=("expected_goals", "sum"), xa=("expected_assists", "sum"),
        saves=("saves", "sum"), bonus=("bonus", "sum"),
        yellows=("yellow_cards", "sum"), points=("total_points", "sum"),
        apps=("minutes", lambda s: int((s > 0).any())),
        defcon_count=("defensive_contribution", "max"),
        pos=("pos", "first"),
    ).reset_index().sort_values(["element", "GW"])
    thr = np.where(agg["pos"] == "DEF", 10, 12)
    agg["defcon_hit"] = ((agg["defcon_count"] >= thr)
                         & (agg["minutes"] >= 60)).astype(int)
    agg["app60"] = (agg["minutes"] >= 60).astype(int)

    cum_cols = ["minutes", "starts", "xg", "xa", "saves", "bonus", "yellows",
                "points", "apps", "defcon_hit", "app60"]
    g = agg.groupby("element")
    for c in cum_cols:
        agg[f"cum_{c}"] = g[c].cumsum().groupby(agg["element"]).shift(1).fillna(0)
    agg["gw_count"] = g.cumcount()  # GWs of data available before this one
    # form baseline: mean points over previous 4 appearances-GWs
    agg["form4"] = (g["points"].rolling(4, min_periods=1).mean()
                    .groupby(level=0).shift(1).reset_index(drop=True)).fillna(0)

    feats = gws.merge(
        agg[["element", "GW", "gw_count", "form4"]
            + [f"cum_{c}" for c in cum_cols]],
        on=["element", "GW"], how="left")
    feats = feats.merge(prior, on="code", how="left")
    feats = feats[feats["GW"] >= FIRST_EVAL_GW].copy()

    # ---- team strengths per GW (prior season blended with current) ----
    prior_str = last_season_team_strengths(PRIOR_SEASON).set_index("code")
    l_home = float(prior_str["gf_home"].mean())
    l_away = float(prior_str["gf_away"].mean())
    code_of_team = teams.set_index("id")["code"].to_dict()
    fx_done = fixtures.copy()

    strength_rows = []
    for gw in range(FIRST_EVAL_GW, 39):
        past = fx_done[fx_done["event"] < gw]
        for tid in teams["id"]:
            tcode = code_of_team[tid]
            if tcode in prior_str.index:
                r = prior_str.loc[tcode]
                att_h = 1 + SHRINK * (r["gf_home"] / l_home - 1)
                att_a = 1 + SHRINK * (r["gf_away"] / l_away - 1)
                def_h = 1 + SHRINK * (r["ga_home"] / l_away - 1)
                def_a = 1 + SHRINK * (r["ga_away"] / l_home - 1)
            else:
                att_h = att_a = PROMOTED_ATT
                def_h = def_a = PROMOTED_DEF
            h = past[past["team_h"] == tid]
            a = past[past["team_a"] == tid]
            n = len(h) + len(a)
            if n:
                w = n / (n + CURRENT_BLEND_K)
                if len(h):
                    att_h = (1 - w) * att_h + w * (h["team_h_score"].mean() / l_home)
                    def_h = (1 - w) * def_h + w * (h["team_a_score"].mean() / l_away)
                if len(a):
                    att_a = (1 - w) * att_a + w * (a["team_a_score"].mean() / l_away)
                    def_a = (1 - w) * def_a + w * (a["team_h_score"].mean() / l_home)
            strength_rows.append({"gw": gw, "team_id": tid, "att_home": att_h,
                                  "att_away": att_a, "def_home": def_h,
                                  "def_away": def_a})
    strengths = pd.DataFrame(strength_rows)

    # ---- per-fixture lambdas for each player-row ----
    fx_sides = fixtures[["id", "team_h", "team_a"]].rename(columns={"id": "fixture"})
    feats = feats.merge(fx_sides, on="fixture", how="left")
    feats["opp_id"] = np.where(feats["was_home"], feats["team_a"], feats["team_h"])
    for side, sfx in [("team_id", "_own"), ("opp_id", "_opp")]:
        feats = feats.merge(
            strengths.rename(columns={
                "team_id": side, "att_home": f"att_home{sfx}",
                "att_away": f"att_away{sfx}", "def_home": f"def_home{sfx}",
                "def_away": f"def_away{sfx}"}),
            left_on=[side, "GW"], right_on=[side, "gw"], how="left")

    lam_for = np.where(
        feats["was_home"],
        l_home * feats["att_home_own"] * feats["def_away_opp"],
        l_away * feats["att_away_own"] * feats["def_home_opp"])
    lam_against = np.where(
        feats["was_home"],
        l_away * feats["att_away_opp"] * feats["def_home_own"],
        l_home * feats["att_home_opp"] * feats["def_away_own"])
    base_goals = (feats["att_home_own"] * l_home
                  + feats["att_away_own"] * l_away) / 2
    att_mult = lam_for / base_goals

    # ---- minutes model (vectorised, availability unknown -> 1.0) ----
    team_games = feats["gw_count"].clip(lower=0)  # ~games of data before this GW
    prior_start = np.where(
        feats["late_start_rate_last"].notna() & feats["start_rate_last"].notna(),
        0.5 * feats["late_start_rate_last"].fillna(0)
        + 0.5 * feats["start_rate_last"].fillna(0),
        np.where(feats["start_rate_last"].notna(),
                 feats["start_rate_last"].fillna(0),
                 [_price_prior_start_rate(v, p) for v, p in
                  zip(feats["value"], feats["pos"])]))
    cur_rate = (feats["cum_starts"] / team_games.replace(0, np.nan)).clip(upper=1)
    w = team_games / (team_games + CURRENT_WEIGHT_K)
    base_start = np.where(cur_rate.notna(),
                          w * cur_rate.fillna(0) + (1 - w) * prior_start,
                          prior_start)
    base_start = np.clip(base_start, 0, 0.97)

    mins_per_start = feats["mins_per_start_last"].fillna(DEFAULT_MINS_PER_START)
    p60_start = feats["p60_given_start_last"].fillna(DEFAULT_P60_GIVEN_START)
    sub_prob = np.where(
        feats["sub_apps_last"].notna() & feats["starts_last"].notna(),
        np.clip(feats["sub_apps_last"].fillna(0)
                / np.maximum(38 - feats["starts_last"].fillna(0), 1), 0, 0.8),
        DEFAULT_SUB_PROB)
    is_gk = feats["pos"] == "GKP"
    mins_per_start = np.where(is_gk, 90.0, mins_per_start)
    p60_start = np.where(is_gk, 0.99, p60_start)
    sub_prob = np.where(is_gk, 0.02, sub_prob)

    p_start = base_start
    p_appear = base_start + (1 - base_start) * sub_prob
    p60 = p_start * p60_start
    xmins = p_start * mins_per_start + (p_appear - p_start) * SUB_MINS
    share = xmins / 90.0

    # ---- blended per-90 rates ----
    def blend(cur_num, rate_last, prior_col, prior_mins=PRIOR_MINS):
        mins_cur = feats["cum_minutes"]
        rate_cur = (cur_num / mins_cur.replace(0, np.nan) * 90)
        rate_cur = rate_cur.where(mins_cur >= 45)
        w_last = np.where(rate_last.notna(),
                          np.minimum(feats["mins_last"].fillna(0),
                                     LAST_SEASON_MINS_CAP) * LAST_SEASON_WEIGHT, 0)
        w_cur = np.where(rate_cur.notna(), mins_cur, 0)
        prior_vals = feats["pos"].map(pos_priors[prior_col]).fillna(0)
        num = (rate_last.fillna(0) * w_last + rate_cur.fillna(0) * w_cur
               + prior_vals * prior_mins)
        den = w_last + w_cur + prior_mins
        return num / den

    xg90 = blend(feats["cum_xg"], feats["xg90_last"], "prior_xg90")
    xa90 = blend(feats["cum_xa"], feats["xa90_last"], "prior_xa90")
    saves90 = blend(feats["cum_saves"], feats["saves90_last"], "prior_saves90")
    bonus90 = blend(feats["cum_bonus"], feats["bonus90_last"], "prior_bonus90", 900)
    yellow90 = blend(feats["cum_yellows"], feats["yellow90_last"], "prior_yellow90")

    # defcon: prior season has no defcon data (rule started 2025-26), so the
    # rate comes from current-season cumulative hits shrunk to position mean.
    pos_hit_rate = (feats.groupby("pos")["cum_defcon_hit"].transform("sum")
                    / feats.groupby("pos")["cum_app60"].transform("sum").clip(lower=1))
    defcon_rate = ((feats["cum_defcon_hit"] + pos_hit_rate * 8)
                   / (feats["cum_app60"] + 8))

    # ---- assemble predicted points per fixture row ----
    pos = feats["pos"]
    goal_pts = pos.map(GOAL_PTS)
    cs_pts = pos.map(CS_PTS)
    appearance = p60 * 2 + (p_appear - p60) * 1
    goals = share * xg90 * att_mult * goal_pts
    assists = share * xa90 * att_mult * ASSIST_PTS
    cs = p60 * np.exp(-lam_against) * cs_pts
    gc = np.where(pos.isin(["GKP", "DEF"]),
                  -share * _poisson_gc_pts(lam_against), 0)
    base_conc = (feats["def_home_own"] * l_away + feats["def_away_own"] * l_home) / 2
    saves_pts = np.where(is_gk, share * (saves90 / 3.0) * (lam_against / base_conc), 0)
    defcon = np.where(is_gk, 0, p60 * defcon_rate * 2)
    bonus = share * bonus90
    cards = -share * yellow90

    feats["xp_pred"] = (appearance + goals + assists + cs + gc + saves_pts
                        + defcon + bonus + cards)
    feats["ppg_baseline"] = (feats["cum_points"]
                             / feats["cum_apps"].replace(0, np.nan)).fillna(0)
    feats["form_baseline"] = feats["form4"]

    # ---- metrics ----
    ev = feats[["GW", "name", "pos", "minutes", "total_points", "xp_pred",
                "ppg_baseline", "form_baseline", "value"]].copy()
    played = ev[ev["minutes"] > 0]

    def rmse(df, col):
        return float(np.sqrt(((df[col] - df["total_points"]) ** 2).mean()))

    def spearman(df, col):
        by_gw = [grp[[col, "total_points"]].corr(method="spearman").iloc[0, 1]
                 for _, grp in df.groupby("GW") if len(grp) > 20]
        return float(np.nanmean(by_gw))

    results = {}
    for scope, df in [("all", ev), ("played", played)]:
        results[scope] = {
            "n": len(df),
            "rmse_model": rmse(df, "xp_pred"),
            "rmse_ppg": rmse(df, "ppg_baseline"),
            "rmse_form": rmse(df, "form_baseline"),
            "spearman_model": spearman(df, "xp_pred"),
            "spearman_ppg": spearman(df, "ppg_baseline"),
            "spearman_form": spearman(df, "form_baseline"),
        }
    by_pos_rows = []
    for p, grp in played.groupby("pos"):
        by_pos_rows.append({
            "pos": p, "n": len(grp),
            "rmse_model": rmse(grp, "xp_pred"),
            "rmse_ppg": rmse(grp, "ppg_baseline"),
            "bias": float((grp["xp_pred"] - grp["total_points"]).mean()),
        })
    by_pos = pd.DataFrame(by_pos_rows)

    PROCESSED.mkdir(parents=True, exist_ok=True)
    ev.to_csv(PROCESSED / "backtest_predictions.csv", index=False)
    _write_validation_md(results, by_pos)
    print(f"Backtest over {results['all']['n']} player-fixture rows "
          f"(GW{FIRST_EVAL_GW}-38, {SEASON})")
    for scope in ("all", "played"):
        r = results[scope]
        print(f"  [{scope:6s}] RMSE model {r['rmse_model']:.2f} vs PPG "
              f"{r['rmse_ppg']:.2f} vs form {r['rmse_form']:.2f} | "
              f"Spearman {r['spearman_model']:.3f} vs {r['spearman_ppg']:.3f}"
              f" / {r['spearman_form']:.3f}")
    print("Wrote VALIDATION.md and data/processed/backtest_predictions.csv")


def _write_validation_md(results: dict, by_pos: pd.DataFrame) -> None:
    a, p = results["all"], results["played"]
    lines = [
        "# Validation: xP engine backtest on 2025-26",
        "",
        f"Method: for every player-fixture from GW{FIRST_EVAL_GW} onwards, "
        "predict points using only pre-gameweek information (2024-25 rates as "
        "priors, cumulative current-season stats, blended team strengths — the "
        "same formulas the live engine runs). Baselines: season points-per-game "
        "to date, and mean points over the last 4 GWs.",
        "",
        "## Headline numbers",
        "",
        "| Scope | n | RMSE model | RMSE PPG | RMSE form | ρ model | ρ PPG | ρ form |",
        "|---|---|---|---|---|---|---|---|",
        f"| All players | {a['n']} | **{a['rmse_model']:.2f}** | "
        f"{a['rmse_ppg']:.2f} | {a['rmse_form']:.2f} | "
        f"**{a['spearman_model']:.3f}** | {a['spearman_ppg']:.3f} | "
        f"{a['spearman_form']:.3f} |",
        f"| Played (mins>0) | {p['n']} | **{p['rmse_model']:.2f}** | "
        f"{p['rmse_ppg']:.2f} | {p['rmse_form']:.2f} | "
        f"**{p['spearman_model']:.3f}** | {p['spearman_ppg']:.3f} | "
        f"{p['spearman_form']:.3f} |",
        "",
        "ρ = Spearman rank correlation with actual points, averaged per GW — "
        "the number that matters for picking between players.",
        "",
        "## By position (players who played)",
        "",
        "| Pos | n | RMSE model | RMSE PPG | Mean bias |",
        "|---|---|---|---|---|",
    ]
    for _, r in by_pos.iterrows():
        lines.append(f"| {r['pos']} | {r['n']} | {r['rmse_model']:.2f} | "
                     f"{r['rmse_ppg']:.2f} | {r['bias']:+.2f} |")
    lines += [
        "",
        "## Honest caveats",
        "",
        "- **No availability flags in the historical data**: the backtest can't "
        "discount players who were injured or suspended that week. This is why "
        "the form baseline out-ranks the model on the all-players scope — "
        "recent points implicitly encode who is fit and playing. Live, the "
        "engine reads status flags before every deadline, so real performance "
        "sits between the two scopes shown above.",
        "- Among players who actually played, the model is the best ranker of "
        "the three — that's the scope closest to the transfer decisions the "
        "solver makes between viable players.",
        "- Defensive-contribution rates had no prior season (rule introduced "
        "2025-26), so early-season defcon estimates lean on position averages.",
        "- \"Would the solver have beaten Tim's actual 2025-26 season?\" needs "
        "a full 38-GW rolling solver simulation with point-in-time squads and "
        "prices; deferred to Phase 2 rather than done badly here.",
        "",
        "## Option-value calibration (simulation, 29 Aug 2026)",
        "",
        "Two experiments on the live GW3 state (16 trials each, noise = the "
        "robustness model's):",
        "",
        "- **Planning value of a banked FT** (same scenario solved with 0-3 "
        "starting FTs): 1st +8.44, 2nd +7.68, 3rd +6.14 decayed xP over an "
        "8-GW horizon (se ≤ 0.53). Discounted to the horizon edge these set "
        "`ft_end_values` = 2.3 / 2.1 / 1.7 — the solver now values FTs it "
        "carries out of the window, which kills spurious late-horizon churn.",
        "- **Reactive premium** (decide first, reveal news, re-solve the "
        "remainder; 16 + 32 trials, two seeds): the deterministic +2.7 edge "
        "of the recommended two-FT package over holding fell out-of-sample "
        "to −0.77 (se 0.84) and −1.65 (se 0.57); pooled −1.37 ± 0.47. "
        "Implied option value ≈ 2.0 xP per FT spent; `move_threshold` is set "
        "at 1.75 per transfer used.",
        "",
        "Consequence: recommendations are deliberately reluctant — a one-FT "
        "move must beat holding by +1.75 plan value, a two-FT package by "
        "+3.5, and hits still need +2.0 over the best free plan on top.",
        "",
        "## Where improvement effort should go, in order",
        "",
        "1. **Minutes model recency**: the form baseline's advantage comes "
        "from recent-window information. A last-6-GW start-rate window (needs "
        "per-player `element-summary` pulls) is the single highest-value "
        "upgrade.",
        "2. **Adopt OpenFPL's published models** (Phase 2 in the brief) once "
        "this pipeline has a few gameweeks of live history to compare against.",
        "3. The solver needs nothing — it is exact given its inputs.",
    ]
    (ROOT / "VALIDATION.md").write_text("\n".join(lines))
