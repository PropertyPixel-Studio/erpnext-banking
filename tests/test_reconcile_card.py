"""Card / rules-engine branch dispatch inside reconcile._reconcile_outgoing and
_reconcile_by_rules (frappe mocked, side effects stubbed)."""

from datetime import date
from unittest.mock import MagicMock

import pytest

import erpnext_banking.reconcile as rec
from erpnext_banking.reconcile import _reconcile_by_rules, _reconcile_outgoing


@pytest.fixture(autouse=True)
def _reset_frappe(monkeypatch):
	import frappe

	monkeypatch.setattr(frappe, "get_all", MagicMock(return_value=[]))
	monkeypatch.setattr(frappe, "logger", MagicMock(return_value=MagicMock()))
	monkeypatch.setattr(frappe, "log_error", MagicMock())
	# getdate is used by _reconcile_card_merchant via reconcile's import of frappe.utils
	monkeypatch.setattr(rec, "getdate", lambda d: d)
	yield


class _AttrDict(dict):
	def __getattr__(self, name):
		try:
			return self[name]
		except KeyError:
			raise AttributeError(name)


class _BT:
	def __init__(self, withdrawal, description, d=date(2026, 7, 17)):
		self.withdrawal = withdrawal
		self.deposit = 0.0
		self.date = d
		self.reference_number = "2164"  # card number, not a VS
		self.description = description
		self.party = None
		self.party_type = None
		self.name = "BT-CARD"


class _Settings:
	def __init__(self, company="propix group s.r.o.", auto_journal_entries=1):
		self.company = company
		self.auto_journal_entries = auto_journal_entries
		self.fx_tolerance_percent = 4.0
		self.default_cost_center = "Main - PXG"


# --- dispatch ------------------------------------------------------------------------


def test_no_rule_returns_none(monkeypatch):
	monkeypatch.setattr(rec, "_load_rules", lambda: [])
	bt = _BT(90.0, "Nákup UNKNOWN MERCHANT")
	assert _reconcile_by_rules(bt, _Settings()) is None


def test_ignore_rule_skips_without_error(monkeypatch):
	monkeypatch.setattr(
		rec,
		"_load_rules",
		lambda: [{"pattern": "REFUND", "rule_type": "ignore", "priority": 1, "enabled": 1}],
	)
	bt = _BT(90.0, "REFUND vrácení")
	result = _reconcile_by_rules(bt, _Settings())
	assert result is not None
	assert result.matched == 0 and result.errors == 0 and result.unresolved == 0


def test_merchant_rule_creates_payment_entry(monkeypatch):
	import frappe

	monkeypatch.setattr(
		rec,
		"_load_rules",
		lambda: [
			{
				"pattern": "ANTHROPIC",
				"rule_type": "merchant_supplier",
				"supplier": "Anthropic, PBC",
				"priority": 100,
				"enabled": 1,
			}
		],
	)
	frappe.get_all.return_value = [
		_AttrDict(name="PI-1", outstanding_amount=2177.55, original_amount=90.0, original_currency="EUR")
	]
	created = MagicMock()
	monkeypatch.setattr(rec, "_create_card_payment_entry", created)

	bt = _BT(2241.84, "Nákup: ANTHROPIC* CLAUDE SUB, dne 17.7.2026, částka  90.00 EUR")
	result = _reconcile_by_rules(bt, _Settings())

	created.assert_called_once()
	assert created.call_args[0][1]["name"] == "PI-1"
	assert result.matched == 1


def test_merchant_rule_ambiguous_candidates_unresolved(monkeypatch):
	import frappe

	monkeypatch.setattr(
		rec,
		"_load_rules",
		lambda: [
			{
				"pattern": "ANTHROPIC",
				"rule_type": "merchant_supplier",
				"supplier": "Anthropic, PBC",
				"priority": 100,
				"enabled": 1,
			}
		],
	)
	# Two invoices both inside FX tolerance → ambiguous → no PE.
	frappe.get_all.return_value = [
		_AttrDict(name="PI-1", outstanding_amount=2177.55, original_amount=None, original_currency=None),
		_AttrDict(name="PI-2", outstanding_amount=2200.0, original_amount=None, original_currency=None),
	]
	created = MagicMock()
	monkeypatch.setattr(rec, "_create_card_payment_entry", created)

	bt = _BT(2241.84, "Nákup: ANTHROPIC* CLAUDE SUB, částka  90.00 EUR")
	result = _reconcile_by_rules(bt, _Settings())

	created.assert_not_called()
	assert result.unresolved == 1


def test_auto_je_rule_creates_journal_entry(monkeypatch):
	monkeypatch.setattr(
		rec,
		"_load_rules",
		lambda: [
			{
				"pattern": "2371741018/3030",
				"rule_type": "auto_je",
				"je_account": "521 - Mzdové náklady - PXG",
				"remark_template": "mzdy",
				"priority": 50,
				"enabled": 1,
			}
		],
	)
	created = MagicMock()
	monkeypatch.setattr(rec, "_create_auto_je", created)

	bt = _BT(6700.0, "Protiúčet: 2371741018/3030\nZpráva: mzda")
	result = _reconcile_by_rules(bt, _Settings())

	created.assert_called_once()
	assert result.matched == 1


def test_auto_je_gated_off_returns_none(monkeypatch):
	monkeypatch.setattr(
		rec,
		"_load_rules",
		lambda: [
			{
				"pattern": "Poplatek",
				"rule_type": "auto_je",
				"je_account": "568 - Ostatní finanční náklady - PXG",
				"priority": 100,
				"enabled": 1,
			}
		],
	)
	created = MagicMock()
	monkeypatch.setattr(rec, "_create_auto_je", created)

	bt = _BT(50.0, "Zpráva: Poplatek za vedení")
	result = _reconcile_by_rules(bt, _Settings(auto_journal_entries=0))

	created.assert_not_called()
	assert result is None


# --- _reconcile_outgoing wiring ------------------------------------------------------


def test_outgoing_no_supplier_enters_rules_branch(monkeypatch):
	# No supplier, no VS-matchable PI → card branch is consulted.
	branch = MagicMock(return_value=rec.ReconcileResult(matched=1))
	monkeypatch.setattr(rec, "_reconcile_by_rules", branch)

	bt = _BT(90.0, "Nákup: ANTHROPIC")
	result = _reconcile_outgoing(bt, _Settings())

	branch.assert_called_once()
	assert result.matched == 1


def test_outgoing_rules_branch_returns_none_falls_through(monkeypatch):
	import frappe

	monkeypatch.setattr(rec, "_reconcile_by_rules", MagicMock(return_value=None))
	frappe.get_all.return_value = []  # Rule 3 finds nothing

	bt = _BT(90.0, "Nákup: UNKNOWN")
	bt.reference_number = ""  # no VS either
	result = _reconcile_outgoing(bt, _Settings())

	assert result.unresolved == 1
