"""Pytest config — keeps Frappe out of pure-Python unit tests."""

import sys
from unittest.mock import MagicMock

# Stub out frappe module so importing erpnext_banking modules doesn't blow up
# when frappe isn't installed (CI / local dev without bench).
if "frappe" not in sys.modules:
	sys.modules["frappe"] = MagicMock()
