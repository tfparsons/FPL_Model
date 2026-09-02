"""The FT ledger must reflect what Tim actually did, not what was advised."""

from mudchute.data import derive_free_transfers


def test_gw2_baseline():
    assert derive_free_transfers([], [], next_gw=2) == 1


def test_hold_banks_one():
    # No transfers made in GW2 -> 2 FTs for GW3.
    assert derive_free_transfers([], [], next_gw=3) == 2


def test_transfer_spends_one():
    assert derive_free_transfers([{"event": 2}], [], next_gw=3) == 1


def test_hit_does_not_go_negative():
    # 3 transfers on 1 FT: two were hits; next week still restarts at 1.
    assert derive_free_transfers([{"event": 2}] * 3, [], next_gw=3) == 1


def test_cap_at_five():
    assert derive_free_transfers([], [], next_gw=9) == 5


def test_wildcard_week_consumes_nothing():
    # Held GW2 (2 banked), wildcarded GW3: FTs untouched, +1 -> 3 for GW4.
    transfers = [{"event": 3}] * 8
    chips = [{"event": 3, "name": "wildcard"}]
    assert derive_free_transfers(transfers, chips, next_gw=4) == 3
