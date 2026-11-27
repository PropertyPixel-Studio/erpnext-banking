"""Splitting and reading payroll slips printed by the payroll system.

Pure Python + pypdf, must not import frappe. The input is the summary report with
several slips per page (one slip = one block starting with the label
"Zaměstnanec:"). Slips on a page are separated by a dotted horizontal line; the
cuts follow that line. When a separator is missing, the boundary falls back to
just above the next anchor. For every slip the module returns the employee name,
the 12 control fields, the rest of the slip (`extra`) and a one-page PDF cropped
to the slip.

The 12 control fields are compared with the JMHZ report and are required: a slip
without them is not recognised and the whole report is refused. Everything else
is read leniently. A value that cannot be read is left out and its key is listed
in `unread`, it is never replaced by zero; a person then has to check the slip.

Reading is positional. A value belongs to a label when it lies on the line of the
label inside the value column of that field (`ZONES`), relative to the anchor.
When the payroll system or its report changes, this table has to be measured again.

Limitation: cropping only changes the visible area. The data of the other slips
on the same page physically stays in every cut. The cuts are for HR only and must
never be shown or sent to an employee.

Error messages never contain names or amounts, only page numbers and labels.
"""

from __future__ import annotations

import io
import math
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from pypdf import PdfReader, PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import ContentStream, RectangleObject

MAX_PDF_BYTES = 10 * 1024 * 1024
MAX_PAGES = 50

ANCHOR = "Zaměstnanec:"
LINE_TOLERANCE = 2.5
# the slip block starts slightly above the anchor line
TOP_MARGIN = 20.0
NAME_MAX_OFFSET = 290.4

# Value columns: [from, to) of the x where a value starts, relative to the x of the anchor
# (the whole layout is shifted between reports). The ranges are wide on purpose: values are
# right-aligned and some reports pad them with leading spaces, so the start of the same
# value moves by tens of points. Lines of one column are at least 9 points apart.
ZONES = {
	"left": (45.0, 105.0),  # days, hours, averages, leave
	"hours": (190.0, 229.0),  # hours of a bonus, in front of "hod."
	"middle": (245.0, 316.0),  # wage components
	"rate": (395.0, 430.0),  # insurance rates
	"right": (440.0, 530.0),  # insurance, tax, net wage, advance
	"employer_rate": (95.0, 125.0),
	"employer": (128.0, 176.0),  # paid by the employer
	"benefits": (240.0, 291.0),  # other payments with the wage
	"deductions": (350.0, 404.0),
}
# a value is printed at most this far above or below the line of its label
VALUE_TOLERANCE = 4.0
# two candidates this close to each other cannot be told apart
AMBIGUOUS = 1.0
# the value of "K výplatě" is printed below its label, in front of ",- Kč"
PAYOUT_ZONE = (425.0, 484.0)
PAYOUT_BELOW = (15.0, 27.0)
MAX_TEXT = 60
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
CONTROL_ZONES = {
	"hruba_mzda": "middle",
	"mzda": "middle",
	"dovolena": "middle",
	"socialni_pojisteni": "right",
	"zdravotni_pojisteni": "right",
	"zaloha_na_dan": "right",
	"zdanitelna_mzda": "right",
	"dan": "right",
	"cista_mzda": "right",
	"hodiny": "left",
	"dny": "left",
}


@dataclass(frozen=True)
class Spec:
	"""One value of the slip outside the control fields."""

	key: str
	label: str  # text of the label on the slip
	zone: str
	group: str
	kind: str = "amount"  # amount | quantity | rate
	nth: int = 1  # which of the labels with the same text, from the top
	blank: bool = False  # the report prints nothing when there is nothing to show
	title: str = ""  # label for people, when the printed one is not clear enough

	@property
	def caption(self) -> str:
		return self.title or self.label.rstrip(": ")


# groups = blocks of the slip, in reading order
GROUPS = {
	"doba": "Odpracovaná doba",
	"dovolena": "Dovolená (hod.)",
	"mzda": "Složky mzdy",
	"dan": "Pojištění a daň",
	"zamestnavatel": "Úhrady zaměstnavatele",
	"davky": "Jiné dávky ke mzdě",
	"srazky": "Srážky",
	"vyplata": "Výplata",
}

SPECS: tuple[Spec, ...] = (
	Spec(
		"dny_svatek_ve_mzde", "Dny - sv. ve mzdě:", "left", "doba", "quantity", title="Svátky ve mzdě (dny)"
	),
	Spec(
		"dny_svatek_nahrada", "Dny - sv. náhr.:", "left", "doba", "quantity", title="Svátky s náhradou (dny)"
	),
	Spec("dny_nemoc", "Prac dny nem.:", "left", "doba", "quantity", title="Pracovní dny nemoci"),
	Spec("prumer_nahrady", "Pr.-náhrady/h.:", "left", "doba", "quantity", title="Průměr pro náhrady (Kč/h)"),
	Spec("prumer_dpn", "Red.prům.DPN:", "left", "doba", "quantity", title="Redukovaný průměr pro DPN"),
	Spec("dovolena_minuly_rok", "z minulého roku :", "left", "dovolena", "quantity", title="Z minulého roku"),
	Spec("dovolena_narok", "nárok-běžný rok:", "left", "dovolena", "quantity", title="Nárok v běžném roce"),
	Spec("dovolena_kraceno", "kráceno :", "left", "dovolena", "quantity", title="Kráceno"),
	Spec("dovolena_cerpano", "čerpáno :", "left", "dovolena", "quantity", title="Čerpáno"),
	Spec("dovolena_zustatek", "zůstatek skut. :", "left", "dovolena", "quantity", title="Zůstatek"),
	Spec("nahrada_svatky", "Svátky:", "middle", "mzda", title="Náhrada za svátky"),
	Spec("nahrady_ostatni", "Ostatní náhrady:", "middle", "mzda"),
	Spec("prescas_hodiny", "Přesčas:", "hours", "mzda", "quantity", title="Přesčas (hod.)"),
	Spec("prescas", "Přesčas:", "middle", "mzda", title="Příplatek za přesčas"),
	Spec("priplatek_svatky_hodiny", "Svátky:", "hours", "mzda", "quantity", nth=2, title="Svátky (hod.)"),
	Spec("priplatek_svatky", "Svátky:", "middle", "mzda", nth=2, title="Příplatek za svátky"),
	Spec("premie", "Prémie a odm. a os. oh.:", "middle", "mzda", title="Prémie, odměny a osobní ohodnocení"),
	Spec("ostatni_slozky", "Ost. složky mzdy:", "middle", "mzda", title="Ostatní složky mzdy"),
	Spec("prostredi_hodiny", "Prostředí:", "hours", "mzda", "quantity", title="Prostředí (hod.)"),
	Spec("prostredi", "Prostředí:", "middle", "mzda", title="Příplatek za prostředí"),
	Spec(
		"priplatek_1_hodiny",
		"Ost. přípl. 1:",
		"hours",
		"mzda",
		"quantity",
		title="Ostatní příplatky 1 (hod.)",
	),
	Spec("priplatek_1", "Ost. přípl. 1:", "middle", "mzda", title="Ostatní příplatky 1"),
	Spec("priplatek_2", "Ost. příplatky 2:", "middle", "mzda", title="Ostatní příplatky 2"),
	Spec("so_ne_noc_hodiny", "SO,NE,noc:", "hours", "mzda", "quantity", title="Sobota, neděle, noc (hod.)"),
	Spec("so_ne_noc", "SO,NE,noc:", "middle", "mzda", title="Příplatek za sobotu, neděli a noc"),
	Spec("rocni_odmeny", "Pololetní a roční odměny:", "middle", "mzda"),
	Spec("osvobozene_prijmy", "Příjmy osvobozené od daně:", "middle", "mzda"),
	Spec(
		"socialni_pojisteni_sazba",
		"Sociální pojištění:",
		"rate",
		"dan",
		"rate",
		title="Sociální pojištění (sazba)",
	),
	Spec(
		"zdravotni_pojisteni_sazba",
		"Zdravotní pojištění:",
		"rate",
		"dan",
		"rate",
		title="Zdravotní pojištění (sazba)",
	),
	Spec(
		"prevod_jine_pp",
		"Ost. složky mzdy a převod z jin. PP:",
		"right",
		"dan",
		title="Ostatní složky mzdy a převod z jiného PP",
	),
	Spec("pridanitelne_polozky", "Přidanitelné položky:", "right", "dan", blank=True),
	Spec(
		"ostatni_nezdanitelne",
		"Ostatní nezd. částky:",
		"right",
		"dan",
		blank=True,
		title="Ostatní nezdanitelné částky",
	),
	Spec("dilci_zaklad_dane", "Dílčí základ daně:", "right", "dan"),
	Spec("zaokrouhleny_zaklad_dane", "Zaokr. základ daně:", "right", "dan", title="Zaokrouhlený základ daně"),
	Spec("sleva_zakladni", "Sleva - základ:", "right", "dan", title="Sleva na poplatníka"),
	Spec("slevy_ostatni", "Ostatní slevy:", "right", "dan"),
	Spec(
		"sleva_deti",
		"Sleva/zvýh. na vyž. děti:",
		"right",
		"dan",
		title="Sleva / zvýhodnění na vyživované děti",
	),
	Spec(
		"zamestnavatel_sp_sazba",
		"Soc.poj :",
		"employer_rate",
		"zamestnavatel",
		"rate",
		title="Sociální pojištění zaměstnavatele (sazba)",
	),
	Spec(
		"zamestnavatel_sp",
		"Soc.poj :",
		"employer",
		"zamestnavatel",
		title="Sociální pojištění zaměstnavatele",
	),
	Spec(
		"zamestnavatel_zp_sazba",
		"Zdr.poj. :",
		"employer_rate",
		"zamestnavatel",
		"rate",
		title="Zdravotní pojištění zaměstnavatele (sazba)",
	),
	Spec(
		"zamestnavatel_zp",
		"Zdr.poj. :",
		"employer",
		"zamestnavatel",
		title="Zdravotní pojištění zaměstnavatele",
	),
	Spec("zamestnavatel_penzijni", "Penz.přip.:", "employer", "zamestnavatel", title="Penzijní připojištění"),
	Spec(
		"zamestnavatel_zivotni",
		"ŽP,DIP,PDP :",
		"employer",
		"zamestnavatel",
		title="Životní pojištění, DIP, PDP",
	),
	Spec("zamestnavatel_benefity", "Ost. benefity :", "employer", "zamestnavatel", title="Ostatní benefity"),
	Spec("cestovni_vydaje", "Cest. výdaje:", "benefits", "davky", title="Cestovní výdaje"),
	Spec("pracovni_prostredky", "Prac.prostředky:", "benefits", "davky", title="Pracovní prostředky"),
	Spec("stravne", "Stravné:", "benefits", "davky"),
	Spec("jine_naklady", "Jiné nákl.:", "benefits", "davky", title="Jiné náklady"),
	Spec("nemocenske", "Nemoc,ost.soc.:", "benefits", "davky", title="Nemocenské a ostatní sociální dávky"),
	Spec("srazka_sporeni", "Spoření:", "deductions", "srazky"),
	Spec("srazka_pojisteni", "Pojištění:", "deductions", "srazky"),
	Spec("srazka_vyzivne", "Výživné:", "deductions", "srazky"),
	Spec("srazka_ostatni_1", "Ost. srážky 1:", "deductions", "srazky", title="Ostatní srážky 1"),
	Spec("srazka_ostatni_2", "Ost. srážky 2:", "deductions", "srazky", title="Ostatní srážky 2"),
	Spec(
		"srazka_zp",
		"ZP za NV,NEAB:",
		"deductions",
		"srazky",
		title="Zdravotní pojištění za neplacené volno a absence",
	),
	Spec("zaloha", "Záloha:", "right", "vyplata", title="Záloha na mzdu"),
)

# values read in a special way: key -> (group, caption)
SPECIAL = {
	"dovolena_dny": ("doba", "Dovolená (dny)"),
	"dovolena_hodiny": ("doba", "Dovolená (hod.)"),
	"absence_pracovni_dny": ("doba", "Absence (pracovní dny)"),
	"absence_kalendarni_dny": ("doba", "Absence (kalendářní dny)"),
	"stredisko": ("vyplata", "Středisko"),
	"osobni_cislo": ("vyplata", "Osobní číslo"),
	"mzdovy_vymer": ("vyplata", "Mzdový výměr"),
	"typ_pp": ("vyplata", "Typ pracovního vztahu"),
	"pojistovna": ("vyplata", "Zdravotní pojišťovna"),
	"zpusob_vyplaty": ("vyplata", "Způsob výplaty"),
	"ucet": ("vyplata", "Účet"),
}
TEXT_KEYS = ("stredisko", "osobni_cislo", "mzdovy_vymer", "typ_pp", "pojistovna", "zpusob_vyplaty", "ucet")
# labels printed more than once on a slip
LABEL_COUNT = {"Svátky:": 2}
EXTRA_KEYS = tuple(dict.fromkeys([sp.key for sp in SPECS] + list(SPECIAL)))
CAPTIONS = {sp.key: sp.caption for sp in SPECS} | {k: v[1] for k, v in SPECIAL.items()}
KEY_GROUPS = {sp.key: sp.group for sp in SPECS} | {k: v[0] for k, v in SPECIAL.items()}

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
	fields: dict[str, Decimal]  # the 12 control fields
	box: tuple[float, float]  # (bottom, top) in points
	pdf: bytes = field(repr=False)
	extra: dict[str, Decimal | str] = field(default_factory=dict)  # the rest of the slip
	unread: list[str] = field(default_factory=list)  # keys of `extra` that could not be read
	period: tuple[int, int] | None = None  # (month, year) printed on the slip


def parse_number(text: str) -> Decimal | None:
	t = text.strip().replace(",- Kč", "").replace("Kč", "").strip()
	if not _NUMBER.match(t):
		return None
	t = t.replace(" ", "").replace(" ", "").replace(",-", "").replace(",", ".")
	try:
		return Decimal(t)
	except InvalidOperation:
		return None


def parse_rate(text: str) -> Decimal | None:
	t = text.strip()
	return parse_number(t[:-1]) if t.endswith("%") else None


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


def _labels(texts: list[_Text], label: str) -> list[_Text]:
	return sorted((t for t in texts if t.text == label), key=lambda t: -t.y)


def _candidates(texts: list[_Text], label: _Text, origin: float, zone: tuple[float, float], parse):
	"""Values of a column on the line of the label, the nearest line first."""
	out = []
	for t in texts:
		if t is label or not zone[0] <= t.x - origin < zone[1]:
			continue
		distance = abs(t.y - label.y)
		if distance > VALUE_TOLERANCE:
			continue
		value = parse(t.text)
		if value is not None:
			out.append((distance, t.x, value))
	return sorted(out, key=lambda c: c[0])


def _value(
	texts: list[_Text], label: _Text, origin: float, zone: str, kind: str = "amount"
) -> Decimal | None:
	"""The value of a label: the nearest number of its column on its line. None when there
	is none or when two are equally near."""
	found = _candidates(texts, label, origin, ZONES[zone], parse_rate if kind == "rate" else parse_number)
	if not found or (len(found) > 1 and found[1][0] - found[0][0] < AMBIGUOUS):
		return None
	return found[0][2]


def _value_below(texts: list[_Text], label: _Text, origin: float) -> Decimal | None:
	candidates = [
		t
		for t in texts
		if PAYOUT_BELOW[0] <= label.y - t.y <= PAYOUT_BELOW[1]
		and PAYOUT_ZONE[0] <= t.x - origin < PAYOUT_ZONE[1]
		and parse_number(t.text) is not None
	]
	if len(candidates) != 1:
		return None
	return parse_number(candidates[0].text)


def _text_below(texts: list[_Text], label: _Text, below: tuple[float, float]) -> str | None:
	"""Text printed under a label, starting at the same x."""
	candidates = [t for t in texts if abs(t.x - label.x) <= 4.0 and below[0] <= label.y - t.y <= below[1]]
	if len(candidates) != 1:
		return None
	return candidates[0].text[:MAX_TEXT]


def _text_right(texts: list[_Text], label: _Text, right: tuple[float, float]) -> str | None:
	candidates = [
		t
		for t in texts
		if right[0] <= t.x - label.x < right[1] and abs(t.y - label.y) <= VALUE_TOLERANCE and t is not label
	]
	if len(candidates) != 1:
		return None
	return candidates[0].text[:MAX_TEXT]


_ACCOUNT = re.compile(r"^(\d{1,6}-)?\d{2,10}/\d{4}$")
_INTEGER = re.compile(r"^\d{1,4}$")


def _read_extra(texts: list[_Text], anchor: _Text) -> tuple[dict, list[str], tuple[int, int] | None]:
	"""Everything outside the control fields. Returns (values, unread keys, period)."""
	origin = anchor.x
	values: dict[str, Decimal | str] = {}
	unread: list[str] = []

	def put(key: str, value, blank: bool = False) -> None:
		if value is not None:
			values[key] = value
		elif not blank:
			unread.append(key)

	def one(label: str, nth: int = 1) -> _Text | None:
		found = _labels(texts, label)
		# a label printed more or fewer times than expected: the layout is not the known one
		return found[nth - 1] if len(found) == LABEL_COUNT.get(label, 1) else None

	for spec in SPECS:
		label = one(spec.label, spec.nth)
		put(spec.key, label and _value(texts, label, origin, spec.zone, spec.kind), spec.blank)

	# leave taken in the month: days / hours
	label = one("D/h. - dovol:")
	pair = (
		sorted(_candidates(texts, label, origin, ZONES["left"], parse_number), key=lambda c: c[1])
		if label
		else []
	)
	put("dovolena_dny", pair[0][2] if len(pair) == 2 else None)
	put("dovolena_hodiny", pair[1][2] if len(pair) == 2 else None)

	# absence: working / calendar days in one text
	label = one("Pr./kal. dny abs.:")
	parts = []
	if label:
		for t in texts:
			near = (
				abs(t.y - label.y) <= VALUE_TOLERANCE and ZONES["left"][0] <= t.x - origin < ZONES["left"][1]
			)
			if near and t.text.count("/") == 1:
				parts.append([parse_number(p) for p in t.text.split("/")])
	ok = len(parts) == 1 and None not in parts[0]
	put("absence_pracovni_dny", parts[0][0] if ok else None)
	put("absence_kalendarni_dny", parts[0][1] if ok else None)

	label = one("Stř.:")
	put("stredisko", label and _text_right(texts, label, (10.0, 60.0)))
	label = one("EČ:")
	put("osobni_cislo", label and _text_right(texts, label, (20.0, 75.0)))
	for key, text in (
		("mzdovy_vymer", "Mzdový výměr :"),
		("typ_pp", "Typ PP :"),
		("pojistovna", "Pojišťovna :"),
	):
		label = one(text)
		put(key, label and _text_below(texts, label, (8.0, 16.0)))
	label = one("Způsob výplaty:")
	zpusob = label and _text_below(texts, label, (8.0, 19.0))
	put("zpusob_vyplaty", zpusob.rstrip(": ") if zpusob else None)
	ucet = label and _text_below(texts, label, (20.0, 30.0))
	put("ucet", ucet if ucet and _ACCOUNT.match(ucet) else None, blank=ucet is None)

	period = None
	label = one("Výplata za měsíc:")
	if label:
		numbers = sorted(
			(t.x, t.text)
			for t in texts
			if 40.0 <= t.x - label.x < 130.0
			and abs(t.y - label.y) <= VALUE_TOLERANCE
			and _INTEGER.match(t.text)
		)
		if len(numbers) == 2 and 1 <= int(numbers[0][1]) <= 12 and 2000 <= int(numbers[1][1]) <= 2100:
			period = (int(numbers[0][1]), int(numbers[1][1]))
	return values, unread, period


def _read_slip(texts: list[_Text], anchor: _Text, page_no: int) -> tuple[str, dict[str, Decimal]]:
	name = " ".join(
		t.text
		for t in sorted(texts, key=lambda t: t.x)
		if abs(t.y - anchor.y) <= LINE_TOLERANCE and anchor.x < t.x < anchor.x + NAME_MAX_OFFSET
	).strip()
	values: dict[str, Decimal] = {}
	missing = []
	for key, label_text in FIELDS.items():
		labels = [t for t in texts if t.text == label_text]
		if len(labels) != 1:
			missing.append(label_text)
			continue
		value = (
			_value_below(texts, labels[0], anchor.x)
			if key in VALUE_BELOW
			else _value(texts, labels[0], anchor.x, CONTROL_ZONES[key])
		)
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
			extra, unread, period = _read_extra(block, anchor)
			slips.append(
				PdfSlip(
					page=page_no,
					index=i + 1,
					name=name,
					fields=values,
					box=(bottom, top),
					pdf=_cut(content, page_index, bottom, top),
					extra=extra,
					unread=unread,
					period=period,
				)
			)
	return slips


# --- checks of the printed values ------------------------------------------------
# Pure functions over the values read by split(). They return captions of what does not
# fit, never values.

TOLERANCE = Decimal("0.005")
# Monthly limit of the 15 % advance tax (three times the average wage), by year. A base
# above the highest known limit is not checked.
TAX_LIMIT = {2025: 139671, 2026: 146901}
TAX_RATE = Decimal("0.15")
TAX_RATE_HIGH = Decimal("0.23")
# wage components that add up to the gross wage
GROSS_PARTS = (
	"mzda",
	"nahrada_svatky",
	"dovolena",
	"nahrady_ostatni",
	"prescas",
	"priplatek_svatky",
	"premie",
	"ostatni_slozky",
	"prostredi",
	"priplatek_1",
	"priplatek_2",
	"so_ne_noc",
	"rocni_odmeny",
)
SUM_PREFIX = "součet: "
# kind of employment printed on the slip -> codes of the "Druh činnosti" code list
TYP_PP_CODES = {
	"DPP": {"T", "U", "V", "W", "X", "Y", "Z", "ZA", "ZB", "ZC"},
	"DPČ": set("ABCDEFGHIJ"),
}


def extra_to_json(extra: dict) -> dict[str, str]:
	return {k: str(v) for k, v in extra.items()}


def extra_from_json(data: dict | None) -> dict[str, Decimal | str]:
	out: dict[str, Decimal | str] = {}
	for k, v in (data or {}).items():
		if k not in CAPTIONS or v is None:
			continue
		if k in TEXT_KEYS:
			out[k] = str(v)[:MAX_TEXT]
			continue
		try:
			number = Decimal(str(v))
		except InvalidOperation:
			continue
		if number.is_finite():
			out[k] = number
	return out


def _round_base(base: Decimal) -> Decimal:
	"""Base of the advance tax: up to 100 rounded up to whole crowns, above to whole hundreds."""
	if base <= 100:
		return Decimal(math.ceil(base))
	return Decimal(math.ceil(base / 100) * 100)


def _tax(base: Decimal, rok: int | None) -> Decimal | None:
	limit = TAX_LIMIT.get(rok) if rok else None
	if limit is None:
		limit = max(TAX_LIMIT.values())
		if base > limit:
			return None  # the limit of that year is not known
	low = min(base, Decimal(limit))
	return Decimal(math.ceil(low * TAX_RATE + (base - low) * TAX_RATE_HIGH))


def check_sums(fields: dict, extra: dict, rok: int | None = None) -> list[str]:
	"""Internal sums of one printed slip. A rule with a missing value is skipped: the missing
	value is already reported as unread."""
	v = {**extra, **fields}
	failed = []

	def rule(caption: str, keys: tuple[str, ...], fn) -> None:
		if any(not isinstance(v.get(k), Decimal) for k in keys):
			return
		result = fn(*(v[k] for k in keys))
		if result is not None and not result:
			failed.append(SUM_PREFIX + caption)

	rule(
		"zůstatek dovolené",
		(
			"dovolena_zustatek",
			"dovolena_minuly_rok",
			"dovolena_narok",
			"dovolena_kraceno",
			"dovolena_cerpano",
		),
		lambda zustatek, minuly, narok, kraceno, cerpano: abs(zustatek - (minuly + narok - kraceno - cerpano))
		<= TOLERANCE,
	)
	rule(
		"zaokrouhlený základ daně",
		("zaokrouhleny_zaklad_dane", "dilci_zaklad_dane"),
		lambda zaokrouhleny, dilci: zaokrouhleny == _round_base(dilci),
	)
	rule(
		"daň ze zaokrouhleného základu",
		("dan", "zaokrouhleny_zaklad_dane"),
		lambda dan, zaklad: None if _tax(zaklad, rok) is None else dan == _tax(zaklad, rok),
	)
	rule(
		"záloha na daň po slevách",
		("zaloha_na_dan", "dan", "sleva_zakladni", "slevy_ostatni", "sleva_deti"),
		lambda zaloha, dan, zakladni, ostatni, deti: zaloha
		== max(Decimal(0), dan - zakladni - ostatni - deti),
	)
	rule(
		"hrubá mzda (součet složek)", ("hruba_mzda", *GROSS_PARTS), lambda hruba, *parts: hruba == sum(parts)
	)
	return failed


def extra_mismatches(extra: dict, jmhz: dict | None, employer: dict | None = None) -> list[str]:
	"""Printed values against the informative values of the JMHZ report.

	`jmhz` is `Person.extra` (a missing key is zero, None is unreadable), None when the row
	has no such values at all. `employer` holds the employer insurance from the report.
	A value that is missing on either side is not compared.
	"""
	wrong = []

	def compare(key: str, expected, tolerance: Decimal = TOLERANCE) -> None:
		printed = extra.get(key)
		if not isinstance(printed, Decimal) or expected is None:
			return
		options = expected if isinstance(expected, tuple) else (expected,)
		if not any(abs(printed - Decimal(str(e))) <= tolerance for e in options):
			wrong.append(CAPTIONS[key])

	for key, value in (employer or {}).items():
		compare(key, value)
	if jmhz is None:
		return wrong

	def amount(*keys: str):
		values = [jmhz.get(k, 0) for k in keys]
		return None if None in values else sum(values)

	compare("sleva_zakladni", amount("sleva_poplatnik"))
	compare(
		"slevy_ostatni",
		amount("sleva_invalidita", "sleva_invalidita_rozsirena", "sleva_ztpp", "sleva_student"),
	)
	# the slip prints either the whole child tax benefit or only the part used as a credit
	deti = (amount("zvyhodneni_deti"), amount("sleva_deti"))
	compare("sleva_deti", None if None in deti else deti)
	compare("osvobozene_prijmy", amount("osvobozene_prijmy"))
	prumer = jmhz.get("prumerny_vydelek")
	if prumer:  # not reported, or different on more employments: nothing to compare with
		# the report has three decimals, the slip two: any rounding of the third one fits
		compare("prumer_nahrady", prumer)
	typ, druh = extra.get("typ_pp"), jmhz.get("druh_cinnosti")
	if isinstance(typ, str) and druh and typ.upper() in TYP_PP_CODES:
		if not {c.strip() for c in str(druh).split(",")} <= TYP_PP_CODES[typ.upper()]:
			wrong.append(CAPTIONS["typ_pp"])
	return wrong
