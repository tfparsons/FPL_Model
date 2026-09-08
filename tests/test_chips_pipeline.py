"""The pipeline's chip wiring end to end on a synthetic dataset: a real
Dataset + xP matrix + GWPlans in, JSON-serialisable verdicts out."""
import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from mudchute.chips import chip_windows
from mudchute.config import load_settings
from mudchute.data import Dataset
from mudchute.pipeline import _assess_chips, _jsonify
from mudchute.solver import GWPlan

NEXT_GW, HORIZON = 4, 8
GWS = list(range(NEXT_GW, NEXT_GW + HORIZON))
SQUAD = list(range(1, 16))            # 1 = the captain (MCI), 12-15 = bench
BENCH = [12, 13, 14, 15]


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _dataset(doubles: dict[int, list[int]] | None = None, played=()) -> Dataset:
    base = datetime(2026, 9, 12, 12, 30, tzinfo=timezone.utc)
    events = pd.DataFrame([{"id": g, "deadline_time": _iso(base + timedelta(days=7 * (g - NEXT_GW))),
                            "finished": False, "data_checked": False, "is_next": g == NEXT_GW,
                            "is_current": False} for g in range(1, 39)])
    teams = pd.DataFrame([{"id": t, "short_name": f"T{t:02d}" if t != 1 else "MCI", "name": f"Team {t}"}
                          for t in range(1, 21)])
    rows = []
    for g in range(NEXT_GW, 20):                      # every GW to the half's end has a full round
        ko = base + timedelta(days=7 * (g - NEXT_GW), hours=3)
        for h in range(1, 21, 2):
            rows.append({"event": g, "kickoff_time": _iso(ko), "team_h": h, "team_a": h + 1, "code": g * 100 + h})
        for t in (doubles or {}).get(g, []):          # a second fixture = a double
            rows.append({"event": g, "kickoff_time": _iso(ko + timedelta(days=3)), "team_h": t,
                         "team_a": 20 if t != 20 else 19, "code": g * 100 + 50 + t})
    fixtures = pd.DataFrame(rows)
    squad = pd.DataFrame({"id": SQUAD, "web_name": [f"P{i}" for i in SQUAD], "sell_price": [50] * 15})
    return Dataset(players=pd.DataFrame(), teams=teams, fixtures=fixtures, events=events,
                   squad=squad, bank=0.0, free_transfers=1, confirmed_transfers=[],
                   total_points=0, entry_name="Test", max_ft=5, next_gw=NEXT_GW,
                   chips_available=["wildcard", "freehit", "bboost", "3xc"], picks_gw=NEXT_GW - 1,
                   fetched_at="now", chips_played=list(played), chip_windows=chip_windows({}))


def _matrix(spike: dict[tuple[int, int], float] | None = None, avail: dict[int, float] | None = None):
    rows = []
    for pid in range(1, 40):
        r = {"id": pid, "web_name": "Haaland" if pid == 1 else f"P{pid}",
             "team_short": "MCI" if pid == 1 else f"T{(pid % 19) + 2:02d}",
             "avail": (avail or {}).get(pid, 1.0), "p_start": 0.95, "xmins": 85.0}
        for g in GWS:
            r[f"xp_gw{g}"] = (spike or {}).get((pid, g), 6.0 if pid == 1 else 1.0)
        rows.append(r)
    return pd.DataFrame(rows)


def _plans():
    return [GWPlan(gw=g, squad=SQUAD, lineup=[p for p in SQUAD if p not in BENCH], captain=1, vice=2,
                   bench=BENCH, transfers_in=[], transfers_out=[], hits=0, ft_before=1, ft_after=1,
                   bank_after=0.0) for g in GWS]


def test_pipeline_wiring_produces_serialisable_verdicts_with_runway():
    ds = _dataset()
    chips, plan = _assess_chips(ds, _matrix({(1, 6): 9.0}), _plans(),
                                {"wildcard": {"best_gw": 6, "gain": 12.0}},
                                {g: 3.0 for g in GWS}, GWS, load_settings())
    blob = json.loads(json.dumps(_jsonify({"chips": chips, "chip_plan": plan})))
    for c in ("wildcard", "freehit", "bboost", "3xc"):
        assert blob["chips"][c]["runway"] == 16 and blob["chips"][c]["expiry_gw"] == 19
        assert blob["chips"][c]["available"] is True
    assert blob["chips"]["3xc"]["verdict"] == "consider"
    assert blob["chips"]["3xc"]["assigned_gw"] == 6
    assert "Haaland" in blob["chips"]["3xc"]["why"]
    assert blob["chips"]["3xc"]["triggers"]["6"]["standout"] is True
    assert blob["chips"]["wildcard"]["verdict"] == "hold" and blob["chips"]["wildcard"]["gain"] == 12.0
    assert blob["chip_plan"]["endgame"] is False
    assert blob["chip_plan"]["order"] == [{"chip": "3xc", "gw": 6, "value": 9.0}]
    assert blob["chip_plan"]["squad_fit"]["n_fit"] == 15
    assert blob["chip_plan"]["calendar"]["bboost"]["window"] == [1, 19]


def test_pipeline_sees_a_double_beyond_the_horizon_and_holds_for_it():
    ds = _dataset(doubles={18: [1]})                  # MCI double in GW18, out of the 8-GW view
    chips, plan = _assess_chips(ds, _matrix({(1, 6): 9.0}), _plans(), {}, {g: 3.0 for g in GWS},
                                GWS, load_settings())
    assert chips["3xc"]["dgw_in_runway"] == [18]
    assert chips["3xc"]["verdict"] == "hold" and "GW18" in chips["3xc"]["why"]
    assert plan["order"] == []


def test_pipeline_reads_fitness_from_the_matrix_for_the_bench_boost():
    ds = _dataset()
    m = _matrix({(b, g): 2.5 for b in BENCH for g in GWS}, avail={3: 0.25, 7: 0.0})
    chips, plan = _assess_chips(ds, m, _plans(), {}, {g: 3.0 for g in GWS}, GWS, load_settings())
    assert plan["squad_fit"]["n_fit"] == 13 and sorted(plan["squad_fit"]["doubtful"]) == ["P3", "P7"]
    assert chips["bboost"]["verdict"] == "hold" and "13/15 fit" in chips["bboost"]["why"]


def test_pipeline_marks_a_played_chip_used_and_names_when_the_next_unlocks():
    ds = _dataset(played=[{"name": "wildcard", "event": 3}])
    ds.chips_available = ["freehit", "bboost", "3xc"]
    chips, _ = _assess_chips(ds, _matrix(), _plans(), {}, {}, GWS, load_settings())
    assert chips["wildcard"]["verdict"] == "used" and chips["wildcard"]["used_gw"] == 3
    assert "unlocks GW20" in chips["wildcard"]["why"]
