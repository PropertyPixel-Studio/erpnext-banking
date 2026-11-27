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

after_migrate = [
	# JMHZ module: idempotent setup of salary components, structure and settings
	"erpnext_banking.jmhz.setup.after_migrate",
	# banking: no rule may book by an account of an employee, salaries are matched from slips
	"erpnext_banking.salary.remove_account_rules",
]


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
	"Salary Slip": {
		# banking: look for the bank payment of the slip (after the submit is committed)
		"on_submit": "erpnext_banking.salary.on_salary_slip_submit",
		"before_submit": "erpnext_banking.jmhz.guards.salary_slip_before_submit",
		"before_cancel": "erpnext_banking.jmhz.guards.salary_slip_before_cancel",
		"after_delete": "erpnext_banking.jmhz.guards.scrub_deleted",
	},
	"Payroll Entry": {"validate": "erpnext_banking.jmhz.guards.block_payroll_entry"},
	# no IK MPSV or amounts in Deleted Document (System Manager can read it)
	"Additional Salary": {"after_delete": "erpnext_banking.jmhz.guards.scrub_deleted"},
	"JMHZ Import": {"after_delete": "erpnext_banking.jmhz.guards.scrub_deleted"},
	"JMHZ Person": {"after_delete": "erpnext_banking.jmhz.guards.scrub_deleted"},
}

# JMHZ module: button "Poslat pásku e-mailem" on the salary slip
doctype_js = {"Salary Slip": "public/js/jmhz_salary_slip.js"}

# JMHZ module: the HRMS list action "Email Salary Slips" respects JMHZ Settings.email_mode
override_whitelisted_methods = {
	"hrms.payroll.doctype.salary_slip.salary_slip.enqueue_email_salary_slips": (
		"erpnext_banking.jmhz.mailer.enqueue_email_salary_slips"
	)
}

# JMHZ module: users without an HR role do not see draft and cancelled salary slips
permission_query_conditions = {"Salary Slip": "erpnext_banking.jmhz.guards.salary_slip_query"}
has_permission = {"Salary Slip": "erpnext_banking.jmhz.guards.salary_slip_has_permission"}
