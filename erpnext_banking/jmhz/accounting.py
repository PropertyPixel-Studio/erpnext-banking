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
