"""Seed the default Bank Matching Rules (idempotent).

Each rule is created only when (a) no rule with the same pattern+rule_type already exists
and (b) its target Supplier / Account exists on the site — otherwise it is skipped and
logged, so a fresh site without the CZ chart of accounts / suppliers migrates cleanly and
the rule can be added later.

Patterns are matched case-insensitively as substrings of the Bank Transaction description
(see erpnext_banking._card). One "GOOGLE" rule covers both "GOOGLE*WORKSPACE PROPI" and
"Google CLOUD ...". auto_je salary rules match on the counterparty account that the Fio
mapper writes into the description as "Protiúčet: <acc>/<bank>".
"""

import frappe

# (pattern, rule_type, supplier, je_account, remark_template, priority)
_RULES = [
	# Merchant → supplier (card subscriptions)
	("FACEBK", "merchant_supplier", "Meta Platforms Ireland Limited", None, None, 100),
	("ANTHROPIC", "merchant_supplier", "Anthropic, PBC", None, None, 100),
	("GOOGLE", "merchant_supplier", "GOOGLE CLOUD EMEA LIMITED", None, None, 100),
	("HETZNER", "merchant_supplier", "Hetzner Online GmbH", None, None, 100),
	# Salaries → 521 (matched by counterparty account in the description)
	("2371741018/3030", "auto_je", None, "521 - Mzdové náklady - PXG", "mzdy", 50),
	("670100-2215007057/6210", "auto_je", None, "521 - Mzdové náklady - PXG", "mzdy", 50),
	("670100-2215440256/6210", "auto_je", None, "521 - Mzdové náklady - PXG", "mzdy", 50),
	# Bank fees → 568
	("Poplatek", "auto_je", None, "568 - Ostatní finanční náklady - PXG", "poplatek", 100),
]


def execute():
	if not frappe.db.exists("DocType", "Bank Matching Rule"):
		return
	logger = frappe.logger("erpnext_banking")

	for pattern, rule_type, supplier, je_account, remark, priority in _RULES:
		if frappe.db.exists("Bank Matching Rule", {"pattern": pattern, "rule_type": rule_type}):
			continue
		if rule_type == "merchant_supplier" and not frappe.db.exists("Supplier", supplier):
			logger.info(f"seed_bank_matching_rules: Supplier '{supplier}' missing — skip '{pattern}'")
			continue
		if rule_type == "auto_je" and not frappe.db.exists("Account", je_account):
			logger.info(f"seed_bank_matching_rules: Account '{je_account}' missing — skip '{pattern}'")
			continue

		doc = frappe.get_doc(
			{
				"doctype": "Bank Matching Rule",
				"enabled": 1,
				"pattern": pattern,
				"rule_type": rule_type,
				"supplier": supplier,
				"je_account": je_account,
				"remark_template": remark,
				"priority": priority,
			}
		)
		doc.insert(ignore_permissions=True)

	frappe.db.commit()
