"""Pure helpers for matching salary payments to Salary Slips. Must not import frappe.

A payment is a salary when two independent signals agree: it went to an account of the
employee and its amount is the net pay of one of the employee's slips. The date only has
to be plausible. Any ambiguity yields no match and the payment is left for a person.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from ._helpers import amount_matches

# [prefix-]number[/bank code], digits only
_ACCOUNT_RE = re.compile(r"^(?:(\d{1,6})-)?(\d{1,10})(?:/(\d{4}))?$")
# Czech IBAN: CZ, 2 check digits, 4 bank code, 6 prefix, 10 number
_IBAN_CZ_RE = re.compile(r"^CZ\d{2}(\d{4})(\d{6})(\d{10})$")

# A salary is paid around the end of its period: some agreements are paid out a few days
# before the month ends, regular wages by the end of the following month.
DAYS_BEFORE_PERIOD_END = 10
DAYS_AFTER_PERIOD_END = 45


def normalize_account(text: str | None) -> tuple[str, str | None] | None:
	"""Account as (number, bank code) for comparison, None when the text is not an account.

	Spaces and leading zeros do not matter and an empty prefix is dropped, so
	"000000-0001234567/0100" and "1234567 / 0100" are the same account. A Czech IBAN is
	read as the account it encodes.
	"""
	compact = re.sub(r"\s+", "", text or "")
	iban = _IBAN_CZ_RE.match(compact.upper())
	if iban:
		bank, prefix, number = iban.groups()
	else:
		m = _ACCOUNT_RE.match(compact)
		if not m:
			return None
		prefix, number, bank = m.groups()
	prefix = (prefix or "").lstrip("0")
	number = number.lstrip("0")
	if not number:
		return None
	return (f"{prefix}-{number}" if prefix else number, bank)


def same_account(a: tuple[str, str | None] | None, b: tuple[str, str | None] | None) -> bool:
	"""Both accounts known and equal. The bank code has to agree only when both have one."""
	if not a or not b or a[0] != b[0]:
		return False
	return a[1] is None or b[1] is None or a[1] == b[1]


def has_account(accounts, counter: tuple[str, str | None] | None) -> bool:
	return any(same_account(normalize_account(a), counter) for a in accounts or ())


def payment_window(period_end: date) -> tuple[date, date]:
	"""(from, to): when the salary of a period ending on `period_end` can be paid."""
	return (
		period_end - timedelta(days=DAYS_BEFORE_PERIOD_END),
		period_end + timedelta(days=DAYS_AFTER_PERIOD_END),
	)


def slip_window(payment_date: date) -> tuple[date, date]:
	"""(from, to): period ends of the slips a payment made on `payment_date` can belong to."""
	return (
		payment_date - timedelta(days=DAYS_AFTER_PERIOD_END),
		payment_date + timedelta(days=DAYS_BEFORE_PERIOD_END),
	)


def _fits(amount: float, paid_on: date, counter, slip: dict) -> bool:
	start, end = payment_window(slip["end_date"])
	return (
		amount_matches(amount, slip["amount"])
		and start <= paid_on <= end
		and has_account(slip.get("accounts"), counter)
	)


def pick_slip(amount: float, paid_on: date, counter, slips: list[dict]) -> dict | None:
	"""The single slip a payment belongs to, None when there is none or more than one.

	`slips`: dicts with `amount` (net pay), `end_date` and `accounts` (texts).
	"""
	fitting = [s for s in slips if _fits(amount, paid_on, counter, s)]
	return fitting[0] if len(fitting) == 1 else None


def pick_payment(slip: dict, payments: list[dict]) -> dict | None:
	"""The single payment a slip was paid by, None when there is none or more than one.

	`payments`: dicts with `amount`, `date` and `counter` (normalized account or None).
	"""
	fitting = [p for p in payments if _fits(p["amount"], p["date"], p.get("counter"), slip)]
	return fitting[0] if len(fitting) == 1 else None
