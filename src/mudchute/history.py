"""Track record: log every run's call, then settle outcomes once the GW is in.

data/processed/history.jsonl  — one line per solve run (committed)
data/processed/outcomes.json  — per finished GW: what the model's last
                                pre-deadline call would have scored vs what
                                Tim's actual team scored (committed)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from .api import fetch_entry_picks, fetch_gw_live, load_snapshot
from .changelog import MODEL_VERSION
from .config import PROCESSED

HISTORY_FILE = PROCESSED / "history.jsonl"
OUTCOMES_FILE = PROCESSED / "outcomes.json"


def record_run(plan: dict, matrix: pd.DataFrame) -> None:
    """Append this run's headline call to the history log."""
    m = matrix.set_index("id")
    first = plan["baseline"]["plans"][0]
    gw = plan["meta"]["next_gw"]
    xp_col = f"xp_gw{gw}"
    rec = {
        "run_at": plan["meta"]["generated_at"],
        "gw": gw,
        "fts": plan["meta"].get("free_transfers_assumed"),
        "model": MODEL_VERSION,
        "deadline": plan["meta"]["deadline"],
        "transfers_in": [{"id": int(p), "name": str(m.at[p, "web_name"])}
                         for p in first["transfers_in"]],
        "transfers_out": [{"id": int(p), "name": str(m.at[p, "web_name"])}
                          for p in first["transfers_out"]],
        "hits": first["hits"],
        "captain": int(first["captain"]),
        "vice": int(first["vice"]),
        "lineup": [int(p) for p in first["lineup"]],
        "bench": [int(p) for p in first["bench"]],
        "lineup_xp": {int(p): float(m.at[p, xp_col])
                      for p in first["lineup"] + first["bench"]},
        "pred_points": float(sum(m.at[p, xp_col] for p in first["lineup"])
                             + m.at[first["captain"], xp_col]) - 4 * first["hits"],
        "survival": (plan.get("robustness") or {}).get("survival"),
        # Payload conviction: the strongest recommended buy's scenario rate —
        # far more informative than exact-set survival for multi-transfer moves.
        "conviction": (max(((plan.get("robustness") or {})
                            .get("player_in_rates") or {}).values(), default=None)
                       if first["transfers_in"] else
                       (plan.get("robustness") or {}).get("survival")),
        "move_gain": (plan.get("move_decision") or {}).get("gain"),
        "move_bar": (plan.get("move_decision") or {}).get("threshold"),
        "risk": ({"p_deadline": pr["p_deadline"], "gap": pr["gap"]}
                 if (pr := (plan.get("timing") or {}).get("package_risk"))
                 else None),
        "top_moves": [{"in": mv["in"], "out": mv["out"], "share": mv["share"]}
                      for mv in ((plan.get("robustness") or {})
                                 .get("top_moves") or [])[:8]],
    }
    with open(HISTORY_FILE, "a") as f:
        f.write(json.dumps(rec) + "\n")


def load_history() -> list[dict]:
    if not HISTORY_FILE.exists():
        return []
    return [json.loads(line) for line in HISTORY_FILE.read_text().splitlines()
            if line.strip()]


def load_outcomes() -> dict[str, dict]:
    if not OUTCOMES_FILE.exists():
        return {}
    return json.loads(OUTCOMES_FILE.read_text())


def final_call_for_gw(runs: list[dict], gw: int) -> dict | None:
    """The last run before the deadline is the one that counts."""
    cands = [r for r in runs if r["gw"] == gw and r["run_at"] <= r["deadline"]]
    return max(cands, key=lambda r: r["run_at"]) if cands else None


def settle(team_id: int) -> None:
    """Fill in outcomes for every finished GW that has a recorded call."""
    snap = load_snapshot()
    events = {e["id"]: e for e in snap["bootstrap"]["events"]}
    names = {p["id"]: p["web_name"] for p in snap["bootstrap"]["elements"]}
    runs = load_history()
    outcomes = load_outcomes()
    transfers = snap["transfers"]
    changed = False

    for gw in sorted({r["gw"] for r in runs}):
        key = str(gw)
        ev = events.get(gw)
        if key in outcomes or not ev or not ev.get("data_checked"):
            continue
        call = final_call_for_gw(runs, gw)
        if call is None:
            continue
        live = {e["id"]: e["stats"]["total_points"]
                for e in fetch_gw_live(gw)["elements"]}
        picks = fetch_entry_picks(team_id, gw)
        tim_points = picks["entry_history"]["points"]
        tim_in = [t["element_in"] for t in transfers if t["event"] == gw]
        tim_out = [t["element_out"] for t in transfers if t["event"] == gw]

        # Counterfactual: the model's lineup + captain, no autosubs, minus hits.
        model_points = (sum(live.get(p, 0) for p in call["lineup"])
                        + live.get(call["captain"], 0) - 4 * call["hits"])
        call_in = {t["id"] for t in call["transfers_in"]}
        followed = call_in.issubset(set(tim_in)) if call_in else (not tim_in)

        outcomes[key] = {
            "gw": gw,
            "model_call_in": [t["name"] for t in call["transfers_in"]],
            "model_call_out": [t["name"] for t in call["transfers_out"]],
            "tim_in": [names.get(p, str(p)) for p in tim_in],
            "tim_out": [names.get(p, str(p)) for p in tim_out],
            "followed": followed,
            "pred_points": round(call["pred_points"], 1),
            "model_points": int(model_points),
            "tim_points": int(tim_points),
            "captain": names.get(call["captain"]),
            "captain_points": live.get(call["captain"], 0),
            "model": call.get("model"),
            "settled_at": datetime.now(timezone.utc).isoformat(),
        }
        changed = True
        print(f"Settled GW{gw}: model {model_points} vs Tim {tim_points} "
              f"(predicted {call['pred_points']:.1f})")

    if changed:
        OUTCOMES_FILE.write_text(json.dumps(outcomes, indent=1))
