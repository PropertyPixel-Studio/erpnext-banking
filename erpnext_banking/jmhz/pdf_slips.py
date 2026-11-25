"""Splitting and reading payroll slips printed by the payroll system (SPEC §6.6).

Pure Python + pypdf, must not import frappe. The input is the summary report with
several slips per page (one slip = one block starting with the label
"Zaměstnanec:"). Slips on a page are separated by a dotted horizontal line; the
cuts follow that line. When a separator is missing, the boundary falls back to
just above the next anchor. For every slip the module returns the employee name,
12 numeric fields and a one-page PDF cropped to the slip.

Limitation: cropping only changes the visible area. The data of the other slips
on the same page physically stays in every cut. The cuts are for HR only and must
never be shown or sent to an employee.

Error messages never contain names or amounts, only page numbers and labels.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import ContentStream, RectangleObject

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 50

ANCHOR = "Zaměstnanec:"
# x where the middle and the right column of the slip start (points)
COLUMNS = (145.0, 355.0)
LINE_TOLERANCE = 2.5
# the slip block starts slightly above the anchor line
TOP_MARGIN = 20.0
# the value of "K výplatě" is printed below its label
BELOW_MAX = 35.0
NAME_MAX_X = 330.0
# a separator is a dashed horizontal line over most of the page width
SEPARATOR_MIN_WIDTH = 0.6

# field -> label on the slip
FIELDS = {
	"hruba_mzda": "HRUBÁ MZDA:",
	"mzda": "Mzda:",
	"dovolena": "Dovolená:",
	"socialni_pojisteni": "Sociální pojištění:",
	"zdravotni_pojisteni": "Zdravotní pojištění:",
	"zaloha_na_dan": "Záloha na daň po slevě:",
	"zdanitelna_mzda": "Zdanitelná mzda:",
	"dan": "Daň:",
	"cista_mzda": "ČISTÁ MZDA:",
	"k_vyplate": "K výplatě",
	"hodiny": "Hodiny - odpr.:",
	"dny": "Dny - odprac.:",
}
VALUE_BELOW = {"k_vyplate"}

_NUMBER = re.compile(r"^-?[\d\s ]+(,\d+)?(,-)?$")


class PdfSlipError(ValueError):
	"""The report cannot be split reliably. Nothing is stored."""


@dataclass
class _Text:
	x: float
	y: float
	text: str


@dataclass
class PdfSlip:
	page: int  # 1-based
	index: int  # order on the page, 1-based
	name: str
	fields: dict[str, Decimal]
	box: tuple[float, float]  # (bottom, top) in points
	pdf: bytes = field(repr=False)


def parse_number(text: str) -> Decimal | None:
	t = text.strip().replace(",- Kč", "").replace("Kč", "").strip()
	if not _NUMBER.match(t):
		return None
	t = t.replace(" ", "").replace(" ", "").replace(",-", "").replace(",", ".")
	try:
		return Decimal(t)
	except InvalidOperation:
		return None


def _column(x: float) -> int:
	return sum(x >= c for c in COLUMNS)


def _page_texts(page) -> list[_Text]:
	items: list[_Text] = []

	def visit(text, cm, tm, font_dict, font_size):
		t = (text or "").strip()
		if t:
			items.append(_Text(round(tm[4] * cm[0] + cm[4], 1), round(tm[5] * cm[3] + cm[5], 1), t))

	page.extract_text(visitor_text=visit)
	return items


def _matmul(a: tuple, b: tuple) -> tuple:
	"""PDF matrix product a × b, matrices as (a, b, c, d, e, f)."""
	return (
		a[0] * b[0] + a[1] * b[2],
		a[0] * b[1] + a[1] * b[3],
		a[2] * b[0] + a[3] * b[2],
		a[2] * b[1] + a[3] * b[3],
		a[4] * b[0] + a[5] * b[2] + b[4],
		a[4] * b[1] + a[5] * b[3] + b[5],
	)


def _apply(m: tuple, x: float, y: float) -> tuple[float, float]:
	return m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]


def _separators(page, reader) -> list[float]:
	"""y of dashed horizontal lines spanning most of the page (the dotted cut lines)."""
	try:
		operations = ContentStream(page.get_contents(), reader).operations
	except Exception:
		return []
	width = float(page.mediabox.width)
	ctm = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
	dashed = False
	stack = []
	current = None
	found = set()
	for operands, op in operations:
		if op == b"q":
			stack.append((ctm, dashed))
		elif op == b"Q":
			ctm, dashed = stack.pop() if stack else ((1.0, 0.0, 0.0, 1.0, 0.0, 0.0), False)
		elif op == b"cm" and len(operands) == 6:
			ctm = _matmul(tuple(float(v) for v in operands), ctm)
		elif op == b"d" and operands:
			dashed = len(operands[0]) > 0
		elif op == b"m" and len(operands) == 2:
			current = _apply(ctm, float(operands[0]), float(operands[1]))
		elif op == b"l" and len(operands) == 2 and current is not None:
			end = _apply(ctm, float(operands[0]), float(operands[1]))
			if (
				dashed
				and abs(end[1] - current[1]) < 0.5
				and abs(end[0] - current[0]) >= SEPARATOR_MIN_WIDTH * width
			):
				found.add(round(current[1], 2))
			current = end
	return sorted(found, reverse=True)


def _boundaries(anchors: list[_Text], separators: list[float], page_top: float, page_bottom: float):
	"""(bottom, top) of every slip: separators when present, otherwise just above the next anchor."""
	out = []
	for i, anchor in enumerate(anchors):
		above = [y for y in separators if y > anchor.y]
		previous_anchor = anchors[i - 1].y if i else None
		if above and (previous_anchor is None or min(above) < previous_anchor):
			top = min(above)
		elif i:
			top = out[-1][0]
		else:
			top = min(page_top, anchor.y + TOP_MARGIN)
		below = [y for y in separators if y < anchor.y]
		next_anchor = anchors[i + 1].y if i + 1 < len(anchors) else None
		if below and (next_anchor is None or max(below) > next_anchor):
			bottom = max(below)
		elif next_anchor is not None:
			bottom = next_anchor + TOP_MARGIN
		else:
			bottom = page_bottom
		out.append((bottom, top))
	return out


def _value_right(texts: list[_Text], label: _Text) -> Decimal | None:
	col = _column(label.x)
	candidates = [
		t
		for t in texts
		if t is not label
		and abs(t.y - label.y) <= LINE_TOLERANCE
		and t.x > label.x
		and _column(t.x) == col
		and "%" not in t.text
		and parse_number(t.text) is not None
	]
	if not candidates:
		return None
	return parse_number(max(candidates, key=lambda t: t.x).text)


def _value_below(texts: list[_Text], label: _Text) -> Decimal | None:
	candidates = [
		t
		for t in texts
		if 0 < label.y - t.y <= BELOW_MAX and t.x >= label.x - 5 and parse_number(t.text) is not None
	]
	if not candidates:
		return None
	return parse_number(min(candidates, key=lambda t: label.y - t.y).text)


def _read_slip(texts: list[_Text], anchor: _Text, page_no: int) -> tuple[str, dict[str, Decimal]]:
	name = " ".join(
		t.text
		for t in sorted(texts, key=lambda t: t.x)
		if abs(t.y - anchor.y) <= LINE_TOLERANCE and anchor.x < t.x < NAME_MAX_X
	).strip()
	values: dict[str, Decimal] = {}
	missing = []
	for key, label_text in FIELDS.items():
		labels = [t for t in texts if t.text == label_text]
		if len(labels) != 1:
			missing.append(label_text)
			continue
		value = _value_below(texts, labels[0]) if key in VALUE_BELOW else _value_right(texts, labels[0])
		if value is None:
			missing.append(label_text)
			continue
		values[key] = value
	if not name:
		missing.append(ANCHOR)
	if missing:
		raise PdfSlipError(f"Strana {page_no}: pásku nejde přečíst, chybí {', '.join(missing)}.")
	return name, values


def _cut(content: bytes, page_index: int, bottom: float, top: float) -> bytes:
	reader = PdfReader(io.BytesIO(content))
	writer = PdfWriter()
	page = writer.add_page(reader.pages[page_index])
	left = float(page.mediabox.left)
	right = float(page.mediabox.right)
	box = RectangleObject((left, bottom, right, top))
	page.mediabox = box
	page.cropbox = box
	out = io.BytesIO()
	writer.write(out)
	return out.getvalue()


def split(content: bytes) -> list[PdfSlip]:
	"""Split the report into slips. Raises PdfSlipError when anything is not recognised."""
	if not isinstance(content, bytes | bytearray):
		raise TypeError("content must be bytes")
	content = bytes(content)
	if len(content) > MAX_PDF_BYTES:
		raise PdfSlipError("Soubor je větší než 10 MB.")
	try:
		reader = PdfReader(io.BytesIO(content))
		if reader.is_encrypted:
			raise PdfSlipError("PDF je zaheslované.")
		pages = list(reader.pages)
	except PdfSlipError:
		raise
	except (PdfReadError, ValueError, KeyError, TypeError, OSError):
		raise PdfSlipError("Soubor není čitelné PDF.") from None
	if not pages:
		raise PdfSlipError("PDF nemá žádnou stranu.")
	if len(pages) > MAX_PAGES:
		raise PdfSlipError(f"PDF má víc než {MAX_PAGES} stran.")

	slips: list[PdfSlip] = []
	for page_index, page in enumerate(pages):
		page_no = page_index + 1
		try:
			texts = _page_texts(page)
		except Exception:
			raise PdfSlipError(f"Strana {page_no}: text nejde přečíst.") from None
		anchors = sorted((t for t in texts if t.text == ANCHOR), key=lambda t: -t.y)
		if not anchors:
			raise PdfSlipError(f"Strana {page_no}: nenašla se žádná páska.")
		page_top = float(page.mediabox.top)
		page_bottom = float(page.mediabox.bottom)
		boxes = _boundaries(anchors, _separators(page, reader), page_top, page_bottom)
		for i, (anchor, (bottom, top)) in enumerate(zip(anchors, boxes)):
			block = [t for t in texts if bottom < t.y <= top]
			name, values = _read_slip(block, anchor, page_no)
			slips.append(
				PdfSlip(
					page=page_no,
					index=i + 1,
					name=name,
					fields=values,
					box=(bottom, top),
					pdf=_cut(content, page_index, bottom, top),
				)
			)
	return slips
