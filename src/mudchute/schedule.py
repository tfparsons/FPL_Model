"""Gameweek schedule awareness: deadlines in UK time, midweek rounds, doubles,
blanks, postponements, and what changed since the last data pull.

Nothing here assumes Saturday football. Advice is always expressed relative
to the deadline and the fixtures actually on the calendar.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from .config import RAW
from .data import Dataset

UK = ZoneInfo("Europe/London")
MATCH_LENGTH = timedelta(hours=2, minutes=15)   # kickoff -> safe to treat as over
LATE_WINDOW = timedelta(hours=24)                # "transfer late" = inside this


def _utc(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def fmt_uk(dt: datetime, with_day: bool = True) -> str:
    local = dt.astimezone(UK)
    return local.strftime("%a %d %b %H:%M" if with_day else "%H:%M") + " UK"


def gw_schedule(ds: Dataset, gw: int) -> dict:
    """Deadline, kickoff span, midweek flag, doubles and blanks for one GW."""
    ev = ds.events.loc[ds.events["id"] == gw].iloc[0]
    deadline = _utc(ev["deadline_time"])
    fx = ds.fixtures[(ds.fixtures["event"] == gw) & ds.fixtures["kickoff_time"].notna()]
    short = ds.teams.set_index("id")["short_name"].to_dict()
    counts = {int(t): 0 for t in ds.teams["id"]}
    for _, f in fx.iterrows():
        counts[int(f["team_h"])] += 1
        counts[int(f["team_a"])] += 1
    kickoffs = sorted(_utc(k) for k in fx["kickoff_time"])
    first = kickoffs[0] if kickoffs else None
    last = kickoffs[-1] if kickoffs else None
    return {
        "gw": int(gw),
        "deadline": deadline,
        "deadline_uk": fmt_uk(deadline),
        "first_kickoff": first,
        "last_kickoff": last,
        "ends": (last + MATCH_LENGTH) if last else None,
        "n_fixtures": int(len(fx)),
        # Tue/Wed/Thu first kickoff = midweek round
        "midweek": bool(first and first.astimezone(UK).weekday() in (1, 2, 3)),
        "doubles": sorted(short[t] for t, n in counts.items() if n >= 2),
        "blanks": sorted(short[t] for t, n in counts.items() if n == 0),
        "late_window_opens": deadline - LATE_WINDOW,
    }


def horizon_notes(ds: Dataset, gws: list[int]) -> dict[int, str]:
    """Short per-GW note for the plan table: 'DGW: MCI, ARS · BGW: LIV · midweek'."""
    out = {}
    for g in gws:
        s = gw_schedule(ds, g)
        bits = []
        if s["doubles"]:
            bits.append("DGW: " + ", ".join(s["doubles"]))
        if s["blanks"]:
            bits.append("BGW: " + ", ".join(s["blanks"]))
        if s["midweek"]:
            bits.append("midweek")
        out[g] = " · ".join(bits)
    return out


INTL_MONTHS = {3, 9, 10, 11}     # FIFA windows
NORMAL_GAP_DAYS = 9.5            # deadline-to-deadline beyond this = a break


def gw_breaks(ds: Dataset, gws: list[int]) -> list[dict]:
    """Breaks (internationals, cup rounds) after each horizon GW, from the
    gap between consecutive deadlines."""
    ev = ds.events.set_index("id")
    out = []
    for g in gws:
        if g + 1 not in ev.index:
            continue
        d1 = _utc(ev.at[g, "deadline_time"])
        d2 = _utc(ev.at[g + 1, "deadline_time"])
        days = (d2 - d1).total_seconds() / 86400
        if days > NORMAL_GAP_DAYS:
            intl = d1.month in INTL_MONTHS or d2.month in INTL_MONTHS
            out.append({"after_gw": int(g), "days": round(days),
                        "label": "international break" if intl
                        else "extended gap (cup round or break)"})
    return out


def unscheduled_fixtures(ds: Dataset) -> list[str]:
    """Fixtures with no gameweek assigned (postponed / awaiting rescheduling)."""
    short = ds.teams.set_index("id")["short_name"].to_dict()
    fx = ds.fixtures[ds.fixtures["event"].isna()]
    return [f"{short[int(f['team_h'])]} v {short[int(f['team_a'])]}"
            for _, f in fx.iterrows()]


def schedule_changes(ds: Dataset) -> list[str]:
    """Fixtures whose GW or kickoff moved since the previous data snapshot."""
    snaps = sorted(d for d in RAW.iterdir() if d.is_dir())
    if len(snaps) < 2:
        return []
    prev = json.loads((snaps[-2] / "fixtures.json").read_text())
    prev_by_code = {f["code"]: f for f in prev}
    short = ds.teams.set_index("id")["short_name"].to_dict()
    changes = []
    for _, f in ds.fixtures.iterrows():
        old = prev_by_code.get(f["code"])
        if old is None:
            continue
        new_ev = None if pd.isna(f["event"]) else int(f["event"])
        old_ev = old.get("event")
        old_ko, new_ko = old.get("kickoff_time"), f["kickoff_time"]
        label = f"{short[int(f['team_h'])]} v {short[int(f['team_a'])]}"
        if new_ev != old_ev:
            changes.append(
                f"{label}: GW{old_ev if old_ev else '?'} → "
                f"{'GW' + str(new_ev) if new_ev else 'unscheduled'}")
        elif old_ko and new_ko and old_ko != new_ko:
            changes.append(
                f"{label}: kickoff {fmt_uk(_utc(old_ko))} → {fmt_uk(_utc(new_ko))}")
    return changes


def rerun_guidance(ds: Dataset) -> list[str]:
    """When to run the model next, derived from the calendar."""
    now = datetime.now(UK)
    tips = []
    live = ds.events[ds.events["is_current"] & ~ds.events["finished"]]
    if len(live):
        s = gw_schedule(ds, int(live["id"].iloc[0]))
        if s["ends"] and s["ends"] > now:
            tips.append(f"after GW{s['gw']} finishes (~{fmt_uk(s['ends'])})")
    nxt = gw_schedule(ds, ds.next_gw)
    if nxt["late_window_opens"] > now:
        tips.append(f"for the final call, from {fmt_uk(nxt['late_window_opens'])} "
                    f"(24h before the GW{nxt['gw']} deadline)")
    else:
        tips.append(f"now — inside the final 24h before the GW{nxt['gw']} deadline")
    return tips
