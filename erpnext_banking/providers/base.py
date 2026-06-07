"""Provider-agnostic abstract base for bank integrations."""

from abc import ABC, abstractmethod
from datetime import date


class BankProvider(ABC):
	"""Each bank implements this. Drop-in: add new subclass + Settings doctype + register."""

	name: str = ""
	settings_doctype: str = ""

	@abstractmethod
	def is_enabled(self) -> bool:
		"""Return True if provider is configured and switched on."""

	@abstractmethod
	def fetch_new(self) -> list[dict]:
		"""Incremental fetch (using provider's server-side pointer)."""

	@abstractmethod
	def fetch_period(self, date_from: date, date_to: date) -> list[dict]:
		"""Date-range fetch (used for lookback / 413 split / recovery)."""

	@abstractmethod
	def to_bank_transaction(self, raw: dict) -> dict:
		"""Map provider-specific raw record to Bank Transaction kwargs.

		Required keys in returned dict:
		    transaction_id, date, deposit OR withdrawal (one), currency,
		    reference_number, description
		"""
