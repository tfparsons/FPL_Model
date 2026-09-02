"""Robustness check: does the headline move survive when xP is perturbed?

Each run multiplies every player's xP by a per-player systematic factor
(model error on the player) and a per-player-per-GW factor (week noise),
then re-solves. The survival rate of the first-GW transfer set is what
drives the early-vs-late timing advice.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Settings
from .solver import SolveResult, solve_plan


def perturb_xp(pool: pd.DataFrame, gws: list[int], rng: np.random.Generator,
               player_noise: float, gw_noise: float) -> dict[tuple[int, int], float]:
    out = {}
    for _, row in pool.iterrows():
        f_player = np.exp(rng.normal(0, player_noise))
        for g in gws:
            f_gw = np.exp(rng.normal(0, gw_noise))
            out[(row["id"], g)] = float(row[f"xp_gw{g}"]) * f_player * f_gw
    return out


AMBIGUOUS_LOW = 0.30    # conviction band where extra sampling is worth buying
AMBIGUOUS_HIGH = 0.70


def run_robustness(
    pool: pd.DataFrame,
    gws: list[int],
    initial_squad: set[int],
    bank0: int,
    settings: Settings,
    baseline: SolveResult,
    lock_ids: set[int],
    ban_ids: set[int],
    no_hits: bool,
    seed: int = 22,
    escalate_to: int | None = None,
    force_deep: bool = False,
) -> dict:
    """Re-solve with perturbed xP; report survival.

    Adaptive sampling: run `robustness_runs` first; if the headline conviction
    lands in the ambiguous band (or force_deep, e.g. near the bar or the final
    pre-deadline run), keep sampling up to `escalate_to`. Proportion noise
    shrinks with 1/sqrt(n), so extra runs are only bought where they can
    actually change the read.
    """
    rng = np.random.default_rng(seed)
    base_in = set(baseline.plans[0].transfers_in)
    base_out = set(baseline.plans[0].transfers_out)

    # Pin the first-GW transfer count to the baseline's. Unconstrained noise
    # runs degenerate into hit-taking sprees (a player drawn +1sd across the
    # whole horizon makes any hit look profitable), which answers the wrong
    # question. Pinned, the runs answer: same move budget, do the NAMES hold?
    force_n = len(base_in) if base_in else None
    hold_mode = not base_in  # baseline says hold: count how many runs agree

    from collections import Counter

    n_same = 0
    in_counts: dict[int, int] = {p: 0 for p in base_in}
    out_counts: dict[int, int] = {p: 0 for p in base_out}
    all_in: Counter = Counter()
    all_out: Counter = Counter()
    move_counts: dict[tuple, int] = {}
    n_done = 0

    def _batch(n_runs: int) -> None:
        nonlocal n_same, n_done
        for _ in range(n_runs):
            noisy = perturb_xp(pool, gws, rng, settings.player_noise,
                               settings.gw_noise)
            res = solve_plan(
                pool, gws, initial_squad, bank0, settings,
                lock_ids=lock_ids, ban_ids=ban_ids,
                no_hits=no_hits or hold_mode,
                force_transfers=force_n,
                xp_override=noisy, mip_gap=0.02, time_limit=25.0)
            if not res.plans:
                continue
            n_done += 1
            got_in = set(res.plans[0].transfers_in)
            got_out = set(res.plans[0].transfers_out)
            if got_in == base_in and got_out == base_out:
                n_same += 1
            all_in.update(got_in)
            all_out.update(got_out)
            key = (tuple(sorted(got_in)), tuple(sorted(got_out)))
            move_counts[key] = move_counts.get(key, 0) + 1
            for p in base_in & got_in:
                in_counts[p] += 1
            for p in base_out & got_out:
                out_counts[p] += 1

    def _conviction() -> float | None:
        if not n_done:
            return None
        if base_in:
            return max(in_counts.values(), default=0) / n_done
        return n_same / n_done

    _batch(settings.robustness_runs)
    escalated = False
    target = escalate_to or settings.robustness_runs
    conv = _conviction()
    ambiguous = conv is not None and AMBIGUOUS_LOW <= conv <= AMBIGUOUS_HIGH
    if target > n_done and (force_deep or ambiguous):
        why = "final-window/near-bar" if force_deep else f"conviction {conv:.0%} ambiguous"
        print(f"  escalating to {target} runs ({why}) ...")
        _batch(target - n_done)
        escalated = True

    top_moves = sorted(move_counts.items(), key=lambda kv: -kv[1])[:8]
    return {
        "runs": n_done,
        "escalated": escalated,
        "survival": n_same / n_done if n_done else float("nan"),
        "player_in_rates": {int(p): c / n_done for p, c in in_counts.items()}
        if n_done else {},
        "player_out_rates": {int(p): c / n_done for p, c in out_counts.items()}
        if n_done else {},
        "themes": {
            "in": [[int(p), c / n_done] for p, c in all_in.most_common(6)],
            "out": [[int(p), c / n_done] for p, c in all_out.most_common(6)],
        } if n_done else {"in": [], "out": []},
        "top_moves": [
            {"in": [int(p) for p in k[0]], "out": [int(p) for p in k[1]],
             "share": c / n_done}
            for k, c in top_moves] if n_done else [],
    }
