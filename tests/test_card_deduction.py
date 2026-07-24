"""FX write-off deduction sign / account selection for the card Payment Entry."""

from erpnext_banking._card import compute_fx_deduction

LOSS = "563 - Kurzové ztráty - PXG"
GAIN = "663 - Kurzové zisky - PXG"


def _ded(bt, allocated):
	return compute_fx_deduction(bt, allocated, loss_account=LOSS, gain_account=GAIN)


def test_no_deduction_when_equal():
	assert _ded(2177.55, 2177.55) is None


def test_no_deduction_within_rounding():
	assert _ded(2177.554, 2177.55) is None


def test_loss_when_bank_charged_more():
	# Bank withdrew 2241.84 for a 2177.55 invoice → 64.29 exchange loss (debit 563).
	ded = _ded(2241.84, 2177.55)
	assert ded["account"] == LOSS
	assert ded["amount"] == 64.29


def test_gain_when_bank_charged_less():
	# Bank withdrew 2100.00 for a 2177.55 invoice → 77.55 exchange gain (credit 663).
	ded = _ded(2100.00, 2177.55)
	assert ded["account"] == GAIN
	assert ded["amount"] == -77.55  # negative amount → credit to the income account


def test_deduction_zeroes_difference_amount():
	# ERPNext (Pay): difference = base_paid - allocated - sum(deductions). The deduction
	# amount must equal base_paid - allocated so difference nets to zero.
	bt, allocated = 2241.84, 2177.55
	ded = _ded(bt, allocated)
	assert round(bt - allocated - ded["amount"], 2) == 0.0


def test_deduction_zeroes_difference_amount_gain_side():
	bt, allocated = 2100.00, 2177.55
	ded = _ded(bt, allocated)
	assert round(bt - allocated - ded["amount"], 2) == 0.0


def test_small_loss():
	ded = _ded(100.02, 100.0)
	assert ded["account"] == LOSS
	assert ded["amount"] == 0.02
