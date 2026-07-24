"""Bank Matching Rule pattern matching + rule selection (pure logic)."""

from erpnext_banking._card import rule_matches, select_rule


def _rule(pattern, rule_type="merchant_supplier", priority=100, enabled=1, **kw):
	return {"pattern": pattern, "rule_type": rule_type, "priority": priority, "enabled": enabled, **kw}


# --- rule_matches --------------------------------------------------------------------


def test_substring_match_case_insensitive():
	assert rule_matches("anthropic", "Zpráva: Nákup: ANTHROPIC* CLAUDE SUB, ...")
	assert rule_matches("ANTHROPIC", "... anthropic* claude sub ...")


def test_substring_no_match():
	assert not rule_matches("HETZNER", "Zpráva: Nákup: ANTHROPIC* CLAUDE SUB")


def test_google_pattern_covers_workspace_and_cloud():
	assert rule_matches("GOOGLE", "Nákup: GOOGLE*WORKSPACE PROPI")
	assert rule_matches("GOOGLE", "Nákup: Google CLOUD wHNFHF")


def test_salary_counterparty_pattern():
	assert rule_matches("2371741018/3030", "Protiúčet: 2371741018/3030\nZpráva: mzda")


def test_empty_pattern_never_matches():
	assert not rule_matches("", "anything")


def test_regex_pattern():
	assert rule_matches(r"re:google\*(workspace|cloud)", "Nákup: GOOGLE*WORKSPACE PROPI")
	assert not rule_matches(r"re:google\*(workspace|cloud)", "Nákup: GOOGLE*ADS")


def test_invalid_regex_is_safe():
	assert rule_matches("re:[unclosed", "whatever") is False


# --- select_rule ---------------------------------------------------------------------


def test_select_returns_first_matching():
	rules = [_rule("ANTHROPIC", supplier="A"), _rule("HETZNER", supplier="H")]
	assert select_rule(rules, "Nákup: HETZNER ONLINE GMBH")["supplier"] == "H"


def test_select_none_when_no_match():
	rules = [_rule("ANTHROPIC"), _rule("HETZNER")]
	assert select_rule(rules, "Nákup: SOMETHING ELSE") is None


def test_select_priority_lower_wins():
	# Both patterns match; the lower-priority number is evaluated first.
	rules = [
		_rule("Nákup", rule_type="ignore", priority=200),
		_rule("ANTHROPIC", rule_type="merchant_supplier", priority=10, supplier="A"),
	]
	chosen = select_rule(rules, "Nákup: ANTHROPIC* CLAUDE SUB")
	assert chosen["rule_type"] == "merchant_supplier"


def test_select_ignore_rule_can_win():
	rules = [
		_rule("REFUND", rule_type="ignore", priority=1),
		_rule("ANTHROPIC", rule_type="merchant_supplier", priority=100, supplier="A"),
	]
	chosen = select_rule(rules, "REFUND ANTHROPIC vrácení")
	assert chosen["rule_type"] == "ignore"


def test_select_skips_disabled_rule():
	rules = [
		_rule("ANTHROPIC", priority=10, enabled=0, supplier="disabled"),
		_rule("ANTHROPIC", priority=20, enabled=1, supplier="enabled"),
	]
	assert select_rule(rules, "Nákup ANTHROPIC")["supplier"] == "enabled"


def test_select_stable_on_equal_priority():
	rules = [_rule("A", priority=100), _rule("ANTHROPIC", priority=100, supplier="x")]
	# Both match "ANTHROPIC" description; tie broken deterministically by pattern.
	chosen = select_rule(rules, "ANTHROPIC")
	assert chosen["pattern"] == "A"
