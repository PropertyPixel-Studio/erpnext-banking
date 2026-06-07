"""Provider registry. Add new providers to ALL_PROVIDERS."""
from .base import BankProvider

ALL_PROVIDERS: list[type[BankProvider]] = []
# FioProvider is appended in providers/fio/__init__.py (registered in Task 13)


def iter_enabled_providers():
	"""Yield instantiated providers whose is_enabled() returns True."""
	for cls in ALL_PROVIDERS:
		p = cls()
		if p.is_enabled():
			yield p
