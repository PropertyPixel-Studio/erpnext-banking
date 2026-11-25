"""Unit tests of the JMHZ parser. Synthetic data only, generated at runtime.

All numbers and identifiers are made up. The HPP case uses the public sample
from the MPSV instructions (MH - Pokyny k vyplnění 1.4.14): gross 15353,
SP 1091, ZP 691, advance tax 0 -> net income 13571.
"""

import ast
import pathlib

import pytest

from erpnext_banking.jmhz import components as C
from erpnext_banking.jmhz.model import JmhzParseError
from erpnext_banking.jmhz.parser import MAX_XML_BYTES, parse, pseudonym
from erpnext_banking.jmhz.tests.synthetic import form, hpp, report


def lines(person):
	return {line.component: (line.amount, line.kind) for line in person.lines}


def codes(person):
	return set(person.issue_codes)


# --- basic scenarios ---------------------------------------------------------


def test_hpp_vzor_mpsv():
	r = parse(report(hpp(), souhrn_zal=0, pvpoj_sp=1091), file_name="test.xml")
	assert (r.schema, r.rok, r.mesic, r.typ_podani) == ("1.4", 2026, 2, "R")
	assert r.issues == []
	assert r.file_name == "test.xml"
	assert r.vendor == "Test 1.0"
	assert r.variabilni_symbol == "0000000001"
	(p,) = r.persons
	assert p.issues == []
	assert p.has_income
	assert (p.zuctovano, p.cisty_prijem, p.bonus, p.k_vyplate) == (15353, 13571, 0, 13571)
	assert lines(p) == {
		"Tarifní mzda": (15353, "earning"),
		"Sociální pojištění zaměstnanec": (1091, "deduction"),
		"Zdravotní pojištění zaměstnanec": (691, "deduction"),
	}
	earnings = sum(line.amount for line in p.lines if line.kind == "earning")
	deductions = sum(line.amount for line in p.lines if line.kind == "deduction")
	assert earnings - deductions == p.k_vyplate
	# employer contributions are informative fields, not lines (SPEC D3)
	assert (p.sp_zamestnavatel, p.zp_zamestnavatel) == (1091 * 3, 691 * 2)
	assert p.pseudonym == pseudonym("1111111111")
	assert p.id_ppv == ["9000000001"]


def test_bonus_neni_v_cistem_prijmu():
	# 30000 - SP 2130 - ZP 1350 = 26520; the tax bonus 1000 is paid on top
	r = parse(
		report(
			form(zuct=30000, mzda=30000, sp=2130, zp=1350, bonus=1000, cisty=26520),
			souhrn_zal=0,
			souhrn_bon=1000,
		)
	)
	(p,) = r.persons
	assert p.issues == []
	assert p.k_vyplate == 27520
	assert lines(p)["Daňový bonus"] == (1000, "earning")
	assert lines(p)["Mzda za práci"] == (30000, "earning")


@pytest.mark.parametrize("ver", ["1.4", "1.5"])
def test_srazkova_dan_1_4_a_1_5(ver):
	r = parse(report(form(ver, zuct=10000, mzda=10000, srd=1500, cisty=8500, dny=20, hodiny=80), ver=ver))
	(p,) = r.persons
	assert codes(p) == set()
	assert lines(p)["Srážková daň"] == (1500, "deduction")
	assert p.k_vyplate == 8500
	assert r.schema == ver
	assert ("F07" in r.issue_codes) == (ver == "1.5")
	# 10267 (worked days) exists only in 1.4
	assert ("odpracovane_dny" in p.info) == (ver == "1.4")
	assert p.info["odpracovane_hodiny"] == 80


def test_srazkova_dan_cesta_podle_verze():
	# the 1.5 element in a 1.4 file is not read (and vice versa)
	x = report(form("1.5", zuct=10000, mzda=10000, srd=1500, cisty=8500)).replace(b"/2027", b"/1.0")
	x = x.replace(b"formulareZamestnani", b"formulareOsob").replace(
		b"form:formularZamestnani", b"n1:formularOsoby"
	)
	(p,) = parse(x).persons
	assert "Srážková daň" not in lines(p)
	assert "P01" in codes(p)


def test_dve_ppv_slouceni():
	# summary data only on the primary employment (Pokyny MH 2.4)
	primary = form(
		ppv="9000000001", zuct=14321, mzda=10000, sp=500, cisty=13821, hodiny="40.5", hodiny_dovolena=8
	)
	secondary = form(
		ppv="9000000002",
		primarni=False,
		souhrn=False,
		mzda=0,
		nahrady={"mzdyZuctovane": 4321, "dovolena": 4321},
		hodiny="10.25",
	)
	r = parse(report(primary, secondary, pvpoj_sp=500))
	assert r.issues == []
	(p,) = r.persons
	assert codes(p) == {"P10"}
	assert p.id_ppv == ["9000000001", "9000000002"]
	assert lines(p) == {
		"Mzda za práci": (10000, "earning"),
		"Náhrada za dovolenou": (4321, "earning"),
		"Sociální pojištění zaměstnanec": (500, "deduction"),
	}
	assert p.k_vyplate == 13821
	assert p.info["odpracovane_hodiny"] == 50.75
	assert p.info["hodiny_dovolena"] == 8


def test_nesoulad_a_exekuce():
	(p,) = parse(report(form(zuct=10000, mzda=10000, cisty=9000, exekuce=True))).persons
	assert {"P01", "P02"} <= codes(p)
	assert p.exekuce
	assert all(i.severity == "block" for i in p.issues if i.code in ("P01", "P02"))


def test_nulovy_mesic_a_opravne_podani():
	r = parse(report(form(typ="O"), typ_podani="O"))
	assert "F04" in r.issue_codes
	(p,) = r.persons
	assert not p.has_income
	assert p.lines == []
	assert codes(p) == {"P11"}


def test_nepodporovany_formular():
	(p,) = parse(report(form(kind="jinyPrijem", zuct=100, mzda=100, cisty=100))).persons
	assert "P03" in codes(p)
	assert p.has_income  # never hidden as "Bez příjmu"
	assert "P11" not in codes(p)


def test_neznamy_namespace():
	with pytest.raises(JmhzParseError) as e:
		parse(report(hpp()).replace(b"podani/1.0", b"podani/9.9"))
	assert e.value.code == "F01"


def test_dtd_entity_odmitnuta():
	evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'
	with pytest.raises(JmhzParseError) as e:
		parse(evil)
	assert e.value.code == "F02"


# --- input, validation and edge cases ------------------------------------------


def test_vstup_bajty_bom_crlf():
	plain = report(hpp())
	variant = b"\xef\xbb\xbf" + plain.replace(b"\n", b"\r\n")
	a, b = parse(plain), parse(variant)
	assert lines(a.persons[0]) == lines(b.persons[0])
	assert a.sha256 != b.sha256
	assert parse(bytearray(plain)).persons[0].k_vyplate == 13571
	with pytest.raises(TypeError):
		parse(plain.decode())


def test_dtd_bez_entit_odmitnuta():
	x = report(hpp()).replace(b"?>", b"?><!DOCTYPE jmhz>", 1)
	with pytest.raises(JmhzParseError) as e:
		parse(x)
	assert e.value.code == "F02"


def test_externi_entita_odmitnuta():
	x = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a SYSTEM "file:///etc/passwd">]><x>&a;</x>'
	with pytest.raises(JmhzParseError) as e:
		parse(x)
	assert e.value.code == "F02"


def test_nevalidni_xml():
	with pytest.raises(JmhzParseError) as e:
		parse(b"<n1:jmhz")
	assert e.value.code == "F02"


def test_soubor_nad_5_mb():
	x = report(hpp())
	x = x.replace(b"</n1:jmhz>", b"<!--" + b"x" * (MAX_XML_BYTES - len(x)) + b"--></n1:jmhz>")
	assert len(x) > MAX_XML_BYTES
	with pytest.raises(JmhzParseError) as e:
		parse(x)
	assert e.value.code == "F02"


@pytest.mark.parametrize(
	"old,new",
	[
		(b"<n1:rok>2026</n1:rok>", b""),
		(b"<n1:mesic>2</n1:mesic>", b"<n1:mesic>13</n1:mesic>"),
		(b"<n1:typPodani>R</n1:typPodani>", b"<n1:typPodani>X</n1:typPodani>"),
	],
)
def test_chybejici_hlavicka(old, new):
	x = report(hpp())
	assert old in x
	with pytest.raises(JmhzParseError) as e:
		parse(x.replace(old, new))
	assert e.value.code == "F03"


def test_chybejici_ik_u_dvou_osob_neslucuje():
	r = parse(report(hpp(ik=None, ppv="9000000001"), hpp(ik=None, ppv="9000000002")))
	assert len(r.persons) == 2
	for p in r.persons:
		assert "P06" in codes(p)
		assert p.ik_mpsv == ""
	assert len({p.pseudonym for p in r.persons}) == 2


def test_chybejici_id_ppv():
	(p,) = parse(report(hpp(ppv=None))).persons
	assert "P06" in codes(p)


def test_chybejici_souhrnna_data():
	(p,) = parse(report(hpp(souhrn=False))).persons
	assert "P06" in codes(p)
	assert p.has_income


def test_necelociselna_castka_jen_u_dane_osoby():
	bad = hpp(ik="2222222222", raw_zuct="123.50")
	r = parse(report(hpp(), bad))
	ok, blocked = r.persons
	assert ok.issues == []
	assert "P04" in codes(blocked)
	assert "P01" not in codes(blocked)  # no noise on top of P04


def test_necislo():
	(p,) = parse(report(hpp(raw_zuct="abc"))).persons
	assert "P04" in codes(p)


def test_celociselna_castka_s_desetinnou_teckou():
	(p,) = parse(report(hpp(raw_zuct="15353.00"))).persons
	assert p.issues == []


def test_zaporna_castka():
	(p,) = parse(report(hpp(raw_zuct="-15353"))).persons
	assert "P05" in codes(p)


def test_rozpad_vetsi_nez_mzda():
	(p,) = parse(report(hpp(rozpad={"tarif": 15000, "odmenyPravidelne": 1000}))).persons
	assert "P05" in codes(p)


def test_rozpad_nahrad_vetsi_nez_celkem():
	(p,) = parse(report(hpp(nahrady={"mzdyZuctovane": 100, "dovolena": 200}))).persons
	assert "P05" in codes(p)


def test_zbytek_mzdy_a_nahrad():
	f = form(
		zuct=20000,
		mzda=15000,
		rozpad={"tarif": 10000, "odmenyNepravidelne": 2000, "priplatky": 500},
		nahrady={"mzdyZuctovane": 5000, "dovolena": 3000, "svatky": 1000},
		cisty=20000,
	)
	(p,) = parse(report(f)).persons
	assert p.issues == []
	assert lines(p) == {
		"Tarifní mzda": (10000, "earning"),
		"Prémie a odměny nepravidelné": (2000, "earning"),
		"Příplatky": (500, "earning"),
		"Mzda - ostatní": (2500, "earning"),
		"Náhrada za dovolenou": (3000, "earning"),
		"Náhrada za svátky": (1000, "earning"),
		"Náhrady - ostatní": (1000, "earning"),
	}
	assert [line.component for line in p.lines] == [
		c.name for c in C.COMPONENTS if c.name in lines(p)
	]  # structure order
	assert lines(p).keys() and all(line.amount > 0 for line in p.lines)


def test_ostatni_zuctovane_prijmy():
	(p,) = parse(report(form(zuct=12000, mzda=10000, cisty=12000))).persons
	assert codes(p) == {"P09"}
	assert lines(p)["Ostatní zúčtované příjmy"] == (2000, "earning")
	assert p.has_income


def test_zuctovano_mensi_nez_mzda():
	(p,) = parse(report(form(zuct=9000, mzda=10000, cisty=9000))).persons
	assert "P05" in codes(p)


def test_bezpriznaku_a_jiny_prijem():
	r = parse(report(hpp(), form(kind="jinyPrijem", ppv="9000000002", souhrn=False, mzda=500)))
	(p,) = r.persons
	assert "P03" in codes(p)


def test_osvobozene_prijmy():
	# 10286 includes exempt income 5000, 10344 excludes it
	(p,) = parse(report(form(zuct=20000, osvobozeno=5000, mzda=20000, cisty=15000))).persons
	assert "P07" in codes(p)
	assert "P01" not in codes(p)


def test_srazky_10350_10353():
	(p,) = parse(report(hpp(srazky={"srazkaStrava": 300}))).persons
	assert "P08" in codes(p)


def test_nulove_srazky_10350_10353():
	(p,) = parse(report(hpp(srazky={"srazkaStrava": 0}))).persons
	assert p.issues == []


def test_dve_ppv_hodiny_bez_prumerneho_vydelku():
	a = form(ppv="9000000001", zuct=3000, mzda=1000, cisty=3000, hodiny=10, prumerny_vydelek=150)
	b = form(ppv="9000000002", primarni=False, souhrn=False, mzda=2000, hodiny=20, prumerny_vydelek=200)
	(p,) = parse(report(a, b)).persons
	assert codes(p) == {"P10"}
	assert p.id_ppv == ["9000000001", "9000000002"]
	assert p.info == {"odpracovane_hodiny": 30.0}  # P-5: average earnings are not summed
	assert lines(p) == {"Mzda za práci": (3000, "earning")}


def test_typ_formulare_v_radnem_podani():
	(p,) = parse(report(hpp(typ="O"))).persons
	assert "P12" in codes(p)


def test_typ_formulare_v_opravnem_podani():
	r = parse(report(hpp(typ="O"), typ_podani="O"))
	assert "P12" not in codes(r.persons[0])


def test_rocni_zuctovani_vysvetleni():
	# 10344 includes the refund 700 from the annual tax settlement
	(p,) = parse(report(hpp(cisty=14271, preplatek=700))).persons
	(issue,) = p.issues
	assert issue.code == "P01"
	assert "ročního zúčtování" in issue.message


def test_kontroly_souhrnu_podani():
	r = parse(report(hpp(), souhrn_zal=5, pvpoj_sp=7))
	assert {"F05", "F06"} <= set(r.issue_codes)
	assert all(i.severity == "warn" for i in r.issues)
	r = parse(report(hpp(zal=0), souhrn_zal=0, pvpoj_sp=1091))
	assert r.issues == []


def test_formular_bez_dat():
	x = report(hpp()).replace(
		b"</n1:formulareOsob>",
		b"<n1:formularOsoby><n1:hlavicka><n1:typFormulare>S</n1:typFormulare></n1:hlavicka></n1:formularOsoby></n1:formulareOsob>",
	)
	r = parse(x)
	assert "F09" in r.issue_codes
	assert len(r.persons) == 1


def test_kontejner_formularu_podle_verze():
	# 1.5 renamed formulareOsob/formularOsoby to formulareZamestnani/formularZamestnani
	(p,) = parse(report(hpp("1.5"), ver="1.5")).persons
	assert p.k_vyplate == 13571
	old_layout = report(hpp("1.4"), ver="1.4").replace(b"podani/1.0", b"podani/2027")
	assert parse(old_layout).persons == []


def test_zpravy_neobsahuji_data():
	ik, ppv = "8765432109", "7654321098765"
	f = form(
		ik=ik,
		ppv=ppv,
		typ="O",
		zuct=98761,
		mzda=98753,
		rozpad={"tarif": 99991},
		nahrady={"mzdyZuctovane": 12347, "dovolena": 23459},
		sp=7777,
		zp=6666,
		zal=5555,
		cisty=11113,
		osvobozeno=4444,
		exekuce=True,
		srazky={"srazkaStrava": 3333},
	)
	bad = form(ik="9988776655", ppv="5544332211", raw_zuct="98761.5", mzda=98753)
	r = parse(report(f, bad, souhrn_zal=55551, pvpoj_sp=77771))
	messages = [i.message for i in r.issues] + [i.message for p in r.persons for i in p.issues]
	assert len(messages) >= 6
	data = [ik, ppv, "9988776655", "5544332211", "98761", "98753", "99991", "12347", "23459", "7777", "6666"]
	data += ["5555", "11113", "4444", "3333", "55551", "77771"]
	for m in messages:
		for d in data:
			assert d not in m, (d, m)


def test_has_income_a_blokace():
	# zero month without issues -> P11; a blocked zero month is never "without income"
	(p,) = parse(report(form())).persons
	assert codes(p) == {"P11"} and not p.has_income
	(p,) = parse(report(form(typ="O"))).persons
	assert "P12" in codes(p) and p.has_income and "P11" not in codes(p)


# --- components and module boundaries ----------------------------------------


def test_slozky():
	assert len(C.COMPONENTS) == 19
	assert len({c.name for c in C.COMPONENTS}) == 19
	assert len({c.abbr for c in C.COMPONENTS}) == 19
	assert len(C.EARNINGS) == 14 and len(C.DEDUCTIONS) == 5
	assert C.MANUAL_DEDUCTION.name == "Exekuce a jiné srážky"
	assert all(c.component_type in ("Earning", "Deduction") for c in C.COMPONENTS)


@pytest.mark.parametrize("module", ["parser", "model", "components"])
def test_bez_importu_frappe(module):
	path = pathlib.Path(__file__).parents[1] / "erpnext_banking" / "jmhz" / f"{module}.py"
	tree = ast.parse(path.read_text(encoding="utf-8"))
	for node in ast.walk(tree):
		if isinstance(node, ast.Import):
			assert not any(a.name.split(".")[0] == "frappe" for a in node.names)
		if isinstance(node, ast.ImportFrom):
			assert (node.module or "").split(".")[0] != "frappe"
