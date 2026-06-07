"""Provider registry. Add new providers to ALL_PROVIDERS."""
from .base import BankProvider
from .fio import FioProvider

ALL_PROVIDERS: list[type[BankProvider]] = [FioProvider]


def iter_enabled_providers():
	"""Yield instantiated providers whose is_enabled() returns True."""
	for cls in ALL_PROVIDERS:
		p = cls()
		if p.is_enabled():
			yield p
