"""Knock-on minutes: a club's minutes belong to positional SLOTS, not players.

Each club starts roughly N players per position group (its formation
shape, read from game logs). The candidates' standalone start probabilities
should sum to those slots. When a regular is known to be out, the shortfall
is redistributed to fit teammates in proportion to how likely they already
were to start (next in line gets most) and their headroom; if a group runs
out of candidates the slot spills to the adjacent group (a striker's slot
becomes a midfielder's — the false nine). When a returner creates a squeeze,
everyone in the group scales down. Beneficiaries keep their own per-90
rates: knock-on moves minutes, never talent.

Guards, so this generalises rather than chasing noise:
- redistribute only for KNOWN absences (availability <= ABSENT_AVAIL) of a
  player who would otherwise start (standalone P(start) >= REGULAR_MIN);
- only when the gap between slots and available mass is material;
- pairings learned from logs (who started when X was out) are blended in
  only once there are enough absence games to learn from.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

GROUPS = ["GKP", "FWD", "DEF", "MID"]     # processing order: MID absorbs spill
SPILL_TO = {"FWD": "MID", "DEF": "MID", "MID": "FWD", "GKP": None}
ABSENT_AVAIL = 0.25       # availability at/below which an absence is "known"
REGULAR_MIN = 0.40        # standalone P(start) that makes an absence matter
GAP_MIN = 0.25            # slots minus available mass must exceed this
SQUEEZE_MIN = 1.00        # available mass minus slots must exceed this
FIT_AVAIL = 0.50          # who can receive minutes
CAP = 0.97
COVER_MIN = 0.05          # gain that earns a "covering for" flag
PAIR_MIN_GAMES = 5        # absence games before learned pairings count
PAIR_SHARE = 0.5          # weight of learned pairings vs proportional split
SHAPE_PRIOR_GAMES = 4.0   # last-season shape counts like this many games


def formation_slots(ds, logs: pd.DataFrame,
                    last_gws: pd.DataFrame | None = None) -> dict[tuple[int, str], float]:
    """Expected starters per (team, position group) per game."""
    pos_of = dict(zip(ds.players["id"].astype(int), ds.players["pos"]))
    cur: dict[tuple[int, str], list[float]] = {}
    if len(logs):
        st = logs[logs["started"] == 1].copy()
        st["pos"] = st["id"].map(pos_of)
        per_game = st.groupby(["team", "fixture", "pos"]).size().reset_index(name="n")
        n_games = logs.groupby("team")["fixture"].nunique()
        for (team, pos), grp in per_game.groupby(["team", "pos"]):
            g = int(n_games.get(team, 0))
            if g:
                cur[(int(team), str(pos))] = [grp["n"].sum() / g, g]
    last: dict[tuple[int, str], float] = {}
    league = {"DEF": 4.0, "MID": 4.5, "FWD": 1.5}
    if last_gws is not None and len(last_gws):
        name_to_id = dict(zip(ds.teams["name"], ds.teams["id"].astype(int)))
        lg = last_gws[last_gws["starts"] == 1]
        per = lg.groupby(["team", "GW", "position"]).size().reset_index(name="n")
        mean = per.groupby(["team", "position"])["n"].sum() / lg.groupby("team")["GW"].nunique()
        for (tname, pos), v in mean.items():
            if tname in name_to_id and pos in league:
                last[(name_to_id[tname], pos)] = float(v)
        lm = per.groupby("position")["n"].sum() / lg.groupby("team")["GW"].nunique().sum()
        for pos in league:
            if pos in lm.index:
                league[pos] = float(lm[pos])
    out: dict[tuple[int, str], float] = {}
    for tid in ds.teams["id"].astype(int):
        raw = {}
        for pos in ["DEF", "MID", "FWD"]:
            prior = last.get((tid, pos), league[pos])
            c = cur.get((tid, pos))
            if c:
                rate, g = c
                raw[pos] = (g * rate + SHAPE_PRIOR_GAMES * prior) / (g + SHAPE_PRIOR_GAMES)
            else:
                raw[pos] = prior
        tot = sum(raw.values()) or 10.0
        for pos in raw:
            out[(tid, pos)] = 10.0 * raw[pos] / tot
        out[(tid, "GKP")] = 1.0
    return out


def absence_pairings(games: pd.DataFrame) -> dict[tuple[int, int], dict[int, float]]:
    """Who starts when X doesn't: {(team, X): {j: uplift}} from a game table
    with columns team, game, player, started (one row per player-game)."""
    out: dict[tuple[int, int], dict[int, float]] = {}
    if games is None or not len(games):
        return out
    for team, g in games.groupby("team"):
        piv = g.pivot_table(index="game", columns="player", values="started",
                            aggfunc="max", fill_value=0)
        if len(piv) < PAIR_MIN_GAMES:
            continue
        base = piv.mean()
        for x in piv.columns:
            if base[x] < REGULAR_MIN:
                continue
            out_games = piv[piv[x] == 0]
            if len(out_games) < PAIR_MIN_GAMES:
                continue
            uplift = (out_games.mean() - base).clip(lower=0)
            uplift = uplift.drop(index=x)
            if uplift.sum() > 0:
                out[(int(team), int(x))] = {int(j): float(u) for j, u in uplift.items() if u > 0}
    return out


def rebalance(base: pd.DataFrame, slots: dict[tuple[int, str], float],
              pairings: dict | None = None) -> pd.DataFrame:
    """base: id, name, team, pos, base_start, avail -> id, base_start, cover_for, squeeze."""
    pairings = pairings or {}
    df = base.copy()
    df["cover_for"] = ""
    df["squeeze"] = False
    df["base_start"] = df["base_start"].astype(float)
    for team, tdf in df.groupby("team"):
        team = int(team)
        extra: dict[str, float] = {g: 0.0 for g in GROUPS}
        spill_from: dict[str, list[str]] = {g: [] for g in GROUPS}
        order = GROUPS + ["FWD"]           # a second FWD pass catches MID spill
        for pos in order:
            idx = tdf.index[tdf["pos"] == pos]
            if not len(idx):
                continue
            S = slots.get((team, pos), 1.0 if pos == "GKP" else 3.0) + extra[pos]
            spilled_in, extra[pos] = extra[pos], 0.0
            inherited = spill_from[pos]
            spill_from[pos] = []
            p = df.loc[idx, "base_start"].to_numpy(float)
            a = df.loc[idx, "avail"].to_numpy(float)
            M = float((a * p).sum())
            absent = [(i, pi) for i, pi, ai in zip(idx, p, a)
                      if ai <= ABSENT_AVAIL and pi >= REGULAR_MIN]
            if (absent or spilled_in > GAP_MIN) and S - M > GAP_MIN:
                shortfall = S - M
                fit = a >= FIT_AVAIL
                # pairing weights: who covered for these absentees historically
                pw = np.zeros(len(idx))
                for i_abs, _ in absent:
                    pr = pairings.get((team, int(df.at[i_abs, "id"])))
                    if pr:
                        pw += np.array([pr.get(int(df.at[i, "id"]), 0.0) for i in idx])
                for _ in range(3):
                    head = np.clip(CAP - p, 0, None)
                    w = a * p * head * fit
                    if pw.sum() > 0:
                        w = (1 - PAIR_SHARE) * w / (w.sum() or 1) + PAIR_SHARE * (pw * head * fit) / ((pw * head * fit).sum() or 1)
                    if w.sum() <= 0 or shortfall <= 1e-6:
                        break
                    give = np.minimum(shortfall * w / w.sum(), head)
                    p = p + give
                    shortfall -= float((give * a).sum())
                gained = p - df.loc[idx, "base_start"].to_numpy(float)
                absent_sorted = [str(df.at[i, "name"]) for i, _ in
                                 sorted(absent, key=lambda t: -t[1])] + inherited
                names = ", ".join(absent_sorted[:3]) + (" +more" if len(absent_sorted) > 3 else "")
                for k, i in enumerate(idx):
                    if gained[k] >= COVER_MIN:
                        df.at[i, "cover_for"] = names
                df.loc[idx, "base_start"] = np.clip(p, 0, CAP)
                if shortfall > GAP_MIN and SPILL_TO[pos]:
                    extra[SPILL_TO[pos]] += shortfall
                    spill_from[SPILL_TO[pos]] += [str(df.at[i, "name"]) for i, _ in absent] + inherited
            elif M - S > SQUEEZE_MIN:
                # Too many fit starters for the slots: take the excess from
                # the uncertain candidates first (weight p(1-p)^2); a nailed
                # 0.95 barely moves, a 0.5 coin-flip carries the cut.
                excess = M - S
                p0 = p.copy()
                for _ in range(4):
                    u = a * p * (1 - p) ** 2      # squared: near-certain starters are shielded
                    if u.sum() <= 0 or excess <= 1e-6:
                        break
                    take = np.minimum(excess * u / u.sum(), p)
                    p = p - take
                    excess -= float((take * a).sum())
                df.loc[idx, "base_start"] = np.clip(p, 0, CAP)
                cut = p0 - p
                for k, i in enumerate(idx):
                    if cut[k] >= COVER_MIN:
                        df.at[i, "squeeze"] = True
    return df[["id", "base_start", "cover_for", "squeeze"]]
