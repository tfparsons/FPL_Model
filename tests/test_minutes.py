"""The minutes model's two in-season learners: last season's prior fades as
the club's games mount (the rate itself stays weighted to the last six), and
minutes per start blend this season's starts with last season's pattern."""
import math

import pandas as pd

from mudchute.data import Dataset
from mudchute.logs import recent_form
from mudchute.minutes import build_minutes


def _dataset(players, fixtures=None):
    teams = pd.DataFrame({"id": [1, 2], "short_name": ["AAA", "BBB"], "name": ["A", "B"]})
    if fixtures is None:
        fixtures = pd.DataFrame(columns=["id", "team_h", "team_a", "started", "finished", "kickoff_time"])
    events = pd.DataFrame([{"id": 1, "finished": True, "data_checked": True}])
    return Dataset(players=pd.DataFrame(players), teams=teams, fixtures=fixtures, events=events,
                   squad=pd.DataFrame(), bank=0.0, free_transfers=1, confirmed_transfers=[],
                   total_points=0, entry_name="T", max_ft=5, next_gw=2, chips_available=[],
                   picks_gw=1, fetched_at="now")


def _player(pid, pos="FWD", cost=60, starts=0, team=1):
    return {"id": pid, "code": pid, "web_name": f"P{pid}", "team": team, "pos": pos,
            "status": "a", "chance_of_playing_next_round": None, "now_cost": cost,
            "starts": starts, "minutes": 0}


def _rates(code, start_rate, mins=1500, mps=85.0):
    return {"code": code, "mins_last": mins, "starts_last": int(start_rate * 38),
            "start_rate_last": start_rate, "late_start_rate_last": start_rate,
            "sub_apps_last": 5, "mins_per_start_last": mps, "p60_given_start_last": 0.9}


def _form(pid, club_games, win_rate=1.0, win_mps=float("nan"), win_starts=0):
    return {"id": pid, "win_rate": win_rate, "win_n": 3.69, "win_mps": win_mps,
            "win_starts": win_starts, "absent_streak": 0, "returning": False,
            "return_games": 0, "club_games": club_games}


def _p_start(club_games, start_rate_last=0.3):
    ds = _dataset([_player(1, starts=club_games)])
    out = build_minutes(ds, pd.DataFrame([_rates(1, start_rate_last, mins=1200)]),
                        form=pd.DataFrame([_form(1, club_games)]))
    return float(out.set_index("id").at[1, "p_start"])


def test_last_seasons_prior_fades_as_the_seasons_games_mount():
    # Last season's backup (starts 30%) who has started every game this season.
    p6, p12, p25 = _p_start(6), _p_start(12), _p_start(25)
    assert p6 < p12 < p25                      # the six-game cap used to freeze this
    assert p6 < 0.90 and p25 > 0.94
    assert p25 <= 0.97


def test_a_regular_who_has_lost_his_place_is_still_read_from_the_window():
    # Nailed last season, benched for the whole recent window: the fade must
    # not resurrect him — the window rate still drives the number.
    ds = _dataset([_player(1, starts=14)])
    out = build_minutes(ds, pd.DataFrame([_rates(1, 0.95, mins=3000)]),
                        form=pd.DataFrame([_form(1, 20, win_rate=0.0)]))
    assert float(out.set_index("id").at[1, "p_start"]) < 0.3


def _xmins(win_mps, win_starts, mps_last=85.0, pos="FWD"):
    ds = _dataset([_player(1, pos=pos, starts=6)])
    out = build_minutes(ds, pd.DataFrame([_rates(1, 0.9, mps=mps_last)]),
                        form=pd.DataFrame([_form(1, 6, win_mps=win_mps, win_starts=win_starts)]))
    r = out.set_index("id").loc[1]
    # back out minutes per start from xmins = p_start·mps + (p_appear − p_start)·18
    return (float(r["xmins"]) - (float(r["p_appear"]) - float(r["p_start"])) * 18) / float(r["p_start"])


def test_minutes_per_start_blend_toward_the_window_one_start_at_a_time():
    no_window = _xmins(float("nan"), 0)
    one_start = _xmins(65.0, 1)
    five_starts = _xmins(65.0, 5)
    assert math.isclose(no_window, 85.0, abs_tol=0.01)
    assert math.isclose(one_start, (65 + 5 * 85) / 6, abs_tol=0.01)       # 81.7
    assert math.isclose(five_starts, (5 * 65 + 5 * 85) / 10, abs_tol=0.01)  # 75.0
    assert five_starts < one_start < no_window


def test_keepers_stay_at_ninety_whatever_the_window_says():
    assert math.isclose(_xmins(70.0, 5, pos="GKP"), 90.0, abs_tol=0.01)


def test_recent_form_reads_minutes_per_start_from_the_windows_starts():
    fixtures = pd.DataFrame([{"id": f, "team_h": 1, "team_a": 2, "started": True, "finished": True,
                              "kickoff_time": f"2026-08-{10 + f:02d}T14:00:00Z"} for f in range(1, 9)])
    ds = _dataset([_player(1), _player(2)], fixtures)
    logs = pd.DataFrame([  # player 1: started the last three (latest first: 60, 90, 90), a sub before
        {"id": 1, "gw": 8, "fixture": 8, "team": 1, "minutes": 60, "started": 1},
        {"id": 1, "gw": 7, "fixture": 7, "team": 1, "minutes": 90, "started": 1},
        {"id": 1, "gw": 6, "fixture": 6, "team": 1, "minutes": 90, "started": 1},
        {"id": 1, "gw": 5, "fixture": 5, "team": 1, "minutes": 20, "started": 0},
        {"id": 2, "gw": 8, "fixture": 8, "team": 1, "minutes": 15, "started": 0},
    ])
    form = recent_form(ds, logs).set_index("id")
    assert form.at[1, "win_starts"] == 3
    assert math.isclose(form.at[1, "win_mps"], (60 + 0.8 * 90 + 0.64 * 90) / 2.44, abs_tol=0.01)
    assert form.at[2, "win_starts"] == 0 and math.isnan(form.at[2, "win_mps"])
