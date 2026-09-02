"""Is a model run due? Used by the scheduled GitHub Action.

A run is due when any of:
- no plan exists yet
- a gameweek has finished (data checked) since the last run
- we're inside the final 24h before the next deadline and the last run is
  older than REFRESH_LATE hours (team news window)
- it's a new day and past the morning anchor (~07:00 UK) — so the dashboard
  is refreshed every morning, not on a drifting 24h clock
- backstop: the last run is older than REFRESH_DAILY hours
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from .config import PROCESSED
from .data import load_dataset
from .schedule import gw_schedule

REFRESH_LATE = timedelta(hours=6)
REFRESH_DAILY = timedelta(hours=24)   # backstop only; the morning anchor below leads
MORNING_HOUR_UTC = 6                  # solve every day from ~06:00 UTC (07:00 UK)


def is_due() -> tuple[bool, str]:
    plan_file = PROCESSED / "plan.json"
    if not plan_file.exists():
        return True, "no plan yet"
    meta = json.loads(plan_file.read_text())["meta"]
    last_run = datetime.fromisoformat(meta["generated_at"])
    now = datetime.now(timezone.utc)
    ds = load_dataset()

    finished = ds.events[ds.events["finished"] & ds.events["data_checked"]]
    if len(finished):
        last_gw = int(finished["id"].max())
        # Compare against the finalised GWs the last run SAW, not the plan's
        # next GW (which is always one ahead and would never trigger).
        if last_gw > meta.get("max_finished_gw", -1):
            return True, f"GW{last_gw} was finalised after the last run"

    sched = gw_schedule(ds, ds.next_gw)
    if sched["late_window_opens"] <= now < sched["deadline"]:
        if now - last_run >= REFRESH_LATE:
            return True, f"inside 24h of the GW{ds.next_gw} deadline, last run {now - last_run} ago"
        return False, "inside deadline window but refreshed recently"
    if now.date() > last_run.date() and now.hour >= MORNING_HOUR_UTC:
        return True, "morning refresh — first run of the day"
    if now - last_run >= REFRESH_DAILY:
        return True, f"daily refresh (last run {now - last_run} ago)"
    return False, f"last run {now - last_run} ago, nothing new"
