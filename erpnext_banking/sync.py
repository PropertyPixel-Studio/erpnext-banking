"""Sync orchestrator — fetch transactions from a provider into Bank Transaction.

Provider-agnostic: takes any BankProvider implementation. Runs in two modes:
- "incremental": uses provider.fetch_new() (Fio /last/, pointer advances on Fio side)
- "lookback":    uses provider.fetch_period(today - lookback_days, today)
                 (Fio /periods/, pointer NOT advanced — safety net for dropped runs)

Per-transaction commit. Failed inserts go through with_retries (3 rounds, 1/3/9 s).
"""

from __future__ import annotations

import time
import traceback
from datetime import date as _date

import frappe
from frappe.utils import now_datetime, today

from ._retry import with_retries
from .providers import iter_enabled_providers
from .providers.base import BankProvider
from .providers.fio.errors import FioApiError


class _RunContext:
	"""Mutable per-run state — passed to retry attempt closures."""

	def __init__(self, provider, settings, log, dry_run):
		self.provider = provider
		self.settings = settings
		self.log = log
		self.dry_run = dry_run


def run_for_provider(
	provider: BankProvider, *, trigger: str, mode: str = "incremental", dry_run: bool = False
) -> dict:
	"""Run one sync cycle for one provider. Returns summary dict."""
	settings = frappe.get_single(provider.settings_doctype)
	start = time.monotonic()
	log = frappe.get_doc(
		{
			"doctype": "Bank Sync Log",
			"sync_dt": now_datetime(),
			"provider": provider.name,
			"trigger": trigger,
			"bank_account": settings.bank_account,
			"status": "Running",
		}
	)
	log.insert(ignore_permissions=True)
	frappe.db.commit()

	ctx = _RunContext(provider, settings, log, dry_run)

	try:
		raw_list = _fetch(ctx, mode)
		log.fetched = len(raw_list)

		failed = with_retries(
			raw_list,
			lambda raw, round_no: _attempt_insert(ctx, raw, round_no),
			dedup_check=lambda raw: _is_already_inserted(ctx, raw),
			round_aware=True,
		)

		log.retried = len(failed) + (log.retried_succeeded or 0)

		if failed:
			log.status = "Partial"
			log.error_log = _format_failures(failed, ctx)
		else:
			log.status = "Success"

	except FioApiError as e:
		log.status = "Error"
		log.error_log = traceback.format_exc()
		if not dry_run:
			settings.last_sync_status = "Error"
			settings.save(ignore_permissions=True)
		if settings.notify_on_error:
			_notify(settings, provider, e)
		log.duration_ms = int((time.monotonic() - start) * 1000)
		log.save(ignore_permissions=True)
		frappe.db.commit()
		raise

	if not dry_run:
		settings.last_sync_dt = now_datetime()
		settings.last_sync_status = log.status
		settings.save(ignore_permissions=True)

	log.duration_ms = int((time.monotonic() - start) * 1000)
	log.save(ignore_permissions=True)
	frappe.db.commit()

	return {
		"log_name": log.name,
		"status": log.status,
		"fetched": log.fetched or 0,
		"created": log.created or 0,
		"skipped_duplicates": log.skipped_duplicates or 0,
		"retried": log.retried or 0,
		"retried_succeeded": log.retried_succeeded or 0,
	}


def _fetch(ctx: _RunContext, mode: str) -> list[dict]:
	if mode == "incremental":
		ctx.log.endpoint = "/last/"
		return ctx.provider.fetch_new()
	elif mode == "lookback":
		from_date = frappe.utils.add_days(today(), -int(ctx.settings.lookback_days or 7))
		to_date = today()
		ctx.log.endpoint = f"/periods/{from_date}/{to_date}/"
		return ctx.provider.fetch_period(_to_date(from_date), _to_date(to_date))
	else:
		raise ValueError(f"Unknown sync mode: {mode}")


def _to_date(d) -> _date:
	if isinstance(d, _date):
		return d
	return frappe.utils.getdate(d)


def _attempt_insert(ctx: _RunContext, raw: dict, round_no: int = 0) -> bool:
	"""Map → dedup-check → insert+submit BT. Mutates ctx.log counters."""
	kwargs = ctx.provider.to_bank_transaction(raw)
	if _is_already_inserted(ctx, raw):
		if round_no == 0:
			ctx.log.skipped_duplicates = (ctx.log.skipped_duplicates or 0) + 1
		else:
			# dedup hit in retry round means a previous attempt actually committed
			ctx.log.created = (ctx.log.created or 0) + 1
			ctx.log.retried_succeeded = (ctx.log.retried_succeeded or 0) + 1
		return True

	if ctx.dry_run:
		ctx.log.created = (ctx.log.created or 0) + 1
		return True

	bt = frappe.get_doc(
		{
			"doctype": "Bank Transaction",
			**kwargs,
			"bank_account": ctx.settings.bank_account,
			"company": ctx.settings.company,
			"status": "Unreconciled",
		}
	)
	_try_attach_supplier(bt, raw, ctx.provider)
	bt.insert(ignore_permissions=True)
	bt.submit()
	if not ctx.dry_run:
		ctx.settings.last_transaction_id = str(kwargs["transaction_id"])
	frappe.db.commit()

	ctx.log.created = (ctx.log.created or 0) + 1
	if round_no > 0:
		ctx.log.retried_succeeded = (ctx.log.retried_succeeded or 0) + 1
	return True


def _is_already_inserted(ctx: _RunContext, raw: dict) -> bool:
	tx_id = str(ctx.provider.to_bank_transaction(raw)["transaction_id"])
	return bool(
		frappe.db.exists(
			"Bank Transaction",
			{"transaction_id": tx_id, "bank_account": ctx.settings.bank_account},
		)
	)


def _try_attach_supplier(bt, raw: dict, provider) -> None:
	"""Silent-fail supplier identification from counterparty bank account."""
	try:
		if not hasattr(provider, "extract_party_account"):
			return
		acc, bank = provider.extract_party_account(raw)
		if not acc:
			return
		supplier = frappe.db.get_value(
			"Bank Account",
			{"account_number": acc, "branch_code": bank, "party_type": "Supplier"},
			"party",
		)
		if not supplier:
			# Fallback: try iban-based match if account_number doesn't hit
			supplier = frappe.db.get_value(
				"Bank Account",
				{"bank_account_no": acc, "party_type": "Supplier"},
				"party",
			)
		if supplier:
			bt.party_type = "Supplier"
			bt.party = supplier
	except Exception:
		frappe.log_error("Supplier identification failed", "erpnext_banking")


def _format_failures(failed: list, ctx: _RunContext) -> str:
	"""Build human-readable error_log from failed retry items."""
	lines = []
	for raw, exc in failed:
		try:
			tx_id = ctx.provider.to_bank_transaction(raw).get("transaction_id", "?")
		except Exception:
			tx_id = "?"
		lines.append(f"--- transaction_id={tx_id}, error={type(exc).__name__}: {exc}")
		lines.append(traceback.format_exception_only(type(exc), exc)[-1].rstrip())
	return "\n".join(lines)


def _notify(settings, provider, error: BaseException) -> None:
	"""Send error notification, rate-limited 1× / 24 h per (provider, error_class).

	Uses a frappe.cache key as throttle marker.
	"""
	key = f"erpnext_banking:notify:{provider.name}:{type(error).__name__}"
	cache = frappe.cache()
	if cache.get_value(key):
		return
	cache.set_value(key, "1", expires_in_sec=24 * 60 * 60)

	try:
		frappe.sendmail(
			recipients=[settings.notify_email],
			subject=f"[ERPNext Banking] {provider.name} sync error: {type(error).__name__}",
			message=(
				f"Provider: {provider.name}\n"
				f"Error: {type(error).__name__}\n"
				f"Detail: {error}\n\n"
				f"Check Bank Sync Log for full traceback.\n"
				f"(This alert is rate-limited to 1× per 24 h per error class.)"
			),
		)
	except Exception:
		frappe.log_error("Notification send failed", "erpnext_banking")


# --- Scheduler entrypoints ---


def scheduled_sync():
	"""Cron entry: incremental /last/ fetch for every enabled provider."""
	for provider in iter_enabled_providers():
		try:
			run_for_provider(provider, trigger="Scheduled", mode="incremental")
		except Exception:
			frappe.log_error(
				f"scheduled_sync failed for provider={provider.name}",
				"erpnext_banking",
			)


def scheduled_lookback():
	"""Cron entry: /periods/ safety-net fetch for every enabled provider."""
	for provider in iter_enabled_providers():
		try:
			run_for_provider(provider, trigger="Lookback", mode="lookback")
		except Exception:
			frappe.log_error(
				f"scheduled_lookback failed for provider={provider.name}",
				"erpnext_banking",
			)
