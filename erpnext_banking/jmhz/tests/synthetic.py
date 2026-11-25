"""Generator of synthetic JMHZ reports for tests and for the e-mail test (SPEC §6.7).

All numbers and identifiers are made up. Pure Python, must not import frappe.
The element structure follows the official XSD 1.4.3.6 and 1.5.0.3.
"""

NS = {"1.4": "1.0", "1.5": "2027"}


def form(
	ver="1.4",
	*,
	ik="1111111111",
	ppv="9000000001",
	typ="R",
	primarni=True,
	kind="bezPriznaku",
	souhrn=True,
	zuct=0,
	cisty=0,
	osvobozeno=None,
	bonus=0,
	zal=0,
	srd=0,
	zp=0,
	sp=0,
	mzda=0,
	rozpad=None,
	nahrady=None,
	exekuce=False,
	srazky=None,
	hodiny=None,
	dny=None,
	hodiny_dovolena=None,
	prumerny_vydelek=None,
	preplatek=None,
	raw_zuct=None,
	zaklad_dane=None,
	vypoctena_dan=None,
):
	"""One employment form. `rozpad` / `nahrady` / `srazky` are {element: value}."""
	ident = ""
	if ik is not None:
		ident += f"<form:ikMpsv>{ik}</form:ikMpsv>"
	if ppv is not None:
		ident += f"<form:idPpv>{ppv}</form:idPpv>"

	souhrn_xml = ""
	if souhrn:
		prijmy = f"<form:zuctovanoCelkem>{raw_zuct if raw_zuct is not None else zuct}</form:zuctovanoCelkem>"
		if osvobozeno is not None:
			prijmy += f"<form:osvobozenoCelkem>{osvobozeno}</form:osvobozenoCelkem>"
		zaloha = ""
		if zaklad_dane is not None:
			zaloha += f"<form:zakladDane>{zaklad_dane}</form:zakladDane>"
		if vypoctena_dan is not None:
			zaloha += f"<form:vypoctenaZaloha>{vypoctena_dan}</form:vypoctenaZaloha>"
		zaloha += (
			f"<form:danZalohaPoSleve>{zal}</form:danZalohaPoSleve><form:danBonus>{bonus}</form:danBonus>"
		)
		zvlastni = ""
		if srd and ver == "1.5":
			zaloha += f"<form:srazenaDanPar6>{srd}</form:srazenaDanPar6>"
		elif srd:
			zvlastni = (
				f"<form:zvlastniSazbaDane><form:srazenaDan>{srd}</form:srazenaDan></form:zvlastniSazbaDane>"
			)
		rocni = ""
		if preplatek is not None:
			rocni = (
				"<form:rocniUhrny><form:vysledekRocnihoZuctovani>"
				f"<form:danPreplatekRok>{preplatek}</form:danPreplatekRok>"
				"<form:danBonusPreplatekRok>0</form:danBonusPreplatekRok>"
				"</form:vysledekRocnihoZuctovani></form:rocniUhrny>"
			)
		# 10419 sits under souhrnDataZec only in 1.4
		prohlaseni = "<form:prohlaseniPoplatnika>true</form:prohlaseniPoplatnika>" if ver == "1.4" else ""
		srazky_xml = "".join(f"<form:{k}>{v}</form:{k}>" for k, v in (srazky or {}).items())
		souhrn_xml = (
			f"<form:souhrnDataZec><form:prijmy>{prijmy}</form:prijmy>"
			f"<form:zalohaNaDan>{zaloha}</form:zalohaNaDan>{zvlastni}"
			f"{prohlaseni}{rocni}"
			f"<form:mzdaCista><form:mzdaCista>{cisty}</form:mzdaCista>"
			f"<form:srazkyZeMzdyEvidovany>{str(exekuce).lower()}</form:srazkyZeMzdyEvidovany>"
			f"<form:srazky>{srazky_xml}</form:srazky></form:mzdaCista>"
			f"<form:zdravPojZamestnavatel><form:zdravotniPojisteni>{zp * 2}</form:zdravotniPojisteni></form:zdravPojZamestnavatel>"
			f"<form:zdravPojZamestnanec><form:zdravotniPojisteni>{zp}</form:zdravotniPojisteni></form:zdravPojZamestnanec>"
			"</form:souhrnDataZec>"
		)

	pojisteni = (
		f"<form:pojisteni><form:pojisteniZamestnanec><form:socialniPojisteni>{sp}</form:socialniPojisteni></form:pojisteniZamestnanec>"
		f"<form:pojisteniZamestnavatel><form:socialniPojisteni>{sp * 3}</form:socialniPojisteni></form:pojisteniZamestnavatel></form:pojisteni>"
	)

	prubeh = ""
	if hodiny is not None:
		prubeh += f"<form:odpracovaneHodiny><form:pocet>{hodiny}</form:pocet></form:odpracovaneHodiny>"
	if dny is not None and ver == "1.4":
		prubeh += f"<form:odpracovaneDny><form:dnyOdpracovanePocet>{dny}</form:dnyOdpracovanePocet></form:odpracovaneDny>"
	if hodiny_dovolena is not None:
		prubeh += (
			"<form:neodpracovaneHodiny>"
			f"<form:hodinyNeodpracCelkem>{hodiny_dovolena}</form:hodinyNeodpracCelkem>"
			f"<form:hodinyNeodpracDovol>{hodiny_dovolena}</form:hodinyNeodpracDovol>"
			"</form:neodpracovaneHodiny>"
		)

	mzda_xml = f"<form:mzdaZuctovana>{mzda}</form:mzdaZuctovana>"
	if rozpad is not None:
		items = ""
		for k, v in rozpad.items():
			items += (
				f"<form:priplatky><form:celkem>{v}</form:celkem></form:priplatky>"
				if k == "priplatky"
				else f"<form:{k}>{v}</form:{k}>"
			)
		mzda_xml += f"<form:mzdaRozpad>{items}</form:mzdaRozpad>"
	if nahrady is not None:
		mzda_xml += (
			"<form:nahrady>"
			+ "".join(f"<form:{k}>{v}</form:{k}>" for k, v in nahrady.items())
			+ "</form:nahrady>"
		)
	if prumerny_vydelek is not None:
		mzda_xml += f"<form:vydelek><form:vydelekPrumernyHod>{prumerny_vydelek}</form:vydelekPrumernyHod></form:vydelek>"

	body = (
		f"<form:{kind}><form:identifikace>{ident}</form:identifikace>{souhrn_xml}{pojisteni}"
		f"<form:prubehZamestnani>{prubeh}</form:prubehZamestnani><form:mzda>{mzda_xml}</form:mzda></form:{kind}>"
	)
	head = (
		"<{p}:hlavicka><{p}:idFormulare>00000000-0000-0000-0000-000000000001</{p}:idFormulare>"
		"<{p}:typFormulare>{typ}</{p}:typFormulare><{p}:primarniPpv>{prim}</{p}:primarniPpv></{p}:hlavicka>"
	)
	if ver == "1.5":  # form container and its header live in the form namespace in 1.5
		return (
			"<form:formularZamestnani>"
			+ head.format(p="form", typ=typ, prim=str(primarni).lower())
			+ body
			+ "</form:formularZamestnani>"
		)
	return (
		"<n1:formularOsoby>"
		+ head.format(p="n1", typ=typ, prim=str(primarni).lower())
		+ body
		+ "</n1:formularOsoby>"
	)


def report(
	*forms, ver="1.4", typ_podani="R", souhrn_zal=None, souhrn_bon=None, pvpoj_sp=None, rok=2026, mesic=2
):
	v = NS[ver]
	container = "n1:formulareOsob" if ver == "1.4" else "n1:formulareZamestnani"
	souhrn = ""
	if souhrn_zal is not None:
		souhrn = (
			f"<so:souhrn><so:danUdajeMesic><so:danZalohaPoSleve>{souhrn_zal}</so:danZalohaPoSleve>"
			f"<so:danBonus>{souhrn_bon or 0}</so:danBonus></so:danUdajeMesic></so:souhrn>"
		)
	pvpoj = ""
	if pvpoj_sp is not None:
		pvpoj = f"<pv:PVPOJ><pv:pojistne><pv:pojistneZamestnance>{pvpoj_sp}</pv:pojistneZamestnance></pv:pojistne></pv:PVPOJ>"
	return f"""<?xml version="1.0" encoding="UTF-8"?>
<n1:jmhz xmlns:n1="http://schemas.cssz.cz/JMHZ/podani/{v}" xmlns:so="http://schemas.cssz.cz/JMHZ/souhrn/{v}"
 xmlns:pv="http://schemas.cssz.cz/JMHZ/PVPOJ/{v}" xmlns:form="http://schemas.cssz.cz/JMHZ/form/{v}">
 <n1:VENDOR productName="Test" productVersion="1.0"/>
 <n1:hlavicka><n1:idPodani>00000000-0000-0000-0000-00000000000a</n1:idPodani><n1:typPodani>{typ_podani}</n1:typPodani>
 <n1:variabilniSymbol>0000000001</n1:variabilniSymbol><n1:mesic>{mesic}</n1:mesic><n1:rok>{rok}</n1:rok></n1:hlavicka>
 {souhrn}{pvpoj}
 <{container}>{"".join(forms)}</{container}>
</n1:jmhz>""".encode()


def hpp(ver="1.4", **kw):
	"""MPSV sample: HPP, gross 15353, SP 1091, ZP 691 -> 10344 = 13571."""
	args = dict(zuct=15353, mzda=15353, rozpad={"tarif": 15353}, sp=1091, zp=691, cisty=13571)
	args.update(kw)
	return form(ver, **args)
