from . import __version__ as app_version

app_name = "erpnext_banking"
app_title = "ERPNext Banking"
app_publisher = "PropertyPixel-Studio"
app_description = "Bank integration for ERPNext (Fio + extensible to other providers)"
app_icon = "octicon octicon-credit-card"
app_color = "blue"
app_email = "krysta@propix.cz"
app_license = "MIT"

required_apps = ["erpnext"]


scheduler_events = {
	"cron": {
		# 02:00 — incremental fetch from each enabled provider (/last/ or equivalent)
		"0 2 * * *":  ["erpnext_banking.sync.scheduled_sync"],
		# 03:00 — safety-net /periods/ fetch (covers /last/ pointer drift)
		"0 3 * * *":  ["erpnext_banking.sync.scheduled_lookback"],
		# 03:30 — re-match Unreconciled BT in sliding window (catches "payment before invoice")
		"30 3 * * *": ["erpnext_banking.reconcile.scheduled_rematch"],
	}
}


doc_events = {
	"Sales Invoice":    {"on_submit": "erpnext_banking.reconcile.on_invoice_submit"},
	"Purchase Invoice": {"on_submit": "erpnext_banking.reconcile.on_invoice_submit"},
	"Payment Request":  {"on_submit": "erpnext_banking.reconcile.on_payment_request_submit"},
}
