"""Matching salary payments to Salary Slips: pure rules (`_salary.py`). Made-up data only."""

from datetime import date

import pytest

from erpnext_banking._salary import (
	has_account,
	normalize_account,
	payment_window,
	pick_payment,
	pick_slip,
	same_account,
	slip_window,
)

ACC = "1000000001/0100"
OTHER = "2000000002/0300"
END = date(2026, 6, 30)


def slip(name="SLIP-1", amount=13571.0, end=END, accounts=(ACC,)):
	return {"name": name, "amount": amount, "end_date": end, "accounts": list(accounts)}


def payment(name="BT-1", amount=13571.0, on=date(2026, 7, 10), account=ACC):
	return {"name": name, "amount": amount, "date": on, "counter": normalize_account(account)}


# --- accounts ------------------------------------------------------------------------


@pytest.mark.parametrize(
	"text,expected",
	[
		("1000000001/0100", ("1000000001", "0100")),
		("0001000000001/0100", None),  # too many digits for an account number
		("000000-1000000001/0100", ("1000000001", "0100")),  # empty prefix
		("000019-0000000123/0100", ("19-123", "0100")),
		("19-123/0100", ("19-123", "0100")),
		(" 19 - 123 / 0100 ", ("19-123", "0100")),
		("1000000001", ("1000000001", None)),
		("CZ6508000000192000145399", ("19-2000145399", "0800")),  # the usual IBAN example
		("cz65 0800 0000 1920 0014 5399", ("19-2000145399", "0800")),
		("", None),
		(None, None),
		("Převodem na účet", None),
		("0/0100", None),
		("12a45/0100", None),
	],
)
def test_normalize_account(text, expected):
	assert normalize_account(text) == expected


def test_same_account():
	a = normalize_account("19-123/0100")
	assert same_account(a, normalize_account("000019-0000000123/0100"))
	assert not same_account(a, normalize_account("19-123/0300"))  # another bank
	assert not same_account(a, normalize_account("19-124/0100"))
	assert not same_account(a, normalize_account("123/0100"))  # the prefix is part of the account
	# the bank code has to agree only when both sides have one
	assert same_account(a, normalize_account("19-123"))
	assert not same_account(a, None) and not same_account(None, None)


def test_has_account():
	counter = normalize_account(ACC)
	assert has_account(["nesmysl", None, OTHER, "0000001000000001/0100"[6:]], counter)
	assert not has_account([OTHER, "", None], counter)
	assert not has_account([ACC], None)
	assert not has_account(None, counter)


# --- windows -------------------------------------------------------------------------


def test_windows_are_two_views_of_one_rule():
	start, end = payment_window(END)
	assert (start, end) == (date(2026, 6, 20), date(2026, 8, 14))
	for paid_on in (start, end, date(2026, 7, 10)):
		lo, hi = slip_window(paid_on)
		assert lo <= END <= hi
	for paid_on in (date(2026, 6, 19), date(2026, 8, 15)):
		lo, hi = slip_window(paid_on)
		assert not lo <= END <= hi


# --- payment -> slip -----------------------------------------------------------------


def test_pick_slip():
	counter = normalize_account(ACC)
	assert pick_slip(13571.0, date(2026, 7, 10), counter, [slip()])["name"] == "SLIP-1"
	# within the rounding tolerance
	assert pick_slip(13571.9, date(2026, 7, 10), counter, [slip()])["name"] == "SLIP-1"


@pytest.mark.parametrize(
	"amount,on,account",
	[
		(13570.0 - 1, date(2026, 7, 10), ACC),  # another amount
		(13571.0, date(2026, 9, 1), ACC),  # too late for that period
		(13571.0, date(2026, 6, 1), ACC),  # too early
		(13571.0, date(2026, 7, 10), OTHER),  # not the employee's account
	],
)
def test_pick_slip_needs_all_signals(amount, on, account):
	assert pick_slip(amount, on, normalize_account(account), [slip()]) is None


def test_pick_slip_without_counter_account():
	# amount alone is never enough
	assert pick_slip(13571.0, date(2026, 7, 10), None, [slip()]) is None


def test_pick_slip_ambiguity():
	counter = normalize_account(ACC)
	# two slips of the same person with the same pay in the window (two periods)
	two = [slip("MAY", end=date(2026, 5, 31)), slip("JUNE")]
	assert pick_slip(13571.0, date(2026, 7, 5), counter, two) is None
	# the date tells them apart once only one period fits
	assert pick_slip(13571.0, date(2026, 8, 1), counter, two)["name"] == "JUNE"
	# the same pay of two people is not ambiguous: the account decides
	people = [slip("A"), slip("B", accounts=(OTHER,))]
	assert pick_slip(13571.0, date(2026, 7, 5), counter, people)["name"] == "A"


def test_pick_slip_accounts_from_more_sources():
	counter = normalize_account("000000-1000000001/0100")
	s = slip(accounts=("neni ucet", OTHER, "CZ0001000000001000000001"))
	assert normalize_account("CZ0001000000001000000001") == ("1000000001", "0100")
	assert pick_slip(13571.0, date(2026, 7, 10), counter, [s])["name"] == "SLIP-1"


# --- slip -> payment -----------------------------------------------------------------


def test_pick_payment():
	assert pick_payment(slip(), [payment()])["name"] == "BT-1"
	noise = [
		payment("other-account", account=OTHER),
		payment("other-amount", amount=5000.0),
		payment("too-late", on=date(2026, 10, 1)),
		payment("no-counter", account=None),
	]
	assert pick_payment(slip(), [*noise, payment()])["name"] == "BT-1"
	assert pick_payment(slip(), noise) is None
	assert pick_payment(slip(), []) is None


def test_pick_payment_ambiguity():
	# the same amount sent twice to the same account: a person has to decide
	assert pick_payment(slip(), [payment("A"), payment("B", on=date(2026, 7, 12))]) is None


def test_no_frappe_import():
	import ast
	import pathlib

	path = pathlib.Path(__file__).parents[1] / "erpnext_banking" / "_salary.py"
	for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
		if isinstance(node, ast.Import):
			assert not any(a.name.split(".")[0] == "frappe" for a in node.names)
		if isinstance(node, ast.ImportFrom):
			assert (node.module or "").split(".")[0] != "frappe"
