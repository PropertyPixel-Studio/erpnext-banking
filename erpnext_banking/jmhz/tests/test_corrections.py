"""Integration tests: corrective and cancelling reports, cancelling an import, re-upload.

bench --site <site> run-tests --module erpnext_banking.jmhz.tests.test_corrections
"""

import frappe

from erpnext_banking.jmhz import importer
from erpnext_banking.jmhz.tests.synthetic import form, hpp, report
from erpnext_banking.jmhz.tests.test_importer import MESIC, ROK, JmhzTestCase


class TestCorrections(JmhzTestCase):
	def done_import(self, *forms, pair, **kw):
		"""Regular import, slips created and submitted."""
		doc = self.import_file(*forms, pair=pair, **kw)
		self.assertTrue(self.create(doc)["ok"])
		importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		self.assertEqual(doc.status, importer.DOKONCENO)
		return doc

	def live_slips(self, employee):
		return frappe.get_all(
			"Salary Slip", {"employee": employee, "docstatus": ["!=", 2]}, ["name", "jmhz_import", "net_pay"]
		)

	def live_additional_salaries(self, import_name):
		return frappe.db.count("Additional Salary", {"jmhz_import": import_name, "docstatus": 1})

	def test_stejny_soubor_dvakrat(self):
		emp = self.employee()
		self.done_import(hpp(ik="3000000001"), pair={"3000000001": emp})
		with self.assertRaises(frappe.ValidationError):
			self.import_file(hpp(ik="3000000001"))

	def test_stejny_soubor_po_preskoceni(self):
		a, b = self.employee(), self.employee()
		file_forms = (hpp(ik="3000000002"), hpp(ik="3000000003", ppv="9000000003"))
		first = self.import_file(*file_forms, pair={"3000000002": a})
		first.rows[1].skip = 1
		first.rows[1].skip_reason = "druhá vlna"
		first.save(ignore_permissions=True)
		self.create(first)
		importer.submit_slips(frappe.get_doc(importer.IMPORT, first.name))

		second = self.import_file(*file_forms, pair={"3000000003": b})
		self.assertFalse(second.replaces)
		self.assertEqual(second.rows[0].status, importer.R_JIZ_IMPORTOVANO)
		self.assertEqual(second.rows[1].status, importer.R_PRIPRAVENO)
		self.assertTrue(self.create(second)["ok"])
		self.assertEqual(second.created_slips, 1)
		self.assertEqual(len(self.live_slips(a)), 1)
		self.assertEqual(self.live_slips(b)[0].jmhz_import, second.name)

	def test_opravne_podani_jedne_ze_dvou_osob(self):
		a, b = self.employee(), self.employee()
		original = self.done_import(
			hpp(ik="3000000004"),
			hpp(ik="3000000005", ppv="9000000005"),
			pair={"3000000004": a, "3000000005": b},
		)
		slip_b = self.live_slips(b)[0].name

		# corrective report: only person A, higher wage
		fixed = form(ik="3000000004", typ="O", zuct=20000, mzda=20000, sp=1420, zp=900, cisty=17680)
		corr = self.import_file(fixed, typ_podani="O")
		self.assertEqual(corr.typ_podani, "O")
		self.assertEqual(corr.replaces, original.name)
		self.assertEqual(corr.rows[0].employee, a)  # paired automatically through JMHZ Person
		self.assertEqual(corr.rows[0].status, importer.R_PRIPRAVENO)
		self.assertTrue(self.create(corr)["ok"])

		(slip_a,) = self.live_slips(a)
		self.assertEqual(slip_a.jmhz_import, corr.name)
		self.assertEqual(slip_a.net_pay, 17680)
		self.assertEqual([s.name for s in self.live_slips(b)], [slip_b])  # unchanged
		original.reload()
		self.assertEqual(original.status, importer.DOKONCENO)  # person B still lives there
		self.assertEqual([r.status for r in original.rows], [importer.R_ZRUSENO, importer.R_ODESLANA])
		self.assertEqual(
			frappe.db.count(
				"Additional Salary", {"jmhz_import": original.name, "employee": a, "docstatus": 1}
			),
			0,
		)
		importer.submit_slips(frappe.get_doc(importer.IMPORT, corr.name))

		# second corrective report for B too -> the original import is fully replaced
		corr2 = self.import_file(
			form(ik="3000000005", ppv="9000000005", typ="O", zuct=1000, mzda=1000, cisty=1000), typ_podani="O"
		)
		self.assertTrue(self.create(corr2)["ok"])
		original.reload()
		self.assertEqual(original.status, importer.NAHRAZENO)

	def test_radne_podani_z_jineho_souboru_vyzaduje_potvrzeni(self):
		a = self.employee()
		original = self.done_import(hpp(ik="3000000006"), pair={"3000000006": a})
		other = self.import_file(hpp(ik="3000000006", ppv="9000000099", cisty=13571, zuct=15353))
		self.assertEqual(other.replaces, original.name)
		self.assertEqual(other.rows[0].status, importer.R_PRIPRAVENO)
		self.assertFalse(importer.can_create_slips(other))
		other.replace_confirmed = 1
		other.save(ignore_permissions=True)
		self.assertTrue(importer.can_create_slips(other))
		self.assertTrue(self.create(other)["ok"])
		(slip,) = self.live_slips(a)
		self.assertEqual(slip.jmhz_import, other.name)
		original.reload()
		self.assertEqual(original.status, importer.NAHRAZENO)

	def test_opravne_bez_ziveho_importu(self):
		a = self.employee()
		doc = self.import_file(hpp(ik="3000000007", typ="O"), typ_podani="O", pair={"3000000007": a})
		self.assertFalse(doc.replaces)
		self.assertIn("zpracuje jako řádné", doc.issues)
		self.assertTrue(self.create(doc)["ok"])

	def test_opravne_s_mene_ppv(self):
		a = self.employee()
		primary = form(ik="3000000008", ppv="9000000011", zuct=14321, mzda=10000, cisty=14321)
		secondary = form(ik="3000000008", ppv="9000000012", primarni=False, souhrn=False, mzda=4321)
		self.done_import(primary, secondary, pair={"3000000008": a})
		corr = self.import_file(
			form(ik="3000000008", ppv="9000000011", typ="O", zuct=10000, mzda=10000, cisty=10000),
			typ_podani="O",
		)
		self.assertEqual(corr.rows[0].status, importer.R_BLOKOVANO)
		self.assertIn("I11", corr.rows[0].issues)

	def test_storno_formulare_v_opravnem_podani(self):
		a = self.employee()
		self.done_import(hpp(ik="3000000009"), pair={"3000000009": a})
		corr = self.import_file(hpp(ik="3000000009", typ="S"), typ_podani="O")
		self.assertEqual(corr.rows[0].status, importer.R_BLOKOVANO)
		self.assertIn("P13", corr.rows[0].issues)

	def test_storno_podani(self):
		a, b = self.employee(), self.employee()
		original = self.done_import(
			hpp(ik="3000000010"),
			hpp(ik="3000000011", ppv="9000000021"),
			pair={"3000000010": a, "3000000011": b},
		)
		storno = importer.create_import(report(typ_podani="S", rok=ROK, mesic=MESIC), file_name="s.xml")
		self.assertEqual(storno.rows, [])
		self.assertEqual(storno.replaces, original.name)
		self.assertFalse(importer.can_create_slips(storno))
		res = importer.run_storno(frappe.get_doc(importer.IMPORT, storno.name))
		self.assertEqual(res["imports"], [original.name])
		self.assertEqual(self.live_slips(a) + self.live_slips(b), [])
		self.assertEqual(self.live_additional_salaries(original.name), 0)
		original.reload()
		storno.reload()
		self.assertEqual(original.status, importer.ZRUSENO)
		self.assertEqual(storno.status, importer.DOKONCENO)
		self.assertEqual({r.status for r in original.rows}, {importer.R_ZRUSENO})

	def test_storno_bez_ziveho_importu(self):
		storno = importer.create_import(report(typ_podani="S", rok=ROK, mesic=MESIC), file_name="s.xml")
		self.assertIn("jen zaznamená", storno.issues)
		self.assertEqual(importer.run_storno(frappe.get_doc(importer.IMPORT, storno.name))["imports"], [])

	def test_zrusit_import_po_odeslani(self):
		a = self.employee()
		doc = self.done_import(hpp(ik="3000000012"), pair={"3000000012": a})
		slip = doc.rows[0].salary_slip
		importer.cancel_import(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		self.assertEqual(doc.status, importer.ZRUSENO)
		self.assertEqual(doc.rows[0].status, importer.R_ZRUSENO)
		self.assertEqual(frappe.db.get_value("Salary Slip", slip, "docstatus"), 2)
		self.assertEqual(self.live_additional_salaries(doc.name), 0)
		# the same file can be imported again afterwards
		again = self.import_file(hpp(ik="3000000012"))
		self.assertEqual(again.rows[0].status, importer.R_PRIPRAVENO)
		self.assertTrue(self.create(again)["ok"])
		# the cancelled import stays for audit: cancelled documents still link it
		with self.assertRaises(frappe.LinkExistsError):
			frappe.delete_doc(importer.IMPORT, doc.name, ignore_permissions=True)

	def test_zrusit_import_s_drafty(self):
		a = self.employee()
		doc = self.import_file(hpp(ik="3000000013"), pair={"3000000013": a})
		self.create(doc)
		slip = doc.rows[0].salary_slip
		importer.cancel_import(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		self.assertFalse(frappe.db.exists("Salary Slip", slip))  # drafts are deleted
		self.assertFalse(doc.rows[0].salary_slip)
		self.assertEqual(doc.status, importer.ZRUSENO)
		self.assertEqual(self.live_additional_salaries(doc.name), 0)

	def test_zrusit_import_v_nahledu(self):
		doc = self.import_file(hpp(ik="3000000014"))
		importer.cancel_import(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		self.assertEqual(doc.status, importer.ZRUSENO)
		with self.assertRaises(frappe.ValidationError):
			importer.cancel_import(frappe.get_doc(importer.IMPORT, doc.name))
