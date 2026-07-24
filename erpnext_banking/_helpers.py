"""Pure helpers used by reconcile.py — extracted for unit testability."""

from __future__ import annotations

import re
from datetime import date, timedelta

AMOUNT_TOLERANCE_CZK = 1.0  # ±1 Kč rounding tolerance (per spec §4)

# "Protiúčet: 2371741018/3030" / "Protiúčet: 2371741018" — same token
# providers/fio/mapper._build_description writes into Bank Transaction.description.
_COUNTER_ACCOUNT_RE = re.compile(r"Protiúčet:\s*([0-9\-]+)(?:/(\d+))?")


def amount_matches(a: float, b: float, tolerance: float = AMOUNT_TOLERANCE_CZK) -> bool:
	"""True iff |a - b| <= tolerance."""
	return abs(float(a) - float(b)) <= tolerance


def outgoing_window(bt_date: date, *, days_back: int = 14, days_forward: int = 7) -> tuple[date, date]:
	"""Return (from, to) window for outgoing-payment supplier+amount matching.

	Per spec §4: bt.date - 14 to bt.date + 7.
	"""
	return (bt_date - timedelta(days=days_back), bt_date + timedelta(days=days_forward))


def incoming_window(bt_date: date, *, days_back: int = 35, days_forward: int = 7) -> tuple[date, date]:
	"""Return (from, to) window for the no-Customer incoming amount/window fallback match.

	Wider than `outgoing_window`: a customer paying against a wrong/garbled VS may pay well
	before or after the invoice's posting_date (same days_back as `_card.card_window`, whose
	card-charge-vs-bill_date gap is comparably wide).
	"""
	return (bt_date - timedelta(days=days_back), bt_date + timedelta(days=days_forward))


def parse_counter_account(description: str | None) -> tuple[str, str | None] | None:
	"""Extract (account_number, bank_code) from a BT description's "Protiúčet: acc/bank" token.

	Mirrors the format `providers/fio/mapper._build_description` writes into
	Bank Transaction.description. `bank_code` is None when the description only carries the
	bare account number (no "/bank" suffix). Returns None when no such token is present.
	"""
	if not description:
		return None
	m = _COUNTER_ACCOUNT_RE.search(description)
	if not m:
		return None
	return (m.group(1), m.group(2))


def pick_unique_voucher(bt_amount, bt_date, candidates, *, tolerance=AMOUNT_TOLERANCE_CZK, max_day_gap=7):
	"""Pick the single existing voucher that unambiguously matches a Bank Transaction.

	`candidates` is a list of dicts, each with at least ``amount`` (float) and ``date``
	(a ``datetime.date`` or ``None``). Matching is conservative — it is meant for linking
	BTs to *already-posted* Payment Entries / Journal Entries, where a wrong pick mislinks
	money, so ambiguity must yield None (left for manual reconciliation):

	- keep only candidates whose amount is within ``tolerance`` of ``bt_amount``;
	- none → None; exactly one → that one;
	- several → the strictly-closest by date within ``max_day_gap`` days, but only if it is
	  strictly closer than the next candidate (a tie is ambiguous → None).
	"""
	matches = [c for c in candidates if abs(float(c["amount"]) - float(bt_amount)) <= tolerance]
	if not matches:
		return None
	if len(matches) == 1:
		return matches[0]

	def gap(c):
		if c.get("date") and bt_date:
			return abs((c["date"] - bt_date).days)
		return 10**6

	ranked = sorted(matches, key=gap)
	if gap(ranked[0]) <= max_day_gap and gap(ranked[0]) < gap(ranked[1]):
		return ranked[0]
	return None
