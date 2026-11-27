"""Idempotent setup of the JMHZ module (salary components, structure, settings).

`ensure_all()` runs from `after_migrate`, so missing or broken setup is fixed on
every migrate. It never edits a submitted salary structure and never overwrites
an existing holiday list assignment; such differences are only reported.

Each step runs in its own savepoint. A failing step is rolled back and logged,
the other steps and the migrate itself continue.
"""

from __future__ import annotations

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext_banking.jmhz import components as C

SETTINGS = "JMHZ Settings"
STRUCTURE_NAME = "Import z JMHZ"
HOLIDAY_LIST_NAME = "JMHZ - technický"
HOLIDAY_LIST_FROM = "2026-01-01"
HOLIDAY_LIST_TO = "2099-12-31"

# Values enforced on every JMHZ salary component (SPEC §5.1).
COMPONENT_FLAGS = {
	"depends_on_payment_days": 0,  # otherwise HRMS prorates by payment days
	"is_tax_applicable": 0,
	"variable_based_on_taxable_salary": 0,
	"round_to_the_nearest_integer": 0,
	"remove_if_zero_valued": 1,
	"amount_based_on_formula": 0,
	"formula": "",
	"condition": "",
	"amount": 0,
	"statistical_component": 0,
	"is_flexible_benefit": 0,
	"accrual_component": 0,
	"arrear_component": 0,
	"do_not_include_in_total": 0,
	"disabled": 0,
}

PAYROLL_SETTINGS = {
	"disable_rounded_total": 1,
	# Built-in e-mail sends every slip on submit, cannot be limited to selected people.
	# Stays off for good, the module decides who gets an e-mail.
	"email_salary_slip_to_employee": 0,
}

_COMMON = {"read_only": 1, "no_copy": 1, "allow_on_submit": 0}
CUSTOM_FIELDS = {
	"Salary Slip": [
		{
			"fieldname": "jmhz_section",
			"fieldtype": "Section Break",
			"label": "Údaje z JMHZ",
			"collapsible": 1,
			"insert_after": "base_total_in_words",
		},
		{"fieldname": "jmhz_import", "fieldtype": "Link", "label": "JMHZ Import", "options": "JMHZ Import"},
		{"fieldname": "jmhz_zuctovany_prijem", "fieldtype": "Currency", "label": "Zúčtovaný příjem (10286)"},
		{"fieldname": "jmhz_cisty_prijem", "fieldtype": "Currency", "label": "Čistý příjem (10344)"},
		{"fieldname": "jmhz_danovy_bonus", "fieldtype": "Currency", "label": "Daňový bonus (10306)"},
		{"fieldname": "jmhz_rucni_srazka", "fieldtype": "Currency", "label": "Ruční srážka"},
		{"fieldname": "jmhz_k_vyplate", "fieldtype": "Currency", "label": "K výplatě podle JMHZ"},
		{"fieldname": "jmhz_id_ppv", "fieldtype": "Data", "label": "ID PPV (10228)"},
		{"fieldname": "jmhz_column_break", "fieldtype": "Column Break"},
		{"fieldname": "jmhz_odpracovane_hodiny", "fieldtype": "Float", "label": "Odpracované hodiny (10268)"},
		{"fieldname": "jmhz_odpracovane_dny", "fieldtype": "Float", "label": "Odpracované dny (10267)"},
		{"fieldname": "jmhz_hodiny_dovolena", "fieldtype": "Float", "label": "Hodiny dovolené (10279)"},
		{
			"fieldname": "jmhz_neodpracovane_hodiny",
			"fieldtype": "Float",
			"label": "Neodpracované hodiny (10275)",
		},
		{"fieldname": "jmhz_sp_zamestnavatel", "fieldtype": "Currency", "label": "SP zaměstnavatel (10481)"},
		{"fieldname": "jmhz_zp_zamestnavatel", "fieldtype": "Currency", "label": "ZP zaměstnavatel (10482)"},
		{
			# everything else the print format shows, see slip_data.py
			"fieldname": "jmhz_slip_data",
			"fieldtype": "Long Text",
			"label": "Údaje úplné pásky",
			"hidden": 1,
			"print_hide": 1,
		},
	],
	"Additional Salary": [
		{
			"fieldname": "jmhz_import",
			"fieldtype": "Link",
			"label": "JMHZ Import",
			"options": "JMHZ Import",
			"insert_after": "ref_docname",
		},
	],
}


def _chain_insert_after(fields: list[dict]) -> list[dict]:
	"""Each field without an explicit anchor goes right after the previous one."""
	out, previous = [], None
	for field in fields:
		field = {**_COMMON, **field}
		if previous and "insert_after" not in field:
			field["insert_after"] = previous
		out.append(field)
		previous = field["fieldname"]
	return out


def custom_fields() -> dict[str, list[dict]]:
	return {doctype: _chain_insert_after(fields) for doctype, fields in CUSTOM_FIELDS.items()}


# --- entry points --------------------------------------------------------------


def after_migrate():
	problems = ensure_all()
	for problem in problems:
		print(f"JMHZ setup: {problem}")


def ensure_all(*, strict: bool = False) -> list[str]:
	"""Run all setup steps. Returns human readable problems (empty = all fine).

	With `strict=True` the first failing step raises (used in tests).
	"""
	problems: list[str] = []
	steps = (
		ensure_custom_fields,
		ensure_settings,
		ensure_components,
		ensure_payroll_settings,
		ensure_structure,
		ensure_holiday_list,
	)
	for step in steps:
		savepoint = f"jmhz_setup_{step.__name__}"
		frappe.db.savepoint(savepoint)
		try:
			problems.extend(step() or [])
		except Exception:
			frappe.db.rollback(save_point=savepoint)
			if strict:
				raise
			frappe.log_error(title=f"JMHZ setup: {step.__name__}")
			problems.append(f"krok {step.__name__} selhal, viz Error Log")
	return problems


# --- steps ---------------------------------------------------------------------


def ensure_custom_fields() -> list[str]:
	create_custom_fields(custom_fields(), ignore_validate=False, update=True)
	return []


def get_company() -> str | None:
	company = frappe.db.get_single_value(SETTINGS, "company")
	if company and frappe.db.exists("Company", company):
		return company
	return None


def _default_company() -> str | None:
	company = frappe.db.get_single_value("Global Defaults", "default_company")
	if company and frappe.db.exists("Company", company):
		return company
	companies = frappe.get_all("Company", pluck="name", limit=2)
	return companies[0] if len(companies) == 1 else None


def ensure_settings() -> list[str]:
	settings = frappe.get_single(SETTINGS)
	changed = False
	if not settings.company or not frappe.db.exists("Company", settings.company):
		company = _default_company()
		if company:
			settings.company = company
			changed = True
	if not settings.salary_structure:
		settings.salary_structure = STRUCTURE_NAME
		changed = True
	if not settings.email_mode:
		settings.email_mode = "Vypnuto"
		changed = True
	if changed:
		# the structure may not exist yet on the first run
		settings.flags.ignore_links = True
		settings.save(ignore_permissions=True)
	if not settings.company:
		return ["JMHZ Settings nemá firmu, nastavte ji ručně (výchozí firma není jednoznačná)"]
	return []


def ensure_components() -> list[str]:
	problems = []
	for comp in C.COMPONENTS:
		values = {"salary_component_abbr": comp.abbr, "type": comp.component_type, **COMPONENT_FLAGS}
		if frappe.db.exists("Salary Component", comp.name):
			doc = frappe.get_doc("Salary Component", comp.name)
			if all(_same(doc.get(k), v) for k, v in values.items()):
				continue
			doc.update(values)
		else:
			doc = frappe.get_doc({"doctype": "Salary Component", "salary_component": comp.name, **values})
		with _muted():
			doc.save(ignore_permissions=True)
		if doc.salary_component_abbr != comp.abbr:
			# HRMS appends a number when the abbreviation is taken by another component
			problems.append(
				f"zkratka {comp.abbr} je obsazená jinou složkou, složka {comp.name} dostala {doc.salary_component_abbr}"
			)
	return problems


def ensure_payroll_settings() -> list[str]:
	for field, value in PAYROLL_SETTINGS.items():
		if frappe.db.get_single_value("Payroll Settings", field) != value:
			frappe.db.set_single_value("Payroll Settings", field, value)
	if not frappe.db.get_single_value("Payroll Settings", "payroll_based_on"):
		return ["Payroll Settings: pole payroll_based_on je prázdné"]
	return []


def ensure_structure() -> list[str]:
	company = get_company()
	if not company:
		return ["struktura mzdy se nevytvořila, chybí firma v JMHZ Settings"]
	name = frappe.db.get_single_value(SETTINGS, "salary_structure") or STRUCTURE_NAME

	if not frappe.db.exists("Salary Structure", name):
		doc = frappe.new_doc("Salary Structure")
		doc.set("__newname", name)
		doc.update(
			{
				"company": company,
				"currency": frappe.get_cached_value("Company", company, "default_currency"),
				"payroll_frequency": "Monthly",
				"is_active": "Yes",
				"salary_slip_based_on_timesheet": 0,
			}
		)
		for comp in C.COMPONENTS:
			doc.append(
				"earnings" if comp.kind == "earning" else "deductions",
				{"salary_component": comp.name, "abbr": comp.abbr, "amount": 0, "amount_based_on_formula": 0},
			)
		with _muted():
			doc.insert(ignore_permissions=True)
			doc.submit()
		return []

	doc = frappe.get_doc("Salary Structure", name)
	problems = []
	if doc.docstatus == 0:
		with _muted():
			doc.submit()
	elif doc.docstatus == 2:
		return [f"struktura mzdy {name} je zrušená, založte novou verzi patchem"]
	if doc.company != company:
		problems.append(f"struktura mzdy {name} patří jiné firmě než JMHZ Settings")
	if doc.is_active != "Yes":
		problems.append(f"struktura mzdy {name} není aktivní")
	present = {row.salary_component for row in doc.earnings} | {
		row.salary_component for row in doc.deductions
	}
	missing = [comp.name for comp in C.COMPONENTS if comp.name not in present]
	if missing:
		problems.append(
			f"struktura mzdy {name} nemá složky {', '.join(missing)}; novou verzi je potřeba zavést patchem"
		)
	return problems


def ensure_holiday_list() -> list[str]:
	"""HRMS needs a holiday list for every slip, even if attendance is not used (SPEC §5.4).

	A submitted company assignment stays in effect indefinitely and also covers dates
	before it (hrms.utils.holiday_list.resolve_holiday_list_assignment), so one
	assignment is enough. An existing assignment is never changed.
	"""
	company = get_company()
	if not company:
		return []
	if frappe.db.exists(
		"Holiday List Assignment", {"assigned_to": company, "applicable_for": "Company", "docstatus": 1}
	):
		return []

	if not frappe.db.exists("Holiday List", HOLIDAY_LIST_NAME):
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": HOLIDAY_LIST_NAME,
				"from_date": HOLIDAY_LIST_FROM,
				"to_date": HOLIDAY_LIST_TO,
			}
		).insert(ignore_permissions=True)

	assignment = frappe.get_doc(
		{
			"doctype": "Holiday List Assignment",
			"applicable_for": "Company",
			"assigned_to": company,
			"holiday_list": HOLIDAY_LIST_NAME,
			"from_date": HOLIDAY_LIST_FROM,
		}
	)
	assignment.insert(ignore_permissions=True)
	assignment.submit()
	return []


# --- helpers -------------------------------------------------------------------


def _same(current, wanted) -> bool:
	if isinstance(wanted, int):
		return int(current or 0) == wanted
	return (current or "") == wanted


class _muted:
	"""Silence msgprint warnings of HRMS (e.g. missing accounts) during setup."""

	def __enter__(self):
		self._previous = frappe.flags.mute_messages
		frappe.flags.mute_messages = True

	def __exit__(self, *exc):
		frappe.flags.mute_messages = self._previous
		return False
