"""Pure helpers for card-payment / rules-engine reconciliation — no Frappe imports,
so they are fully unit-testable (see tests/test_card_*.py).

Card Bank Transactions have no counterparty account (reference_number carries the card
number, not a VS), so the layered supplier/VS rules in reconcile.py never fire. Instead
we drive them from Bank Matching Rules matched against the BT description, which for a Fio
card purchase looks like:

    Zpráva: Nákup: ANTHROPIC* CLAUDE SUB,  548 Market Street ..., dne 17.7.2026, částka  90.00 EUR

The trailing ``částka <amount> <CCY>`` is the ORIGINAL transaction amount+currency (before
the bank converted it to CZK at its own card rate), which we parse to match against a
Purchase Invoice.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from ._helpers import amount_matches

# "částka  90.00 EUR" / "částka  2 241,84 EUR" / "částka 2555.00 CZK"
_AMOUNT_RE = re.compile(
	r"částka\s+([\d][\d\s.,]*[.,]\d{2})\s+([A-Z]{3})",
	re.IGNORECASE,
)


def _to_number(raw: str) -> float:
	"""Parse a Czech/international decimal string to float.

	Handles space / non-breaking-space thousands separators and both ',' and '.' as the
	decimal separator (e.g. '2 241,84' -> 2241.84, '2555.00' -> 2555.0).
	"""
	s = raw.replace("\xa0", "").replace(" ", "")
	if "," in s and "." in s:
		# both present → '.' is thousands, ',' is decimal (e.g. '1.234,56')
		s = s.replace(".", "").replace(",", ".")
	else:
		s = s.replace(",", ".")
	return float(s)


def parse_original_amount(description: str) -> tuple[float, str] | None:
	"""Extract (amount, currency) of the original card transaction from a BT description.

	Returns None when no ``částka <amount> <CCY>`` token is present.
	"""
	if not description:
		return None
	m = _AMOUNT_RE.search(description)
	if not m:
		return None
	try:
		return (_to_number(m.group(1)), m.group(2).upper())
	except ValueError:
		return None


def rule_matches(pattern: str, description: str) -> bool:
	"""True if ``pattern`` matches ``description``.

	Plain patterns are case-insensitive substring matches. A pattern prefixed with 're:'
	is treated as a case-insensitive regular expression.
	"""
	if not pattern:
		return False
	desc = description or ""
	if pattern.startswith("re:"):
		try:
			return re.search(pattern[3:], desc, re.IGNORECASE) is not None
		except re.error:
			return False
	return pattern.lower() in desc.lower()


def select_rule(rules: list[dict], description: str) -> dict | None:
	"""Return the first enabled rule (by ascending priority, then pattern) whose pattern
	matches the description, or None."""

	def _sort_key(r):
		return (int(r.get("priority") or 100), str(r.get("pattern") or ""))

	for rule in sorted(rules, key=_sort_key):
		if rule.get("enabled", 1) and rule_matches(str(rule.get("pattern") or ""), description):
			return rule
	return None


def card_window(bt_date: date, *, days_back: int = 35, days_forward: int = 7) -> tuple[date, date]:
	"""(from, to) window for matching a card BT to a Purchase Invoice by bill_date.

	Wider than the wire-transfer window (outgoing_window): a card is charged days after the
	invoice is issued, and a subscription invoice may predate the charge by up to a month.
	"""
	return (bt_date - timedelta(days=days_back), bt_date + timedelta(days=days_forward))


def fx_amount_matches(bt_amount: float, outstanding: float, tolerance_percent: float) -> bool:
	"""Relative-tolerance match for EUR/USD cards: the bank converts at its card rate while
	the invoice is booked at the ČNB rate, so CZK amounts differ by a couple of percent.

	True iff |bt_amount - outstanding| / outstanding <= tolerance_percent / 100.
	"""
	outstanding = float(outstanding)
	if outstanding <= 0:
		return False
	return abs(float(bt_amount) - outstanding) / outstanding <= float(tolerance_percent) / 100.0


def _card_candidate_matches(
	candidate: dict,
	bt_withdrawal: float,
	orig_amount: float | None,
	orig_currency: str | None,
	fx_tolerance_percent: float,
	czk_tolerance: float,
) -> bool:
	"""Whether a single Purchase Invoice candidate matches the card BT.

	(i)   original currency CZK → exact (±czk_tolerance) BT vs outstanding;
	(ii)  foreign currency + PI has original_amount/original_currency → compare the parsed
	      original amount against those custom fields;
	(iii) foreign currency but PI original fields are empty → fall back to the FX relative
	      tolerance of BT withdrawal vs outstanding (CZK).
	"""
	outstanding = float(candidate.get("outstanding_amount") or 0)

	# (i) CZK card — no FX involved, amounts should match to the crown.
	if orig_currency == "CZK":
		return amount_matches(bt_withdrawal, outstanding, czk_tolerance)

	c_orig_amt = candidate.get("original_amount")
	c_orig_cur = candidate.get("original_currency")

	# (ii) foreign card and the invoice records its own original amount/currency.
	if orig_currency and orig_amount is not None and c_orig_amt and c_orig_cur:
		return (
			str(c_orig_cur).upper() == str(orig_currency).upper()
			and abs(float(c_orig_amt) - float(orig_amount)) <= 0.01
		)

	# (iii) fallback — relative FX tolerance on the CZK amounts.
	return fx_amount_matches(bt_withdrawal, outstanding, fx_tolerance_percent)


def pick_card_candidate(
	candidates: list[dict],
	bt_withdrawal: float,
	orig_amount: float | None,
	orig_currency: str | None,
	*,
	fx_tolerance_percent: float = 4.0,
	czk_tolerance: float = 1.0,
) -> dict | None:
	"""Return the single Purchase Invoice candidate matching this card BT, or None when
	zero or several match (ambiguity → manual reconciliation).

	Each candidate dict needs: outstanding_amount, and optionally original_amount /
	original_currency (the PI custom fields, which may be None).
	"""
	matches = [
		c
		for c in candidates
		if _card_candidate_matches(
			c, bt_withdrawal, orig_amount, orig_currency, fx_tolerance_percent, czk_tolerance
		)
	]
	return matches[0] if len(matches) == 1 else None


def compute_fx_deduction(
	bt_withdrawal: float,
	allocated: float,
	*,
	loss_account: str,
	gain_account: str,
) -> dict | None:
	"""Deduction row that zeroes a Payment Entry's difference_amount for an FX card charge.

	ERPNext (Pay): difference_amount = base_paid_amount - base_total_allocated_amount -
	sum(deductions.amount). We want it zero, so:

	    deduction.amount = bt_withdrawal - allocated

	A POSITIVE amount debits an expense account → the bank charged MORE CZK than the invoice
	(exchange loss → account 563). A NEGATIVE amount credits an income account → the bank
	charged LESS (exchange gain → account 663). Returns None when the amounts are equal.
	"""
	diff = round(float(bt_withdrawal) - float(allocated), 2)
	if abs(diff) < 0.005:
		return None
	return {"account": loss_account if diff > 0 else gain_account, "amount": diff}


def journal_entry_payload(
	*,
	company: str,
	posting_date,
	amount: float,
	debit_account: str,
	credit_account: str,
	cost_center: str | None,
	remark: str,
	reference_no: str | None = None,
	reference_date=None,
) -> dict:
	"""Build the Journal Entry doc dict for an auto_je rule (Bank Entry: debit the expense
	account, credit the bank contra account). cost_center is attached to the debit
	(expense) line only."""
	debit_line = {
		"account": debit_account,
		"debit_in_account_currency": amount,
		"credit_in_account_currency": 0,
	}
	if cost_center:
		debit_line["cost_center"] = cost_center
	return {
		"doctype": "Journal Entry",
		"voucher_type": "Bank Entry",
		"company": company,
		"posting_date": posting_date,
		# ERPNext validates "Reference No & Reference Date is required for Bank Entry"
		# on submit — a Bank Entry JE without cheque_no/cheque_date cannot be submitted.
		"cheque_no": reference_no or "bank",
		"cheque_date": reference_date or posting_date,
		"user_remark": remark,
		"accounts": [
			debit_line,
			{
				"account": credit_account,
				"debit_in_account_currency": 0,
				"credit_in_account_currency": amount,
			},
		],
	}


def build_je_remark(remark_template: str | None, description: str | None, *, max_len: int = 140) -> str:
	"""Compose the JE user_remark from an optional template + a shortened one-line BT
	description."""
	desc = " ".join((description or "").split())
	if len(desc) > max_len:
		desc = desc[:max_len].rstrip() + "…"
	tmpl = (remark_template or "").strip()
	if tmpl and desc:
		return f"{tmpl} | {desc}"
	return tmpl or desc
