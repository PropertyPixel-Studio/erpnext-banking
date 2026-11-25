"""Safety hooks of the JMHZ module (SPEC §7).

- a slip imported from JMHZ can be submitted only while it matches the report;
- a manually created slip and Payroll Entry are blocked unless JMHZ Settings allow them;
- users without an HR role do not see draft and cancelled slips.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt

SETTINGS = "JMHZ Settings"
HR_ROLES = {"HR Manager", "HR User"}
TOLERANCE = 0.005


def _setting(field: str) -> int:
	return cint(frappe.db.get_single_value(SETTINGS, field))


def salary_slip_before_submit(doc, method=None):
	if not doc.get("jmhz_import"):
		if not _setting("allow_slips_outside_import"):
			frappe.throw(
				_(
					"Výplatní pásky se odesílají jen z importu JMHZ. Ručně založenou pásku jde odeslat, "
					"jen když to povolí JMHZ Settings."
				)
			)
		return

	if abs(flt(doc.net_pay) - flt(doc.jmhz_k_vyplate)) > TOLERANCE:
		frappe.throw(_("Částka k výplatě na pásce {0} nesedí s JMHZ. Pásku nejde odeslat.").format(doc.name))
	expected_gross = flt(doc.jmhz_zuctovany_prijem) + flt(doc.jmhz_danovy_bonus)
	if abs(flt(doc.gross_pay) - expected_gross) > TOLERANCE:
		frappe.throw(_("Hrubá mzda na pásce {0} nesedí s JMHZ. Pásku nejde odeslat.").format(doc.name))
	status = frappe.db.get_value("JMHZ Import", doc.jmhz_import, "status")
	if status != "Pásky vytvořeny":
		frappe.throw(_("Pásku {0} jde odeslat jen z importu ve stavu „Pásky vytvořeny“.").format(doc.name))


def block_payroll_entry(doc, method=None):
	if not _setting("allow_payroll_entry"):
		frappe.throw(
			_(
				"Payroll Entry je vypnutá: mzdy se nezaúčtovávají v ERPNextu, pásky vznikají z importu JMHZ. "
				"Povolit ji jde v JMHZ Settings."
			)
		)


def _is_hr(user: str) -> bool:
	if user == "Administrator":
		return True
	return bool(HR_ROLES & set(frappe.get_roles(user)))


def salary_slip_query(user=None, doctype=None):
	user = user or frappe.session.user
	if _is_hr(user):
		return ""
	return "`tabSalary Slip`.`docstatus` = 1"


def salary_slip_has_permission(doc, ptype=None, user=None, debug=False):
	# Frappe treats any falsy return value as "denied", so return True when there is no objection.
	user = user or frappe.session.user
	if _is_hr(user):
		return True
	return cint(doc.docstatus) == 1
