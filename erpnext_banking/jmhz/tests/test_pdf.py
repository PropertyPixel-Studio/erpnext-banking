"""Integration tests of printed payroll slips. Synthetic data only.

bench --site <site> run-tests --module erpnext_banking.jmhz.tests.test_pdf
"""

import json

import frappe
from frappe.utils.response import download_private_file
from werkzeug.exceptions import Forbidden

from erpnext_banking.jmhz import api, importer, pdf_import
from erpnext_banking.jmhz.tests.synthetic import form
from erpnext_banking.jmhz.tests.synthetic_pdf import (
	advance_tax,
	build_pdf,
	report_pdf,
	slip_texts,
	slip_values,
)
from erpnext_banking.jmhz.tests.test_importer import JmhzTestCase


def jmhz_form(ik, ppv, gross=15353, sp=1091, zp=691, hours="40.5", days=5, **kw):
	"""The report side of `slip_values()`: the tax is fully covered by the taxpayer credit."""
	args = dict(
		ik=ik,
		ppv=ppv,
		zuct=gross,
		mzda=gross,
		sp=sp,
		zp=zp,
		cisty=gross - sp - zp,
		zaklad_dane=gross,
		vypoctena_dan=advance_tax(gross),
		slevy={"zakladniSleva": advance_tax(gross)},
		hodiny=hours,
		dny=days,
	)
	args.update(kw)
	return form(**args)


class TestPdf(JmhzTestCase):
	def setUp(self):
		super().setUp()
		self.a, self.b = self.employee(), self.employee()
		self.name_a = frappe.db.get_value("Employee", self.a, "employee_name")
		self.name_b = frappe.db.get_value("Employee", self.b, "employee_name")

	@staticmethod
	def reversed_name(name):
		return " ".join(reversed(name.split()))  # the printed slip has "surname first name"

	def upload(self, doc, pdf, file_name="sestava.pdf"):
		frappe.local.uploaded_file = pdf
		frappe.local.uploaded_filename = file_name
		frappe.form_dict.docname = doc.name
		try:
			return api.upload_pdf()
		finally:
			frappe.local.uploaded_file = None
			frappe.local.uploaded_filename = None
			frappe.form_dict.pop("docname", None)

	def pdf_files(self, doc):
		return frappe.get_all(
			"File",
			filters={"attached_to_doctype": importer.IMPORT, "attached_to_name": doc.name},
			fields=["name", "file_name", "is_private", "file_url"],
		)

	def two_person_import(self, same_amounts=False):
		b = {} if same_amounts else {"gross": 20000, "sp": 1420, "zp": 900}
		return self.import_file(
			jmhz_form("5000000001", "9100000001"),
			jmhz_form("5000000002", "9100000002", **b),
			pair={"5000000001": self.a, "5000000002": self.b},
		)

	def test_prirazeni_podle_castek(self):
		doc = self.two_person_import()
		files_before = frappe.db.count("File")
		pdf = report_pdf(
			[
				(self.reversed_name(self.name_a), slip_values(hours=40.5, days=5)),
				(
					self.reversed_name(self.name_b),
					slip_values(gross=20000, sp=1420, zp=900, hours=40.5, days=5),
				),
			]
		)
		res = self.upload(doc, pdf)
		self.assertEqual((res["slips"], res["assigned"], res["unassigned"], res["mismatch"]), (2, 2, [], []))
		doc.reload()
		self.assertEqual([r.pdf_check for r in doc.rows], [pdf_import.SHODA, pdf_import.SHODA])
		files = self.pdf_files(doc)
		# the report and the two cuts are stored, all private and attached to the import
		self.assertEqual(len(files), 3)
		self.assertEqual(frappe.db.count("File"), files_before + 3)
		self.assertTrue(all(f.is_private for f in files))
		self.assertEqual({r.slip_pdf for r in doc.rows} | {doc.report_pdf}, {f.file_url for f in files})
		self.assertNotIn(doc.report_pdf, {r.slip_pdf for r in doc.rows})
		self.assertTrue(all(r.pseudonym in r.slip_pdf for r in doc.rows))
		# no link from the salary slip side
		self.create(doc)
		self.assertIsNone(frappe.get_meta("Salary Slip").get_field("slip_pdf"))

	def test_stejne_castky_rozhodne_jmeno(self):
		doc = self.two_person_import(same_amounts=True)
		pdf = report_pdf(
			[
				(self.reversed_name(self.name_b), slip_values()),
				(self.reversed_name(self.name_a), slip_values()),
			]
		)
		res = self.upload(doc, pdf)
		self.assertEqual(res["assigned"], 2)
		doc.reload()
		self.assertEqual([r.pdf_check for r in doc.rows], [pdf_import.SHODA, pdf_import.SHODA])
		a_row = next(r for r in doc.rows if r.employee == self.a)
		self.assertIn(a_row.pseudonym, a_row.slip_pdf)

	def test_rucni_prirazeni(self):
		doc = self.two_person_import(same_amounts=True)
		res = self.upload(doc, report_pdf([("Neznámý Člověk", slip_values())]))
		self.assertEqual((res["assigned"], res["unassigned"]), (0, ["strana 1, páska 1"]))
		doc.reload()
		(pending,) = pdf_import._unassigned(doc)
		self.assertNotIn("Neznámý", pending["label"])
		api.assign_pdf(doc.name, pending["file_url"], doc.rows[1].name)
		doc.reload()
		self.assertEqual(doc.rows[1].pdf_check, pdf_import.SHODA)
		self.assertEqual(doc.rows[1].slip_pdf, pending["file_url"])
		self.assertFalse(doc.pdf_unassigned)

	def test_neshoda_blokuje_odeslani(self):
		doc = self.two_person_import()
		pdf = report_pdf(
			[
				(self.reversed_name(self.name_a), slip_values(hours=41)),  # hours differ
				(self.reversed_name(self.name_b), slip_values(gross=20000, sp=1420, zp=900)),
			]
		)
		res = self.upload(doc, pdf)
		doc.reload()
		a_row = doc.rows[0]
		self.assertEqual(res["mismatch"], [a_row.pseudonym])
		self.assertEqual(a_row.pdf_check, pdf_import.NESHODA)
		self.assertEqual(a_row.pdf_check_detail, "Hodiny - odpr.")
		self.assertTrue(self.create(doc)["ok"])
		with self.assertRaises(frappe.ValidationError):
			importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		self.assertEqual(api.confirm_pdf(doc.name), {"confirmed": [a_row.pseudonym]})
		self.assertTrue(importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))["ok"])

	def test_rucni_srazka_odhali_neshodu(self):
		doc = self.import_file(jmhz_form("5000000003", "9100000003"), pair={"5000000003": self.a})
		doc.rows[0].rucni_srazka = 300
		doc.rows[0].rucni_srazka_poznamka = "test"
		doc.save(ignore_permissions=True)
		# the printed slip pays the full amount -> "K výplatě" does not match
		res = self.upload(doc, report_pdf([(self.reversed_name(self.name_a), slip_values())]))
		self.assertEqual(res["assigned"], 0)  # amounts do not match the expected pay
		doc.reload()
		(pending,) = pdf_import._unassigned(doc)
		api.assign_pdf(doc.name, pending["file_url"], doc.rows[0].name)
		doc.reload()
		self.assertEqual(doc.rows[0].pdf_check, pdf_import.NESHODA)
		self.assertEqual(doc.rows[0].pdf_check_detail, "K výplatě")

	def test_rucni_srazka_po_nahrani_pdf(self):
		"""The opposite order: PDF first (Shoda), then a manual deduction -> Neshoda, blocks submit."""
		doc = self.import_file(jmhz_form("5000000005", "9100000005"), pair={"5000000005": self.a})
		self.upload(doc, report_pdf([(self.reversed_name(self.name_a), slip_values())]))
		doc.reload()
		self.assertEqual(doc.rows[0].pdf_check, pdf_import.SHODA)

		doc.rows[0].rucni_srazka = 500
		doc.rows[0].rucni_srazka_poznamka = "omyl"
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.rows[0].pdf_check, pdf_import.NESHODA)
		self.assertEqual(doc.rows[0].pdf_check_detail, "K výplatě")
		self.assertTrue(self.create(doc)["ok"])
		with self.assertRaises(frappe.ValidationError):
			importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))

	def test_potvrzeni_plati_jen_pro_potvrzene_hodnoty(self):
		doc = self.import_file(jmhz_form("5000000006", "9100000006"), pair={"5000000006": self.a})
		self.upload(doc, report_pdf([(self.reversed_name(self.name_a), slip_values(hours=41))]))
		api.confirm_pdf(doc.name)
		doc.reload()
		self.assertEqual(doc.rows[0].pdf_check, pdf_import.POTVRZENO)
		doc.save(ignore_permissions=True)  # saving again keeps the confirmation
		self.assertEqual(doc.rows[0].pdf_check, pdf_import.POTVRZENO)

		doc.rows[0].rucni_srazka = 200
		doc.rows[0].rucni_srazka_poznamka = "test"
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.rows[0].pdf_check, pdf_import.NESHODA)
		self.assertEqual(doc.rows[0].pdf_check_detail, "K výplatě, Hodiny - odpr.")

	def test_kontrolu_pdf_nejde_prepnout_rucne(self):
		doc = self.import_file(jmhz_form("5000000007", "9100000007"), pair={"5000000007": self.a})
		self.upload(doc, report_pdf([(self.reversed_name(self.name_a), slip_values(hours=41))]))
		doc.reload()
		doc.rows[0].pdf_check = pdf_import.SHODA  # e.g. edited in the grid
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.rows[0].pdf_check, pdf_import.NESHODA)

	def test_stejne_pdf_dvakrat(self):
		"""Uploading the same report again keeps the cut (same content -> same File URL)."""
		doc = self.two_person_import()
		pdf = report_pdf(
			[
				(self.reversed_name(self.name_a), slip_values()),
				(self.reversed_name(self.name_b), slip_values(gross=20000, sp=1420, zp=900)),
				("Nikdo Neznámý", slip_values(gross=1)),
			]
		)
		self.upload(doc, pdf)
		doc.reload()
		urls = [r.slip_pdf for r in doc.rows]
		files = len(self.pdf_files(doc))
		self.upload(doc, pdf)
		doc.reload()
		self.assertEqual([r.slip_pdf for r in doc.rows], urls)
		self.assertEqual([r.pdf_check for r in doc.rows], [pdf_import.SHODA, pdf_import.SHODA])
		for url in urls:
			self.assertTrue(frappe.db.exists("File", {"file_url": url, "attached_to_name": doc.name}))
		self.assertEqual(len(self.pdf_files(doc)), files)
		self.assertEqual(len(pdf_import._unassigned(doc)), 1)  # listed once, not twice

	def test_rucni_prirazeni_stejneho_vyrezu_znovu(self):
		doc = self.two_person_import(same_amounts=True)
		pdf = report_pdf([("Neznámý Člověk", slip_values())])
		self.upload(doc, pdf)
		doc.reload()
		(pending,) = pdf_import._unassigned(doc)
		api.assign_pdf(doc.name, pending["file_url"], doc.rows[1].name)
		self.upload(doc, pdf)  # the same cut again, still ambiguous
		doc.reload()
		self.assertFalse(doc.pdf_unassigned)  # already assigned to a row, not listed again
		self.assertTrue(frappe.db.exists("File", {"file_url": doc.rows[1].slip_pdf}))

	def test_paska_preskocene_osoby(self):
		doc = self.import_file(jmhz_form("5000000004", "9100000004"))
		doc.rows[0].skip = 1
		doc.rows[0].skip_reason = "test"
		doc.save(ignore_permissions=True)
		res = self.upload(doc, report_pdf([("Kdokoli Jiný", slip_values())]))
		self.assertEqual(res["assigned"], 1)  # no Employee -> amounts decide
		doc.reload()
		self.assertEqual(doc.rows[0].pdf_check, pdf_import.SHODA)

	def test_neplatne_pdf_nic_neulozi(self):
		doc = self.two_person_import()
		files_before = frappe.db.count("File")
		with self.assertRaises(frappe.ValidationError):
			self.upload(doc, b"%PDF-1.4 broken")
		with self.assertRaises(frappe.ValidationError):
			self.upload(doc, report_pdf([("X Y", slip_values())]), file_name="sestava.txt")
		self.assertEqual(frappe.db.count("File"), files_before)

	def test_smazat_pdf_a_zruseni_importu(self):
		doc = self.two_person_import()
		self.upload(
			doc,
			report_pdf(
				[(self.reversed_name(self.name_a), slip_values()), ("Nikdo Neznámý", slip_values(gross=1))]
			),
		)
		doc.reload()
		self.assertEqual(len(self.pdf_files(doc)), 3)  # the report, a cut and an unassigned cut
		api.delete_pdfs(doc.name)
		doc.reload()
		self.assertEqual(self.pdf_files(doc), [])
		self.assertEqual({r.pdf_check for r in doc.rows}, {pdf_import.BEZ_PDF})
		self.assertFalse(doc.pdf_unassigned or doc.report_pdf)
		self.assertFalse(any(r.pdf_extra or r.pdf_unread or r.pdf_fields for r in doc.rows))

		self.upload(doc, report_pdf([(self.reversed_name(self.name_a), slip_values())]))
		self.create(doc)
		importer.cancel_import(frappe.get_doc(importer.IMPORT, doc.name))
		self.assertEqual(self.pdf_files(doc), [])

	def test_zamestnanec_nema_pristup_k_vyrezu(self):
		doc = self.two_person_import()
		self.upload(doc, report_pdf([(self.reversed_name(self.name_a), slip_values())]))
		doc.reload()
		self.create(doc)
		importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		# the report and the cut stay with the import after the slips are submitted
		files = self.pdf_files(doc)
		self.assertEqual({f.file_url for f in files}, {doc.report_pdf, doc.rows[0].slip_pdf})

		from erpnext_banking.jmhz.tests.test_importer import TestEmployeeAccess

		user = TestEmployeeAccess.make_user(self, self.a)
		frappe.set_user(user)
		# neither the cut nor the report with the slips of everybody, not even by a direct URL
		for f in files:
			file_doc = frappe.get_doc("File", f.name)
			self.assertFalse(frappe.has_permission("File", "read", doc=file_doc))
			self.assertFalse(file_doc.is_downloadable())
			self.assertNotIn(f.name, frappe.get_list("File", pluck="name"))
			with self.assertRaises(Forbidden):
				download_private_file(f.file_url)  # what a direct URL to the file does
		# and no way to it from the slip the employee can read
		slip = frappe.get_doc("Salary Slip", doc.rows[0].salary_slip)
		self.assertTrue(frappe.has_permission("Salary Slip", "read", doc=slip))
		dump = frappe.as_json(slip.as_dict())
		for f in files:
			self.assertNotIn(f.file_url, dump)

	def test_sestava_se_uklada_a_nahrazuje(self):
		doc = self.two_person_import()
		first = report_pdf([(self.reversed_name(self.name_a), slip_values())])
		self.upload(doc, first)
		doc.reload()
		old_report, old_cut = doc.report_pdf, doc.rows[0].slip_pdf
		self.assertTrue(old_report and old_report != old_cut)
		self.assertTrue(frappe.db.get_value("File", {"file_url": old_report}, "is_private"))

		self.upload(doc, first)  # the same report again: nothing new
		doc.reload()
		self.assertEqual((doc.report_pdf, len(self.pdf_files(doc))), (old_report, 2))

		second = report_pdf(
			[
				(self.reversed_name(self.name_a), slip_values()),
				(self.reversed_name(self.name_b), slip_values(gross=20000, sp=1420, zp=900)),
			]
		)
		self.upload(doc, second)  # a new report replaces the old one
		doc.reload()
		self.assertNotEqual(doc.report_pdf, old_report)
		self.assertFalse(frappe.db.exists("File", {"file_url": old_report}))
		self.assertEqual(len(self.pdf_files(doc)), 3)

	def test_cela_paska_na_radku(self):
		doc = self.import_file(
			jmhz_form("5000000010", "9100000010", eldp="T++", prumerny_vydelek="187.125"),
			pair={"5000000010": self.a},
		)
		res = self.upload(doc, report_pdf([(self.reversed_name(self.name_a), slip_values())]))
		self.assertEqual((res["mismatch"], res["unread"]), ([], []))
		doc.reload()
		row = doc.rows[0]
		self.assertEqual((row.pdf_check, row.pdf_check_detail), (pdf_import.SHODA, None))
		extra = pdf_import.row_pdf_extra(row)
		self.assertEqual(pdf_import.row_pdf_unread(row), [])
		self.assertEqual(extra["dovolena_zustatek"], 72)
		self.assertEqual(extra["sleva_zakladni"], advance_tax(15353))
		self.assertEqual((extra["typ_pp"], extra["zpusob_vyplaty"]), ("DPP", "Převodem na účet"))
		self.assertEqual(len(extra), 65)

	def test_doplnkovy_udaj_nesedi_s_jmhz(self):
		doc = self.import_file(
			jmhz_form(
				"5000000011", "9100000011", eldp="A++", prumerny_vydelek="190", osvobozeno=100, cisty=13571
			),
			pair={"5000000011": self.a},
		)
		values = slip_values()
		extra = {"sleva_zakladni": values["dan"] - 10, "zamestnavatel_zp": 1}
		# the control fields still match, so the slip is assigned by its amounts
		pdf = report_pdf([(self.reversed_name(self.name_a), values | {"zaloha_na_dan": 10}, extra)])
		res = self.upload(doc, pdf)
		doc.reload()
		row = doc.rows[0]
		self.assertEqual(res["mismatch"], [row.pseudonym])
		self.assertEqual(row.pdf_check, pdf_import.NESHODA)
		self.assertEqual(
			row.pdf_check_detail,
			"Záloha na daň po slevě, Zdravotní pojištění zaměstnavatele, Sleva na poplatníka, "
			"Příjmy osvobozené od daně, Průměr pro náhrady (Kč/h), Typ pracovního vztahu",
		)
		self.assertFalse(any(ch.isdigit() for ch in row.pdf_check_detail))

	def test_vnitrni_soucet_blokuje_odeslani(self):
		doc = self.import_file(jmhz_form("5000000012", "9100000012"), pair={"5000000012": self.a})
		pdf = report_pdf([(self.reversed_name(self.name_a), slip_values(), {"dovolena_zustatek": 70})])
		self.upload(doc, pdf)
		doc.reload()
		row = doc.rows[0]
		self.assertEqual(row.pdf_check, pdf_import.NESHODA)
		self.assertEqual(row.pdf_check_detail, "součet: zůstatek dovolené")
		self.assertTrue(self.create(doc)["ok"])
		with self.assertRaises(frappe.ValidationError):
			importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		api.confirm_pdf(doc.name)
		self.assertTrue(importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))["ok"])

	def test_neprectene_pole(self):
		doc = self.import_file(jmhz_form("5000000013", "9100000013"), pair={"5000000013": self.a})
		texts = slip_texts(
			798.7, self.reversed_name(self.name_a), slip_values(), omit=("Výživné:", "Typ PP :")
		)
		res = self.upload(doc, build_pdf([texts]))
		doc.reload()
		row = doc.rows[0]
		self.assertEqual(res["unread"], [row.pseudonym])
		self.assertEqual(sorted(pdf_import.row_pdf_unread(row)), ["srazka_vyzivne", "typ_pp"])
		extra = json.loads(row.pdf_extra)
		self.assertNotIn("srazka_vyzivne", extra)  # unread is never stored as zero
		self.assertEqual(row.pdf_check, pdf_import.SHODA)  # what was read does fit

	def test_sestava_za_jine_obdobi(self):
		doc = self.import_file(jmhz_form("5000000014", "9100000014"), pair={"5000000014": self.a})
		files_before = frappe.db.count("File")
		pdf = report_pdf([(self.reversed_name(self.name_a), slip_values())], period=(3, 2026))
		with self.assertRaises(frappe.ValidationError):
			self.upload(doc, pdf)
		doc.reload()
		self.assertEqual(frappe.db.count("File"), files_before)
		self.assertFalse(doc.report_pdf or doc.rows[0].slip_pdf)
		# a slip without a readable period is not refused
		pdf = report_pdf([(self.reversed_name(self.name_a), slip_values())], period=None)
		self.assertEqual(self.upload(doc, pdf)["assigned"], 1)

	def test_rucni_prirazeni_prenese_celou_pasku(self):
		doc = self.two_person_import(same_amounts=True)
		texts = slip_texts(798.7, "Neznámý Člověk", slip_values(), omit=("Stravné:",))
		self.upload(doc, build_pdf([texts]))
		doc.reload()
		(pending,) = pdf_import._unassigned(doc)
		api.assign_pdf(doc.name, pending["file_url"], doc.rows[1].name)
		doc.reload()
		row = doc.rows[1]
		self.assertEqual(pdf_import.row_pdf_unread(row), ["stravne"])
		self.assertEqual(pdf_import.row_pdf_extra(row)["dovolena_zustatek"], 72)
		self.assertEqual(row.pdf_check, pdf_import.SHODA)

	def test_vyrez_ze_starsi_verze(self):
		"""A cut stored by a version that read only the 12 control fields keeps working."""
		doc = self.import_file(jmhz_form("5000000015", "9100000015"), pair={"5000000015": self.a})
		self.upload(doc, report_pdf([(self.reversed_name(self.name_a), slip_values(hours=41))]))
		api.confirm_pdf(doc.name)
		doc.reload()
		row = doc.rows[0]
		# make it look like an older row: no informative values on either side
		row.db_set({"pdf_extra": None, "pdf_unread": None, "jmhz_extra": None})
		stored = json.loads(row.pdf_fields)
		row.pdf_extra = None
		stored["confirmed"] = pdf_import._fingerprint(row)
		row.db_set("pdf_fields", json.dumps(stored))
		doc.reload()
		doc.save(ignore_permissions=True)
		row = doc.rows[0]
		self.assertEqual(row.pdf_check, pdf_import.POTVRZENO)  # the confirmation still holds
		self.assertEqual(row.pdf_check_detail, "Hodiny - odpr.")
		self.assertIsNone(pdf_import.row_pdf_extra(row))
