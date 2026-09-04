"""Minutes model: P(start), P(appears), P(60+), expected minutes per fixture.

The highest-value component of the xP engine. Core ideas:
- Availability from status flags is a multiplier, never a hard filter.
- Start probability blends current-season starts (strong early signal of the
  post-transfer-window pecking order) with last season's late-season start rate.
- New signings with no PL history get a price-based prior that evidence
  quickly overrides.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .data import Dataset

CURRENT_WEIGHT_K = 1.5   # team games for current season to reach 40% weight
ARRIVAL_WEIGHT_K = 1.0   # movers/arrivals: new-club evidence dominates faster
WINDOW_SHARE = 0.7       # evidence = 0.7 x recent-window rate + 0.3 x season rate
                         # (last-season backtest: Brier 0.163 vs 0.191 season-only)
RETURN_START_CAP = 0.75  # a regular back from a 3+ game absence is eased in
RETURN_MINS_SCALE = 0.85
DEFAULT_MINS_PER_START = 78.0
DEFAULT_P60_GIVEN_START = 0.85
DEFAULT_SUB_PROB = 0.15
SUB_MINS = 18.0


def availability(row: pd.Series) -> float:
    """Map status flag + chance_of_playing to P(available) multiplier."""
    status = row["status"]
    chance = row["chance_of_playing_next_round"]
    if status == "a":
        return 1.0
    if status == "d":
        return (chance / 100.0) if pd.notna(chance) else 0.75
    if status in ("i", "s", "n"):
        return (chance / 100.0) if pd.notna(chance) else (
            0.1 if status == "i" else 0.0)
    if status == "u":
        return 0.0
    return 1.0


def _price_prior_start_rate(now_cost: int, pos: str) -> float:
    """Prior P(start) for players with no PL history, from price."""
    price = now_cost / 10.0
    if pos == "GKP":
        return 0.85 if price >= 5.0 else 0.4
    if price >= 7.0:
        return 0.78
    if price >= 5.5:
        return 0.60
    if price >= 4.8:
        return 0.45
    return 0.30


def build_minutes(ds: Dataset, last_rates: pd.DataFrame,
                  moves: dict[int, dict] | None = None,
                  flags: pd.DataFrame | None = None,
                  form: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-player minutes profile (per-fixture quantities)."""
    moves = moves or {}
    adj = (flags.set_index("id")["adjusted"].to_dict() if flags is not None else {})
    frm = form.set_index("id") if form is not None else None
    players = ds.players.merge(last_rates, on="code", how="left")

    # Games each team has actually started this season (live GWs count).
    fx = ds.fixtures
    started = fx[fx["started"] == True]  # noqa: E712
    games_played = {}
    for tid in ds.teams["id"]:
        games_played[tid] = int(
            ((started["team_h"] == tid) | (started["team_a"] == tid)).sum())

    out = []
    for _, p in players.iterrows():
        n_cur = games_played.get(p["team"], 0)
        avail = availability(p)

        # -- start probability --
        # Blend late-season and full-season start rates: late captures the
        # current pecking order, full smooths single-window noise (rests in
        # dead rubbers, a red card, one injury).
        late, full = p["late_start_rate_last"], p["start_rate_last"]
        if pd.notna(late) and pd.notna(full):
            prior = 0.5 * late + 0.5 * full
        elif pd.notna(full):
            prior = full
        else:
            prior = _price_prior_start_rate(p["now_cost"], p["pos"])
        # Nailed premiums: a 10m+ player who played heavy minutes last season
        # starts when fit, whatever end-of-season rotation said.
        if (p["now_cost"] >= 100 and p["status"] == "a"
                and pd.notna(p.get("mins_last")) and p["mins_last"] >= 2000):
            prior = max(prior, 0.90)
        starts_cur = float(p["starts"])
        k_cur = CURRENT_WEIGHT_K
        pid = int(p["id"])
        if pid in moves:
            # Mid-season mover: the old club's starts are not evidence about
            # the new pecking order. Start from the price prior and let the
            # new club's games decide.
            mv = moves[pid]
            n_cur = int(mv["games_since"])
            starts_cur = max(starts_cur - float(mv["starts_at_move"]), 0.0)
            prior = _price_prior_start_rate(p["now_cost"], p["pos"])
            k_cur = ARRIVAL_WEIGHT_K
        elif adj.get(pid) == "arrival":
            # Summer signing / no PL record: last season's pattern (if any)
            # was at another club — meet it halfway with the price prior,
            # and let this season's games at the new club dominate quickly.
            price_prior = _price_prior_start_rate(p["now_cost"], p["pos"])
            prior = 0.5 * (prior + price_prior) if pd.notna(full) else price_prior
            k_cur = ARRIVAL_WEIGHT_K
        # Evidence: the recency window at the current club (which already
        # excludes an old club's games), blended with the season rate.
        f = frm.loc[pid] if frm is not None and pid in frm.index else None
        if f is not None and pd.notna(f["win_rate"]) and f["club_games"] > 0:
            n_cur = int(f["club_games"]) if pid not in moves else int(moves[pid]["games_since"])
            season_rate = min(starts_cur / n_cur, 1.0) if n_cur > 0 else f["win_rate"]
            cur_rate = WINDOW_SHARE * float(f["win_rate"]) + (1 - WINDOW_SHARE) * season_rate
            n_eff = min(float(f["win_n"]), float(n_cur)) if n_cur > 0 else 0.0
        elif n_cur > 0:
            cur_rate = min(starts_cur / n_cur, 1.0)
            n_eff = float(n_cur)
        else:
            cur_rate, n_eff = prior, 0.0
        if n_eff > 0:
            w = n_eff / (n_eff + k_cur)
            base_start = w * cur_rate + (1 - w) * prior
        else:
            base_start = prior
        returning = bool(f is not None and f["returning"] and p["status"] == "a")
        if returning:
            base_start = min(base_start, RETURN_START_CAP)
        base_start = float(np.clip(base_start, 0.0, 0.97))

        # -- minutes patterns --
        mins_per_start = p["mins_per_start_last"]
        if pd.isna(mins_per_start):
            mins_per_start = DEFAULT_MINS_PER_START
        p60_start = p["p60_given_start_last"]
        if pd.isna(p60_start):
            p60_start = DEFAULT_P60_GIVEN_START
        sub_prob = DEFAULT_SUB_PROB
        if pd.notna(p.get("sub_apps_last")) and pd.notna(p.get("starts_last")):
            non_start_gws = max(38 - p["starts_last"], 1)
            sub_prob = float(np.clip(p["sub_apps_last"] / non_start_gws, 0.0, 0.8))
        if p["pos"] == "GKP":
            mins_per_start, p60_start, sub_prob = 90.0, 0.99, 0.02
        if returning:
            mins_per_start *= RETURN_MINS_SCALE

        p_start = avail * base_start
        p_appear = avail * (base_start + (1 - base_start) * sub_prob)
        p60 = p_start * p60_start
        xmins = p_start * mins_per_start + (p_appear - p_start) * SUB_MINS

        out.append({
            "id": p["id"], "code": p["code"], "avail": avail,
            "p_start": p_start, "p_appear": p_appear, "p60": p60,
            "xmins": xmins,
        })
    return pd.DataFrame(out)
