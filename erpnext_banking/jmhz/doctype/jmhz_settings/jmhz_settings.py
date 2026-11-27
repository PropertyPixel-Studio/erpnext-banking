"""Settings of the JMHZ module."""

from frappe.model.document import Document


class JMHZSettings(Document):
	def validate(self):
		if self.post_accrual:
			from erpnext_banking.jmhz import accounting

			accounting.accounts(self)  # every account has to be set before booking is turned on
