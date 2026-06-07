"""Fio Settings — Single doctype.

Validation only here. Tlačítka volají whitelisted methods v erpnext_banking.api
(viz Task 21). Client script v fio_settings.js (Task 22).
"""
import frappe
from frappe import _
from frappe.model.document import Document


class FioSettings(Document):
	def validate(self):
		if self.enabled:
			if not self.api_token:
				frappe.throw(_("API Token is required when Fio is enabled."))
			if not self.company:
				frappe.throw(_("Company is required when Fio is enabled."))
			if not self.bank_account:
				frappe.throw(_("Bank Account is required when Fio is enabled."))
		if self.notify_on_error and not self.notify_email:
			frappe.throw(_("Notify Email is required when Notify on Error is enabled."))
		if self.lookback_days is not None and self.lookback_days < 1:
			frappe.throw(_("Lookback Days must be ≥ 1."))
		if self.reconcile_window_days is not None and self.reconcile_window_days < 1:
			frappe.throw(_("Reconcile Window Days must be ≥ 1."))
