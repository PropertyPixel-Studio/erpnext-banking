"""Tests for the secondary (no-transaction_id) dedup guard in sync._is_already_inserted.

Bank Transactions can exist without a transaction_id (created via another path than
this app's sync — e.g. manually, or a legacy import). The primary dedup check keys
only on transaction_id, so such records don't block a re-sync and get duplicated.
The secondary check closes that gap: same bank_account/date/deposit/withdrawal/
reference_number, docstatus not cancelled.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from erpnext_banking.sync import _is_already_inserted, _RunContext


def _ctx(bank_account="Fio - CZK", tx_id="12345"):
	"""Build a minimal _RunContext with a fake provider that always maps `raw` to a
	fixed Bank Transaction kwargs dict (transaction_id configurable per test)."""
	provider = MagicMock()
	provider.to_bank_transaction.return_value = {
		"transaction_id": tx_id,
		"date": date(2026, 6, 3),
		"deposit": 0.0,
		"withdrawal": 385.0,
		"currency": "CZK",
		"reference_number": "2026060312345",
		"description": "Název: FACEBK",
	}
	settings = MagicMock()
	settings.bank_account = bank_account
	log = MagicMock()
	return _RunContext(provider, settings, log, dry_run=False)


@pytest.fixture(autouse=True)
def _reset_frappe_db(monkeypatch):
	"""Each test wires up its own frappe.db.exists/get_value/set_value expectations —
	make sure no state leaks between tests (conftest's frappe stub is a module
	singleton shared across the whole test session)."""
	import frappe

	monkeypatch.setattr(frappe.db, "exists", MagicMock(return_value=False))
	monkeypatch.setattr(frappe.db, "get_value", MagicMock(return_value=None))
	monkeypatch.setattr(frappe.db, "set_value", MagicMock())
	yield


def test_primary_transaction_id_match_short_circuits():
	"""If a Bank Transaction with this transaction_id already exists, that's enough —
	the secondary (no-tx-id) check must not even run."""
	import frappe

	frappe.db.exists.return_value = True

	ctx = _ctx()
	raw = {"any": "raw"}

	assert _is_already_inserted(ctx, raw) is True
	frappe.db.get_value.assert_not_called()
	frappe.db.set_value.assert_not_called()


def test_no_primary_and_no_secondary_match_inserts_normally():
	"""Neither transaction_id nor (bank_account, date, amount, ref) matches anything
	existing → not a duplicate, normal insert proceeds."""
	import frappe

	frappe.db.exists.return_value = False
	frappe.db.get_value.return_value = None

	ctx = _ctx()
	assert _is_already_inserted(ctx, {"any": "raw"}) is False
	frappe.db.set_value.assert_not_called()


def test_existing_record_without_transaction_id_gets_backfilled_not_duplicated():
	"""A Bank Transaction with matching bank_account/date/amount/reference_number but
	NO transaction_id (e.g. created outside this app's sync) must not cause a
	duplicate insert. Instead, the existing record is backfilled with the
	transaction_id and treated as 'already inserted'."""
	import frappe

	frappe.db.exists.return_value = False
	frappe.db.get_value.return_value = {"name": "BT-0001", "transaction_id": None}

	ctx = _ctx(tx_id="12345")
	assert _is_already_inserted(ctx, {"any": "raw"}) is True

	frappe.db.set_value.assert_called_once_with("Bank Transaction", "BT-0001", "transaction_id", "12345")


def test_existing_record_with_different_transaction_id_is_left_alone_and_new_one_inserts():
	"""A same-day/same-amount/same-ref record that already HAS a (different)
	transaction_id represents a legitimate separate movement (e.g. two identical
	FACEBK payments on the same day) — do not collide with it, do not touch it,
	let the new transaction insert normally."""
	import frappe

	frappe.db.exists.return_value = False
	frappe.db.get_value.return_value = {"name": "BT-0002", "transaction_id": "OTHER-999"}

	ctx = _ctx(tx_id="12345")
	assert _is_already_inserted(ctx, {"any": "raw"}) is False
	frappe.db.set_value.assert_not_called()


def test_secondary_lookup_uses_bank_account_date_amount_reference_and_excludes_cancelled():
	"""Sanity-check the actual filter shape passed to frappe.db.get_value, so a future
	refactor can't silently drop one of the dedup keys or the docstatus exclusion."""
	import frappe

	frappe.db.exists.return_value = False
	frappe.db.get_value.return_value = None

	ctx = _ctx(bank_account="Fio - CZK", tx_id="12345")
	_is_already_inserted(ctx, {"any": "raw"})

	args, kwargs = frappe.db.get_value.call_args
	assert args[0] == "Bank Transaction"
	filters = args[1]
	assert filters["bank_account"] == "Fio - CZK"
	assert filters["date"] == date(2026, 6, 3)
	assert filters["deposit"] == 0.0
	assert filters["withdrawal"] == 385.0
	assert filters["reference_number"] == "2026060312345"
	assert filters["docstatus"] == ("!=", 2)
