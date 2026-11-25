"""Controlled sending of salary slips by e-mail (SPEC §6.7).

The built-in HRMS e-mail on submit stays off for good (Payroll Settings,
see setup.py). This module only decides who gets the slip. The e-mail itself is
the standard `SalarySlip.email_salary_slip()`: PDF of the ERPNext slip, recipient
`Employee.prefered_email`, template and encryption from Payroll Settings.

Cancelling or replacing a slip never sends an e-mail.
"""

import frappe
from frappe import _

SETTINGS = "JMHZ Settings"

MODE_OFF = "Vypnuto"
MODE_TEST = "Jen testovací zaměstnanci"
MODE_ALL = "Všem"

QUEUED = "Zařazeno k odeslání"
SKIPPED = "Přeskočeno (režim)"
NO_EMAIL = "Bez e-mailu"
NOT_SENT = "Neodesláno"


def mode() -> str:
	return frappe.db.get_single_value(SETTINGS, "email_mode") or MODE_OFF


def test_employees() -> set[str]:
	return set(
		frappe.get_all(
			"JMHZ Email Test Employee",
			filters={"parent": SETTINGS, "parenttype": SETTINGS},
			pluck="employee",
		)
	)


def allowed(employee: str, current_mode: str | None = None, testers: set[str] | None = None) -> bool:
	current_mode = current_mode or mode()
	if current_mode == MODE_ALL:
		return True
	if current_mode == MODE_TEST:
		return employee in (testers if testers is not None else test_employees())
	return False


def _send(slip_name: str) -> str:
	slip = frappe.get_doc("Salary Slip", slip_name)
	if not frappe.db.get_value("Employee", slip.employee, "prefered_email"):
		return NO_EMAIL
	slip.email_salary_slip()
	return QUEUED


def queue_for_import(doc) -> dict:
	"""After the slips of an import were submitted: send them according to the mode.

	A failing e-mail never undoes the submitted slips; the row stays "Neodesláno".
	"""
	current_mode = mode()
	if current_mode == MODE_OFF:
		return {"mode": current_mode, "queued": 0}
	testers = test_employees()
	queued = 0
	for row in doc.rows:
		if not row.salary_slip or row.status != "Páska odeslána":
			continue
		if not allowed(row.employee, current_mode, testers):
			row.email_status = SKIPPED
			continue
		try:
			row.email_status = _send(row.salary_slip)
		except Exception:
			frappe.log_error(title="JMHZ: odeslání pásky e-mailem")
			row.email_status = NOT_SENT
		queued += row.email_status == QUEUED
	doc.flags.jmhz_internal = True
	doc.save(ignore_permissions=True)
	doc.flags.jmhz_internal = False
	return {"mode": current_mode, "queued": queued}


@frappe.whitelist(methods=["POST"])
def send_slip(name: str) -> dict:
	"""Button "Poslat pásku e-mailem" on a submitted slip. HR Manager only, respects the mode."""
	frappe.only_for("HR Manager")
	slip = frappe.get_doc("Salary Slip", name)
	slip.check_permission("read")
	if slip.docstatus != 1:
		frappe.throw(_("E-mailem jde poslat jen odeslanou pásku."))
	current_mode = mode()
	if current_mode == MODE_OFF:
		frappe.throw(_("Odesílání pásek e-mailem je v JMHZ Settings vypnuté."))
	if not allowed(slip.employee, current_mode):
		frappe.throw(_("Zaměstnanec není mezi testovacími zaměstnanci v JMHZ Settings."))
	status = _send(slip.name)
	if status == NO_EMAIL:
		frappe.throw(_("Zaměstnanec nemá vyplněný e-mail pro zasílání (Preferred Email)."))
	return {"status": status}
