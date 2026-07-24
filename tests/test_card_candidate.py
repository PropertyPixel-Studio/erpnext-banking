"""Candidate selection for card payments: CZK exact, EUR via original_amount, EUR FX
fallback, ambiguity, and the matching window."""

from datetime import date

from erpnext_banking._card import (
	card_window,
	fx_amount_matches,
	pick_card_candidate,
)


def _pi(name, outstanding, original_amount=None, original_currency=None):
	return {
		"name": name,
		"outstanding_amount": outstanding,
		"original_amount": original_amount,
		"original_currency": original_currency,
	}


# --- fx_amount_matches ---------------------------------------------------------------


def test_fx_match_within_tolerance():
	# Real sample: bank 2241.84 CZK vs invoice 2177.55 CZK → 2.95% < 4%.
	assert fx_amount_matches(2241.84, 2177.55, 4.0)


def test_fx_no_match_beyond_tolerance():
	assert not fx_amount_matches(2241.84, 2177.55, 2.0)


def test_fx_exact_is_match():
	assert fx_amount_matches(100.0, 100.0, 4.0)


def test_fx_zero_outstanding_never_matches():
	assert not fx_amount_matches(100.0, 0.0, 4.0)


# --- CZK card (path i) ---------------------------------------------------------------


def test_czk_exact_single_candidate():
	cands = [_pi("PI-1", 2555.0)]
	match = pick_card_candidate(cands, 2555.0, 2555.0, "CZK")
	assert match["name"] == "PI-1"


def test_czk_within_one_crown():
	cands = [_pi("PI-1", 2555.40)]
	assert pick_card_candidate(cands, 2554.50, 2555.0, "CZK")["name"] == "PI-1"


def test_czk_no_match_when_amount_off():
	cands = [_pi("PI-1", 2600.0)]
	assert pick_card_candidate(cands, 2555.0, 2555.0, "CZK") is None


# --- EUR card via PI original_amount (path ii) --------------------------------------


def test_eur_matches_original_amount():
	cands = [_pi("PI-EUR", 2177.55, original_amount=90.0, original_currency="EUR")]
	# Bank CZK differs from outstanding, but original 90.00 EUR matches exactly.
	match = pick_card_candidate(cands, 2241.84, 90.0, "EUR")
	assert match["name"] == "PI-EUR"


def test_eur_original_currency_mismatch_no_match():
	cands = [_pi("PI-USD", 2177.55, original_amount=90.0, original_currency="USD")]
	assert pick_card_candidate(cands, 2241.84, 90.0, "EUR") is None


def test_eur_original_amount_mismatch_no_fallback():
	# PI records original 95.00 EUR but the charge was 90.00 EUR → different invoice,
	# and we do NOT silently fall back to FX tolerance when the field is present.
	cands = [_pi("PI-EUR", 2177.55, original_amount=95.0, original_currency="EUR")]
	assert pick_card_candidate(cands, 2241.84, 90.0, "EUR") is None


# --- EUR card FX fallback when PI has no original fields (path iii) ------------------


def test_eur_fallback_fx_tolerance_matches():
	cands = [_pi("PI-1", 2177.55)]  # no original_amount
	match = pick_card_candidate(cands, 2241.84, 90.0, "EUR", fx_tolerance_percent=4.0)
	assert match["name"] == "PI-1"


def test_eur_fallback_beyond_tolerance_no_match():
	cands = [_pi("PI-1", 2177.55)]
	assert pick_card_candidate(cands, 2241.84, 90.0, "EUR", fx_tolerance_percent=2.0) is None


def test_eur_fallback_when_parse_failed():
	# parse returned None → orig_amount/currency None → still FX-fallback on CZK amounts.
	cands = [_pi("PI-1", 2177.55)]
	match = pick_card_candidate(cands, 2241.84, None, None, fx_tolerance_percent=4.0)
	assert match["name"] == "PI-1"


# --- ambiguity -----------------------------------------------------------------------


def test_two_candidates_fx_match_is_ambiguous():
	cands = [_pi("PI-1", 2177.55), _pi("PI-2", 2200.0)]
	assert pick_card_candidate(cands, 2241.84, 90.0, "EUR", fx_tolerance_percent=4.0) is None


def test_two_candidates_but_only_one_original_match():
	cands = [
		_pi("PI-1", 2177.55, original_amount=90.0, original_currency="EUR"),
		_pi("PI-2", 2200.0, original_amount=91.0, original_currency="EUR"),
	]
	assert pick_card_candidate(cands, 2241.84, 90.0, "EUR")["name"] == "PI-1"


def test_no_candidates_returns_none():
	assert pick_card_candidate([], 2241.84, 90.0, "EUR") is None


# --- window --------------------------------------------------------------------------


def test_card_window_default_span():
	d_from, d_to = card_window(date(2026, 7, 17))
	assert d_from == date(2026, 6, 12)  # -35
	assert d_to == date(2026, 7, 24)  # +7


def test_card_window_configurable():
	d_from, d_to = card_window(date(2026, 7, 17), days_back=10, days_forward=2)
	assert d_from == date(2026, 7, 7)
	assert d_to == date(2026, 7, 19)
