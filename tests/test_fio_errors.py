"""Tests for Fio error class hierarchy + token masking."""
import pytest


def test_fio_api_error_carries_status_body_endpoint():
	from erpnext_banking.providers.fio.errors import FioApiError
	e = FioApiError(409, "rate limit", "/last/***/transactions.json")
	assert e.status == 409
	assert e.body == "rate limit"
	assert e.endpoint == "/last/***/transactions.json"
	assert "Fio API 409" in str(e)
	assert "rate limit" in str(e)


def test_fio_subclasses():
	from erpnext_banking.providers.fio.errors import (
		FioApiError,
		FioRateLimitError,
		FioPayloadTooLargeError,
		FioInvalidArgsError,
		FioAuthError,
	)
	for cls in (FioRateLimitError, FioPayloadTooLargeError, FioInvalidArgsError, FioAuthError):
		assert issubclass(cls, FioApiError)


def test_mask_replaces_token_in_path():
	from erpnext_banking.providers.fio.errors import mask_token
	token = "a" * 64
	masked = mask_token(f"/last/{token}/transactions.json")
	assert "a" * 40 not in masked
	assert "***" in masked
	assert masked.endswith("/transactions.json")


def test_mask_handles_periods_path():
	from erpnext_banking.providers.fio.errors import mask_token
	token = "b" * 50
	masked = mask_token(f"/periods/{token}/2026-01-01/2026-01-07/transactions.json")
	assert "***" in masked
	assert "2026-01-01" in masked


def test_mask_idempotent_on_already_masked():
	from erpnext_banking.providers.fio.errors import mask_token
	masked = mask_token("/last/***/transactions.json")
	assert masked == "/last/***/transactions.json"


def test_mask_truncates_long_body_in_repr():
	from erpnext_banking.providers.fio.errors import FioApiError
	long_body = "x" * 5000
	e = FioApiError(500, long_body, "/last/***/transactions.json")
	# str() body is truncated to keep error messages readable
	assert len(str(e)) < 500
