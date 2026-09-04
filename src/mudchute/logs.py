"""Per-player game logs -> recent form for the minutes model.

Season-to-date start rates go stale: a player benched for the last three
games still reads as a starter in October. The window here weights the
current club's last WINDOW games with geometric decay, so the latest games
dominate, and it reads two things season totals can't: an absence streak
(a regular who vanished, i.e. injury/suspension) and his return, which is
eased in rather than snapped back.
"""

from __future__ import annotations

import pandas as pd

from .api import fetch_element_summaries

WINDOW = 6            # team games in the recency window
DECAY = 0.8           # weight of a game one step older
ABSENCE_MIN = 3       # games missed before a reappearance counts as a "return"
RETURN_GAMES = 2      # games of evidence before a returner is trusted again


def build_game_logs(ds) -> pd.DataFrame:
    """Rows: id, gw, fixture, team, minutes, started — this season only."""
    finished = ds.events[ds.events["finished"] & ds.events["data_checked"]]
    max_fin = int(finished["id"].max()) if len(finished) else 0
    ids = [int(i) for i in ds.players.loc[ds.players["minutes"] > 0, "id"]]
    logs = fetch_element_summaries(ids, max_fin)
    fx = ds.fixtures.set_index("id")
    rows = []
    for pid, hist in logs.items():
        for h in hist:
            fid = h.get("fixture")
            if fid not in fx.index:
                continue
            team = int(fx.at[fid, "team_h"] if h.get("was_home") else fx.at[fid, "team_a"])
            rows.append({"id": pid, "gw": int(h["round"]), "fixture": int(fid),
                         "team": team, "minutes": int(h["minutes"]),
                         "started": int(h.get("starts") or 0)})
    return pd.DataFrame(rows, columns=["id", "gw", "fixture", "team", "minutes", "started"])


def recent_form(ds, logs: pd.DataFrame) -> pd.DataFrame:
    """Per player: windowed start rate at the current club, absence/return state."""
    fx = ds.fixtures
    done = fx[(fx["finished"] == True) & fx["kickoff_time"].notna()]  # noqa: E712
    by_team: dict[int, list[int]] = {}
    for tid in ds.teams["id"]:
        tf = done[(done["team_h"] == tid) | (done["team_a"] == tid)].sort_values("kickoff_time")
        by_team[int(tid)] = [int(f) for f in tf["id"]]
    played = {(int(r.id), int(r.fixture)): (int(r.started), int(r.minutes))
              for r in logs.itertuples()}

    rows = []
    for _, p in ds.players.iterrows():
        pid, team = int(p["id"]), int(p["team"])
        games = by_team.get(team, [])
        recent = games[-WINDOW:]
        # weighted start rate over the club's recent games (absent = 0)
        num = den = 0.0
        for age, fid in enumerate(reversed(recent)):
            w = DECAY ** age
            st, _ = played.get((pid, fid), (0, 0))
            num += w * st
            den += w
        win_rate = num / den if den else float("nan")
        # absence streak / return detection over the club's full season
        featured = [played.get((pid, f), (0, 0))[1] > 0 for f in games]
        started = [played.get((pid, f), (0, 0))[0] for f in games]
        absent_streak = 0
        for f in reversed(featured):
            if f:
                break
            absent_streak += 1
        returning, return_games = False, 0
        # find the most recent absence run of >= ABSENCE_MIN that has ended
        i = len(featured) - 1
        while i >= 0 and featured[i]:
            i -= 1
        back = len(featured) - 1 - i          # games featured since last absence
        j = i
        while j >= 0 and not featured[j]:
            j -= 1
        gap = i - j                           # length of that absence
        regular_before = sum(started[max(0, j - 2):j + 1]) >= 2 if j >= 0 else False
        if gap >= ABSENCE_MIN and regular_before and 0 < back <= RETURN_GAMES:
            returning, return_games = True, back
        rows.append({"id": pid, "win_rate": win_rate, "win_n": den,
                     "absent_streak": absent_streak,
                     "returning": returning, "return_games": return_games,
                     "club_games": len(games)})
    return pd.DataFrame(rows)
