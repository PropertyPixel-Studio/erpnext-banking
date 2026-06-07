from frappe import _


def get_data():
	return [
		{
			"module_name": "ERPNext Banking",
			"category": "Modules",
			"label": _("ERPNext Banking"),
			"color": "blue",
			"icon": "octicon octicon-credit-card",
			"type": "module",
			"description": "Bank integration for ERPNext.",
		}
	]
