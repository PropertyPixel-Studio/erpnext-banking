from datetime import date

from erpnext_banking._helpers import pick_unique_voucher


def _c(amount, d=None, key="x"):
    return {"amount": amount, "date": d, "key": key, "name": key}


def test_no_amount_match_returns_none():
    assert pick_unique_voucher(100.0, date(2026, 6, 1), [_c(99.0), _c(101.0)]) is None


def test_single_amount_match_returns_it():
    c = _c(100.0, date(2026, 6, 10), "PE-1")
    assert pick_unique_voucher(100.0, date(2026, 6, 1), [c, _c(50.0)]) is c


def test_within_tolerance():
    c = _c(100.5, date(2026, 6, 1))
    assert pick_unique_voucher(100.0, date(2026, 6, 1), [c]) is c  # default ±1 Kč


def test_multiple_disambiguated_by_nearest_date():
    near = _c(100.0, date(2026, 6, 3), "near")
    far = _c(100.0, date(2026, 6, 20), "far")
    assert pick_unique_voucher(100.0, date(2026, 6, 1), [far, near]) is near


def test_multiple_equidistant_is_ambiguous_none():
    a = _c(100.0, date(2026, 6, 4), "a")
    b = _c(100.0, date(2026, 5, 29), "b")  # both 3 days from June 1
    assert pick_unique_voucher(100.0, date(2026, 6, 1), [a, b]) is None


def test_multiple_but_nearest_outside_window_none():
    a = _c(100.0, date(2026, 7, 1), "a")   # 30 days
    b = _c(100.0, date(2026, 8, 1), "b")   # 61 days
    assert pick_unique_voucher(100.0, date(2026, 6, 1), [a, b], max_day_gap=7) is None
