"""Parser of the JMHZ monthly employer report (XML) into slip data.

Pure Python, must not import frappe: unit tests run without a bench and the
parser can be run locally (`python -I`) on real files.

Security:
- input is bytes, the file is never written to disk;
- defusedxml with `forbid_dtd=True` (no DTD, no entities, no external references);
- size limit is checked before parsing;
- elements about children and other persons (`zvyhodneniDetiMesic`, `zvyhodneniNaDeti`,
  `slevaNaPartnera`) and the alternative identification (name, birth date) are never
  read, only the amounts of the tax credits next to them;
- issue messages never contain amounts, IK MPSV or ID PPV.

Persons with more employments (PPV) are merged into one person. The employee
summary data (`souhrnDataZec`: 10286, 10344, tax, health insurance) is reported
only on the primary employment, so all balance checks are done per person.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal, InvalidOperation
from xml.etree.ElementTree import Element
from xml.etree.ElementTree import ParseError as XmlParseError

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from erpnext_banking.jmhz import components as C
from erpnext_banking.jmhz.model import Issue, JmhzParseError, Line, ParsedFile, Person

MAX_XML_BYTES = 5 * 1024 * 1024
TYPY_PODANI = ("R", "O", "S")
_ORDER = {c.name: i for i, c in enumerate(C.COMPONENTS)}
_DRUH_CINNOSTI = re.compile(C.DRUH_CINNOSTI_PATTERN)


# --- XML helpers -------------------------------------------------------------


def _local(tag: str) -> str:
	return tag.rsplit("}", 1)[-1]


def _namespace(tag: str) -> str:
	return tag[1:].split("}", 1)[0] if tag.startswith("{") else ""


def _child(el: Element, name: str) -> Element | None:
	return next((c for c in el if _local(c.tag) == name), None)


def _find(el: Element, path: str) -> Element | None:
	cur: Element | None = el
	for part in path.split("."):
		cur = _child(cur, part)
		if cur is None:
			return None
	return cur


def _text(el: Element, path: str) -> str | None:
	e = _find(el, path)
	if e is None or e.text is None:
		return None
	t = e.text.strip()
	return t or None


def pseudonym(ik_mpsv: str) -> str:
	"""Display label of a person: `P-` + first 8 hex chars of SHA-256 of the IK MPSV."""
	return "P-" + hashlib.sha256(ik_mpsv.encode()).hexdigest()[:8]


# --- reading values with issue reporting ------------------------------------


class _Reader:
	"""Reads values of one person and records issues instead of raising."""

	def __init__(self) -> None:
		self.issues: list[Issue] = []
		self.invalid = False  # a value was unusable -> balance checks are meaningless
		self.unread: set[str] = set()  # keys of informative values that could not be read

	def add(self, code: str, severity: str, message: str) -> None:
		issue = Issue(code, severity, message)
		if issue not in self.issues:
			self.issues.append(issue)

	def _decimal(self, el: Element, path: str, *, required: bool) -> Decimal | None:
		t = _text(el, path)
		if t is None:
			if required:
				self.invalid = True
				self.add("P06", "block", f"Chybí povinný údaj {path}.")
			return None
		try:
			d = Decimal(t)
		except InvalidOperation:
			d = None
		if d is None or not d.is_finite():
			self.invalid = True
			self.add("P04", "block", f"Hodnota v {path} není číslo.")
			return None
		if d < 0:
			self.invalid = True
			self.add("P05", "block", f"Záporná hodnota v {path}.")
			return None
		return d

	def amount(self, el: Element, path: str, *, required: bool = False) -> int:
		"""Amount in whole CZK. A missing optional element is 0."""
		d = self._decimal(el, path, required=required)
		if d is None:
			return 0
		if d != d.to_integral_value():
			self.invalid = True
			self.add("P04", "block", f"Neceločíselná částka v {path}.")
			return 0
		return int(d)

	def quantity(self, el: Element, path: str) -> Decimal | None:
		"""Hours and days, may have decimals."""
		return self._decimal(el, path, required=False)

	@staticmethod
	def flag(el: Element, path: str) -> bool:
		return (_text(el, path) or "").lower() in ("true", "1")

	def extra(self, el: Element, x: C.Extra, path: str) -> Decimal | str | None:
		"""Informative value for the full slip. None = missing or unreadable.

		Never blocks the person and never marks it invalid: the slip amounts do not depend
		on these values. An unreadable value is reported as P14 and left out.
		"""
		t = _text(el, path)
		if t is None:
			return None
		if x.kind == "code":
			if _DRUH_CINNOSTI.fullmatch(t):
				return t
		else:
			try:
				d = Decimal(t)
			except InvalidOperation:
				d = None
			whole = x.kind == "amount"
			if (
				d is not None
				and d.is_finite()
				and (x.signed or d >= 0)
				and (not whole or d == d.to_integral_value())
			):
				return d
		self.unread.add(x.key)
		self.add("P14", "warn", f"Doplňkový údaj {path} nejde přečíst, na pásce nebude.")
		return None


# --- person ------------------------------------------------------------------


class _Form:
	__slots__ = ("kind", "body", "typ_formulare")

	def __init__(self, kind: str, body: Element, typ_formulare: str | None):
		self.kind = kind
		self.body = body
		self.typ_formulare = typ_formulare


def _build_person(ik: str, forms: list[_Form], schema: str, typ_podani: str, label: str) -> Person:
	r = _Reader()
	if not ik:
		r.add("P06", "block", f"Chybí povinný údaj {C.IK_MPSV}.")

	unsupported = sorted({f.kind for f in forms if f.kind != C.SUPPORTED_FORM})
	if unsupported:
		r.add(
			"P03",
			"block",
			f"Osoba má formulář typu {', '.join(unsupported)}; podporovaný je jen {C.SUPPORTED_FORM}.",
		)
	if typ_podani == "R" and any(f.typ_formulare != "R" for f in forms):
		r.add("P12", "block", "Formulář osoby v řádném podání není řádný (typFormulare ≠ R).")
	if typ_podani == "O" and any(f.typ_formulare == "S" for f in forms):
		# cancelling a single employment inside a corrective report: the slip cannot be rebuilt
		# from the remaining data, HR resolves it manually
		r.add(
			"P13",
			"block",
			"Opravné podání stornuje pracovněprávní vztah osoby (typFormulare S); řešte ručně.",
		)

	supported = [f.body for f in forms if f.kind == C.SUPPORTED_FORM]
	amounts: dict[str, int] = {}

	def add_line(comp: C.Component, value: int) -> None:
		if value:
			amounts[comp.name] = amounts.get(comp.name, 0) + value

	id_ppv: list[str] = []
	mzda_total = nahrady_total = 0
	sp = sp_zam = 0
	zuctovano = cisty = osvobozeno = bonus = zp = zaloha = srazkova = zp_zam = preplatek = 0
	zaklad_dane = vypoctena_dan = 0
	souhrn_count = 0
	exekuce = srazky_ozp = False
	info: dict[str, Decimal] = {}
	extra_sums: dict[str, Decimal] = {}
	prumery: list[Decimal] = []
	druhy_cinnosti: list[str] = []

	for b in supported:
		ppv = _text(b, C.ID_PPV)
		if ppv:
			id_ppv.append(ppv)
		else:
			r.add("P06", "block", f"Chybí povinný údaj {C.ID_PPV}.")

		# wages for work (10328), optionally split into 10329-10332
		mzda = r.amount(b, C.MZDA_ZUCTOVANA, required=True)
		mzda_total += mzda
		if _find(b, C.MZDA_ROZPAD) is not None:
			rozpad = 0
			for comp in C.MZDA_ROZPAD_ITEMS:
				v = r.amount(b, comp.path)
				add_line(comp, v)
				rozpad += v
			if mzda < rozpad:
				r.invalid = True
				r.add("P05", "block", "Rozpad mzdy (10329–10332) je větší než mzda za práci (10328).")
			else:
				add_line(C.MZDA_OSTATNI, mzda - rozpad)
		else:
			add_line(C.MZDA, mzda)

		# wage compensations (10337), optionally split into 10338-10342
		if _find(b, C.NAHRADY) is not None:
			nahrady = r.amount(b, C.NAHRADY_CELKEM, required=True)
			nahrady_total += nahrady
			rozpad = 0
			for comp in C.NAHRADY_ITEMS:
				v = r.amount(b, comp.path)
				add_line(comp, v)
				rozpad += v
			if nahrady < rozpad:
				r.invalid = True
				r.add("P05", "block", "Rozpad náhrad (10338–10342) je větší než náhrady celkem (10337).")
			else:
				add_line(C.NAHRADY_OSTATNI, nahrady - rozpad)

		# social insurance is per employment
		sp += r.amount(b, C.SOCIALNI_POJISTENI.path)
		sp_zam += r.amount(b, C.SP_ZAMESTNAVATEL)

		for key, path in C.INFO_PATHS.items():
			if C.INFO_ONLY_IN.get(key, schema) != schema:
				continue
			q = r.quantity(b, path)
			if q is not None:
				info[key] = info.get(key, Decimal(0)) + q

		for x in C.EXTRAS:
			path = x.paths.get(schema)
			if path is None or x is C.OSVOBOZENE_PRIJMY:  # 10289 is read strictly below
				continue
			v = r.extra(b, x, path)
			if v is None:
				continue
			if x.kind == "code":
				if v not in druhy_cinnosti:
					druhy_cinnosti.append(v)
			elif x.kind == "average":
				prumery.append(v)
			else:
				extra_sums[x.key] = extra_sums.get(x.key, Decimal(0)) + v

		# employee summary data, only on the primary employment
		if _find(b, C.SOUHRN_ZEC) is not None:
			souhrn_count += 1
			zuctovano += r.amount(b, C.ZUCTOVANO, required=True)
			cisty += r.amount(b, C.CISTY_PRIJEM, required=True)
			osvobozeno += r.amount(b, C.OSVOBOZENO)
			bonus += r.amount(b, C.DANOVY_BONUS.path)
			zp += r.amount(b, C.ZDRAVOTNI_POJISTENI.path)
			zaloha += r.amount(b, C.ZALOHA_NA_DAN.path)
			zaklad_dane += r.amount(b, C.ZAKLAD_DANE)
			vypoctena_dan += r.amount(b, C.VYPOCTENA_ZALOHA)
			srazkova += r.amount(b, C.SRAZKOVA_DAN_PATH[schema])
			zp_zam += r.amount(b, C.ZP_ZAMESTNAVATEL)
			# signed (overpayment / underpayment), only explains P01 -> read leniently
			preplatek += _sum_amounts(b, C.ROCNI_ZUCTOVANI_PREPLATEK) or 0
			exekuce = exekuce or r.flag(b, C.SRAZKY_EVIDOVANY)
			if any([r.amount(b, p) for p in C.SRAZKY_OZP]):
				srazky_ozp = True

	if supported and not souhrn_count:
		r.invalid = True
		r.add("P06", "block", f"Chybí souhrnná data zaměstnance ({C.SOUHRN_ZEC}: 10286, 10344).")

	if souhrn_count and not r.invalid:
		ostatni = zuctovano - mzda_total - nahrady_total
		if ostatni < 0:
			r.invalid = True
			r.add("P05", "block", "Zúčtovaný příjem (10286) je menší než mzda (10328) a náhrady (10337).")
		elif ostatni > 0:
			add_line(C.OSTATNI_PRIJMY, ostatni)
			r.add(
				"P09",
				"warn",
				"Zúčtovaný příjem (10286) je větší než mzda (10328) a náhrady (10337): "
				f"vznikl řádek „{C.OSTATNI_PRIJMY.name}“, ověřte ho proti pásce.",
			)

	add_line(C.DANOVY_BONUS, bonus)
	add_line(C.SOCIALNI_POJISTENI, sp)
	add_line(C.ZDRAVOTNI_POJISTENI, zp)
	add_line(C.ZALOHA_NA_DAN, zaloha)
	add_line(C.SRAZKOVA_DAN, srazkova)

	# definition of 10344 (Pokyny MH 1.4.14 / 1.5): taxable income - SP - ZP - tax,
	# without the tax bonus, negative -> 0
	if souhrn_count and not r.invalid:
		vypocet = max(0, zuctovano - sp - zp - zaloha - srazkova)
		if vypocet != cisty:
			if osvobozeno and vypocet - osvobozeno == cisty:
				r.add(
					"P07",
					"block",
					"Čistý příjem (10344) nezahrnuje osvobozené příjmy (10289); řešte individuálně.",
				)
			else:
				message = "Čistý příjem (10344) neodpovídá výpočtu 10286 − 10370 − 10371 − 10305 − 10309."
				if preplatek and cisty - vypocet == preplatek:
					message += " Rozdíl odpovídá přeplatku z ročního zúčtování (10322, 10323)."
				r.add("P01", "block", message)

	if exekuce:
		r.add(
			"P02",
			"block",
			"Zaměstnavatel eviduje srážky ze mzdy (10116); částka v JMHZ není, doplňte ruční srážku.",
		)
	if srazky_ozp:
		r.add("P08", "block", "Vyplněné srážky 10350–10353; ověřte a řešte ručně.")
	if len(supported) > 1:
		r.add("P10", "info", "Více pracovněprávních vztahů sloučeno do jedné pásky.")

	lines = [
		Line(name, value, C.BY_NAME[name].kind, C.BY_NAME[name].jmhz_id)
		for name, value in sorted(amounts.items(), key=lambda kv: _ORDER[kv[0]])
	]
	# A person with a blocking issue never counts as "without income",
	# otherwise the block would be hidden behind the status "Bez příjmu".
	has_income = bool(lines) or any(i.severity == "block" for i in r.issues)
	if not has_income:
		r.add("P11", "info", "Nulový příjem, páska se nevytváří.")

	if osvobozeno:
		extra_sums[C.OSVOBOZENE_PRIJMY.key] = Decimal(osvobozeno)
	# an average of more employments cannot be summed: kept only when it is the same everywhere
	if len(set(prumery)) == 1:
		extra_sums[C.PRUMERNY_VYDELEK.key] = prumery[0]
	extra: dict[str, int | float | str | None] = {}
	for x in C.EXTRAS:
		if x.key in r.unread:
			extra[x.key] = None
		elif x.kind == "code":
			if druhy_cinnosti:
				extra[x.key] = ", ".join(druhy_cinnosti)
		elif extra_sums.get(x.key):
			v = extra_sums[x.key]
			extra[x.key] = int(v) if x.kind == "amount" else float(v)

	return Person(
		ik_mpsv=ik,
		pseudonym=pseudonym(ik) if ik else label,
		id_ppv=id_ppv,
		lines=lines,
		zuctovano=zuctovano,
		cisty_prijem=cisty,
		bonus=bonus,
		k_vyplate=cisty + bonus,
		sp_zamestnavatel=sp_zam,
		zp_zamestnavatel=zp_zam,
		info={k: float(v) for k, v in info.items()},
		exekuce=exekuce,
		has_income=has_income,
		issues=r.issues,
		zdanitelna_mzda=zaklad_dane,
		vypoctena_dan=vypoctena_dan,
		extra=extra,
	)


# --- file --------------------------------------------------------------------


def _header_int(h: Element, name: str, low: int, high: int) -> int:
	t = _text(h, name)
	try:
		v = int(t) if t is not None else None
	except ValueError:
		v = None
	if v is None or not low <= v <= high:
		raise JmhzParseError("F03", f"Chybí nebo je neplatný údaj hlavicka.{name}.")
	return v


def _sum_amounts(el: Element, paths: tuple[str, ...]) -> int | None:
	"""Lenient sum for report-level cross checks. None = some value is not an integer."""
	total = 0
	for path in paths:
		t = _text(el, path)
		if t is None:
			continue
		try:
			d = Decimal(t)
		except InvalidOperation:
			return None
		if not d.is_finite() or d != d.to_integral_value():
			return None
		total += int(d)
	return total


def parse(content: bytes, *, file_name: str | None = None) -> ParsedFile:
	"""Parse a JMHZ report. Raises `JmhzParseError` on a fatal error (F01-F03)."""
	if not isinstance(content, bytes | bytearray):
		raise TypeError("content must be bytes")
	content = bytes(content)
	if len(content) > MAX_XML_BYTES:
		raise JmhzParseError("F02", "Soubor je větší než 5 MB.")
	sha256 = hashlib.sha256(content).hexdigest()

	# `from None`: never chain exceptions that could carry file content
	try:
		root = fromstring(content, forbid_dtd=True)
	except DefusedXmlException:
		raise JmhzParseError("F02", "Soubor obsahuje DTD nebo entity, které nejsou povolené.") from None
	except XmlParseError:
		raise JmhzParseError("F02", "Soubor není platné XML.") from None

	schema = C.ROOT_NAMESPACES.get(_namespace(root.tag))
	if schema is None or _local(root.tag) != C.ROOT_ELEMENT:
		raise JmhzParseError("F01", "Neznámý formát: soubor není podání JMHZ ve schématu 1.4 ani 1.5.")

	h = _child(root, C.HLAVICKA)
	if h is None:
		raise JmhzParseError("F03", "Chybí hlavička podání.")
	rok = _header_int(h, "rok", 2000, 2100)
	mesic = _header_int(h, "mesic", 1, 12)
	typ_podani = _text(h, "typPodani")
	if typ_podani not in TYPY_PODANI:
		raise JmhzParseError("F03", "Chybí nebo je neplatný údaj hlavicka.typPodani.")

	issues: list[Issue] = []
	if typ_podani != "R":
		druh = "opravné" if typ_podani == "O" else "storno"
		issues.append(
			Issue("F04", "info", f"Podání je {druh} (typPodani {typ_podani}); nahrazuje dřívější import.")
		)
	if schema == C.SCHEMA_15:
		issues.append(
			Issue("F07", "info", "Schéma 1.5 zatím nebylo ověřené na reálném souboru; zkontrolujte výsledek.")
		)

	vendor_el = _child(root, "VENDOR")
	vendor = None
	if vendor_el is not None:
		vendor = " ".join(
			v for v in (vendor_el.get("productName"), vendor_el.get("productVersion")) if v
		).strip()

	# forms grouped by person, in file order
	container, item = C.FORM_CONTAINER[schema]
	box = _child(root, container)
	grouped: dict[str, tuple[str, list[_Form]]] = {}
	empty_forms = 0
	sum_zaloha: int | None = 0
	sum_bonus: int | None = 0
	sum_sp: int | None = 0
	for form in box if box is not None else ():
		if _local(form.tag) != item:
			continue
		fh = _child(form, "hlavicka")
		typ_formulare = _text(fh, "typFormulare") if fh is not None else None
		bodies = [c for c in form if _local(c.tag) != "hlavicka"]
		if not bodies:
			empty_forms += 1
		for body in bodies:
			ik = _text(body, C.IK_MPSV) or ""
			# persons without IK MPSV must never be merged together
			key = ik or f"\0{len(grouped)}"
			grouped.setdefault(key, (ik, []))[1].append(_Form(_local(body.tag), body, typ_formulare))

			z = _sum_amounts(body, (C.ZALOHA_NA_DAN.path,))
			bo = _sum_amounts(body, (C.DANOVY_BONUS.path,))
			s = _sum_amounts(body, (C.SOCIALNI_POJISTENI.path,))
			sum_zaloha = None if sum_zaloha is None or z is None else sum_zaloha + z
			sum_bonus = None if sum_bonus is None or bo is None else sum_bonus + bo
			sum_sp = None if sum_sp is None or s is None else sum_sp + s

	if empty_forms:
		issues.append(
			Issue("F09", "info", f"Formuláře bez dat osoby (jen hlavička): {empty_forms}; nezpracovávají se.")
		)

	persons = []
	missing_ik = 0
	for ik, forms in grouped.values():
		label = ""
		if not ik:
			missing_ik += 1
			label = f"P-bez-IK-{missing_ik}"
		persons.append(_build_person(ik, forms, schema, typ_podani, label))

	# cross checks with the report-level summary
	souhrn = _find(root, "souhrn")
	if souhrn is not None:
		zal = _sum_amounts(root, (C.SOUHRN_ZALOHA,))
		bon = _sum_amounts(root, (C.SOUHRN_BONUS,))
		if None in (zal, bon, sum_zaloha, sum_bonus) or (zal, bon) != (sum_zaloha, sum_bonus):
			issues.append(
				Issue(
					"F05",
					"warn",
					"Součet záloh a bonusů z formulářů nesedí se souhrnem podání (10034, 10035).",
				)
			)
	if _find(root, "PVPOJ") is not None:
		pvpoj = _sum_amounts(root, (C.PVPOJ_SP_ZAMESTNANCU,))
		if pvpoj is None or sum_sp is None or pvpoj != sum_sp:
			issues.append(
				Issue(
					"F06", "warn", "Součet SP zaměstnanců z formulářů nesedí s přehledem o pojistném (10028)."
				)
			)

	return ParsedFile(
		schema=schema,
		rok=rok,
		mesic=mesic,
		typ_podani=typ_podani,
		id_podani=_text(h, "idPodani"),
		vendor=vendor or None,
		sha256=sha256,
		persons=persons,
		issues=issues,
		file_name=file_name,
		variabilni_symbol=_text(h, "variabilniSymbol"),
	)
