"""Single source of truth for the JMHZ -> Salary Component mapping.

JMHZ ("jednotné měsíční hlášení zaměstnavatele") is the Czech monthly employer
report. This module is shared by the parser (pure Python) and the setup code,
so it must never import frappe.

Paths are dot-separated local element names. Person-level paths are relative to
a `bezPriznaku` form, report-level paths are relative to the root `jmhz`
element. Numbers in comments are attribute IDs from the MPSV data dictionary.
Paths were checked against the official XSD 1.4.3.6 and 1.5.0.3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Kind = Literal["earning", "deduction"]

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

# --- report-level paths (relative to the root) ---------------------------------
HLAVICKA = "hlavicka"
SOUHRN_ZALOHA = "souhrn.danUdajeMesic.danZalohaPoSleve"  # 10034
SOUHRN_BONUS = "souhrn.danUdajeMesic.danBonus"  # 10035
PVPOJ_SP_ZAMESTNANCU = "PVPOJ.pojistne.pojistneZamestnance"  # 10028
