from . import __version__ as app_version  # noqa: F401  # used by Frappe hook discovery

app_name = "erpnext_banking"
app_title = "ERPNext Banking"
app_publisher = "propix group"
app_description = "Bank integration for ERPNext (Fio + extensible to other providers)"
app_icon = "octicon octicon-credit-card"
app_color = "blue"
app_email = "krysta@propix.cz"
app_license = "MIT"

required_apps = ["erpnext", "hrms"]

# JMHZ module: idempotent setup of salary components, structure and settings
after_migrate = "erpnext_banking.jmhz.setup.after_migrate"


scheduler_events = {
	"cron": {
		# 02:00 — incremental fetch from each enabled provider (/last/ or equivalent)
		"0 2 * * *": ["erpnext_banking.sync.scheduled_sync"],
		# 03:00 — safety-net /periods/ fetch (covers /last/ pointer drift)
		"0 3 * * *": ["erpnext_banking.sync.scheduled_lookback"],
		# 03:30 — re-match Unreconciled BT in sliding window (catches "payment before invoice")
		"30 3 * * *": ["erpnext_banking.reconcile.scheduled_rematch"],
	}
}


doc_events = {
	"Sales Invoice": {"on_submit": "erpnext_banking.reconcile.on_invoice_submit"},
	"Purchase Invoice": {"on_submit": "erpnext_banking.reconcile.on_invoice_submit"},
	"Payment Request": {"on_submit": "erpnext_banking.reconcile.on_payment_request_submit"},
	# JMHZ module (SPEC §7)
	"Salary Slip": {"before_submit": "erpnext_banking.jmhz.guards.salary_slip_before_submit"},
	"Payroll Entry": {"validate": "erpnext_banking.jmhz.guards.block_payroll_entry"},
}

# JMHZ module: users without an HR role do not see draft and cancelled salary slips
permission_query_conditions = {"Salary Slip": "erpnext_banking.jmhz.guards.salary_slip_query"}
has_permission = {"Salary Slip": "erpnext_banking.jmhz.guards.salary_slip_has_permission"}
