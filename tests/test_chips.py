"""Chip strategy: the calendar, the sliding bar, the triggers, the endgame."""
from mudchute.chips import (CHIPS, assess, assign, bar, bb_triggers, chip_calendar,
                            chip_windows, config, fh_triggers, pressure_runway,
                            tc_triggers, value_profiles)

BOOT = {"chips": [
    {"name": "wildcard", "start_event": 1, "stop_event": 19},
    {"name": "wildcard", "start_event": 20, "stop_event": 38},
    {"name": "freehit", "start_event": 1, "stop_event": 19},
    {"name": "freehit", "start_event": 20, "stop_event": 38},
    {"name": "bboost", "start_event": 1, "stop_event": 19},
    {"name": "bboost", "start_event": 20, "stop_event": 38},
    {"name": "3xc", "start_event": 1, "stop_event": 19},
    {"name": "3xc", "start_event": 20, "stop_event": 38},
]}


def _cal(next_gw, played=(), boot=BOOT):
    return chip_calendar(chip_windows(boot), list(played), next_gw)


# ------------------------------------------------------------- calendar

def test_windows_read_from_bootstrap_and_fall_back_to_halves():
    w = chip_windows(BOOT)
    assert w["bboost"] == [(1, 19), (20, 38)]
    assert chip_windows({}) == {c: [(1, 19), (20, 38)] for c in CHIPS}
    assert chip_windows(None)["3xc"] == [(1, 19), (20, 38)]
    # malformed rows and duplicates are ignored / collapsed
    w = chip_windows({"chips": [{"name": "3xc", "start_event": 1, "stop_event": 19},
                                {"name": "3xc", "start_event": 1, "stop_event": 19},
                                {"name": "nope", "start_event": 1, "stop_event": 5},
                                "garbage"]})
    assert w["3xc"] == [(1, 19)]


def test_first_half_all_available_with_runway_to_gw19():
    cal = _cal(4)
    for c in CHIPS:
        assert cal[c].available and cal[c].runway == 16 and cal[c].expiry_gw == 19
        assert cal[c].next_window == (20, 38) and cal[c].half == 1


def test_half_is_the_windows_position_not_whether_it_starts_at_gw1():
    # The live API opens the wildcard and free hit at GW2 (nothing to wildcard
    # before the first deadline); that is still the first-half instance.
    boot = {"chips": [{"name": "wildcard", "start_event": 2, "stop_event": 19},
                      {"name": "wildcard", "start_event": 20, "stop_event": 38}]}
    cal = _cal(4, boot=boot)
    assert cal["wildcard"].window == (2, 19) and cal["wildcard"].half == 1
    assert _cal(25, boot=boot)["wildcard"].half == 2
    chips, _ = _world(4)
    assert all(chips[c]["half"] == 1 for c in CHIPS)


def test_chip_played_this_half_is_spent_but_second_instance_survives():
    cal = _cal(4, played=[{"name": "wildcard", "event": 3}])
    assert not cal["wildcard"].available and cal["wildcard"].used_gw == 3
    assert cal["wildcard"].runway == 0
    assert cal["bboost"].available
    # in the second half the GW3 wildcard no longer counts
    cal2 = _cal(22, played=[{"name": "wildcard", "event": 3}])
    assert cal2["wildcard"].available and cal2["wildcard"].window == (20, 38)
    assert cal2["wildcard"].runway == 17 and cal2["wildcard"].next_window is None
    cal3 = _cal(22, played=[{"name": "wildcard", "event": 3}, {"name": "wildcard", "event": 21}])
    assert not cal3["wildcard"].available and cal3["wildcard"].used_gw == 21


def test_pressure_runway_counts_the_other_chips_that_need_a_week():
    cal = _cal(15)                                  # runway 5, four chips unplayed
    assert pressure_runway(cal["3xc"], cal) == 2    # 5 - 3 rivals
    cal = _cal(15, played=[{"name": "wildcard", "event": 6},
                           {"name": "freehit", "event": 9}])
    assert pressure_runway(cal["3xc"], cal) == 4    # 5 - 1 rival


def test_bar_slides_to_zero_as_the_window_closes():
    cfg = config()
    assert bar("3xc", 20, cfg) == 8.0               # plenty of runway: full bar
    assert bar("3xc", 9, cfg) == 8.0
    assert abs(bar("3xc", 5, cfg) - 4.0) < 1e-9     # half way down the ramp
    assert bar("3xc", 1, cfg) == 0.0                # last usable week: anything goes
    assert bar("3xc", 0, cfg) == 0.0
    assert bar("freehit", 20, cfg) > bar("bboost", 20, cfg)   # FH keeps longest


def test_config_merges_yaml_overrides_over_defaults():
    cfg = config({"bar_start": {"3xc": 5}, "ramp": "6", "tc_standout_ratio": 1.4})
    assert cfg["bar_start"]["3xc"] == 5.0 and cfg["bar_start"]["freehit"] == 12.0
    assert cfg["ramp"] == 6 and cfg["tc_standout_ratio"] == 1.4


# ------------------------------------------------------------- profiles + triggers

def test_value_profiles_are_what_each_chip_literally_adds():
    plans = [{"gw": 4, "captain": 1, "bench": [10, 11, 12, 13], "squad": []},
             {"gw": 5, "captain": 2, "bench": [10, 11, 12, 13], "squad": []}]
    xp = {(1, 4): 6.0, (2, 5): 5.5, (10, 4): 1.0, (11, 4): 1.5, (12, 4): 0.5,
          (13, 4): 2.0, (10, 5): 1.0, (11, 5): 1.0, (12, 5): 1.0, (13, 5): 1.0}
    prof = value_profiles(plans, lambda p, g: xp.get((p, g), 0.0), {4: 3.0})
    assert prof["3xc"] == {4: 6.0, 5: 5.5}
    assert prof["bboost"] == {4: 5.0, 5: 4.0}
    assert prof["freehit"] == {4: 3.0}


def test_bench_boost_wants_a_fit_15_and_a_bench_above_its_norm():
    cfg = config()
    prof = {4: 4.0, 5: 4.0, 6: 6.0, 7: 4.0}          # norm 4.5; GW6 = 1.33x
    fit = {"n_fit": 15, "n_squad": 15, "doubtful": []}
    assert bb_triggers(6, prof, fit, None, cfg)["met"]
    assert not bb_triggers(4, prof, fit, None, cfg)["met"]          # ordinary bench week
    t = bb_triggers(6, prof, {"n_fit": 13, "n_squad": 15, "doubtful": ["A", "B"]}, None, cfg)
    assert not t["met"] and not t["fit_ok"] and t["doubtful"] == ["A", "B"]
    # the week after a wildcard is a candidate even with an ordinary bench
    t = bb_triggers(5, prof, fit, 4, cfg)
    assert t["post_wildcard"] and t["met"]


def test_triple_captain_wants_a_week_unusually_high_for_him_or_a_double():
    cfg = config()
    xp = {(1, g): 6.0 for g in range(4, 12)}
    xp[(1, 6)] = 9.0                                  # 1.41x his norm
    at = lambda p, g: xp[(p, g)]
    assert tc_triggers(6, 1, at, list(range(4, 12)), False, cfg)["met"]
    assert not tc_triggers(5, 1, at, list(range(4, 12)), False, cfg)["met"]
    assert tc_triggers(5, 1, at, list(range(4, 12)), True, cfg)["met"]     # double trumps


def test_free_hit_wants_a_blank_or_a_crisis_and_the_crisis_is_a_today_test():
    cfg = config()
    assert fh_triggers(7, 4, {"n_fit": 15}, 5, cfg)["blank"]
    assert not fh_triggers(7, 4, {"n_fit": 15}, 2, cfg)["met"]
    assert fh_triggers(4, 4, {"n_fit": 9}, 0, cfg)["crisis"]
    assert not fh_triggers(5, 4, {"n_fit": 9}, 0, cfg)["crisis"]  # can't know next week's injuries


# ------------------------------------------------------------- assignment

def test_assign_gives_each_chip_a_distinct_week_and_settles_a_double():
    got = assign({"3xc": {12: 12.0}, "bboost": {12: 10.0}})
    assert got == {"3xc": 12}                         # one week between them: the higher value takes it
    got = assign({"3xc": {12: 12.0, 13: 6.0}, "bboost": {12: 10.0}})
    assert got == {"3xc": 13, "bboost": 12}           # 16 beats 12: playing both beats one on the double
    assert assign({"3xc": {}, "bboost": {}}) == {}


def test_assign_prefers_more_chips_placed_then_a_later_free_hit():
    got = assign({"freehit": {17: 2.0, 19: 2.0}, "bboost": {17: 2.0}}, prefer_late={"freehit"})
    assert got == {"freehit": 19, "bboost": 17}
    got = assign({"freehit": {17: 1.0, 18: 1.0}}, prefer_late={"freehit"})
    assert got == {"freehit": 18}


# ------------------------------------------------------------- assess: end to end

def _world(next_gw, horizon=8, captain=1, cap_team="MCI", bench=(10, 11, 12, 13),
           squad=None, xp=None, fh=None, doubles=None, blanks=None, n_fit=15,
           played=(), solver=None, doubtful=()):
    gws = list(range(next_gw, next_gw + horizon))
    squad = squad or [captain, 2, 3, 4, 5, 6, 7, 8, 9, 14, 15] + list(bench)
    lineup = [p for p in squad if p not in bench]
    plans = [{"gw": g, "captain": captain, "bench": list(bench), "squad": squad,
              "lineup": lineup} for g in gws]
    xp = xp or {}
    xp_at = lambda p, g: xp.get((p, g), 6.0 if p == captain else 1.0)
    fh = fh if fh is not None else {g: 3.0 for g in gws}
    cal = _cal(next_gw, played)
    top = max(s.expiry_gw for s in cal.values())
    sched = {g: {"doubles": list((doubles or {}).get(g, [])),
                 "blanks": list((blanks or {}).get(g, []))}
             for g in range(next_gw, max(top, gws[-1]) + 1)}
    team_of = {p: "OTH" for p in squad}
    team_of[captain] = cap_team
    prof = value_profiles(plans, xp_at, fh)
    return assess(cal, prof, solver or {}, plans, xp_at, team_of,
                  {"n_fit": n_fit, "n_squad": 15, "doubtful": list(doubtful)},
                  sched, gws, next_gw, names={captain: "Haaland"},
                  breaks=[{"after_gw": 5, "label": "international break"}])


def test_early_in_the_half_everything_holds_with_runway_in_the_reason():
    chips, plan = _world(4)
    assert not plan["endgame"] and plan["order"] == []
    for c in CHIPS:
        assert chips[c]["verdict"] == "hold", c
        assert chips[c]["runway"] == 16 and chips[c]["expiry_gw"] == 19
        assert "runway" in chips[c]["why"]
    assert chips["bboost"]["bar"] == 8.0 and chips["freehit"]["bar"] == 12.0
    assert "international break" in chips["wildcard"]["why"]


def test_a_captain_spike_in_view_is_a_consider_and_a_weak_bench_holds():
    xp = {(1, 6): 9.0}                                # Haaland 1.41x his norm in GW6
    chips, plan = _world(4, xp=xp, n_fit=13, doubtful=("Saka", "Rice"))
    assert chips["3xc"]["verdict"] == "consider" and chips["3xc"]["assigned_gw"] == 6
    assert "Haaland" in chips["3xc"]["why"] and "GW6" in chips["3xc"]["why"]
    assert chips["bboost"]["verdict"] == "hold"
    assert plan["order"] == [{"chip": "3xc", "gw": 6, "value": 9.0}]


def test_a_bench_boost_with_two_doubtful_holds_and_says_so():
    # bench worth 10 (>= bar) and 1.0x its norm, but only 13 fit
    xp = {(b, g): 2.5 for b in (10, 11, 12, 13) for g in range(4, 12)}
    chips, _ = _world(4, xp=xp, n_fit=13, doubtful=("Saka", "Rice"))
    assert chips["bboost"]["verdict"] == "hold"
    assert "13/15 fit" in chips["bboost"]["why"] and "Saka" in chips["bboost"]["why"]


def test_a_double_still_out_of_view_holds_every_chip_even_over_a_spike():
    xp = {(1, 6): 9.0}
    chips, plan = _world(4, xp=xp, doubles={18: ["MCI", "ARS"]})
    assert chips["3xc"]["verdict"] == "hold"
    assert "GW18" in chips["3xc"]["why"] and "hold for it" in chips["3xc"]["why"]
    assert chips["3xc"]["dgw_in_runway"] == [18]
    assert plan["order"] == []


def test_a_double_in_view_goes_to_one_chip_and_the_loser_is_told_why():
    # GW12 double for MCI: the captain projects 12, two MCI bench players double too
    xp = {(1, 12): 12.0, (10, 12): 4.0, (11, 12): 4.0}
    chips, plan = _world(10, xp=xp, doubles={12: ["MCI"]})
    # runway 10 > horizon 8: not the endgame; the bar is 8 * 6/8 = 6.0
    assert not plan["endgame"] and chips["3xc"]["bar"] == 6.0
    assert chips["3xc"]["verdict"] == "consider" and chips["3xc"]["assigned_gw"] == 12
    assert chips["3xc"]["triggers"][12]["captain_doubles"]
    assert chips["bboost"]["assigned_gw"] is None and chips["bboost"]["verdict"] == "hold"
    assert "goes to the triple captain" in chips["bboost"]["why"]


def test_endgame_gives_each_chip_its_own_week_and_plays_the_first_now():
    xp = {(1, 17): 6.0, (1, 18): 7.5, (1, 19): 6.5,
          (10, 17): 2.0, (10, 18): 1.0, (10, 19): 0.0}   # bench 5 / 4 / 3
    fh = {17: 2.0, 18: 3.0, 19: 2.5}
    chips, plan = _world(17, xp=xp, fh=fh, played=[{"name": "wildcard", "event": 6}])
    assert plan["endgame"] and plan["expiry_gw"] == 19
    assert [(r["chip"], r["gw"]) for r in plan["order"]] == \
        [("bboost", 17), ("3xc", 18), ("freehit", 19)]
    assert chips["bboost"]["verdict"] == "play"
    assert chips["3xc"]["verdict"] == "expiring" and chips["freehit"]["verdict"] == "expiring"
    assert "GW19 or it is lost" in chips["3xc"]["why"]
    assert chips["wildcard"]["verdict"] == "used" and "GW6" in chips["wildcard"]["why"]
    assert "unlocks GW20" in chips["wildcard"]["why"]
    assert plan["unassigned"] == []


def test_more_chips_than_weeks_left_flags_the_one_that_will_lapse():
    xp = {(1, 17): 6.0, (1, 18): 7.5, (1, 19): 6.5,
          (10, 17): 2.0, (10, 18): 1.0, (10, 19): 0.0}
    fh = {17: 2.0, 18: 3.0, 19: 2.5}
    chips, plan = _world(17, xp=xp, fh=fh, solver={"wildcard": {"best_gw": 17, "gain": 5.0}})
    assert [(r["chip"], r["gw"]) for r in plan["order"]] == \
        [("bboost", 17), ("3xc", 18), ("wildcard", 19)]
    assert plan["unassigned"] == ["freehit"]
    assert chips["freehit"]["verdict"] == "expiring"
    assert "no week left" in chips["freehit"]["why"]


def test_the_last_week_of_the_window_plays_whatever_is_left():
    played = [{"name": "wildcard", "event": 6}, {"name": "freehit", "event": 9},
              {"name": "bboost", "event": 12}]
    xp = {(1, 19): 0.5}
    chips, plan = _world(19, xp=xp, played=played)
    assert chips["3xc"]["runway"] == 1 and chips["3xc"]["bar"] == 0.0
    assert chips["3xc"]["verdict"] == "play" and chips["3xc"]["assigned_gw"] == 19
    assert plan["order"] == [{"chip": "3xc", "gw": 19, "value": 0.5}]


def test_injury_crisis_now_makes_the_free_hit_a_play():
    fh = {g: 4.0 for g in range(4, 12)}
    chips, _ = _world(4, fh=fh, n_fit=9, doubtful=tuple("ABCDEF"))
    assert chips["freehit"]["verdict"] == "play"
    assert "only 9 fit" in chips["freehit"]["why"]


def test_a_blank_in_view_for_a_chunk_of_the_squad_is_free_hit_territory():
    fh = {g: 4.0 for g in range(4, 12)}
    blanks = {8: ["OTH"]}                            # 14 of the 15 are OTH
    chips, _ = _world(4, fh=fh, blanks=blanks)
    assert chips["freehit"]["assigned_gw"] == 8 and chips["freehit"]["verdict"] == "consider"
    assert "blank in GW8" in chips["freehit"]["why"]


def test_second_half_instance_is_fresh_even_after_a_first_half_burn():
    played = [{"name": "3xc", "event": 9}, {"name": "bboost", "event": 12}]
    chips, _ = _world(22, played=played)
    assert chips["3xc"]["available"] and chips["3xc"]["window"] == [20, 38]
    assert chips["3xc"]["half"] == 2 and chips["3xc"]["runway"] == 17
    assert chips["3xc"]["verdict"] == "hold"
