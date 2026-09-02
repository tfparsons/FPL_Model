"""Solver constraint tests — these bugs are silent and costly."""

from __future__ import annotations

import pandas as pd
import pytest

from mudchute.config import Settings
from mudchute.solver import solve_plan, solve_single_gw

GWS = [2, 3]


def make_settings(**kw) -> Settings:
    defaults = dict(
        team_id=1, free_transfers=1, horizon=len(GWS), decay=0.85,
        bench_gk_weight=0.03, bench_outfield_weights=[0.18, 0.09, 0.04],
        vice_weight=0.05, hit_threshold=2.0, robustness_runs=5,
        player_noise=0.15, gw_noise=0.08,
        pool_sizes={"GKP": 15, "DEF": 45, "MID": 55, "FWD": 30},
    )
    defaults.update(kw)
    return Settings(**defaults)


def make_pool(n_teams: int = 8) -> pd.DataFrame:
    """Synthetic pool: 3 GKP, 8 DEF, 8 MID, 5 FWD per two-team block, plenty
    of cheap fodder so a legal squad always exists."""
    rows = []
    pid = 1
    for shape_pos, per_team, base_xp in [
        ("GKP", 2, 3.5), ("DEF", 4, 3.0), ("MID", 4, 4.0), ("FWD", 2, 4.5),
    ]:
        for t in range(n_teams):
            for k in range(per_team):
                # First player of each team/pos is good and pricey, rest cheap.
                good = k == 0
                rows.append({
                    "id": pid, "web_name": f"{shape_pos}{pid}", "pos": shape_pos,
                    "team": t + 1,
                    "now_cost": 90 - 10 * k if good else 40 + 2 * k,
                    "sell_price": pd.NA, "owned": False,
                    **{f"xp_gw{g}": (base_xp + 3 if good else base_xp - k * 0.3)
                       for g in GWS},
                })
                pid += 1
    return pd.DataFrame(rows)


def initial_squad_of(pool: pd.DataFrame) -> set[int]:
    """A legal cheap-ish starting squad: 2 GKP, 5 DEF, 5 MID, 3 FWD, ≤2/club."""
    ids: list[int] = []
    per_club: dict[int, int] = {}
    for pos, need in [("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]:
        cands = pool[pool["pos"] == pos].sort_values(["team", "id"])
        picked = 0
        for _, r in cands.iterrows():
            if picked == need:
                break
            if per_club.get(r["team"], 0) < 2:  # global club cap, room to spare
                ids.append(r["id"])
                per_club[r["team"]] = per_club.get(r["team"], 0) + 1
                picked += 1
        assert picked == need
    return set(ids)


def mark_owned(pool: pd.DataFrame, squad: set[int]) -> pd.DataFrame:
    pool = pool.copy()
    pool["owned"] = pool["id"].isin(squad)
    pool.loc[pool["owned"], "sell_price"] = pool.loc[pool["owned"], "now_cost"]
    return pool


@pytest.fixture
def base():
    pool = make_pool()
    squad = initial_squad_of(pool)
    pool = mark_owned(pool, squad)
    return pool, squad


def check_squad_legality(pool: pd.DataFrame, plan) -> None:
    df = pool.set_index("id")
    squad = df.loc[plan.squad]
    counts = squad["pos"].value_counts().to_dict()
    assert counts == {"DEF": 5, "MID": 5, "FWD": 3, "GKP": 2}
    assert squad["team"].value_counts().max() <= 3
    lineup = df.loc[plan.lineup]
    lc = lineup["pos"].value_counts().to_dict()
    assert len(plan.lineup) == 11
    assert lc.get("GKP", 0) == 1
    assert lc.get("DEF", 0) >= 3
    assert lc.get("MID", 0) >= 2
    assert lc.get("FWD", 0) >= 1
    assert set(plan.lineup) <= set(plan.squad)
    assert plan.captain in plan.lineup
    assert plan.vice in plan.lineup and plan.vice != plan.captain
    assert sorted(plan.bench + plan.lineup) == sorted(plan.squad)


def test_legal_plans_all_gws(base):
    pool, squad = base
    res = solve_plan(pool, GWS, squad, bank0=20, settings=make_settings())
    assert res.status in ("Optimal", "Gap limit reached")
    assert len(res.plans) == len(GWS)
    for plan in res.plans:
        check_squad_legality(pool, plan)


def test_budget_respected_with_sell_prices(base):
    pool, squad = base
    # Owned players sell below market: sell_price = now_cost - 2.
    pool.loc[pool["owned"], "sell_price"] = pool.loc[pool["owned"], "now_cost"] - 2
    res = solve_plan(pool, GWS, squad, bank0=0, settings=make_settings())
    df = pool.set_index("id")
    bank = 0
    prev_squad = squad
    for plan in res.plans:
        for p in plan.transfers_out:
            bank += (df.at[p, "sell_price"] if p in squad
                     else df.at[p, "now_cost"])
        for p in plan.transfers_in:
            bank -= df.at[p, "now_cost"]
        assert bank >= 0, "bank went negative"
        assert abs(plan.bank_after - bank / 10.0) < 1e-6
        # continuity: squad = prev + in - out
        expected = (set(prev_squad) - set(plan.transfers_out)) | set(plan.transfers_in)
        assert set(plan.squad) == expected
        prev_squad = plan.squad


def test_free_transfer_banking(base):
    pool, squad = base
    # Make standing still strictly optimal (owned players outscore everyone),
    # then check FTs bank 1 -> 2.
    for g in GWS:
        pool[f"xp_gw{g}"] = 1.0
        pool.loc[pool["owned"], f"xp_gw{g}"] = 2.0
    res = solve_plan(pool, GWS, squad, bank0=0, settings=make_settings())
    p0 = res.plans[0]
    assert p0.transfers_in == [] and p0.transfers_out == []
    assert p0.ft_before == 1 and p0.ft_after == 2


def test_hits_charged(base):
    pool, squad = base
    settings = make_settings()
    res = solve_plan(pool, GWS, squad, bank0=200, settings=settings,
                     force_transfers=3)
    p0 = res.plans[0]
    assert len(p0.transfers_in) == 3
    # 3 transfers, 1 FT -> 2 hits
    assert p0.hits == 2


def test_no_hits_override(base):
    pool, squad = base
    res = solve_plan(pool, GWS, squad, bank0=200, settings=make_settings(),
                     no_hits=True)
    for plan in res.plans:
        assert plan.hits == 0


def test_lock_and_ban(base):
    pool, squad = base
    worst_owned = min(squad)  # arbitrary owned player
    best_free = pool[~pool["owned"]].nlargest(1, "xp_gw2")["id"].iloc[0]
    res = solve_plan(pool, GWS, squad, bank0=200, settings=make_settings(),
                     lock_ids={worst_owned}, ban_ids={int(best_free)})
    for plan in res.plans:
        assert worst_owned in plan.squad
        assert best_free not in plan.squad


def test_single_gw_solver(base):
    pool, _ = base
    out = solve_single_gw(pool, gw=2, budget=1000, settings=make_settings())
    assert out["status"] in ("Optimal", "Gap limit reached")
    assert len(out["lineup"]) == 11
    assert out["captain"] in out["lineup"]
