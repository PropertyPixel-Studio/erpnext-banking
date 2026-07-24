"""Reconciliation — match Unreconciled Bank Transactions to invoices.

Provider-agnostic: works over Bank Transaction (stock ERPNext doctype), regardless
of which provider created the BT. Provider-specific Settings supply gating flags
(auto_reconcile_incoming/outgoing) and amount-matching window.

Strategy per direction:
- deposit  → Sales Invoice (variable_symbol == BT.reference_number) OR Payment Request
- withdrawal → Purchase Invoice (layered: supplier+VS, supplier+amount+window, VS-only)

Hard rules (spec §4):
- Tolerance: ±1 Kč (see _helpers.amount_matches).
- Minimum 2 matching signals for outgoing — never amount alone.
- Any ambiguity → leave Unreconciled, manual via Bank Reconciliation Tool.
"""

from __future__ import annotations

from dataclasses import dataclass

import frappe
from frappe.utils import getdate

from ._card import (
	build_je_remark,
	card_window,
	compute_fx_deduction,
	journal_entry_payload,
	parse_original_amount,
	pick_card_candidate,
	select_rule,
)
from ._helpers import amount_matches, outgoing_window, pick_unique_voucher
from .providers import iter_enabled_providers
from .providers.base import BankProvider

# Config defaults for the card / rules-engine branch (overridable in Fio Settings).
DEFAULT_FX_TOLERANCE_PERCENT = 4.0
DEFAULT_FX_LOSS_ACCOUNT = "563 - Kurzové ztráty - PXG"
DEFAULT_FX_GAIN_ACCOUNT = "663 - Kurzové zisky - PXG"
DEFAULT_JE_CONTRA_ACCOUNT = "221 - Bankovní účty CZK - PXG"
DEFAULT_COST_CENTER = "Main - PXG"


@dataclass
class ReconcileResult:
	matched: int = 0
	unresolved: int = 0
	errors: int = 0
	linked: int = 0  # BTs linked to a pre-existing Payment Entry / Journal Entry

	def to_dict(self) -> dict:
		return {
			"matched": self.matched,
			"unresolved": self.unresolved,
			"errors": self.errors,
			"linked": self.linked,
		}


def reconcile_one(bt_name: str) -> ReconcileResult:
	"""Reconcile a single Bank Transaction. No-op if already reconciled or no matching settings."""
	bt = frappe.get_doc("Bank Transaction", bt_name)
	if bt.status != "Unreconciled":
		return ReconcileResult()

	provider = _provider_for_bank_account(bt.bank_account)
	if not provider:
		return ReconcileResult()  # BT not owned by us
	settings = frappe.get_single(provider.settings_doctype)

	deposit = float(bt.deposit or 0)
	withdrawal = float(bt.withdrawal or 0)

	if deposit > 0 and withdrawal == 0:
		if not settings.auto_reconcile_incoming:
			return ReconcileResult()
		return _reconcile_incoming(bt, settings)
	elif withdrawal > 0:
		if not settings.auto_reconcile_outgoing:
			return ReconcileResult()
		return _reconcile_outgoing(bt, settings)
	return ReconcileResult()


def _find_unique(doctype: str, filters: dict, fields: list[str]) -> dict | None:
	"""Return the single record matching `filters`, or None if there are zero or several.

	Replaces `frappe.db.get_value(...)`, which silently returns "the first" row on
	ambiguous filters. That's dangerous for invoice matching: e.g. a supplier billing
	the exact same amount every month has several open Purchase Invoices matching
	`{supplier, variable_symbol}` at once, and picking "the first" one can allocate a
	payment to the wrong invoice. Any rule that needs "the one invoice this filter
	identifies" should go through this helper instead of get_value/get_list[0].
	"""
	results = frappe.get_all(doctype, filters=filters, fields=fields)
	if len(results) == 1:
		return results[0]
	if len(results) > 1:
		frappe.logger("erpnext_banking").debug(
			f"Ambiguous {doctype} match ({len(results)} candidates) for filters={filters} — skipping"
		)
	return None


def _provider_for_bank_account(bank_account: str) -> BankProvider | None:
	"""Find which enabled provider owns this Bank Account."""
	for p in iter_enabled_providers():
		settings = frappe.get_single(p.settings_doctype)
		if settings.bank_account == bank_account:
			return p
	return None


def _reconcile_incoming(bt, settings) -> ReconcileResult:
	"""Match incoming deposit to a Sales Invoice (preferred) or Payment Request by VS."""
	vs = (bt.reference_number or "").strip()
	if not vs:
		return ReconcileResult(unresolved=1)

	# 1) Sales Invoice with matching variable_symbol, still has outstanding
	# (ambiguity-guarded: skip if >1 open SI shares this VS — see _find_unique)
	si = _find_unique(
		"Sales Invoice",
		{
			"variable_symbol": vs,
			"docstatus": 1,
			"outstanding_amount": (">", 0),
			"company": settings.company,
		},
		["name", "outstanding_amount"],
	)
	if si and amount_matches(float(bt.deposit), float(si["outstanding_amount"])):
		try:
			_create_payment_entry_and_reconcile(bt, "Sales Invoice", si["name"], float(bt.deposit))
			return ReconcileResult(matched=1)
		except Exception:
			frappe.log_error("Incoming SI reconcile failed", "erpnext_banking")
			return ReconcileResult(errors=1)

	# 2) Payment Request with matching variable_symbol (same ambiguity guard)
	pr = _find_unique(
		"Payment Request",
		{
			"variable_symbol": vs,
			"docstatus": 1,
			"status": ("in", ["Requested", "Partially Paid"]),
		},
		["name", "reference_doctype", "reference_name", "grand_total"],
	)
	if pr and amount_matches(float(bt.deposit), float(pr["grand_total"])):
		try:
			_create_payment_entry_and_reconcile(
				bt, pr["reference_doctype"], pr["reference_name"], float(bt.deposit)
			)
			return ReconcileResult(matched=1)
		except Exception:
			frappe.log_error("Incoming PR reconcile failed", "erpnext_banking")
			return ReconcileResult(errors=1)

	return ReconcileResult(unresolved=1)


def _reconcile_outgoing(bt, settings) -> ReconcileResult:
	"""Match outgoing withdrawal to Purchase Invoice, layered rules (spec §4)."""
	amount = float(bt.withdrawal)
	vs = (bt.reference_number or "").strip()
	supplier = bt.party if bt.party_type == "Supplier" else None

	# Rule 1: supplier known + VS == bill_no, single open PI
	# (ambiguity-guarded: e.g. a supplier billing the same amount+VS twice — see _find_unique)
	if supplier and vs:
		pi = _find_unique(
			"Purchase Invoice",
			{
				"supplier": supplier,
				"variable_symbol": vs,
				"docstatus": 1,
				"outstanding_amount": (">", 0),
				"company": settings.company,
			},
			["name", "outstanding_amount"],
		)
		if pi and amount_matches(amount, float(pi["outstanding_amount"])):
			return _try_pay_and_reconcile(bt, "Purchase Invoice", pi["name"], amount)

	# Rule 2: supplier known + single open PI with matching amount in window
	if supplier:
		w_from, w_to = outgoing_window(getdate(bt.date))
		candidates = frappe.get_all(
			"Purchase Invoice",
			filters={
				"supplier": supplier,
				"docstatus": 1,
				"outstanding_amount": (">", 0),
				"company": settings.company,
				"posting_date": ("between", [w_from, w_to]),
			},
			fields=["name", "outstanding_amount"],
		)
		matching = [c for c in candidates if amount_matches(amount, float(c.outstanding_amount))]
		if len(matching) == 1:
			return _try_pay_and_reconcile(bt, "Purchase Invoice", matching[0].name, amount)
		# >1 matching → ambiguity, do not auto-reconcile

	# Card / rules-engine branch: BTs with no counterparty (card payments, bank fees,
	# salaries). Driven by Bank Matching Rule instead of supplier+VS. Runs after the
	# supplier rules (which need a party) and before the VS-only Rule 3.
	if not supplier:
		r = _reconcile_by_rules(bt, settings)
		if r is not None:
			return r

	# Rule 3: supplier unknown but VS == bill_no uniquely identifies one PI
	# (rewritten onto _find_unique for consistency — behavior unchanged, was already
	# ambiguity-guarded via the len(pis) == 1 check)
	if vs and not supplier:
		pi = _find_unique(
			"Purchase Invoice",
			{
				"variable_symbol": vs,
				"docstatus": 1,
				"outstanding_amount": (">", 0),
				"company": settings.company,
			},
			["name", "outstanding_amount"],
		)
		if pi and amount_matches(amount, float(pi["outstanding_amount"])):
			return _try_pay_and_reconcile(bt, "Purchase Invoice", pi["name"], amount)

	return ReconcileResult(unresolved=1)


def _try_pay_and_reconcile(bt, dt: str, dn: str, amount: float) -> ReconcileResult:
	try:
		_create_payment_entry_and_reconcile(bt, dt, dn, amount)
		return ReconcileResult(matched=1)
	except Exception:
		frappe.log_error("Outgoing reconcile failed", "erpnext_banking")
		return ReconcileResult(errors=1)


def _create_payment_entry_and_reconcile(
	bt, reference_doctype: str, reference_name: str, amount: float
) -> None:
	"""Create Payment Entry against the reference and attach to BT.payment_entries.

	Uses stock helper `get_payment_entry` from erpnext.accounts.doctype.payment_entry.
	Sets reference_no = bt.name for audit traceability.
	"""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	payment_type = "Receive" if (bt.deposit and bt.deposit > 0) else "Pay"
	pe = get_payment_entry(reference_doctype, reference_name, party_amount=amount)
	pe.payment_type = payment_type
	pe.posting_date = bt.date
	pe.reference_no = bt.name
	pe.reference_date = bt.date
	pe.paid_amount = amount
	pe.received_amount = amount
	pe.insert(ignore_permissions=True)
	pe.submit()

	bt.append(
		"payment_entries",
		{
			"payment_document": "Payment Entry",
			"payment_entry": pe.name,
			"allocated_amount": amount,
		},
	)
	bt.save(ignore_permissions=True)
	frappe.db.commit()


# --- Card / rules-engine branch (Bank Matching Rule) ---


def _setting(settings, field: str, default):
	"""Read a Fio Settings field, falling back to `default` when unset/empty."""
	val = getattr(settings, field, None)
	return val if val not in (None, "") else default


def _load_rules() -> list[dict]:
	"""Enabled Bank Matching Rules, cheapest fields only, priority-ordered."""
	return frappe.get_all(
		"Bank Matching Rule",
		filters={"enabled": 1},
		fields=["name", "pattern", "rule_type", "supplier", "je_account", "remark_template", "priority"],
		order_by="priority asc",
	)


def _reconcile_by_rules(bt, settings) -> ReconcileResult | None:
	"""Apply the first matching Bank Matching Rule to a no-counterparty BT.

	Returns a ReconcileResult when a rule handled the BT, or None to fall through to the
	remaining reconcile rules (no rule matched, or an auto_je rule was gated off).
	"""
	description = bt.description or ""
	rule = select_rule(_load_rules(), description)
	if not rule:
		return None

	rule_type = rule.get("rule_type")
	if rule_type == "ignore":
		# Intentional skip — leave Unreconciled, never auto-process. Not an error.
		return ReconcileResult()
	if rule_type == "merchant_supplier":
		return _reconcile_card_merchant(bt, settings, rule)
	if rule_type == "auto_je":
		if not _setting(settings, "auto_journal_entries", 1):
			return None  # feature gated off → let the remaining rules try
		return _reconcile_auto_je(bt, settings, rule)
	return None


def _reconcile_card_merchant(bt, settings, rule) -> ReconcileResult:
	"""Match a card payment to a single open Purchase Invoice of the rule's supplier."""
	supplier = rule.get("supplier")
	if not supplier:
		return ReconcileResult(unresolved=1)

	withdrawal = float(bt.withdrawal)
	parsed = parse_original_amount(bt.description or "")
	orig_amount, orig_currency = parsed if parsed else (None, None)

	w_from, w_to = card_window(getdate(bt.date))
	candidates = frappe.get_all(
		"Purchase Invoice",
		filters={
			"supplier": supplier,
			"docstatus": 1,
			"outstanding_amount": (">", 0),
			"company": settings.company,
			"bill_date": ("between", [w_from, w_to]),
		},
		fields=["name", "outstanding_amount", "original_amount", "original_currency"],
	)

	fx_tol = float(_setting(settings, "fx_tolerance_percent", DEFAULT_FX_TOLERANCE_PERCENT))
	match = pick_card_candidate(
		candidates, withdrawal, orig_amount, orig_currency, fx_tolerance_percent=fx_tol
	)
	if not match:
		frappe.logger("erpnext_banking").debug(
			f"Card match for BT={bt.name} supplier={supplier}: "
			f"{len(candidates)} candidate(s), no unique match — skipping"
		)
		return ReconcileResult(unresolved=1)

	try:
		_create_card_payment_entry(bt, match, withdrawal, settings)
		return ReconcileResult(matched=1)
	except Exception:
		frappe.log_error(f"Card payment reconcile failed BT={bt.name}", "erpnext_banking")
		return ReconcileResult(errors=1)


def _create_card_payment_entry(bt, pi: dict, withdrawal: float, settings) -> None:
	"""Create + submit a Payment Entry for a card charge and link it to the BT.

	Allocates the PI outstanding to the invoice and, when the bank withdrawal differs
	(EUR/USD FX spread), books the difference to the exchange loss/gain account via a
	`deductions` row so the Payment Entry balances.
	"""
	from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry

	allocated = float(pi["outstanding_amount"])
	pe = get_payment_entry("Purchase Invoice", pi["name"], party_amount=allocated)
	pe.payment_type = "Pay"
	pe.posting_date = bt.date
	pe.reference_no = bt.name
	pe.reference_date = bt.date

	gl = frappe.db.get_value("Bank Account", settings.bank_account, "account")
	if gl:
		pe.paid_from = gl
	pe.paid_amount = withdrawal
	pe.received_amount = withdrawal
	for ref in pe.references:
		ref.allocated_amount = allocated

	ded = compute_fx_deduction(
		withdrawal,
		allocated,
		loss_account=_setting(settings, "fx_loss_account", DEFAULT_FX_LOSS_ACCOUNT),
		gain_account=_setting(settings, "fx_gain_account", DEFAULT_FX_GAIN_ACCOUNT),
	)
	if ded:
		pe.append(
			"deductions",
			{
				"account": ded["account"],
				"cost_center": _setting(settings, "default_cost_center", DEFAULT_COST_CENTER),
				"amount": ded["amount"],
			},
		)

	pe.insert(ignore_permissions=True)
	pe.submit()

	bt.append(
		"payment_entries",
		{
			"payment_document": "Payment Entry",
			"payment_entry": pe.name,
			"allocated_amount": withdrawal,
		},
	)
	bt.save(ignore_permissions=True)
	frappe.db.commit()


def _reconcile_auto_je(bt, settings, rule) -> ReconcileResult:
	"""Book a no-document withdrawal (salary, bank fee) straight to an account via a
	Journal Entry, then link it to the BT."""
	try:
		_create_auto_je(bt, settings, rule)
		return ReconcileResult(matched=1)
	except Exception:
		frappe.log_error(f"Auto Journal Entry failed BT={bt.name}", "erpnext_banking")
		return ReconcileResult(errors=1)


def _create_auto_je(bt, settings, rule) -> None:
	amount = float(bt.withdrawal)
	payload = journal_entry_payload(
		company=settings.company,
		posting_date=bt.date,
		amount=amount,
		debit_account=rule["je_account"],
		credit_account=_setting(settings, "je_contra_account", DEFAULT_JE_CONTRA_ACCOUNT),
		cost_center=_setting(settings, "default_cost_center", DEFAULT_COST_CENTER),
		remark=build_je_remark(rule.get("remark_template"), bt.description),
		reference_no=(bt.reference_number or bt.name),
		reference_date=bt.date,
	)
	je = frappe.get_doc(payload)
	je.insert(ignore_permissions=True)
	je.submit()

	bt.append(
		"payment_entries",
		{
			"payment_document": "Journal Entry",
			"payment_entry": je.name,
			"allocated_amount": amount,
		},
	)
	bt.save(ignore_permissions=True)
	frappe.db.commit()


# --- Scheduler entries and doc_events handlers ---


def scheduled_rematch() -> None:
	"""Cron entry — for each enabled provider with auto_reconcile_*, re-match BT in window."""
	for provider in iter_enabled_providers():
		try:
			settings = frappe.get_single(provider.settings_doctype)
			if not (settings.auto_reconcile_incoming or settings.auto_reconcile_outgoing):
				continue
			_rematch_provider(provider, settings)
		except Exception:
			frappe.log_error(
				f"scheduled_rematch failed for provider={provider.name}",
				"erpnext_banking",
			)


def reconcile_for_provider(provider_name: str) -> dict:
	"""Manual trigger from Fio Settings button. Same logic as scheduled_rematch but one provider."""
	for provider in iter_enabled_providers():
		if provider.name != provider_name:
			continue
		settings = frappe.get_single(provider.settings_doctype)
		return _rematch_provider(provider, settings).to_dict()
	return ReconcileResult().to_dict()


def _rematch_provider(provider, settings) -> ReconcileResult:
	from frappe.utils import add_days
	from frappe.utils import today as _today

	totals = ReconcileResult()

	# Pass 1: link Unreconciled BTs to ALREADY-POSTED vouchers (Payment Entry / Journal
	# Entry) that hit the bank account but were never reconciled against the feed. This
	# runs FIRST so the invoice pass below never creates a duplicate Payment Entry for a
	# payment that is already booked. Links only — it cannot double-pay.
	try:
		totals.linked += _match_existing_payments(settings)
	except Exception:
		frappe.log_error("match_existing_payments failed", "erpnext_banking")

	window_start = add_days(_today(), -int(settings.reconcile_window_days or 90))
	candidates = frappe.get_all(
		"Bank Transaction",
		filters={
			"bank_account": settings.bank_account,
			"status": "Unreconciled",  # re-queried after pass 1, so linked BTs drop out
			"date": (">=", window_start),
			"docstatus": 1,
		},
		order_by="date asc",  # explicit — v16 default switched from `modified` to `creation`
		pluck="name",
	)

	for name in candidates:
		try:
			r = reconcile_one(name)
			totals.matched += r.matched
			totals.unresolved += r.unresolved
			totals.errors += r.errors
		except Exception:
			totals.errors += 1
			frappe.log_error(
				f"reconcile_one failed for BT={name}",
				"erpnext_banking",
			)
	return totals


def _existing_voucher_pools(gl: str, company: str):
	"""Build (outgoing, incoming) candidate pools of unreconciled vouchers on the bank GL.

	A voucher is a candidate only if it posts to the bank account `gl` and has no
	`clearance_date` (i.e. it was never reconciled against the bank feed). Each candidate
	is a dict: {key, doctype, name, amount, date}. `key` is unique per voucher so a voucher
	can be consumed by at most one Bank Transaction.
	"""
	from frappe.utils import getdate

	out: list[dict] = []
	inc: list[dict] = []

	pes = frappe.get_all(
		"Payment Entry",
		filters={"docstatus": 1, "clearance_date": ["is", "not set"], "company": company},
		fields=[
			"name", "posting_date", "payment_type",
			"paid_amount", "received_amount", "paid_from", "paid_to",
		],
	)
	for p in pes:
		d = getdate(p.posting_date)
		if p.payment_type == "Pay" and p.paid_from == gl:
			out.append({"key": ("PE", p.name), "doctype": "Payment Entry", "name": p.name,
						"amount": float(p.paid_amount or 0), "date": d})
		elif p.payment_type == "Receive" and p.paid_to == gl:
			inc.append({"key": ("PE", p.name), "doctype": "Payment Entry", "name": p.name,
						"amount": float(p.received_amount or p.paid_amount or 0), "date": d})

	je_meta = {
		j.name: j.posting_date
		for j in frappe.get_all(
			"Journal Entry",
			filters={"docstatus": 1, "clearance_date": ["is", "not set"], "company": company},
			fields=["name", "posting_date"],
		)
	}
	if je_meta:
		lines = frappe.get_all(
			"Journal Entry Account",
			filters={"account": gl, "parent": ["in", list(je_meta.keys())]},
			fields=["parent", "debit_in_account_currency", "credit_in_account_currency"],
		)
		for r in lines:
			d = getdate(je_meta[r.parent])
			credit = float(r.credit_in_account_currency or 0)
			debit = float(r.debit_in_account_currency or 0)
			if credit > 0:  # money out of the bank → matches a withdrawal
				out.append({"key": ("JE", r.parent), "doctype": "Journal Entry", "name": r.parent,
							"amount": credit, "date": d})
			elif debit > 0:  # money into the bank → matches a deposit
				inc.append({"key": ("JE", r.parent), "doctype": "Journal Entry", "name": r.parent,
							"amount": debit, "date": d})
	return out, inc


def _match_existing_payments(settings) -> int:
	"""Link Unreconciled BTs to pre-existing unreconciled Payment Entries / Journal Entries
	by exact amount + date proximity + uniqueness. Returns the number of BTs linked.

	Gated by the same auto_reconcile flags as invoice matching: outgoing candidates are
	only considered when `auto_reconcile_outgoing` is on, incoming when
	`auto_reconcile_incoming` is on. Never creates a voucher — only links existing ones via
	the stock Bank Reconciliation Tool, so it cannot double-pay.
	"""
	from frappe.utils import add_days, getdate
	from frappe.utils import today as _today
	from erpnext.accounts.doctype.bank_reconciliation_tool.bank_reconciliation_tool import (
		reconcile_vouchers,
	)

	allow_out = bool(settings.auto_reconcile_outgoing)
	allow_in = bool(settings.auto_reconcile_incoming)
	if not (allow_out or allow_in):
		return 0

	gl = frappe.db.get_value("Bank Account", settings.bank_account, "account")
	if not gl:
		return 0

	window_start = add_days(_today(), -int(settings.reconcile_window_days or 90))
	bts = frappe.get_all(
		"Bank Transaction",
		filters={
			"bank_account": settings.bank_account,
			"status": "Unreconciled",
			"date": (">=", window_start),
			"docstatus": 1,
		},
		fields=["name", "date", "deposit", "withdrawal"],
		order_by="date asc",
	)

	out_pool, in_pool = _existing_voucher_pools(gl, settings.company)
	used: set = set()
	linked = 0
	for bt in bts:
		withdrawal = float(bt.withdrawal or 0)
		deposit = float(bt.deposit or 0)
		if withdrawal > 0 and allow_out:
			pool, amount = out_pool, withdrawal
		elif deposit > 0 and allow_in:
			pool, amount = in_pool, deposit
		else:
			continue
		avail = [c for c in pool if c["key"] not in used]
		match = pick_unique_voucher(amount, getdate(bt.date), avail)
		if not match:
			continue
		try:
			reconcile_vouchers(
				bt.name,
				frappe.as_json([
					{"payment_doctype": match["doctype"], "payment_name": match["name"], "amount": amount}
				]),
			)
			used.add(match["key"])
			linked += 1
		except Exception:
			frappe.log_error(f"existing-payment link failed BT={bt.name}", "erpnext_banking")
	return linked


def on_invoice_submit(doc, method=None):
	"""Hook: on submit of Sales/Purchase Invoice, try to match pending BT by VS.

	NEVER raises — invoice submit must not fail because of reconcile error.
	"""
	try:
		vs = (getattr(doc, "variable_symbol", "") or "").strip()
		if not vs:
			return
		_try_immediate_match_by_vs(vs, doc.doctype)
	except Exception:
		frappe.log_error("on_invoice_submit failed (non-fatal)", "erpnext_banking")


def on_payment_request_submit(doc, method=None):
	"""Hook: on submit of Payment Request, try to match pending BT by VS."""
	try:
		vs = (getattr(doc, "variable_symbol", "") or "").strip()
		if not vs:
			return
		# Payment Request always corresponds to a deposit (incoming)
		_try_immediate_match_by_vs(vs, "Sales Invoice")
	except Exception:
		frappe.log_error("on_payment_request_submit failed (non-fatal)", "erpnext_banking")


def _try_immediate_match_by_vs(vs: str, invoice_doctype: str) -> None:
	"""Find Unreconciled BT(s) with reference_number==vs in the correct direction, reconcile."""
	direction_filter = (
		{"deposit": (">", 0)} if invoice_doctype == "Sales Invoice" else {"withdrawal": (">", 0)}
	)
	candidates = frappe.get_all(
		"Bank Transaction",
		filters={"reference_number": vs, "status": "Unreconciled", "docstatus": 1, **direction_filter},
		pluck="name",
	)
	for name in candidates:
		try:
			reconcile_one(name)
		except Exception:
			frappe.log_error(f"immediate match failed for BT={name}", "erpnext_banking")
