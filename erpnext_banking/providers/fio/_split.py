"""413 escalation splitter for Fio /periods/ requests.

Escalation: full → halves(2) → thirds(3) → fifths(5). If a leaf still 413, give up
on that slice, log warning, return whatever we collected. Sync layer marks the
Bank Sync Log as Partial.
"""

import logging
from datetime import date, timedelta

from .errors import FioPayloadTooLargeError

logger = logging.getLogger(__name__)


def slice_dates(date_from: date, date_to: date, parts: int) -> list[tuple[date, date]]:
	"""Split [from, to] into `parts` contiguous, non-overlapping date ranges."""
	if date_from == date_to or parts <= 1:
		return [(date_from, date_to)]

	total_days = (date_to - date_from).days
	if total_days < parts:
		# can't make `parts` chunks of at least 1 day each — degrade gracefully
		parts = max(1, total_days)

	step = total_days / parts
	chunks: list[tuple[date, date]] = []
	cursor = date_from
	for i in range(parts):
		end = date_to if i == parts - 1 else date_from + timedelta(days=int(round(step * (i + 1))))
		# ensure non-overlap: next chunk starts day after end
		chunks.append((cursor, end))
		cursor = end + timedelta(days=1)
	return chunks


def fetch_with_escalation(client, date_from: date, date_to: date) -> list[dict]:
	"""Fetch /periods/ with 413 escalation 2 → 3 → 5. Returns flat transaction list."""
	return _fetch_recursive(client, date_from, date_to, escalation_level=0)


_ESCALATION_PARTS = {0: 2, 1: 3, 2: 5}


def _fetch_recursive(client, date_from: date, date_to: date, escalation_level: int) -> list[dict]:
	try:
		raw = client.fetch_period(date_from, date_to)
		return _extract_transactions(raw)
	except FioPayloadTooLargeError:
		if escalation_level >= 3:
			# exhausted 2 → 3 → 5 escalation; give up on this slice
			logger.warning(
				"Fio slice %s–%s > 50k records even at /5 split, skipping",
				date_from,
				date_to,
			)
			return []
		parts = _ESCALATION_PARTS[escalation_level]
		out: list[dict] = []
		for c_from, c_to in slice_dates(date_from, date_to, parts):
			out.extend(_fetch_recursive(client, c_from, c_to, escalation_level + 1))
		return out


def _extract_transactions(raw: dict) -> list[dict]:
	"""Unwrap Fio's nested response shape: accountStatement.transactionList.transaction."""
	try:
		return raw["accountStatement"]["transactionList"]["transaction"] or []
	except (KeyError, TypeError):
		return []
