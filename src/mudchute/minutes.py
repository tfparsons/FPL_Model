"""Minutes model: P(start), P(appears), P(60+), expected minutes per fixture.

The highest-value component of the xP engine. Core ideas:
- Availability from status flags is a multiplier, never a hard filter.
- Start probability blends current-season starts (strong early signal of the
  post-transfer-window pecking order) with last season's late-season start rate.
  The evidence is weighted toward the last six games; the prior's weight
  fades with the number of games the club has played this season.
- New signings with no PL history get a price-based prior that evidence
  quickly overrides.
- Minutes per start blend this season's recent starts with last season's
  pattern, the window earning weight one start at a time.
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
RELIABLE_MINS = 1500.0   # last-season minutes for the prior to count in full;
                         # below it the prior leans on price and evidence weighs more
DEFAULT_MINS_PER_START = 78.0
MPS_PRIOR_STARTS = 5.0   # last season's minutes-per-start counts as this many
                         # starts against the recency window's own starts
                         # (2025-26 backtest: MAE 9.2 -> 8.0 min per start, and
                         # 12.2 -> 11.3 where the two disagree; VALIDATION.md)
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
                  form: pd.DataFrame | None = None,
                  logs: pd.DataFrame | None = None) -> pd.DataFrame:
    """Per-player minutes profile (per-fixture quantities).

    Two passes: a standalone start probability per player, then a slot
    rebalance per club/position (knock-on from known absences), then the
    minutes quantities.
    """
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
        price_prior = _price_prior_start_rate(p["now_cost"], p["pos"])
        # How much last season is there? An injury-wrecked 700-minute season
        # says little about the pecking order; its start rate must not read
        # as "rotation option". Reliability scales the prior toward price and
        # hands weight to this season's games.
        mins_last_ = float(p["mins_last"]) if pd.notna(p.get("mins_last")) else 0.0
        rel = float(np.clip(mins_last_ / RELIABLE_MINS, 0.0, 1.0))
        if pd.notna(late) and pd.notna(full):
            prior = rel * (0.5 * late + 0.5 * full) + (1 - rel) * price_prior
        elif pd.notna(full):
            prior = rel * full + (1 - rel) * price_prior
        else:
            prior = price_prior
        # Nailed premiums: a 10m+ player who played heavy minutes last season
        # starts when fit, whatever end-of-season rotation said.
        if (p["now_cost"] >= 100 and p["status"] == "a"
                and pd.notna(p.get("mins_last")) and p["mins_last"] >= 2000):
            prior = max(prior, 0.90)
        starts_cur = float(p["starts"])
        k_cur = rel * CURRENT_WEIGHT_K + (1 - rel) * ARRIVAL_WEIGHT_K
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
        # excludes an old club's games), blended with the season rate. The
        # RATE stays weighted to the last six games (form can dip, a rival can
        # arrive in January); what grows with the season is the CONFIDENCE in
        # it, so last season's prior fades as the club's games mount instead
        # of keeping a fifth of the say all year. (2025-26 backtest: better
        # in every phase, including after a rival starter emerges; VALIDATION.md)
        f = frm.loc[pid] if frm is not None and pid in frm.index else None
        if f is not None and pd.notna(f["win_rate"]) and f["club_games"] > 0:
            n_cur = int(f["club_games"]) if pid not in moves else int(moves[pid]["games_since"])
            season_rate = min(starts_cur / n_cur, 1.0) if n_cur > 0 else f["win_rate"]
            cur_rate = WINDOW_SHARE * float(f["win_rate"]) + (1 - WINDOW_SHARE) * season_rate
            n_eff = float(n_cur)
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
        if avail <= 0.25:
            # An absent player's standalone P(start) is "would he start if
            # fit?" — the games he missed while out are not evidence of a
            # lost place. Use his prior; availability zeroes his own xP anyway.
            base_start = max(base_start, prior)
        base_start = float(np.clip(base_start, 0.0, 0.97))

        # -- minutes patterns --
        mins_per_start = p["mins_per_start_last"]
        if pd.isna(mins_per_start):
            mins_per_start = DEFAULT_MINS_PER_START
        else:  # a thin season's substitution pattern is weak evidence too
            mins_per_start = rel * mins_per_start + (1 - rel) * DEFAULT_MINS_PER_START
        # This season's pattern, from the starts in the recency window at the
        # current club: a role change shows up here (a striker now going 90,
        # a winger now hooked on 65) but one or two starts are noise, so last
        # season keeps the weight of MPS_PRIOR_STARTS starts.
        if (f is not None and pd.notna(f.get("win_mps")) and float(f.get("win_starts", 0)) > 0):
            ns = float(f["win_starts"])
            mins_per_start = ((ns * float(f["win_mps"]) + MPS_PRIOR_STARTS * mins_per_start)
                              / (ns + MPS_PRIOR_STARTS))
        p60_start = p["p60_given_start_last"]
        if pd.isna(p60_start):
            p60_start = DEFAULT_P60_GIVEN_START
        else:
            p60_start = rel * p60_start + (1 - rel) * DEFAULT_P60_GIVEN_START
        sub_prob = DEFAULT_SUB_PROB
        if pd.notna(p.get("sub_apps_last")) and pd.notna(p.get("starts_last")):
            non_start_gws = max(38 - p["starts_last"], 1)
            sub_prob = float(np.clip(p["sub_apps_last"] / non_start_gws, 0.0, 0.8))
        if p["pos"] == "GKP":
            mins_per_start, p60_start, sub_prob = 90.0, 0.99, 0.02
        if returning:
            mins_per_start *= RETURN_MINS_SCALE

        out.append({
            "id": int(p["id"]), "code": p["code"], "name": str(p["web_name"]),
            "team": int(p["team"]), "pos": str(p["pos"]), "avail": avail,
            "base_start": base_start, "mins_per_start": mins_per_start,
            "p60_start": p60_start, "sub_prob": sub_prob,
        })
    base = pd.DataFrame(out)

    # -- pass 2: slot rebalance (knock-on from known absences) --
    from .slots import absence_pairings, formation_slots, rebalance
    if logs is not None and len(logs):
        slots = formation_slots(ds, logs, _last_season_gws())
        games = pd.DataFrame({"team": logs["team"], "game": logs["fixture"],
                              "player": logs["id"], "started": logs["started"]})
        reb = rebalance(base[["id", "name", "team", "pos", "base_start", "avail"]],
                        slots, absence_pairings(games)).set_index("id")
        base["base_start"] = base["id"].map(reb["base_start"]).fillna(base["base_start"])
        base["cover_for"] = base["id"].map(reb["cover_for"]).fillna("")
        base["squeeze"] = base["id"].map(reb["squeeze"]).fillna(False).astype(bool)
    else:
        base["cover_for"], base["squeeze"] = "", False

    # -- pass 3: minutes quantities --
    b = base
    b["p_start"] = b["avail"] * b["base_start"]
    b["p_appear"] = b["avail"] * (b["base_start"] + (1 - b["base_start"]) * b["sub_prob"])
    b["p60"] = b["p_start"] * b["p60_start"]
    b["xmins"] = b["p_start"] * b["mins_per_start"] + (b["p_appear"] - b["p_start"]) * SUB_MINS
    return b[["id", "code", "avail", "p_start", "p_appear", "p60", "xmins",
              "cover_for", "squeeze"]]


def _last_season_gws() -> pd.DataFrame | None:
    """Last season's game table (team, GW, position, starts) for formation shape."""
    from .config import HISTORY
    path = HISTORY / "2025-26" / "merged_gw.csv"
    if not path.exists():
        return None
    return pd.read_csv(path, usecols=["team", "GW", "position", "starts"], low_memory=False)
