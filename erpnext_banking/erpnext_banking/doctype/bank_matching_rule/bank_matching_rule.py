"""Bank Matching Rule — declarative rule for auto-processing card / no-counterparty
Bank Transactions.

A rule matches when its ``pattern`` (case-insensitive substring, or a regex when
prefixed with ``re:``) is found in the Bank Transaction description. Rules are evaluated
in ascending ``priority`` and the first match wins. See ``erpnext_banking._card`` for the
pure matching logic and ``erpnext_banking.reconcile`` for how rules drive reconciliation.
"""

import frappe
from frappe import _
from frappe.model.document import Document


class BankMatchingRule(Document):
	def validate(self):
		if self.rule_type == "merchant_supplier" and not self.supplier:
			frappe.throw(_("Supplier is required for a merchant_supplier rule."))
		if self.rule_type == "auto_je" and not self.je_account:
			frappe.throw(_("JE Account is required for an auto_je rule."))
		if not (self.pattern or "").strip():
			frappe.throw(_("Pattern must not be empty."))
