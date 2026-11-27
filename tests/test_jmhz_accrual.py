"""Accrual of a salary slip in Czech double-entry bookkeeping: the pure part. Made-up numbers."""

import ast
import pathlib

import pytest

from erpnext_banking.jmhz import accrual as A
from erpnext_banking.jmhz import components as C


def by_side(lines):
	return {(li["role"], "D" if li["debit"] else "C"): li["debit"] or li["credit"] for li in lines}


def balanced(lines):
	return sum(li["debit"] for li in lines) == sum(li["credit"] for li in lines)


def test_hpp_vzor():
	# gross 15353, employee SP 1091 and ZP 691, employer SP 3273 and ZP 1382
	slip = [
		("Tarifní mzda", 15353),
		("Sociální pojištění zaměstnanec", 1091),
		("Zdravotní pojištění zaměstnanec", 691),
	]
	lines = A.accrual_lines(slip, employer_insurance=3273 + 1382)
	assert by_side(lines) == {
		("wage", "D"): 15353,  # 521 / 331
		("payable", "C"): 15353,
		("payable", "D"): 1782,  # 331 / 336
		("employer", "D"): 4655,  # 524 / 336
		("insurance", "C"): 1782 + 4655,
	}
	assert balanced(lines)
	assert A.net_payable(lines) == 13571
	assert [li["role"] for li in lines] == ["wage", "employer", "payable", "payable", "insurance"]


def test_vsechny_druhy_radku():
	slip = [
		("Tarifní mzda", 30000),
		("Prémie a odměny pravidelné", 5000),
		("Příplatky", 2000),
		("Náhrada za dovolenou", 3500),
		("Náhrada při DPN", 1500),  # stays a wage expense, the prevailing Czech practice
		("Daňový bonus", 1000),
		("Sociální pojištění zaměstnanec", 2982),
		("Zdravotní pojištění zaměstnanec", 1890),
		("Záloha na daň", 2463),
		("Srážková daň", 100),
		("Exekuce a jiné srážky", 1500),
	]
	lines = A.accrual_lines(slip, employer_insurance=12000)
	assert by_side(lines) == {
		("wage", "D"): 42000,
		("employer", "D"): 12000,
		("tax", "D"): 1000,  # bonus 342 / 331
		("payable", "C"): 43000,
		("payable", "D"): 2982 + 1890 + 2463 + 100 + 1500,
		("insurance", "C"): 2982 + 1890 + 12000,
		("tax", "C"): 2563,  # 331 / 342
		("deductions", "C"): 1500,  # 331 / 379
	}
	assert balanced(lines)
	# the payable ends with exactly the net pay
	assert A.net_payable(lines) == 42000 + 1000 - 2982 - 1890 - 2563 - 1500


def test_dohoda_bez_odvodu():
	lines = A.accrual_lines([("Mzda za práci", 12000)])
	assert by_side(lines) == {("wage", "D"): 12000, ("payable", "C"): 12000}
	assert A.net_payable(lines) == 12000


def test_nulove_radky_a_prazdna_paska():
	assert A.accrual_lines([("Tarifní mzda", 0), ("Záloha na daň", 0)]) == []
	assert A.accrual_lines([]) == []
	assert A.net_payable([]) == 0


def test_neznama_slozka():
	# a slip with a component of another payroll setup is never booked by guessing
	with pytest.raises(ValueError):
		A.accrual_lines([("Basic", 1000)])


def test_kazda_slozka_ma_ucet():
	for component in C.COMPONENTS:
		lines = A.accrual_lines([(component.name, 100)])
		assert balanced(lines) and len(lines) == 2, component.name
		roles = {li["role"] for li in lines}
		assert "payable" in roles and roles <= set(A.ROLES)


def test_bez_importu_frappe():
	path = pathlib.Path(__file__).parents[1] / "erpnext_banking" / "jmhz" / "accrual.py"
	for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
		if isinstance(node, ast.Import):
			assert not any(a.name.split(".")[0] == "frappe" for a in node.names)
		if isinstance(node, ast.ImportFrom):
			assert (node.module or "").split(".")[0] != "frappe"
