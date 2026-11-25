"""Integration tests of the JMHZ setup. Need a bench with HRMS:

bench --site <site> run-tests --module erpnext_banking.jmhz.tests.test_setup
"""

import frappe
from frappe.tests import IntegrationTestCase

from erpnext_banking.jmhz import components as C
from erpnext_banking.jmhz import setup


class TestJmhzSetup(IntegrationTestCase):
	def setUp(self):
		frappe.set_user("Administrator")
		setup.ensure_all(strict=True)

	def tearDown(self):
		frappe.db.rollback()

	def counts(self):
		return {
			"components": frappe.db.count(
				"Salary Component", {"name": ["in", [c.name for c in C.COMPONENTS]]}
			),
			"structures": frappe.db.count("Salary Structure", {"name": setup.STRUCTURE_NAME}),
			"holiday_lists": frappe.db.count("Holiday List", {"name": setup.HOLIDAY_LIST_NAME}),
			"assignments": frappe.db.count("Holiday List Assignment", {"docstatus": 1}),
			"custom_fields": frappe.db.count("Custom Field", {"fieldname": ["like", "jmhz%"]}),
		}

	def test_idempotent(self):
		before = self.counts()
		self.assertEqual(setup.ensure_all(strict=True), [])
		self.assertEqual(setup.ensure_all(strict=True), [])
		self.assertEqual(self.counts(), before)
		self.assertEqual(before["components"], 19)
		self.assertEqual(before["structures"], 1)

	def test_component_flags(self):
		for comp in C.COMPONENTS:
			doc = frappe.get_doc("Salary Component", comp.name)
			self.assertEqual(doc.salary_component_abbr, comp.abbr)
			self.assertEqual(doc.type, comp.component_type)
			for field, value in setup.COMPONENT_FLAGS.items():
				self.assertEqual(doc.get(field) or type(value)(), value, (comp.name, field))

	def test_flags_are_enforced_again(self):
		name = C.TARIF.name
		frappe.db.set_value("Salary Component", name, {"depends_on_payment_days": 1, "is_tax_applicable": 1})
		frappe.db.set_single_value("Payroll Settings", "email_salary_slip_to_employee", 1)
		frappe.db.set_single_value("Payroll Settings", "disable_rounded_total", 0)
		setup.ensure_all(strict=True)
		self.assertEqual(frappe.db.get_value("Salary Component", name, "depends_on_payment_days"), 0)
		self.assertEqual(frappe.db.get_value("Salary Component", name, "is_tax_applicable"), 0)
		self.assertEqual(frappe.db.get_single_value("Payroll Settings", "email_salary_slip_to_employee"), 0)
		self.assertEqual(frappe.db.get_single_value("Payroll Settings", "disable_rounded_total"), 1)

	def test_structure_contains_all_components(self):
		doc = frappe.get_doc("Salary Structure", setup.STRUCTURE_NAME)
		self.assertEqual(doc.docstatus, 1)
		self.assertEqual(doc.is_active, "Yes")
		self.assertEqual(doc.payroll_frequency, "Monthly")
		self.assertEqual(doc.company, setup.get_company())
		self.assertEqual([r.salary_component for r in doc.earnings], [c.name for c in C.EARNINGS])
		self.assertEqual([r.salary_component for r in doc.deductions], [c.name for c in C.DEDUCTIONS])
		self.assertTrue(all(not r.amount and not r.formula for r in doc.earnings + doc.deductions))

	def test_missing_component_in_structure_is_reported(self):
		row = frappe.get_doc("Salary Structure", setup.STRUCTURE_NAME).deductions[-1]
		frappe.db.delete("Salary Detail", {"name": row.name})
		problems = setup.ensure_all(strict=True)
		self.assertTrue(any(C.EXEKUCE.name in p for p in problems), problems)

	def test_settings(self):
		settings = frappe.get_single(setup.SETTINGS)
		self.assertTrue(settings.company)
		self.assertEqual(settings.email_mode, "Vypnuto")
		self.assertEqual(settings.salary_structure, setup.STRUCTURE_NAME)
		self.assertFalse(settings.allow_slips_outside_import)
		self.assertFalse(settings.allow_payroll_entry)

	def test_holiday_list_for_company(self):
		from hrms.utils.holiday_list import get_holiday_list_for_employee

		company = setup.get_company()
		self.assertTrue(
			frappe.db.exists(
				"Holiday List Assignment",
				{"assigned_to": company, "applicable_for": "Company", "docstatus": 1},
			)
		)
		# an employee without own assignment resolves to the company list, also before 2026
		employee = frappe.get_doc(
			{
				"doctype": "Employee",
				"first_name": "Test",
				"last_name": "JMHZ Setup",
				"gender": "Female",
				"date_of_birth": "1990-01-01",
				"date_of_joining": "2025-06-01",
				"company": company,
				"status": "Active",
			}
		).insert(ignore_permissions=True)
		for day in ("2025-07-15", "2026-03-31", "2031-12-31"):
			self.assertTrue(get_holiday_list_for_employee(employee.name, as_on=day))

	def test_existing_company_assignment_is_kept(self):
		company = setup.get_company()
		frappe.db.delete("Holiday List Assignment", {"assigned_to": company})
		other = frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": "Test JMHZ vlastní",
				"from_date": "2026-01-01",
				"to_date": "2026-12-31",
			}
		).insert(ignore_permissions=True)
		hla = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Company",
				"assigned_to": company,
				"holiday_list": other.name,
				"from_date": "2026-01-01",
			}
		)
		hla.insert(ignore_permissions=True)
		hla.submit()
		setup.ensure_all(strict=True)
		lists = frappe.get_all(
			"Holiday List Assignment", {"assigned_to": company, "docstatus": 1}, pluck="holiday_list"
		)
		self.assertEqual(lists, [other.name])

	def test_custom_fields(self):
		meta = frappe.get_meta("Salary Slip")
		for field in setup.CUSTOM_FIELDS["Salary Slip"]:
			df = meta.get_field(field["fieldname"])
			self.assertIsNotNone(df, field["fieldname"])
			if df.fieldtype not in ("Section Break", "Column Break"):
				self.assertTrue(df.read_only and df.no_copy and not df.allow_on_submit, df.fieldname)
		self.assertEqual(frappe.get_meta("Additional Salary").get_field("jmhz_import").options, "JMHZ Import")

	def test_jmhz_person_has_no_version_history(self):
		self.assertFalse(frappe.get_meta("JMHZ Person").track_changes)
		self.assertFalse(frappe.get_meta("JMHZ Import").track_changes)
