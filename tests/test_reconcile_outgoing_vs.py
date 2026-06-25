import pathlib

SRC = pathlib.Path(__file__).resolve().parent.parent / "erpnext_banking" / "reconcile.py"


def test_outgoing_matches_on_variable_symbol_not_bill_no():
    text = SRC.read_text()
    outgoing = text.split("def _reconcile_outgoing")[1].split("def _try_pay_and_reconcile")[0]
    assert outgoing.count('"variable_symbol": vs') == 2
    assert '"bill_no": vs' not in outgoing
