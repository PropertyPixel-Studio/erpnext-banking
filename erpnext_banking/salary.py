"""Salary payments: match outgoing bank transactions to Salary Slips.

Nothing is configured by hand. Whose account a payment went to and how much was due comes
from ERPNext itself: the bank details of the Employee, Bank Accounts of the Employee, the
account on the Salary Slip and its net pay.

Two directions, one rule (`_salary.py`: account + amount + plausible date, unique):

- a new bank transaction looks for its slip (`reconcile_salary`, called by reconcile.py);
- a submitted slip looks for its payment (`match_slip`), also among transactions that are
  already reconciled, so a slip created after its payment still gets linked.

A matched payment is booked by a Journal Entry linked to the bank transaction, and the
slip gets the date and the transaction it was paid by. What the entry debits follows
Czech bookkeeping:

- the slip was booked in its period (accrual, JMHZ Settings): the payment settles the
  payable to the employee, 331 / 221;
- the slip was not booked: the payment goes straight to the wage expense, 521 / 221,
  as the account rules did before.

A payment booked to the expense earlier (by a rule or by hand) that now meets a slip with
an accrual is moved to the payable once (331 / 521), otherwise the wage would be an
expense twice.

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


def _own_accounts(slip) -> list[str]:
	"""Accounts written on one slip: its own field and the one read from the printed slip
	(JMHZ module), which a person has reviewed."""
	accounts = [slip.bank_account_no] if slip.get("bank_account_no") else []
	try:
		data = json.loads(slip.get("jmhz_slip_data") or "null") or {}
	except ValueError:
		data = {}
	for block in (data.get("pdf") or {}).get("blocks") or []:
		accounts += [r["value"] for r in block.get("rows", []) if r.get("key") == "ucet" and r.get("value")]
	return accounts


def _slip_accounts(slip) -> list[str]:
	"""Every account a payment of the slip can go to: of its employee and on the slip."""
	return employee_accounts(slip.employee) + _own_accounts(slip)


def known_employee_accounts() -> list[str]:
	"""Every account ERPNext knows as an employee's: master data of all employees, also
	former ones, and the accounts on all submitted slips."""
	accounts = []
	for e in frappe.get_all("Employee", filters={}, fields=["bank_ac_no", "iban"]):
		accounts += [a for a in (e.bank_ac_no, e.iban) if a]
	for row in _bank_account_texts({"party_type": "Employee"}):
		accounts += row["accounts"]
	fields = ["bank_account_no"]
	if frappe.db.has_column("Salary Slip", "jmhz_slip_data"):
		fields.append("jmhz_slip_data")
	for slip in frappe.get_all("Salary Slip", filters={"docstatus": 1}, fields=fields):
		accounts += _own_accounts(slip)
	return accounts


def is_employee_account(counter) -> bool:
	return bool(counter) and has_account(known_employee_accounts(), counter)


def remove_account_rules() -> int:
	"""Delete Bank Matching Rules that book by an account of an employee. Salaries are
	matched from the slips now, and an account of a person does not belong into a rule.
	Runs after every migrate; a rule for an account ERPNext does not know is left alone.
	Returns how many rules were deleted."""
	removed = 0
	try:
		if not frappe.db.exists("DocType", "Bank Matching Rule"):
			return 0
		accounts = known_employee_accounts()
		rules = frappe.get_all(
			"Bank Matching Rule", filters={"rule_type": "auto_je"}, fields=["name", "pattern"]
		)
		for rule in rules:
			if has_account(accounts, normalize_account(rule.pattern)):
				# permanently: Deleted Document would keep the account readable
				frappe.delete_doc(
					"Bank Matching Rule",
					rule.name,
					ignore_permissions=True,
					force=True,
					delete_permanently=True,
				)
				removed += 1
		if removed:
			frappe.logger("erpnext_banking").info(f"removed {removed} salary account rule(s)")
	except Exception:
		# a migrate must never fail because of this clean-up
		frappe.log_error("Removing salary account rules failed", "erpnext_banking")
	return removed


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


def _expense_account(settings) -> str:
	return _setting(settings, "salary_account", DEFAULT_SALARY_ACCOUNT)


def _payable_line(slip_name: str) -> dict | None:
	"""Account and party to debit when the slip was booked in its period, else None."""
	from erpnext_banking.jmhz import accounting

	if not accounting.accrued(slip_name):
		return None
	account = accounting.payable_account()
	employee = frappe.db.get_value("Salary Slip", slip_name, "employee")
	return {"account": account, **accounting.party_fields(account, employee)}


def _book(bt, slip: dict, settings) -> str:
	"""Journal Entry for the payment, linked to the bank transaction, and the slip marked paid.
	Returns the entry."""
	amount = float(bt.withdrawal)
	payable = _payable_line(slip["name"])
	payload = journal_entry_payload(
		company=settings.company,
		posting_date=bt.date,
		amount=amount,
		debit_account=payable["account"] if payable else _expense_account(settings),
		credit_account=_setting(settings, "je_contra_account", DEFAULT_JE_CONTRA_ACCOUNT),
		# a cost center belongs to the expense, not to the payable
		cost_center=None if payable else _setting(settings, "default_cost_center", DEFAULT_COST_CENTER),
		# the slip is enough to find the person; no name or account in the ledger remark
		remark=f"mzda {slip['end_date']:%m/%Y} | {slip['name']}",
		reference_no=(bt.reference_number or bt.name),
		reference_date=bt.date,
	)
	if payable:
		payload["accounts"][0].update(payable)
	je = frappe.get_doc(payload)
	je.insert(ignore_permissions=True)
	je.submit()
	bt.append(
		"payment_entries",
		{"payment_document": "Journal Entry", "payment_entry": je.name, "allocated_amount": amount},
	)
	bt.save(ignore_permissions=True)
	_mark_paid(slip["name"], bt)
	return je.name


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
	_move_to_payable(bt, data, settings)
	_mark_paid(data["name"], bt)  # booked earlier, by a rule or by hand
	return "linked"


RECLASS_PREFIX = "přeúčtování výplaty na závazek"


def _move_to_payable(bt, slip: dict, settings) -> str | None:
	"""The payment was booked to the wage expense and the slip now has an accrual: move it to
	the payable (331 / 521), once per bank transaction. Returns the entry when one was posted."""
	payable = _payable_line(slip["name"])
	if not payable:
		return None
	expense = _expense_account(settings)
	entries = [e.payment_entry for e in bt.payment_entries if e.payment_document == "Journal Entry"]
	amount = float(bt.withdrawal)
	to_expense = entries and frappe.get_all(
		"Journal Entry Account",
		filters={"parent": ["in", entries], "account": expense, "debit_in_account_currency": amount},
		limit=1,
	)
	if not to_expense:
		return None  # booked to the payable already, or some other way a person chose
	moved = frappe.get_all(
		"Journal Entry",
		filters={"docstatus": 1, "cheque_no": bt.name, "user_remark": ["like", f"{RECLASS_PREFIX}%"]},
		limit=1,
	)
	if moved:
		return None
	cost_center = _setting(settings, "default_cost_center", DEFAULT_COST_CENTER)
	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": settings.company,
			"posting_date": bt.date,
			"cheque_no": bt.name,
			"cheque_date": bt.date,
			"user_remark": f"{RECLASS_PREFIX} {slip['end_date']:%m/%Y} | {slip['name']}",
			"accounts": [
				{**payable, "debit_in_account_currency": amount, "credit_in_account_currency": 0},
				{
					"account": expense,
					"debit_in_account_currency": 0,
					"credit_in_account_currency": amount,
					"cost_center": cost_center,
				},
			],
		}
	)
	je.insert(ignore_permissions=True)
	je.submit()
	return je.name


def settle_slip(slip_name: str, settings) -> tuple[str, list[str]]:
	"""Bring the payment of one submitted slip in line with its accrual. Used when slips that
	are already submitted get booked afterwards (accounting.backfill).

	Returns (outcome, Journal Entries posted): "booked" (an open payment was booked now),
	"moved" (a payment booked to the expense was moved to the payable), "linked" (the payment
	is fine as it is) or "none" (no payment found, the payable stays open). Does not commit.
	"""
	slip = frappe.db.get_value(
		"Salary Slip", slip_name, [*_SLIP_FIELDS, "docstatus", "company", SLIP_TRANSACTION], as_dict=True
	)
	if not slip or slip.docstatus != 1 or slip.company != settings.company or float(slip.net_pay or 0) <= 0:
		return "none", []
	data = _slip_dict(slip)
	if slip.get(SLIP_TRANSACTION):
		bt = frappe.get_doc("Bank Transaction", slip.get(SLIP_TRANSACTION))
	else:
		payment = pick_payment(data, _payments_for(data, settings))
		if not payment:
			return "none", []
		bt = frappe.get_doc("Bank Transaction", payment["name"])
		if bt.status == "Unreconciled":
			return "booked", [_book(bt, data, settings)]
		_mark_paid(data["name"], bt)
	entry = _move_to_payable(bt, data, settings)
	return ("moved", [entry]) if entry else ("linked", [])


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
