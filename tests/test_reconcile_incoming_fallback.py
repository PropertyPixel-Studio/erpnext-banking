"""Tests for the incoming-fallback matching in reconcile._reconcile_incoming
(_reconcile_incoming_fallback + _customer_from_counter_account).

Bug this closes: a payer sending a wrong/garbled VS (real case — Studio BARÁK sent VS
260100001, the invoice was 20260002) left the deposit permanently Unreconciled, because
_reconcile_incoming only ever matched on exact reference_number == variable_symbol. This
adds two fallback layers, only reached once both VS lookups (Sales Invoice, Payment
Request) come back empty:

  (a) identify the Customer from the counterparty bank account ("Protiúčet: acc/bank" in
      bt.description, mirroring sync._try_attach_supplier's Bank Account lookup for
      Supplier) and require exactly one of that Customer's open Sales Invoices to match
      the deposit amount (±1 Kč);
  (b) no Customer identified — company-wide amount+window match (posting_date within
      bt.date -35..+7 days), again requiring exactly one candidate.

Both layers reuse the ambiguity discipline of the rest of reconcile.py: 0 or >1
candidates => skip, left Unreconciled for manual review.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from erpnext_banking.reconcile import _reconcile_incoming, _reconcile_incoming_fallback


@pytest.fixture(autouse=True)
def _reset_frappe(monkeypatch):
	import frappe

	monkeypatch.setattr(frappe, "get_all", MagicMock(return_value=[]))
	monkeypatch.setattr(frappe, "logger", MagicMock(return_value=MagicMock()))
	monkeypatch.setattr(frappe, "log_error", MagicMock())
	monkeypatch.setattr(frappe.db, "get_value", MagicMock(return_value=None))
	# incoming_window(getdate(bt.date)) — keep dates untouched, like test_reconcile_card.py
	import erpnext_banking.reconcile as rec

	monkeypatch.setattr(rec, "getdate", lambda d: d)
	yield


class _BT:
	"""Stand-in for a Bank Transaction doc — only the attributes reconcile code reads."""

	def __init__(self, deposit, d, reference_number, description="", name="BT-INCOMING"):
		self.deposit = deposit
		self.withdrawal = 0.0
		self.date = d
		self.reference_number = reference_number
		self.description = description
		self.name = name


class _Settings:
	def __init__(self, company="propix group s.r.o."):
		self.company = company


def _si(name, outstanding, posting_date=None):
	return {"name": name, "outstanding_amount": outstanding, "posting_date": posting_date}


def _dispatcher(*, vs_matches=None, pr_matches=None, customer_matches=None, window_pool=None):
	"""Build a frappe.get_all side_effect that routes by doctype/filter shape, mimicking
	what the real DB filters (variable_symbol / customer / posting_date window) would
	already have narrowed down — same style as test_reconcile_ambiguity's fake_get_all."""
	vs_matches = vs_matches if vs_matches is not None else []
	pr_matches = pr_matches if pr_matches is not None else []
	customer_matches = customer_matches if customer_matches is not None else []
	window_pool = window_pool if window_pool is not None else []

	def fake_get_all(doctype, filters=None, fields=None, **kwargs):
		filters = filters or {}
		if doctype == "Payment Request":
			return pr_matches
		if doctype == "Sales Invoice" and "variable_symbol" in filters:
			return vs_matches
		if doctype == "Sales Invoice" and "customer" in filters:
			return customer_matches
		if doctype == "Sales Invoice" and "posting_date" in filters:
			lo, hi = filters["posting_date"][1]
			return [c for c in window_pool if lo <= c["posting_date"] <= hi]
		return []

	return fake_get_all


# --- (a) Customer via counter-account -----------------------------------------------


def test_wrong_vs_customer_via_counter_account_matches(monkeypatch):
	"""Studio BARÁK case: wrong VS, but the counterparty account identifies the Customer,
	and that Customer has exactly one open SI matching the deposit amount."""
	import frappe

	frappe.db.get_value.return_value = "Studio BARÁK s.r.o."
	frappe.get_all.side_effect = _dispatcher(
		customer_matches=[_si("SI-BARAK-0002", 4500.0)],
	)
	try_pay = MagicMock(return_value="RECONCILED-VIA-CUSTOMER")
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(
		deposit=4500.0,
		d=date(2026, 7, 15),
		reference_number="260100001",  # wrong VS — real invoice VS is 20260002
		description="Protiúčet: 2200123456/2010\nZpráva: uhrada faktury",
	)
	result = _reconcile_incoming(bt, _Settings())

	frappe.db.get_value.assert_called_once_with(
		"Bank Account",
		{"account_number": "2200123456", "branch_code": "2010", "party_type": "Customer"},
		"party",
	)
	try_pay.assert_called_once_with(bt, "Sales Invoice", "SI-BARAK-0002", 4500.0)
	assert result == "RECONCILED-VIA-CUSTOMER"


def test_customer_lookup_falls_back_to_bank_account_no(monkeypatch):
	"""account_number+branch_code misses (e.g. Bank Account only holds the IBAN) — falls
	back to a bank_account_no-only match, same as sync._try_attach_supplier's supplier
	fallback."""
	import frappe

	frappe.db.get_value.side_effect = [None, "Studio BARÁK s.r.o."]
	frappe.get_all.side_effect = _dispatcher(customer_matches=[_si("SI-BARAK-0002", 4500.0)])
	try_pay = MagicMock(return_value="RECONCILED-VIA-IBAN")
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(4500.0, date(2026, 7, 15), "260100001", "Protiúčet: 2200123456/2010")
	result = _reconcile_incoming(bt, _Settings())

	assert frappe.db.get_value.call_count == 2
	assert result == "RECONCILED-VIA-IBAN"


def test_customer_identified_but_two_matching_invoices_skips(monkeypatch):
	"""Customer determined, but 2 open SI both match the deposit amount → ambiguous, must
	NOT fall through to the window-only fallback (we already know who paid)."""
	import frappe

	frappe.db.get_value.return_value = "Studio BARÁK s.r.o."
	frappe.get_all.side_effect = _dispatcher(
		customer_matches=[_si("SI-1", 4500.0), _si("SI-2", 4500.0)],
	)
	try_pay = MagicMock()
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(4500.0, date(2026, 7, 15), "260100001", "Protiúčet: 2200123456/2010")
	result = _reconcile_incoming(bt, _Settings())

	try_pay.assert_not_called()
	assert result.matched == 0
	assert result.unresolved == 1


def test_customer_identified_but_no_amount_match_skips_without_window_fallback(monkeypatch):
	"""Customer determined, but none of their open SI match the deposit amount — skip, do
	NOT widen to the company-wide window search (that's reserved for the no-Customer case)."""
	import frappe

	frappe.db.get_value.return_value = "Studio BARÁK s.r.o."
	frappe.get_all.side_effect = _dispatcher(
		customer_matches=[_si("SI-1", 9999.0)],
		window_pool=[_si("SI-OTHER", 4500.0, posting_date=date(2026, 7, 10))],
	)
	try_pay = MagicMock()
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(4500.0, date(2026, 7, 15), "260100001", "Protiúčet: 2200123456/2010")
	result = _reconcile_incoming(bt, _Settings())

	try_pay.assert_not_called()
	assert result.unresolved == 1


# --- (b) No Customer — company-wide amount+window fallback --------------------------


def test_wrong_vs_no_counter_account_unique_amount_in_window_matches(monkeypatch):
	"""No 'Protiúčet:' token in the description (no counter-account info at all) — falls
	to the window-only search; exactly one open SI matches amount+window → match."""
	import frappe

	frappe.get_all.side_effect = _dispatcher(
		window_pool=[_si("SI-0099", 2500.0, posting_date=date(2026, 7, 10))],
	)
	try_pay = MagicMock(return_value="RECONCILED-VIA-WINDOW")
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(2500.0, date(2026, 7, 15), "999999999", description="Zpráva: bez protiuctu")
	result = _reconcile_incoming(bt, _Settings())

	frappe.db.get_value.assert_not_called()  # no Protiúčet token → no Bank Account lookup at all
	try_pay.assert_called_once_with(bt, "Sales Invoice", "SI-0099", 2500.0)
	assert result == "RECONCILED-VIA-WINDOW"


def test_two_same_amount_invoices_in_window_skips(monkeypatch):
	"""Two open SI, same amount, both inside the window → ambiguous, skip."""
	import frappe

	frappe.get_all.side_effect = _dispatcher(
		window_pool=[
			_si("SI-A", 2500.0, posting_date=date(2026, 7, 10)),
			_si("SI-B", 2500.0, posting_date=date(2026, 7, 12)),
		],
	)
	try_pay = MagicMock()
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(2500.0, date(2026, 7, 15), "999999999")
	result = _reconcile_incoming(bt, _Settings())

	try_pay.assert_not_called()
	assert result.matched == 0
	assert result.unresolved == 1
	frappe.logger.assert_called_with("erpnext_banking")


def test_matching_invoice_outside_window_skips(monkeypatch):
	"""Amount matches, but posting_date is outside bt.date -35..+7 days → not a
	candidate at all (filtered at the DB layer), left Unreconciled."""
	import frappe

	# bt.date = 2026-07-15 → window is 2026-06-10..2026-07-22. This invoice posted
	# 2026-05-01, well before the window.
	frappe.get_all.side_effect = _dispatcher(
		window_pool=[_si("SI-TOO-OLD", 2500.0, posting_date=date(2026, 5, 1))],
	)
	try_pay = MagicMock()
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(2500.0, date(2026, 7, 15), "999999999")
	result = _reconcile_incoming(bt, _Settings())

	try_pay.assert_not_called()
	assert result.unresolved == 1


def test_window_bounds_are_minus_35_plus_7_days(monkeypatch):
	"""Direct check of _reconcile_incoming_fallback's window math: a candidate exactly on
	the -35 boundary matches, one day further out does not."""
	import frappe

	bt_date = date(2026, 7, 15)
	lower_bound = date(2026, 7, 15) - __import__("datetime").timedelta(days=35)  # 2026-06-10
	just_outside = lower_bound - __import__("datetime").timedelta(days=1)  # 2026-06-09

	frappe.get_all.side_effect = _dispatcher(
		window_pool=[_si("SI-EDGE", 100.0, posting_date=lower_bound)],
	)
	try_pay = MagicMock(return_value="RECONCILED-EDGE")
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(100.0, bt_date, "")
	result = _reconcile_incoming_fallback(bt, _Settings())
	assert result == "RECONCILED-EDGE"

	# Now push the only candidate one day before the lower bound — no longer a candidate.
	frappe.get_all.side_effect = _dispatcher(
		window_pool=[_si("SI-EDGE2", 100.0, posting_date=just_outside)],
	)
	try_pay.reset_mock()
	result2 = _reconcile_incoming_fallback(bt, _Settings())
	try_pay.assert_not_called()
	assert result2.unresolved == 1


# --- exact VS is unaffected -----------------------------------------------------------


def test_exact_vs_still_matches_without_reaching_fallback(monkeypatch):
	"""Correct VS finds the Sales Invoice on the first lookup — the fallback function
	must never even be called."""
	import frappe

	frappe.get_all.side_effect = _dispatcher(vs_matches=[_si("SI-EXACT", 3000.0)])
	create_pe = MagicMock()
	monkeypatch.setattr("erpnext_banking.reconcile._create_payment_entry_and_reconcile", create_pe)
	fallback = MagicMock(side_effect=AssertionError("fallback must not run on an exact VS match"))
	monkeypatch.setattr("erpnext_banking.reconcile._reconcile_incoming_fallback", fallback)

	bt = _BT(3000.0, date(2026, 7, 15), "2026001")
	result = _reconcile_incoming(bt, _Settings())

	fallback.assert_not_called()
	create_pe.assert_called_once_with(bt, "Sales Invoice", "SI-EXACT", 3000.0)
	assert result.matched == 1


def test_exact_vs_no_match_falls_through_to_fallback(monkeypatch):
	"""VS present but doesn't match anything — the (now-existing) fallback path is
	reached, not an immediate unresolved (this is the behavior change from before)."""
	import frappe

	frappe.get_all.side_effect = _dispatcher()  # nothing anywhere
	bt = _BT(3000.0, date(2026, 7, 15), "260100001")
	result = _reconcile_incoming(bt, _Settings())

	# No customer, no window candidates → still unresolved, but via the fallback path.
	assert result.unresolved == 1
