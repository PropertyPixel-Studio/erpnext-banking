"""Create the original_amount / original_currency custom fields on Purchase Invoice.

Foreign-currency (EUR/USD) supplier invoices are normalized to CZK on import (see the
Paperless `create_purchase_invoice_from_extraction` server script). These two fields
preserve the pre-conversion amount + currency so a card charge can be matched against the
original transaction value instead of only the FX-drifted CZK total.
"""

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def execute():
	create_custom_fields(
		{
			"Purchase Invoice": [
				{
					"fieldname": "original_amount",
					"label": "Original Amount (FX)",
					"fieldtype": "Float",
					"precision": "2",
					"insert_after": "grand_total",
					"read_only": 1,
					"no_copy": 1,
					"description": "Invoice total in its original currency, before normalization to CZK.",
				},
				{
					"fieldname": "original_currency",
					"label": "Original Currency",
					"fieldtype": "Link",
					"options": "Currency",
					"insert_after": "original_amount",
					"read_only": 1,
					"no_copy": 1,
					"description": "Original transaction currency (e.g. EUR / USD) before normalization to CZK.",
				},
			]
		},
		ignore_validate=True,
	)
	frappe.db.commit()
