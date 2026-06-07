"""Generic 3-round retry harness for per-item commit operations.

Used by sync.py (Bank Transaction inserts) and reconcile.py (Payment Entry creates).
Each item gets one main attempt + up to N retries with exponential backoff.
"""
from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import Any


def with_retries(
	items: Iterable[Any],
	attempt_fn: Callable[[Any], bool],
	*,
	dedup_check: Callable[[Any], bool] | None = None,
	rounds: int = 3,
	backoff: tuple[int, ...] = (1, 3, 9),
) -> list[tuple[Any, BaseException]]:
	"""Try `attempt_fn(item)` for each item, retry failures up to `rounds` times.

	Args:
	    items: iterable of items to process.
	    attempt_fn: called per item; should return True on success or raise on failure.
	    dedup_check: optional — called before retry; if returns True, item is treated as
	                 already succeeded (e.g. partial commit from previous attempt).
	    rounds: number of retry rounds after the main pass (default 3).
	    backoff: sleep seconds per round, indexed by attempt-1.

	Returns:
	    List of (item, last_exception) for items still failing after all rounds.
	"""
	if len(backoff) < rounds:
		raise ValueError(f"backoff has {len(backoff)} values, need at least {rounds}")

	failed: list[tuple[Any, BaseException]] = []

	# Main pass
	for item in items:
		try:
			attempt_fn(item)
		except BaseException as e:
			failed.append((item, e))

	# Retry rounds
	for attempt in range(1, rounds + 1):
		if not failed:
			break
		time.sleep(backoff[attempt - 1])
		next_failed: list[tuple[Any, BaseException]] = []
		for item, _ in failed:
			if dedup_check and dedup_check(item):
				continue
			try:
				attempt_fn(item)
			except BaseException as e:
				next_failed.append((item, e))
		failed = next_failed

	return failed
