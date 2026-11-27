"""Generator of synthetic payroll slip PDFs for tests. Pure Python, no extra libraries.

The layout follows the summary report read by `pdf_slips` (label positions relative
to the "Zaměstnanec:" anchor, two slips per A4 page). All names, numbers and the
employer are made up. Czech characters outside WinAnsi are mapped through a font
/Differences encoding, so text extraction returns proper Unicode.

Like the real report, a slip is a full form: every label is printed and an empty
value is printed as zero. The positions below were measured on the report, they are
not taken from the reader, so the tests do not just repeat the reader's own table.
"""

from __future__ import annotations

from decimal import Decimal

# characters outside cp1252 -> (code, glyph name)
_EXTRA = {
	"č": (0x81, "ccaron"),
	"Č": (0x8D, "Ccaron"),
	"ě": (0x8F, "ecaron"),
	"ň": (0x90, "ncaron"),
	"ř": (0x9D, "rcaron"),
	"Ř": (0xA4, "Rcaron"),
	"ů": (0xA6, "uring"),
	"ď": (0xA8, "dcaron"),
	"ť": (0xAA, "tcaron"),
	"Ě": (0xAC, "Ecaron"),
	"Ď": (0xAF, "Dcaron"),
	"Ň": (0xB2, "Ncaron"),
	"Ť": (0xB3, "Tcaron"),
	"Ů": (0xB9, "Uring"),
}

# (label, label dx, label dy, value dx, value dy) relative to the anchor at (39.6, y)
_FIELDS = [
	("Mzda:", 160.5, -30.7, 275.6, -29.4, "mzda"),
	("Dovolená:", 159.7, -53.2, 283.1, -53.4, "dovolena"),
	("Sociální pojištění:", 318.0, -33.7, 479.4, -34.1, "socialni_pojisteni"),
	("Zdravotní pojištění:", 318.0, -46.5, 479.4, -46.1, "zdravotni_pojisteni"),
	("Zdanitelná mzda:", 385.7, -87.0, 479.2, -87.7, "zdanitelna_mzda"),
	("Daň:", 433.3, -158.2, 497.0, -159.0, "dan"),
	("HRUBÁ MZDA:", 183.7, -196.6, 273.8, -196.1, "hruba_mzda"),
	("Záloha na daň po slevě:", 318.0, -211.5, 505.1, -210.9, "zaloha_na_dan"),
	("ČISTÁ MZDA:", 392.4, -225.9, 488.4, -225.1, "cista_mzda"),
	("K výplatě", 405.7, -260.2, 440.5, -281.1, "k_vyplate"),
	("Hodiny - odpr.:", 0.0, -108.7, 77.9, -108.1, "hodiny"),
	("Dny - odprac.:", 0.0, -96.0, 82.9, -95.4, "dny"),
]
# The rest of the form: (key, label, label dx, label dy, right edge of the value, value dy,
# decimals). Values are right-aligned, so a value starts at `edge - width`.
_FORM = [
	("dny_svatek_ve_mzde", "Dny - sv. ve mzdě:", 0.0, -33.7, 100.0, -33.9, 2),
	("dny_svatek_nahrada", "Dny - sv. náhr.:", 0.0, -45.0, 100.0, -45.2, 2),
	("dny_nemoc", "Prac dny nem.:", 0.0, -83.2, 100.0, -83.4, 2),
	("prumer_nahrady", "Pr.-náhrady/h.:", 0.7, -120.7, 90.0, -120.9, 2),
	("prumer_dpn", "Red.prům.DPN:", 0.0, -135.0, 90.0, -134.2, 2),
	("dovolena_minuly_rok", "z minulého roku :", 0.0, -160.5, 101.5, -159.8, 3),
	("dovolena_narok", "nárok-běžný rok:", 0.0, -173.2, 101.5, -172.4, 3),
	("dovolena_kraceno", "kráceno :", 0.0, -186.0, 101.5, -185.3, 3),
	("dovolena_cerpano", "čerpáno :", 0.0, -198.7, 101.5, -198.0, 3),
	("dovolena_zustatek", "zůstatek skut. :", 0.0, -224.2, 101.5, -223.5, 3),
	("nahrada_svatky", "Svátky:", 160.5, -42.0, 308.5, -41.4, 0),
	("nahrady_ostatni", "Ostatní náhrady:", 159.7, -65.2, 308.5, -65.4, 0),
	("prescas_hodiny", "Přesčas:", 160.5, -79.5, 227.5, -78.6, 2),
	("prescas", None, 0, 0, 308.5, -79.7, 0),
	("priplatek_svatky_hodiny", "Svátky:", 160.5, -91.5, 227.5, -90.6, 2),
	("priplatek_svatky", None, 0, 0, 308.5, -92.3, 0),
	("premie", "Prémie a odm. a os. oh.:", 160.5, -105.0, 308.5, -104.6, 0),
	("ostatni_slozky", "Ost. složky mzdy:", 160.5, -116.2, 308.5, -117.9, 0),
	("prostredi_hodiny", "Prostředí:", 160.5, -130.5, 227.5, -129.6, 2),
	("prostredi", None, 0, 0, 308.5, -130.7, 0),
	("priplatek_1_hodiny", "Ost. přípl. 1:", 160.5, -142.5, 227.5, -141.6, 2),
	("priplatek_1", None, 0, 0, 308.5, -142.7, 0),
	("priplatek_2", "Ost. příplatky 2:", 160.5, -154.5, 308.5, -156.0, 0),
	("so_ne_noc_hodiny", "SO,NE,noc:", 160.5, -167.2, 227.5, -166.4, 2),
	("so_ne_noc", None, 0, 0, 308.5, -167.4, 0),
	("rocni_odmeny", "Pololetní a roční odměny:", 109.5, -181.5, 308.5, -181.7, 0),
	("osvobozene_prijmy", "Příjmy osvobozené od daně:", 110.2, -210.7, 308.5, -211.5, 0),
	("socialni_pojisteni_sazba", None, 0, 0, 428.0, -33.0, "%"),
	("zdravotni_pojisteni_sazba", None, 0, 0, 428.0, -45.7, "%"),
	("prevod_jine_pp", "Ost. složky mzdy a převod z jin. PP:", 318.0, -64.5, 508.5, -65.8, 0),
	("pridanitelne_polozky", "Přidanitelné položky:", 370.0, -101.2, 508.5, -101.9, 0),
	("ostatni_nezdanitelne", "Ostatní nezd. částky:", 370.6, -115.5, 508.5, -116.2, 0),
	("dilci_zaklad_dane", "Dílčí základ daně:", 383.1, -129.0, 508.5, -129.6, 0),
	("zaokrouhleny_zaklad_dane", "Zaokr. základ daně:", 376.1, -143.2, 508.5, -143.9, 0),
	("sleva_zakladni", "Sleva - základ:", 395.2, -171.7, 508.5, -172.4, 0),
	("slevy_ostatni", "Ostatní slevy:", 398.1, -184.5, 508.5, -185.3, 0),
	("sleva_deti", "Sleva/zvýh. na vyž. děti:", 359.8, -195.7, 508.5, -196.4, 0),
	("zamestnavatel_sp_sazba", "Soc.poj :", 74.1, -257.8, 122.0, -257.9, "%"),
	("zamestnavatel_sp", None, 0, 0, 170.0, -258.6, 0),
	("zamestnavatel_zp_sazba", "Zdr.poj. :", 74.1, -269.1, 122.0, -269.1, "%"),
	("zamestnavatel_zp", None, 0, 0, 170.0, -269.9, 0),
	("zamestnavatel_penzijni", "Penz.přip.:", 74.1, -279.6, 170.0, -280.4, 0),
	("zamestnavatel_zivotni", "ŽP,DIP,PDP :", 74.1, -290.8, 170.0, -291.6, 0),
	("zamestnavatel_benefity", "Ost. benefity :", 74.1, -302.1, 170.0, -302.9, 0),
	("cestovni_vydaje", "Cest. výdaje:", 178.3, -260.1, 285.0, -259.9, 0),
	("pracovni_prostredky", "Prac.prostředky:", 178.3, -273.6, 285.0, -273.4, 0),
	("stravne", "Stravné:", 178.3, -287.1, 285.0, -286.9, 0),
	("jine_naklady", "Jiné nákl.:", 178.3, -300.6, 285.0, -300.4, 0),
	("nemocenske", "Nemoc,ost.soc.:", 178.3, -314.1, 285.0, -313.9, 0),
	("srazka_sporeni", "Spoření:", 293.1, -260.1, 396.0, -259.9, 0),
	("srazka_pojisteni", "Pojištění:", 293.1, -273.6, 396.0, -273.4, 0),
	("srazka_vyzivne", "Výživné:", 293.1, -287.1, 396.0, -286.9, 0),
	("srazka_ostatni_1", "Ost. srážky 1:", 293.1, -300.6, 396.0, -300.4, 0),
	("srazka_ostatni_2", "Ost. srážky 2:", 293.1, -314.1, 396.0, -313.9, 0),
	("srazka_zp", "ZP za NV,NEAB:", 293.1, -326.1, 396.0, -325.9, 0),
	("zaloha", "Záloha:", 408.0, -243.0, 508.5, -243.2, 0),
]
# printed empty on the real report unless there is something to show
_BLANK = {"pridanitelne_polozky", "ostatni_nezdanitelne"}
# labels without a number next to them: headings and labels with a value elsewhere
_STATIC = [
	("Stř.:", 292.0, 2.2),
	("Výpl. m.:", 364.1, 2.2),
	("EČ:", 446.3, 2.2),
	("Výplata za měsíc:", 387.3, -16.5),
	("/", 477.0, -18.0),
	("D/h. - dovol:", 0.0, -57.7),
	("/", 72.8, -58.0),
	("Pr./kal. dny abs.:", 0.0, -70.5),
	("hod.", 231.1, -79.5),
	("hod.", 231.1, -91.5),
	("hod.", 231.1, -130.5),
	("hod.", 231.1, -142.5),
	("hod.", 231.1, -167.2),
	("Základní", 109.6, -43.5),
	("složky mzdy", 109.6, -52.7),
	("Příplatky", 109.6, -83.3),
	("Zvláštní", 109.6, -138.0),
	("příplatky", 109.6, -147.8),
	("DOVOLENÁ", 15.0, -147.8),
	("(hod.)", 61.6, -147.8),
	("Mzdový výměr :", -1.4, -240.7),
	("Typ PP :", -1.4, -292.5),
	("Pojišťovna :", -1.4, -316.5),
	("Úhrady zaměstnavatele:", 80.9, -243.0),
	("Jiné dávky ke mzdě", 195.0, -243.0),
	("Srážky", 326.5, -243.0),
	("Stravenkový paušál - dny / Kč :", 74.6, -316.3),
	("Způsob výplaty:", 405.6, -296.1),
]
# average width of one character of the 7 pt font, enough to right-align the values
_CHAR = 3.9

EXTRA_DEFAULTS = {
	"socialni_pojisteni_sazba": "7.1",
	"zdravotni_pojisteni_sazba": "4.5",
	"zamestnavatel_sp_sazba": "24.8",
	"zamestnavatel_zp_sazba": "9",
	"stredisko": "1",
	"osobni_cislo": "7",
	"mzdovy_vymer": "200,00/hod.",
	"typ_pp": "DPP",
	"pojistovna": "111",
	"zpusob_vyplaty": "Převodem na účet",
	"ucet": "000000-0000000000/0000",
	"absence_pracovni_dny": "0",
	"absence_kalendarni_dny": "0",
	"dovolena_dny": "0",
	"dovolena_hodiny": "0",
}


def _encode(text: str) -> bytes:
	out = bytearray()
	for ch in text:
		if ch in _EXTRA:
			out.append(_EXTRA[ch][0])
		else:
			out += ch.encode("cp1252")
	return bytes(out).replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")


def fmt(value, decimals: int = 0) -> str:
	"""Number as printed on the slip: thousands separated by a space, decimal comma."""
	d = Decimal(str(value))
	text = f"{d:,.{decimals}f}".replace(",", " ").replace(".", ",")
	return text


def rounded_base(gross) -> int:
	"""Base of the advance tax: above 100 rounded up to whole hundreds."""
	gross = int(gross)
	return gross if gross <= 100 else -(-gross // 100) * 100


def advance_tax(gross) -> int:
	"""15 % of the rounded base, rounded up (all synthetic wages are below the 23 % limit)."""
	return -(-rounded_base(gross) * 15 // 100)


def slip_extra(values: dict, **kw) -> dict:
	"""The rest of an internally consistent slip for the given control values: tax base, tax
	credit, employer insurance (the same multiples as the synthetic JMHZ report), leave.
	Keyword arguments override single values."""
	extra = {
		"dilci_zaklad_dane": values["zdanitelna_mzda"],
		"zaokrouhleny_zaklad_dane": rounded_base(values["zdanitelna_mzda"]),
		"sleva_zakladni": values["dan"] - values["zaloha_na_dan"],
		"zamestnavatel_sp": values["socialni_pojisteni"] * 3,
		"zamestnavatel_zp": values["zdravotni_pojisteni"] * 2,
		"prumer_nahrady": "187.13",
		"prumer_dpn": "168.42",
		"dovolena_narok": 80,
		"dovolena_cerpano": 8,
		"dovolena_zustatek": 72,
	}
	extra.update(kw)
	return extra


def slip_texts(
	anchor_y: float,
	name: str,
	values: dict,
	dx: float = 0.0,
	extra: dict | None = None,
	period: tuple[int, int] | None = (2, 2026),
	pad: float = 0.0,
	omit: tuple[str, ...] = (),
) -> list[tuple[float, float, str]]:
	"""Texts of one slip.

	`values` are the 12 control fields. The rest of the form is `slip_extra(values)`, zero
	elsewhere; `extra` overrides single values. `dx` shifts the whole layout horizontally and
	`pad` moves the start of every amount to the left, as in reports that pad amounts with
	spaces (days, hours and rates are not padded there). `omit` leaves out the labels (and values) of the given keys or label
	texts.
	"""
	x0 = 39.6 + dx
	e = {**EXTRA_DEFAULTS, **slip_extra(values), **(extra or {})}
	texts = [
		(x0, anchor_y, "Zaměstnanec:"),
		(x0 + 62.2, anchor_y + 0.1, name),
		(x0, anchor_y - 16.5, "Zaměstnavatel:"),
		(x0 + 61.5, anchor_y - 16.5, "Testovací firma s.r.o., Testovací 1, 10000 Praha"),
	]
	for label, ldx, ldy, vdx, vdy, key in _FIELDS:
		texts.append((x0 + ldx, anchor_y + ldy, label))
		decimals = 2 if key in ("hodiny", "dny") else 0
		texts.append((x0 + vdx, anchor_y + vdy, fmt(values[key], decimals)))
		if key == "k_vyplate":
			texts.append((x0 + vdx + 46.1, anchor_y + vdy, ",- Kč"))

	def right(edge: float, dy: float, text: str, padded: float = 0.0) -> None:
		texts.append((x0 + edge - _CHAR * len(text) - padded, anchor_y + dy, text))

	for key, label, ldx, ldy, edge, vdy, decimals in _FORM:
		if key in omit or (label and label in omit):
			continue
		if label:
			texts.append((x0 + ldx, anchor_y + ldy, label))
		value = e.get(key, None if key in _BLANK else 0)
		if value is None:
			continue
		if decimals == "%":
			right(edge, vdy, fmt(value, 2) + "%")
		else:
			# the narrow employer column is padded less than the others
			right(edge, vdy, fmt(value, decimals), 0.0 if decimals else min(pad, 12.0) if edge < 180 else pad)
	for text, sdx, sdy in _STATIC:
		if text not in omit:
			texts.append((x0 + sdx, anchor_y + sdy, text))
	# values printed in their own way
	right(69.0, -56.0, fmt(e["dovolena_dny"], 2))
	right(102.0, -57.0, fmt(e["dovolena_hodiny"], 2))
	right(102.0, -69.9, f"{fmt(e['absence_pracovni_dny'], 2)}/{fmt(e['absence_kalendarni_dny'], 2)}")
	if e.get("stredisko") is not None:
		texts.append((x0 + 313.6, anchor_y + 2.2, str(e["stredisko"])))
	if e.get("osobni_cislo") is not None:
		right(509.0, 1.0, str(e["osobni_cislo"]))
	if period:
		right(474.0, -18.0, str(period[0]))
		texts.append((x0 + 483.9, anchor_y - 18.0, str(period[1])))
	for key, tdy in (("mzdovy_vymer", -252.9), ("typ_pp", -303.8), ("pojistovna", -327.8)):
		if e.get(key) is not None:
			texts.append((x0 - 1.4, anchor_y + tdy, str(e[key])))
	if e.get("zpusob_vyplaty") is not None:
		texts.append((x0 + 405.8, anchor_y - 310.7, str(e["zpusob_vyplaty"]) + ":"))
	if e.get("ucet") is not None:
		texts.append((x0 + 405.8, anchor_y - 321.0, str(e["ucet"])))
	return texts


# the dotted cut line between two slips sits this far above the next anchor
SEPARATOR_ABOVE_ANCHOR = 32.3


def separator_ops(y: float, dashed: bool = True, shift: float = 0.0) -> bytes:
	"""Horizontal cut line like in the real report: a stroked segment with a dotted dash pattern.

	`shift` draws it through a `cm` translation (the line ends at the same page y).
	"""
	dash = b"[0.75 0.75] 0 d " if dashed else b""
	cm = f"1 0 0 1 0 {shift:.2f} cm ".encode() if shift else b""
	return (
		b"q " + cm + dash + f"0 0 0 RG 0.75 w 34.88 {y - shift:.2f} m 554.62 {y - shift:.2f} l S Q".encode()
	)


def _content(texts, graphics=()) -> bytes:
	# one text object per item, like the real report (pypdf merges items of one BT block on a line)
	ops = list(graphics)
	for x, y, text in texts:
		ops.append(f"BT /F1 7 Tf 1 0 0 1 {x:.1f} {y:.1f} Tm (".encode() + _encode(text) + b") Tj ET")
	return b"\n".join(ops)


def build_pdf(
	pages: list[list[tuple[float, float, str]]], graphics: list[list[bytes]] | None = None
) -> bytes:
	"""Minimal PDF with one Helvetica font, the given texts and optional graphics ops per page."""
	differences = " ".join(f"{code} /{glyph}" for code, glyph in sorted(_EXTRA.values()))
	objects: list[bytes] = []

	def add(body: bytes) -> int:
		objects.append(body)
		return len(objects)

	font = add(
		b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding << /Type /Encoding "
		b"/BaseEncoding /WinAnsiEncoding /Differences [" + differences.encode() + b"] >> >>"
	)
	pages_id = len(objects) + 1 + 2 * len(pages)
	page_ids = []
	for page_no, texts in enumerate(pages):
		stream = _content(texts, (graphics or [])[page_no] if graphics and page_no < len(graphics) else ())
		content = add(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
		page_ids.append(
			add(
				b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 %d 0 R >> >> "
				b"/Contents %d 0 R >>" % (pages_id, font, content)
			)
		)
	kids = " ".join(f"{p} 0 R" for p in page_ids).encode()
	assert add(b"<< /Type /Pages /Kids [" + kids + b"] /Count %d >>" % len(page_ids)) == pages_id
	catalog = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

	out = bytearray(b"%PDF-1.4\n")
	offsets = []
	for i, body in enumerate(objects, start=1):
		offsets.append(len(out))
		out += b"%d 0 obj\n" % i + body + b"\nendobj\n"
	xref = len(out)
	out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
	for off in offsets:
		out += b"%010d 00000 n \n" % off
	out += b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
		len(objects) + 1,
		catalog,
		xref,
	)
	return bytes(out)


def slip_values(gross=15353, sp=1091, zp=691, tax=0, hours=40.5, days=5, vacation=0, bonus=0) -> dict:
	"""The 12 control fields. `tax` is the advance tax after credits."""
	net = gross - sp - zp - tax
	return {
		"hruba_mzda": gross,
		"mzda": gross - vacation,
		"dovolena": vacation,
		"socialni_pojisteni": sp,
		"zdravotni_pojisteni": zp,
		"zaloha_na_dan": tax,
		"zdanitelna_mzda": gross,
		"dan": advance_tax(gross),
		"cista_mzda": net,
		"k_vyplate": net + bonus,
		"hodiny": hours,
		"dny": days,
	}


def anchor_y(index: int, per_page: int = 2) -> float:
	return 798.7 - index * (842 / per_page)


def report_pdf(
	slips: list[tuple],
	per_page: int = 2,
	separator: str | None = "dashed",
	shift: float = 0.0,
	period: tuple[int, int] | None = (2, 2026),
) -> bytes:
	"""Slips as (name, values) or (name, values, extra); `per_page` slips stacked from the
	top of each A4 page. `period` is the month and year printed on every slip.

	`separator`: "dashed" (dotted cut line between slips, like the real report), "solid"
	(a plain line, which is not a cut line) or None.
	"""
	pages, graphics = [], []
	for start in range(0, len(slips), per_page):
		texts, ops = [], []
		chunk = slips[start : start + per_page]
		for i, (name, values, *rest) in enumerate(chunk):
			texts += slip_texts(
				anchor_y(i, per_page), name, values, extra=rest[0] if rest else None, period=period
			)
			if i and separator:
				y = anchor_y(i, per_page) + SEPARATOR_ABOVE_ANCHOR
				ops.append(separator_ops(y, dashed=separator == "dashed", shift=shift))
		pages.append(texts)
		graphics.append(ops)
	return build_pdf(pages, graphics)
