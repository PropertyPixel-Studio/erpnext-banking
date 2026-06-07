"""Map Fio raw record (column-coded dict) → Bank Transaction kwargs."""
from __future__ import annotations

from datetime import date, datetime


def to_bank_transaction(raw: dict) -> dict:
	"""Convert one Fio transaction record to Bank Transaction kwargs.

	Returned keys:
	    transaction_id (str), date (date), deposit (float), withdrawal (float),
	    currency (str), reference_number (str), description (str)
	"""
	amount = _value(raw, "column1") or 0.0
	return {
		"transaction_id": str(_value(raw, "column22") or ""),
		"date": _parse_date(_value(raw, "column0")),
		"deposit": float(amount) if amount > 0 else 0.0,
		"withdrawal": float(abs(amount)) if amount < 0 else 0.0,
		"currency": _value(raw, "column14") or "CZK",
		"reference_number": str(_value(raw, "column5") or ""),
		"description": _build_description(raw),
	}


def extract_party_account(raw: dict) -> tuple[str | None, str | None]:
	"""Return (account_number, bank_code) from Fio raw, or (None, None) if missing."""
	acc = _value(raw, "column2")
	bank = _value(raw, "column3")
	return (str(acc) if acc else None, str(bank) if bank else None)


def _value(raw: dict, key: str):
	cell = raw.get(key)
	if cell is None:
		return None
	if isinstance(cell, dict):
		return cell.get("value")
	return cell


def _parse_date(raw_dt) -> date | None:
	"""Fio date format: '2026-06-07+0200' (ISO + timezone offset suffix)."""
	if not raw_dt:
		return None
	# Strip timezone suffix — Fio dates are date-only semantically
	main = str(raw_dt).split("+")[0].split("Z")[0]
	return datetime.fromisoformat(main).date()


def _build_description(raw: dict) -> str:
	parts: list[str] = []
	party = _value(raw, "column10")
	acc = _value(raw, "column2")
	bank = _value(raw, "column3")
	msg = _value(raw, "column16")
	ident = _value(raw, "column7")
	comment = _value(raw, "column25")

	if party:
		parts.append(f"Název: {party}")
	if acc and bank:
		parts.append(f"Protiúčet: {acc}/{bank}")
	elif acc:
		parts.append(f"Protiúčet: {acc}")
	if msg:
		parts.append(f"Zpráva: {msg}")
	if ident:
		parts.append(f"Identifikace: {ident}")
	if comment:
		parts.append(f"Komentář: {comment}")
	return "\n".join(parts)
