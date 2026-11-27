"""Printed slips from the payroll system: assignment to import rows, check, storage.

The summary report is stored whole and every slip also as a one-page cut. All of them
are private Files attached to the JMHZ Import (never to a Salary Slip), so only users
who can read the import (HR Manager) can open them. Employees never see them: the
report holds the slips of everybody and a cut still physically contains the rest of
its page.

The printed slip is checked three ways (`mismatches`): the 12 control fields and some
informative values against the JMHZ report, and the internal sums of the slip itself.

Messages never contain names or amounts, only pseudonyms, page numbers and labels.
"""

from __future__ import annotations

import json
import unicodedata
from decimal import Decimal

import frappe
from frappe import _
from frappe.core.doctype.file.utils import get_content_hash
from frappe.utils import flt, now_datetime

from erpnext_banking.jmhz import components as C
from erpnext_banking.jmhz import importer, pdf_slips, slip_data
from erpnext_banking.jmhz.pdf_slips import (
	FIELDS,
	PdfSlip,
	PdfSlipError,
	check_sums,
	extra_from_json,
	extra_mismatches,
	extra_to_json,
	split,
)

BEZ_PDF = "Bez PDF"
SHODA = "Shoda"
NESHODA = "Neshoda"
POTVRZENO = "Potvrzeno ručně"
TOLERANCE = Decimal("0.005")

MZDA_COMPONENTS = (
	C.TARIF,
	C.PREMIE_PRAVIDELNE,
	C.PREMIE_NEPRAVIDELNE,
	C.PRIPLATKY,
	C.MZDA_OSTATNI,
	C.MZDA,
)
# rows that can have a printed slip (skipped rows too, the check does not depend on the Employee)
NO_SLIP_STATUSES = (importer.R_BEZ_PRIJMU, importer.R_ZRUSENO)


# --- comparison ----------------------------------------------------------------


def _d(value) -> Decimal | None:
	return None if value is None else Decimal(str(flt(value)))


def jmhz_values(row) -> dict[str, Decimal | None]:
	"""The 12 printed fields computed from the JMHZ data of the row."""
	lines = {li["component"]: Decimal(str(li["amount"])) for li in importer.row_lines(row)}
	return {
		"hruba_mzda": _d(row.zuctovano),
		"mzda": sum((lines.get(c.name, Decimal(0)) for c in MZDA_COMPONENTS), Decimal(0)),
		"dovolena": lines.get(C.NAHRADA_DOVOLENA.name, Decimal(0)),
		"socialni_pojisteni": lines.get(C.SOCIALNI_POJISTENI.name, Decimal(0)),
		"zdravotni_pojisteni": lines.get(C.ZDRAVOTNI_POJISTENI.name, Decimal(0)),
		"zaloha_na_dan": lines.get(C.ZALOHA_NA_DAN.name, Decimal(0)),
		"zdanitelna_mzda": _d(row.zdanitelna_mzda),
		"dan": _d(row.vypoctena_dan),
		"cista_mzda": _d(row.cisty_prijem),
		"k_vyplate": _d(flt(row.k_vyplate) - flt(row.rucni_srazka)),
		"hodiny": _d(row.odpracovane_hodiny),
		# 10267 does not exist in schema 1.5
		"dny": None if row.odpracovane_dny is None else _d(row.odpracovane_dny),
	}


def mismatches(
	row, fields: dict[str, Decimal], extra: dict | None = None, rok: int | None = None
) -> list[str]:
	"""Captions of everything that does not fit. `extra` is the rest of the printed slip,
	None for a cut read by a version that did not read it (then only the 12 fields)."""
	expected = jmhz_values(row)
	out = []
	for key, label in FIELDS.items():
		if expected[key] is None:
			continue
		if abs(Decimal(str(fields[key])) - expected[key]) > TOLERANCE:
			out.append(label.rstrip(":"))
	if extra is None:
		return out
	employer = {"zamestnavatel_sp": _d(row.sp_zamestnavatel), "zamestnavatel_zp": _d(row.zp_zamestnavatel)}
	out += extra_mismatches(extra, importer.row_extra(row), employer)
	out += check_sums(fields, extra, rok)
	return out


def _words(name: str | None) -> set[str]:
	text = unicodedata.normalize("NFKD", name or "")
	text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
	return {w for w in text.replace(",", " ").split() if w}


def name_matches(row, name: str) -> bool:
	return bool(row.employee_name) and _words(row.employee_name) == _words(name)


# --- assignment ------------------------------------------------------------------


def candidates(doc, fields: dict[str, Decimal]) -> list:
	out = []
	for row in doc.rows:
		if row.status in NO_SLIP_STATUSES:
			continue
		values = jmhz_values(row)
		if values["hruba_mzda"] == fields["hruba_mzda"] and values["k_vyplate"] == fields["k_vyplate"]:
			out.append(row)
	return out


def match(doc, slips: list[PdfSlip]) -> tuple[dict[str, PdfSlip], list[PdfSlip]]:
	"""Assign slips to rows by amounts; the name confirms and breaks ties."""
	proposed: dict[str, list[PdfSlip]] = {}
	unassigned: list[PdfSlip] = []
	for slip in slips:
		rows = candidates(doc, slip.fields)
		if len(rows) > 1:
			rows = [r for r in rows if name_matches(r, slip.name)]
		if len(rows) == 1 and (not rows[0].employee_name or name_matches(rows[0], slip.name)):
			proposed.setdefault(rows[0].name, []).append(slip)
		else:
			unassigned.append(slip)
	assigned = {}
	for row_name, row_slips in proposed.items():
		if len(row_slips) == 1:
			assigned[row_name] = row_slips[0]
		else:  # two printed slips claim the same row
			unassigned.extend(row_slips)
	return assigned, unassigned


# --- storage -----------------------------------------------------------------------


def _save_file(doc, file_name: str, content: bytes, field: str | None = None) -> str:
	"""Store one cut or the report as a private File of the import and return its URL.

	The same content uploaded again reuses its File. Frappe would otherwise create a second File
	with the same URL (same content hash), and replacing the row's "old" cut would delete both of
	them together with the file on disk.

	`field` is the Attach field of the import that will hold the URL. Without it Frappe adds a
	second File record for the field when the import is saved.
	"""
	existing = frappe.db.get_value(
		"File",
		{
			"attached_to_doctype": importer.IMPORT,
			"attached_to_name": doc.name,
			"content_hash": get_content_hash(content),
		},
		"file_url",
	)
	if existing:
		return existing
	f = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": importer.IMPORT,
			"attached_to_name": doc.name,
			"attached_to_field": field,
			"is_private": 1,
			"content": content,
		}
	)
	f.flags.ignore_duplicate_entry_error = True
	f.insert(ignore_permissions=True)
	return f.file_url


def _delete_file(doc, file_url: str | None) -> None:
	if not file_url:
		return
	for name in frappe.get_all(
		"File",
		filters={"file_url": file_url, "attached_to_doctype": importer.IMPORT, "attached_to_name": doc.name},
		pluck="name",
	):
		frappe.delete_doc("File", name, ignore_permissions=True, force=True)


def _referenced(doc, file_url: str, except_row=None) -> bool:
	return any(r.slip_pdf == file_url for r in doc.rows if r is not except_row) or any(
		p["file_url"] == file_url for p in _unassigned(doc)
	)


def _stored(row) -> dict:
	"""Printed values of the row's cut ({"fields": ...}) and the JMHZ values a manual
	confirmation was given for ({"confirmed": ...})."""
	return json.loads(row.pdf_fields or "{}")


def row_pdf_extra(row) -> dict | None:
	"""The rest of the printed slip of the row. None = no cut, or a cut read by a version
	that read only the 12 control fields."""
	return extra_from_json(json.loads(row.pdf_extra)) if row.pdf_extra else None


def row_pdf_unread(row) -> list[str]:
	return json.loads(row.pdf_unread or "[]")


def row_pdf_fields(row) -> dict | None:
	"""The 12 control fields read from the cut of the row."""
	fields = _stored(row).get("fields")
	return {k: Decimal(v) for k, v in fields.items()} if fields else None


def _clear_review(row) -> None:
	row.pdf_reviewed = 0
	row.pdf_reviewed_by = None
	row.pdf_reviewed_on = None


def _fingerprint(row) -> str:
	"""What a manual confirmation was given for: the JMHZ values and the printed values."""
	data = {k: None if v is None else str(v) for k, v in jmhz_values(row).items()}
	if row.pdf_extra:  # a cut read by an older version keeps the fingerprint it was confirmed with
		data["jmhz_extra"] = importer.row_extra(row)
		data["pdf_extra"] = json.loads(row.pdf_extra)
	return json.dumps(data, sort_keys=True)


def _apply_check(row, fields: dict[str, Decimal], keep_confirmation: bool, rok: int | None) -> None:
	wrong = mismatches(row, fields, row_pdf_extra(row), rok)
	if not wrong:
		row.pdf_check = SHODA
	else:
		row.pdf_check = POTVRZENO if keep_confirmation else NESHODA
	row.pdf_check_detail = ", ".join(wrong) if wrong else None


def _set_row_pdf(doc, row, file_url: str, fields: dict[str, Decimal], extra: dict, unread: list[str]) -> None:
	if row.slip_pdf == file_url and row.pdf_extra:
		# The same cut again (a File is found by its content, so the same URL is the same
		# cut): what a person corrected and confirmed stays. "Smazat PDF" starts over.
		recheck(row, doc.rok)
		return
	old = row.slip_pdf
	row.slip_pdf = file_url
	if old and old != file_url and not _referenced(doc, old, except_row=row):
		_delete_file(doc, old)
	row.pdf_fields = json.dumps({"fields": {k: str(v) for k, v in fields.items()}})
	# always JSON: an empty object says "read, nothing found", unlike the missing value of a
	# cut stored by a version that read only the control fields
	row.pdf_extra = json.dumps(extra_to_json(extra), ensure_ascii=False)
	row.pdf_unread = json.dumps(unread)
	_clear_review(row)
	_apply_check(row, fields, keep_confirmation=False, rok=doc.rok)


def recheck(row, rok: int | None = None) -> None:
	"""Called on every save in Náhled (importer.refresh_rows).

	The expected "K výplatě" changes with the manual deduction, so the check is computed again
	from the stored printed values. A manual confirmation holds only for the values it was
	given for. A row without a cut is always "Bez PDF".
	"""
	if not row.slip_pdf:
		row.pdf_check = BEZ_PDF
		row.pdf_check_detail = None
		return
	stored = _stored(row)
	if not stored.get("fields"):
		return
	fields = {k: Decimal(v) for k, v in stored["fields"].items()}
	keep = row.pdf_check == POTVRZENO and stored.get("confirmed") == _fingerprint(row)
	_apply_check(row, fields, keep_confirmation=keep, rok=rok)


def _unassigned(doc) -> list[dict]:
	return json.loads(doc.pdf_unassigned or "[]")


def _save(doc) -> None:
	doc.flags.jmhz_internal = True
	doc.save(ignore_permissions=True)
	doc.flags.jmhz_internal = False
	# the draft slips print what the rows hold now
	importer.update_slip_data(doc)


def _check_status(doc) -> None:
	if doc.status not in (importer.NAHLED, importer.VYTVORENO) or doc.typ_podani == "S":
		frappe.throw(_("PDF pásky jde nahrát jen k importu ve stavu Náhled nebo Pásky vytvořeny."))


# --- actions -----------------------------------------------------------------------


def _save_report(doc, content: bytes) -> None:
	"""Keep the whole report with the import. A new report replaces the previous one."""
	old = doc.report_pdf
	doc.report_pdf = _save_file(doc, f"{doc.name}-sestava.pdf", content, field="report_pdf")
	if old and old != doc.report_pdf:
		_delete_file(doc, old)


def upload(doc, content: bytes) -> dict:
	_check_status(doc)
	try:
		slips = split(content)
	except PdfSlipError as e:
		frappe.throw(str(e), title=_("PDF nejde zpracovat"))
	if any(s.period and s.period != (int(doc.mesic), int(doc.rok)) for s in slips):
		# a report of another month would only produce mismatches; nothing is stored
		frappe.throw(_("Sestava je za jiné období než tento import."), title=_("PDF nejde zpracovat"))
	assigned, unassigned = match(doc, slips)
	_save_report(doc, content)

	rows = {r.name: r for r in doc.rows}
	for row_name, slip in assigned.items():
		row = rows[row_name]
		url = _save_file(doc, f"{doc.name}-{row.pseudonym}.pdf", slip.pdf)
		_set_row_pdf(doc, row, url, slip.fields, slip.extra, slip.unread)

	# a cut that is now assigned leaves the pending list; the same cut is listed only once
	row_urls = {r.slip_pdf for r in doc.rows if r.slip_pdf}
	pending = [p for p in _unassigned(doc) if p["file_url"] not in row_urls]
	known = row_urls | {p["file_url"] for p in pending}
	for slip in unassigned:
		url = _save_file(doc, f"{doc.name}-neprirazeno-{slip.page}-{slip.index}.pdf", slip.pdf)
		if url in known:
			continue
		known.add(url)
		pending.append(
			{
				"file_url": url,
				"label": f"strana {slip.page}, páska {slip.index}",
				"fields": {k: str(v) for k, v in slip.fields.items()},
				"extra": extra_to_json(slip.extra),
				"unread": slip.unread,
			}
		)
	doc.pdf_unassigned = json.dumps(pending, ensure_ascii=False) if pending else None
	_save(doc)
	return {
		"slips": len(slips),
		"assigned": len(assigned),
		"unassigned": [p["label"] for p in pending],
		"mismatch": [r.pseudonym for r in doc.rows if r.pdf_check == NESHODA],
		"unread": [r.pseudonym for r in doc.rows if row_pdf_unread(r)],
	}


def assign(doc, file_url: str, row_name: str) -> dict:
	"""Manual assignment of a slip that could not be assigned automatically."""
	_check_status(doc)
	pending = _unassigned(doc)
	item = next((p for p in pending if p["file_url"] == file_url), None)
	row = next((r for r in doc.rows if r.name == row_name), None)
	if not item or not row:
		frappe.throw(_("Nepřiřazená páska nebo řádek neexistuje."))
	if row.status in NO_SLIP_STATUSES:
		frappe.throw(_("K řádku bez příjmu nebo zrušenému řádku nejde PDF přiřadit."))
	fields = {k: Decimal(v) for k, v in item["fields"].items()}
	_set_row_pdf(doc, row, file_url, fields, extra_from_json(item.get("extra")), item.get("unread") or [])
	pending.remove(item)
	doc.pdf_unassigned = json.dumps(pending, ensure_ascii=False) if pending else None
	_save(doc)
	return {"pdf_check": row.pdf_check}


def confirm_mismatches(doc) -> dict:
	"""HR consciously accepts the mismatching rows (submit is blocked until then)."""
	confirmed = []
	for row in doc.rows:
		if row.pdf_check == NESHODA:
			row.pdf_check = POTVRZENO
			stored = _stored(row)
			stored["confirmed"] = _fingerprint(row)
			row.pdf_fields = json.dumps(stored)
			confirmed.append(row.pseudonym)
	if not confirmed:
		frappe.throw(_("Žádný řádek nemá neshodu s PDF."))
	_save(doc)
	return {"confirmed": confirmed}


def delete_all(doc, save: bool = True) -> None:
	"""Button "Smazat PDF" and cancelling an import: remove the report and all its cuts."""
	for row in doc.rows:
		_delete_file(doc, row.slip_pdf)
		row.slip_pdf = None
		row.pdf_fields = None
		row.pdf_extra = None
		row.pdf_unread = None
		row.pdf_check = BEZ_PDF
		row.pdf_check_detail = None
		_clear_review(row)
	for item in _unassigned(doc):
		_delete_file(doc, item["file_url"])
	doc.pdf_unassigned = None
	_delete_file(doc, doc.report_pdf)
	doc.report_pdf = None
	if save:
		_save(doc)


# --- review by a person --------------------------------------------------------------
# Reading a printed slip is positional and fragile, and values that exist only on the
# printed slip (leave, wage rate) have nothing to be checked against. So every row with a
# printed slip has to be looked at and confirmed before the slips can be submitted.


def _row(doc, row_name: str):
	row = next((r for r in doc.rows if r.name == row_name), None)
	if not row or not row.slip_pdf:
		frappe.throw(_("Řádek neexistuje nebo nemá PDF pásku."))
	return row


def _display(value) -> str:
	"""A number the way it is typed and printed here: decimal comma, no thousands separator."""
	return str(value).replace(".", ",") if isinstance(value, Decimal) else str(value)


def review_data(doc, row_name: str) -> dict:
	"""Everything the review dialog shows for one row: the cut and the values read from it."""
	row = _row(doc, row_name)
	fields = row_pdf_fields(row) or {}
	extra = row_pdf_extra(row)
	unread = set(row_pdf_unread(row))
	corrected = set(_stored(row).get("corrected") or [])
	values = {**(extra or {}), **fields}
	blocks = []
	for group, keys in slip_data.LAYOUT.items():
		rows = []
		for key in keys:
			control = key in FIELDS
			if extra is None and not control:
				continue  # a cut read by a version that read only the control fields
			rows.append(
				{
					"key": key,
					"label": slip_data.CAPTIONS[key],
					"value": _display(values[key]) if key in values else "",
					"kind": slip_data.KINDS[key],
					"editable": not control,
					"unread": key in unread,
					"corrected": key in corrected,
				}
			)
		if rows:
			blocks.append({"key": group, "title": pdf_slips.GROUPS[group], "rows": rows})
	return {
		"row": row.name,
		"pseudonym": row.pseudonym,
		"employee_name": row.employee_name,
		"slip_pdf": row.slip_pdf,
		"pdf_check": row.pdf_check,
		"pdf_check_detail": row.pdf_check_detail,
		"reviewed": bool(row.pdf_reviewed),
		"reviewed_by": row.pdf_reviewed_by,
		"reviewed_on": str(row.pdf_reviewed_on or ""),
		"editable": doc.status in (importer.NAHLED, importer.VYTVORENO),
		"blocks": blocks,
	}


def _typed(key: str, text: str):
	"""A value typed by HR. Raises a ValueError with no value in it."""
	if key in pdf_slips.TEXT_KEYS:
		value = text[: pdf_slips.MAX_TEXT]
		if key == "ucet" and not pdf_slips.ACCOUNT.match(value):
			raise ValueError
		return value
	number = pdf_slips.parse_number(text.replace(".", ","))
	if number is None:
		raise ValueError
	return number


def correct(doc, row_name: str, values: dict) -> dict:
	"""HR fixes values that were read wrong or not at all. An empty value says "nothing is
	printed there". The 12 control fields cannot be changed: they are checked against the
	JMHZ report. A correction cancels the review and a manual confirmation of a mismatch."""
	_check_status(doc)
	row = _row(doc, row_name)
	extra = row_pdf_extra(row)
	if extra is None:
		frappe.throw(_("Tuto PDF pásku načetla starší verze. Nahrajte sestavu znovu."))
	unread = row_pdf_unread(row)
	stored = _stored(row)
	corrected = list(stored.get("corrected") or [])
	changed = False
	for key, text in (values or {}).items():
		if key not in pdf_slips.EXTRA_KEYS:
			frappe.throw(_("Toto pole nejde opravit."))
		text = str(text or "").strip()
		try:
			value = _typed(key, text) if text else None
		except ValueError:
			frappe.throw(_("Pole {0}: zadaná hodnota není platná.").format(slip_data.CAPTIONS[key]))
		if value == extra.get(key) and key not in unread:
			continue
		changed = True
		if value is None:
			extra.pop(key, None)
		else:
			extra[key] = value
		if key in unread:
			unread.remove(key)
		if key not in corrected:
			corrected.append(key)
	if not changed:
		return {"changed": False, "pdf_check": row.pdf_check}
	stored["corrected"] = corrected
	row.pdf_fields = json.dumps(stored)
	row.pdf_extra = json.dumps(extra_to_json(extra), ensure_ascii=False)
	row.pdf_unread = json.dumps(unread)
	_clear_review(row)
	_apply_check(row, row_pdf_fields(row), keep_confirmation=False, rok=doc.rok)
	_save(doc)
	return {"changed": True, "pdf_check": row.pdf_check}


def review(doc, row_name: str, accept_mismatch: bool = False) -> dict:
	"""HR compared the cut with the read values of one row and confirms them."""
	_check_status(doc)
	row = _row(doc, row_name)
	if row.pdf_check == NESHODA:
		if not accept_mismatch:
			frappe.throw(
				_("PDF páska má neshodu ({0}). Potvrďte ji spolu s kontrolou.").format(row.pdf_check_detail)
			)
		row.pdf_check = POTVRZENO
		stored = _stored(row)
		stored["confirmed"] = _fingerprint(row)
		row.pdf_fields = json.dumps(stored)
	row.pdf_reviewed = 1
	row.pdf_reviewed_by = frappe.session.user
	row.pdf_reviewed_on = now_datetime()
	_save(doc)
	return {"reviewed": True, "pdf_check": row.pdf_check}


def unreview(doc, row_name: str) -> dict:
	_check_status(doc)
	_clear_review(_row(doc, row_name))
	_save(doc)
	return {"reviewed": False}
