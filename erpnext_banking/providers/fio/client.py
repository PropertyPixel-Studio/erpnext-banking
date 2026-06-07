"""HTTP client for Fio Banka REST API.

API reference: https://www.fio.cz/docs/cz/API_Bankovnictvi.pdf
Rate limit: 1 request per 30 s per token (we don't retry inside this class —
upper layer decides retry strategy by error type).
"""

from datetime import date

import requests

from .errors import (
	FioApiError,
	FioAuthError,
	FioInvalidArgsError,
	FioPayloadTooLargeError,
	FioRateLimitError,
	mask_token,
)


class FioClient:
	BASE = "https://fioapi.fio.cz/v1/rest"

	def __init__(self, token: str, *, timeout: int = 30):
		self._token = token
		self._timeout = timeout

	def fetch_last(self) -> dict:
		"""GET /last/{token}/transactions.json — incremental; Fio moves pointer."""
		return self._get(f"/last/{self._token}/transactions.json")

	def fetch_period(self, date_from: date, date_to: date) -> dict:
		"""GET /periods/{token}/{from}/{to}/transactions.json — does NOT move pointer."""
		path = (
			f"/periods/{self._token}" f"/{date_from.isoformat()}/{date_to.isoformat()}" "/transactions.json"
		)
		return self._get(path)

	def set_last_id(self, transaction_id: str) -> None:
		"""GET /set-last-id/{token}/{id}/ — recovery tool, not used in normal flow."""
		self._get(f"/set-last-id/{self._token}/{transaction_id}/", expect_json=False)
		return None

	def _get(self, path: str, *, expect_json: bool = True):
		url = self.BASE + path
		masked = mask_token(path)
		try:
			r = requests.get(url, timeout=self._timeout, headers={"Accept": "application/json"})
		except requests.RequestException as e:
			raise FioApiError(0, str(e), masked) from e

		if r.status_code == 200:
			return r.json() if expect_json else None

		if r.status_code == 409:
			raise FioRateLimitError(409, r.text, masked)
		if r.status_code == 413:
			raise FioPayloadTooLargeError(413, r.text, masked)
		if r.status_code == 422:
			raise FioInvalidArgsError(422, r.text, masked)
		if r.status_code == 500:
			raise FioAuthError(500, r.text, masked)
		raise FioApiError(r.status_code, r.text, masked)
