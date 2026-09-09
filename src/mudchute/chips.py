"""Chip strategy: the calendar, the triggers, the sliding bar, the endgame.

FPL hands out two of every chip, one usable in each half of the season, and an
unused first-half chip is simply lost at the half-way line. Nothing here
auto-plays anything: the module turns the calendar, the squad's fitness and the
per-gameweek expected points into a verdict per chip plus a one-chip-per-
gameweek plan, and the report shows the reasoning.

Rules, in priority order:

1. Never let a chip expire. The bar a chip must clear slides down with its
   *pressure runway*: gameweeks left in its window minus the other unplayed
   chips that must also fit in that window, because only one chip can be
   played per gameweek. At a pressure runway of one the bar is zero.
2. A double gameweek anywhere in the window is the thing to hold for, even
   before it comes into the solver's view.
3. Each chip has its own conditions. The bench boost wants a fully fit squad,
   bench points above their own norm and, ideally, the week after a wildcard.
   The triple captain wants the captain's week to be unusually high *for him*.
   The free hit wants a blank, an injury crisis or a big one-week gain, and
   otherwise keeps the longest.
4. Once the whole rest of the window is in view (runway <= horizon) the
   endgame planner assigns each remaining chip to a distinct gameweek to
   maximise the total, which also settles who gets a double when two chips
   want it.

Pure functions over plain dicts, so every rule is unit-testable without the
solver or the API.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import mean
from typing import Any, Callable

CHIPS = ("wildcard", "freehit", "bboost", "3xc")
LABELS = {"wildcard": "Wildcard", "freehit": "Free Hit",
          "bboost": "Bench Boost", "3xc": "Triple Captain"}

DEFAULTS: dict[str, Any] = {
    # xP gain a chip must show while there is plenty of runway
    "bar_start": {"3xc": 8.0, "bboost": 8.0, "freehit": 12.0, "wildcard": 15.0},
    "wc_window": 5,             # GWs over which a wildcard's rebuilt squad is judged
    "ramp": 8,                  # pressure-runway GWs over which the bar slides to zero
    "tc_standout_ratio": 1.25,  # captain's week vs his own typical week
    "bb_min_fit": 15,           # squad members that must be fit for a comfortable BB
    "bb_bench_ratio": 1.15,     # bench xP vs its own norm across the horizon
    "fit_avail": 0.75,          # avail at or above this counts as fit
    "fh_crisis_fit": 11,        # fewer fit players than this = injury crisis
    "fh_blank_players": 4,      # this many starters blanking = free-hit territory
}


def config(overrides: dict | None = None) -> dict[str, Any]:
    """DEFAULTS with any settings.yaml `chips:` block merged over the top."""
    cfg = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update({kk: float(vv) for kk, vv in v.items()})
        elif k in cfg:
            cfg[k] = type(cfg[k])(v)
    return cfg


# ---------------------------------------------------------------- calendar

def chip_windows(bootstrap: dict | None, total_gws: int = 38
                 ) -> dict[str, list[tuple[int, int]]]:
    """Per chip, the (first_gw, last_gw) windows FPL declares in
    bootstrap-static's `chips` list, falling back to two halves when the field
    is missing or malformed."""
    found: dict[str, set[tuple[int, int]]] = {}
    raw = bootstrap.get("chips") if isinstance(bootstrap, dict) else None
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        name, s, e = entry.get("name"), entry.get("start_event"), entry.get("stop_event")
        if name in CHIPS and s and e:
            found.setdefault(name, set()).add((int(s), int(e)))
    mid = total_gws // 2
    return {c: sorted(found[c]) if found.get(c) else [(1, mid), (mid + 1, total_gws)]
            for c in CHIPS}


@dataclass
class ChipState:
    name: str
    window: tuple[int, int]         # the window that governs the next deadline
    available: bool                 # this window's instance is still unplayed
    used_gw: int | None             # GW it was played in this window
    runway: int                     # GWs from the next deadline to the window's end
    expiry_gw: int                  # last GW it can be played
    next_window: tuple[int, int] | None   # when the following instance unlocks
    half: int = 1                   # which instance this is: 1st, 2nd, ... window


def chip_calendar(windows: dict[str, list[tuple[int, int]]],
                  played: list[dict], next_gw: int) -> dict[str, ChipState]:
    """Which instance of each chip governs the next deadline, whether it has
    been used, and how many gameweeks remain before it lapses.

    played: [{name, event}] straight from the entry's chips_played / history.
    """
    out: dict[str, ChipState] = {}
    for c in CHIPS:
        wins = windows[c]
        cur = next((w for w in wins if w[0] <= next_gw <= w[1]), None)
        if cur is None:                              # between windows or past the last
            later = [w for w in wins if w[0] > next_gw]
            cur = later[0] if later else wins[-1]
        used = sorted(int(p["event"]) for p in played
                      if p.get("name") == c and p.get("event") is not None
                      and cur[0] <= int(p["event"]) <= cur[1])
        used_gw = used[0] if used else None
        available = used_gw is None and next_gw <= cur[1]
        out[c] = ChipState(
            name=c, window=cur, available=available, used_gw=used_gw,
            runway=(cur[1] - next_gw + 1) if available else 0, expiry_gw=cur[1],
            next_window=next((w for w in wins if w[0] > cur[1]), None),
            half=wins.index(cur) + 1)
    return out


def pressure_runway(state: ChipState, calendar: dict[str, ChipState]) -> int:
    """Runway minus the OTHER unplayed chips sharing the window: one chip per
    gameweek means three chips with five weeks left really have three spare."""
    rivals = sum(1 for s in calendar.values()
                 if s.available and s.window == state.window and s.name != state.name)
    return state.runway - rivals


def bar(chip: str, pr: int, cfg: dict) -> float:
    """The xP gain a chip must show: its full bar with plenty of pressure
    runway, sliding linearly to zero as that runway reaches one."""
    if pr <= 1:
        return 0.0
    return float(cfg["bar_start"][chip]) * min(1.0, (pr - 1) / max(int(cfg["ramp"]), 1))


# ---------------------------------------------------------------- profiles

def _get(plan: Any, key: str, default: Any = None) -> Any:
    if isinstance(plan, dict):
        return plan.get(key, default)
    return getattr(plan, key, default)


def plan_gw_xp(plan: Any, g: int, xp_at: Callable[[int, int], float]) -> float:
    """A plan's expected points in one GW: the eleven, plus the captain again."""
    return float(sum(xp_at(p, g) for p in (_get(plan, "lineup") or []))
                 + xp_at(_get(plan, "captain"), g))


def value_profiles(plans: list, xp_at: Callable[[int, int], float],
                   fh_gains: dict[int, float],
                   bench_weights: tuple[float, list[float]] | None = None,
                   wc_plans: list | None = None) -> dict[str, dict[int, float]]:
    """What each chip literally adds in each horizon GW, undiscounted.

    3xc: one more multiple of the captain's xP. bboost: the bench's xP, net of
    the share the objective already credits for autosubs when bench_weights
    (gk, [outfield 1-3]) are given. freehit: the solver's single-GW re-pick
    gain. wildcard: the rebuilt squad's extra points over the current plan,
    GW by GW, when the wildcard solve's plans are given.
    """
    prof: dict[str, dict[int, float]] = {"3xc": {}, "bboost": {}, "freehit": {}}
    for p in plans:
        g = int(_get(p, "gw"))
        prof["3xc"][g] = float(xp_at(_get(p, "captain"), g))
        bench = list(_get(p, "bench") or [])
        if bench_weights:
            gk_w, out_w = bench_weights
            ws = [gk_w] + [out_w[i] if i < len(out_w) else 0.0 for i in range(len(bench) - 1)]
            prof["bboost"][g] = float(sum(xp_at(b, g) * (1 - w) for b, w in zip(bench, ws)))
        else:
            prof["bboost"][g] = float(sum(xp_at(b, g) for b in bench))
        if g in fh_gains:
            prof["freehit"][g] = float(fh_gains[g])
    if wc_plans:
        base = {int(_get(p, "gw")): p for p in plans}
        prof["wildcard"] = {}
        for wp in wc_plans:
            g = int(_get(wp, "gw"))
            if g in base:
                prof["wildcard"][g] = plan_gw_xp(wp, g, xp_at) - plan_gw_xp(base[g], g, xp_at)
    return prof


# ---------------------------------------------------------------- triggers

def bb_triggers(g: int, prof_bb: dict[int, float], squad_fit: dict,
                post_wc_gw: int | None, cfg: dict) -> dict:
    """Bench boost conditions for one GW: everyone fit, bench points above
    their own norm, or the week after a wildcard."""
    vals = list(prof_bb.values())
    norm = mean(vals) if vals else 0.0
    ratio = (prof_bb.get(g, 0.0) / norm) if norm > 0 else 1.0
    n_fit, n_squad = int(squad_fit.get("n_fit", 0)), int(squad_fit.get("n_squad", 15))
    t = {
        "n_fit": n_fit, "n_squad": n_squad,
        "fit_ok": n_fit >= min(int(cfg["bb_min_fit"]), n_squad),
        "bench_xp": round(prof_bb.get(g, 0.0), 2), "bench_norm": round(norm, 2),
        "bench_ratio": round(ratio, 3),
        "bench_ok": ratio >= float(cfg["bb_bench_ratio"]),
        "post_wildcard": post_wc_gw is not None and g == post_wc_gw + 1,
        "doubtful": list(squad_fit.get("doubtful", [])),
    }
    t["met"] = bool(t["fit_ok"] and (t["bench_ok"] or t["post_wildcard"]))
    return t


def tc_triggers(g: int, captain: int, xp_at: Callable[[int, int], float],
                gws: list[int], captain_doubles: bool, cfg: dict) -> dict:
    """Triple captain conditions: the captain's week is unusually high for
    him, or he plays twice."""
    own = [float(xp_at(captain, h)) for h in gws]
    norm = mean(own) if own else 0.0
    this = float(xp_at(captain, g))
    ratio = (this / norm) if norm > 0 else 1.0
    t = {
        "captain": int(captain), "xp": round(this, 2), "norm": round(norm, 2),
        "ratio": round(ratio, 3),
        "standout": ratio >= float(cfg["tc_standout_ratio"]),
        "captain_doubles": bool(captain_doubles),
    }
    t["met"] = bool(t["standout"] or t["captain_doubles"])
    return t


def fh_triggers(g: int, next_gw: int, squad_fit: dict, n_blank: int,
                cfg: dict) -> dict:
    """Free hit conditions: a blank for a chunk of the squad, or an injury
    crisis right now (fitness is a today measure, so the crisis test only
    applies to the coming deadline)."""
    n_fit = int(squad_fit.get("n_fit", 15))
    crisis = g == next_gw and n_fit < int(cfg["fh_crisis_fit"])
    blank = int(n_blank) >= int(cfg["fh_blank_players"])
    return {"n_fit": n_fit, "crisis": crisis, "n_blank": int(n_blank),
            "blank": blank, "met": bool(crisis or blank)}


# ---------------------------------------------------------------- endgame

def assign(eligible: dict[str, dict[int, float]], prefer_late: set[str] = frozenset()
           ) -> dict[str, int]:
    """Assign chips to distinct GWs maximising total value; a chip may go
    unassigned. Ties: more chips placed, then later weeks for `prefer_late`
    chips (the free hit keeps its contingency value longest). Brute force: at
    most four chips over a handful of weeks."""
    chips = list(eligible)
    best: dict[str, int] = {}
    best_key: tuple = (float("-inf"), -1, -1)

    def key(cur: dict[str, int]) -> tuple:
        total = sum(eligible[c][g] for c, g in cur.items())
        late = sum(g for c, g in cur.items() if c in prefer_late)
        return (round(total, 6), len(cur), late)

    def rec(i: int, used: frozenset, cur: dict[str, int]) -> None:
        nonlocal best, best_key
        if i == len(chips):
            k = key(cur)
            if k > best_key:
                best, best_key = dict(cur), k
            return
        c = chips[i]
        for g in sorted(eligible[c]):
            if g in used:
                continue
            cur[c] = g
            rec(i + 1, used | {g}, cur)
            del cur[c]
        rec(i + 1, used, cur)

    rec(0, frozenset(), {})
    return best


# ---------------------------------------------------------------- assess

def _fmt_gws(gws: list[int]) -> str:
    return ", ".join(f"GW{g}" for g in gws)


def _fmt_span(gws: list) -> str:
    gws = [int(g) for g in gws if g is not None]
    if not gws:
        return ""
    return f"GW{gws[0]}" if len(gws) == 1 else f"GW{gws[0]}–{gws[-1]}"


def assess(calendar: dict[str, ChipState], profiles: dict[str, dict[int, float]],
           solver: dict[str, dict], plans: list, xp_at: Callable[[int, int], float],
           team_of: dict[int, str], squad_fit: dict, schedule: dict[int, dict],
           horizon_gws: list[int], next_gw: int, names: dict[int, str] | None = None,
           breaks: list[dict] | None = None, cfg: dict | None = None,
           ) -> tuple[dict[str, dict], dict]:
    """Verdict per chip plus the one-chip-per-gameweek plan.

    solver:   {chip: {best_gw, gain[, approx]}} from the chip solves (optional).
    plans:    the baseline GWPlans (gw, squad, captain, bench).
    schedule: {gw: {"doubles": [team_short], "blanks": [team_short]}} for every
              GW from next_gw to the furthest expiry.
    squad_fit: {"n_fit", "n_squad", "doubtful": [names]} for the current 15.
    """
    cfg = cfg or config()
    names = names or {}
    horizon = len(horizon_gws)
    by_gw = {int(_get(p, "gw")): p for p in plans}
    post_wc_gw = calendar["wildcard"].used_gw
    label = lambda pid: names.get(pid, str(pid))

    out: dict[str, dict] = {}
    eligible: dict[str, dict[int, float]] = {}
    endgame_any = False

    for c in CHIPS:
        st = calendar[c]
        base = {
            "label": LABELS[c], "window": list(st.window), "expiry_gw": st.expiry_gw,
            "half": st.half, "available": st.available,
            "used_gw": st.used_gw, "runway": st.runway,
            "next_window": list(st.next_window) if st.next_window else None,
        }
        sol = solver.get(c) or {}
        if not st.available:
            out[c] = {**base, "verdict": "used", "best_gw": None, "gain": None,
                      "why": (f"played in GW{st.used_gw}" if st.used_gw else
                              "not available in this window")
                      + (f"; the second-half {LABELS[c].lower()} unlocks GW"
                         f"{st.next_window[0]}" if st.next_window else "")}
            continue

        pr = pressure_runway(st, calendar)
        b = bar(c, pr, cfg)
        endgame = st.runway <= horizon
        endgame_any = endgame_any or endgame
        window_gws = list(range(next_gw, st.expiry_gw + 1))
        visible = [g for g in horizon_gws if g <= st.expiry_gw]
        dgw_runway = [g for g in window_gws if schedule.get(g, {}).get("doubles")]
        bgw_runway = [g for g in window_gws if schedule.get(g, {}).get("blanks")]
        dgw_unseen = [g for g in dgw_runway if g not in visible]

        prof = dict(profiles.get(c, {}))
        triggers: dict[int, dict] = {}
        elig: dict[int, float] = {}
        extra: dict = {}

        if c == "wildcard":
            # A wildcard is a medium-term call: it is judged on what the rebuilt
            # squad adds over its next `wc_window` weeks from the week it is
            # played (the solver's spot), undiscounted. Without the rebuilt
            # plans, the solver's horizon-wide number stands in.
            solver_gain = float(sol.get("gain") or 0.0)
            bg = sol.get("best_gw")
            window = max(int(cfg["wc_window"]), 1)
            if prof:
                start = (bg if bg in visible else
                         next((g for g in visible if prof.get(g, 0.0) > 0), None))
                win_gws = [g for g in visible if g >= start][:window] if start is not None else []
                value = sum(prof.get(g, 0.0) for g in win_gws)
                horizon_gain = sum(prof.get(g, 0.0) for g in visible)
            else:
                start = bg
                win_gws = [int(bg)] if bg is not None else []
                value = horizon_gain = solver_gain
            if endgame:
                elig = {g: max(value, 0.0) for g in visible}
            elif start is not None and value >= b:
                elig = {int(start): value}
            extra = {"gain": round(value, 2), "gain_gw": start, "gain_window": win_gws,
                     "horizon_gain": round(horizon_gain, 2), "window_len": window}
        else:
            for g in visible:
                p = by_gw.get(g)
                if p is None or g not in prof:
                    continue
                if c == "bboost":
                    t = bb_triggers(g, prof, squad_fit, post_wc_gw, cfg)
                elif c == "3xc":
                    cap = int(_get(p, "captain"))
                    t = tc_triggers(g, cap, xp_at, horizon_gws,
                                    team_of.get(cap) in schedule.get(g, {}).get("doubles", []),
                                    cfg)
                    t["captain_name"] = label(cap)
                else:
                    blanks = set(schedule.get(g, {}).get("blanks", []))
                    starters = _get(p, "lineup") or _get(p, "squad") or []
                    n_blank = sum(1 for pid in starters if team_of.get(pid) in blanks)
                    t = fh_triggers(g, next_gw, squad_fit, n_blank, cfg)
                triggers[g] = t
                v = prof[g]
                if endgame:
                    elig[g] = max(v, 0.0)
                elif c == "freehit":
                    # A blank or an injury crisis is the trigger in itself; a big
                    # one-week gain clears the bar on its own.
                    if v > 0 and (v >= b or t["met"]):
                        elig[g] = v
                elif v >= b and t["met"]:
                    elig[g] = v
        # A double still out of the solver's view is worth waiting for: no
        # chip is spent before it unless the endgame forces the issue.
        if dgw_unseen and not endgame:
            elig = {}
        eligible[c] = elig

        best_prof_gw = max(prof, key=prof.get) if prof else None
        out[c] = {
            **base, "pressure_runway": pr, "bar": round(b, 2),
            "bar_start": float(cfg["bar_start"][c]), "endgame": endgame,
            "best_gw": sol.get("best_gw", best_prof_gw),
            "solver_gain": sol.get("gain"), "gain": None, "gain_gw": None, **extra,
            **({"approx": True} if sol.get("approx") else {}),
            "profile": {int(g): round(v, 2) for g, v in prof.items()},
            "profile_best_gw": best_prof_gw,
            "triggers": {int(g): t for g, t in triggers.items()},
            "dgw_in_runway": dgw_runway, "bgw_in_runway": bgw_runway,
        }

    chosen = assign(eligible, prefer_late={"freehit"}) if eligible else {}

    # ---- verdicts + reasons ----
    for c, info in out.items():
        if info.get("verdict") == "used":
            continue
        st = calendar[c]
        g = chosen.get(c)
        info["assigned_gw"] = g
        t = info["triggers"].get(g) if g is not None else None
        if c != "wildcard":
            gg = g if g is not None else info.get("profile_best_gw")
            info["gain_gw"] = gg
            info["gain"] = info["profile"].get(gg) if gg is not None else None
        why: list[str] = []
        if g is not None:
            info["verdict"] = ("play" if g == next_gw else
                               "expiring" if info["endgame"] else "consider")
            if c == "3xc" and t:
                why.append(f"{t.get('captain_name', 'the captain')} projects "
                           f"{t['xp']:.1f} in GW{g}, {t['ratio']:.2f}x his usual week"
                           + (" and he plays twice" if t["captain_doubles"] else ""))
            elif c == "bboost" and t:
                why.append(f"bench worth {t['bench_xp']:.1f} in GW{g} "
                           f"({t['bench_ratio']:.2f}x its norm), {t['n_fit']}/{t['n_squad']} fit"
                           + (", the week after your wildcard" if t["post_wildcard"] else ""))
            elif c == "freehit" and t:
                if t["crisis"]:
                    why.append(f"only {t['n_fit']} fit players: a free hit rebuilds the week")
                elif t["blank"]:
                    why.append(f"{t['n_blank']} of your starters blank in GW{g}")
                else:
                    why.append(f"a one-week re-pick adds {info['profile'].get(g, 0):.1f} in GW{g}")
            elif c == "wildcard":
                why.append(f"the rebuilt squad adds +{info['gain'] or 0:.1f} over "
                           f"{_fmt_span(info.get('gain_window') or [g])}")
            if info["endgame"]:
                why.append(f"must be played by GW{st.expiry_gw} or it is lost; "
                           f"this is its slot in the one-chip-per-week plan")
            elif g == next_gw:
                why.append("clears the bar this week")
        else:
            info["verdict"] = "hold"
            if info.get("endgame"):
                info["verdict"] = "expiring"
                why.append(f"no week left for it before GW{st.expiry_gw}: "
                           f"the other chips take the remaining slots")
            elif eligible.get(c):
                lost = sorted(eligible[c])
                winners = {chosen_c: cg for chosen_c, cg in chosen.items()
                           if cg in lost and chosen_c != c}
                if winners:
                    wc_, wg_ = min(winners.items(), key=lambda kv: kv[1])
                    why.append(f"GW{wg_} goes to the {LABELS[wc_].lower()} "
                               f"(one chip per week); nothing else in view "
                               f"clears the bar")
                else:
                    why.append(f"{_fmt_gws(lost[:2])} clear(s) the bar but no "
                               f"slot is free")
            elif info["dgw_in_runway"] and not any(
                    x in info["dgw_in_runway"] for x in horizon_gws):
                why.append(f"double gameweek at {_fmt_gws(info['dgw_in_runway'][:2])} "
                           f"is still in this half; hold for it")
            elif info["dgw_in_runway"]:
                why.append(f"a double at {_fmt_gws(info['dgw_in_runway'][:2])} is in view "
                           f"but the gain there is ordinary")
            else:
                pv = info["profile"]
                bp = info.get("profile_best_gw")
                bt = info["triggers"].get(bp) if bp is not None else None
                if c == "wildcard":
                    why.append("a wildcard always shows a gain on paper; it earns "
                               "its burn when the squad needs surgery or at a "
                               "classic window")
                    if info.get("gain") is not None:
                        span = _fmt_span(info.get("gain_window") or [info.get("gain_gw")])
                        why.append(f"+{info['gain']:.1f}{(' over ' + span) if span else ''} "
                                   f"against a {info['bar']:.1f} bar")
                    if breaks:
                        why.append(f"the {breaks[0]['label']} after "
                                   f"GW{breaks[0]['after_gw']} is the next one")
                elif bp is not None and pv.get(bp, 0) < info["bar"]:
                    why.append(f"best week in view is GW{bp} at {pv[bp]:.1f}, "
                               f"under the {info['bar']:.1f} bar")
                elif bt and not bt.get("met"):
                    if c == "3xc":
                        why.append(f"{bt.get('captain_name', 'the captain')}'s best week "
                                   f"in view is only {bt['ratio']:.2f}x his norm; "
                                   f"waiting for a spike")
                    elif c == "bboost":
                        if not bt["fit_ok"]:
                            dl = ", ".join(bt["doubtful"][:3]) or "a squad member"
                            why.append(f"{bt['n_fit']}/{bt['n_squad']} fit ({dl}); "
                                       f"wants a fully fit 15")
                        else:
                            why.append(f"bench is {bt['bench_ratio']:.2f}x its norm; "
                                       f"wants a stronger bench week or the week "
                                       f"after a wildcard")
                    else:
                        why.append("no blank or injury crisis; the free hit keeps "
                                   "its contingency value longest")
                why.append(f"{st.runway} weeks of runway to GW{st.expiry_gw}; "
                           f"the bar is {info['bar']:.1f} and falls as it shortens")
        info["why"] = "; ".join(why)

    order = sorted(({"chip": c, "gw": g, "value": round(eligible[c][g], 2)}
                    for c, g in chosen.items()), key=lambda r: r["gw"])
    avail = [c for c in CHIPS if calendar[c].available]
    unassigned = [c for c in avail if c not in chosen]
    plan = {
        "endgame": endgame_any,
        "order": order,
        "unassigned": unassigned,
        "expiry_gw": max((calendar[c].expiry_gw for c in avail), default=None),
        "note": (
            f"Endgame: {len(avail)} chip(s) must be played by GW"
            f"{max(calendar[c].expiry_gw for c in avail)}, one per week"
            if endgame_any and avail else
            "One chip per gameweek; the bar each must clear falls as its window closes"),
    }
    return out, plan


def calendar_json(calendar: dict[str, ChipState]) -> dict[str, dict]:
    return {c: asdict(s) for c, s in calendar.items()}
