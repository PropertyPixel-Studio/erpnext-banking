"""Auto Journal Entry payload + remark building for auto_je rules (salaries, fees)."""

from datetime import date

from erpnext_banking._card import (
	build_je_remark,
	journal_entry_payload,
	rule_matches,
	select_rule,
)

# --- salary rule selection -----------------------------------------------------------


def test_salary_rule_matches_counterparty_account():
	rules = [
		{
			"pattern": "2371741018/3030",
			"rule_type": "auto_je",
			"priority": 50,
			"enabled": 1,
			"je_account": "521 - Mzdové náklady - PXG",
			"remark_template": "mzdy",
		},
	]
	desc = "Název: Jan Novák\nProtiúčet: 2371741018/3030\nZpráva: mzda 06/2026"
	chosen = select_rule(rules, desc)
	assert chosen["je_account"] == "521 - Mzdové náklady - PXG"


def test_fee_rule_matches_poplatek():
	assert rule_matches("Poplatek", "Zpráva: Poplatek za vedení účtu")


# --- journal_entry_payload -----------------------------------------------------------


def test_je_payload_debits_expense_credits_bank():
	payload = journal_entry_payload(
		company="propix group s.r.o.",
		posting_date=date(2026, 7, 17),
		amount=6700.0,
		debit_account="521 - Mzdové náklady - PXG",
		credit_account="221 - Bankovní účty CZK - PXG",
		cost_center="Main - PXG",
		remark="mzdy | výplata",
	)
	assert payload["doctype"] == "Journal Entry"
	assert payload["voucher_type"] == "Bank Entry"
	assert payload["posting_date"] == date(2026, 7, 17)
	debit, credit = payload["accounts"]
	assert debit["account"] == "521 - Mzdové náklady - PXG"
	assert debit["debit_in_account_currency"] == 6700.0
	assert debit["credit_in_account_currency"] == 0
	assert debit["cost_center"] == "Main - PXG"
	assert credit["account"] == "221 - Bankovní účty CZK - PXG"
	assert credit["credit_in_account_currency"] == 6700.0
	assert credit["debit_in_account_currency"] == 0


def test_je_payload_balances():
	payload = journal_entry_payload(
		company="C",
		posting_date=date(2026, 7, 1),
		amount=1234.56,
		debit_account="568 - Ostatní finanční náklady - PXG",
		credit_account="221 - Bankovní účty CZK - PXG",
		cost_center="Main - PXG",
		remark="poplatek",
	)
	total_debit = sum(a["debit_in_account_currency"] for a in payload["accounts"])
	total_credit = sum(a["credit_in_account_currency"] for a in payload["accounts"])
	assert total_debit == total_credit == 1234.56


def test_je_payload_no_cost_center_on_bank_line():
	payload = journal_entry_payload(
		company="C",
		posting_date=date(2026, 7, 1),
		amount=100.0,
		debit_account="521 - Mzdové náklady - PXG",
		credit_account="221 - Bankovní účty CZK - PXG",
		cost_center="Main - PXG",
		remark="x",
	)
	_, credit = payload["accounts"]
	assert "cost_center" not in credit


def test_je_payload_omits_cost_center_when_none():
	payload = journal_entry_payload(
		company="C",
		posting_date=date(2026, 7, 1),
		amount=100.0,
		debit_account="D",
		credit_account="221 - Bankovní účty CZK - PXG",
		cost_center=None,
		remark="x",
	)
	debit, _ = payload["accounts"]
	assert "cost_center" not in debit


# --- build_je_remark -----------------------------------------------------------------


def test_remark_combines_template_and_description():
	out = build_je_remark("mzdy", "Protiúčet: 2371741018/3030 Zpráva: mzda")
	assert out.startswith("mzdy | ")
	assert "mzda" in out


def test_remark_template_only():
	assert build_je_remark("poplatek", "") == "poplatek"


def test_remark_description_only():
	assert build_je_remark(None, "Zpráva: cosi") == "Zpráva: cosi"


def test_remark_collapses_whitespace_and_truncates():
	long_desc = "A " * 200
	out = build_je_remark("t", long_desc, max_len=20)
	assert out.startswith("t | ")
	assert out.endswith("…")
	assert len(out) <= 4 + 20 + 1


def test_remark_empty_when_both_empty():
	assert build_je_remark("", "") == ""
