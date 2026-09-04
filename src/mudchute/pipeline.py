"""Orchestration: xP -> pool -> solves -> robustness -> chips -> timing -> plan.json."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from .changelog import MODEL_VERSION
from .config import PROCESSED, Settings, load_settings
from .data import Dataset, load_dataset
from .logs import build_game_logs, recent_form
from .moves import detect_club_moves
from .robustness import run_robustness
from .schedule import (fmt_uk, gw_breaks, gw_schedule, horizon_notes,
                       rerun_guidance, schedule_changes, unscheduled_fixtures)
from .solver import solve_plan, solve_single_gw
from .xp import build_xp, write_outputs

PRICE_RISE_IMMINENT = 90.0  # projected_percent at offset 0/1 treated as "about to rise"
WC_CONSIDER_GAIN = 20.0     # decayed xP a wildcard must show before "consider"
CHIP_CONSIDER_GAIN = 8.0    # ditto for BB/TC/FH (and only with a double in sight)


def build_pool(matrix: pd.DataFrame, ds: Dataset, settings: Settings) -> pd.DataFrame:
    """Solver player pool: current squad + top xP per position + cheap enablers."""
    parts = [matrix[matrix["id"].isin(ds.squad["id"])]]
    for pos, n in settings.pool_sizes.items():
        by_pos = matrix[matrix["pos"] == pos]
        parts.append(by_pos.nlargest(n, "xp_total"))
        # Cheapest players who actually play — bench fodder the solver may need.
        playing = by_pos[by_pos["p_start"] > 0.5]
        parts.append(playing.nsmallest(6, "now_cost"))
    pool = pd.concat(parts).drop_duplicates("id").copy()
    pool["owned"] = pool["id"].isin(ds.squad["id"])
    sell = ds.squad.set_index("id")["sell_price"]
    pool["sell_price"] = pool["id"].map(sell)
    return pool.reset_index(drop=True)




def _fixture_before_deadline(ds: Dataset, team_id: int) -> bool:
    """Does this club play a PL match between now and the next deadline?"""
    deadline = ds.events.loc[ds.events["id"] == ds.next_gw, "deadline_time"].iloc[0]
    now = datetime.now(timezone.utc).isoformat()
    fx = ds.fixtures
    mine = fx[((fx["team_h"] == team_id) | (fx["team_a"] == team_id))
              & fx["kickoff_time"].notna()]
    return bool(((mine["kickoff_time"] > now)
                 & (mine["kickoff_time"] < deadline)).any())


def _price_signal(player_row: pd.Series) -> dict:
    """Native FPL price-change projections for the next couple of nights."""
    proj = player_row.get("price_change_projections") or []
    by_offset = {p["offset"]: float(p["projected_percent"]) for p in proj
                 if p.get("projected_percent") is not None}
    return {
        "tonight": by_offset.get(0), "tomorrow": by_offset.get(1),
        "imminent_rise": any(by_offset.get(o, 0) >= PRICE_RISE_IMMINENT
                             for o in (0, 1)),
        "imminent_fall": any(by_offset.get(o, 0) <= -PRICE_RISE_IMMINENT
                             for o in (0, 1)),
    }


def run_solve(skip_chips: bool = False, skip_robustness: bool = False) -> None:
    settings = load_settings()
    ds = load_dataset()
    print(f"Building xP matrix for GW{ds.next_gw}-{ds.next_gw + settings.horizon - 1} ...")
    moves = detect_club_moves(ds)
    if moves:
        print("Club moves in the settling window: " + ", ".join(
            f"{mv['name']} ({mv['games_since']} game{'s' if mv['games_since'] != 1 else ''} at new club)"
            for mv in moves.values()))
    print("Game logs: pulling per-player histories (cached per finished GW) ...")
    logs = build_game_logs(ds)
    form = recent_form(ds, logs)
    n_ret = int(form["returning"].sum())
    print(f"  {logs['id'].nunique()} players with logs; {n_ret} returning from absence")
    matrix, comps = build_xp(ds, settings.horizon, moves, form)
    write_outputs(matrix, comps)

    gws = [int(c.replace("xp_gw", "")) for c in matrix.columns
           if c.startswith("xp_gw")]
    pool = build_pool(matrix, ds, settings)
    print(f"Solver pool: {len(pool)} players, horizon GW{gws[0]}-{gws[-1]}")

    settings.max_free_transfers = ds.max_ft
    if settings.free_transfers is None:
        settings.free_transfers = ds.free_transfers
        print(f"Free transfers (derived from your actual moves): {ds.free_transfers}"
              f" (cap {ds.max_ft})")
    else:
        print(f"Free transfers (manual override in settings.yaml): {settings.free_transfers}")

    lock_ids: set[int] = set()
    ban_ids: set[int] = set()
    initial = set(int(i) for i in ds.squad["id"])
    bank0 = int(round(ds.bank * 10))

    print("Baseline solve ...")
    baseline = solve_plan(
        pool, gws, initial, bank0, settings,
        lock_ids=lock_ids, ban_ids=ban_ids,
        force_transfers=None, no_hits=False,
        mip_gap=0.002, time_limit=120.0)
    print(f"  status={baseline.status} objective={baseline.objective:.1f}")
    if not baseline.plans:
        raise RuntimeError(f"Baseline solve failed: {baseline.status}")

    # Hits must clear a bar. A MILP happily takes a -4 for +0.1 of decayed xP;
    # a human should not. If the hit plan doesn't beat the best no-hit plan by
    # hit_threshold, the no-hit plan becomes the recommendation.
    hit_decision = None
    if baseline.plans[0].hits > 0:
        no_hit = solve_plan(
            pool, gws, initial, bank0, settings,
            lock_ids=lock_ids, ban_ids=ban_ids, no_hits=True,
            mip_gap=0.002, time_limit=120.0)
        if no_hit.plans:
            gain = baseline.objective - no_hit.objective
            taken = gain >= settings.hit_threshold
            hit_decision = {
                "taken": taken, "gain": gain,
                "hit_in": baseline.plans[0].transfers_in,
                "hit_out": baseline.plans[0].transfers_out,
                "hits": baseline.plans[0].hits,
            }
            print(f"  hit buys +{gain:.1f} decayed xP vs best no-hit plan -> "
                  f"{'taking it' if taken else 'rejected, going with the free move'}")
            if not taken:
                baseline = no_hit

    # A move must clear the option-value bar over holding, and the report
    # gets the package structure: hold / best single / the package.
    decomposition = None
    move_decision = None
    if baseline.plans[0].transfers_in:
        hold_res = solve_plan(pool, gws, initial, bank0, settings,
                              lock_ids=lock_ids, ban_ids=ban_ids,
                              force_transfers=0, mip_gap=0.002, time_limit=90.0)
        single_res = solve_plan(pool, gws, initial, bank0, settings,
                                lock_ids=lock_ids, ban_ids=ban_ids,
                                force_transfers=1, no_hits=True,
                                mip_gap=0.005, time_limit=60.0)
        if hold_res.plans:
            gain = baseline.objective - hold_res.objective
            decomposition = {
                "hold_obj": hold_res.objective,
                "single": ({"in": single_res.plans[0].transfers_in,
                            "out": single_res.plans[0].transfers_out,
                            "gain": single_res.objective - hold_res.objective}
                           if single_res.plans else None),
                "package_gain": gain,
            }
            bar = settings.move_threshold * len(baseline.plans[0].transfers_in)
            held = gain < bar
            move_decision = {"held": held, "gain": gain,
                             "threshold": bar,
                             "rec_in": baseline.plans[0].transfers_in,
                             "rec_out": baseline.plans[0].transfers_out}
            print(f"  move beats hold by +{gain:.1f} vs bar {bar:.2f} "
                  f"-> {'HOLD' if held else 'move'}")
            if held:
                baseline = hold_res

    robustness = None
    if not skip_robustness:
        # Deep sampling is bought only where precision can change the read:
        # the move gain sits near the bar, or we're inside the final 24h
        # before the deadline (the run that gets acted on).
        near_bar = bool(move_decision and
                        abs(move_decision["gain"] - move_decision["threshold"]) <= 1.0)
        deadline_iso = str(ds.events.loc[ds.events["id"] == ds.next_gw,
                                         "deadline_time"].iloc[0])
        hours_left = (datetime.fromisoformat(deadline_iso.replace("Z", "+00:00"))
                      - datetime.now(timezone.utc)).total_seconds() / 3600
        force_deep = near_bar or 0 <= hours_left <= 24
        print(f"Robustness: {settings.robustness_runs} perturbed re-solves "
              f"(up to {settings.robustness_max_runs} if ambiguous"
              f"{', deep forced' if force_deep else ''}) ...")
        robustness = run_robustness(pool, gws, initial, bank0, settings,
                                    baseline, lock_ids, ban_ids, False,
                                    escalate_to=settings.robustness_max_runs,
                                    force_deep=force_deep)
        print(f"  headline move survives {robustness['survival']:.0%} of "
              f"{robustness['runs']} runs")

    chips = {}
    if not skip_chips:
        chip_names = {"wildcard": "wildcard", "bboost": "bboost", "3xc": "3xc"}
        for chip in ds.chips_available:
            if chip not in chip_names:
                continue
            print(f"Chip analysis: {chip} ...")
            res = solve_plan(
                pool, gws, initial, bank0, settings, chip=chip,
                lock_ids=lock_ids, ban_ids=ban_ids, no_hits=False,
                mip_gap=0.005, time_limit=60.0)
            if res.plans:
                chips[chip] = {
                    "best_gw": res.chip_played[1] if res.chip_played else None,
                    "gain": res.objective - baseline.objective,
                }
        if "freehit" in ds.chips_available:
            print("Chip analysis: freehit (single-GW approximation) ...")
            squad_value = int(ds.squad["sell_price"].sum()) + bank0
            best_gw, best_gain = None, 0.0
            for t, g in enumerate(gws):
                fh = solve_single_gw(pool, g, squad_value, settings,
                                     ban_ids=ban_ids, time_limit=15.0)
                base_gw_xp = sum(
                    matrix.set_index("id").loc[p, f"xp_gw{g}"]
                    for p in baseline.plans[t].lineup) + matrix.set_index(
                        "id").loc[baseline.plans[t].captain, f"xp_gw{g}"]
                gain = fh.get("xp", 0) - base_gw_xp
                if gain > best_gain:
                    best_gw, best_gain = g, gain
            chips["freehit"] = {"best_gw": best_gw, "gain": best_gain,
                                "approx": True}
        # Chip verdicts (reported judgement, not solver constraints). The gain
        # answers a narrow question — best spot IF burned inside this horizon —
        # and can't see future windows, so the default verdict is hold.
        breaks = gw_breaks(ds, gws)
        brk = {b["after_gw"]: b for b in breaks}
        doubles_gws = [g for g in gws if gw_schedule(ds, g)["doubles"]]
        for cname, info in chips.items():
            gain, bg = info.get("gain", 0.0), info.get("best_gw")
            if cname == "wildcard":
                if gain >= WC_CONSIDER_GAIN:
                    verdict = "consider"
                    why = (f"an unusually large gain — the current squad is "
                           f"leaving a lot on the table this window")
                else:
                    verdict = "hold"
                    why = ("a wildcard always shows a gain on paper (unlimited "
                           "free transfers help any squad); it earns its burn "
                           "when the squad needs surgery or at a classic window")
                    if breaks:
                        b = breaks[0]
                        why += (f" — the {b['label']} after GW{b['after_gw']} "
                                f"is the next one")
            else:
                if doubles_gws and gain >= CHIP_CONSIDER_GAIN:
                    verdict = "consider"
                    why = (f"GW{doubles_gws[0]} is a double gameweek and the "
                           f"gain clears the bar")
                elif doubles_gws:
                    verdict = "hold"
                    why = "even with a double in the horizon, the gain is ordinary"
                else:
                    verdict = "hold"
                    why = ("no double gameweek in this horizon — this chip "
                           "spikes when players play twice; hold for one")
            if bg and bg - 1 in brk:
                why += (f". Would land just after the {brk[bg - 1]['label']} — "
                        f"team news fully settled")
            elif bg and bg in brk:
                why += (f". Would land right before the {brk[bg]['label']} — "
                        f"new picks sit exposed to injuries over the break")
            info["verdict"], info["why"] = verdict, why

    # ---- transfer timing ----
    from .api import load_snapshot as _ls
    raw_boot = _ls()["bootstrap"]
    raw_players = {p["id"]: p for p in raw_boot["elements"]}
    timing = {"targets": [], "outgoing": [], "advice": "hold", "deviates": False}
    first = baseline.plans[0]
    meta_by_id = matrix.set_index("id")
    for p in first.transfers_in:
        timing["targets"].append({
            "id": int(p), "name": meta_by_id.at[p, "web_name"],
            "price_signal": _price_signal(pd.Series(raw_players.get(p, {}))),
            "survival": (robustness or {}).get("player_in_rates", {}).get(p),
            "plays_before_deadline": _fixture_before_deadline(
                ds, int(meta_by_id.at[p, "team"])),
        })
    for p in first.transfers_out:
        timing["outgoing"].append({
            "id": int(p), "name": meta_by_id.at[p, "web_name"],
            "price_signal": _price_signal(pd.Series(raw_players.get(p, {}))),
            "survival": (robustness or {}).get("player_out_rates", {}).get(p),
        })
    # Early only when it beats the option value of waiting for team news:
    # a robust buy about to be priced up, or a robust sell about to lose value.
    early_buy = [t for t in timing["targets"]
                 if t["price_signal"]["imminent_rise"]
                 and (t["survival"] or 0) >= 0.8
                 and not t["plays_before_deadline"]]
    early_sell = [t for t in timing["outgoing"]
                  if t["price_signal"]["imminent_fall"]
                  and (t["survival"] or 0) >= 0.8]
    sched = gw_schedule(ds, ds.next_gw)
    late_from = fmt_uk(sched["late_window_opens"])

    # Package-feasibility trigger: a recommended move on an exact-fit budget,
    # with the prices involved moving against it and a costly fallback, earns
    # an early window — urgency scaled by how close each price is to moving.
    pkg_risk = None
    if first.transfers_in and not (move_decision or {}).get("held"):
        slack = int(round(first.bank_after * 10))  # £0.1m units
        at_risk = []   # (name, direction, p_tonight, p_tomorrow)
        for p in first.transfers_out:
            sig = _price_signal(pd.Series(raw_players.get(p, {})))
            p0, p1 = -(sig["tonight"] or 0), -(sig["tomorrow"] or 0)
            if p1 >= 40:
                at_risk.append((str(meta_by_id.at[p, "web_name"]), "fall",
                                max(p0, 0), max(p1, 0)))
        for p in first.transfers_in:
            sig = _price_signal(pd.Series(raw_players.get(p, {})))
            p0, p1 = (sig["tonight"] or 0), (sig["tomorrow"] or 0)
            if p1 >= 40:
                at_risk.append((str(meta_by_id.at[p, "web_name"]), "rise",
                                max(p0, 0), max(p1, 0)))
        adverse = sum(1 for _, _, _, p1 in at_risk if p1 >= 50)
        # fallback cost: best same-position substitute for each incoming player
        near_cols = [c for c in matrix.columns if c.startswith("xp_gw")][:5]
        mtx = matrix.copy()
        mtx["near_"] = mtx[near_cols].sum(axis=1)
        gap = 0.0
        for p in first.transfers_in:
            row = meta_by_id.loc[p]
            alts = mtx[(mtx["pos"] == row["pos"]) & (mtx["id"] != p)
                       & (~mtx["id"].isin(initial))
                       & ((mtx["now_cost"] - row["now_cost"]).abs() <= 10)]
            mine = float(mtx.loc[mtx["id"] == p, "near_"].iloc[0])
            if len(alts):
                gap = max(gap, mine - float(alts["near_"].max()))
        if slack < adverse and gap >= 1.5 and at_risk:
            deadline = sched["deadline"]
            # break probability: tonight from offset-0; by-deadline from the
            # per-day slope extrapolated over remaining nightly windows
            days_left = max((deadline - datetime.now(timezone.utc)).total_seconds()
                            / 86400, 0.5)
            p_night = 1.0
            p_dead = 1.0
            for _, _, p0, p1 in at_risk:
                p_night *= 1 - min(p0 / 100, 1)
                p_dead *= 1 - min((p0 + max(p1 - p0, 0) * days_left) / 100, 1)
            p_night, p_dead = 1 - p_night, 1 - p_dead
            # act-between window: after the last fixture involving these
            # players, before the next published price-change deadline
            involved_teams = {int(meta_by_id.at[p, "team"])
                              for p in list(first.transfers_in)
                              + list(first.transfers_out)}
            fx = ds.fixtures
            now_iso = datetime.now(timezone.utc).isoformat()
            mine_fx = fx[(fx["team_h"].isin(involved_teams)
                          | fx["team_a"].isin(involved_teams))
                         & fx["kickoff_time"].notna()
                         & (fx["kickoff_time"] > now_iso)
                         & (fx["kickoff_time"] < deadline.isoformat())]
            from .schedule import MATCH_LENGTH, _utc
            earliest = (max(_utc(k) for k in mine_fx["kickoff_time"])
                        + MATCH_LENGTH) if len(mine_fx) else datetime.now(timezone.utc)
            price_dls = (raw_boot.get("game_config", {}).get("settings", {})
                         .get("price_change_deadlines", []))
            next_window = next((_utc(d) for d in price_dls
                                if _utc(d) > earliest), None)
            pkg_risk = {
                "slack": slack / 10, "at_risk": at_risk, "gap": round(gap, 1),
                "p_tonight": round(p_night, 2), "p_deadline": round(p_dead, 2),
                "act_from": fmt_uk(earliest),
                "act_before": fmt_uk(next_window) if next_window else None,
                "act_before_iso": next_window.isoformat() if next_window else None,
            }
    timing["package_risk"] = pkg_risk

    reasons = []
    if early_buy:
        names = ", ".join(t["name"] for t in early_buy)
        reasons.append(f"{names} predicted to rise in price imminently, the "
                       f"move is robust, and there's no match before the deadline")
    if early_sell:
        names = ", ".join(t["name"] for t in early_sell)
        reasons.append(f"{names} predicted to drop in price imminently — "
                       f"selling early protects value")
    if pkg_risk:
        moving = " and ".join(f"{n} ({'▼' if d == 'fall' else '▲'} {p1:.0f}%)"
                              for n, d, _, p1 in pkg_risk["at_risk"][:3])
        urgency = ("tonight's price window is the one to beat"
                   if pkg_risk["p_tonight"] >= 0.6 else
                   "pressure is building over the next couple of windows"
                   if pkg_risk["p_deadline"] >= 0.5 else
                   "worth watching, not yet urgent")
        window = (f" Act between {pkg_risk['act_from']} and "
                  f"{pkg_risk['act_before']}." if pkg_risk["act_before"] else "")
        reasons.append(
            f"the package is an exact fit (£{pkg_risk['slack']:.1f} slack) and "
            f"{moving} are moving against it — ≈{pkg_risk['p_deadline']:.0%} "
            f"chance it breaks before the deadline "
            f"({pkg_risk['p_tonight']:.0%} tonight alone) and the fallback "
            f"costs ~{pkg_risk['gap']:.1f} xP; {urgency}.{window}")
    if not first.transfers_in:
        if ds.confirmed_transfers:
            timing["advice"] = ("transfers done for the week — captaincy and "
                                "line-up stay open until the deadline")
        elif settings.free_transfers == 0:
            timing["advice"] = ("no transfer recommended — nothing worth a −4, "
                                "and a fresh FT arrives next week")
        else:
            timing["advice"] = "no transfer recommended this week — bank the FT"
    elif reasons:
        timing["advice"] = "consider moving early: " + "; ".join(reasons)
        timing["deviates"] = True
    else:
        timing["advice"] = (f"transfer late — inside the final 24h before the "
                            f"deadline (from {late_from}), once team news is in")

    # Transfers already made this week: attach them plus the rationale of the
    # run they matched, so the report can show WHAT was done and WHY — the raw
    # material for evaluating decisions later.
    completed = None
    if ds.confirmed_transfers:
        from .history import load_history as _load_runs
        conf_ins = {t["in"] for t in ds.confirmed_transfers}
        conf_outs = {t["out"] for t in ds.confirmed_transfers}
        matched = None
        for r in _load_runs():
            if r["gw"] != ds.next_gw:
                continue
            rec_ins = {t["id"] for t in r.get("transfers_in", [])}
            if rec_ins and rec_ins == conf_ins:
                matched = r   # keep the latest matching recommendation
        completed = {
            "transfers": [{"in": int(t["in"]), "out": int(t["out"]),
                           "source": t["source"]} for t in ds.confirmed_transfers],
            "in_line": matched is not None,
            "matched": ({"run_at": matched["run_at"],
                         "pred_points": matched.get("pred_points"),
                         "conviction": matched.get("conviction"),
                         "move_gain": matched.get("move_gain"),
                         "move_bar": matched.get("move_bar"),
                         "risk": matched.get("risk"),
                         "top_moves": matched.get("top_moves")} if matched else None),
        }
        print(f"Confirmed this week: {len(ds.confirmed_transfers)} transfer(s), "
              f"{'in line with' if matched else 'different from'} the recommendation")

    plan = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "model_version": MODEL_VERSION,
            "snapshot": ds.fetched_at,
            "next_gw": int(ds.next_gw),
            "max_finished_gw": int(ds.events.loc[
                ds.events["finished"] & ds.events["data_checked"], "id"].max())
            if (ds.events["finished"] & ds.events["data_checked"]).any() else 0,
            "deadline": str(ds.events.loc[ds.events["id"] == ds.next_gw,
                                          "deadline_time"].iloc[0]),
            "schedule": {
                "deadline_uk": sched["deadline_uk"],
                "first_kickoff_uk": fmt_uk(sched["first_kickoff"])
                if sched["first_kickoff"] else None,
                "midweek": sched["midweek"],
                "n_fixtures": sched["n_fixtures"],
                "doubles": sched["doubles"],
                "blanks": sched["blanks"],
                "gw_notes": horizon_notes(ds, gws),
                "breaks": gw_breaks(ds, gws),
                "unscheduled": unscheduled_fixtures(ds),
                "changes_since_last_pull": schedule_changes(ds),
                "rerun": rerun_guidance(ds),
            },
            "free_transfers_assumed": settings.free_transfers,
            "bank": ds.bank,
            "chips_available": ds.chips_available,
            "decay": settings.decay,
            "horizon": settings.horizon,
            "club_moves": {str(k): v for k, v in moves.items()},
        },
        "baseline": {
            "status": baseline.status,
            "objective": baseline.objective,
            "plans": [vars(p) for p in baseline.plans],
        },
        "robustness": robustness,
        "hit_decision": hit_decision,
        "decomposition": decomposition,
        "move_decision": move_decision,
        "completed": completed,
        "chips": chips,
        "timing": timing,
    }
    plan = _jsonify(plan)
    (PROCESSED / "plan.json").write_text(json.dumps(plan, indent=1))
    print("Wrote data/processed/plan.json")
    from .history import record_run
    record_run(plan, matrix)


def _load_bootstrap_elements() -> list[dict]:
    from .api import load_snapshot
    return load_snapshot()["bootstrap"]["elements"]


def _jsonify(obj):
    if isinstance(obj, dict):
        return {k: _jsonify(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_jsonify(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, float) and np.isnan(obj):
        return None
    return obj


def run_report() -> None:
    from .report import render_report

    render_report()
    print("Wrote report.html")
