"""Tests for generic with_retries harness."""

from unittest.mock import MagicMock


def test_with_retries_success_first_pass(monkeypatch):
	from erpnext_banking._retry import with_retries

	monkeypatch.setattr("time.sleep", lambda s: None)
	attempt = MagicMock(return_value=True)
	failed = with_retries([1, 2, 3], attempt)
	assert attempt.call_count == 3
	assert failed == []


def test_with_retries_one_round_to_succeed(monkeypatch):
	from erpnext_banking._retry import with_retries

	monkeypatch.setattr("time.sleep", lambda s: None)
	attempts = {1: 0, 2: 0}

	def attempt(item):
		attempts[item] += 1
		if item == 2 and attempts[2] == 1:
			raise RuntimeError("transient")
		return True

	failed = with_retries([1, 2], attempt)
	assert failed == []
	assert attempts[2] == 2  # main + 1 retry


def test_with_retries_gives_up_after_3_rounds(monkeypatch):
	from erpnext_banking._retry import with_retries

	monkeypatch.setattr("time.sleep", lambda s: None)

	def attempt(item):
		raise RuntimeError("permanent")

	failed = with_retries([1], attempt)
	assert len(failed) == 1
	assert failed[0][0] == 1
	assert isinstance(failed[0][1], RuntimeError)


def test_dedup_check_short_circuits_retry(monkeypatch):
	from erpnext_banking._retry import with_retries

	monkeypatch.setattr("time.sleep", lambda s: None)

	calls = {"attempt": 0, "dedup": 0}

	def attempt(item):
		calls["attempt"] += 1
		raise RuntimeError("nope")

	def dedup(item):
		calls["dedup"] += 1
		return True

	failed = with_retries([1], attempt, dedup_check=dedup)
	assert failed == []
	assert calls["dedup"] == 1
	assert calls["attempt"] == 1


def test_backoff_sleeps_called_with_correct_values(monkeypatch):
	from erpnext_banking._retry import with_retries

	sleeps = []
	monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))

	def attempt(item):
		raise RuntimeError("nope")

	with_retries([1], attempt)
	assert sleeps == [1, 3, 9]


def test_custom_rounds_and_backoff(monkeypatch):
	from erpnext_banking._retry import with_retries

	sleeps = []
	monkeypatch.setattr("time.sleep", lambda s: sleeps.append(s))
	with_retries([1], lambda i: (_ for _ in ()).throw(RuntimeError()), rounds=2, backoff=(5, 10))
	assert sleeps == [5, 10]


def test_with_retries_round_aware_callback(monkeypatch):
	"""attempt_fn receives (item, round_no) where round_no=0 is main pass, 1+ is retry."""
	from erpnext_banking._retry import with_retries

	monkeypatch.setattr("time.sleep", lambda s: None)
	calls = []

	def attempt(item, round_no):
		calls.append((item, round_no))
		if round_no == 0 and item == "fail-once":
			raise RuntimeError("transient")
		return True

	failed = with_retries(["ok", "fail-once"], attempt, round_aware=True)
	assert failed == []
	assert ("ok", 0) in calls
	assert ("fail-once", 0) in calls
	assert ("fail-once", 1) in calls
