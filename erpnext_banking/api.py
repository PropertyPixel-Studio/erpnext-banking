"""Whitelisted endpoints called from Fio Settings buttons.

All endpoints require System Manager or Accounts Manager role.
Manual sync triggers include a 35-second throttle (Fio rate limit guard).
"""

from __future__ import annotations

from datetime import datetime

import frappe
from frappe import _

from .reconcile import reconcile_for_provider
from .sync import run_for_provider

THROTTLE_SECONDS = 35


def _require_role():
	frappe.only_for(["System Manager", "Accounts Manager"])


def _check_throttle(provider_name: str):
	"""Server-side guard against manual button spam — Fio's 1/30s rate limit."""
	settings = frappe.get_single(f"{provider_name.title()} Settings")
	last = settings.last_sync_dt
	if not last:
		return
	last_dt = frappe.utils.get_datetime(last)
	delta = (datetime.now() - last_dt).total_seconds()
	if delta < THROTTLE_SECONDS:
		retry_after = int(THROTTLE_SECONDS - delta) + 1
		frappe.throw(
			_("Rate limited: please wait {0} more seconds before triggering Fio again.").format(retry_after),
			title=_("Throttled"),
		)


def _get_provider(provider_name: str):
	from .providers import ALL_PROVIDERS

	for cls in ALL_PROVIDERS:
		if cls.name == provider_name:
			return cls()
	frappe.throw(_("Unknown provider: {0}").format(provider_name))


@frappe.whitelist()
def trigger_sync_fio(dry_run: bool | int = False) -> dict:
	"""Manual /last/ sync for Fio. Dry-run skips inserts and doesn't touch Settings state."""
	_require_role()
	dry_run = bool(int(dry_run))
	if not dry_run:
		_check_throttle("fio")
	provider = _get_provider("fio")
	return run_for_provider(
		provider,
		trigger="Manual" if not dry_run else "Dry-run",
		mode="incremental",
		dry_run=dry_run,
	)


@frappe.whitelist()
def trigger_lookback_fio(days: int | None = None) -> dict:
	"""Manual /periods/ lookback sync for Fio."""
	_require_role()
	_check_throttle("fio")
	provider = _get_provider("fio")
	settings = frappe.get_single("Fio Settings")
	if days is not None:
		settings.lookback_days = int(days)
	return run_for_provider(provider, trigger="Lookback", mode="lookback", dry_run=False)


@frappe.whitelist()
def trigger_reconcile_fio() -> dict:
	"""Manual re-match over sliding window for Fio."""
	_require_role()
	return reconcile_for_provider("fio")


@frappe.whitelist()
def fio_reset_pointer(transaction_id: str) -> dict:
	"""Recovery — manually set Fio pointer to a specific transaction ID.

	Only System Manager. No UI button (intentional — dangerous if misused).
	"""
	frappe.only_for(["System Manager"])
	from .providers.fio import FioProvider

	provider = FioProvider()
	provider._client().set_last_id(str(transaction_id))
	return {"status": "OK", "transaction_id": transaction_id}
