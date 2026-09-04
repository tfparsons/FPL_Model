"""Turn raw snapshots + historical CSVs into the model's working datasets."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .api import load_snapshot
from .config import CONFIG, HISTORY, POSITIONS


def derive_free_transfers(transfers: list[dict], chips: list[dict],
                          next_gw: int, cap: int = 5) -> int:
    """Replay FPL's free-transfer banking from the player's ACTUAL moves.

    1 FT ahead of GW2; each completed deadline consumes what was actually
    used (wildcard/free-hit weeks consume nothing) and banks +1, capped at 5.
    """
    from collections import Counter

    chips_by_gw = {c.get("event"): c.get("name") for c in chips}
    per_gw = Counter(t["event"] for t in transfers)
    ft = 1
    for gw in range(2, next_gw):
        used = 0 if chips_by_gw.get(gw) in ("wildcard", "freehit") else             min(per_gw.get(gw, 0), ft)
        ft = min(ft - used + 1, cap)
    return ft


@dataclass
class Dataset:
    players: pd.DataFrame          # one row per current player, incl. history joins
    teams: pd.DataFrame            # current season teams
    fixtures: pd.DataFrame         # full fixture list
    events: pd.DataFrame           # gameweeks
    squad: pd.DataFrame            # Tim's current 15 with selling prices
    bank: float                    # £m in the bank
    free_transfers: int            # derived from actual transfer history
    confirmed_transfers: list      # transfers already made THIS gameweek
    total_points: int              # season points so far (entry summary)
    entry_name: str                # the team's name, from the entry (header brand)
    max_ft: int                    # bankable-FT cap, read from the API rules
    next_gw: int
    chips_available: list[str]
    picks_gw: int
    fetched_at: str


def _selling_prices(squad_ids: list[int], transfers: list[dict],
                    players: pd.DataFrame) -> dict[int, int]:
    """Reconstruct selling price (in 0.1m units) for each owned player.

    Purchase price: from the transfer that brought him in, or season start price
    if he's been owned since GW1. Selling price: purchase + floor(profit/2).
    """
    buy_price: dict[int, int] = {}
    for t in sorted(transfers, key=lambda t: t["time"]):  # oldest first
        buy_price[t["element_in"]] = t["element_in_cost"]
    out = {}
    for pid in squad_ids:
        row = players.loc[players["id"] == pid].iloc[0]
        now = int(row["now_cost"])
        start_price = now - int(row["cost_change_start"])
        bought_at = buy_price.get(pid, start_price)
        profit = now - bought_at
        out[pid] = bought_at + profit // 2 if profit > 0 else now
    return out


def load_dataset() -> Dataset:
    snap = load_snapshot()
    boot = snap["bootstrap"]

    players = pd.DataFrame(boot["elements"])
    players["pos"] = players["element_type"].map(POSITIONS)
    teams = pd.DataFrame(boot["teams"])
    players = players.merge(
        teams[["id", "short_name", "name"]].rename(
            columns={"id": "team", "short_name": "team_short", "name": "team_name"}),
        on="team", how="left")
    events = pd.DataFrame(boot["events"])
    fixtures = pd.DataFrame(snap["fixtures"])

    nxt = events.loc[events["is_next"], "id"]
    next_gw = int(nxt.iloc[0]) if len(nxt) else int(events.loc[~events["finished"], "id"].min())

    picks = snap["picks"]
    squad_ids = [p["element"] for p in picks["picks"]]
    pick_meta = {p["element"]: p for p in picks["picks"]}
    bank_units = picks["entry_history"]["bank"]
    # Transfers CONFIRMED for the upcoming GW happen between deadlines and are
    # invisible to the picks endpoint until it rolls over — apply them here so
    # the model always works from the real squad, bank and FT stock. Confirmed
    # beats suggested, everywhere.
    confirmed: list[dict] = []
    pending = [t for t in snap["transfers"] if t.get("event") == next_gw]
    for t in sorted(pending, key=lambda t: t["time"]):
        if t["element_out"] in squad_ids:
            squad_ids.remove(t["element_out"])
        squad_ids.append(t["element_in"])
        bank_units += t["element_out_cost"] - t["element_in_cost"]
        confirmed.append({"in": t["element_in"], "out": t["element_out"],
                          "source": "api"})
    # The public API hides pending moves until the deadline, so declared
    # confirmed transfers (config/confirmed.yaml) fill the gap. Skip any the
    # API already knows about; entries for other GWs are ignored.
    manual_tx = []
    conf_path = CONFIG / "confirmed.yaml"
    if conf_path.exists():
        import yaml as _yaml
        by_name = {str(n).casefold(): int(i) for n, i in
                   zip(players["web_name"], players["id"])}
        selling_pre = _selling_prices(squad_ids, snap["transfers"], players)
        for entry in (_yaml.safe_load(conf_path.read_text()) or []):
            if int(entry.get("gw", -1)) != next_gw:
                continue
            out_id = by_name.get(str(entry.get("out", "")).casefold())
            in_id = by_name.get(str(entry.get("in", "")).casefold())
            if out_id is None or in_id is None or out_id not in squad_ids:
                continue  # unknown name, or the API already applied this move
            in_cost = int(entry.get("in_cost",
                          players.loc[players["id"] == in_id, "now_cost"].iloc[0]))
            out_cost = selling_pre.get(out_id, int(
                players.loc[players["id"] == out_id, "now_cost"].iloc[0]))
            squad_ids.remove(out_id)
            squad_ids.append(in_id)
            bank_units += out_cost - in_cost
            manual_tx.append({"element_in": in_id, "element_in_cost": in_cost,
                              "element_out": out_id, "element_out_cost": out_cost,
                              "event": next_gw, "time": "9999-manual"})
            confirmed.append({"in": in_id, "out": out_id, "source": "declared"})
    selling = _selling_prices(squad_ids, snap["transfers"] + manual_tx, players)
    squad = players[players["id"].isin(squad_ids)][
        ["id", "web_name", "pos", "team_short", "now_cost", "status",
         "chance_of_playing_next_round", "news"]].copy()
    squad["sell_price"] = squad["id"].map(selling)
    squad["is_captain"] = squad["id"].map(
        lambda i: pick_meta.get(i, {}).get("is_captain", False))
    squad["position_order"] = squad["id"].map(
        lambda i: pick_meta.get(i, {}).get("position", 99))
    squad = squad.sort_values("position_order")

    used = {c["name"] for c in snap["entry"].get("chips_played", [])}
    # entry history also lists chips
    for c in snap.get("entry_history", {}).get("chips", []):
        used.add(c["name"])
    all_chips = ["wildcard", "freehit", "bboost", "3xc"]
    chips_available = [c for c in all_chips if c not in used]

    bank = bank_units / 10.0
    all_chips = snap.get("entry_history", {}).get("chips", []) or [
        {"event": c.get("event"), "name": c["name"]}
        for c in snap["entry"].get("chips_played", [])]
    max_ft = int(boot.get("game_settings", {}).get("max_extra_free_transfers", 4)) + 1
    free_transfers = max(
        derive_free_transfers(snap["transfers"], all_chips, next_gw, max_ft)
        - len(pending) - len(manual_tx), 0)

    return Dataset(
        players=players, teams=teams, fixtures=fixtures, events=events,
        squad=squad, bank=bank, free_transfers=free_transfers,
        confirmed_transfers=confirmed, max_ft=max_ft,
        total_points=int(snap["entry"].get("summary_overall_points") or 0),
        entry_name=str(snap["entry"].get("name") or "FPL Model"),
        next_gw=next_gw,
        chips_available=chips_available, picks_gw=picks["_gw"],
        fetched_at=snap["meta"]["fetched_at"],
    )


def load_history(season: str) -> dict[str, pd.DataFrame]:
    """Load one historical season's CSVs (downloaded by `make update`)."""
    base = HISTORY / season
    out = {}
    for name in ["fixtures", "teams", "players_raw", "merged_gw"]:
        path = base / f"{name}.csv"
        if path.exists():
            out[name] = pd.read_csv(path, low_memory=False)
    return out


def last_season_player_rates(season: str = "2025-26",
                             min_minutes: int = 270) -> pd.DataFrame:
    """Per-player rates for a historical season, keyed by stable player `code`.

    Returns per-90 rates plus start-pattern stats used by the minutes model.
    """
    hist = load_history(season)
    raw, gws = hist["players_raw"], hist["merged_gw"]

    id_to_code = raw.set_index("id")["code"].to_dict()
    gws = gws.copy()
    gws["code"] = gws["element"].map(id_to_code)

    team_gw_counts = 38  # full season

    def agg(group: pd.DataFrame) -> pd.Series:
        mins = group["minutes"].sum()
        starts = int(group["starts"].sum()) if "starts" in group else np.nan
        apps = int((group["minutes"] > 0).sum())
        started_rows = group[group["starts"] == 1] if "starts" in group else group
        mins_per_start = (
            started_rows["minutes"].mean() if len(started_rows) else np.nan)
        p60_given_start = (
            (started_rows["minutes"] >= 60).mean() if len(started_rows) else np.nan)
        # Start rate over the last 15 GWs of the season (recency: captures
        # end-of-season pecking order better than the full-season average).
        late = group[group["GW"] >= 24]
        late_start_rate = late["starts"].mean() if len(late) else np.nan
        # Defensive contribution: merged_gw holds the raw action COUNT, so
        # estimate P(hit threshold) per 60+ min appearance (10 DEF, 12 MID/FWD;
        # the count column is already position-correct per FPL's live data).
        pos = group["position"].mode().iloc[0] if "position" in group else "MID"
        threshold = 10 if pos == "DEF" else 12
        full = group[group["minutes"] >= 60]
        defcon_rate = (
            (full["defensive_contribution"] >= threshold).mean()
            if "defensive_contribution" in group and len(full) >= 3 else np.nan)
        per90 = lambda col: (group[col].sum() / mins * 90) if mins >= min_minutes else np.nan
        return pd.Series({
            "mins_last": mins,
            "starts_last": starts,
            "apps_last": apps,
            "start_rate_last": starts / team_gw_counts if not np.isnan(starts) else np.nan,
            "late_start_rate_last": late_start_rate,
            "sub_apps_last": apps - starts if not np.isnan(starts) else np.nan,
            "mins_per_start_last": mins_per_start,
            "p60_given_start_last": p60_given_start,
            "xg90_last": per90("expected_goals"),
            "xa90_last": per90("expected_assists"),
            "saves90_last": per90("saves"),
            "defcon_rate_last": defcon_rate,
            "apps60_last": len(full),
            "bonus90_last": per90("bonus"),
            "yellow90_last": per90("yellow_cards"),
        })

    rates = gws.groupby("code").apply(agg, include_groups=False).reset_index()
    team_last = raw.drop_duplicates("code").set_index("code")["team_code"]
    rates["team_code_last"] = rates["code"].map(team_last)
    return rates


def last_season_team_strengths(season: str = "2025-26",
                               through_gw: int | None = None) -> pd.DataFrame:
    """Goals for/against per game per team code from a historical season."""
    hist = load_history(season)
    fx, teams = hist["fixtures"], hist["teams"]
    fx = fx[fx["finished"] == True]  # noqa: E712
    if through_gw is not None:
        fx = fx[fx["event"] < through_gw]

    rows = []
    for tid in teams["id"]:
        home = fx[fx["team_h"] == tid]
        away = fx[fx["team_a"] == tid]
        rows.append({
            "id": tid,
            "gf_home": home["team_h_score"].mean(),
            "ga_home": home["team_a_score"].mean(),
            "gf_away": away["team_a_score"].mean(),
            "ga_away": away["team_h_score"].mean(),
        })
    df = pd.DataFrame(rows).merge(
        teams[["id", "code", "name", "short_name"]], on="id")
    return df
