"""Pytest config — keeps Frappe out of pure-Python unit tests."""

import sys
from unittest.mock import MagicMock

# Stub out frappe module so importing erpnext_banking modules doesn't blow up
# when frappe isn't installed (CI / local dev without bench).
if "frappe" not in sys.modules:
	frappe_mock = MagicMock()
	sys.modules["frappe"] = frappe_mock

	# `from frappe.utils import x` (used at module load time by sync.py / reconcile.py)
	# needs frappe.utils registered as an importable submodule — attribute access on
	# the frappe MagicMock alone isn't enough for a `from pkg.sub import name` import.
	utils_mock = MagicMock()
	sys.modules["frappe.utils"] = utils_mock
	frappe_mock.utils = utils_mock
