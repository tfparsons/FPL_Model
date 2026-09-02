"""Paths and settings shared across the package."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
RAW = DATA / "raw"
HISTORY = DATA / "history"
PROCESSED = DATA / "processed"
CONFIG = ROOT / "config"

POSITIONS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
SQUAD_SHAPE = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
FORMATION_MIN = {"GKP": 1, "DEF": 3, "MID": 2, "FWD": 1}
FORMATION_MAX = {"GKP": 1, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_CLUB = 3
SQUAD_SIZE = 15
LINEUP_SIZE = 11
MAX_FREE_TRANSFERS = 5
HIT_COST = 4


@dataclass
class Settings:
    team_id: int
    free_transfers: int | None   # None = derive from actual transfer history
    horizon: int
    decay: float
    bench_gk_weight: float
    bench_outfield_weights: list[float]
    vice_weight: float
    hit_threshold: float
    robustness_runs: int
    player_noise: float
    gw_noise: float
    pool_sizes: dict[str, int]
    max_free_transfers: int = 5          # overwritten from the API each run
    robustness_max_runs: int = 100       # adaptive-sampling ceiling
    ft_end_values: list[float] = field(default_factory=lambda: [1.5, 1.0, 0.6])
    churn_penalty: float = 0.05
    move_threshold: float = 1.0
    lock: list[str] = field(default_factory=list)
    ban: list[str] = field(default_factory=list)
    force_transfers: int | None = None
    no_hits: bool = False


def _read_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_settings() -> Settings:
    s = _read_yaml(CONFIG / "settings.yaml")
    o = _read_yaml(CONFIG / "overrides.yaml")
    return Settings(
        team_id=int(s["team_id"]),
        free_transfers=(None if str(s.get("free_transfers", "auto")).lower() == "auto"
                        else int(s["free_transfers"])),
        horizon=int(s["horizon"]),
        decay=float(s["decay"]),
        bench_gk_weight=float(s["bench_weights"]["gk"]),
        bench_outfield_weights=[float(w) for w in s["bench_weights"]["outfield"]],
        vice_weight=float(s["vice_weight"]),
        hit_threshold=float(s.get("hit_threshold", 2.0)),
        robustness_runs=int(s["robustness_runs"]),
        robustness_max_runs=int(s.get("robustness_max_runs", 100)),
        player_noise=float(s["player_noise"]),
        gw_noise=float(s["gw_noise"]),
        pool_sizes={k: int(v) for k, v in s["pool"].items()},
        ft_end_values=[float(v) for v in s.get("ft_end_values", [1.5, 1.0, 0.6])],
        churn_penalty=float(s.get("churn_penalty", 0.05)),
        move_threshold=float(s.get("move_threshold", 1.0)),
        lock=list(o.get("lock") or []),
        ban=list(o.get("ban") or []),
        force_transfers=o.get("force_transfers"),
        no_hits=bool(o.get("no_hits") or False),
    )
