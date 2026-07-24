"""Parsing the original amount + currency out of a Fio card-purchase description."""

from erpnext_banking._card import parse_original_amount

ANTHROPIC = (
	"Zpráva: Nákup: ANTHROPIC* CLAUDE SUB,  548 Market Street PMB 90375, "
	"SAN FRANCISCO, 94104, USA, dne 17.7.2026, částka  90.00 EUR"
)
FACEBK_CZK = (
	"Zpráva: Nákup: FACEBK *ABC123, 4 Grand Canal Square, DUBLIN, IRL, " "dne 3.7.2026, částka  2555.00 CZK"
)
GOOGLE_USD = "Zpráva: Nákup: GOOGLE*CLOUD wHNFHF, dne 1.7.2026, částka  12.34 USD"


def test_parse_eur_double_space():
	assert parse_original_amount(ANTHROPIC) == (90.0, "EUR")


def test_parse_czk_double_space():
	assert parse_original_amount(FACEBK_CZK) == (2555.0, "CZK")


def test_parse_usd():
	assert parse_original_amount(GOOGLE_USD) == (12.34, "USD")


def test_parse_comma_decimal():
	assert parse_original_amount("... částka  2241,84 CZK") == (2241.84, "CZK")


def test_parse_space_thousands_and_comma_decimal():
	assert parse_original_amount("... částka  2 241,84 EUR") == (2241.84, "EUR")


def test_parse_dot_thousands_and_comma_decimal():
	assert parse_original_amount("... částka 1.234,56 EUR") == (1234.56, "EUR")


def test_parse_currency_uppercased():
	amount, ccy = parse_original_amount("částka  10.00 eur".replace("eur", "EUR"))
	assert ccy == "EUR"


def test_parse_case_insensitive_keyword():
	assert parse_original_amount("Částka  10.00 EUR") == (10.0, "EUR")


def test_parse_returns_none_without_token():
	assert parse_original_amount("Zpráva: běžná platba bez částky") is None


def test_parse_returns_none_on_empty():
	assert parse_original_amount("") is None
	assert parse_original_amount(None) is None


def test_parse_picks_amount_currency_pair_only():
	# A stray 3-letter word elsewhere must not be mistaken for the currency.
	desc = "Nákup USA obchod, dne 1.1.2026, částka  49.99 USD"
	assert parse_original_amount(desc) == (49.99, "USD")


def test_parse_large_amount():
	assert parse_original_amount("částka  15000.00 CZK") == (15000.0, "CZK")
