"""Tests for Fio raw record → Bank Transaction kwargs mapping."""

from datetime import date

SAMPLE_DEPOSIT = {
	"column22": {"value": 26500001234, "name": "ID pohybu"},
	"column0": {"value": "2026-06-07+0200", "name": "Datum"},
	"column1": {"value": 5000.0, "name": "Objem"},
	"column14": {"value": "CZK", "name": "Měna"},
	"column5": {"value": "20260607", "name": "VS"},
	"column2": {"value": "123456789", "name": "Protiúčet"},
	"column3": {"value": "0100", "name": "Kód banky"},
	"column10": {"value": "Acme s.r.o.", "name": "Název protiúčtu"},
	"column16": {"value": "Faktura 20260607", "name": "Zpráva"},
	"column7": {"value": None, "name": "Identifikace"},
	"column25": {"value": None, "name": "Komentář"},
}


SAMPLE_WITHDRAWAL = dict(
	SAMPLE_DEPOSIT,
	**{
		"column1": {"value": -1500.50, "name": "Objem"},
		"column5": {"value": "FX-2026-04", "name": "VS"},
	},
)


def test_maps_deposit():
	from erpnext_banking.providers.fio.mapper import to_bank_transaction

	kwargs = to_bank_transaction(SAMPLE_DEPOSIT)
	assert kwargs["transaction_id"] == "26500001234"
	assert kwargs["date"] == date(2026, 6, 7)
	assert kwargs["deposit"] == 5000.0
	assert kwargs["withdrawal"] == 0.0
	assert kwargs["currency"] == "CZK"
	assert kwargs["reference_number"] == "20260607"
	assert "Acme s.r.o." in kwargs["description"]
	assert "123456789/0100" in kwargs["description"]
	assert "Faktura 20260607" in kwargs["description"]


def test_maps_withdrawal_uses_abs_amount():
	from erpnext_banking.providers.fio.mapper import to_bank_transaction

	kwargs = to_bank_transaction(SAMPLE_WITHDRAWAL)
	assert kwargs["deposit"] == 0.0
	assert kwargs["withdrawal"] == 1500.50
	assert kwargs["reference_number"] == "FX-2026-04"


def test_handles_missing_optional_fields():
	from erpnext_banking.providers.fio.mapper import to_bank_transaction

	raw = {
		"column22": {"value": 1, "name": "ID"},
		"column0": {"value": "2026-06-07+0200", "name": "Datum"},
		"column1": {"value": 100.0, "name": "Objem"},
		"column14": {"value": "CZK", "name": "Měna"},
	}
	kwargs = to_bank_transaction(raw)
	assert kwargs["transaction_id"] == "1"
	assert kwargs["reference_number"] == ""
	assert kwargs["deposit"] == 100.0
	assert kwargs["description"] == ""


def test_extracts_party_account_and_bank_code():
	from erpnext_banking.providers.fio.mapper import extract_party_account

	acc, bank = extract_party_account(SAMPLE_DEPOSIT)
	assert acc == "123456789"
	assert bank == "0100"


def test_extracts_party_account_handles_missing():
	from erpnext_banking.providers.fio.mapper import extract_party_account

	acc, bank = extract_party_account({})
	assert acc is None
	assert bank is None
