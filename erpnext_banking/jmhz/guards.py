"""Safety hooks of the JMHZ module (SPEC §7).

- a slip imported from JMHZ can be submitted and cancelled only by the importer, and submitted
  only while it matches the report;
- a manually created slip and Payroll Entry are blocked unless JMHZ Settings allow them;
- users without an HR role do not see draft and cancelled slips;
- a deleted JMHZ document leaves no IK MPSV and no amounts in Deleted Document.
"""

import frappe
from frappe import _
from frappe.model import no_value_fields, table_fields
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

	if not doc.flags.jmhz_internal:
		# the importer checks the whole import first (row status, printed slip check)
		frappe.throw(
			_("Pásku {0} z importu JMHZ jde odeslat jen tlačítkem „Odeslat pásky“ na importu.").format(
				doc.name
			)
		)
	if abs(flt(doc.net_pay) - flt(doc.jmhz_k_vyplate)) > TOLERANCE:
		frappe.throw(_("Částka k výplatě na pásce {0} nesedí s JMHZ. Pásku nejde odeslat.").format(doc.name))
	expected_gross = flt(doc.jmhz_zuctovany_prijem) + flt(doc.jmhz_danovy_bonus)
	if abs(flt(doc.gross_pay) - expected_gross) > TOLERANCE:
		frappe.throw(_("Hrubá mzda na pásce {0} nesedí s JMHZ. Pásku nejde odeslat.").format(doc.name))
	status = frappe.db.get_value("JMHZ Import", doc.jmhz_import, "status")
	if status != "Pásky vytvořeny":
		frappe.throw(_("Pásku {0} jde odeslat jen z importu ve stavu „Pásky vytvořeny“.").format(doc.name))


def salary_slip_before_cancel(doc, method=None):
	"""A JMHZ slip is cancelled only by the importer, which also updates the import rows."""
	if doc.get("jmhz_import") and not doc.flags.jmhz_internal:
		frappe.throw(_("Pásku {0} z importu JMHZ jde zrušit jen z importu JMHZ.").format(doc.name))


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


# Fields that may stay in Deleted Document. Everything else (IK MPSV, amounts, issues) is dropped.
DELETED_DOCUMENT_KEEP = {
	"JMHZ Import": ("company", "rok", "mesic", "typ_podani", "status"),
	"JMHZ Person": ("employee",),
	"Salary Slip": ("employee", "company", "start_date", "end_date", "jmhz_import"),
	"Additional Salary": ("employee", "company", "salary_component", "payroll_date", "jmhz_import"),
}


def scrub_deleted(doc, method=None):
	"""after_delete: Frappe copies the document as JSON into Deleted Document right after this
	hook, and System Manager can read Deleted Document. The row is already gone from the
	database, so only the in-memory copy is emptied."""
	if doc.doctype in ("Salary Slip", "Additional Salary") and not doc.get("jmhz_import"):
		return
	keep = DELETED_DOCUMENT_KEEP[doc.doctype]
	for df in doc.meta.fields:
		if df.fieldname in keep:
			continue
		if df.fieldtype in table_fields:
			doc.set(df.fieldname, [])
		elif df.fieldtype not in no_value_fields:
			doc.set(df.fieldname, None)
