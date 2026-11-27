"""Booking of salary slips (accrual) for the JMHZ module. See accrual.py for the entries.

Off by default: `JMHZ Settings.post_accrual` turns it on once the accounts are set. A slip
is booked when its import is submitted, in the same transaction, with the last day of
its period as the posting date. Cancelling the slip cancels its entry.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate

from erpnext_banking.jmhz import accrual

SETTINGS = "JMHZ Settings"
SLIP_ENTRY = "jmhz_accrual_entry"

# role -> (field of JMHZ Settings, account number in the Czech chart of accounts)
ACCOUNT_FIELDS = {
	accrual.WAGE: ("accrual_wage_account", "521"),
	accrual.PAYABLE: ("accrual_payable_account", "331"),
	accrual.INSURANCE: ("accrual_insurance_account", "336"),
	accrual.TAX: ("accrual_tax_account", "342"),
	accrual.EMPLOYER: ("accrual_employer_account", "524"),
	accrual.DEDUCTIONS: ("accrual_deductions_account", "379"),
}


def enabled() -> bool:
	return bool(cint(frappe.db.get_single_value(SETTINGS, "post_accrual")))


def default_account(company: str | None, number: str) -> str | None:
	"""The single ledger account of the company with that number, None when unclear."""
	if not company:
		return None
	base = {"company": company, "is_group": 0, "disabled": 0}
	for extra in ({"account_number": number}, {"name": ["like", f"{number} - %"]}):
		names = frappe.get_all("Account", filters={**base, **extra}, pluck="name", limit=2)
		if len(names) == 1:
			return names[0]
	return None


def accounts(settings=None) -> dict[str, str]:
	"""role -> account. Raises when a needed account is not set."""
	settings = settings or frappe.get_cached_doc(SETTINGS)
	out = {role: settings.get(field) for role, (field, _number) in ACCOUNT_FIELDS.items()}
	missing = [number for role, (_field, number) in ACCOUNT_FIELDS.items() if not out[role]]
	if missing:
		frappe.throw(_("V JMHZ Settings chybí účty pro účtování mezd: {0}.").format(", ".join(missing)))
	return out


def payable_account() -> str:
	return accounts()[accrual.PAYABLE]


def party_fields(account: str, employee: str) -> dict:
	"""A receivable or payable account needs its party: the employee."""
	if frappe.get_cached_value("Account", account, "account_type") in ("Receivable", "Payable"):
		return {"party_type": "Employee", "party": employee}
	return {}


def _cost_center(settings, company: str) -> str | None:
	return settings.get("accrual_cost_center") or frappe.get_cached_value("Company", company, "cost_center")


def accrued(slip_name: str) -> bool:
	"""The slip has a live accrual entry, so its net pay sits on the payable."""
	entry = frappe.db.get_value("Salary Slip", slip_name, SLIP_ENTRY)
	return bool(entry) and frappe.db.get_value("Journal Entry", entry, "docstatus") == 1


def post_accrual(slip) -> str | None:
	"""Book a submitted slip. Returns the Journal Entry, None when booking is off."""
	if not enabled():
		return None
	settings = frappe.get_cached_doc(SETTINGS)
	acc = accounts(settings)
	slip_lines = [(row.salary_component, flt(row.amount)) for row in [*slip.earnings, *slip.deductions]]
	employer = flt(slip.get("jmhz_sp_zamestnavatel")) + flt(slip.get("jmhz_zp_zamestnavatel"))
	try:
		lines = accrual.accrual_lines(slip_lines, employer)
	except ValueError:
		frappe.throw(_("Pásku {0} nejde zaúčtovat: obsahuje složku mimo JMHZ.").format(slip.name))
	if abs(accrual.net_payable(lines) - flt(slip.net_pay)) > 0.005:
		frappe.throw(_("Pásku {0} nejde zaúčtovat: předpis nevychází na částku k výplatě.").format(slip.name))
	if not lines:
		return None

	cost_center = _cost_center(settings, slip.company)
	rows = []
	for li in lines:
		account = acc[li["role"]]
		row = {
			"account": account,
			"debit_in_account_currency": li["debit"],
			"credit_in_account_currency": li["credit"],
			**party_fields(account, slip.employee),
		}
		if li["role"] in (accrual.WAGE, accrual.EMPLOYER) and cost_center:
			row["cost_center"] = cost_center
		rows.append(row)
	end = getdate(slip.end_date)
	je = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"voucher_type": "Journal Entry",
			"company": slip.company,
			"posting_date": end,
			# the slip is enough to find the person; no name in the ledger remark
			"user_remark": f"mzdy {end:%m/%Y} | {slip.name}",
			"accounts": rows,
		}
	)
	je.insert(ignore_permissions=True)
	je.submit()
	frappe.db.set_value("Salary Slip", slip.name, SLIP_ENTRY, je.name, update_modified=False)
	return je.name


def cancel_accrual(slip_name: str) -> None:
	"""Cancel the accrual of a slip that has just been cancelled."""
	entry = frappe.db.get_value("Salary Slip", slip_name, SLIP_ENTRY)
	if entry and frappe.db.get_value("Journal Entry", entry, "docstatus") == 1:
		je = frappe.get_doc("Journal Entry", entry)
		je.flags.ignore_permissions = True
		je.cancel()


# --- slips submitted before booking was turned on ---------------------------------------


def _bank_settings(company: str):
	"""Settings of the enabled bank provider of the company, None when there is none."""
	from erpnext_banking.providers import iter_enabled_providers

	for provider in iter_enabled_providers():
		settings = frappe.get_single(provider.settings_doctype)
		if settings.company == company:
			return settings
	return None


def _plain_error(e: Exception) -> str:
	from frappe.utils import strip_html

	return strip_html(str(e))[:300] or type(e).__name__


def backfill(from_date=None, dry_run: bool = True, bank_settings=None) -> dict:
	"""Book the slips that were submitted while booking was off, and bring their payments in
	line: an open payment is booked to the payable, a payment booked to the expense earlier is
	moved to the payable once.

	Every slip is handled on its own, in a savepoint: a slip that cannot be booked (a closed
	period, for example) is reported and the others go on. `dry_run` does all of it and
	rolls every slip back, so the summary shows exactly what a real run would post and where
	it would fail. Running it again does nothing for slips that are already done.
	"""
	from erpnext_banking import salary

	if not enabled():
		frappe.throw(_("Nejdřív v JMHZ Settings zapněte účtování předpisu mezd."))
	settings = frappe.get_cached_doc(SETTINGS)
	accounts(settings)
	bank = bank_settings or _bank_settings(settings.company)
	filters = {"company": settings.company, "docstatus": 1, "jmhz_import": ["is", "set"]}
	if from_date:
		filters["end_date"] = [">=", getdate(from_date)]
	slips = frappe.get_all(
		"Salary Slip", filters=filters, fields=["name", "end_date"], order_by="end_date asc, name asc"
	)

	periods: dict[str, dict] = {}
	totals: dict[str, list[float]] = {}
	errors = []
	# One savepoint around the whole run and one per slip. A preview keeps the effects of
	# the earlier slips while it goes on (two slips must not claim one payment) and drops
	# everything at the end.
	frappe.db.savepoint("jmhz_backfill_run")
	for row in slips:
		period = f"{getdate(row.end_date):%m/%Y}"
		p = periods.setdefault(
			period,
			{"period": period, "slips": 0, "accrual_new": 0, "accrual_existing": 0, "payment_booked": 0}
			| {"payment_moved": 0, "payment_linked": 0, "payment_none": 0, "errors": 0},
		)
		p["slips"] += 1
		frappe.db.savepoint("jmhz_backfill_slip")
		try:
			entries = []
			new = not accrued(row.name)
			if new:
				entry = post_accrual(frappe.get_doc("Salary Slip", row.name))
				entries += [entry] if entry else []
			outcome, posted = salary.settle_slip(row.name, bank) if bank else ("none", [])
			entries += posted
			lines = (
				frappe.get_all(
					"Journal Entry Account",
					filters={"parent": ["in", entries]},
					fields=[
						"account",
						"debit_in_account_currency as debit",
						"credit_in_account_currency as credit",
					],
				)
				if entries
				else []
			)
		except Exception as e:
			frappe.db.rollback(save_point="jmhz_backfill_slip")
			frappe.clear_messages()  # the reason goes into the summary, not into pop-ups
			p["errors"] += 1
			errors.append({"slip": row.name, "period": period, "message": _plain_error(e)})
			continue
		p["accrual_new" if new else "accrual_existing"] += 1
		p[f"payment_{outcome}"] += 1
		for li in lines:
			total = totals.setdefault(li.account, [0.0, 0.0])
			total[0] += flt(li.debit)
			total[1] += flt(li.credit)
	if dry_run:
		frappe.db.rollback(save_point="jmhz_backfill_run")
	return {
		"dry_run": bool(dry_run),
		"bank": bool(bank),
		"periods": list(periods.values()),
		"accounts": [{"account": a, "debit": t[0], "credit": t[1]} for a, t in sorted(totals.items())],
		"errors": errors,
	}
