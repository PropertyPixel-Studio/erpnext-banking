"""Data of the full slip stored on the Salary Slip (`jmhz_slip_data`, JSON).

Pure Python, must not import frappe. The print format reads only this field and the
`jmhz_*` fields of the slip, never the import: an employee can read the own slip but
not the import, its rows or the printed report.

Shape:

    {"v": 1,
     "pdf": {"blocks": [{"key", "title", "rows": [{"key", "label", "value", "kind"}]}],
             "unread": [label, ...]} | null,
     "jmhz": {"rows": [{"key", "label", "value", "kind"}]} | null}

`pdf` is the slip printed by the payroll system as it was read and reviewed, block by
block in reading order; a printed zero is kept, an unread value is left out and named in
`unread`. `jmhz` are the informative values of the JMHZ report. `kind` says how to print
a value: amount (CZK), quantity (hours, days), rate (%) or text. A slip created before
these values were read has no data at all.

The JMHZ report decides the money. The totals of the printed slip that the report also
has (`AUTHORITATIVE`) are therefore taken from the report, so the print never shows a
total that differs from the slip amounts, not even after HR accepted a mismatch.
"""

from __future__ import annotations

from decimal import Decimal

from erpnext_banking.jmhz import components as C
from erpnext_banking.jmhz import pdf_slips as P

VERSION = 1

# reading order of the printed slip, block by block
LAYOUT: dict[str, tuple[str, ...]] = {
	"doba": (
		"dny",
		"hodiny",
		"dny_svatek_ve_mzde",
		"dny_svatek_nahrada",
		"dovolena_dny",
		"dovolena_hodiny",
		"absence_pracovni_dny",
		"absence_kalendarni_dny",
		"dny_nemoc",
		"prumer_nahrady",
		"prumer_dpn",
	),
	"dovolena": (
		"dovolena_minuly_rok",
		"dovolena_narok",
		"dovolena_kraceno",
		"dovolena_cerpano",
		"dovolena_zustatek",
	),
	"mzda": (
		"mzda",
		"nahrada_svatky",
		"dovolena",
		"nahrady_ostatni",
		"prescas_hodiny",
		"prescas",
		"priplatek_svatky_hodiny",
		"priplatek_svatky",
		"premie",
		"ostatni_slozky",
		"prostredi_hodiny",
		"prostredi",
		"priplatek_1_hodiny",
		"priplatek_1",
		"priplatek_2",
		"so_ne_noc_hodiny",
		"so_ne_noc",
		"rocni_odmeny",
		"hruba_mzda",
		"osvobozene_prijmy",
	),
	"dan": (
		"socialni_pojisteni_sazba",
		"socialni_pojisteni",
		"zdravotni_pojisteni_sazba",
		"zdravotni_pojisteni",
		"prevod_jine_pp",
		"zdanitelna_mzda",
		"pridanitelne_polozky",
		"ostatni_nezdanitelne",
		"dilci_zaklad_dane",
		"zaokrouhleny_zaklad_dane",
		"dan",
		"sleva_zakladni",
		"slevy_ostatni",
		"sleva_deti",
		"zaloha_na_dan",
		"cista_mzda",
	),
	"zamestnavatel": (
		"zamestnavatel_sp_sazba",
		"zamestnavatel_sp",
		"zamestnavatel_zp_sazba",
		"zamestnavatel_zp",
		"zamestnavatel_penzijni",
		"zamestnavatel_zivotni",
		"zamestnavatel_benefity",
	),
	"davky": ("cestovni_vydaje", "pracovni_prostredky", "stravne", "jine_naklady", "nemocenske"),
	"srazky": (
		"srazka_sporeni",
		"srazka_pojisteni",
		"srazka_vyzivne",
		"srazka_ostatni_1",
		"srazka_ostatni_2",
		"srazka_zp",
	),
	"vyplata": (
		"zaloha",
		"k_vyplate",
		"zpusob_vyplaty",
		"ucet",
		"mzdovy_vymer",
		"typ_pp",
		"pojistovna",
		"stredisko",
		"osobni_cislo",
	),
}

CONTROL_CAPTIONS = {
	"hruba_mzda": "Hrubá mzda",
	"mzda": "Mzda",
	"dovolena": "Náhrada za dovolenou",
	"socialni_pojisteni": "Sociální pojištění",
	"zdravotni_pojisteni": "Zdravotní pojištění",
	"zaloha_na_dan": "Záloha na daň po slevě",
	"zdanitelna_mzda": "Zdanitelná mzda",
	"dan": "Daň",
	"cista_mzda": "Čistá mzda",
	"k_vyplate": "K výplatě",
	"hodiny": "Odpracované hodiny",
	"dny": "Odpracované dny",
}
CAPTIONS = P.CAPTIONS | CONTROL_CAPTIONS
# control fields printed from the JMHZ report even when the printed slip says otherwise
AUTHORITATIVE = (
	"hruba_mzda",
	"socialni_pojisteni",
	"zdravotni_pojisteni",
	"zaloha_na_dan",
	"zdanitelna_mzda",
	"dan",
	"cista_mzda",
	"k_vyplate",
)
# of the report's own values these two are not on the Salary Slip, the print needs them
JMHZ_TAX_ROWS = (("zdanitelna_mzda", "Zdanitelná mzda"), ("dan", "Daň před slevami"))
_QUANTITIES = {
	"hodiny",
	"dny",
	"dovolena_dny",
	"dovolena_hodiny",
	"absence_pracovni_dny",
	"absence_kalendarni_dny",
}
KINDS = (
	{key: "amount" for key in P.FIELDS}
	| {sp.key: sp.kind for sp in P.SPECS}
	| {key: "quantity" for key in _QUANTITIES}
	| {key: "text" for key in P.TEXT_KEYS}
)
_JMHZ_KINDS = {"amount": "amount", "quantity": "quantity", "average": "quantity", "code": "text"}


def _plain(value):
	"""JSON value: whole numbers as int, other numbers as float, text as it is."""
	if isinstance(value, Decimal):
		return int(value) if value == value.to_integral_value() else float(value)
	return value


def pdf_blocks(fields: dict, extra: dict, jmhz_control: dict | None = None) -> list[dict]:
	"""The printed slip block by block. Values that were not read are left out."""
	values = {**extra, **fields}
	for key in AUTHORITATIVE:
		if (jmhz_control or {}).get(key) is not None:
			values[key] = jmhz_control[key]
	blocks = []
	for group, keys in LAYOUT.items():
		rows = [
			{"key": key, "label": CAPTIONS[key], "value": _plain(values[key]), "kind": KINDS[key]}
			for key in keys
			if values.get(key) is not None
		]
		if rows:
			blocks.append({"key": group, "title": P.GROUPS[group], "rows": rows})
	return blocks


def jmhz_rows(jmhz_extra: dict, jmhz_control: dict | None = None) -> list[dict]:
	rows = [
		{"key": key, "label": label, "value": _plain(jmhz_control[key]), "kind": "amount"}
		for key, label in JMHZ_TAX_ROWS
		if (jmhz_control or {}).get(key) is not None
	]
	for x in C.EXTRAS:
		value = jmhz_extra.get(x.key)
		if value is None:  # not reported, or not readable
			continue
		if x is C.DRUH_CINNOSTI:
			value = ", ".join(C.druh_cinnosti_label(code.strip()) for code in str(value).split(","))
		rows.append({"key": x.key, "label": x.label, "value": value, "kind": _JMHZ_KINDS[x.kind]})
	return rows


def build(
	jmhz_extra: dict | None,
	pdf_fields: dict | None = None,
	pdf_extra: dict | None = None,
	pdf_unread: list[str] | None = None,
	jmhz_control: dict | None = None,
) -> dict | None:
	"""Data for `jmhz_slip_data`. None when nothing beyond the slip amounts is known.

	`pdf_extra` None means there is no printed slip, or one read by a version that read only
	the control fields: then the slip is printed without the printed-slip blocks.
	`jmhz_control` are the control fields as the JMHZ report has them.
	"""
	pdf = None
	if pdf_fields is not None and pdf_extra is not None:
		pdf = {
			"blocks": pdf_blocks(pdf_fields, pdf_extra, jmhz_control),
			"unread": [CAPTIONS[key] for key in pdf_unread or () if key in CAPTIONS],
		}
	jmhz = {"rows": jmhz_rows(jmhz_extra, jmhz_control)} if jmhz_extra is not None else None
	if pdf is None and jmhz is None:
		return None
	return {"v": VERSION, "pdf": pdf, "jmhz": jmhz}
