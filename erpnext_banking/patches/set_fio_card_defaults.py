"""Backfill card-matching defaults on an existing Fio Settings single.

New installs get these via the doctype JSON `default`s; this patch fills them in on sites
that already had Fio Settings before the card-matching feature. Account/cost-center links
are only set when the target actually exists on the site, so the patch never writes a
dangling link.
"""

import frappe

_ACCOUNT_DEFAULTS = {
	"je_contra_account": "221 - Bankovní účty CZK - PXG",
	"fx_loss_account": "563 - Kurzové ztráty - PXG",
	"fx_gain_account": "663 - Kurzové zisky - PXG",
}
_COST_CENTER_DEFAULT = ("default_cost_center", "Main - PXG")


def execute():
	if not frappe.db.exists("DocType", "Fio Settings"):
		return
	settings = frappe.get_single("Fio Settings")
	changed = False

	if not settings.get("fx_tolerance_percent"):
		settings.fx_tolerance_percent = 4.0
		changed = True
	if settings.get("auto_journal_entries") is None:
		settings.auto_journal_entries = 1
		changed = True

	for field, account in _ACCOUNT_DEFAULTS.items():
		if not settings.get(field) and frappe.db.exists("Account", account):
			settings.set(field, account)
			changed = True

	cc_field, cc_value = _COST_CENTER_DEFAULT
	if not settings.get(cc_field) and frappe.db.exists("Cost Center", cc_value):
		settings.set(cc_field, cc_value)
		changed = True

	if changed:
		settings.save(ignore_permissions=True)
		frappe.db.commit()
