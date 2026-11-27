"""Unit tests of splitting printed payroll slips. Synthetic PDFs generated at runtime."""

import io
from decimal import Decimal

import pytest
from pypdf import PdfReader

from erpnext_banking.jmhz import pdf_slips
from erpnext_banking.jmhz.pdf_slips import (
	PdfSlipError,
	check_sums,
	extra_from_json,
	extra_mismatches,
	extra_to_json,
	parse_number,
	split,
)
from erpnext_banking.jmhz.tests.synthetic_pdf import (
	SEPARATOR_ABOVE_ANCHOR,
	advance_tax,
	anchor_y,
	build_pdf,
	report_pdf,
	slip_extra,
	slip_texts,
	slip_values,
)

A = ("Zkušební Žofie", slip_values(gross=15353, sp=1091, zp=691, hours=40.5, days=5))
B = (
	"Vymyšlený Čeněk",
	slip_values(gross=23456, sp=1665, zp=1056, tax=1234, hours=98.25, days=12, vacation=2000),
)
C = ("Řehoř Ďáblík", slip_values(gross=4000, sp=0, zp=0, hours=16, days=2, bonus=500))


def test_dve_pasky_na_strane():
	slips = split(report_pdf([A, B]))
	assert [(s.page, s.index) for s in slips] == [(1, 1), (1, 2)]
	assert [s.name for s in slips] == [A[0], B[0]]
	assert slips[0].fields == {k: Decimal(str(v)) for k, v in A[1].items()}
	assert slips[1].fields == {k: Decimal(str(v)) for k, v in B[1].items()}
	assert slips[1].fields["hodiny"] == Decimal("98.25")
	# the cuts do not overlap and each is a one-page PDF cropped to its box
	top_bottom, top_top = slips[0].box
	low_bottom, low_top = slips[1].box
	assert low_top <= top_bottom
	for s in slips:
		page = PdfReader(io.BytesIO(s.pdf)).pages
		assert len(page) == 1
		assert float(page[0].mediabox.bottom) == pytest.approx(s.box[0])
		assert float(page[0].mediabox.top) == pytest.approx(s.box[1])


SEPARATOR = round(anchor_y(1) + SEPARATOR_ABOVE_ANCHOR, 2)


def test_rez_po_teckovane_care():
	top, low = split(report_pdf([A, B]))
	assert top.box[0] == pytest.approx(SEPARATOR)
	assert low.box == (0.0, pytest.approx(SEPARATOR))


def test_teckovana_cara_pres_transformaci():
	top, low = split(report_pdf([A, B], shift=-15.0))
	assert top.box[0] == pytest.approx(SEPARATOR)
	assert low.box[1] == pytest.approx(SEPARATOR)


@pytest.mark.parametrize("separator", [None, "solid"])
def test_bez_teckovane_cary(separator):
	# no dotted line (a plain line does not count): the boundary falls back to just above the next anchor
	top, low = split(report_pdf([A, B], separator=separator))
	assert top.box[0] == pytest.approx(anchor_y(1) + pdf_slips.TOP_MARGIN)
	assert low.box[1] == pytest.approx(top.box[0])
	assert top.fields == {k: Decimal(str(v)) for k, v in A[1].items()}


@pytest.mark.parametrize("dx", [-10.6, 8.0])
def test_posunute_rozlozeni(dx):
	# reports from different months are shifted by several points
	pdf = build_pdf([slip_texts(809.9, *A, dx=dx) + slip_texts(410.2, *B, dx=dx)])
	top, low = split(pdf)
	assert (top.name, low.name) == (A[0], B[0])
	assert top.fields == {k: Decimal(str(v)) for k, v in A[1].items()}
	assert low.fields == {k: Decimal(str(v)) for k, v in B[1].items()}


def test_tri_pasky_na_dvou_stranach():
	slips = split(report_pdf([A, B, C]))
	assert [(s.page, s.index) for s in slips] == [(1, 1), (1, 2), (2, 1)]
	assert slips[2].name == C[0]
	assert slips[2].fields["k_vyplate"] == Decimal("4500")  # tax bonus paid on top


def test_jedna_paska_na_strane():
	(slip,) = split(report_pdf([A], per_page=1))
	assert slip.box == (0.0, pytest.approx(818.7))


def test_strana_bez_pasky():
	pdf = build_pdf([slip_texts(798.7, *A), [(50, 700, "Jiný dokument")]])
	with pytest.raises(PdfSlipError, match="Strana 2"):
		split(pdf)


def test_chybejici_pole():
	texts = [t for t in slip_texts(798.7, *A) if t[2] != "ČISTÁ MZDA:"]
	with pytest.raises(PdfSlipError) as e:
		split(build_pdf([texts]))
	message = str(e.value)
	assert "ČISTÁ MZDA" in message
	# no names or amounts in messages
	assert "Zkušební" not in message and "13" not in message.replace("Strana 1", "")


@pytest.mark.parametrize("content", [b"", b"%PDF-1.4 broken", b"not a pdf at all"])
def test_poskozene_pdf(content):
	with pytest.raises(PdfSlipError):
		split(content)


def test_prilis_mnoho_stran():
	pages = [slip_texts(798.7, *A) for _ in range(pdf_slips.MAX_PAGES + 1)]
	with pytest.raises(PdfSlipError, match="stran"):
		split(build_pdf(pages))


def test_prilis_velky_soubor():
	with pytest.raises(PdfSlipError, match="10 MB"):
		split(b"%PDF-1.4\n" + b"0" * pdf_slips.MAX_PDF_BYTES)


def test_vstup_jen_bajty():
	with pytest.raises(TypeError):
		split("text")


@pytest.mark.parametrize(
	"text,value",
	[
		("1 234", Decimal("1234")),
		("1 234", Decimal("1234")),
		("12,50", Decimal("12.50")),
		("9 999,-", Decimal("9999")),
		("0", Decimal("0")),
		("9,50%", None),
		("Mzda:", None),
		("12-34", None),
	],
)
def test_cisla(text, value):
	assert parse_number(text) == value


# --- the whole slip ------------------------------------------------------------

BLANK = {"pridanitelne_polozky", "ostatni_nezdanitelne"}


def D(value) -> Decimal:
	return Decimal(str(value))


def one(texts) -> pdf_slips.PdfSlip:
	(slip,) = split(build_pdf([texts]))
	return slip


def test_cela_paska():
	a, b = split(report_pdf([A, B]))
	for slip, (_name, values) in ((a, A), (b, B)):
		assert slip.unread == []
		assert set(slip.extra) == set(pdf_slips.EXTRA_KEYS) - BLANK
		assert slip.period == (2, 2026)
		assert check_sums(slip.fields, slip.extra, 2026) == []
		assert slip.extra["dilci_zaklad_dane"] == D(values["hruba_mzda"])
		assert slip.extra["zamestnavatel_sp"] == D(values["socialni_pojisteni"] * 3)
		# a printed zero is a value, not an unread field
		assert slip.extra["srazka_vyzivne"] == 0 and slip.extra["prescas_hodiny"] == 0
	assert a.extra["zaokrouhleny_zaklad_dane"] == D(15400)
	assert a.extra["sleva_zakladni"] == D(advance_tax(15353))
	assert b.extra["sleva_zakladni"] == D(advance_tax(23456) - 1234)
	assert (a.extra["socialni_pojisteni_sazba"], a.extra["zdravotni_pojisteni_sazba"]) == (D("7.1"), D("4.5"))
	assert (a.extra["zamestnavatel_sp_sazba"], a.extra["zamestnavatel_zp_sazba"]) == (D("24.8"), D(9))
	assert (a.extra["prumer_nahrady"], a.extra["prumer_dpn"]) == (D("187.13"), D("168.42"))
	assert (a.extra["dovolena_narok"], a.extra["dovolena_cerpano"], a.extra["dovolena_zustatek"]) == (
		80,
		8,
		72,
	)
	texts = {k: a.extra[k] for k in pdf_slips.TEXT_KEYS}
	assert texts == {
		"stredisko": "1",
		"osobni_cislo": "7",
		"mzdovy_vymer": "200,00/hod.",
		"typ_pp": "DPP",
		"pojistovna": "111",
		"zpusob_vyplaty": "Převodem na účet",
		"ucet": "000000-0000000000/0000",
	}


# every value of the form different and non-zero, with thousands and decimals
FULL = {
	key: 1000 + 37 * i
	for i, key in enumerate(k for k in pdf_slips.EXTRA_KEYS if k not in pdf_slips.TEXT_KEYS)
}
FULL |= {
	k: f"{12 + i}.{25 + i}"
	for i, k in enumerate(
		sp.key
		for sp in pdf_slips.SPECS
		if sp.kind in ("quantity", "rate") and not sp.key.startswith("dovolena_")
	)
}
FULL |= {
	"dovolena_dny": "1.5",
	"dovolena_hodiny": "12",
	"absence_pracovni_dny": "2",
	"absence_kalendarni_dny": "3",
}
FULL |= {
	f"dovolena_{k}": v
	for k, v in zip(
		("minuly_rok", "narok", "kraceno", "cerpano", "zustatek"), ("4.125", "160", "8", "40.5", "115.625")
	)
}
FULL |= {"stredisko": "12", "osobni_cislo": "345", "mzdovy_vymer": "1 234,50/měs.", "typ_pp": "DPČ"}
FULL |= {"pojistovna": "205", "zpusob_vyplaty": "Převodem na účet", "ucet": "123-4567890/0100"}


@pytest.mark.parametrize("pad,dx", [(0.0, 0.0), (21.0, 0.0), (0.0, -10.6), (25.0, 8.0)])
def test_cela_paska_nenulove_hodnoty(pad, dx):
	# reports differ: the whole layout is shifted and some pad the values with spaces
	slip = one(slip_texts(798.7, *A, extra=FULL, pad=pad, dx=dx))
	assert slip.unread == []
	assert set(slip.extra) == set(pdf_slips.EXTRA_KEYS)
	for key, value in FULL.items():
		expected = value if key in pdf_slips.TEXT_KEYS else D(value)
		assert slip.extra[key] == expected, key
	assert slip.fields == {k: D(v) for k, v in A[1].items()}


def test_dve_pasky_se_nemichaji():
	low_extra = {
		"srazka_vyzivne": 4321,
		"dovolena_zustatek": "55.5",
		"typ_pp": "DPČ",
		"ucet": "19-123456/0800",
	}
	top, low = split(report_pdf([A, (B[0], B[1], low_extra)]))
	assert top.extra["srazka_vyzivne"] == 0 and low.extra["srazka_vyzivne"] == 4321
	assert (top.extra["typ_pp"], low.extra["typ_pp"]) == ("DPP", "DPČ")
	assert low.extra["ucet"] == "19-123456/0800"
	assert low.extra["dovolena_zustatek"] == D("55.5")


def test_necitelne_pole_neni_nula():
	# a missing label: the value is unread, never zero, and the slip is still accepted
	slip = one(slip_texts(798.7, *A, omit=("Zdr.poj. :", "srazka_vyzivne")))
	assert sorted(slip.unread) == ["srazka_vyzivne", "zamestnavatel_zp", "zamestnavatel_zp_sazba"]
	assert not set(slip.unread) & set(slip.extra)
	assert slip.extra["zamestnavatel_sp"] == D(1091 * 3)
	# a rule with an unread value is skipped, the others still hold
	assert check_sums(slip.fields, slip.extra, 2026) == []


def test_popisek_dvakrat_nebo_jednou():
	# "Svátky:" is printed twice; with one of them missing neither can be told apart
	texts = slip_texts(798.7, *A)
	first = next(t for t in texts if t[2] == "Svátky:")
	slip = one([t for t in texts if t is not first])
	assert {"nahrada_svatky", "priplatek_svatky", "priplatek_svatky_hodiny"} <= set(slip.unread)
	# a label printed more often than expected is not trusted either
	slip = one(texts + [(39.6 + 300.0, 798.7 - 300.0, "Stravné:")])
	assert "stravne" in slip.unread and "stravne" not in slip.extra


def test_prazdna_pole():
	slip = one(slip_texts(798.7, *A, extra={"ucet": None}))
	assert not BLANK & (set(slip.extra) | set(slip.unread))  # printed empty, nothing to read
	assert "ucet" not in slip.extra and slip.unread == []  # paid in cash: no account
	slip = one(slip_texts(798.7, *A, extra={"ucet": "neco jineho"}))
	assert "ucet" not in slip.extra and slip.unread == ["ucet"]
	slip = one(slip_texts(798.7, *A, extra={"pridanitelne_polozky": 1500, "ostatni_nezdanitelne": 250}))
	assert (slip.extra["pridanitelne_polozky"], slip.extra["ostatni_nezdanitelne"]) == (1500, 250)


def test_dve_stejne_blizke_hodnoty():
	# two numbers in the value column on one line cannot be told apart: unread, not a guess
	texts = slip_texts(798.7, *A, extra={"stravne": 300})
	slip = one(texts + [(39.6 + 250.0, 798.7 - 286.9, "999")])
	assert "stravne" in slip.unread and "stravne" not in slip.extra
	# a number in another column on the same line does not matter
	slip = one(texts + [(39.6 + 120.0, 798.7 - 286.9, "999")])
	assert slip.extra["stravne"] == 300


def test_obdobi():
	assert one(slip_texts(798.7, *A, period=(11, 2027))).period == (11, 2027)
	assert one(slip_texts(798.7, *A, period=None)).period is None
	assert one(slip_texts(798.7, *A, period=(13, 2026))).period is None


def test_k_vyplate_jen_ze_sveho_sloupce():
	# a number under the label but outside its column is not the payout
	texts = [t for t in slip_texts(798.7, *A) if not (t[2] == "13 571" and t[1] < 798.7 - 270)]
	assert len(texts) == len(slip_texts(798.7, *A)) - 1
	with pytest.raises(PdfSlipError, match="K výplatě"):
		split(build_pdf([texts + [(39.6 + 520.0, 798.7 - 281.1, "13 571")]]))


# --- internal sums ---------------------------------------------------------------


def consistent(**kw):
	values = slip_values(gross=23456, sp=1665, zp=1056, tax=1234, vacation=2000)
	fields = {k: D(v) for k, v in values.items()}
	extra = {k: D(v) for k, v in slip_extra(values).items()}
	extra |= {k: D(0) for k in pdf_slips.GROSS_PARTS if k not in fields}
	extra |= {
		"slevy_ostatni": D(0),
		"sleva_deti": D(0),
		"dovolena_minuly_rok": D(0),
		"dovolena_kraceno": D(0),
	}
	for k, v in kw.items():
		target = fields if k in fields else extra
		if v is None:
			target.pop(k)
		else:
			target[k] = D(v)
	return fields, extra


def test_soucty_sedi():
	assert check_sums(*consistent(), 2026) == []


@pytest.mark.parametrize(
	"change,failed",
	[
		({"dovolena_zustatek": "71.5"}, "zůstatek dovolené"),
		({"dovolena_kraceno": 4}, "zůstatek dovolené"),
		({"zaokrouhleny_zaklad_dane": 23400}, "zaokrouhlený základ daně"),
		({"dan": 3524}, "daň ze zaokrouhleného základu"),
		({"zaloha_na_dan": 1233}, "záloha na daň po slevách"),
		({"sleva_deti": 100}, "záloha na daň po slevách"),
		({"premie": 500}, "hrubá mzda (součet složek)"),
		({"mzda": 21455}, "hrubá mzda (součet složek)"),
	],
)
def test_soucet_nesedi(change, failed):
	result = check_sums(*consistent(**change), 2026)
	assert pdf_slips.SUM_PREFIX + failed in result
	# captions only, never an amount
	assert not any(ch.isdigit() for ch in "".join(result))


def test_soucet_s_chybejici_hodnotou_se_preskoci():
	fields, extra = consistent(dovolena_zustatek=None, dilci_zaklad_dane=None, premie=None, sleva_deti=None)
	assert check_sums(fields, extra, 2026) == []
	# the other rules are still checked
	fields, extra = consistent(dovolena_zustatek=None, premie=500)
	assert check_sums(fields, extra, 2026) == [pdf_slips.SUM_PREFIX + "hrubá mzda (součet složek)"]


def test_zaokrouhleni_zakladu_a_dan():
	def tax_fields(base, rounded, tax):
		return {"dan": D(tax)}, {"dilci_zaklad_dane": D(base), "zaokrouhleny_zaklad_dane": D(rounded)}

	assert check_sums(*tax_fields("99.5", 100, 15), 2026) == []  # up to 100: whole crowns
	assert check_sums(*tax_fields(100, 100, 15), 2026) == []
	assert check_sums(*tax_fields(101, 200, 30), 2026) == []  # above: whole hundreds
	assert check_sums(*tax_fields(33, 33, 5), 2026) == []  # 4.95 rounded up
	assert check_sums(*tax_fields(0, 0, 0), 2026) == []
	limit = pdf_slips.TAX_LIMIT[2026]
	# above the limit of the year: 23 % of the excess
	high = -(-(limit * 15 + 53100 * 23) // 100)
	assert (
		check_sums(*tax_fields(limit + 53099, limit + 53099 - (limit + 53099) % 100 + 100, high), 2026) != []
	)
	rounded = 200000
	exact = -(-(limit * 15 + (rounded - limit) * 23) // 100)
	assert check_sums(*tax_fields(199950, rounded, exact), 2026) == []
	assert check_sums(*tax_fields(199950, rounded, 30000), 2026) == [
		pdf_slips.SUM_PREFIX + "daň ze zaokrouhleného základu"
	]
	# a year without a known limit: only a base under the highest known limit is checked
	assert check_sums(*tax_fields(199950, rounded, 30000), 2031) == []
	assert check_sums(*tax_fields(15353, 15400, 2311), 2031) != []
	assert check_sums(*tax_fields(15353, 15400, 2310), None) == []


# --- printed slip against the JMHZ report ---------------------------------------


def test_doplnkove_udaje_sedi():
	extra = {
		"sleva_zakladni": D(2570),
		"slevy_ostatni": D(630),
		"sleva_deti": D(1267),
		"osvobozene_prijmy": D(0),
		"prumer_nahrady": D("187.13"),
		"typ_pp": "DPP",
		"zamestnavatel_sp": D(3273),
		"zamestnavatel_zp": D(1382),
	}
	jmhz = {
		"sleva_poplatnik": 2570,
		"sleva_invalidita": 210,
		"sleva_invalidita_rozsirena": 420,
		"zvyhodneni_deti": 1267,
		"sleva_deti": 900,
		"prumerny_vydelek": 187.125,
		"druh_cinnosti": "T",
	}
	employer = {"zamestnavatel_sp": 3273, "zamestnavatel_zp": 1382}
	assert extra_mismatches(extra, jmhz, employer) == []
	# the slip may print only the part of the child benefit used as a credit
	assert extra_mismatches(extra | {"sleva_deti": D(900)}, jmhz, employer) == []
	assert extra_mismatches(extra | {"typ_pp": "DPČ"}, jmhz | {"druh_cinnosti": "A, B"}, employer) == []


@pytest.mark.parametrize(
	"printed,reported,wrong",
	[
		({"sleva_zakladni": D(2500)}, {}, "Sleva na poplatníka"),
		({"slevy_ostatni": D(0)}, {}, "Ostatní slevy"),
		({"sleva_deti": D(1000)}, {}, "Sleva / zvýhodnění na vyživované děti"),
		({"osvobozene_prijmy": D(500)}, {}, "Příjmy osvobozené od daně"),
		({}, {"osvobozene_prijmy": 500}, "Příjmy osvobozené od daně"),
		({"prumer_nahrady": D("187.14")}, {}, "Průměr pro náhrady (Kč/h)"),
		({"typ_pp": "DPČ"}, {}, "Typ pracovního vztahu"),
		({}, {"druh_cinnosti": "T, 1"}, "Typ pracovního vztahu"),
		({"zamestnavatel_sp": D(3272)}, {}, "Sociální pojištění zaměstnavatele"),
		({"zamestnavatel_zp": D(0)}, {}, "Zdravotní pojištění zaměstnavatele"),
	],
)
def test_doplnkovy_udaj_nesedi(printed, reported, wrong):
	extra = {
		"sleva_zakladni": D(2570),
		"slevy_ostatni": D(630),
		"sleva_deti": D(1267),
		"osvobozene_prijmy": D(0),
		"prumer_nahrady": D("187.13"),
		"typ_pp": "DPP",
		"zamestnavatel_sp": D(3273),
		"zamestnavatel_zp": D(1382),
	} | printed
	jmhz = {
		"sleva_poplatnik": 2570,
		"sleva_invalidita": 210,
		"sleva_ztpp": 420,
		"zvyhodneni_deti": 1267,
		"sleva_deti": 900,
		"prumerny_vydelek": 187.125,
		"druh_cinnosti": "T",
	} | reported
	assert extra_mismatches(extra, jmhz, {"zamestnavatel_sp": 3273, "zamestnavatel_zp": 1382}) == [wrong]


def test_doplnkove_udaje_neporovnatelne():
	extra = {
		"sleva_zakladni": D(2570),
		"prumer_nahrady": D("187.13"),
		"typ_pp": "HPP",
		"zamestnavatel_sp": D(1),
	}
	# a row imported before the values were read: only the employer insurance can be compared
	assert extra_mismatches(extra, None, {"zamestnavatel_sp": 1}) == []
	assert extra_mismatches(extra, None, {"zamestnavatel_sp": 2}) == ["Sociální pojištění zaměstnavatele"]
	# unreadable in the report (None), not reported (no key), unknown printed kind: not compared
	jmhz = {"sleva_poplatnik": None, "druh_cinnosti": "1"}
	assert extra_mismatches(extra, jmhz) == []
	# unread on the slip: not compared
	assert extra_mismatches({}, {"sleva_poplatnik": 2570, "osvobozene_prijmy": 5}) == []


def test_json_tam_a_zpet():
	slip = one(slip_texts(798.7, *A, extra=FULL))
	data = extra_to_json(slip.extra)
	assert all(isinstance(v, str) for v in data.values())
	assert extra_from_json(data) == slip.extra
	# unknown keys and broken numbers are dropped, text is cut
	loaded = extra_from_json({"nesmysl": "1", "stravne": "abc", "typ_pp": "x" * 500, "premie": "12.5"})
	assert loaded == {"typ_pp": "x" * pdf_slips.MAX_TEXT, "premie": D("12.5")}
	assert extra_from_json(None) == {}


def test_popisky_pro_vsechny_klice():
	assert set(pdf_slips.CAPTIONS) == set(pdf_slips.EXTRA_KEYS) == set(pdf_slips.KEY_GROUPS)
	assert set(pdf_slips.KEY_GROUPS.values()) <= set(pdf_slips.GROUPS)
	assert not set(pdf_slips.EXTRA_KEYS) & set(pdf_slips.FIELDS)
	assert all(sp.zone in pdf_slips.ZONES for sp in pdf_slips.SPECS)
