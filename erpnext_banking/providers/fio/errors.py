"""Fio API error class hierarchy + token masking for logs."""
import re


_TOKEN_PATTERN = re.compile(r"/[a-zA-Z0-9]{40,}/")


def mask_token(path: str) -> str:
	"""Replace Fio token (40+ alnum chars between slashes) with '***'."""
	return _TOKEN_PATTERN.sub("/***/", path)


class FioApiError(Exception):
	"""Base class for Fio HTTP errors."""

	def __init__(self, status: int, body: str, endpoint: str):
		self.status = status
		self.body = body
		self.endpoint = endpoint
		super().__init__(f"Fio API {status} on {endpoint}: {body[:200]}")


class FioRateLimitError(FioApiError):
	"""HTTP 409 — 1 request per 30 s per token violated."""


class FioPayloadTooLargeError(FioApiError):
	"""HTTP 413 — Fio caps response at 50 000 records."""


class FioInvalidArgsError(FioApiError):
	"""HTTP 422 — bad date range, expired token scope, etc."""


class FioAuthError(FioApiError):
	"""HTTP 500 (Fio uses 500 for invalid token, not 401/403)."""
