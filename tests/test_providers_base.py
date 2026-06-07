"""Tests for BankProvider abstract base class."""

import pytest


def test_bank_provider_is_abstract():
	from erpnext_banking.providers.base import BankProvider

	with pytest.raises(TypeError):
		BankProvider()  # type: ignore[abstract]


def test_bank_provider_subclass_must_implement_all_methods():
	from erpnext_banking.providers.base import BankProvider

	class Incomplete(BankProvider):
		name = "incomplete"
		settings_doctype = "Incomplete Settings"
		# missing fetch_new, fetch_period, to_bank_transaction, is_enabled

	with pytest.raises(TypeError):
		Incomplete()  # type: ignore[abstract]


def test_bank_provider_complete_subclass_works():
	from erpnext_banking.providers.base import BankProvider

	class Complete(BankProvider):
		name = "complete"
		settings_doctype = "Complete Settings"

		def is_enabled(self):
			return True

		def fetch_new(self):
			return []

		def fetch_period(self, date_from, date_to):
			return []

		def to_bank_transaction(self, raw):
			return {}

	p = Complete()
	assert p.name == "complete"
	assert p.settings_doctype == "Complete Settings"
	assert p.is_enabled() is True
