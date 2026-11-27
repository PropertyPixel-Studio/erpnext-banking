"""Unit tests of the data of the full slip (`Salary Slip.jmhz_slip_data`). Synthetic data only."""

import json
from decimal import Decimal

from erpnext_banking.jmhz import components as C
from erpnext_banking.jmhz import pdf_slips, slip_data
from erpnext_banking.jmhz.pdf_slips import split
from erpnext_banking.jmhz.tests.synthetic_pdf import build_pdf, slip_texts, slip_values

NAME = "Zkušební Žofie"


def read(**kw):
	(slip,) = split(build_pdf([slip_texts(798.7, NAME, slip_values(), **kw)]))
	return slip


def rows(block):
	return {r["key"]: r for r in block["rows"]}


def test_rozlozeni_pokryva_vsechna_pole():
	keys = [k for group in slip_data.LAYOUT.values() for k in group]
	assert len(keys) == len(set(keys))
	assert set(keys) == set(pdf_slips.FIELDS) | set(pdf_slips.EXTRA_KEYS)
	assert set(slip_data.LAYOUT) == set(pdf_slips.GROUPS)
	assert set(slip_data.KINDS) == set(keys) == set(slip_data.CAPTIONS)
	assert set(slip_data.KINDS.values()) == {"amount", "quantity", "rate", "text"}
	# a value stays in the block the reader put it in
	for group, group_keys in slip_data.LAYOUT.items():
		assert all(pdf_slips.KEY_GROUPS.get(k, group) == group for k in group_keys)


def test_bloky_z_pdf():
	slip = read(extra={"srazka_vyzivne": 1500, "dovolena_zustatek": "71.5", "dovolena_cerpano": "8.5"})
	data = slip_data.build({}, slip.fields, slip.extra, slip.unread)
	assert data["v"] == 1 and data["jmhz"] == {"rows": []}
	blocks = {b["key"]: b for b in data["pdf"]["blocks"]}
	assert list(blocks) == list(slip_data.LAYOUT)  # reading order
	assert blocks["dovolena"]["title"] == "Dovolená (hod.)"
	assert rows(blocks["dovolena"])["dovolena_zustatek"] == {
		"key": "dovolena_zustatek",
		"label": "Zůstatek",
		"value": 71.5,
		"kind": "quantity",
	}
	assert rows(blocks["srazky"])["srazka_vyzivne"]["value"] == 1500
	# a printed zero is kept: the print format decides what to show
	assert rows(blocks["srazky"])["srazka_sporeni"]["value"] == 0
	assert rows(blocks["mzda"])["hruba_mzda"] == {
		"key": "hruba_mzda",
		"label": "Hrubá mzda",
		"value": 15353,
		"kind": "amount",
	}
	assert rows(blocks["dan"])["socialni_pojisteni_sazba"]["kind"] == "rate"
	assert rows(blocks["vyplata"])["typ_pp"] == {
		"key": "typ_pp",
		"label": "Typ pracovního vztahu",
		"value": "DPP",
		"kind": "text",
	}
	assert data["pdf"]["unread"] == []
	json.dumps(data)  # plain JSON types only
	assert all(isinstance(r["value"], int | float | str) for b in data["pdf"]["blocks"] for r in b["rows"])


def test_neprectene_pole_se_netiskne_jako_nula():
	slip = read(omit=("Výživné:", "Typ PP :"))
	data = slip_data.build(None, slip.fields, slip.extra, slip.unread)
	assert data["jmhz"] is None
	printed = {r["key"] for b in data["pdf"]["blocks"] for r in b["rows"]}
	assert not {"srazka_vyzivne", "typ_pp"} & printed
	assert sorted(data["pdf"]["unread"]) == ["Typ pracovního vztahu", "Výživné"]


def test_udaje_z_jmhz():
	jmhz = {
		"sleva_poplatnik": 2570,
		"sleva_deti": None,  # could not be read: left out
		"prumerny_vydelek": 187.125,
		"druh_cinnosti": "T, 1, K",
		"hodiny_prescas": 6.5,
		"prispevek_sporeni": -300,
	}
	data = slip_data.build(jmhz)
	assert data["pdf"] is None
	got = {r["key"]: (r["label"], r["value"], r["kind"]) for r in data["jmhz"]["rows"]}
	assert got == {
		"sleva_poplatnik": ("Sleva na poplatníka", 2570, "amount"),
		"prumerny_vydelek": ("Průměrný hodinový výdělek", 187.125, "quantity"),
		"druh_cinnosti": ("Druh činnosti", "DPP, pracovní poměr, K", "text"),
		"hodiny_prescas": ("Přesčasové hodiny", 6.5, "quantity"),
		"prispevek_sporeni": ("Příspěvky zaměstnavatele na spoření na stáří celkem", -300, "amount"),
	}
	assert [r["key"] for r in data["jmhz"]["rows"]] == [x.key for x in C.EXTRAS if x.key in got]


def test_bez_udaju():
	# a slip created before the values were read prints the short form
	assert slip_data.build(None) is None
	# a cut read by a version that read only the control fields: no printed-slip blocks
	fields = {k: Decimal(str(v)) for k, v in slip_values().items()}
	assert slip_data.build(None, fields, None) is None
	assert slip_data.build({}, fields, None) == {"v": 1, "pdf": None, "jmhz": {"rows": []}}


def test_druh_cinnosti_popisek():
	assert [C.druh_cinnosti_label(c) for c in ("1", "9", "A", "J", "T", "ZC", "K", "10", "S")] == [
		"pracovní poměr",
		"pracovní poměr",
		"DPČ",
		"DPČ",
		"DPP",
		"DPP",
		"K",
		"10",
		"S",
	]
	assert pdf_slips.TYP_PP_CODES["DPP"] == {"T", "U", "V", "W", "X", "Y", "Z", "ZA", "ZB", "ZC"}
	assert pdf_slips.TYP_PP_CODES["DPČ"] == set("ABCDEFGHIJ")


def test_penize_ridi_jmhz():
	# HR accepted a mismatch: the print still shows the totals of the report
	slip = read()
	control = {k: None for k in pdf_slips.FIELDS} | {
		"hruba_mzda": Decimal(15000),
		"k_vyplate": Decimal("13000"),
	}
	control |= {"mzda": Decimal(1), "hodiny": Decimal(1)}  # detail lines stay as printed
	data = slip_data.build({}, slip.fields, slip.extra, [], control)
	got = {r["key"]: r["value"] for b in data["pdf"]["blocks"] for r in b["rows"]}
	assert (got["hruba_mzda"], got["k_vyplate"]) == (15000, 13000)
	assert (got["mzda"], got["hodiny"]) == (15353, 40.5)
	assert got["cista_mzda"] == 13571  # nothing in the report to take it from


def test_dan_z_hlaseni_bez_pdf():
	control = {"zdanitelna_mzda": Decimal(15353), "dan": Decimal(2310), "hruba_mzda": Decimal(15353)}
	data = slip_data.build({"sleva_poplatnik": 2570}, None, None, None, control)
	assert [(r["key"], r["label"], r["value"]) for r in data["jmhz"]["rows"]] == [
		("zdanitelna_mzda", "Zdanitelná mzda", 15353),
		("dan", "Daň před slevami", 2310),
		("sleva_poplatnik", "Sleva na poplatníka", 2570),
	]
