"""The slot model must generalise: vacancy, squeeze, spill, keeper, no-op."""
import pandas as pd

from mudchute.slots import rebalance


def _base(rows):
    return pd.DataFrame(rows, columns=["id", "name", "team", "pos", "base_start", "avail"])


def test_vacancy_goes_to_next_in_line_and_is_flagged():
    base = _base([
        (1, "Star", 1, "FWD", 0.90, 0.0),    # injured regular
        (2, "Backup", 1, "FWD", 0.40, 1.0),
        (3, "Kid", 1, "FWD", 0.05, 1.0),
    ])
    out = rebalance(base, {(1, "FWD"): 1.0}).set_index("id")
    assert out.at[2, "base_start"] > 0.85          # backup becomes the starter
    assert out.at[2, "cover_for"] == "Star"
    assert out.at[3, "base_start"] < 0.3           # the kid barely moves
    assert out.at[1, "base_start"] == 0.90          # standalone untouched (avail zeroes him)


def test_no_change_without_a_known_absence():
    base = _base([(1, "A", 1, "MID", 0.6, 1.0), (2, "B", 1, "MID", 0.5, 1.0),
                  (3, "C", 1, "MID", 0.5, 1.0)])
    out = rebalance(base, {(1, "MID"): 4.0}).set_index("id")   # under-subscribed, nobody absent
    assert (out["base_start"].round(6) == base.set_index("id")["base_start"].round(6)).all()
    assert (out["cover_for"] == "").all()


def test_doubtful_flag_does_not_trigger_redistribution():
    base = _base([(1, "Star", 1, "FWD", 0.9, 0.75), (2, "Backup", 1, "FWD", 0.3, 1.0)])
    out = rebalance(base, {(1, "FWD"): 1.0}).set_index("id")
    assert out.at[2, "base_start"] == 0.3


def test_squeeze_takes_mass_from_the_uncertain_first():
    base = _base([(1, "Nailed", 1, "FWD", 0.96, 1.0), (2, "Maybe", 1, "FWD", 0.6, 1.0),
                  (3, "CoinFlip", 1, "FWD", 0.5, 1.0), (4, "Fringe", 1, "FWD", 0.4, 1.0)])
    out = rebalance(base, {(1, "FWD"): 1.0}).set_index("id")   # mass 2.46 vs 1 slot
    assert abs(out["base_start"].sum() - 1.0) < 0.05
    assert out.at[1, "base_start"] > 0.9            # the nailed starter barely moves
    assert out.at[3, "base_start"] < 0.3            # the coin-flips carry the cut
    assert not out.at[1, "squeeze"] and out.at[3, "squeeze"]


def test_mild_oversubscription_is_left_alone():
    base = _base([(1, "A", 1, "FWD", 0.9, 1.0), (2, "B", 1, "FWD", 0.9, 1.0)])
    out = rebalance(base, {(1, "FWD"): 1.0}).set_index("id")   # gap 0.8 < threshold
    assert (out["base_start"] == 0.9).all()


def test_spill_to_midfield_when_no_forward_can_cover():
    base = _base([
        (1, "Striker", 1, "FWD", 0.95, 0.0),   # out, and the only forward
        (2, "Mid1", 1, "MID", 0.70, 1.0),
        (3, "Mid2", 1, "MID", 0.60, 1.0),
        (4, "Mid3", 1, "MID", 0.50, 1.0),
    ])
    out = rebalance(base, {(1, "FWD"): 1.0, (1, "MID"): 1.8}).set_index("id")
    assert out.loc[[2, 3, 4], "base_start"].sum() > 1.8 + 0.5   # the FWD slot spilled
    assert (out.loc[[2, 3, 4], "cover_for"] == "Striker").any()


def test_keeper_backup_takes_the_gloves():
    base = _base([(1, "No1", 1, "GKP", 0.95, 0.0), (2, "No2", 1, "GKP", 0.05, 1.0)])
    out = rebalance(base, {(1, "GKP"): 1.0}).set_index("id")
    assert out.at[2, "base_start"] > 0.85


def test_learned_pairing_directs_the_cover():
    base = _base([(1, "Star", 1, "FWD", 0.9, 0.0), (2, "Usual", 1, "FWD", 0.3, 1.0),
                  (3, "Other", 1, "FWD", 0.3, 1.0)])
    pairings = {(1, 1): {2: 0.8}}               # historically Usual starts when Star is out
    out = rebalance(base, {(1, "FWD"): 1.0}, pairings).set_index("id")
    assert out.at[2, "base_start"] > out.at[3, "base_start"] + 0.1
