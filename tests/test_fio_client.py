"""Tests for FioClient HTTP layer."""

from datetime import date

import pytest

TOKEN = "T" * 64


def test_client_initializes():
	from erpnext_banking.providers.fio.client import FioClient

	c = FioClient(TOKEN)
	assert c._token == TOKEN
	assert c._timeout == 30


def test_fetch_last_returns_json_on_200(requests_mock):
	from erpnext_banking.providers.fio.client import FioClient

	requests_mock.get(
		f"https://fioapi.fio.cz/v1/rest/last/{TOKEN}/transactions.json",
		json={"accountStatement": {"transactionList": {"transaction": []}}},
	)
	c = FioClient(TOKEN)
	assert c.fetch_last() == {"accountStatement": {"transactionList": {"transaction": []}}}


def test_fetch_period_constructs_url(requests_mock):
	from erpnext_banking.providers.fio.client import FioClient

	requests_mock.get(
		f"https://fioapi.fio.cz/v1/rest/periods/{TOKEN}/2026-01-01/2026-01-07/transactions.json",
		json={"ok": True},
	)
	c = FioClient(TOKEN)
	r = c.fetch_period(date(2026, 1, 1), date(2026, 1, 7))
	assert r == {"ok": True}


def test_409_raises_rate_limit(requests_mock):
	from erpnext_banking.providers.fio.client import FioClient
	from erpnext_banking.providers.fio.errors import FioRateLimitError

	requests_mock.get(
		f"https://fioapi.fio.cz/v1/rest/last/{TOKEN}/transactions.json",
		status_code=409,
		text="rate limited",
	)
	with pytest.raises(FioRateLimitError) as exc:
		FioClient(TOKEN).fetch_last()
	assert exc.value.status == 409
	assert TOKEN not in exc.value.endpoint  # masked


def test_413_raises_payload_too_large(requests_mock):
	from erpnext_banking.providers.fio.client import FioClient
	from erpnext_banking.providers.fio.errors import FioPayloadTooLargeError

	requests_mock.get(
		f"https://fioapi.fio.cz/v1/rest/last/{TOKEN}/transactions.json",
		status_code=413,
		text="too many",
	)
	with pytest.raises(FioPayloadTooLargeError):
		FioClient(TOKEN).fetch_last()


def test_422_raises_invalid_args(requests_mock):
	from erpnext_banking.providers.fio.client import FioClient
	from erpnext_banking.providers.fio.errors import FioInvalidArgsError

	requests_mock.get(
		f"https://fioapi.fio.cz/v1/rest/last/{TOKEN}/transactions.json",
		status_code=422,
		text="bad",
	)
	with pytest.raises(FioInvalidArgsError):
		FioClient(TOKEN).fetch_last()


def test_500_raises_auth(requests_mock):
	from erpnext_banking.providers.fio.client import FioClient
	from erpnext_banking.providers.fio.errors import FioAuthError

	requests_mock.get(
		f"https://fioapi.fio.cz/v1/rest/last/{TOKEN}/transactions.json",
		status_code=500,
		text="invalid token",
	)
	with pytest.raises(FioAuthError):
		FioClient(TOKEN).fetch_last()


def test_network_error_raises_generic(requests_mock):
	import requests

	from erpnext_banking.providers.fio.client import FioClient
	from erpnext_banking.providers.fio.errors import FioApiError

	requests_mock.get(
		f"https://fioapi.fio.cz/v1/rest/last/{TOKEN}/transactions.json",
		exc=requests.ConnectionError("boom"),
	)
	with pytest.raises(FioApiError) as exc:
		FioClient(TOKEN).fetch_last()
	assert exc.value.status == 0


def test_set_last_id_returns_none_on_200(requests_mock):
	from erpnext_banking.providers.fio.client import FioClient

	requests_mock.get(
		f"https://fioapi.fio.cz/v1/rest/set-last-id/{TOKEN}/12345/",
		text="OK",
	)
	c = FioClient(TOKEN)
	assert c.set_last_id("12345") is None
