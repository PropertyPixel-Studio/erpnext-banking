import pathlib, re

SRC = pathlib.Path(__file__).resolve().parent.parent / "erpnext_banking" / "reconcile.py"


def test_outgoing_matches_on_variable_symbol_not_bill_no():
    text = SRC.read_text()
    # Rule 1 and Rule 3 must filter Purchase Invoice by variable_symbol == vs
    assert '"variable_symbol": vs' in text
    # The old bill_no-keyed VS lookup must be gone from the outgoing matcher
    outgoing = text.split("def _reconcile_outgoing")[1].split("def _try_pay_and_reconcile")[0]
    assert '"bill_no": vs' not in outgoing
