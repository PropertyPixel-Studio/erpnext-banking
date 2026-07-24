"""Tests for pure helpers used by reconcile."""

from datetime import date


def test_amount_matches_exact():
	from erpnext_banking._helpers import amount_matches

	assert amount_matches(100.0, 100.0)


def test_amount_matches_within_one_crown():
	from erpnext_banking._helpers import amount_matches

	assert amount_matches(100.50, 101.49)
	assert amount_matches(100.50, 99.51)


def test_amount_matches_rejects_more_than_one_crown():
	from erpnext_banking._helpers import amount_matches

	assert not amount_matches(100.0, 102.0)
	assert not amount_matches(100.0, 98.0)


def test_amount_matches_handles_negative_zero():
	from erpnext_banking._helpers import amount_matches

	assert amount_matches(0.0, 0.0)


def test_outgoing_window_dates():
	from erpnext_banking._helpers import outgoing_window

	bt_date = date(2026, 6, 7)
	d_from, d_to = outgoing_window(bt_date)
	assert d_from == date(2026, 5, 24)  # bt - 14
	assert d_to == date(2026, 6, 14)  # bt + 7


def test_outgoing_window_configurable():
	from erpnext_banking._helpers import outgoing_window

	d_from, d_to = outgoing_window(date(2026, 6, 7), days_back=5, days_forward=3)
	assert d_from == date(2026, 6, 2)
	assert d_to == date(2026, 6, 10)


def test_incoming_window_dates():
	from erpnext_banking._helpers import incoming_window

	bt_date = date(2026, 7, 15)
	d_from, d_to = incoming_window(bt_date)
	assert d_from == date(2026, 6, 10)  # bt - 35
	assert d_to == date(2026, 7, 22)  # bt + 7


def test_incoming_window_configurable():
	from erpnext_banking._helpers import incoming_window

	d_from, d_to = incoming_window(date(2026, 6, 7), days_back=5, days_forward=3)
	assert d_from == date(2026, 6, 2)
	assert d_to == date(2026, 6, 10)


def test_parse_counter_account_with_bank_code():
	from erpnext_banking._helpers import parse_counter_account

	assert parse_counter_account("Protiúčet: 2371741018/3030\nZpráva: mzda") == ("2371741018", "3030")


def test_parse_counter_account_without_bank_code():
	from erpnext_banking._helpers import parse_counter_account

	assert parse_counter_account("Protiúčet: 2371741018\nZpráva: mzda") == ("2371741018", None)


def test_parse_counter_account_multiline_description():
	from erpnext_banking._helpers import parse_counter_account

	desc = "Název: Studio BARÁK s.r.o.\nProtiúčet: 2200123456/2010\nZpráva: uhrada faktury"
	assert parse_counter_account(desc) == ("2200123456", "2010")


def test_parse_counter_account_no_token_returns_none():
	from erpnext_banking._helpers import parse_counter_account

	assert parse_counter_account("Zpráva: bez protiuctu") is None


def test_parse_counter_account_empty_or_none_returns_none():
	from erpnext_banking._helpers import parse_counter_account

	assert parse_counter_account("") is None
	assert parse_counter_account(None) is None
