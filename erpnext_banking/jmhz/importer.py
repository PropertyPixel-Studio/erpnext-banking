"""Importer: JMHZ report -> JMHZ Import -> Additional Salary + Salary Slip.

HRMS computes nothing here. Amounts come from the report, the slip only has to
add them up to the same numbers (checked row by row, I09).

Messages written by this module never contain amounts, IK MPSV or ID PPV.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json

import frappe
from frappe import _
from frappe.utils import flt, getdate, now_datetime

from erpnext_banking.jmhz import components as C
from erpnext_banking.jmhz.model import JmhzParseError, ParsedFile, Person
from erpnext_banking.jmhz.parser import parse

IMPORT = "JMHZ Import"
PERSON = "JMHZ Person"
SETTINGS = "JMHZ Settings"

# import status
NAHLED = "Náhled"
VYTVORENO = "Pásky vytvořeny"
DOKONCENO = "Dokončeno"
ZRUSENO = "Zrušeno"
NAHRAZENO = "Nahrazeno"
LIVE_STATUSES = (VYTVORENO, DOKONCENO)

# row status
R_PRIPRAVENO = "Připraveno"
R_NENAPAROVANO = "Nenapárováno"
R_BLOKOVANO = "Blokováno"
R_BEZ_PRIJMU = "Bez příjmu"
R_PRESKOCENO = "Přeskočeno"
R_JIZ_IMPORTOVANO = "Již importováno"
R_VYTVORENA = "Páska vytvořena"
R_ODESLANA = "Páska odeslána"
R_ZRUSENO = "Zrušeno"
ROW_DONE = (R_PRIPRAVENO, R_BEZ_PRIJMU, R_PRESKOCENO, R_JIZ_IMPORTOVANO)

# parser issues that block the row (P02 is resolved by a manual deduction)
PARSER_BLOCKING = {"P01", "P03", "P04", "P05", "P06", "P07", "P08", "P12"}
TOLERANCE = 0.005


# --- helpers -------------------------------------------------------------------


def period(rok: int, mesic: int) -> tuple[dt.date, dt.date]:
	return dt.date(rok, mesic, 1), dt.date(rok, mesic, calendar.monthrange(rok, mesic)[1])


def settings():
	return frappe.get_cached_doc(SETTINGS)


def structure_name() -> str:
	from erpnext_banking.jmhz.setup import STRUCTURE_NAME

	return frappe.db.get_single_value(SETTINGS, "salary_structure") or STRUCTURE_NAME


def _issue_text(code: str, message: str) -> str:
	return f"{code}: {message}"


def _company_currency(company: str) -> str:
	return frappe.get_cached_value("Company", company, "default_currency")


# --- upload --------------------------------------------------------------------


def create_import(content: bytes, file_name: str | None = None, company: str | None = None):
	"""Parse the file and create a JMHZ Import in status Náhled. Nothing is stored as File."""
	try:
		parsed = parse(content, file_name=file_name)
	except JmhzParseError as e:
		frappe.throw(_issue_text(e.code, e.message), title=_("Soubor nejde importovat"))

	if parsed.typ_podani != "R":
		# corrective and cancelling reports are handled in a separate flow (SPEC §6.5)
		frappe.throw(
			_("Opravné a stornovací podání (typPodani {0}) zatím import nepodporuje.").format(
				parsed.typ_podani
			),
			title=_("Soubor nejde importovat"),
		)

	company = company or settings().company
	if not company:
		frappe.throw(_("V JMHZ Settings není nastavená firma."))

	doc = frappe.new_doc(IMPORT)
	fill_import(doc, parsed, company)
	doc.flags.jmhz_internal = True
	doc.insert(ignore_permissions=True)
	doc.flags.jmhz_internal = False
	return doc


def fill_import(doc, parsed: ParsedFile, company: str) -> None:
	start, end = period(parsed.rok, parsed.mesic)
	doc.update(
		{
			"company": company,
			"rok": parsed.rok,
			"mesic": parsed.mesic,
			"period_start": start,
			"period_end": end,
			"typ_podani": parsed.typ_podani,
			"schema_version": parsed.schema,
			"id_podani": parsed.id_podani,
			"vendor": parsed.vendor,
			"file_name": parsed.file_name,
			"file_sha256": parsed.sha256,
			"status": NAHLED,
			"issues": "\n".join(_issue_text(i.code, i.message) for i in parsed.issues),
		}
	)
	paired = dict(frappe.get_all(PERSON, fields=["ik_mpsv", "employee"], as_list=True))
	for person in parsed.persons:
		doc.append("rows", row_from_person(person, paired.get(person.ik_mpsv) if person.ik_mpsv else None))


def row_from_person(person: Person, employee: str | None) -> dict:
	return {
		"pseudonym": person.pseudonym,
		"ik_mpsv": person.ik_mpsv,
		"employee": employee,
		"id_ppv": ", ".join(person.id_ppv),
		"zuctovano": person.zuctovano,
		"cisty_prijem": person.cisty_prijem,
		"danovy_bonus": person.bonus,
		"k_vyplate": person.k_vyplate,
		"sp_zamestnavatel": person.sp_zamestnavatel,
		"zp_zamestnavatel": person.zp_zamestnavatel,
		"odpracovane_hodiny": person.info.get("odpracovane_hodiny"),
		"odpracovane_dny": person.info.get("odpracovane_dny"),
		"hodiny_dovolena": person.info.get("hodiny_dovolena"),
		"neodpracovane_hodiny": person.info.get("neodpracovane_hodiny"),
		"lines_json": json.dumps(
			[
				{"component": li.component, "amount": li.amount, "kind": li.kind, "jmhz_id": li.jmhz_id}
				for li in person.lines
			],
			ensure_ascii=False,
		),
		# parser issues are kept as the first lines of `issues`, see row_parser_issues()
		"issues": "\n".join(_issue_text(i.code, i.message) for i in person.issues),
		"pdf_check": "Bez PDF",
		"email_status": "Neodesláno",
	}


# --- preview: pairing and row status (SPEC §6.2) -----------------------------


def row_lines(row) -> list[dict]:
	return json.loads(row.lines_json or "[]")


def row_parser_codes(row) -> set[str]:
	return {line.split(":", 1)[0] for line in (row.issues or "").splitlines() if line[:1] == "P"}


def row_parser_issue_lines(row) -> list[str]:
	return [line for line in (row.issues or "").splitlines() if line[:1] == "P"]


def row_has_income(row) -> bool:
	# the parser reports P11 exactly when a person has no income and no blocking issue
	return "P11" not in row_parser_codes(row)


def validate_pairing(doc) -> None:
	"""Same Employee twice, or an Employee paired with another IK MPSV, is an error."""
	seen = {}
	for row in doc.rows:
		if not row.employee:
			continue
		if row.employee in seen:
			frappe.throw(
				_("Zaměstnanec {0} je přiřazený osobám {1} a {2}.").format(
					row.employee, seen[row.employee], row.pseudonym
				)
			)
		seen[row.employee] = row.pseudonym
		if not row.ik_mpsv:
			continue
		paired_ik = frappe.db.get_value(PERSON, row.employee, "ik_mpsv")
		if paired_ik and paired_ik != row.ik_mpsv:
			frappe.throw(
				_(
					"Zaměstnanec {0} je už spárovaný s jinou osobou z JMHZ, osobu {1} k němu nejde přiřadit."
				).format(row.employee, row.pseudonym)
			)
		paired_employee = frappe.db.get_value(PERSON, {"ik_mpsv": row.ik_mpsv}, "employee")
		if paired_employee and paired_employee != row.employee:
			frappe.throw(
				_("Osoba {0} je už spárovaná se zaměstnancem {1}.").format(row.pseudonym, paired_employee)
			)


def refresh_rows(doc) -> None:
	"""Recompute the status of every row. Only in status Náhled."""
	start, end = getdate(doc.period_start), getdate(doc.period_end)
	for row in doc.rows:
		status, extra = row_status(doc, row, start, end)
		row.status = status
		row.issues = "\n".join(row_parser_issue_lines(row) + extra)


def row_status(doc, row, start: dt.date, end: dt.date) -> tuple[str, list[str]]:
	codes = row_parser_codes(row)
	if row.skip:
		if not (row.skip_reason or "").strip():
			frappe.throw(_("U přeskočené osoby {0} vyplňte důvod.").format(row.pseudonym))
		return R_PRESKOCENO, []
	if not row_has_income(row):
		return R_BEZ_PRIJMU, []
	if codes & PARSER_BLOCKING:
		return R_BLOKOVANO, []
	if flt(row.rucni_srazka) < 0:
		return R_BLOKOVANO, [_issue_text("I10", "Ruční srážka nesmí být záporná.")]
	if flt(row.rucni_srazka) and not (row.rucni_srazka_poznamka or "").strip():
		return R_BLOKOVANO, [_issue_text("I10", "K ruční srážce vyplňte poznámku.")]
	if "P02" in codes and not flt(row.rucni_srazka):
		return R_BLOKOVANO, []
	if not row.employee:
		return R_NENAPAROVANO, [_issue_text("I01", "Osoba není spárovaná se zaměstnancem.")]

	emp = frappe.db.get_value(
		"Employee", row.employee, ["status", "date_of_joining", "relieving_date", "company"], as_dict=True
	)
	if not emp:
		return R_NENAPAROVANO, [_issue_text("I01", "Zaměstnanec neexistuje.")]
	if emp.status == "Inactive":
		return R_BLOKOVANO, [_issue_text("I02", "Zaměstnanec je neaktivní.")]
	if not emp.date_of_joining:
		return R_BLOKOVANO, [_issue_text("I03", "Zaměstnanec nemá datum nástupu.")]
	if emp.relieving_date and getdate(emp.relieving_date) < start:
		return R_BLOKOVANO, [_issue_text("I04", "Zaměstnanec odešel před začátkem období.")]
	if getdate(emp.date_of_joining) > end:
		return R_BLOKOVANO, [_issue_text("I05", "Zaměstnanec nastoupil po konci období.")]
	if emp.company != doc.company:
		return R_BLOKOVANO, [_issue_text("I06", "Zaměstnanec patří jiné firmě.")]
	if existing_slip(row.employee, start, end, exclude_import=doc.name):
		return R_BLOKOVANO, [_issue_text("I07", "Zaměstnanec už má za toto období jinou výplatní pásku.")]
	if not has_holiday_list(row.employee, start, end):
		return R_BLOKOVANO, [_issue_text("I08", "Zaměstnanec ani firma nemá přiřazený Holiday List.")]
	if flt(row.k_vyplate) - flt(row.rucni_srazka) < 0:
		return R_BLOKOVANO, [_issue_text("I10", "Ruční srážka je vyšší než částka k výplatě.")]
	return R_PRIPRAVENO, []


def existing_slip(employee: str, start, end, exclude_import: str | None = None) -> str | None:
	filters = {"employee": employee, "start_date": start, "end_date": end, "docstatus": ["!=", 2]}
	for slip in frappe.get_all("Salary Slip", filters=filters, fields=["name", "jmhz_import"]):
		if exclude_import and slip.jmhz_import == exclude_import:
			continue
		return slip.name
	return None


def has_holiday_list(employee: str, start, end) -> bool:
	from hrms.utils.holiday_list import get_holiday_list_ranges_for_employee

	return bool(get_holiday_list_ranges_for_employee(employee, start, end, raise_exception=False))


def can_create_slips(doc) -> bool:
	return (
		doc.status == NAHLED
		and any(r.status == R_PRIPRAVENO for r in doc.rows)
		and all(r.status in ROW_DONE for r in doc.rows)
	)


# --- step 6.3: create slips ----------------------------------------------------


def create_slips(doc) -> dict:
	"""Create Additional Salaries and draft Salary Slips for all ready rows.

	Runs in one transaction (savepoint). Any error rolls everything back, the import
	stays in Náhled and gets the error in `issues`.
	"""
	doc.flags.jmhz_internal = True
	refresh_rows(doc)
	if not can_create_slips(doc):
		frappe.throw(
			_("Pásky jde vytvořit, jen když jsou všechny osoby připravené, přeskočené nebo bez příjmu.")
		)

	savepoint = "jmhz_create_slips"
	frappe.db.savepoint(savepoint)
	current = None
	try:
		created = 0
		for row in doc.rows:
			if row.status != R_PRIPRAVENO:
				continue
			current = row.pseudonym
			row.salary_slip = create_slip_for_row(doc, row)
			row.status = R_VYTVORENA
			created += 1
		doc.status = VYTVORENO
		doc.created_slips = created
		doc.imported_on = now_datetime()
		doc.imported_by = frappe.session.user
		doc.save(ignore_permissions=True)
		doc.flags.jmhz_internal = False
	except Exception as e:
		frappe.db.rollback(save_point=savepoint)
		message = getattr(e, "jmhz_message", None) or _error_text(current, e)
		frappe.log_error(title="JMHZ: vytvoření pásek")
		# the HRMS dialogs of the failed attempt would only confuse, the result carries the message
		frappe.local.message_log = []
		frappe.db.set_value(
			IMPORT, doc.name, "issues", _append_issue(doc.issues, message), update_modified=False
		)
		return {"ok": False, "message": message}
	return {"ok": True, "created": created}


def _error_text(pseudonym: str | None, e: Exception) -> str:
	from frappe.utils import strip_html

	detail = strip_html(str(e)).strip() or type(e).__name__
	return f"{pseudonym or '-'}: {detail}. Nic se neuložilo."


def _append_issue(existing: str | None, message: str) -> str:
	lines = [line for line in (existing or "").splitlines() if not line.startswith("Chyba: ")]
	return "\n".join([*lines, f"Chyba: {message}"])


class SlipCheckError(frappe.ValidationError):
	def __init__(self, pseudonym: str, what: str):
		self.jmhz_message = f"I09: {pseudonym}: {what}"
		super().__init__(self.jmhz_message)


def create_slip_for_row(doc, row) -> str:
	employee = row.employee
	emp = frappe.db.get_value("Employee", employee, ["date_of_joining", "relieving_date"], as_dict=True)
	start, end = getdate(doc.period_start), getdate(doc.period_end)
	currency = _company_currency(doc.company)

	ensure_person(employee, row.ik_mpsv)
	ensure_structure_assignment(employee, doc.company, emp.date_of_joining, start, currency)

	payroll_date = min(end, getdate(emp.relieving_date)) if emp.relieving_date else end
	expected = expected_lines(row)
	for component, amount in expected.items():
		create_additional_salary(employee, doc, component, amount, payroll_date, currency, row.pseudonym)

	slip = frappe.get_doc(
		{
			"doctype": "Salary Slip",
			"employee": employee,
			"company": doc.company,
			"start_date": start,
			"end_date": end,
			"posting_date": payroll_date,
			"payroll_frequency": "Monthly",
			"currency": currency,
			"jmhz_import": doc.name,
			"jmhz_zuctovany_prijem": row.zuctovano,
			"jmhz_cisty_prijem": row.cisty_prijem,
			"jmhz_danovy_bonus": row.danovy_bonus,
			"jmhz_rucni_srazka": flt(row.rucni_srazka),
			"jmhz_k_vyplate": flt(row.k_vyplate) - flt(row.rucni_srazka),
			"jmhz_id_ppv": row.id_ppv,
			"jmhz_odpracovane_hodiny": row.odpracovane_hodiny,
			"jmhz_odpracovane_dny": row.odpracovane_dny,
			"jmhz_hodiny_dovolena": row.hodiny_dovolena,
			"jmhz_neodpracovane_hodiny": row.neodpracovane_hodiny,
			"jmhz_sp_zamestnavatel": row.sp_zamestnavatel,
			"jmhz_zp_zamestnavatel": row.zp_zamestnavatel,
		}
	)
	slip.flags.jmhz_internal = True
	slip.insert(ignore_permissions=True)
	check_slip(slip, row, expected)
	return slip.name


def expected_lines(row) -> dict[str, float]:
	"""{component: amount} the slip must contain, including the manual deduction."""
	out = {li["component"]: flt(li["amount"]) for li in row_lines(row)}
	if flt(row.rucni_srazka):
		out[C.MANUAL_DEDUCTION.name] = out.get(C.MANUAL_DEDUCTION.name, 0) + flt(row.rucni_srazka)
	return out


def ensure_person(employee: str, ik_mpsv: str | None) -> None:
	if not ik_mpsv or frappe.db.exists(PERSON, employee):
		return
	frappe.get_doc({"doctype": PERSON, "employee": employee, "ik_mpsv": ik_mpsv}).insert(
		ignore_permissions=True
	)


def ensure_structure_assignment(employee, company, date_of_joining, period_start, currency) -> None:
	name = structure_name()
	needed_from = max(getdate(period_start), getdate(date_of_joining))
	if frappe.db.exists(
		"Salary Structure Assignment",
		{"employee": employee, "salary_structure": name, "docstatus": 1, "from_date": ["<=", needed_from]},
	):
		return
	ssa = frappe.get_doc(
		{
			"doctype": "Salary Structure Assignment",
			"employee": employee,
			"salary_structure": name,
			"company": company,
			"currency": currency,
			"from_date": date_of_joining,
			"base": 0,
		}
	)
	ssa.insert(ignore_permissions=True)
	ssa.submit()


def create_additional_salary(
	employee, doc, component: str, amount: float, payroll_date, currency, pseudonym: str
) -> None:
	add = frappe.get_doc(
		{
			"doctype": "Additional Salary",
			"employee": employee,
			"company": doc.company,
			"salary_component": component,
			"type": C.BY_NAME[component].component_type,
			"amount": amount,
			"payroll_date": payroll_date,
			"currency": currency,
			"overwrite_salary_structure_amount": 1,
			"is_recurring": 0,
			"jmhz_import": doc.name,
		}
	)
	add.insert(ignore_permissions=True)
	if not add.overwrite_salary_structure_amount:
		# HRMS switches overwrite off with only a message when the component is not in the structure
		raise SlipCheckError(pseudonym, f"složka {component} není ve struktuře mzdy")
	add.submit()


def check_slip(slip, row, expected: dict[str, float]) -> None:
	"""I09: the slip must match the report exactly, line by line."""

	def fail(what: str):
		raise SlipCheckError(row.pseudonym, what)

	if slip.salary_structure != structure_name():
		fail("páska použila jinou strukturu mzdy (jiné přiřazení struktury)")
	actual: dict[str, float] = {}
	for table in ("earnings", "deductions"):
		for d in slip.get(table):
			actual[d.salary_component] = actual.get(d.salary_component, 0) + flt(d.amount)
	missing = sorted(set(expected) - set(actual))
	extra = sorted(set(actual) - set(expected))
	if missing:
		fail(f"chybí řádky {', '.join(missing)}")
	if extra:
		fail(f"řádky navíc {', '.join(extra)} (cizí Additional Salary nebo složka struktury)")
	differs = sorted(c for c in expected if abs(expected[c] - actual[c]) > TOLERANCE)
	if differs:
		fail(f"jiná částka u {', '.join(differs)}")
	earnings = sum(v for c, v in expected.items() if C.BY_NAME[c].kind == "earning")
	deductions = sum(v for c, v in expected.items() if C.BY_NAME[c].kind == "deduction")
	if abs(flt(slip.gross_pay) - earnings) > TOLERANCE:
		fail("hrubá mzda (gross_pay) nesedí")
	if abs(flt(slip.total_deduction) - deductions) > TOLERANCE:
		fail("srážky celkem nesedí")
	if abs(flt(slip.net_pay) - flt(slip.jmhz_k_vyplate)) > TOLERANCE:
		fail("částka k výplatě (net_pay) nesedí")
	if abs(flt(slip.gross_pay) - (flt(row.zuctovano) + flt(row.danovy_bonus))) > TOLERANCE:
		fail("hrubá mzda neodpovídá 10286 + 10306")
	if flt(slip.get("total_loan_repayment")):
		fail("páska obsahuje splátku půjčky")
	if slip.get("employer_contributions"):
		fail("páska obsahuje odvody zaměstnavatele ze struktury")


# --- step 6.4: submit ------------------------------------------------------------


def submit_slips(doc) -> dict:
	if doc.status != VYTVORENO:
		frappe.throw(_("Odeslat jde jen import ve stavu {0}.").format(VYTVORENO))
	blocked = [r.pseudonym for r in doc.rows if r.salary_slip and r.pdf_check == "Neshoda"]
	if blocked:
		frappe.throw(
			_("PDF páska nesedí s JMHZ u osob {0}. Zkontrolujte ji a potvrďte ručně.").format(
				", ".join(blocked)
			)
		)

	savepoint = "jmhz_submit_slips"
	frappe.db.savepoint(savepoint)
	try:
		for row in doc.rows:
			if not row.salary_slip or row.status != R_VYTVORENA:
				continue
			slip = frappe.get_doc("Salary Slip", row.salary_slip)
			if slip.docstatus == 0:
				slip.flags.jmhz_internal = True
				slip.submit()
			row.status = R_ODESLANA
		for row in doc.rows:
			if row.status not in (R_PRESKOCENO, R_NENAPAROVANO):
				row.ik_mpsv = None  # the identifier stays only in JMHZ Person
		doc.status = DOKONCENO
		doc.flags.jmhz_internal = True
		doc.save(ignore_permissions=True)
		doc.flags.jmhz_internal = False
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	return {"ok": True}
