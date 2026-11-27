"""Single source of truth for the JMHZ -> Salary Component mapping.

JMHZ ("jednotné měsíční hlášení zaměstnavatele") is the Czech monthly employer
report. This module is shared by the parser (pure Python) and the setup code,
so it must never import frappe.

Paths are dot-separated local element names. Person-level paths are relative to
a `bezPriznaku` form, report-level paths are relative to the root `jmhz`
element. Numbers in comments are attribute IDs from the MPSV data dictionary.
Paths were checked against the official XSD 1.4.3.6 and 1.5.0.3 and against the
data dictionaries 1.4.1.6 and 1.5.0.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["earning", "deduction"]
ExtraKind = Literal["amount", "quantity", "average", "code"]

SCHEMA_14 = "1.4"
SCHEMA_15 = "1.5"

# Root namespace -> schema version (JMHZ 1.4.x for 2026, 1.5.x from 1 January 2027).
ROOT_NAMESPACES = {
	"http://schemas.cssz.cz/JMHZ/podani/1.0": SCHEMA_14,
	"http://schemas.cssz.cz/JMHZ/podani/2027": SCHEMA_15,
}
ROOT_ELEMENT = "jmhz"

# Container of the per-employment forms. Renamed in 1.5.
FORM_CONTAINER = {
	SCHEMA_14: ("formulareOsob", "formularOsoby"),
	SCHEMA_15: ("formulareZamestnani", "formularZamestnani"),
}
SUPPORTED_FORM = "bezPriznaku"


@dataclass(frozen=True)
class Component:
	name: str
	abbr: str
	kind: Kind
	jmhz_id: int | None = None  # None = derived (remainder) or manual component
	path: str | None = None  # None = not read from a single element

	@property
	def component_type(self) -> str:
		"""Salary Component `type` in HRMS."""
		return "Earning" if self.kind == "earning" else "Deduction"


TARIF = Component("Tarifní mzda", "JM_TARIF", "earning", 10329, "mzda.mzdaRozpad.tarif")
PREMIE_PRAVIDELNE = Component(
	"Prémie a odměny pravidelné", "JM_PREM_P", "earning", 10330, "mzda.mzdaRozpad.odmenyPravidelne"
)
PREMIE_NEPRAVIDELNE = Component(
	"Prémie a odměny nepravidelné", "JM_PREM_N", "earning", 10331, "mzda.mzdaRozpad.odmenyNepravidelne"
)
PRIPLATKY = Component("Příplatky", "JM_PRIPL", "earning", 10332, "mzda.mzdaRozpad.priplatky.celkem")
MZDA_OSTATNI = Component("Mzda - ostatní", "JM_MZDA_O", "earning")  # 10328 - sum(10329..10332)
MZDA = Component("Mzda za práci", "JM_MZDA", "earning", 10328, "mzda.mzdaZuctovana")  # no mzdaRozpad
NAHRADA_DOVOLENA = Component("Náhrada za dovolenou", "JM_N_DOV", "earning", 10338, "mzda.nahrady.dovolena")
NAHRADA_SVATKY = Component("Náhrada za svátky", "JM_N_SVA", "earning", 10339, "mzda.nahrady.svatky")
NAHRADA_PREKAZKY_ZAMESTNAVATEL = Component(
	"Náhrada - překážky zaměstnavatel", "JM_N_PZL", "earning", 10340, "mzda.nahrady.prekazkyZamestnavatel"
)
NAHRADA_PREKAZKY_ZAMESTNANEC = Component(
	"Náhrada - překážky zaměstnanec", "JM_N_PZC", "earning", 10341, "mzda.nahrady.prekazkyZamestnanec"
)
NAHRADA_DPN = Component("Náhrada při DPN", "JM_N_DPN", "earning", 10342, "mzda.nahrady.docasnaNeschopnost")
NAHRADY_OSTATNI = Component("Náhrady - ostatní", "JM_N_OST", "earning")  # 10337 - sum(10338..10342)
OSTATNI_PRIJMY = Component("Ostatní zúčtované příjmy", "JM_OST", "earning")  # 10286 - 10328 - 10337
DANOVY_BONUS = Component("Daňový bonus", "JM_BONUS", "earning", 10306, "souhrnDataZec.zalohaNaDan.danBonus")
SOCIALNI_POJISTENI = Component(
	"Sociální pojištění zaměstnanec",
	"JM_SP",
	"deduction",
	10370,
	"pojisteni.pojisteniZamestnanec.socialniPojisteni",
)
ZDRAVOTNI_POJISTENI = Component(
	"Zdravotní pojištění zaměstnanec",
	"JM_ZP",
	"deduction",
	10371,
	"souhrnDataZec.zdravPojZamestnanec.zdravotniPojisteni",
)
ZALOHA_NA_DAN = Component(
	"Záloha na daň", "JM_ZAL", "deduction", 10305, "souhrnDataZec.zalohaNaDan.danZalohaPoSleve"
)
SRAZKOVA_DAN = Component("Srážková daň", "JM_SRD", "deduction", 10309)  # path depends on schema
EXEKUCE = Component("Exekuce a jiné srážky", "JM_EXE", "deduction")  # manual, not in JMHZ

# Order = order of rows in the salary structure and on the slip.
COMPONENTS: tuple[Component, ...] = (
	TARIF,
	PREMIE_PRAVIDELNE,
	PREMIE_NEPRAVIDELNE,
	PRIPLATKY,
	MZDA_OSTATNI,
	MZDA,
	NAHRADA_DOVOLENA,
	NAHRADA_SVATKY,
	NAHRADA_PREKAZKY_ZAMESTNAVATEL,
	NAHRADA_PREKAZKY_ZAMESTNANEC,
	NAHRADA_DPN,
	NAHRADY_OSTATNI,
	OSTATNI_PRIJMY,
	DANOVY_BONUS,
	SOCIALNI_POJISTENI,
	ZDRAVOTNI_POJISTENI,
	ZALOHA_NA_DAN,
	SRAZKOVA_DAN,
	EXEKUCE,
)
EARNINGS = tuple(c for c in COMPONENTS if c.kind == "earning")
DEDUCTIONS = tuple(c for c in COMPONENTS if c.kind == "deduction")
BY_NAME = {c.name: c for c in COMPONENTS}
MANUAL_DEDUCTION = EXEKUCE

MZDA_ROZPAD_ITEMS = (TARIF, PREMIE_PRAVIDELNE, PREMIE_NEPRAVIDELNE, PRIPLATKY)
NAHRADY_ITEMS = (
	NAHRADA_DOVOLENA,
	NAHRADA_SVATKY,
	NAHRADA_PREKAZKY_ZAMESTNAVATEL,
	NAHRADA_PREKAZKY_ZAMESTNANEC,
	NAHRADA_DPN,
)

# 10309 moved in 1.5 from zvlastniSazbaDane.srazenaDan to zalohaNaDan.srazenaDanPar6.
SRAZKOVA_DAN_PATH = {
	SCHEMA_14: "souhrnDataZec.zvlastniSazbaDane.srazenaDan",
	SCHEMA_15: "souhrnDataZec.zalohaNaDan.srazenaDanPar6",
}

# --- person-level paths (relative to the bezPriznaku form) ---------------------
IK_MPSV = "identifikace.ikMpsv"  # 10051
ID_PPV = "identifikace.idPpv"  # 10228
# Employee summary data. Present only on the primary employment of a person.
SOUHRN_ZEC = "souhrnDataZec"
ZUCTOVANO = "souhrnDataZec.prijmy.zuctovanoCelkem"  # 10286
# tax base and computed tax, used only to compare with the printed slip (SPEC §6.6)
ZAKLAD_DANE = "souhrnDataZec.zalohaNaDan.zakladDane"  # 10297
VYPOCTENA_ZALOHA = "souhrnDataZec.zalohaNaDan.vypoctenaZaloha"  # 10298
OSVOBOZENO = "souhrnDataZec.prijmy.osvobozenoCelkem"  # 10289
CISTY_PRIJEM = "souhrnDataZec.mzdaCista.mzdaCista"  # 10344
SRAZKY_EVIDOVANY = "souhrnDataZec.mzdaCista.srazkyZeMzdyEvidovany"  # 10116
SRAZKY_OZP = (  # 10350..10353, filled only for disabled employees on the protected labour market
	"souhrnDataZec.mzdaCista.srazky.srazkaPlneniZel",
	"souhrnDataZec.mzdaCista.srazky.srazkaSkodaZec",
	"souhrnDataZec.mzdaCista.srazky.srazkaStrava",
	"souhrnDataZec.mzdaCista.srazky.srazkaZavazkyZec",
)
# Annual tax settlement refund (tax 10322, bonus 10323). Paid out with the salary,
# so 10344 contains it. Used only to explain a P01 mismatch.
ROCNI_ZUCTOVANI_PREPLATEK = (
	"souhrnDataZec.rocniUhrny.vysledekRocnihoZuctovani.danPreplatekRok",
	"souhrnDataZec.rocniUhrny.vysledekRocnihoZuctovani.danBonusPreplatekRok",
)
MZDA_ZUCTOVANA = "mzda.mzdaZuctovana"  # 10328
MZDA_ROZPAD = "mzda.mzdaRozpad"
NAHRADY = "mzda.nahrady"
NAHRADY_CELKEM = "mzda.nahrady.mzdyZuctovane"  # 10337
SP_ZAMESTNAVATEL = "pojisteni.pojisteniZamestnavatel.socialniPojisteni"  # 10481
ZP_ZAMESTNAVATEL = "souhrnDataZec.zdravPojZamestnavatel.zdravotniPojisteni"  # 10482

# Informative values for the slip. Summed over employments of one person.
INFO_PATHS = {
	"odpracovane_hodiny": "prubehZamestnani.odpracovaneHodiny.pocet",  # 10268
	"odpracovane_dny": "prubehZamestnani.odpracovaneDny.dnyOdpracovanePocet",  # 10267, 1.4 only
	"hodiny_dovolena": "prubehZamestnani.neodpracovaneHodiny.hodinyNeodpracDovol",  # 10279
	"neodpracovane_hodiny": "prubehZamestnani.neodpracovaneHodiny.hodinyNeodpracCelkem",  # 10275
}
INFO_ONLY_IN = {"odpracovane_dny": SCHEMA_14}

# --- informative values for the full slip ---------------------------------------
# Shown on the slip and compared with the slip printed by the payroll system. They never
# change an amount of the slip and never block a person: an unreadable value is skipped
# with a warning.
#
# Only amounts are read. The XSD keeps personal data of children and other persons next to
# them (`prohlaseniPoplatnikaDane.zvyhodneniDetiMesic`, `rocniUhrny.vysledekRocnihoZuctovani`):
# those elements must never get a path here. A unit test guards it.


@dataclass(frozen=True)
class Extra:
	key: str  # key in `Person.extra`
	label: str
	jmhz_id: int
	paths: dict[str, str]  # schema -> path relative to the form; no schema = not reported there
	kind: ExtraKind = "amount"
	signed: bool = False  # the XSD allows a negative value (corrections)


def _both(path: str) -> dict[str, str]:
	return {SCHEMA_14: path, SCHEMA_15: path}


def _prohlaseni(element: str) -> dict[str, str]:
	# the taxpayer declaration moved under zalohaNaDan in 1.5
	return {
		SCHEMA_14: f"souhrnDataZec.prohlaseniPoplatnikaDane.{element}",
		SCHEMA_15: f"souhrnDataZec.zalohaNaDan.prohlaseniPoplatnikaDane.{element}",
	}


def _prispevek_14(element: str) -> dict[str, str]:
	# 1.5 reports only the total 10417, the breakdown exists in 1.4 only
	return {SCHEMA_14: f"souhrnDataZec.prijmy.prispevekZamestnavatele.{element}"}


# read strictly by the parser (it explains P07), listed here for the key and the label
OSVOBOZENE_PRIJMY = Extra("osvobozene_prijmy", "Příjmy osvobozené od daně", 10289, _both(OSVOBOZENO))
PRUMERNY_VYDELEK = Extra(
	"prumerny_vydelek",
	"Průměrný hodinový výdělek",
	10345,
	_both("mzda.vydelek.vydelekPrumernyHod"),
	kind="average",
)
# Kind of employment (code list "Druh činnosti", 10239). The report carries it directly only
# for a person without IK MPSV (`identifikace.druhCinnosti`, next to the name and the birth
# date), which is never read. For everybody else it is the ELDP code (10240) without its last
# two characters: checked on all codes of the code list "Kód ELDP" of both data dictionaries.
ELDP_SEZNAM = "pojisteni.eldpSeznam"
ELDP = "eldp"  # repeats when the code changes during the month
ELDP_KOD = "kod"  # 3 or 4 characters
DRUH_CINNOSTI = Extra(
	"druh_cinnosti", "Druh činnosti", 10240, _both(f"{ELDP_SEZNAM}.{ELDP}.{ELDP_KOD}"), kind="code"
)
# 1-16 or one or two capital letters
DRUH_CINNOSTI_PATTERN = r"(?:[1-9][0-9]?|[A-Z]{1,2})"

EXTRAS: tuple[Extra, ...] = (
	OSVOBOZENE_PRIJMY,
	# tax credits applied in the month (amounts only)
	Extra("sleva_poplatnik", "Sleva na poplatníka", 10299, _prohlaseni("zakladniSleva")),
	Extra(
		"sleva_invalidita",
		"Sleva na invaliditu (I./II. stupeň)",
		10300,
		_prohlaseni("zakladniSlevaInvalidita12"),
	),
	Extra(
		"sleva_invalidita_rozsirena",
		"Sleva na invaliditu (III. stupeň)",
		10301,
		_prohlaseni("rozsirenaSlevaInvalidita3"),
	),
	Extra("sleva_ztpp", "Sleva na držitele průkazu ZTP/P", 10302, _prohlaseni("slevaZTPP")),
	Extra(
		"sleva_student",
		"Sleva na studenta",
		10565,
		{SCHEMA_15: "souhrnDataZec.zalohaNaDan.prohlaseniPoplatnikaDane.slevaStudent.vyseSlevy"},
	),
	Extra("zvyhodneni_deti", "Daňové zvýhodnění na děti", 10303, _prohlaseni("danoveZvyhodneniDetiMesic")),
	Extra("sleva_deti", "Sleva na děti", 10304, _prohlaseni("slevaDite")),
	PRUMERNY_VYDELEK,
	DRUH_CINNOSTI,
	# split of 10332; the slip keeps one component "Příplatky"
	Extra("priplatek_prescas", "Příplatek za přesčas", 10333, _both("mzda.mzdaRozpad.priplatky.prescas")),
	Extra("priplatek_noc", "Příplatek za práci v noci", 10334, _both("mzda.mzdaRozpad.priplatky.nocni")),
	Extra(
		"priplatek_vikend",
		"Příplatek za sobotu a neděli",
		10335,
		_both("mzda.mzdaRozpad.priplatky.sobotaNedele"),
	),
	Extra("priplatek_svatek", "Příplatek za svátek", 10336, _both("mzda.mzdaRozpad.priplatky.svatek")),
	Extra(
		"hodiny_prescas",
		"Přesčasové hodiny",
		10269,
		_both("prubehZamestnani.odpracovaneHodiny.rozpad.prescas"),
		kind="quantity",
	),
	# employer contributions (part of the exempt income)
	Extra(
		"prispevek_sporeni",
		"Příspěvky zaměstnavatele na spoření na stáří celkem",
		10417,
		{
			SCHEMA_14: "souhrnDataZec.prijmy.prispevekZamestnavatele.prispevekZelSporeniOsvob",
			SCHEMA_15: "souhrnDataZec.prijmy.prispevekZelSporeniOsvob",
		},
		signed=True,
	),
	Extra(
		"prispevek_dlouhodoba_pece",
		"Příspěvek na pojištění dlouhodobé péče",
		10418,
		_prispevek_14("prispevekZelPojDlPece"),
	),
	Extra(
		"prispevek_penzijni_pripojisteni",
		"Příspěvek na penzijní připojištění",
		10292,
		_prispevek_14("prispevekPenzPripoj"),
		signed=True,
	),
	Extra(
		"prispevek_doplnkove_penzijni",
		"Příspěvek na doplňkové penzijní spoření",
		10293,
		_prispevek_14("prispevekDoplnPenzPripoj"),
		signed=True,
	),
	Extra(
		"prispevek_penzijni_pojisteni",
		"Příspěvek na penzijní pojištění",
		10294,
		_prispevek_14("prispevekPenzPoj"),
		signed=True,
	),
	Extra(
		"prispevek_zivotni_pojisteni",
		"Příspěvek na životní pojištění",
		10295,
		_prispevek_14("prispevekZivotPoj"),
	),
	Extra(
		"prispevek_dip", "Příspěvek na dlouhodobý investiční produkt", 10296, _prispevek_14("prispevekDip")
	),
)
EXTRA_BY_KEY = {x.key: x for x in EXTRAS}

# --- report-level paths (relative to the root) ---------------------------------
HLAVICKA = "hlavicka"
SOUHRN_ZALOHA = "souhrn.danUdajeMesic.danZalohaPoSleve"  # 10034
SOUHRN_BONUS = "souhrn.danUdajeMesic.danBonus"  # 10035
PVPOJ_SP_ZAMESTNANCU = "PVPOJ.pojistne.pojistneZamestnance"  # 10028
