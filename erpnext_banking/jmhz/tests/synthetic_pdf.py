"""Generator of synthetic payroll slip PDFs for tests. Pure Python, no extra libraries.

The layout follows the summary report read by `pdf_slips` (label positions relative
to the "Zaměstnanec:" anchor, two slips per A4 page). All names, numbers and the
employer are made up. Czech characters outside WinAnsi are mapped through a font
/Differences encoding, so text extraction returns proper Unicode.
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
# distractors on the same lines in other columns, and percentages next to insurance
_NOISE = [
	("Dny - sv. ve mzdě:", 0.0, -33.7),
	("0,00", 82.9, -33.9),
	("9,50%", 408.9, -33.0),
	("4,50%", 408.9, -45.7),
	("0", 285.6, -211.5),
	("Svátky:", 160.5, -42.0),
	("0", 283.1, -41.4),
]


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


def slip_texts(anchor_y: float, name: str, values: dict) -> list[tuple[float, float, str]]:
	x0 = 39.6
	texts = [
		(x0, anchor_y, "Zaměstnanec:"),
		(x0 + 62.2, anchor_y + 0.1, name),
		(332.7, anchor_y + 2.3, "Stř.:"),
		(x0, anchor_y - 16.5, "Zaměstnavatel:"),
		(x0 + 61.5, anchor_y - 16.5, "Testovací firma s.r.o., Testovací 1, 10000 Praha"),
	]
	for label, ldx, ldy, vdx, vdy, key in _FIELDS:
		texts.append((x0 + ldx, anchor_y + ldy, label))
		decimals = 2 if key in ("hodiny", "dny") else 0
		texts.append((x0 + vdx, anchor_y + vdy, fmt(values[key], decimals)))
		if key == "k_vyplate":
			texts.append((x0 + vdx + 46.1, anchor_y + vdy, ",- Kč"))
	for text, dx, dy in _NOISE:
		texts.append((x0 + dx, anchor_y + dy, text))
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
	net = gross - sp - zp - tax
	return {
		"hruba_mzda": gross,
		"mzda": gross - vacation,
		"dovolena": vacation,
		"socialni_pojisteni": sp,
		"zdravotni_pojisteni": zp,
		"zaloha_na_dan": tax,
		"zdanitelna_mzda": gross,
		"dan": tax,
		"cista_mzda": net,
		"k_vyplate": net + bonus,
		"hodiny": hours,
		"dny": days,
	}


def anchor_y(index: int, per_page: int = 2) -> float:
	return 798.7 - index * (842 / per_page)


def report_pdf(
	slips: list[tuple[str, dict]], per_page: int = 2, separator: str | None = "dashed", shift: float = 0.0
) -> bytes:
	"""Slips as (name, values); `per_page` slips stacked from the top of each A4 page.

	`separator`: "dashed" (dotted cut line between slips, like the real report), "solid"
	(a plain line, which is not a cut line) or None.
	"""
	pages, graphics = [], []
	for start in range(0, len(slips), per_page):
		texts, ops = [], []
		chunk = slips[start : start + per_page]
		for i, (name, values) in enumerate(chunk):
			texts += slip_texts(anchor_y(i, per_page), name, values)
			if i and separator:
				y = anchor_y(i, per_page) + SEPARATOR_ABOVE_ANCHOR
				ops.append(separator_ops(y, dashed=separator == "dashed", shift=shift))
		pages.append(texts)
		graphics.append(ops)
	return build_pdf(pages, graphics)
