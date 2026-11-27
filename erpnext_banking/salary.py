"""Salary payments: match outgoing bank transactions to Salary Slips.

Nothing is configured by hand. Whose account a payment went to and how much was due comes
from ERPNext itself: the bank details of the Employee, Bank Accounts of the Employee, the
account on the Salary Slip and its net pay.

Two directions, one rule (`_salary.py`: account + amount + plausible date, unique):

- a new bank transaction looks for its slip (`reconcile_salary`, called by reconcile.py);
- a submitted slip looks for its payment (`match_slip`), also among transactions that are
  already reconciled, so a slip created after its payment still gets linked.

A matched payment is booked like the other no-document withdrawals: a Journal Entry from
the bank contra account to the salary account, linked to the bank transaction. The slip
gets the date and the transaction it was paid by.

Log messages name documents only, never accounts or amounts.
"""

from __future__ import annotations

import json

import frappe
from frappe.utils import add_days, getdate

from ._card import journal_entry_payload
from ._helpers import parse_counter_account
from ._salary import has_account, normalize_account, payment_window, pick_payment, pick_slip, slip_window

SLIP_TRANSACTION = "banking_bank_transaction"
SLIP_PAID_ON = "banking_paid_on"
DEFAULT_SALARY_ACCOUNT = "521 - Mzdové náklady - PXG"
DEFAULT_JE_CONTRA_ACCOUNT = "221 - Bankovní účty CZK - PXG"
DEFAULT_COST_CENTER = "Main - PXG"


def _setting(settings, field: str, default):
	value = getattr(settings, field, None)
	return value if value not in (None, "") else default


def enabled(settings) -> bool:
	return bool(getattr(settings, "auto_reconcile_outgoing", 0)) and bool(
		_setting(settings, "auto_reconcile_salaries", 1)
	)


def counter_account(description: str | None):
	"""Normalized counterparty account of a bank transaction, None when it has none."""
	parsed = parse_counter_account(description)
	if not parsed:
		return None
	return normalize_account(f"{parsed[0]}/{parsed[1]}" if parsed[1] else parsed[0])


# --- whose account is it ---------------------------------------------------------


def _bank_account_texts(filters: dict) -> list[dict]:
	rows = frappe.get_all(
		"Bank Account", filters=filters, fields=["party", "bank_account_no", "branch_code", "iban"]
	)
	out = []
	for r in rows:
		texts = [r.iban]
		if r.bank_account_no:
			# the number may already carry the bank code; otherwise it is in branch_code
			texts.append(
				r.bank_account_no
				if "/" in r.bank_account_no or not r.branch_code
				else f"{r.bank_account_no}/{r.branch_code}"
			)
		out.append({"party": r.party, "accounts": [t for t in texts if t]})
	return out


def employee_accounts(employee: str) -> list[str]:
	"""Accounts ERPNext knows for an employee: bank details of the Employee and its Bank Accounts."""
	emp = frappe.db.get_value("Employee", employee, ["bank_ac_no", "iban"], as_dict=True) or {}
	accounts = [a for a in (emp.get("bank_ac_no"), emp.get("iban")) if a]
	for row in _bank_account_texts({"party_type": "Employee", "party": employee, "disabled": 0}):
		accounts += row["accounts"]
	return accounts


def _slip_accounts(slip) -> list[str]:
	"""Accounts of one slip: of its employee, on the slip itself, and the one read from the
	printed slip (JMHZ module), which a person has reviewed."""
	accounts = employee_accounts(slip.employee)
	if slip.get("bank_account_no"):
		accounts.append(slip.bank_account_no)
	try:
		data = json.loads(slip.get("jmhz_slip_data") or "null") or {}
	except ValueError:
		data = {}
	for block in (data.get("pdf") or {}).get("blocks") or []:
		accounts += [r["value"] for r in block.get("rows", []) if r.get("key") == "ucet" and r.get("value")]
	return accounts


def is_employee_account(counter) -> bool:
	"""The account belongs to an active or former employee, by the master data."""
	if not counter:
		return False
	employees = frappe.get_all("Employee", filters={}, fields=["bank_ac_no", "iban"])
	if any(has_account([e.bank_ac_no, e.iban], counter) for e in employees):
		return True
	return any(has_account(r["accounts"], counter) for r in _bank_account_texts({"party_type": "Employee"}))


# --- candidates ------------------------------------------------------------------

_SLIP_FIELDS = ["name", "employee", "net_pay", "start_date", "end_date", "bank_account_no", "jmhz_slip_data"]


def _slip_dict(slip) -> dict:
	return {
		"name": slip.name,
		"amount": float(slip.net_pay or 0),
		"start_date": getdate(slip.start_date),
		"end_date": getdate(slip.end_date),
		"accounts": _slip_accounts(slip),
	}


def _open_slips(company: str, paid_on) -> list[dict]:
	"""Submitted slips of the company that are not paid yet and could be paid on that day."""
	start, end = slip_window(getdate(paid_on))
	slips = frappe.get_all(
		"Salary Slip",
		filters={
			"company": company,
			"docstatus": 1,
			"end_date": ["between", [start, end]],
			SLIP_TRANSACTION: ["is", "not set"],
		},
		fields=_SLIP_FIELDS,
	)
	return [_slip_dict(s) for s in slips if float(s.net_pay or 0) > 0]


def _taken(bt_name: str, except_slip: str | None = None) -> bool:
	"""A live slip is already linked to the bank transaction."""
	filters = {SLIP_TRANSACTION: bt_name, "docstatus": 1}
	if except_slip:
		filters["name"] = ["!=", except_slip]
	return bool(frappe.db.exists("Salary Slip", filters))


# --- booking -----------------------------------------------------------------------


def _mark_paid(slip_name: str, bt) -> None:
	frappe.db.set_value(
		"Salary Slip", slip_name, {SLIP_TRANSACTION: bt.name, SLIP_PAID_ON: bt.date}, update_modified=False
	)


def _book(bt, slip: dict, settings) -> None:
	"""Journal Entry for the payment, linked to the bank transaction, and the slip marked paid."""
	amount = float(bt.withdrawal)
	payload = journal_entry_payload(
		company=settings.company,
		posting_date=bt.date,
		amount=amount,
		debit_account=_setting(settings, "salary_account", DEFAULT_SALARY_ACCOUNT),
		credit_account=_setting(settings, "je_contra_account", DEFAULT_JE_CONTRA_ACCOUNT),
		cost_center=_setting(settings, "default_cost_center", DEFAULT_COST_CENTER),
		# the slip is enough to find the person; no name or account in the ledger remark
		remark=f"mzda {slip['end_date']:%m/%Y} | {slip['name']}",
		reference_no=(bt.reference_number or bt.name),
		reference_date=bt.date,
	)
	je = frappe.get_doc(payload)
	je.insert(ignore_permissions=True)
	je.submit()
	bt.append(
		"payment_entries",
		{"payment_document": "Journal Entry", "payment_entry": je.name, "allocated_amount": amount},
	)
	bt.save(ignore_permissions=True)
	_mark_paid(slip["name"], bt)


# --- bank transaction -> slip --------------------------------------------------------


def reconcile_salary(bt, settings) -> str | None:
	"""Try to book an Unreconciled withdrawal as the payment of a Salary Slip.

	Returns "matched", "unresolved" (the money went to an employee but no single slip fits:
	leave it for a person, never book it by a generic rule) or None (not a salary payment as
	far as ERPNext knows; the caller goes on with its other rules).
	"""
	if not enabled(settings):
		return None
	counter = counter_account(bt.description)
	if not counter:
		return None
	slips = _open_slips(settings.company, bt.date)
	slip = pick_slip(float(bt.withdrawal), getdate(bt.date), counter, slips)
	if slip and not _taken(bt.name):
		_book(bt, slip, settings)
		frappe.db.commit()
		return "matched"
	known = any(has_account(s["accounts"], counter) for s in slips) or is_employee_account(counter)
	return "unresolved" if known else None


# --- slip -> bank transaction --------------------------------------------------------


def _payments_for(slip: dict, settings) -> list[dict]:
	"""Withdrawals on the provider's bank account around the pay day, with their counter account."""
	start, end = payment_window(slip["end_date"])
	rows = frappe.get_all(
		"Bank Transaction",
		filters={
			"bank_account": settings.bank_account,
			"docstatus": 1,
			"date": ["between", [start, end]],
			"withdrawal": ["between", [slip["amount"] - 1, slip["amount"] + 1]],
		},
		fields=["name", "date", "withdrawal", "description", "status"],
	)
	return [
		{
			"name": r.name,
			"amount": float(r.withdrawal),
			"date": getdate(r.date),
			"counter": counter_account(r.description),
			"status": r.status,
		}
		for r in rows
		if not _taken(r.name, except_slip=slip["name"])
	]


def match_slip(slip_name: str, settings) -> str | None:
	"""Find the payment of one submitted slip. Returns "matched" (booked now), "linked" (the
	payment was already reconciled, only the slip was marked) or None."""
	if not enabled(settings):
		return None
	slip = frappe.db.get_value(
		"Salary Slip", slip_name, [*_SLIP_FIELDS, "docstatus", "company", SLIP_TRANSACTION], as_dict=True
	)
	if not slip or slip.docstatus != 1 or slip.get(SLIP_TRANSACTION) or slip.company != settings.company:
		return None
	if float(slip.net_pay or 0) <= 0:
		return None
	data = _slip_dict(slip)
	payment = pick_payment(data, _payments_for(data, settings))
	if not payment:
		return None
	bt = frappe.get_doc("Bank Transaction", payment["name"])
	if bt.status == "Unreconciled":
		_book(bt, data, settings)
		return "matched"
	_mark_paid(data["name"], bt)  # booked earlier, by a rule or by hand
	return "linked"


def match_open_slips(settings, today=None) -> int:
	"""Nightly: slips in the reconcile window that still have no payment. Returns how many got one."""
	if not enabled(settings):
		return 0
	since = add_days(getdate(today), -int(settings.reconcile_window_days or 90))
	names = frappe.get_all(
		"Salary Slip",
		filters={
			"company": settings.company,
			"docstatus": 1,
			"end_date": [">=", since],
			SLIP_TRANSACTION: ["is", "not set"],
		},
		order_by="end_date asc",
		pluck="name",
	)
	found = 0
	for name in names:
		try:
			if match_slip(name, settings):
				found += 1
				frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(f"Salary payment match failed for slip={name}", "erpnext_banking")
	return found


# --- hooks ---------------------------------------------------------------------------


def on_salary_slip_submit(doc, method=None):
	"""A slip is often submitted after its payment left the bank. Look for the payment once
	the submit is committed; the submit itself must never fail or wait because of this."""
	try:
		frappe.enqueue(
			"erpnext_banking.salary.match_slip_for_all_providers",
			slip_name=doc.name,
			queue="short",
			enqueue_after_commit=True,
		)
	except Exception:
		# the nightly run finds the payment as well
		frappe.log_error(f"Salary payment match not queued for slip={doc.name}", "erpnext_banking")


def match_slip_for_all_providers(slip_name: str) -> None:
	from .providers import iter_enabled_providers

	for provider in iter_enabled_providers():
		try:
			settings = frappe.get_single(provider.settings_doctype)
			if match_slip(slip_name, settings):
				frappe.db.commit()
				return
		except Exception:
			frappe.db.rollback()
			frappe.log_error(f"Salary payment match failed for slip={slip_name}", "erpnext_banking")
