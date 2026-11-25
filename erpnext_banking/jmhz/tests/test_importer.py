"""Integration tests of the JMHZ importer (SPEC §8.2, regular reports). Synthetic data only.

bench --site <site> run-tests --module erpnext_banking.jmhz.tests.test_importer
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_banking.jmhz import api, guards, importer, setup
from erpnext_banking.jmhz import components as C
from erpnext_banking.jmhz.parser import pseudonym
from erpnext_banking.jmhz.tests.synthetic import form, hpp, report

ROK, MESIC = 2026, 2
START, END = "2026-02-01", "2026-02-28"


class JmhzTestCase(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		setup.ensure_all(strict=True)
		self.company = setup.get_company()
		self._n = 0

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()

	def employee(self, **kw):
		self._n += 1
		values = {
			"doctype": "Employee",
			"first_name": "Test",
			"last_name": f"JMHZ {self._n}",
			"gender": "Female",
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2025-01-01",
			"company": self.company,
			"status": "Active",
		}
		values.update(kw)
		return frappe.get_doc(values).insert(ignore_permissions=True).name

	def fiscal_year(self, rok: int):
		"""HRMS refuses slips outside an active fiscal year of the company."""
		if not frappe.db.exists("Fiscal Year", str(rok)):
			frappe.get_doc(
				{
					"doctype": "Fiscal Year",
					"year": str(rok),
					"year_start_date": f"{rok}-01-01",
					"year_end_date": f"{rok}-12-31",
				}
			).insert(ignore_permissions=True)

	def import_file(self, *forms, ver="1.4", pair=None, rok=ROK, mesic=MESIC, **kw):
		"""Create an import; `pair` = {ik: employee}."""
		doc = importer.create_import(
			report(*forms, ver=ver, rok=rok, mesic=mesic, **kw), file_name="test.xml"
		)
		if pair:
			ik_to_pseudonym = {ik: pseudonym(ik) for ik in pair}
			for row in doc.rows:
				for ik, employee in pair.items():
					if row.pseudonym == ik_to_pseudonym[ik]:
						row.employee = employee
			doc.save(ignore_permissions=True)
		return doc

	def create(self, doc):
		res = importer.create_slips(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		return res

	def slip(self, doc, idx=0):
		return frappe.get_doc("Salary Slip", doc.rows[idx].salary_slip)

	@staticmethod
	def slip_lines(slip):
		return {d.salary_component: d.amount for d in slip.earnings + slip.deductions}

	@staticmethod
	def row_lines(row):
		return {li["component"]: li["amount"] for li in importer.row_lines(row)}


class TestRegularImport(JmhzTestCase):
	def test_vzor_mpsv_hpp(self):
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000001"), pair={"1000000001": emp})
		self.assertEqual(doc.status, importer.NAHLED)
		self.assertEqual(doc.rows[0].status, importer.R_PRIPRAVENO)
		self.assertEqual(doc.name[:13], "JMHZ-2026-02-")
		self.assertEqual(self.create(doc), {"ok": True, "created": 1})
		self.assertEqual(doc.status, importer.VYTVORENO)
		slip = self.slip(doc)
		self.assertEqual(slip.docstatus, 0)
		self.assertEqual(slip.net_pay, 13571)
		self.assertEqual(slip.gross_pay, 15353)
		self.assertEqual(self.slip_lines(slip), self.row_lines(doc.rows[0]))
		self.assertEqual(slip.salary_structure, setup.STRUCTURE_NAME)
		self.assertEqual(slip.jmhz_import, doc.name)
		self.assertEqual(frappe.db.get_value(importer.PERSON, emp, "ik_mpsv"), "1000000001")
		self.assertFalse(slip.payroll_entry)

		self.assertEqual(importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name)), {"ok": True})
		doc.reload()
		self.assertEqual(doc.status, importer.DOKONCENO)
		self.assertEqual(doc.rows[0].status, importer.R_ODESLANA)
		self.assertFalse(doc.rows[0].ik_mpsv)  # stays only in JMHZ Person
		self.assertEqual(frappe.db.get_value("Salary Slip", slip.name, "docstatus"), 1)
		# no accounting entries
		self.assertEqual(frappe.db.count("GL Entry", {"voucher_type": "Salary Slip"}), 0)
		self.assertEqual(frappe.db.count("Journal Entry", {"user_remark": ["like", f"%{slip.name}%"]}), 0)

	def test_bonus(self):
		emp = self.employee()
		f = form(ik="1000000002", zuct=30000, mzda=30000, sp=2130, zp=1350, bonus=1000, cisty=26520)
		doc = self.import_file(f, pair={"1000000002": emp})
		self.create(doc)
		slip = self.slip(doc)
		self.assertEqual(slip.gross_pay, 31000)  # 10286 + 10306
		self.assertEqual(slip.net_pay, 27520)  # 10344 + 10306
		self.assertEqual(self.slip_lines(slip)[C.DANOVY_BONUS.name], 1000)

	def test_srazkova_dan_obe_verze(self):
		lines = []
		self.fiscal_year(2027)
		for ver, ik, rok in (("1.4", "1000000003", 2026), ("1.5", "1000000004", 2027)):
			emp = self.employee()
			f = form(ver, ik=ik, zuct=10000, mzda=10000, srd=1500, cisty=8500)
			doc = self.import_file(f, ver=ver, pair={ik: emp}, rok=rok)
			self.assertEqual(doc.schema_version, ver)
			self.assertEqual(self.create(doc), {"ok": True, "created": 1})
			slip = self.slip(doc)
			self.assertEqual(slip.net_pay, 8500)
			lines.append(self.slip_lines(slip))
		self.assertEqual(lines[0], lines[1])
		self.assertEqual(lines[0][C.SRAZKOVA_DAN.name], 1500)

	def test_dve_ppv(self):
		emp = self.employee()
		primary = form(
			ik="1000000005", ppv="9000000001", zuct=14321, mzda=10000, sp=500, cisty=13821, hodiny=40
		)
		secondary = form(
			ik="1000000005",
			ppv="9000000002",
			primarni=False,
			souhrn=False,
			nahrady={"mzdyZuctovane": 4321, "dovolena": 4321},
			hodiny=10,
		)
		doc = self.import_file(primary, secondary, pair={"1000000005": emp})
		self.assertEqual(len(doc.rows), 1)
		self.create(doc)
		slip = self.slip(doc)
		self.assertEqual(slip.net_pay, 13821)
		self.assertEqual(slip.jmhz_id_ppv, "9000000001, 9000000002")
		self.assertEqual(slip.jmhz_odpracovane_hodiny, 50)
		for component in self.row_lines(doc.rows[0]):
			self.assertEqual(
				frappe.db.count(
					"Additional Salary",
					{"jmhz_import": doc.name, "employee": emp, "salary_component": component},
				),
				1,
			)

	def test_odchod_v_prubehu_mesice(self):
		emp = self.employee(relieving_date="2026-02-15")
		doc = self.import_file(hpp(ik="1000000006"), pair={"1000000006": emp})
		self.assertEqual(doc.rows[0].status, importer.R_PRIPRAVENO)
		self.create(doc)
		slip = self.slip(doc)
		self.assertEqual(str(slip.posting_date), "2026-02-15")
		dates = frappe.get_all("Additional Salary", {"jmhz_import": doc.name}, pluck="payroll_date")
		self.assertEqual({str(d) for d in dates}, {"2026-02-15"})
		self.assertEqual(slip.net_pay, 13571)

	def test_nastup_v_prubehu_mesice_a_neplacene_volno(self):
		emp = self.employee(date_of_joining="2026-02-10")
		leave_type = frappe.get_doc(
			{"doctype": "Leave Type", "leave_type_name": "Test JMHZ LWP", "is_lwp": 1}
		).insert(ignore_permissions=True)
		leave = frappe.get_doc(
			{
				"doctype": "Leave Application",
				"employee": emp,
				"leave_type": leave_type.name,
				"from_date": "2026-02-16",
				"to_date": "2026-02-18",
				"status": "Approved",
				"company": self.company,
			}
		)
		leave.insert(ignore_permissions=True)
		leave.submit()
		doc = self.import_file(hpp(ik="1000000007"), pair={"1000000007": emp})
		self.create(doc)
		slip = self.slip(doc)
		self.assertLess(slip.payment_days, slip.total_working_days)
		self.assertEqual(slip.net_pay, 13571)  # depends_on_payment_days = 0
		self.assertEqual(self.slip_lines(slip), self.row_lines(doc.rows[0]))
		ssa = frappe.get_all(
			"Salary Structure Assignment",
			{"employee": emp, "docstatus": 1},
			["from_date", "salary_structure"],
		)
		self.assertEqual(
			[(str(s.from_date), s.salary_structure) for s in ssa], [("2026-02-10", setup.STRUCTURE_NAME)]
		)

	def test_exekuce(self):
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000008", exekuce=True), pair={"1000000008": emp})
		self.assertEqual(doc.rows[0].status, importer.R_BLOKOVANO)
		self.assertFalse(importer.can_create_slips(doc))
		doc.rows[0].rucni_srazka = 500
		doc.rows[0].rucni_srazka_poznamka = "test"
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.rows[0].status, importer.R_PRIPRAVENO)
		self.create(doc)
		slip = self.slip(doc)
		self.assertEqual(self.slip_lines(slip)[C.EXEKUCE.name], 500)
		self.assertEqual(slip.net_pay, 13071)
		self.assertEqual(slip.jmhz_k_vyplate, 13071)

	def test_rucni_srazka_vyssi_nez_vyplata(self):
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000009"), pair={"1000000009": emp})
		doc.rows[0].rucni_srazka = 20000
		doc.rows[0].rucni_srazka_poznamka = "test"
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.rows[0].status, importer.R_BLOKOVANO)
		self.assertIn("I10", doc.rows[0].issues)

	def test_cizi_additional_salary(self):
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000010"), pair={"1000000010": emp})
		# a foreign payment in the same period (e.g. entered by hand)
		importer.ensure_structure_assignment(emp, self.company, "2025-01-01", START, "CZK")
		foreign = frappe.get_doc(
			{
				"doctype": "Additional Salary",
				"employee": emp,
				"company": self.company,
				"salary_component": C.PRIPLATKY.name,
				"type": "Earning",
				"amount": 777,
				"payroll_date": "2026-02-20",
				"currency": "CZK",
				"overwrite_salary_structure_amount": 0,
			}
		).insert(ignore_permissions=True)
		foreign.submit()
		res = self.create(doc)
		self.assertFalse(res["ok"])
		self.assertIn("I09", res["message"])
		self.assertNotIn("777", res["message"])
		self.assertEqual(doc.status, importer.NAHLED)
		self.assertIn("I09", doc.issues)
		self.assertEqual(frappe.db.count("Salary Slip", {"employee": emp}), 0)
		self.assertEqual(frappe.db.count("Additional Salary", {"jmhz_import": doc.name}), 0)

	def test_chyba_uprostred_vrati_vse(self):
		emps = [self.employee(), self.employee()]
		doc = self.import_file(
			hpp(ik="1000000011"), hpp(ik="1000000012"), pair={"1000000011": emps[0], "1000000012": emps[1]}
		)
		persons_before = frappe.db.count(importer.PERSON)
		original = importer.create_slip_for_row
		calls = []

		def failing(*args, **kwargs):
			calls.append(1)
			if len(calls) == 2:
				raise RuntimeError("simulated failure")
			return original(*args, **kwargs)

		with patch.object(importer, "create_slip_for_row", side_effect=failing):
			res = self.create(doc)
		self.assertFalse(res["ok"])
		self.assertEqual(len(calls), 2)
		self.assertEqual(doc.status, importer.NAHLED)
		self.assertEqual(frappe.db.count("Salary Slip", {"employee": ["in", emps]}), 0)
		self.assertEqual(frappe.db.count("Additional Salary", {"jmhz_import": doc.name}), 0)
		self.assertEqual(frappe.db.count("Salary Structure Assignment", {"employee": ["in", emps]}), 0)
		self.assertEqual(frappe.db.count(importer.PERSON), persons_before)

	def test_zmena_additional_salary_pred_odeslanim(self):
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000013"), pair={"1000000013": emp})
		self.create(doc)
		add = frappe.get_doc(
			"Additional Salary",
			{"jmhz_import": doc.name, "salary_component": C.ZDRAVOTNI_POJISTENI.name},
		)
		add.cancel()
		with self.assertRaises(frappe.ValidationError):
			importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		self.assertEqual(frappe.db.get_value("Salary Slip", doc.rows[0].salary_slip, "docstatus"), 0)

	def test_bez_prijmu_a_preskoceni(self):
		emp = self.employee()
		doc = self.import_file(
			hpp(ik="1000000014"), form(ik="1000000015"), hpp(ik="1000000016"), pair={"1000000014": emp}
		)
		statuses = [r.status for r in doc.rows]
		self.assertEqual(statuses, [importer.R_PRIPRAVENO, importer.R_BEZ_PRIJMU, importer.R_NENAPAROVANO])
		self.assertFalse(importer.can_create_slips(doc))
		doc.rows[2].skip = 1
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)  # reason is mandatory
		doc.reload()
		doc.rows[2].skip = 1
		doc.rows[2].skip_reason = "není zaměstnanec"
		doc.save(ignore_permissions=True)
		self.assertEqual(doc.rows[2].status, importer.R_PRESKOCENO)
		self.assertTrue(self.create(doc)["ok"])
		self.assertEqual(doc.created_slips, 1)
		importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		self.assertTrue(doc.rows[2].ik_mpsv)  # skipped rows keep the identifier
		self.assertFalse(doc.rows[0].ik_mpsv)

	def test_kontroly_zamestnance(self):
		other_company = frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": "Test JMHZ jiná",
				"abbr": "TJJ",
				"default_currency": "CZK",
				"country": "Czech Republic",
			}
		).insert(ignore_permissions=True)
		cases = {
			"I02": self.employee(status="Inactive", relieving_date="2026-03-31"),
			"I04": self.employee(relieving_date="2026-01-20"),
			"I05": self.employee(date_of_joining="2026-03-02"),
			"I06": self.employee(company=other_company.name),
		}
		forms, pair = [], {}
		for i, (_code, emp) in enumerate(cases.items()):
			ik = f"20000000{i:02d}"
			forms.append(hpp(ik=ik, ppv=f"80000000{i:02d}"))
			pair[ik] = emp
		doc = self.import_file(*forms, pair=pair)
		for row, code in zip(doc.rows, cases):
			self.assertEqual(row.status, importer.R_BLOKOVANO, code)
			self.assertIn(code, row.issues)

	def test_existujici_paska_za_obdobi(self):
		# a slip created outside JMHZ blocks the person (I07); JMHZ slips are replaced instead (M4)
		emp = self.employee()
		importer.ensure_structure_assignment(emp, self.company, "2025-01-01", START, "CZK")
		manual = frappe.get_doc(
			{
				"doctype": "Salary Slip",
				"employee": emp,
				"company": self.company,
				"start_date": START,
				"end_date": END,
				"posting_date": END,
				"payroll_frequency": "Monthly",
			}
		).insert(ignore_permissions=True)
		self.assertFalse(manual.jmhz_import)
		doc = self.import_file(hpp(ik="1000000017"), pair={"1000000017": emp})
		self.assertEqual(doc.rows[0].status, importer.R_BLOKOVANO)
		self.assertIn("I07", doc.rows[0].issues)

	def test_parovani(self):
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000018"), hpp(ik="1000000019"))
		doc.rows[0].employee = emp
		doc.rows[1].employee = emp
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)
		# employee already paired with another IK MPSV
		frappe.get_doc({"doctype": importer.PERSON, "employee": emp, "ik_mpsv": "1000000099"}).insert(
			ignore_permissions=True
		)
		doc.reload()
		doc.rows[0].employee = emp
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)

	def test_automaticke_parovani(self):
		emp = self.employee()
		frappe.get_doc({"doctype": importer.PERSON, "employee": emp, "ik_mpsv": "1000000020"}).insert(
			ignore_permissions=True
		)
		doc = self.import_file(hpp(ik="1000000020"))
		self.assertEqual(doc.rows[0].employee, emp)
		self.assertEqual(doc.rows[0].status, importer.R_PRIPRAVENO)

	def test_blokace_parseru(self):
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000021", cisty=1), pair={"1000000021": emp})
		self.assertEqual(doc.rows[0].status, importer.R_BLOKOVANO)
		self.assertIn("P01", doc.rows[0].issues)

	def test_import_jen_ke_cteni(self):
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000022"), pair={"1000000022": emp})
		self.create(doc)
		fresh = frappe.get_doc(importer.IMPORT, doc.name)
		fresh.rows[0].rucni_srazka = 1
		with self.assertRaises(frappe.ValidationError):
			fresh.save(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc(importer.IMPORT, doc.name, ignore_permissions=True)

	def test_fatalni_chyba_nevytvori_import(self):
		before = frappe.db.count(importer.IMPORT)
		with self.assertRaises(frappe.ValidationError):
			importer.create_import(b"<x/>", file_name="x.xml")
		self.assertEqual(frappe.db.count(importer.IMPORT), before)


class TestUpload(JmhzTestCase):
	def test_upload_nevytvori_file(self):
		files_before = frappe.db.count("File")
		frappe.local.uploaded_file = report(hpp(ik="1000000030"), rok=ROK, mesic=MESIC)
		frappe.local.uploaded_filename = "jmhz.xml"
		try:
			res = api.upload_xml()
		finally:
			frappe.local.uploaded_file = None
			frappe.local.uploaded_filename = None
		self.assertTrue(frappe.db.exists(importer.IMPORT, res["name"]))
		self.assertEqual(frappe.db.count("File"), files_before)

	def test_upload_odmitne_jinou_priponu(self):
		frappe.local.uploaded_file = b"<x/>"
		frappe.local.uploaded_filename = "jmhz.txt"
		try:
			with self.assertRaises(frappe.ValidationError):
				api.upload_xml()
		finally:
			frappe.local.uploaded_file = None


class TestGuards(JmhzTestCase):
	def test_rucni_paska(self):
		doc = frappe._dict(name="X", jmhz_import=None, net_pay=100, gross_pay=100)
		with self.assertRaises(frappe.ValidationError):
			guards.salary_slip_before_submit(doc)
		frappe.db.set_single_value(setup.SETTINGS, "allow_slips_outside_import", 1)
		guards.salary_slip_before_submit(doc)

	def test_payroll_entry(self):
		self.assertIn(
			"erpnext_banking.jmhz.guards.block_payroll_entry",
			frappe.get_hooks("doc_events")["Payroll Entry"]["validate"],
		)
		with self.assertRaises(frappe.ValidationError):
			guards.block_payroll_entry(frappe._dict())
		frappe.db.set_single_value(setup.SETTINGS, "allow_payroll_entry", 1)
		guards.block_payroll_entry(frappe._dict())

	def test_paska_mimo_stav_importu(self):
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000031"), pair={"1000000031": emp})
		self.create(doc)
		frappe.db.set_value(importer.IMPORT, doc.name, "status", importer.ZRUSENO)
		slip = self.slip(doc)
		slip.flags.jmhz_internal = True  # as if from the importer, so that the status check is reached
		with self.assertRaises(frappe.ValidationError):
			slip.submit()

	def test_paska_jen_z_importu(self):
		"""A JMHZ slip is submitted and cancelled only by the importer (bulk submit in the list)."""
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000032"), pair={"1000000032": emp})
		self.create(doc)
		with self.assertRaises(frappe.ValidationError):
			self.slip(doc).submit()
		self.assertEqual(frappe.db.get_value("Salary Slip", doc.rows[0].salary_slip, "docstatus"), 0)

		importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		slip = self.slip(doc)
		self.assertEqual(slip.docstatus, 1)
		with self.assertRaises(frappe.ValidationError):
			slip.cancel()
		self.assertEqual(frappe.db.get_value("Salary Slip", slip.name, "docstatus"), 1)


class TestErrorMessages(JmhzTestCase):
	"""A failed "Vytvořit pásky": expected errors are shown, not logged; foreign texts never pass."""

	def run_failing(self, error):
		emp = self.employee()
		doc = self.import_file(hpp(ik="1000000063"), pair={"1000000063": emp})
		before = frappe.db.count("Error Log", {"method": "JMHZ: vytvoření pásek"})
		with patch.object(importer, "create_slip_for_row", side_effect=error):
			res = self.create(doc)
		self.assertFalse(res["ok"])
		logs = frappe.get_all(
			"Error Log",
			filters={"method": "JMHZ: vytvoření pásek"},
			fields=["error"],
			order_by="creation desc",
		)
		return res["message"], len(logs) - before, logs

	def test_ocekavana_chyba_se_neloguje(self):
		message, logged, _ = self.run_failing(frappe.ValidationError("Chybí fiskální rok"))
		self.assertIn("Chybí fiskální rok", message)
		self.assertEqual(logged, 0)

	def test_unikatni_klic_neprozradi_hodnotu(self):
		secret = "1000000099"
		error = frappe.UniqueValidationError(f"Duplicate entry '{secret}' for key 'ik_mpsv'")
		message, logged, logs = self.run_failing(error)
		self.assertNotIn(secret, message)
		self.assertEqual(logged, 1)
		self.assertNotIn(secret, logs[0].error)

	def test_neocekavana_chyba_bez_textu(self):
		message, logged, _ = self.run_failing(RuntimeError("detail cizí výjimky"))
		self.assertNotIn("detail cizí výjimky", message)
		self.assertIn("RuntimeError", message)
		self.assertEqual(logged, 1)

	def test_import_jen_nahranim_souboru(self):
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{
					"doctype": importer.IMPORT,
					"company": self.company,
					"rok": ROK,
					"mesic": MESIC,
					"typ_podani": "R",
					"status": importer.VYTVORENO,
				}
			).insert()


class TestDeletedDocument(JmhzTestCase):
	"""Deleted Document is readable by System Manager: no IK MPSV and no amounts may end up there."""

	def deleted(self, doctype, name) -> str:
		data = frappe.db.get_value(
			"Deleted Document", {"deleted_doctype": doctype, "deleted_name": name}, "data"
		)
		self.assertTrue(data)
		return data

	def test_smazany_import(self):
		ik = "1000000060"
		doc = self.import_file(hpp(ik=ik))
		frappe.delete_doc(importer.IMPORT, doc.name)
		data = self.deleted(importer.IMPORT, doc.name)
		self.assertNotIn(ik, data)
		self.assertEqual(json.loads(data)["rows"], [])

	def test_smazana_paska_a_parovani(self):
		ik, emp = "1000000061", self.employee()
		doc = self.import_file(hpp(ik=ik), pair={ik: emp})
		self.create(doc)
		slip = doc.rows[0].salary_slip
		frappe.delete_doc("Salary Slip", slip, force=True)
		data = json.loads(self.deleted("Salary Slip", slip))
		self.assertEqual((data["earnings"], data["deductions"]), ([], []))
		self.assertFalse(data.get("net_pay") or data.get("gross_pay") or data.get("jmhz_k_vyplate"))
		self.assertEqual(data["employee"], emp)

		frappe.delete_doc(importer.PERSON, emp)
		self.assertNotIn(ik, self.deleted(importer.PERSON, emp))


class TestEmployeeAccess(JmhzTestCase):
	def make_user(self, employee):
		email = f"jmhz-test-{employee.lower()}@example.com"
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Test", "send_welcome_email": 0}
		)
		user.insert(ignore_permissions=True)
		# ERPNext removes the Employee role from users not linked to an Employee
		emp = frappe.get_doc("Employee", employee)
		emp.user_id = user.name
		emp.save(ignore_permissions=True)
		user.reload()
		user.add_roles("Employee")
		# linking the Employee creates the User Permission (standard ERPNext)
		self.assertTrue(
			frappe.db.exists(
				"User Permission", {"user": user.name, "allow": "Employee", "for_value": employee}
			)
		)
		self.assertIn("Employee", frappe.get_roles(user.name))
		return user.name

	def test_zamestnanec_vidi_jen_sve_odeslane_pasky(self):
		emp_a, emp_b = self.employee(), self.employee()
		user = self.make_user(emp_a)
		doc = self.import_file(
			hpp(ik="1000000040"), hpp(ik="1000000041"), pair={"1000000040": emp_a, "1000000041": emp_b}
		)
		self.create(doc)
		slip_a, slip_b = doc.rows[0].salary_slip, doc.rows[1].salary_slip

		frappe.set_user(user)
		self.assertFalse(frappe.has_permission("Salary Slip", "read", slip_a))  # draft
		self.assertEqual(frappe.get_list("Salary Slip", pluck="name"), [])
		frappe.set_user("Administrator")

		importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))

		frappe.set_user(user)
		self.assertTrue(frappe.has_permission("Salary Slip", "read", slip_a))
		self.assertFalse(frappe.has_permission("Salary Slip", "read", slip_b))
		self.assertEqual(frappe.get_list("Salary Slip", pluck="name"), [slip_a])
		self.assertFalse(frappe.has_permission(importer.IMPORT, "read"))
		self.assertFalse(frappe.has_permission(importer.PERSON, "read"))
		self.assertFalse(frappe.has_permission(setup.SETTINGS, "read"))


class TestNoSystemManagerAccess(JmhzTestCase):
	def test_system_manager_bez_hr_role(self):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "jmhz-sysman@example.com",
				"first_name": "Sys",
				"send_welcome_email": 0,
				"roles": [{"role": "System Manager"}],
			}
		).insert(ignore_permissions=True)
		frappe.set_user(user.name)
		self.assertFalse(frappe.has_permission(importer.IMPORT, "read"))
		self.assertFalse(frappe.has_permission(importer.PERSON, "read"))
