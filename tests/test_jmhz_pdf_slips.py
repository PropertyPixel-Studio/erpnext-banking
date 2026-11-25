"""Unit tests of splitting printed payroll slips. Synthetic PDFs generated at runtime."""

import io
from decimal import Decimal

import pytest
from pypdf import PdfReader

from erpnext_banking.jmhz import pdf_slips
from erpnext_banking.jmhz.pdf_slips import PdfSlipError, parse_number, split
from erpnext_banking.jmhz.tests.synthetic_pdf import (
	SEPARATOR_ABOVE_ANCHOR,
	anchor_y,
	build_pdf,
	report_pdf,
	slip_texts,
	slip_values,
)

A = ("Nováková Žofie", slip_values(gross=15353, sp=1091, zp=691, hours=40.5, days=5))
B = (
	"Dvořák Čeněk",
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
	assert "Nováková" not in message and "13" not in message.replace("Strana 1", "")


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
