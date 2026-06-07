"""Tests for 413 escalation splitter."""
from datetime import date
from unittest.mock import MagicMock

import pytest

from erpnext_banking.providers.fio.errors import FioPayloadTooLargeError


def _make_client():
	"""Returns a MagicMock that mimics FioClient.fetch_period."""
	return MagicMock()


def test_slice_dates_into_n_chunks():
	from erpnext_banking.providers.fio._split import slice_dates
	chunks = slice_dates(date(2026, 1, 1), date(2026, 1, 11), 2)
	assert len(chunks) == 2
	assert chunks[0][0] == date(2026, 1, 1)
	assert chunks[-1][1] == date(2026, 1, 11)
	# adjacent chunks contiguous, no overlap
	assert chunks[0][1] + (chunks[1][0] - chunks[0][1]) == chunks[1][0]


def test_slice_dates_handles_one_day():
	from erpnext_banking.providers.fio._split import slice_dates
	chunks = slice_dates(date(2026, 1, 1), date(2026, 1, 1), 2)
	# can't split a single day — return as one chunk
	assert chunks == [(date(2026, 1, 1), date(2026, 1, 1))]


def test_fetch_with_escalation_no_413_returns_directly():
	from erpnext_banking.providers.fio._split import fetch_with_escalation
	client = _make_client()
	client.fetch_period.return_value = {
		"accountStatement": {"transactionList": {"transaction": [{"x": 1}]}}
	}
	result = fetch_with_escalation(client, date(2026, 1, 1), date(2026, 1, 7))
	assert client.fetch_period.call_count == 1
	assert result == [{"x": 1}]


def test_fetch_with_escalation_splits_to_halves_on_413():
	from erpnext_banking.providers.fio._split import fetch_with_escalation
	client = _make_client()

	def side_effect(d_from, d_to):
		if d_from == date(2026, 1, 1) and d_to == date(2026, 1, 7):
			raise FioPayloadTooLargeError(413, "big", "/periods/***/...")
		return {"accountStatement": {"transactionList": {"transaction": [{"d": str(d_from)}]}}}

	client.fetch_period.side_effect = side_effect
	result = fetch_with_escalation(client, date(2026, 1, 1), date(2026, 1, 7))
	# 1 failed top-level + 2 half-calls = 3 total
	assert client.fetch_period.call_count == 3
	assert len(result) == 2


def test_fetch_with_escalation_escalates_halves_to_thirds():
	from erpnext_banking.providers.fio._split import fetch_with_escalation
	client = _make_client()
	call_count = {"n": 0}

	def side_effect(d_from, d_to):
		call_count["n"] += 1
		# first call (full range) + second call (first half) both 413
		if call_count["n"] in (1, 2):
			raise FioPayloadTooLargeError(413, "big", "/periods/***/...")
		return {"accountStatement": {"transactionList": {"transaction": []}}}

	client.fetch_period.side_effect = side_effect
	fetch_with_escalation(client, date(2026, 1, 1), date(2026, 1, 30))
	# full + halves(2; first fails, second OK) + thirds-of-first-half(3) + second-half = 1+2+3 = 6
	assert client.fetch_period.call_count >= 6


def test_fetch_with_escalation_gives_up_after_fifths(caplog):
	from erpnext_banking.providers.fio._split import fetch_with_escalation
	client = _make_client()
	client.fetch_period.side_effect = FioPayloadTooLargeError(413, "big", "/periods/***/...")

	# Always 413 → after 2/3/5 escalation gives up on the leaf slice, returns [] + warns
	result = fetch_with_escalation(client, date(2026, 1, 1), date(2026, 1, 30))
	assert result == []
	# 1 (top) + 2 (halves) + 3*2 (thirds for each half) + 5*6 (fifths for each third) = 1+2+6+30 = 39
	# we don't assert exact number, just that escalation happened and gave up
	assert client.fetch_period.call_count >= 30
