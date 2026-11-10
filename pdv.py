"""Obrasci PDV i PDV-S za primljene usluge iz EU (npr. Etsyjeve naknade).

Etsy (Etsy Ireland UC) naknade fakturira bez PDV-a (0 % PDV) jer je kupac
poduzetnik s PDV ID-om, pa PDV obračunava primatelj (prijenos porezne obveze):

  PDV obrazac (shema v11-0):
    Podatak210  = II.10 Primljene usluge iz EU po stopi 25 % (vrijednost, porez)
    Podatak200  = II. Oporezive transakcije - ukupno
    Podatak000  = I. + II. (vrijednost)
    Podatak310  = III.10 Pretporez od primljenih usluga iz EU po stopi 25 %
                  (samo ako obveznik ima pravo na odbitak pretporeza)
    Podatak300  = III. Obračunani pretporez - ukupno
    Podatak400  = IV. Obveza PDV-a: II. - III.
    (Podatak630 ostaje 0.00; potvrđeno primjerom obrasca iz ePorezne.)
  PDV-S obrazac (shema v1-0):
    jedan redak po dobavljaču: kod države, PDV ID, I1 (dobra), I2 (usluge)

Nazivi i značenje polja preuzeti su iz službenog "Opis elemenata XML sheme PDV"
i XSD-a Porezne uprave. Generirani XML se validira protiv tih XSD-ova kad su
dostupni (mapa ePorezna_Schemas uz aplikaciju) i lxml je instaliran.
"""
import os
import re
import shutil
import tempfile
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from xml.sax.saxutils import escape

from novac import D, cent

STOPA_PDV = Decimal("0.25")
NS_PDV = "http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacPDV/v11-0"
NS_PDVS = "http://e-porezna.porezna-uprava.hr/sheme/zahtjevi/ObrazacPDVS/v1-0"
NS_META = "http://e-porezna.porezna-uprava.hr/sheme/Metapodaci/v2-0"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
ZADANA_MAPA_SHEMA = os.path.join(os.path.dirname(APP_DIR), "ePorezna_Schemas")

# datoteke sheme potrebne za validaciju (mapa -> datoteke)
_SHEME = {
    "PDV": ("PDV", "ObrazacPDV-v11-0.xsd",
            ("ObrazacPDVtipovi-v11-0.xsd", "ObrazacPDVmetapodaci-v11-0.xsd",
             "MetapodaciTipovi-v2-0.xsd", "TemeljniTipovi-v2-1.xsd")),
    "PDV-S": ("PDV-S", "ObrazacPDVS-v1-0.xsd",
              ("ObrazacPDVStipovi-v1-0.xsd", "ObrazacPDVSmetapodaci-v1-0.xsd",
               "MetapodaciTipovi-v2-0.xsd", "TemeljniTipovi-v2-1.xsd")),
}


class PdvGreska(ValueError):
    """Podaci za obrazac nisu potpuni ili ispravni (poruka je za korisnika)."""


# ── Pomoćno ──────────────────────────────────────────────────────────────────

def razdoblje_mjeseca(mjesec):
    """'2026-08' -> (date(2026,8,1), date(2026,8,31))."""
    try:
        g, m = (int(x) for x in str(mjesec).split("-"))
        od = date(g, m, 1)
    except (ValueError, TypeError):
        raise PdvGreska(f"Neispravan mjesec: {mjesec!r} (očekujem GGGG-MM).")
    do = (date(g + (m == 12), m % 12 + 1, 1)) - timedelta(days=1)
    return od, do


def rok_predaje(razdoblje_do):
    """Zadnji dan mjeseca koji slijedi razdoblju (rok za PDV i PDV-S od 2026.)."""
    sljedeci = razdoblje_do + timedelta(days=1)
    kraj = date(sljedeci.year + (sljedeci.month == 12), sljedeci.month % 12 + 1, 1)
    return kraj - timedelta(days=1)


def parsiraj_adresu(adresa):
    """'Jezerski put 8, 42240 Ivanec' -> ('Ivanec', 'Jezerski put', '8')."""
    dijelovi = [d.strip() for d in str(adresa or "").split(",") if d.strip()]
    if len(dijelovi) < 2:
        raise PdvGreska("Adresa izdavatelja mora biti oblika "
                        "'Ulica broj, 42000 Mjesto' (Postavke → Izdavatelj).")
    ulica_broj, mjesto = dijelovi[0], dijelovi[-1]
    mjesto = re.sub(r"^\d{4,5}\s*", "", mjesto).strip()
    m = re.match(r"^(.*?)\s+(\d+\s?[A-Za-z]?(?:[/-]\d+)?)$", ulica_broj)
    if not m or not mjesto:
        raise PdvGreska("Ne mogu iz adrese izdvojiti ulicu, kućni broj i mjesto. "
                        "Očekujem 'Ulica broj, 42000 Mjesto'.")
    return mjesto, m.group(1).strip(), m.group(2).replace(" ", "")


def _ime_prezime(operater):
    dijelovi = str(operater or "").split()
    if len(dijelovi) < 2:
        raise PdvGreska("Ime i prezime osobe koja sastavlja obrazac nije poznato. "
                        "Upiši ih u Postavke → Operater (npr. 'Ime Prezime').")
    return dijelovi[0], " ".join(dijelovi[1:])


def _iznos(d):
    return f"{cent(d):.2f}"


def _provjeri_osnovno(obveznik, ispostava):
    if not re.fullmatch(r"\d{11}", str(obveznik.get("oib") or "")):
        raise PdvGreska("OIB izdavatelja mora imati 11 znamenki (Postavke).")
    if not (obveznik.get("ime") and obveznik.get("prezime")) and \
            len(str(obveznik.get("naziv") or "").strip()) < 2:
        raise PdvGreska("Naziv izdavatelja nije upisan (Postavke).")
    if not re.fullmatch(r"\d{4}", str(ispostava or "")):
        raise PdvGreska("Šifra ispostave Porezne uprave mora imati 4 znamenke.")


# ── Izračun ──────────────────────────────────────────────────────────────────

def izracun(osnovica_cent, pretporez=False):
    """Iznosi za obrazac iz osnovice (cijeli centi) primljenih usluga iz EU.

    pretporez=True samo ako obveznik ima pravo na odbitak pretporeza; paušalist
    izvan sustava PDV-a ga nema pa PDV na ove usluge u cijelosti plaća.
    """
    osnovica = cent(D(int(osnovica_cent)) / 100)
    if osnovica < 0:
        raise PdvGreska("Osnovica ne može biti negativna.")
    porez = cent(osnovica * STOPA_PDV)
    odbitak = porez if pretporez else Decimal("0.00")
    return {"osnovica": osnovica, "porez": porez, "pretporez": odbitak,
            "obveza": cent(porez - odbitak), "stopa": STOPA_PDV}


# ── XML ──────────────────────────────────────────────────────────────────────

def _metapodaci(naslov, autor, uskladjenost, sada, identifikator):
    return (
        f'    <Metapodaci xmlns="{NS_META}">\n'
        f'        <Naslov dc="http://purl.org/dc/elements/1.1/title">{escape(naslov)}</Naslov>\n'
        f'        <Autor dc="http://purl.org/dc/elements/1.1/creator">{escape(autor)}</Autor>\n'
        f'        <Datum dc="http://purl.org/dc/elements/1.1/date">{sada:%Y-%m-%dT%H:%M:%S}</Datum>\n'
        f'        <Format dc="http://purl.org/dc/elements/1.1/format">text/xml</Format>\n'
        f'        <Jezik dc="http://purl.org/dc/elements/1.1/language">hr-HR</Jezik>\n'
        f'        <Identifikator dc="http://purl.org/dc/elements/1.1/identifier">{identifikator}</Identifikator>\n'
        f'        <Uskladjenost dc="http://purl.org/dc/terms/conformsTo">{uskladjenost}</Uskladjenost>\n'
        f'        <Tip dc="http://purl.org/dc/elements/1.1/type">Elektronički obrazac</Tip>\n'
        f'        <Adresant>Ministarstvo Financija, Porezna uprava, Zagreb</Adresant>\n'
        f'    </Metapodaci>\n')


def _zaglavlje(razdoblje, obveznik, sastavio, ispostava, telefon_email=False):
    od, do = razdoblje
    mjesto, ulica, broj = parsiraj_adresu(obveznik["adresa"])
    ime, prezime = sastavio
    return (
        '    <Zaglavlje>\n'
        '        <Razdoblje>\n'
        f'            <DatumOd>{od:%Y-%m-%d}</DatumOd>\n'
        f'            <DatumDo>{do:%Y-%m-%d}</DatumDo>\n'
        '        </Razdoblje>\n'
        '        <Obveznik>\n'
        + (f'            <Ime>{escape(obveznik["ime"])}</Ime>\n'
           f'            <Prezime>{escape(obveznik["prezime"])}</Prezime>\n'
           if obveznik.get("ime") and obveznik.get("prezime") else
           f'            <Naziv>{escape(obveznik["naziv"].strip())}</Naziv>\n') +
        f'            <OIB>{obveznik["oib"]}</OIB>\n'
        '            <Adresa>\n'
        f'                <Mjesto>{escape(mjesto)}</Mjesto>\n'
        f'                <Ulica>{escape(ulica)}</Ulica>\n'
        f'                <Broj>{escape(broj)}</Broj>\n'
        '            </Adresa>\n'
        '        </Obveznik>\n'
        '        <ObracunSastavio>\n'
        f'            <Ime>{escape(ime)}</Ime>\n'
        f'            <Prezime>{escape(prezime)}</Prezime>\n'
        '        </ObracunSastavio>\n'
        f'        <Ispostava>{ispostava}</Ispostava>\n'
        '    </Zaglavlje>\n')


def xml_pdv(obveznik, mjesec, osnovica_cent, ispostava, operater,
            pretporez=False, sada=None, identifikator=None):
    """XML PDV obrasca (v11-0) za primljene usluge iz EU u danom mjesecu.

    obveznik = {'naziv', 'oib', 'adresa'}; vraća tekst XML-a (UTF-8).
    """
    _provjeri_osnovno(obveznik, ispostava)
    razdoblje = razdoblje_mjeseca(mjesec)
    sastavio = _ime_prezime(operater)
    r = izracun(osnovica_cent, pretporez)
    sada = sada or datetime.now()
    identifikator = identifikator or str(uuid.uuid4())
    nula = "0.00"
    osn, por, odb = _iznos(r["osnovica"]), _iznos(r["porez"]), _iznos(r["pretporez"])

    def isporuka(naziv, vr, po):
        return (f'        <{naziv}>\n            <Vrijednost>{vr}</Vrijednost>\n'
                f'            <Porez>{po}</Porez>\n        </{naziv}>\n')

    t = []
    # I. Transakcije koje ne podliježu oporezivanju / oslobođene (nemamo ih)
    t.append(f'        <Podatak000>{osn}</Podatak000>\n')
    for n in range(100, 112):
        t.append(f'        <Podatak{n}>{nula}</Podatak{n}>\n')
    # II. Oporezive transakcije: ukupno + 10. primljene usluge iz EU po 25 %
    for n in range(200, 216):
        if n == 200 or n == 210:
            t.append(isporuka(f"Podatak{n}", osn, por))
        else:
            t.append(isporuka(f"Podatak{n}", nula, nula))
    # III. Obračunani pretporez: ukupno + 10. pretporez od usluga iz EU po 25 %
    for n in range(300, 315):
        if r["pretporez"] and n in (300, 310):
            t.append(isporuka(f"Podatak{n}", osn, odb))
        else:
            t.append(isporuka(f"Podatak{n}", nula, nula))
    t.append(f'        <Podatak315>{nula}</Podatak315>\n')
    # IV. Obveza PDV-a (II. - III.), V. razmjerni odbitak
    t.append(f'        <Podatak400>{_iznos(r["obveza"])}</Podatak400>\n')
    t.append(f'        <Podatak500>{nula}</Podatak500>\n')
    # Ostali podaci
    for n in range(610, 616):
        t.append(f'        <Podatak{n}>{nula}</Podatak{n}>\n')
    t.append(f'        <Podatak620>{nula}</Podatak620>\n')
    # 630 ostaje 0.00: službeni obrazac iz ePorezne ga ne popunjava za ove usluge
    for n in (630, 640, 650):
        t.append(f'        <Podatak{n}>{nula}</Podatak{n}>\n')
    t.append('        <Podatak660>false</Podatak660>\n')
    for n in range(701, 705):
        t.append(f'        <Podatak{n}>\n            <NabavnaVrijednost>{nula}</NabavnaVrijednost>\n'
                 f'            <ProdajnaVrijednost>{nula}</ProdajnaVrijednost>\n        </Podatak{n}>\n')
    t.append(f'        <Povrat>{nula}</Povrat>\n        <PodaciZaUstup/>\n'
             f'        <Predujam>{nula}</Predujam>\n        <UstupPovrata>{nula}</UstupPovrata>\n')

    autor = f"{sastavio[0]} {sastavio[1]}"
    return (f'<ObrazacPDV verzijaSheme="11.0" xmlns="{NS_PDV}">\n'
            + _metapodaci("Prijava poreza na dodanu vrijednost", autor,
                          "ObrazacPDV-v11-0", sada, identifikator)
            + _zaglavlje(razdoblje, obveznik, sastavio, ispostava)
            + '    <Tijelo>\n' + "".join(t) + '    </Tijelo>\n</ObrazacPDV>\n')


def ocisti_pdvid(pdvid, kod_drzave):
    """'IE 3320113VH' / 'ie3320113vh' -> '3320113VH' (bez koda države i razmaka)."""
    p = re.sub(r"[\s.\-]", "", str(pdvid or "")).upper()
    if p.startswith(kod_drzave.upper()):
        p = p[len(kod_drzave):]
    return p


def xml_pdvs(obveznik, mjesec, osnovica_cent, ispostava, operater,
             pdvid_dobavljaca, kod_drzave="IE", sada=None, identifikator=None):
    """XML PDV-S obrasca (v1-0): primljene usluge iz EU od jednog dobavljača."""
    _provjeri_osnovno(obveznik, ispostava)
    razdoblje = razdoblje_mjeseca(mjesec)
    sastavio = _ime_prezime(operater)
    pdvid = ocisti_pdvid(pdvid_dobavljaca, kod_drzave)
    if not pdvid:
        raise PdvGreska("PDV ID dobavljača (Etsy) nije upisan. Nalazi se na "
                        "Etsyjevoj fakturi za naknade.")
    if len(pdvid) > 12:
        raise PdvGreska("PDV ID dobavljača (bez koda države) smije imati najviše 12 znakova.")
    if int(osnovica_cent) <= 0:
        raise PdvGreska("Nema primljenih usluga u tom mjesecu: PDV-S se ne podnosi.")
    osn = _iznos(cent(D(int(osnovica_cent)) / 100))
    sada = sada or datetime.now()
    identifikator = identifikator or str(uuid.uuid4())
    autor = f"{sastavio[0]} {sastavio[1]}"
    return (f'<ObrazacPDVS verzijaSheme="1.0" xmlns="{NS_PDVS}">\n'
            + _metapodaci("Prijava za stjecanje dobara i primljene usluge iz drugih "
                          "država članica Europske unije", autor, "ObrazacPDVS-v1-0",
                          sada, identifikator)
            + _zaglavlje(razdoblje, obveznik, sastavio, ispostava)
            + '    <Tijelo>\n        <Isporuke>\n            <Isporuka>\n'
              '                <RedBr>1</RedBr>\n'
              f'                <KodDrzave>{kod_drzave.upper()}</KodDrzave>\n'
              f'                <PDVID>{escape(pdvid)}</PDVID>\n'
              '                <I1>0.00</I1>\n'
              f'                <I2>{osn}</I2>\n'
              '            </Isporuka>\n        </Isporuke>\n'
              '        <IsporukeUkupno>\n            <I1>0.00</I1>\n'
              f'            <I2>{osn}</I2>\n        </IsporukeUkupno>\n'
              '    </Tijelo>\n</ObrazacPDVS>\n')


# ── Validacija prema službenoj shemi ─────────────────────────────────────────

_STUB = ('<?xml version="1.0" encoding="UTF-8"?>\n'
         '<xsd:schema xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
         'targetNamespace="{ns}" elementFormDefault="qualified"/>\n')


def validiraj(xml, tip, mapa_shema=None):
    """Validira XML protiv XSD-a Porezne uprave.

    tip = 'PDV' ili 'PDV-S'. Vraća (status, greske) gdje je status
    'ok', 'greske' ili 'preskoceno' (nema lxml ili sheme; tada je greske[0] razlog).
    """
    try:
        from lxml import etree
    except ImportError:
        return "preskoceno", ["lxml nije instaliran (pip install lxml)"]
    mapa = mapa_shema or ZADANA_MAPA_SHEMA
    podmapa, glavna, ostalo = _SHEME[tip]
    izvor = os.path.join(mapa, podmapa)
    if not os.path.isfile(os.path.join(izvor, glavna)):
        return "preskoceno", [f"nema XSD-a u {izvor}"]
    with tempfile.TemporaryDirectory() as tmp:
        for ime in (glavna,) + tuple(ostalo):
            putanja = os.path.join(izvor, ime)
            if not os.path.isfile(putanja):
                return "preskoceno", [f"nedostaje {ime} u {izvor}"]
            shutil.copy(putanja, tmp)
        # Sheme uvoze digitalni potpis (xmldsig, XAdES), koji nije u paketu;
        # obrazac se predaje bez potpisa pa su dovoljni prazni nadomjesci.
        for ime, ns in (("xmldsig-core-schema.xsd", "http://www.w3.org/2000/09/xmldsig#"),
                        ("XAdES.xsd", "http://uri.etsi.org/01903/v1.3.2#")):
            if not os.path.isfile(os.path.join(tmp, ime)):
                with open(os.path.join(tmp, ime), "w", encoding="utf-8") as f:
                    f.write(_STUB.format(ns=ns))
        try:
            shema = etree.XMLSchema(etree.parse(os.path.join(tmp, glavna)))
        except etree.XMLSchemaParseError as e:
            return "preskoceno", [f"shema se ne može učitati: {e}"]
    dokument = etree.fromstring(xml.encode("utf-8"))
    if shema.validate(dokument):
        return "ok", []
    return "greske", [f"redak {g.line}: {g.message}" for g in shema.error_log]
