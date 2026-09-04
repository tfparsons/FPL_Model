"""FPL API client with timestamped on-disk caching, plus historical data download.

Every `update` run writes a fresh timestamped snapshot directory under data/raw/
and points data/raw/LATEST at it, so any model run is reproducible from its snapshot.
"""

from __future__ import annotations

import json
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from .config import HISTORY, RAW

BASE = "https://fantasy.premierleague.com/api"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    )
}
VAASTAV = (
    "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
)
# Historical seasons used for priors (most recent) and backtesting.
HISTORY_FILES = {
    "2025-26": ["fixtures.csv", "teams.csv", "players_raw.csv", "gws/merged_gw.csv"],
    "2024-25": ["fixtures.csv", "teams.csv", "players_raw.csv", "gws/merged_gw.csv"],
}


def _get(url: str, session: requests.Session) -> Any:
    for attempt in range(3):
        resp = session.get(url, headers=HEADERS, timeout=30)
        if resp.status_code == 200:
            return resp.json()
        time.sleep(2 * (attempt + 1))
    resp.raise_for_status()
    return None


def update_snapshot(team_id: int) -> Path:
    """Pull all needed endpoints into a new timestamped snapshot dir."""
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    snap = RAW / stamp
    snap.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    endpoints = {
        "bootstrap.json": f"{BASE}/bootstrap-static/",
        "fixtures.json": f"{BASE}/fixtures/",
        "entry.json": f"{BASE}/entry/{team_id}/",
        "entry_history.json": f"{BASE}/entry/{team_id}/history/",
        "transfers.json": f"{BASE}/entry/{team_id}/transfers/",
    }
    data: dict[str, Any] = {}
    for fname, url in endpoints.items():
        data[fname] = _get(url, session)
        (snap / fname).write_text(json.dumps(data[fname]))
        time.sleep(0.5)  # be polite

    # Picks for the most recent gameweek that has started (squad state).
    events = data["bootstrap.json"]["events"]
    started = [e["id"] for e in events if e["is_current"] or e["finished"]]
    picks_gw = max(started) if started else 1
    try:
        picks = _get(f"{BASE}/entry/{team_id}/event/{picks_gw}/picks/", session)
    except requests.HTTPError:
        picks_gw -= 1
        picks = _get(f"{BASE}/entry/{team_id}/event/{picks_gw}/picks/", session)
    picks["_gw"] = picks_gw
    (snap / "picks.json").write_text(json.dumps(picks))

    (snap / "meta.json").write_text(
        json.dumps({"fetched_at": stamp, "team_id": team_id, "picks_gw": picks_gw})
    )
    (RAW / "LATEST").write_text(stamp)
    print(f"Snapshot written: data/raw/{stamp}")
    return snap


def download_history(force: bool = False) -> None:
    """Fetch historical season CSVs (vaastav repo) if not already present."""
    session = requests.Session()
    for season, files in HISTORY_FILES.items():
        for f in files:
            dest = HISTORY / season / f.replace("gws/", "")
            if dest.exists() and not force:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            url = f"{VAASTAV}/{season}/{f}"
            print(f"Downloading {season}/{f} ...")
            resp = session.get(url, timeout=120)
            resp.raise_for_status()
            dest.write_bytes(resp.content)


def fetch_gw_live(gw: int) -> dict[str, Any]:
    """All players' points for a finished GW, cached permanently (never changes)."""
    dest = HISTORY / "live" / f"gw{gw}.json"
    if dest.exists():
        return json.loads(dest.read_text())
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = _get(f"{BASE}/event/{gw}/live/", requests.Session())
    dest.write_text(json.dumps(data))
    return data


def fetch_entry_picks(team_id: int, gw: int) -> dict[str, Any]:
    """Tim's actual picks/points for a finished GW, cached permanently."""
    dest = HISTORY / "live" / f"picks_gw{gw}.json"
    if dest.exists():
        return json.loads(dest.read_text())
    dest.parent.mkdir(parents=True, exist_ok=True)
    data = _get(f"{BASE}/entry/{team_id}/event/{gw}/picks/", requests.Session())
    dest.write_text(json.dumps(data))
    return data


def latest_snapshot() -> Path:
    pointer = RAW / "LATEST"
    if not pointer.exists():
        raise FileNotFoundError("No data snapshot found — run `make update` first.")
    snap = RAW / pointer.read_text().strip()
    if not snap.exists():
        raise FileNotFoundError(f"Snapshot {snap} missing — run `make update`.")
    return snap


def load_snapshot() -> dict[str, Any]:
    snap = latest_snapshot()
    out = {}
    for f in snap.glob("*.json"):
        out[f.stem] = json.loads(f.read_text())
    return out


def clean_old_snapshots(keep: int = 10) -> None:
    """Keep the newest N snapshots; delete the rest."""
    snaps = sorted([d for d in RAW.iterdir() if d.is_dir()])
    for d in snaps[:-keep]:
        shutil.rmtree(d)


def fetch_element_summaries(ids: list[int], max_finished_gw: int,
                            spacing: float = 0.3) -> dict[int, list[dict]]:
    """Per-player game logs (this season), cached per finished-GW state.

    The cache key is the number of finalised gameweeks: a log can only grow
    when a gameweek finishes, so one pull per player per finished GW is the
    honest minimum. ~1 request per player with minutes this season.
    """
    out: dict[int, list[dict]] = {}
    cache = HISTORY / "summaries" / f"gw{max_finished_gw}"
    cache.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    for pid in ids:
        dest = cache / f"{pid}.json"
        if dest.exists():
            out[int(pid)] = json.loads(dest.read_text())
            continue
        try:
            data = _get(f"{BASE}/element-summary/{pid}/", session)
        except Exception as exc:  # one bad player must not sink the run
            print(f"WARNING: element-summary {pid} failed: {exc}")
            continue
        hist = data.get("history") or []
        dest.write_text(json.dumps(hist))
        out[int(pid)] = hist
        time.sleep(spacing)
    return out
