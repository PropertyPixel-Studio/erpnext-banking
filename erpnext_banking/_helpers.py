"""Pure helpers used by reconcile.py — extracted for unit testability."""

from datetime import date, timedelta

AMOUNT_TOLERANCE_CZK = 1.0  # ±1 Kč rounding tolerance (per spec §4)


def amount_matches(a: float, b: float, tolerance: float = AMOUNT_TOLERANCE_CZK) -> bool:
	"""True iff |a - b| <= tolerance."""
	return abs(float(a) - float(b)) <= tolerance


def outgoing_window(bt_date: date, *, days_back: int = 14, days_forward: int = 7) -> tuple[date, date]:
	"""Return (from, to) window for outgoing-payment supplier+amount matching.

	Per spec §4: bt.date - 14 to bt.date + 7.
	"""
	return (bt_date - timedelta(days=days_back), bt_date + timedelta(days=days_forward))
