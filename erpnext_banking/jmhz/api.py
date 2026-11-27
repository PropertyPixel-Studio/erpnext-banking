"""Whitelisted methods of the JMHZ module. Only for HR Manager."""

import hashlib

import frappe
from frappe import _

from erpnext_banking.jmhz import importer
from erpnext_banking.jmhz.parser import MAX_XML_BYTES

ROLE = "HR Manager"


def _require_role():
	frappe.only_for(ROLE)


@frappe.whitelist(methods=["POST"])
def upload_xml():
	"""Called by frappe `upload_file` with `method`: the content is in memory only.

	Frappe does not create a File document on this path and deletes its temporary
	file before calling us. The XML is parsed from memory and never stored.
	"""
	_require_role()
	content = frappe.local.uploaded_file
	file_name = frappe.local.uploaded_filename or ""
	if content is None:
		frappe.throw(_("Soubor se nenahrál."))
	if not file_name.lower().endswith(".xml"):
		frappe.throw(_("Nahrajte soubor JMHZ ve formátu XML."))
	if len(content) > MAX_XML_BYTES:
		frappe.throw(_("Soubor je větší než 5 MB."))
	try:
		doc = importer.create_import(content, file_name=file_name)
	except frappe.ValidationError:
		raise
	except Exception:
		# log only the file hash, never the content
		frappe.log_error(
			title="JMHZ: import souboru selhal",
			message=f"sha256={hashlib.sha256(content).hexdigest()}\n{frappe.get_traceback()}",
		)
		frappe.throw(_("Import souboru selhal, podrobnosti jsou v Error Logu."))
	return {"name": doc.name}


def _get_import(name: str):
	_require_role()
	doc = frappe.get_doc(importer.IMPORT, name)
	doc.check_permission("write")
	return doc


@frappe.whitelist(methods=["POST"])
def create_slips(name: str):
	return importer.create_slips(_get_import(name))


@frappe.whitelist(methods=["POST"])
def submit_slips(name: str):
	return importer.submit_slips(_get_import(name))


@frappe.whitelist(methods=["POST"])
def cancel_import(name: str):
	return importer.cancel_import(_get_import(name))


@frappe.whitelist(methods=["POST"])
def run_storno(name: str):
	return importer.run_storno(_get_import(name))


# --- booking of slips that are already submitted ------------------------------------


@frappe.whitelist(methods=["POST"])
def accrual_backfill(from_date: str | None = None, dry_run: int | str = 1):
	"""Preview (`dry_run`) or run the booking of slips submitted while booking was off."""
	from erpnext_banking.jmhz import accounting

	_require_role()
	frappe.get_doc(accounting.SETTINGS).check_permission("write")
	result = accounting.backfill(from_date or None, dry_run=bool(frappe.utils.cint(dry_run)))
	if not result["dry_run"]:
		frappe.db.commit()
	return result


# --- printed slips from the payroll system ----------------------------------------


@frappe.whitelist(methods=["POST"])
def upload_pdf():
	"""Called by `upload_file` with `method` and `doctype`/`docname` of the import.

	Frappe itself stores nothing on this path. `pdf_import.upload` keeps the report and
	the per-person cuts as private Files of the import, for HR Manager only.
	"""
	from erpnext_banking.jmhz import pdf_import
	from erpnext_banking.jmhz.pdf_slips import MAX_PDF_BYTES

	doc = _get_import(frappe.form_dict.docname)
	content = frappe.local.uploaded_file
	file_name = frappe.local.uploaded_filename or ""
	if content is None:
		frappe.throw(_("Soubor se nenahrál."))
	if not file_name.lower().endswith(".pdf"):
		frappe.throw(_("Nahrajte sestavu výplatních pásek ve formátu PDF."))
	if len(content) > MAX_PDF_BYTES:
		frappe.throw(_("Soubor je větší než 10 MB."))
	return pdf_import.upload(doc, content)


@frappe.whitelist(methods=["POST"])
def assign_pdf(name: str, file_url: str, row: str):
	from erpnext_banking.jmhz import pdf_import

	return pdf_import.assign(_get_import(name), file_url, row)


@frappe.whitelist(methods=["POST"])
def confirm_pdf(name: str):
	from erpnext_banking.jmhz import pdf_import

	return pdf_import.confirm_mismatches(_get_import(name))


@frappe.whitelist()
def pdf_review_data(name: str, row: str):
	"""The cut and the values read from it, for the review dialog."""
	from erpnext_banking.jmhz import pdf_import

	_require_role()
	doc = frappe.get_doc(importer.IMPORT, name)
	doc.check_permission("read")
	return pdf_import.review_data(doc, row)


@frappe.whitelist(methods=["POST"])
def correct_pdf(name: str, row: str, values: str | dict):
	from erpnext_banking.jmhz import pdf_import

	return pdf_import.correct(_get_import(name), row, frappe.parse_json(values))


@frappe.whitelist(methods=["POST"])
def review_pdf(name: str, row: str, accept_mismatch: int | str = 0):
	from erpnext_banking.jmhz import pdf_import

	return pdf_import.review(_get_import(name), row, accept_mismatch=bool(frappe.utils.cint(accept_mismatch)))


@frappe.whitelist(methods=["POST"])
def unreview_pdf(name: str, row: str):
	from erpnext_banking.jmhz import pdf_import

	return pdf_import.unreview(_get_import(name), row)


@frappe.whitelist(methods=["POST"])
def delete_pdfs(name: str):
	from erpnext_banking.jmhz import pdf_import

	pdf_import.delete_all(_get_import(name))
	return {"ok": True}
