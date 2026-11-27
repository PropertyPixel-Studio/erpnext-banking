"""Accrual of one salary slip in Czech double-entry bookkeeping. Pure Python, no frappe.

The slip is booked in its period, the payment later only settles the payable:

    gross wage (also wage compensations)     521 / 331
    insurance withheld from the employee     331 / 336
    advance tax and withholding tax          331 / 342
    tax bonus paid out with the wage         342 / 331
    other deductions (enforcement, alimony)  331 / 379
    insurance paid by the employer           524 / 336
    payment of the net wage (banking part)   331 / 221

Accounts are referred to by role; the real accounts come from JMHZ Settings. After the
accrual the balance of the payable (331) for the employee is exactly the net pay.
"""

from __future__ import annotations

from erpnext_banking.jmhz import components as C

WAGE = "wage"  # 521
PAYABLE = "payable"  # 331
INSURANCE = "insurance"  # 336
TAX = "tax"  # 342
EMPLOYER = "employer"  # 524
DEDUCTIONS = "deductions"  # 379
ROLES = (WAGE, PAYABLE, INSURANCE, TAX, EMPLOYER, DEDUCTIONS)

# deduction component -> account credited (always against the payable)
_WITHHELD = {
	C.SOCIALNI_POJISTENI.name: INSURANCE,
	C.ZDRAVOTNI_POJISTENI.name: INSURANCE,
	C.ZALOHA_NA_DAN.name: TAX,
	C.SRAZKOVA_DAN.name: TAX,
	C.EXEKUCE.name: DEDUCTIONS,
}
# order of the lines in the entry: (role, side)
_ORDER = (
	(WAGE, "debit"),
	(EMPLOYER, "debit"),
	(TAX, "debit"),
	(PAYABLE, "debit"),
	(PAYABLE, "credit"),
	(INSURANCE, "credit"),
	(TAX, "credit"),
	(DEDUCTIONS, "credit"),
)


def accrual_lines(slip_lines: list[tuple[str, float]], employer_insurance: float = 0) -> list[dict]:
	"""Lines of the accrual entry: [{"role", "debit", "credit"}], zero lines left out.

	`slip_lines` are (salary component, amount) of the slip, earnings and deductions.
	A component the JMHZ module does not know raises ValueError: the slip is not one of
	ours and must not be booked by guessing.
	"""
	totals: dict[tuple[str, str], float] = {}

	def post(debit: str, credit: str, amount: float) -> None:
		totals[(debit, "debit")] = totals.get((debit, "debit"), 0) + amount
		totals[(credit, "credit")] = totals.get((credit, "credit"), 0) + amount

	for name, amount in slip_lines:
		component = C.BY_NAME.get(name)
		if component is None:
			raise ValueError(name)
		if not amount:
			continue
		if component is C.DANOVY_BONUS:
			post(TAX, PAYABLE, amount)
		elif component.kind == "earning":
			post(WAGE, PAYABLE, amount)
		else:
			post(PAYABLE, _WITHHELD[name], amount)
	if employer_insurance:
		post(EMPLOYER, INSURANCE, employer_insurance)

	return [
		{
			"role": role,
			"debit": totals[(role, side)] if side == "debit" else 0,
			"credit": totals[(role, side)] if side == "credit" else 0,
		}
		for role, side in _ORDER
		if totals.get((role, side))
	]


def net_payable(lines: list[dict]) -> float:
	"""What the accrual leaves on the payable: the net pay of the slip."""
	return sum(li["credit"] - li["debit"] for li in lines if li["role"] == PAYABLE)
