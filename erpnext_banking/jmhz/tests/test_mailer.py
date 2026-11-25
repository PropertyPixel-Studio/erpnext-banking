"""Integration tests of sending slips by e-mail (SPEC §6.7). Synthetic data only.

bench --site <site> run-tests --module erpnext_banking.jmhz.tests.test_mailer
"""

import frappe

from erpnext_banking.jmhz import importer, mailer, setup
from erpnext_banking.jmhz.tests.synthetic import hpp
from erpnext_banking.jmhz.tests.test_importer import JmhzTestCase


class TestMailer(JmhzTestCase):
	def setUp(self):
		super().setUp()
		# only example.com addresses, nothing leaves the test site
		self.with_email = [self.employee_with_email(f"jmhz-mail-{i}@example.com") for i in range(2)]
		self.without_email = self.employee()
		self.outgoing_account()

	def outgoing_account(self):
		"""Tests only queue e-mails (frappe.flags.in_test), nothing is sent."""
		if frappe.db.exists("Email Account", {"default_outgoing": 1}):
			return
		frappe.get_doc(
			{
				"doctype": "Email Account",
				"email_account_name": "JMHZ Test Outgoing",
				"email_id": "jmhz-sender@example.com",
				"enable_outgoing": 1,
				"default_outgoing": 1,
				"smtp_server": "localhost",
				"no_smtp_authentication": 1,
			}
		).insert(ignore_permissions=True)

	def employee_with_email(self, email):
		return self.employee(company_email=email, prefered_contact_email="Company Email")

	def set_mode(self, mode, testers=()):
		settings = frappe.get_single(setup.SETTINGS)
		settings.email_mode = mode
		settings.set("email_test_employees", [{"employee": e} for e in testers])
		settings.save(ignore_permissions=True)

	def mails_to(self, email):
		return frappe.db.count("Email Queue Recipient", {"recipient": email})

	def run_import(self):
		emps = [*self.with_email, self.without_email]
		iks = [f"400000000{i}" for i in range(len(emps))]
		forms = [hpp(ik=ik, ppv=f"900000010{i}") for i, ik in enumerate(iks)]
		doc = self.import_file(*forms, pair=dict(zip(iks, emps)))
		self.assertTrue(self.create(doc)["ok"])
		res = importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		return doc, res

	def test_vypnuto(self):
		self.set_mode(mailer.MODE_OFF)
		doc, res = self.run_import()
		self.assertEqual(res["email"]["queued"], 0)
		self.assertEqual({r.email_status for r in doc.rows}, {mailer.NOT_SENT})
		for i in range(2):
			self.assertEqual(self.mails_to(f"jmhz-mail-{i}@example.com"), 0)

	def test_jen_testovaci_zamestnanci(self):
		self.set_mode(mailer.MODE_TEST, testers=[self.with_email[0]])
		doc, res = self.run_import()
		self.assertEqual(res["email"]["queued"], 1)
		self.assertEqual([r.email_status for r in doc.rows], [mailer.QUEUED, mailer.SKIPPED, mailer.SKIPPED])
		self.assertEqual(self.mails_to("jmhz-mail-0@example.com"), 1)
		self.assertEqual(self.mails_to("jmhz-mail-1@example.com"), 0)
		queue = frappe.db.get_value(
			"Email Queue Recipient", {"recipient": "jmhz-mail-0@example.com"}, "parent"
		)
		mail = frappe.get_doc("Email Queue", queue)
		self.assertEqual(mail.reference_doctype, "Salary Slip")
		self.assertEqual(mail.reference_name, doc.rows[0].salary_slip)
		# the slip PDF from ERPNext is embedded in the MIME message
		self.assertIn("application/pdf", mail.message)
		self.assertIn("attachment", mail.message)

	def test_vsem(self):
		self.set_mode(mailer.MODE_ALL)
		doc, res = self.run_import()
		self.assertEqual(res["email"]["queued"], 2)
		self.assertEqual([r.email_status for r in doc.rows], [mailer.QUEUED, mailer.QUEUED, mailer.NO_EMAIL])
		for i in range(2):
			self.assertEqual(self.mails_to(f"jmhz-mail-{i}@example.com"), 1)

	def test_vestaveny_email_hrms_zustava_vypnuty(self):
		frappe.db.set_single_value("Payroll Settings", "email_salary_slip_to_employee", 1)
		setup.ensure_all(strict=True)
		self.assertEqual(frappe.db.get_single_value("Payroll Settings", "email_salary_slip_to_employee"), 0)
		self.set_mode(mailer.MODE_OFF)
		self.run_import()
		self.assertEqual(self.mails_to("jmhz-mail-0@example.com"), 0)

	def test_tlacitko_na_pasce(self):
		self.set_mode(mailer.MODE_OFF)
		doc, _res = self.run_import()
		slip_tester, slip_other = doc.rows[0].salary_slip, doc.rows[1].salary_slip
		with self.assertRaises(frappe.ValidationError):
			mailer.send_slip(slip_tester)
		self.set_mode(mailer.MODE_TEST, testers=[self.with_email[0]])
		with self.assertRaises(frappe.ValidationError):
			mailer.send_slip(slip_other)
		self.assertEqual(mailer.send_slip(slip_tester), {"status": mailer.QUEUED})
		self.assertEqual(self.mails_to("jmhz-mail-0@example.com"), 1)
		self.set_mode(mailer.MODE_ALL)
		with self.assertRaises(frappe.ValidationError):
			mailer.send_slip(doc.rows[2].salary_slip)  # no e-mail address

	def test_zruseni_neposila(self):
		self.set_mode(mailer.MODE_ALL)
		doc, _res = self.run_import()
		before = self.mails_to("jmhz-mail-0@example.com")
		importer.cancel_import(frappe.get_doc(importer.IMPORT, doc.name))
		self.assertEqual(self.mails_to("jmhz-mail-0@example.com"), before)

	def test_chyba_emailu_nerozbije_odeslani_pasek(self):
		frappe.db.set_value("Email Account", {"default_outgoing": 1}, "default_outgoing", 0)
		self.set_mode(mailer.MODE_ALL)
		doc, res = self.run_import()
		self.assertEqual(doc.status, importer.DOKONCENO)
		self.assertEqual(res["email"]["queued"], 0)
		self.assertEqual(doc.rows[0].email_status, mailer.NOT_SENT)
		self.assertEqual(frappe.db.get_value("Salary Slip", doc.rows[0].salary_slip, "docstatus"), 1)

	def test_hromadna_akce_hrms_respektuje_rezim(self):
		"""The list action "Email Salary Slips" goes through the same mode check."""
		hrms_method = "hrms.payroll.doctype.salary_slip.salary_slip.enqueue_email_salary_slips"
		bulk = frappe.get_attr(frappe.override_whitelisted_method(hrms_method))
		self.assertIs(bulk, mailer.enqueue_email_salary_slips)

		self.set_mode(mailer.MODE_OFF)
		doc, _ = self.run_import()
		slips = [r.salary_slip for r in doc.rows]
		with self.assertRaises(frappe.ValidationError):
			bulk(names=frappe.as_json(slips))
		self.assertEqual(self.mails_to("jmhz-mail-0@example.com"), 0)

		self.set_mode(mailer.MODE_TEST, testers=[self.with_email[0]])
		bulk(names=slips)
		self.assertEqual(self.mails_to("jmhz-mail-0@example.com"), 1)
		self.assertEqual(self.mails_to("jmhz-mail-1@example.com"), 0)

	def test_hromadna_akce_neposle_draft(self):
		self.set_mode(mailer.MODE_ALL)
		emp = self.with_email[0]
		doc = self.import_file(hpp(ik="4000000009", ppv="9000000109"), pair={"4000000009": emp})
		self.assertTrue(self.create(doc)["ok"])
		mailer.enqueue_email_salary_slips(names=[doc.rows[0].salary_slip])
		self.assertEqual(self.mails_to("jmhz-mail-0@example.com"), 0)
