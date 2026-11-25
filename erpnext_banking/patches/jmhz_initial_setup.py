"""One-off setup of the JMHZ module on sites where the app is already installed.

`after_install` never runs there, so the first setup happens in this patch.
JMHZ Settings start with e-mail sending switched off. `ensure_all()` also runs
from `after_migrate` on every migrate.
"""

import frappe

from erpnext_banking.jmhz.setup import SETTINGS, ensure_all


def execute():
	frappe.reload_doc("jmhz", "doctype", "jmhz_settings")
	frappe.db.set_single_value(SETTINGS, "email_mode", "Vypnuto")
	for problem in ensure_all():
		print(f"JMHZ setup: {problem}")
