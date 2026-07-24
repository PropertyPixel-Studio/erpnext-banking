"""Tests for the invoice-match ambiguity guard (reconcile._find_unique + call sites).

Bug this closes: `frappe.db.get_value(...)` silently returns "the first" row when a
filter matches several invoices — e.g. supplier FITE bills the identical amount
every month, so `{supplier, variable_symbol}` (and even `{supplier, amount-window}`)
can match more than one open Purchase Invoice. Picking "the first" one allocated
payments to the wrong invoice. The fix: use frappe.get_all and only match when
exactly one candidate comes back; on >1 candidates, skip (leave Unreconciled).
"""

from unittest.mock import MagicMock

import pytest

from erpnext_banking.reconcile import _find_unique, _reconcile_outgoing


@pytest.fixture(autouse=True)
def _reset_frappe(monkeypatch):
	import frappe

	monkeypatch.setattr(frappe, "get_all", MagicMock(return_value=[]))
	monkeypatch.setattr(frappe, "logger", MagicMock(return_value=MagicMock()))
	monkeypatch.setattr(frappe, "log_error", MagicMock())
	yield


class _AttrDict(dict):
	"""Minimal stand-in for frappe._dict — reconcile.py mixes bracket access
	(new `_find_unique` call sites) with attribute access (pre-existing Rule 2
	amount-window candidates), just like real frappe.get_all() rows support both."""

	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name)


def _pi(name, outstanding):
	return _AttrDict(name=name, outstanding_amount=outstanding)


class _BT:
	"""Stand-in for a Bank Transaction doc — only the attributes reconcile code reads."""

	def __init__(self, withdrawal, date, reference_number, party=None, party_type=None):
		self.withdrawal = withdrawal
		self.deposit = 0.0
		self.date = date
		self.reference_number = reference_number
		self.party = party
		self.party_type = party_type
		self.name = "BT-TEST"


class _Settings:
	def __init__(self, company="Propix s.r.o."):
		self.company = company


# --- _find_unique itself -----------------------------------------------------------


def test_find_unique_returns_none_for_zero_candidates():
	import frappe

	frappe.get_all.return_value = []
	assert _find_unique("Purchase Invoice", {"supplier": "FITE"}, ["name"]) is None


def test_find_unique_returns_the_record_for_exactly_one_candidate():
	import frappe

	frappe.get_all.return_value = [_pi("PI-0001", 5787.0)]
	result = _find_unique("Purchase Invoice", {"supplier": "FITE"}, ["name", "outstanding_amount"])
	assert result == _pi("PI-0001", 5787.0)


def test_find_unique_returns_none_and_logs_for_multiple_candidates():
	import frappe

	frappe.get_all.return_value = [_pi("PI-0001", 5787.0), _pi("PI-0002", 5787.0)]
	result = _find_unique("Purchase Invoice", {"supplier": "FITE"}, ["name", "outstanding_amount"])
	assert result is None
	frappe.logger.assert_called_once_with("erpnext_banking")


# --- _reconcile_outgoing Rule 1 (supplier + VS) -------------------------------------


def test_outgoing_rule1_two_candidate_invoices_same_amount_and_vs_no_match(monkeypatch):
	"""The FITE case: two open Purchase Invoices for the same supplier, same VS, same
	5787 Kč amount. Must NOT auto-reconcile — left Unreconciled for manual review."""
	from datetime import date

	import frappe

	frappe.get_all.return_value = [_pi("PI-0010", 5787.0), _pi("PI-0011", 5787.0)]
	try_pay = MagicMock()
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(
		withdrawal=5787.0,
		date=date(2026, 6, 5),
		reference_number="2026001",
		party="FITE s.r.o.",
		party_type="Supplier",
	)
	result = _reconcile_outgoing(bt, _Settings())

	try_pay.assert_not_called()
	assert result.matched == 0
	assert result.unresolved == 1


def test_outgoing_rule1_single_candidate_invoice_matches(monkeypatch):
	"""Same shape as above but only one open PI for this supplier+VS → auto-reconcile."""
	from datetime import date

	import frappe

	frappe.get_all.return_value = [_pi("PI-0010", 5787.0)]
	try_pay = MagicMock(return_value="RECONCILED")
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(
		withdrawal=5787.0,
		date=date(2026, 6, 5),
		reference_number="2026001",
		party="FITE s.r.o.",
		party_type="Supplier",
	)
	result = _reconcile_outgoing(bt, _Settings())

	try_pay.assert_called_once_with(bt, "Purchase Invoice", "PI-0010", 5787.0)
	assert result == "RECONCILED"


def test_outgoing_rule1_ambiguous_falls_through_to_rule2_not_just_unresolved(monkeypatch):
	"""Rule 1 ambiguity must fall through to the later rules (Rule 2: supplier + amount
	+ date window), not stop reconciliation outright — same contract as before."""
	from datetime import date

	import frappe

	# Rule 1 filter (supplier+vs+docstatus+outstanding+company) → 2 candidates (ambiguous)
	# Rule 2 filter (supplier+docstatus+outstanding+company+posting_date window) → 1 candidate
	calls = []

	def fake_get_all(doctype, filters=None, fields=None):
		calls.append(filters)
		if "variable_symbol" in filters:
			return [_pi("PI-0010", 5787.0), _pi("PI-0011", 5787.0)]
		return [_pi("PI-0099", 5787.0)]

	frappe.get_all.side_effect = fake_get_all
	try_pay = MagicMock(return_value="RECONCILED-VIA-RULE2")
	monkeypatch.setattr("erpnext_banking.reconcile._try_pay_and_reconcile", try_pay)

	bt = _BT(
		withdrawal=5787.0,
		date=date(2026, 6, 5),
		reference_number="2026001",
		party="FITE s.r.o.",
		party_type="Supplier",
	)
	result = _reconcile_outgoing(bt, _Settings())

	assert result == "RECONCILED-VIA-RULE2"
	try_pay.assert_called_once_with(bt, "Purchase Invoice", "PI-0099", 5787.0)
