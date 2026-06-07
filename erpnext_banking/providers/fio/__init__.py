"""Fio provider implementation."""
from __future__ import annotations

from datetime import date

import frappe

from ..base import BankProvider
from . import mapper
from ._split import fetch_with_escalation
from .client import FioClient


class FioProvider(BankProvider):
	name = "fio"
	settings_doctype = "Fio Settings"

	def __init__(self):
		self._settings_cache = None

	def _settings(self):
		if self._settings_cache is None:
			self._settings_cache = frappe.get_single(self.settings_doctype)
		return self._settings_cache

	def _client(self) -> FioClient:
		settings = self._settings()
		token = settings.get_password("api_token")
		return FioClient(token)

	def is_enabled(self) -> bool:
		try:
			return bool(self._settings().enabled)
		except Exception:
			# Settings doctype may not exist yet (pre-install) — treat as disabled
			return False

	def fetch_new(self) -> list[dict]:
		raw = self._client().fetch_last()
		return _extract(raw)

	def fetch_period(self, date_from: date, date_to: date) -> list[dict]:
		return fetch_with_escalation(self._client(), date_from, date_to)

	def to_bank_transaction(self, raw: dict) -> dict:
		return mapper.to_bank_transaction(raw)

	def extract_party_account(self, raw: dict):
		return mapper.extract_party_account(raw)


def _extract(raw: dict) -> list[dict]:
	"""Unwrap Fio's nested response shape."""
	try:
		return raw["accountStatement"]["transactionList"]["transaction"] or []
	except (KeyError, TypeError):
		return []
