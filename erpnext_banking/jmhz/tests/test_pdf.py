"""Integration tests of printed payroll slips (SPEC §6.6). Synthetic data only.

bench --site <site> run-tests --module erpnext_banking.jmhz.tests.test_pdf
"""

import frappe

from erpnext_banking.jmhz import api, importer, pdf_import
from erpnext_banking.jmhz.tests.synthetic import form
from erpnext_banking.jmhz.tests.synthetic_pdf import report_pdf, slip_values
from erpnext_banking.jmhz.tests.test_importer import JmhzTestCase


def jmhz_form(ik, ppv, gross=15353, sp=1091, zp=691, hours="40.5", days=5):
	return form(
		ik=ik,
		ppv=ppv,
		zuct=gross,
		mzda=gross,
		sp=sp,
		zp=zp,
		cisty=gross - sp - zp,
		zaklad_dane=gross,
		vypoctena_dan=0,
		hodiny=hours,
		dny=days,
	)


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
		# only the two cuts are stored, private, the summary PDF is not
		self.assertEqual(len(files), 2)
		self.assertEqual(frappe.db.count("File"), files_before + 2)
		self.assertTrue(all(f.is_private for f in files))
		self.assertEqual({r.slip_pdf for r in doc.rows}, {f.file_url for f in files})
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
		self.assertEqual(len(self.pdf_files(doc)), 2)
		api.delete_pdfs(doc.name)
		doc.reload()
		self.assertEqual(self.pdf_files(doc), [])
		self.assertEqual({r.pdf_check for r in doc.rows}, {pdf_import.BEZ_PDF})
		self.assertFalse(doc.pdf_unassigned)

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
		(cut,) = self.pdf_files(doc)

		from erpnext_banking.jmhz.tests.test_importer import TestEmployeeAccess

		user = TestEmployeeAccess.make_user(self, self.a)
		frappe.set_user(user)
		file_doc = frappe.get_doc("File", cut.name)
		self.assertFalse(frappe.has_permission("File", "read", doc=file_doc))
		self.assertFalse(file_doc.is_downloadable())
		self.assertNotIn(cut.name, frappe.get_list("File", pluck="name"))
