"""Integration tests of matching salary payments to Salary Slips. Synthetic data only.

bench --site <site> run-tests --module erpnext_banking.jmhz.tests.test_salary_payments
"""

from unittest.mock import patch

import frappe

from erpnext_banking import reconcile, salary
from erpnext_banking.jmhz import api, importer
from erpnext_banking.jmhz.tests.synthetic_pdf import report_pdf, slip_values
from erpnext_banking.jmhz.tests.test_importer import JmhzTestCase
from erpnext_banking.jmhz.tests.test_pdf import jmhz_form

ACCOUNT = "1000000001/0100"
NET = 13571  # net pay of the synthetic slip
SALARY_ACCOUNT = "521 - Mzdové náklady - PXG"
BANK_GL = "221 - Bankovní účty CZK - PXG"


class SalaryPaymentCase(JmhzTestCase):
	"""A company bank account, an employee with an account, and helpers. No tests here."""

	def setUp(self):
		super().setUp()
		# The banking code commits after every booked transaction, on purpose. A test must
		# not: everything it creates is rolled back in tearDown.
		commit = patch.object(frappe.db, "commit")
		commit.start()
		self.addCleanup(commit.stop)
		self.fiscal_year(2026)
		if not frappe.db.exists("Bank", "Testovací banka"):
			frappe.get_doc({"doctype": "Bank", "bank_name": "Testovací banka"}).insert(
				ignore_permissions=True
			)
		# the test company has the account, but not typed as a bank account
		frappe.db.set_value("Account", BANK_GL, "account_type", "Bank")
		self.bank_account = frappe.get_doc(
			{
				"doctype": "Bank Account",
				"account_name": "Test běžný účet",
				"bank": "Testovací banka",
				"is_company_account": 1,
				"company": self.company,
				"account": BANK_GL,
			}
		).insert(ignore_permissions=True)
		self.settings = frappe._dict(
			company=self.company,
			bank_account=self.bank_account.name,
			auto_reconcile_outgoing=1,
			auto_reconcile_salaries=1,
			auto_journal_entries=1,
			salary_account=SALARY_ACCOUNT,
			je_contra_account=BANK_GL,
			default_cost_center="Main - PXG",
			reconcile_window_days=90,
		)
		self.emp = self.employee(bank_ac_no=ACCOUNT)

	def slip(self, ik="6000000001", emp=None, pdf_extra=None, submit=True):
		"""An import with one person and its slip (period 02/2026)."""
		emp = emp or self.emp
		doc = self.import_file(jmhz_form(ik, "92" + ik[2:]), pair={ik: emp})
		if pdf_extra is not None:
			name = " ".join(reversed(frappe.db.get_value("Employee", emp, "employee_name").split()))
			pdf = report_pdf([(name, slip_values(), pdf_extra)])
			frappe.local.uploaded_file, frappe.local.uploaded_filename = pdf, "sestava.pdf"
			frappe.form_dict.docname = doc.name
			try:
				api.upload_pdf()
			finally:
				frappe.local.uploaded_file = frappe.local.uploaded_filename = None
				frappe.form_dict.pop("docname", None)
		self.create(doc)
		doc.reload()
		if doc.rows[0].slip_pdf:
			api.review_pdf(doc.name, doc.rows[0].name)
		if submit:
			importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		return doc, doc.rows[0].salary_slip

	def payment(self, amount=NET, date="2026-03-10", account=ACCOUNT, name="Zkušební Žofie"):
		description = f"Název: {name}\nZpráva: mzda"
		if account:
			description = f"Název: {name}\nProtiúčet: {account}\nZpráva: mzda"
		bt = frappe.get_doc(
			{
				"doctype": "Bank Transaction",
				"date": date,
				"bank_account": self.bank_account.name,
				"company": self.company,
				"currency": "CZK",
				"withdrawal": amount,
				"description": description,
			}
		).insert(ignore_permissions=True)
		bt.submit()
		return bt

	def paid(self, slip):
		return frappe.db.get_value("Salary Slip", slip, ["banking_bank_transaction", "banking_paid_on"])

	def entries(self, bt):
		bt = frappe.get_doc("Bank Transaction", bt.name)
		return bt.status, [(e.payment_document, e.payment_entry) for e in bt.payment_entries]


class TestSalaryPayments(SalaryPaymentCase):
	# --- bank transaction -> slip ---

	def test_platba_najde_pasku(self):
		_, slip = self.slip()
		bt = self.payment()
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "matched")
		status, entries = self.entries(bt)
		self.assertEqual(status, "Reconciled")
		((doctype, je_name),) = entries
		self.assertEqual(doctype, "Journal Entry")
		je = frappe.get_doc("Journal Entry", je_name)
		self.assertEqual(je.docstatus, 1)
		lines = {a.account: (a.debit_in_account_currency, a.credit_in_account_currency) for a in je.accounts}
		self.assertEqual(lines, {SALARY_ACCOUNT: (NET, 0), BANK_GL: (0, NET)})
		# the remark leads to the slip, without a name or an account
		self.assertIn(slip, je.user_remark)
		self.assertIn("02/2026", je.user_remark)
		for secret in ("Zkušební", "1000000001"):
			self.assertNotIn(secret, je.user_remark)
		bt_name, paid_on = self.paid(slip)
		self.assertEqual((bt_name, str(paid_on)), (bt.name, "2026-03-10"))
		# a second payment of the same amount does not book the paid slip again
		again = self.payment(date="2026-03-11")
		self.assertEqual(salary.reconcile_salary(again, self.settings), "unresolved")
		self.assertEqual(self.entries(again), ("Unreconciled", []))

	def test_jina_castka_zustane_na_cloveku(self):
		_, slip = self.slip()
		bt = self.payment(amount=NET - 500)
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "unresolved")
		self.assertEqual(self.entries(bt), ("Unreconciled", []))
		self.assertEqual(self.paid(slip), (None, None))

	def test_cizi_ucet_neni_mzda(self):
		self.slip()
		bt = self.payment(account="2000000002/0300")
		self.assertIsNone(salary.reconcile_salary(bt, self.settings))
		self.assertIsNone(salary.reconcile_salary(self.payment(account=None), self.settings))

	def test_mimo_obdobi(self):
		_, slip = self.slip()
		bt = self.payment(date="2026-06-01")  # far too late for 02/2026
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "unresolved")
		self.assertEqual(self.paid(slip), (None, None))

	def test_vypnuto(self):
		self.slip()
		bt = self.payment()
		for off in ({"auto_reconcile_salaries": 0}, {"auto_reconcile_outgoing": 0}):
			self.assertIsNone(salary.reconcile_salary(bt, frappe._dict(self.settings, **off)))
		self.assertEqual(self.entries(bt), ("Unreconciled", []))

	def test_draft_pasky_se_neparuje(self):
		_, slip = self.slip(submit=False)
		bt = self.payment()
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "unresolved")
		self.assertIsNone(salary.match_slip(slip, self.settings))

	def test_ucet_v_ruznych_zapisech(self):
		frappe.db.set_value("Employee", self.emp, {"bank_ac_no": None, "iban": "CZ0001000000001000000001"})
		_, slip = self.slip()
		bt = self.payment(account="000000-1000000001/0100")
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "matched")
		self.assertEqual(self.paid(slip)[0], bt.name)

	def test_ucet_z_bank_account_zamestnance(self):
		frappe.db.set_value("Employee", self.emp, "bank_ac_no", None)
		frappe.get_doc(
			{
				"doctype": "Bank Account",
				"account_name": "Účet zaměstnance",
				"bank": "Testovací banka",
				"party_type": "Employee",
				"party": self.emp,
				"bank_account_no": "1000000001",
				"branch_code": "0100",
			}
		).insert(ignore_permissions=True)
		_, slip = self.slip()
		bt = self.payment()
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "matched")
		self.assertEqual(self.paid(slip)[0], bt.name)

	def test_ucet_jen_z_mzdove_pasky(self):
		"""The employee has no account in the master data; the reviewed printed slip has it."""
		frappe.db.set_value("Employee", self.emp, "bank_ac_no", None)
		_, slip = self.slip(pdf_extra={"ucet": "000019-0000000123/0100"})
		# known as an employee's account because a submitted slip carries it
		self.assertTrue(salary.is_employee_account(salary.counter_account("Protiúčet: 19-123/0100")))
		self.assertFalse(salary.is_employee_account(salary.counter_account("Protiúčet: 19-124/0100")))
		bt = self.payment(account="19-123/0100")
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "matched")
		self.assertEqual(self.paid(slip)[0], bt.name)

	def test_dva_lide_se_stejnou_mzdou(self):
		other = self.employee(bank_ac_no="2000000002/0300")
		doc = self.import_file(
			jmhz_form("6000000001", "9200000001"),
			jmhz_form("6000000002", "9200000002"),
			pair={"6000000001": self.emp, "6000000002": other},
		)
		self.create(doc)
		importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		slip_a, slip_b = (r.salary_slip for r in doc.rows)
		self.assertEqual(frappe.db.get_value("Salary Slip", slip_b, "employee"), other)
		bt = self.payment(account="2000000002/0300")
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "matched")
		self.assertEqual((self.paid(slip_a)[0], self.paid(slip_b)[0]), (None, bt.name))

	# --- slip -> bank transaction ---

	def test_paska_najde_drivejsi_platbu(self):
		"""The usual order: the money leaves first, the slip is submitted days later."""
		bt = self.payment(date="2026-03-05")
		_, slip = self.slip()
		self.assertEqual(self.paid(slip), (None, None))  # the hook waits for the commit
		self.assertEqual(salary.match_slip(slip, self.settings), "matched")
		self.assertEqual(self.entries(bt)[0], "Reconciled")
		self.assertEqual(self.paid(slip)[0], bt.name)
		self.assertIsNone(salary.match_slip(slip, self.settings))  # already paid

	def test_paska_se_jen_oznaci_kdyz_je_platba_uz_zauctovana(self):
		"""A payment booked earlier by a rule or by hand gets no second Journal Entry."""
		bt = self.payment()
		rule = {"je_account": SALARY_ACCOUNT, "remark_template": "mzdy"}
		reconcile._create_auto_je(bt, self.settings, rule)
		before = self.entries(bt)
		self.assertEqual(before[0], "Reconciled")
		journal_entries = frappe.db.count("Journal Entry")
		_, slip = self.slip()
		self.assertEqual(salary.match_slip(slip, self.settings), "linked")
		self.assertEqual(self.entries(bt), before)
		self.assertEqual(frappe.db.count("Journal Entry"), journal_entries)
		self.assertEqual(self.paid(slip)[0], bt.name)

	def test_dve_stejne_platby_nejsou_jednoznacne(self):
		self.payment(date="2026-03-05")
		self.payment(date="2026-03-06")
		_, slip = self.slip()
		self.assertIsNone(salary.match_slip(slip, self.settings))
		self.assertEqual(self.paid(slip), (None, None))

	def test_novy_import_prevezme_platbu(self):
		"""Cancel the import and import the month again: the new slip takes over the payment."""
		doc, old = self.slip()
		bt = self.payment()
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "matched")
		importer.cancel_import(frappe.get_doc(importer.IMPORT, doc.name))
		self.assertEqual(frappe.db.get_value("Salary Slip", old, "docstatus"), 2)
		_, new = self.slip()
		self.assertNotEqual(new, old)
		self.assertEqual(salary.match_slip(new, self.settings), "linked")
		self.assertEqual(self.paid(new)[0], bt.name)
		self.assertEqual(len(self.entries(bt)[1]), 1)  # still one Journal Entry

	def test_nocni_dohledani(self):
		bt = self.payment(date="2026-03-05")
		_, slip = self.slip()
		self.assertEqual(salary.match_open_slips(self.settings, today="2026-03-20"), 1)
		self.assertEqual(salary.match_open_slips(self.settings, today="2026-03-20"), 0)
		self.assertEqual(self.paid(slip)[0], bt.name)
		# a slip older than the reconcile window is not looked at any more
		frappe.db.set_value("Salary Slip", slip, {"banking_bank_transaction": None, "banking_paid_on": None})
		self.assertEqual(salary.match_open_slips(self.settings, today="2026-09-01"), 0)

	# --- inside the outgoing reconciliation ---

	def test_pravidlo_s_uctem_uz_mzdu_nezauctuje(self):
		"""A leftover rule with the account as a pattern must not book a payment to an
		employee that fits no slip."""
		frappe.get_doc(
			{
				"doctype": "Bank Matching Rule",
				"enabled": 1,
				"pattern": ACCOUNT,
				"rule_type": "auto_je",
				"je_account": SALARY_ACCOUNT,
				"remark_template": "mzdy",
				"priority": 50,
			}
		).insert(ignore_permissions=True)
		_, slip = self.slip()
		wrong = self.payment(amount=NET + 1000)
		res = reconcile._reconcile_outgoing(wrong, self.settings)
		self.assertEqual((res.matched, res.unresolved), (0, 1))
		self.assertEqual(self.entries(wrong), ("Unreconciled", []))
		right = self.payment()
		res = reconcile._reconcile_outgoing(right, self.settings)
		self.assertEqual((res.matched, res.unresolved), (1, 0))
		self.assertEqual(self.paid(slip)[0], right.name)
		# an account ERPNext does not know as an employee's still goes through the rules
		frappe.db.set_value("Bank Matching Rule", {"pattern": ACCOUNT}, "pattern", "3000000003/0600")
		unknown = self.payment(account="3000000003/0600", amount=777)
		res = reconcile._reconcile_outgoing(unknown, self.settings)
		self.assertEqual(res.matched, 1)
		self.assertEqual(self.entries(unknown)[0], "Reconciled")
