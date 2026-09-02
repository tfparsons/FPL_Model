"""Multi-period MILP transfer solver on HiGHS.

Decisions per GW: squad, lineup, captain, transfers in/out, FT banking, hits.
Bench order and vice-captain are chosen post-hoc (provably optimal given the
squad/lineup split). Chips (wildcard / bench boost / triple captain) enter as
"allowed once anywhere in the horizon" solver modes; free hit is approximated
by a single-GW re-pick solve in pipeline.py.

All money is in 0.1m integer units throughout.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import highspy
import pandas as pd

from .config import (FORMATION_MAX, FORMATION_MIN, HIT_COST,
                     MAX_PER_CLUB, SQUAD_SHAPE, Settings)

TRANSFERS_CAP = 20


@dataclass
class GWPlan:
    gw: int
    squad: list[int]
    lineup: list[int]
    captain: int
    vice: int
    bench: list[int]              # order: GK then outfield 1-3
    transfers_in: list[int]
    transfers_out: list[int]
    hits: int
    ft_before: int
    ft_after: int
    bank_after: float             # £m


@dataclass
class SolveResult:
    status: str
    objective: float
    plans: list[GWPlan] = field(default_factory=list)
    chip_played: tuple[str, int] | None = None   # (chip, gw)


def solve_plan(
    pool: pd.DataFrame,
    gws: list[int],
    initial_squad: set[int],
    bank0: int,
    settings: Settings,
    chip: str | None = None,          # None | "wildcard" | "bboost" | "3xc"
    lock_ids: set[int] = frozenset(),
    ban_ids: set[int] = frozenset(),
    force_transfers: int | None = None,
    no_hits: bool = False,
    xp_override: dict[tuple[int, int], float] | None = None,
    mip_gap: float = 0.005,
    time_limit: float = 90.0,
) -> SolveResult:
    """Solve the multi-period transfer problem over `gws`.

    pool: DataFrame with id, web_name, pos, team, now_cost, sell_price, owned,
          and xp_gw{g} columns. sell_price applies to initially-owned players;
          anyone bought during the horizon buys and sells at now_cost.
    """
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("mip_rel_gap", mip_gap)
    h.setOptionValue("time_limit", time_limit)
    qsum = h.qsum if hasattr(h, "qsum") else sum

    players = pool.reset_index(drop=True)
    P = range(len(players))
    T = range(len(gws))
    pid = players["id"].tolist()
    pos = players["pos"].tolist()
    team = players["team"].tolist()
    buy_cost = players["now_cost"].astype(int).tolist()
    sell_val = [
        int(players.at[i, "sell_price"]) if players.at[i, "owned"]
        else int(players.at[i, "now_cost"])
        for i in P
    ]
    xp = {}
    for i in P:
        for t, g in enumerate(gws):
            v = players.at[i, f"xp_gw{g}"]
            if xp_override is not None:
                v = xp_override.get((pid[i], g), v)
            xp[i, t] = max(float(v), 0.0)

    idx = {p: i for i, p in enumerate(pid)}
    init = [1 if p in initial_squad else 0 for p in pid]

    # ---- variables ----
    squad = {(i, t): h.addBinary() for i in P for t in T}
    lineup = {(i, t): h.addBinary() for i in P for t in T}
    cap = {(i, t): h.addBinary() for i in P for t in T}
    tin = {(i, t): h.addBinary() for i in P for t in T}
    tout = {(i, t): h.addBinary() for i in P for t in T}
    max_ft = settings.max_free_transfers
    bank = {t: h.addVariable(lb=0) for t in T}
    ft = {t: h.addVariable(lb=0, ub=max_ft,
                           type=highspy.HighsVarType.kInteger) for t in T}
    used_ft = {t: h.addVariable(lb=0, ub=max_ft,
                                type=highspy.HighsVarType.kInteger) for t in T}
    paid = {t: h.addVariable(lb=0, ub=TRANSFERS_CAP,
                             type=highspy.HighsVarType.kInteger) for t in T}
    free_wc = {t: h.addVariable(lb=0, ub=TRANSFERS_CAP,
                                type=highspy.HighsVarType.kInteger) for t in T}

    wc = {t: h.addBinary() for t in T} if chip == "wildcard" else None
    bb = {t: h.addBinary() for t in T} if chip == "bboost" else None
    tc = {t: h.addBinary() for t in T} if chip == "3xc" else None
    # Linearization helpers for chip-conditional scoring terms.
    zbb = {(i, t): h.addVariable(lb=0, ub=1) for i in P for t in T} if bb else None
    ztc = {(i, t): h.addVariable(lb=0, ub=1) for i in P for t in T} if tc else None

    # ---- squad continuity, transfers, budget ----
    for t in T:
        transfers_t = qsum([tin[i, t] for i in P])
        for i in P:
            prev = init[i] if t == 0 else squad[i, t - 1]
            h.addConstr(squad[i, t] == prev + tin[i, t] - tout[i, t])
            h.addConstr(tin[i, t] + tout[i, t] <= 1)
        prev_bank = bank0 if t == 0 else bank[t - 1]
        h.addConstr(
            bank[t] == prev_bank
            + qsum([tout[i, t] * sell_val[i] for i in P])
            - qsum([tin[i, t] * buy_cost[i] for i in P]))
        h.addConstr(transfers_t <= TRANSFERS_CAP)

        # Free-transfer accounting: every transfer is paid for by one of
        # banked FTs, a hit, or the wildcard.
        h.addConstr(used_ft[t] + paid[t] + free_wc[t] == transfers_t)
        h.addConstr(used_ft[t] <= ft[t])
        if wc is not None:
            h.addConstr(free_wc[t] <= TRANSFERS_CAP * wc[t])
        else:
            h.addConstr(free_wc[t] == 0)
        if t == 0:
            h.addConstr(ft[0] == min(settings.free_transfers, max_ft))
        if t + 1 in T:
            h.addConstr(ft[t + 1] <= ft[t] - used_ft[t] + 1)
        if no_hits:
            h.addConstr(paid[t] == 0)
    if force_transfers is not None:
        h.addConstr(qsum([tin[i, 0] for i in P]) == int(force_transfers))
    for c in (wc, bb, tc):
        if c is not None:
            h.addConstr(qsum([c[t] for t in T]) <= 1)

    # ---- squad shape, lineup, captain ----
    for t in T:
        for p_name, count in SQUAD_SHAPE.items():
            h.addConstr(qsum([squad[i, t] for i in P if pos[i] == p_name]) == count)
        for club in set(team):
            h.addConstr(
                qsum([squad[i, t] for i in P if team[i] == club]) <= MAX_PER_CLUB)
        h.addConstr(qsum([lineup[i, t] for i in P]) == 11)
        for p_name in SQUAD_SHAPE:
            n = qsum([lineup[i, t] for i in P if pos[i] == p_name])
            h.addConstr(n >= FORMATION_MIN[p_name])
            h.addConstr(n <= FORMATION_MAX[p_name])
        for i in P:
            h.addConstr(lineup[i, t] <= squad[i, t])
            h.addConstr(cap[i, t] <= lineup[i, t])
        h.addConstr(qsum([cap[i, t] for i in P]) == 1)

    # ---- overrides ----
    for p in lock_ids:
        if p in idx:
            for t in T:
                h.addConstr(squad[idx[p], t] == 1)
    for p in ban_ids:
        if p in idx:
            for t in T:
                h.addConstr(squad[idx[p], t] == 0)

    # ---- chip linearizations ----
    if bb is not None:
        for i in P:
            for t in T:
                h.addConstr(zbb[i, t] <= squad[i, t] - lineup[i, t])
                h.addConstr(zbb[i, t] <= bb[t])
    if tc is not None:
        for i in P:
            for t in T:
                h.addConstr(ztc[i, t] <= cap[i, t])
                h.addConstr(ztc[i, t] <= tc[t])

    # ---- objective ----
    w_gk = settings.bench_gk_weight
    w_of = sum(settings.bench_outfield_weights) / len(settings.bench_outfield_weights)
    terms = []
    # Banked FTs carried past the horizon have option value (next horizon's
    # planning value, measured empirically) — without this the solver churns
    # freely in its final weeks.
    last = T[-1] if len(T) else 0
    ft_vals = [v for v in settings.ft_end_values if v > 0][:max_ft]
    if ft_vals and len(T):
        z_ft = [h.addBinary() for _ in ft_vals]
        h.addConstr(qsum(z_ft) <= ft[last] - used_ft[last])
        d_last = settings.decay ** last
        for v, zz in zip(ft_vals, z_ft):
            terms.append(d_last * v * zz)
    for t in T:
        d = settings.decay ** t
        terms.append(-d * settings.churn_penalty * qsum([tin[i, t] for i in P]))
        if t > 0:
            # Future FT spends carry the measured reactive option value —
            # planned moves must each earn their place. (Wildcard-week moves
            # spend no FTs and are exempt via free_wc; this week's move is
            # judged by the pipeline's move-vs-hold bar instead.)
            terms.append(-d * settings.move_threshold * (used_ft[t] + paid[t]))
        for i in P:
            w_bench = w_gk if pos[i] == "GKP" else w_of
            terms.append(d * xp[i, t] * (1 - w_bench) * lineup[i, t])
            terms.append(d * xp[i, t] * w_bench * squad[i, t])
            terms.append(d * xp[i, t] * cap[i, t])
            if bb is not None:
                terms.append(d * xp[i, t] * (1 - w_bench) * zbb[i, t])
            if tc is not None:
                terms.append(d * xp[i, t] * ztc[i, t])
        terms.append(-d * HIT_COST * paid[t])
    h.maximize(qsum(terms))

    status = h.getModelStatus()
    status_str = h.modelStatusToString(status)
    if status_str not in ("Optimal", "Time limit reached", "Gap limit reached"):
        return SolveResult(status=status_str, objective=float("nan"))
    info = h.getInfo()
    if info.primal_solution_status != 2:  # no feasible solution found
        return SolveResult(status=f"{status_str} (no solution)",
                           objective=float("nan"))

    # ---- extract ----
    val = h.val
    plans = []
    chip_played = None
    for t in T:
        sq = [pid[i] for i in P if val(squad[i, t]) > 0.5]
        lu = [pid[i] for i in P if val(lineup[i, t]) > 0.5]
        cp = [pid[i] for i in P if val(cap[i, t]) > 0.5][0]
        xp_of = lambda p: xp[idx[p], t]
        vice_cands = sorted((p for p in lu if p != cp), key=xp_of, reverse=True)
        benched = [p for p in sq if p not in lu]
        bench_gk = [p for p in benched if pos[idx[p]] == "GKP"]
        bench_of = sorted((p for p in benched if pos[idx[p]] != "GKP"),
                          key=xp_of, reverse=True)
        plans.append(GWPlan(
            gw=gws[t], squad=sq, lineup=lu, captain=cp, vice=vice_cands[0],
            bench=bench_gk + bench_of,
            transfers_in=[pid[i] for i in P if val(tin[i, t]) > 0.5],
            transfers_out=[pid[i] for i in P if val(tout[i, t]) > 0.5],
            hits=round(val(paid[t])),
            ft_before=round(val(ft[t])),
            ft_after=round(min(val(ft[t]) - val(used_ft[t]) + 1,
                               max_ft)) if t + 1 in T else -1,
            bank_after=val(bank[t]) / 10.0,
        ))
        for name, var in (("wildcard", wc), ("bboost", bb), ("3xc", tc)):
            if var is not None and val(var[t]) > 0.5:
                chip_played = (name, gws[t])

    return SolveResult(status=status_str, objective=h.getObjectiveValue(),
                       plans=plans, chip_played=chip_played)


def solve_single_gw(
    pool: pd.DataFrame,
    gw: int,
    budget: int,
    settings: Settings,
    ban_ids: set[int] = frozenset(),
    mip_gap: float = 0.005,
    time_limit: float = 30.0,
) -> dict[str, Any]:
    """Best possible one-GW squad for a given budget (the free-hit question)."""
    h = highspy.Highs()
    h.setOptionValue("output_flag", False)
    h.setOptionValue("mip_rel_gap", mip_gap)
    h.setOptionValue("time_limit", time_limit)
    qsum = h.qsum if hasattr(h, "qsum") else sum

    players = pool.reset_index(drop=True)
    players = players[~players["id"].isin(ban_ids)].reset_index(drop=True)
    P = range(len(players))
    pos = players["pos"].tolist()
    team = players["team"].tolist()
    xp = [max(float(players.at[i, f"xp_gw{gw}"]), 0.0) for i in P]

    squad = {i: h.addBinary() for i in P}
    lineup = {i: h.addBinary() for i in P}
    cap = {i: h.addBinary() for i in P}

    h.addConstr(qsum([squad[i] * int(players.at[i, "now_cost"]) for i in P])
                <= budget)
    for p_name, count in SQUAD_SHAPE.items():
        h.addConstr(qsum([squad[i] for i in P if pos[i] == p_name]) == count)
    for club in set(team):
        h.addConstr(qsum([squad[i] for i in P if team[i] == club]) <= MAX_PER_CLUB)
    h.addConstr(qsum([lineup[i] for i in P]) == 11)
    for p_name in SQUAD_SHAPE:
        n = qsum([lineup[i] for i in P if pos[i] == p_name])
        h.addConstr(n >= FORMATION_MIN[p_name])
        h.addConstr(n <= FORMATION_MAX[p_name])
    for i in P:
        h.addConstr(lineup[i] <= squad[i])
        h.addConstr(cap[i] <= lineup[i])
    h.addConstr(qsum([cap[i] for i in P]) == 1)

    h.maximize(qsum([xp[i] * (lineup[i] + cap[i]) for i in P]))
    status_str = h.modelStatusToString(h.getModelStatus())
    if status_str not in ("Optimal", "Time limit reached", "Gap limit reached"):
        return {"status": status_str, "xp": float("nan")}
    lu = [players.at[i, "id"] for i in P if h.val(lineup[i]) > 0.5]
    cp = [players.at[i, "id"] for i in P if h.val(cap[i]) > 0.5]
    return {"status": status_str, "xp": h.getObjectiveValue(),
            "lineup": lu, "captain": cp[0] if cp else None}
