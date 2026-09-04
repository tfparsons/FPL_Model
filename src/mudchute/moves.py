"""Club moves: players whose evidence was earned at a different club.

Two cases, both flagged in the xP matrix so the dashboard can show the xP
as rebuilt-on-thin-evidence rather than business as usual:

- "move"    — a mid-season transfer between PL clubs, detected by comparing
              each player's club against the last committed xP matrix and
              persisted in club_moves.json until the new club has played
              SETTLE_GAMES games since the move.
- "arrival" — a player whose last-season record is at another club (summer
              signing) or who has no PL record at all, until his club has
              played SETTLE_GAMES games this season.

Why: a minutes model built on starts at the old club, and per-90 rates
scaled by the new club's attack, will confidently over-rate a player whose
role hasn't been established yet. The rule makes new-club evidence dominate
fast — in both directions.
"""

from __future__ import annotations

import json

import pandas as pd

from .config import PROCESSED

SETTLE_GAMES = 4
MOVES_FILE = PROCESSED / "club_moves.json"


def detect_club_moves(ds) -> dict[int, dict]:
    """Return active mid-season moves {player_id: {...}} and persist them."""
    prev: dict[int, int] = {}
    mpath = PROCESSED / "xp_matrix.csv"
    if mpath.exists():
        pm = pd.read_csv(mpath, usecols=["id", "team"])
        prev = dict(zip(pm["id"].astype(int), pm["team"].astype(int)))
    moves: dict[int, dict] = {}
    if MOVES_FILE.exists():
        moves = {int(k): v for k, v in json.loads(MOVES_FILE.read_text()).items()}

    cur = ds.players.set_index("id")
    for pid, team in cur["team"].items():
        pid, team = int(pid), int(team)
        if pid in prev and prev[pid] != team and (
                pid not in moves or int(moves[pid]["to"]) != team):
            row = cur.loc[pid]
            moves[pid] = {
                "name": str(row["web_name"]), "from": prev[pid], "to": team,
                "gw": int(ds.next_gw),
                "starts_at_move": int(row.get("starts") or 0),
                "minutes_at_move": int(row.get("minutes") or 0),
            }

    fx = ds.fixtures
    started = fx[fx["started"] == True]  # noqa: E712
    active: dict[int, dict] = {}
    for pid, mv in moves.items():
        games = int((((started["team_h"] == mv["to"]) | (started["team_a"] == mv["to"]))
                     & (started["event"] >= mv["gw"])).sum())
        mv["games_since"] = games
        if (games < SETTLE_GAMES and pid in cur.index
                and int(cur.at[pid, "team"]) == int(mv["to"])):
            active[pid] = mv
    PROCESSED.mkdir(parents=True, exist_ok=True)
    MOVES_FILE.write_text(json.dumps({str(k): v for k, v in active.items()}, indent=1))
    return active


def games_played(ds) -> dict[int, int]:
    """Games each team has started this season."""
    fx = ds.fixtures
    started = fx[fx["started"] == True]  # noqa: E712
    return {int(t): int(((started["team_h"] == t) | (started["team_a"] == t)).sum())
            for t in ds.teams["id"]}


def arrival_flags(ds, last_rates: pd.DataFrame, moves: dict[int, dict]) -> pd.DataFrame:
    """Per player: adjusted ('move' | 'arrival' | ''), and games of evidence."""
    played = games_played(ds)
    lr = last_rates.set_index("code")
    rows = []
    for _, p in ds.players.iterrows():
        pid = int(p["id"])
        if pid in moves:
            rows.append({"id": pid, "adjusted": "move",
                         "adj_games": int(moves[pid]["games_since"])})
            continue
        code = p["code"]
        last_team = lr["team_code_last"].get(code) if code in lr.index else None
        no_history = code not in lr.index or pd.isna(lr["start_rate_last"].get(code))
        summer_move = last_team is not None and pd.notna(last_team) and \
            int(last_team) != int(p["team_code"])
        n = played.get(int(p["team"]), 0)
        if (no_history or summer_move) and n < SETTLE_GAMES:
            rows.append({"id": pid, "adjusted": "arrival", "adj_games": n})
        else:
            rows.append({"id": pid, "adjusted": "", "adj_games": n})
    return pd.DataFrame(rows)
