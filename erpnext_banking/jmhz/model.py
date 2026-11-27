"""Data model of a parsed JMHZ report. Pure Python, must not import frappe.

Rule for every `message`: it never contains an amount, an IK MPSV or an ID PPV.
Only the issue code, an element path, a JMHZ attribute ID or a pseudonym.
Messages end up in the UI, in the Error Log and possibly in e-mail.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Severity = Literal["block", "warn", "info"]


class JmhzParseError(ValueError):
	"""Fatal error (F01-F03): the file cannot be imported at all."""

	def __init__(self, code: str, message: str):
		super().__init__(f"{code}: {message}")
		self.code = code
		self.message = message


@dataclass
class Issue:
	code: str
	severity: Severity
	message: str


@dataclass
class Line:
	component: str
	amount: int  # always positive, the sign is given by `kind`
	kind: Literal["earning", "deduction"]
	jmhz_id: int | None


@dataclass
class Person:
	ik_mpsv: str  # empty when missing (then P06)
	pseudonym: str
	id_ppv: list[str]
	lines: list[Line]
	zuctovano: int  # 10286
	cisty_prijem: int  # 10344
	bonus: int  # 10306
	k_vyplate: int  # 10344 + 10306, manual deductions are added by the importer
	sp_zamestnavatel: int  # 10481
	zp_zamestnavatel: int  # 10482
	info: dict[str, float]
	exekuce: bool  # 10116
	has_income: bool
	issues: list[Issue] = field(default_factory=list)
	zdanitelna_mzda: int = 0  # 10297
	vypoctena_dan: int = 0  # 10298
	# Informative values for the full slip, keyed by `components.EXTRAS`. A missing key is
	# zero / not reported, None is a value that could not be read (then P14).
	extra: dict[str, int | float | str | None] = field(default_factory=dict)

	@property
	def blocking_issues(self) -> list[Issue]:
		return [i for i in self.issues if i.severity == "block"]

	@property
	def issue_codes(self) -> list[str]:
		return [i.code for i in self.issues]


@dataclass
class ParsedFile:
	schema: str  # "1.4" / "1.5"
	rok: int
	mesic: int
	typ_podani: str  # R / O / S
	id_podani: str | None
	vendor: str | None
	sha256: str
	persons: list[Person]
	issues: list[Issue]
	file_name: str | None = None
	# Employer identifier in the report header (10221). The report has no IČO.
	variabilni_symbol: str | None = None

	@property
	def issue_codes(self) -> list[str]:
		return [i.code for i in self.issues]
