"""Integration tests of booking salary slips (accrual) and settling them by the bank payment.
Synthetic data only.

bench --site <site> run-tests --module erpnext_banking.jmhz.tests.test_accounting
"""

import frappe

from erpnext_banking import reconcile, salary
from erpnext_banking.jmhz import accounting, importer, setup
from erpnext_banking.jmhz.tests.synthetic import form
from erpnext_banking.jmhz.tests.test_salary_payments import ACCOUNT, NET, SALARY_ACCOUNT, SalaryPaymentCase

CZECH_ACCOUNTS = {
	"331": ("Zaměstnanci", "Liability", "Payable"),
	"336": ("Zúčtování s institucemi SZ a ZP", "Liability", ""),
	"342": ("Ostatní přímé daně", "Liability", ""),
	"379": ("Jiné závazky", "Liability", ""),
	"524": ("Zákonné sociální pojištění", "Expense", ""),
}
PARENTS = {"Liability": "Source of Funds (Liabilities) - PXG", "Expense": "Indirect Expenses - PXG"}


class AccountingTestCase(SalaryPaymentCase):
	"""Booking is on and the Czech accounts exist. No tests here."""

	def setUp(self):
		super().setUp()
		self.acc = {"521": SALARY_ACCOUNT}
		for number, (name, root, account_type) in CZECH_ACCOUNTS.items():
			full = f"{number} - {name} - PXG"
			if not frappe.db.exists("Account", full):
				frappe.get_doc(
					{
						"doctype": "Account",
						"account_name": name,
						"account_number": number,
						"parent_account": PARENTS[root],
						"company": self.company,
						"root_type": root,
						"account_type": account_type,
					}
				).insert(ignore_permissions=True)
			self.acc[number] = full
		frappe.db.set_value("Account", SALARY_ACCOUNT, "account_number", "521")
		# settings are cached; a rolled back test must not leave "booking on" behind
		self.addCleanup(frappe.clear_document_cache, accounting.SETTINGS, accounting.SETTINGS)
		settings = frappe.get_single(accounting.SETTINGS)
		for field, _number in accounting.ACCOUNT_FIELDS.values():
			settings.set(field, None)
		settings.save(ignore_permissions=True)
		self.assertEqual(setup.ensure_settings(), [])  # fills the accounts by their numbers
		settings = frappe.get_single(accounting.SETTINGS)
		settings.post_accrual = 1
		settings.save(ignore_permissions=True)

	def entry(self, name):
		je = frappe.get_doc("Journal Entry", name)
		lines = sorted(
			(a.account[:3], a.debit_in_account_currency, a.credit_in_account_currency, a.party or "")
			for a in je.accounts
		)
		return je, lines

	def balance(self, account, employee=None):
		"""Credit balance of an account in the ledger, optionally for one employee."""
		filters = {"account": account, "is_cancelled": 0}
		if employee:
			filters.update({"party_type": "Employee", "party": employee})
		rows = frappe.get_all("GL Entry", filters=filters, fields=["debit", "credit"])
		return sum(r.credit - r.debit for r in rows)


class TestAccrual(AccountingTestCase):
	def test_ucty_se_predvyplni_podle_cisla(self):
		settings = frappe.get_single(accounting.SETTINGS)
		got = {number: settings.get(field) for field, number in accounting.ACCOUNT_FIELDS.values()}
		self.assertEqual(got, self.acc)
		# an account set by hand is never overwritten
		settings.accrual_tax_account = self.acc["379"]
		settings.save(ignore_permissions=True)
		setup.ensure_settings()
		self.assertEqual(
			frappe.db.get_single_value(accounting.SETTINGS, "accrual_tax_account"), self.acc["379"]
		)

	def test_zapnout_jde_jen_s_ucty(self):
		settings = frappe.get_single(accounting.SETTINGS)
		settings.accrual_insurance_account = None
		with self.assertRaises(frappe.ValidationError) as e:
			settings.save(ignore_permissions=True)
		self.assertIn("336", str(e.exception))

	def test_predpis_pri_odeslani(self):
		_, slip = self.slip()
		entry = frappe.db.get_value("Salary Slip", slip, "jmhz_accrual_entry")
		je, lines = self.entry(entry)
		self.assertEqual(je.docstatus, 1)
		self.assertEqual(str(je.posting_date), "2026-02-28")  # booked in the period of the slip
		self.assertEqual(
			lines,
			[
				("331", 0, 15353, self.emp),  # 521 / 331 gross
				("331", 1782, 0, self.emp),  # 331 / 336 insurance of the employee
				("336", 0, 1782 + 4655, ""),
				("521", 15353, 0, ""),
				("524", 4655, 0, ""),  # 524 / 336 insurance of the employer
			],
		)
		self.assertIn(slip, je.user_remark)
		self.assertNotIn("Test", je.user_remark)  # no name of the person in the ledger
		# the payable to the employee is exactly the net pay
		self.assertEqual(self.balance(self.acc["331"], self.emp), NET)
		self.assertEqual(self.balance(SALARY_ACCOUNT), -15353)

	def test_bonus_dan_a_rucni_srazka(self):
		ik = "6000000003"
		f = form(
			ik=ik,
			ppv="9200000003",
			zuct=30000,
			mzda=30000,
			sp=2130,
			zp=1350,
			zal=1500,
			bonus=1000,
			cisty=25020,
		)
		doc = self.import_file(f, pair={ik: self.emp}, souhrn_zal=1500, souhrn_bon=1000)
		doc.rows[0].rucni_srazka = 2000
		doc.rows[0].rucni_srazka_poznamka = "test"
		doc.save(ignore_permissions=True)
		self.create(doc)
		importer.submit_slips(frappe.get_doc(importer.IMPORT, doc.name))
		doc.reload()
		slip = doc.rows[0].salary_slip
		_, lines = self.entry(frappe.db.get_value("Salary Slip", slip, "jmhz_accrual_entry"))
		got = {(acc, "D" if debit else "C"): debit or credit for acc, debit, credit, _party in lines}
		self.assertEqual(
			got,
			{
				("521", "D"): 30000,
				("524", "D"): 2130 * 3 + 1350 * 2,
				("342", "D"): 1000,  # tax bonus 342 / 331
				("331", "C"): 31000,
				("331", "D"): 2130 + 1350 + 1500 + 2000,
				("336", "C"): 2130 + 1350 + 2130 * 3 + 1350 * 2,
				("342", "C"): 1500,  # advance tax 331 / 342
				("379", "C"): 2000,  # other deduction 331 / 379
			},
		)
		net = frappe.db.get_value("Salary Slip", slip, "net_pay")
		self.assertEqual((net, self.balance(self.acc["331"], self.emp)), (24020, 24020))

	def test_zruseni_importu_zrusi_predpis(self):
		doc, slip = self.slip()
		entry = frappe.db.get_value("Salary Slip", slip, "jmhz_accrual_entry")
		importer.cancel_import(frappe.get_doc(importer.IMPORT, doc.name))
		self.assertEqual(frappe.db.get_value("Journal Entry", entry, "docstatus"), 2)
		self.assertEqual(self.balance(self.acc["331"], self.emp), 0)
		self.assertFalse(accounting.accrued(slip))

	def test_vypnuto_nic_neuctuje(self):
		frappe.db.set_single_value(accounting.SETTINGS, "post_accrual", 0)
		before = frappe.db.count("Journal Entry")
		_, slip = self.slip()
		self.assertIsNone(frappe.db.get_value("Salary Slip", slip, "jmhz_accrual_entry"))
		self.assertEqual(frappe.db.count("Journal Entry"), before)

	def test_cizi_slozka_se_neuctuje(self):
		_, slip = self.slip(submit=False)
		doc = frappe.get_doc("Salary Slip", slip)
		doc.earnings[0].salary_component = "Basic"
		with self.assertRaises(frappe.ValidationError):
			accounting.post_accrual(doc)

	# --- the payment settles the payable ---

	def test_platba_uhradi_zavazek(self):
		_, slip = self.slip()
		bt = self.payment()
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "matched")
		((_doctype, name),) = self.entries(bt)[1]
		_, lines = self.entry(name)
		self.assertEqual(lines, [("221", 0, NET, ""), ("331", NET, 0, self.emp)])  # 331 / 221
		self.assertEqual(self.balance(self.acc["331"], self.emp), 0)
		self.assertEqual(self.balance(SALARY_ACCOUNT), -15353)  # the expense is the gross wage, once

	def test_platba_pred_paskou(self):
		"""The money leaves first: nothing is booked until the slip exists, then 331 / 221."""
		bt = self.payment(date="2026-03-05")
		self.assertEqual(reconcile._reconcile_outgoing(bt, self.settings).unresolved, 1)
		self.assertEqual(self.entries(bt), ("Unreconciled", []))
		_, slip = self.slip()
		self.assertEqual(salary.match_slip(slip, self.settings), "matched")
		self.assertEqual(self.balance(self.acc["331"], self.emp), 0)

	def test_drive_zauctovana_platba_se_preuctuje_jednou(self):
		"""A payment booked 521 / 221 by an old rule meets a slip with an accrual: it is moved
		to the payable, so the wage is not an expense twice."""
		bt = self.payment()
		reconcile._create_auto_je(
			bt, self.settings, {"je_account": SALARY_ACCOUNT, "remark_template": "mzdy"}
		)
		doc, slip = self.slip()
		self.assertEqual(salary.match_slip(slip, self.settings), "linked")
		moved = frappe.get_all("Journal Entry", filters={"cheque_no": bt.name, "docstatus": 1}, pluck="name")
		moved = [
			m
			for m in moved
			if frappe.db.get_value("Journal Entry", m, "user_remark").startswith(salary.RECLASS_PREFIX)
		]
		self.assertEqual(len(moved), 1)
		_, lines = self.entry(moved[0])
		self.assertEqual(lines, [("331", NET, 0, self.emp), ("521", 0, NET, "")])  # 331 / 521
		self.assertEqual(self.balance(self.acc["331"], self.emp), 0)
		self.assertEqual(self.balance(SALARY_ACCOUNT), -15353)

		# the month is imported again: the new slip takes the payment over, nothing is moved twice
		importer.cancel_import(frappe.get_doc(importer.IMPORT, doc.name))
		_, new = self.slip()
		self.assertEqual(salary.match_slip(new, self.settings), "linked")
		moved_now = [
			name
			for name in frappe.get_all(
				"Journal Entry", filters={"cheque_no": bt.name, "docstatus": 1}, pluck="name"
			)
			if frappe.db.get_value("Journal Entry", name, "user_remark").startswith(salary.RECLASS_PREFIX)
		]
		self.assertEqual(moved_now, moved)
		self.assertEqual(self.balance(self.acc["331"], self.emp), 0)
		self.assertEqual(self.balance(SALARY_ACCOUNT), -15353)

	def test_paska_bez_predpisu_se_plati_do_nakladu(self):
		"""A slip submitted while booking was off has no accrual: its payment is 521 / 221."""
		frappe.db.set_single_value(accounting.SETTINGS, "post_accrual", 0)
		_, slip = self.slip()
		frappe.db.set_single_value(accounting.SETTINGS, "post_accrual", 1)
		bt = self.payment()
		self.assertEqual(salary.reconcile_salary(bt, self.settings), "matched")
		((_doctype, name),) = self.entries(bt)[1]
		_, lines = self.entry(name)
		self.assertEqual(lines, [("221", 0, NET, ""), ("521", NET, 0, "")])


class TestAccountRules(AccountingTestCase):
	def rule(self, pattern, rule_type="auto_je"):
		return (
			frappe.get_doc(
				{
					"doctype": "Bank Matching Rule",
					"enabled": 1,
					"pattern": pattern,
					"rule_type": rule_type,
					"je_account": SALARY_ACCOUNT if rule_type == "auto_je" else None,
					"priority": 50,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	def test_uklid_pravidel_s_ucty_zamestnancu(self):
		known = self.rule("000000-1000000001/0100")  # the account of self.emp, written differently
		unknown = self.rule("3000000003/0600")
		text = self.rule("Nějaký poplatek")
		ignore = self.rule(ACCOUNT, rule_type="ignore")  # not a booking rule
		self.assertEqual(salary.remove_account_rules(), 1)
		self.assertFalse(frappe.db.exists("Bank Matching Rule", known))
		for kept in (unknown, text, ignore):
			self.assertTrue(frappe.db.exists("Bank Matching Rule", kept))
		# deleted for good: the account is not kept in Deleted Document
		self.assertFalse(
			frappe.db.exists(
				"Deleted Document", {"deleted_doctype": "Bank Matching Rule", "deleted_name": known}
			)
		)
		self.assertEqual(salary.remove_account_rules(), 0)

	def test_uklid_zna_ucet_z_pasky(self):
		frappe.db.set_value("Employee", self.emp, "bank_ac_no", None)
		rule = self.rule("19-123/0100")
		self.assertEqual(salary.remove_account_rules(), 0)  # nobody's account yet
		self.slip(pdf_extra={"ucet": "000019-0000000123/0100"})
		self.assertEqual(salary.remove_account_rules(), 1)
		self.assertFalse(frappe.db.exists("Bank Matching Rule", rule))
