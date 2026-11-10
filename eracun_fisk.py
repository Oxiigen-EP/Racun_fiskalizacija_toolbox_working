"""Fiskalizacija 2.0: eRačun i eIzvještavanje (Porezna uprava).

Prema dokumentu "Tehnička specifikacija – Fiskalizacija eRačuna i
eIzvještavanje" (v1.15, 6. 2. 2026.). Poruke su SOAP 1.1 / XML, potpisane
XAdES-B potpisom (aplikativni certifikat s OIB-om), 1-way TLS.

Metode:
  eFiskalizacija  : EvidentirajERacun (izlazni 'I' i ulazni 'U' eRačun)
  eIzvještavanje  : EvidentirajNaplatu, EvidentirajOdbijanje,
                    EvidentirajIsporukuZaKojuNijeIzdanERacun,
                    OvlastenjaFiskalizacije

Modul ne ovisi o Qt-u ni o bazi (osim pomoćne funkcije `eracun_iz_racuna`,
koja prima već pročitane retke) pa se testira bez mreže.

NAPOMENE (nije provjereno na testnoj okolini):
  * WSDL i XSD su na portalu Porezne uprave, nisu u ovoj mapi. Nazivi i redoslijed
    elemenata slijede tablice i primjere iz specifikacije. Ako postaviš
    `fisk2_xsd` u config.json, zahtjev se prije slanja provjerava protiv XSD-a.
  * Specifikacija je na nekoliko mjesta nedosljedna (npr. `vrstaRacuna` je u
    tekstu 'IR', a u primjeru 'I'); odabrano je ono što piše u tekstu.
"""
import base64
import hashlib
import os
import re
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

import lxml.etree as etree
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_pem_x509_certificate

from arhiva_util import spremi_poruku

NS_EFIS = "http://www.porezna-uprava.gov.hr/fin/2024/types/eFiskalizacija"
NS_EIZ = "http://www.porezna-uprava.gov.hr/fin/2024/types/eIzvjestavanje"
NS_DS = "http://www.w3.org/2000/09/xmldsig#"
NS_XADES = "http://uri.etsi.org/01903/v1.3.2#"
NS_SOAP = "http://schemas.xmlsoap.org/soap/envelope/"

ALG_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"
ALG_RSA_SHA256 = "http://www.w3.org/2001/04/xmldsig-more#rsa-sha256"
ALG_SHA256 = "http://www.w3.org/2001/04/xmlenc#sha256"
ALG_SHA512 = "http://www.w3.org/2001/04/xmlenc#sha512"
ALG_ENVELOPED = "http://www.w3.org/2000/09/xmldsig#enveloped-signature"
TYPE_SIGNED_PROPS = "http://uri.etsi.org/01903#SignedProperties"

URL_TEST = "https://cistest.apis-it.hr:8509/FiskalizacijaServiceEprod"
URL_PROD = "https://cis.porezna-uprava.hr:8509/FiskalizacijaService"
# Portal za testiranje sukladnosti (CT portal) koristi ovu adresu:
URL_CT_PORTAL = "https://cis.porezna-uprava.hr:8511/FiskalizacijaService"

VRSTA_RACUNA_BEZ_ERACUNA = "IR"       # spec. 5.5.2
MAX_ZAPISA = 100                      # najviše 100 zapisa po poruci

# jedinice mjere (EN 16931 / UN/ECE Rec. 20) za nazive koje app koristi
JEDINICE = {
    "kom": "H87", "komad": "H87", "kom.": "H87", "pcs": "H87",
    "sat": "HUR", "h": "HUR", "sati": "HUR",
    "kg": "KGM", "g": "GRM", "l": "LTR", "lit": "LTR", "litra": "LTR",
    "m": "MTR", "m2": "MTK", "m²": "MTK", "dan": "DAY", "mj": "MON",
    "mjesec": "MON", "set": "SET", "usluga": "C62", "paket": "10",
}
JEDINICA_ZADANA = "C62"               # "jedinica"

_RE_DATUM = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_RE_HR_OZNAKA = re.compile(r"^[Hh][Rr]:(([Pp][Dd][Vv][0-9]{1,2})|([A-Za-z]{1,10}))$")


class ERacunGreska(ValueError):
    """Podaci ne zadovoljavaju pravila specifikacije (prije slanja)."""


# ── pomoćne funkcije ─────────────────────────────────────────────────────

def oib_ispravan(oib) -> bool:
    """Formalna provjera OIB-a (ISO 7064, MOD 11,10)."""
    oib = str(oib or "")
    if not re.fullmatch(r"\d{11}", oib):
        return False
    a = 10
    for znamenka in oib[:10]:
        a = (a + int(znamenka)) % 10
        a = 10 if a == 0 else a
        a = (a * 2) % 11
    kontrolna = 11 - a
    kontrolna = 0 if kontrolna == 10 else kontrolna
    return kontrolna == int(oib[10])


def iznos(v) -> str:
    """Decimal s točno dvije decimale (točka), npr. '59.99'."""
    return f"{Decimal(str(v)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"


def broj(v) -> str:
    """Decimal do 10 decimala bez suvišnih nula ('25', '0.5', '2.5')."""
    d = Decimal(str(v)).quantize(Decimal("0.0000000001"), rounding=ROUND_HALF_UP)
    s = f"{d:f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def datum_iso(v) -> str:
    """date / datetime / 'dd.MM.yyyy' / 'YYYY-MM-DD' -> 'YYYY-MM-DD'."""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d")
    if isinstance(v, date):
        return v.isoformat()
    s = str(v).strip()
    if _RE_DATUM.match(s):
        return s
    m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    raise ERacunGreska(f"Neispravan datum: {v!r}")


def vrijeme_slanja(dt=None) -> str:
    """ISO 8601 s 4 znamenke za dio sekunde: 2024-04-01T08:04:02.1234"""
    dt = dt or datetime.now()
    return f"{dt:%Y-%m-%dT%H:%M:%S}.{dt.microsecond // 100:04d}"


def jedinica_mjere(naziv) -> str:
    n = str(naziv or "").strip()
    if re.fullmatch(r"[A-Z0-9]{2,3}", n):          # već je šifra
        return n
    return JEDINICE.get(n.lower(), JEDINICA_ZADANA)


def _q(ns, ime):
    return f"{{{ns}}}{ime}"


def _dodaj(roditelj, ns, ime, tekst=None, **atributi):
    el = etree.SubElement(roditelj, _q(ns, ime))
    if tekst is not None:
        el.text = str(tekst)
    for k, v in atributi.items():
        el.set(k, v)
    return el


def _neobavezno(roditelj, ns, ime, vrijednost):
    if vrijednost is None or vrijednost == "":
        return None
    return _dodaj(roditelj, ns, ime, vrijednost)


# ── validacija podataka ──────────────────────────────────────────────────

def provjeri_eracun(e: dict):
    """Lokalna provjera pravila iz specifikacije (poglavlje 3.1.3 i 3.3).
    Baca ERacunGreska s popisom svih problema."""
    p = []
    bd = str(e.get("broj_dokumenta", ""))
    if not 1 <= len(bd) <= 100:
        p.append("brojDokumenta mora imati 1–100 znakova")
    try:
        datum_iso(e.get("datum_izdavanja"))
    except ERacunGreska:
        p.append("datumIzdavanja nije ispravan")
    if not re.fullmatch(r"[0-9]{2,3}", str(e.get("vrsta_dokumenta", "380"))):
        p.append("vrstaDokumenta mora imati 2–3 znamenke (UNTDID 1001)")
    if not re.fullmatch(r"[A-Za-z]{3}", str(e.get("valuta", "EUR"))):
        p.append("valuta mora biti ISO 4217 (npr. EUR)")
    izd, pri = e.get("izdavatelj") or {}, e.get("primatelj") or {}
    if not izd.get("ime"):
        p.append("izdavatelj.ime je obavezan")
    if not oib_ispravan(izd.get("oib")):
        p.append("OIB izdavatelja nije formalno ispravan (S007)")
    if not oib_ispravan(izd.get("oib_operatera")):
        p.append("OIB operatera nije formalno ispravan")
    if not pri.get("ime"):
        p.append("primatelj.ime je obavezan")
    if not oib_ispravan(pri.get("oib")):
        p.append("OIB primatelja nije formalno ispravan (S007)")
    if not e.get("stavke"):
        p.append("eRačun mora imati barem jednu stavku")
    if not e.get("raspodjela_pdv"):
        p.append("eRačun mora imati barem jednu raspodjelu PDV-a")
    for r in e.get("raspodjela_pdv") or []:
        kat = r.get("kategorija", "")
        if not re.fullmatch(r"[A-Za-z]{1,2}", kat):
            p.append(f"kategorijaPdv {kat!r} nije valjana")
        if kat == "AE" and not (r.get("razlog") and r.get("tekst")):
            p.append("kategorija AE traži razlogOslobodenja i tekstRazlogaOslobodenja")
        if r.get("razlog") and not r.get("tekst"):
            p.append("uz razlogOslobodenja treba i tekstRazlogaOslobodenja")
        if r.get("tekst") and kat == "AE" and not r.get("razlog"):
            p.append("uz tekst razloga za AE treba i razlogOslobodenja")
        if r.get("hr_oznaka") and not _RE_HR_OZNAKA.match(r["hr_oznaka"]):
            p.append(f"hrOznakaKategorijaPdv {r['hr_oznaka']!r} nije u obliku HR:...")
    for pr in e.get("prijenos_sredstava") or []:
        if not pr.get("iban"):
            p.append("PrijenosSredstava traži IBAN")
    for s in e.get("stavke") or []:
        if not s.get("naziv"):
            p.append("stavka mora imati naziv")
        if not re.fullmatch(r"[A-Za-z0-9]{2,3}", str(s.get("jedinica", ""))):
            p.append(f"jedinicaMjere {s.get('jedinica')!r} nije valjana šifra")
        if not re.fullmatch(r"[A-Z]{1,2}", str(s.get("kategorija", ""))):
            p.append("artiklKategorijaPdv mora biti 1–2 velika slova")
    if e.get("vrsta_dokumenta") in ("381", "386") and any(
            s.get("klasifikacije") for s in e.get("stavke") or []):
        p.append("za vrste 381 i 386 klasifikacija artikla se ne unosi")
    if p:
        raise ERacunGreska("; ".join(p))


# ── gradnja poruka ───────────────────────────────────────────────────────

def _id_atribut(ns):
    return _q(ns, "id")


def _korijen(ns, ime, zahtjev_id):
    el = etree.Element(_q(ns, ime),
                       nsmap={("efis" if ns == NS_EFIS else "eiz"): ns})
    el.set(_id_atribut(ns), zahtjev_id)
    return el


def _dodaj_eracun(roditelj, ns, e: dict, ime_elementa, ime_stavke, ime_valute):
    r = _dodaj(roditelj, ns, ime_elementa)
    _dodaj(r, ns, "brojDokumenta", e["broj_dokumenta"])
    _dodaj(r, ns, "datumIzdavanja", datum_iso(e["datum_izdavanja"]))
    _dodaj(r, ns, "vrstaDokumenta", e.get("vrsta_dokumenta", "380"))
    _dodaj(r, ns, ime_valute, e.get("valuta", "EUR"))
    if e.get("datum_dospijeca"):
        _dodaj(r, ns, "datumDospijecaPlacanja", datum_iso(e["datum_dospijeca"]))
    _dodaj(r, ns, "vrstaPoslovnogProcesa", e.get("vrsta_procesa", "P1"))
    _neobavezno(r, ns, "referencaNaUgovor", e.get("referenca_ugovor"))
    if e.get("datum_isporuke"):
        _dodaj(r, ns, "datumIsporuke", datum_iso(e["datum_isporuke"]))
    for pr in e.get("prethodni") or []:
        x = _dodaj(r, ns, "PrethodniERacun")
        _dodaj(x, ns, "brojDokumenta", pr["broj_dokumenta"])
        _dodaj(x, ns, "datumIzdavanja", datum_iso(pr["datum_izdavanja"]))

    izd = e["izdavatelj"]
    x = _dodaj(r, ns, "Izdavatelj")
    _dodaj(x, ns, "ime", izd["ime"])
    _dodaj(x, ns, "oibPorezniBroj", izd["oib"])
    _dodaj(x, ns, "oibOperatera", izd["oib_operatera"])
    pri = e["primatelj"]
    x = _dodaj(r, ns, "Primatelj")
    _dodaj(x, ns, "ime", pri["ime"])
    _dodaj(x, ns, "oibPorezniBroj", pri["oib"])

    for pr in e.get("prijenos_sredstava") or []:
        x = _dodaj(r, ns, "PrijenosSredstava")
        _dodaj(x, ns, "identifikatorRacunaZaPlacanje", pr["iban"])
        _neobavezno(x, ns, "nazivRacunaZaPlacanje", pr.get("naziv"))
        _neobavezno(x, ns, "identifikatorPruzateljaPlatnihUsluga", pr.get("pruzatelj"))

    u = e["ukupno"]
    x = _dodaj(r, ns, "DokumentUkupanIznos")
    _dodaj(x, ns, "neto", iznos(u["neto"]))
    if u.get("popust") is not None:
        _dodaj(x, ns, "popust", iznos(u["popust"]))
    if u.get("trosak") is not None:
        _dodaj(x, ns, "trosak", iznos(u["trosak"]))
    _dodaj(x, ns, "iznosBezPdv", iznos(u["bez_pdv"]))
    if u.get("pdv") is not None:
        _dodaj(x, ns, "pdv", iznos(u["pdv"]))
    _dodaj(x, ns, "iznosSPdv", iznos(u["s_pdv"]))
    if u.get("placeno") is not None:
        _dodaj(x, ns, "placeniIznos", iznos(u["placeno"]))
    _dodaj(x, ns, "iznosKojiDospijevaZaPlacanje", iznos(u["dospijeva"]))

    for rp in e["raspodjela_pdv"]:
        x = _dodaj(r, ns, "RaspodjelaPdv")
        _dodaj(x, ns, "kategorijaPdv", rp["kategorija"])
        _dodaj(x, ns, "oporeziviIznos", iznos(rp["oporezivi"]))
        _dodaj(x, ns, "iznosPoreza", iznos(rp["iznos"]))
        if rp.get("stopa") is not None:
            _dodaj(x, ns, "stopa", broj(rp["stopa"]))
        _neobavezno(x, ns, "razlogOslobodenja", rp.get("razlog"))
        _neobavezno(x, ns, "tekstRazlogaOslobodenja", rp.get("tekst"))
        _neobavezno(x, ns, "hrOznakaKategorijaPdv", rp.get("hr_oznaka"))

    for po in e.get("popusti") or []:
        x = _dodaj(r, ns, "DokumentPopust")
        _dodaj(x, ns, "iznosPopust", iznos(po["iznos"]))
        _dodaj(x, ns, "kategorijaPdv", po["kategorija"])
        if po.get("stopa") is not None:
            _dodaj(x, ns, "stopaPdv", iznos(po["stopa"]))
        _neobavezno(x, ns, "tekstRazlogaPopusta", po.get("tekst"))
        _neobavezno(x, ns, "razlogPopusta", po.get("razlog"))

    for tr in e.get("troskovi") or []:
        x = _dodaj(r, ns, "DokumentTrosak")
        _dodaj(x, ns, "iznosTrosak", iznos(tr["iznos"]))
        _dodaj(x, ns, "kategorijaPdv", tr["kategorija"])
        _neobavezno(x, ns, "hrOznakaPorezneKategorije", tr.get("hr_oznaka"))
        if tr.get("stopa") is not None:
            _dodaj(x, ns, "stopaPdv", iznos(tr["stopa"]))
        _neobavezno(x, ns, "tekstRazlogaOslobodenjaPdv", tr.get("tekst"))
        _neobavezno(x, ns, "razlogOslobodenjaPdv", tr.get("razlog"))

    for s in e["stavke"]:
        x = _dodaj(r, ns, ime_stavke)
        _dodaj(x, ns, "kolicina", broj(s["kolicina"]))
        _dodaj(x, ns, "jedinicaMjere", s["jedinica"])
        _dodaj(x, ns, "neto", iznos(s["neto"]))
        _dodaj(x, ns, "artiklNetoCijena", broj(s["neto_cijena"]))
        if s.get("bruto_cijena") is not None:
            _dodaj(x, ns, "artiklBrutoCijena", broj(s["bruto_cijena"]))
        if s.get("osnovna_kolicina") is not None:
            _dodaj(x, ns, "artiklOsnovnaKolicina", broj(s["osnovna_kolicina"]))
        _neobavezno(x, ns, "artiklJedinicaMjereZaOsnovnuKolicinu",
                    s.get("jedinica_osnovna"))
        _dodaj(x, ns, "artiklKategorijaPdv", s["kategorija"])
        if s.get("stopa") is not None:
            _dodaj(x, ns, "artiklStopaPdv", broj(s["stopa"]))
        _dodaj(x, ns, "artiklNaziv", s["naziv"])
        _neobavezno(x, ns, "artiklOpis", s.get("opis"))
        _neobavezno(x, ns, "artiklHrOznakaKategorijaPdv", s.get("hr_oznaka"))
        for k in s.get("klasifikacije") or []:
            kx = _dodaj(x, ns, "ArtiklIdentifikatorKlasifikacija")
            _dodaj(kx, ns, "identifikatorKlasifikacije", k["id"])
            _dodaj(kx, ns, "identifikatorSheme", k.get("shema", "CG"))
            _neobavezno(kx, ns, "verzijaSheme", k.get("verzija"))
    _dodaj(r, ns, "indikatorKopije", str(bool(e.get("kopija"))).lower())
    return r


def gradi_evidentiraj_eracun(eracuni, vrsta="I", zahtjev_id=None, dt=None):
    """EvidentirajERacunZahtjev (nepotpisan). vrsta: 'I' izlazni, 'U' ulazni."""
    if vrsta not in ("I", "U"):
        raise ERacunGreska("vrstaERacuna mora biti 'I' ili 'U'")
    _provjeri_broj_zapisa(eracuni)
    for e in eracuni:
        provjeri_eracun(e)
    if vrsta == "U" and len({e["primatelj"]["oib"] for e in eracuni}) > 1:
        raise ERacunGreska("Za ulazne račune OIB kupca mora biti isti u poruci")
    korijen = _korijen(NS_EFIS, "EvidentirajERacunZahtjev",
                       zahtjev_id or str(uuid.uuid4()))
    z = _dodaj(korijen, NS_EFIS, "Zaglavlje")
    _dodaj(z, NS_EFIS, "datumVrijemeSlanja", vrijeme_slanja(dt))
    _dodaj(z, NS_EFIS, "vrstaERacuna", vrsta)
    for e in eracuni:
        _dodaj_eracun(korijen, NS_EFIS, e, "ERacun", "StavkaERacuna", "valutaERacuna")
    return korijen


def gradi_isporuku_bez_eracuna(racuni, zahtjev_id=None, dt=None):
    """EvidentirajIsporukuZaKojuNijeIzdanERacunZahtjev (nepotpisan)."""
    _provjeri_broj_zapisa(racuni)
    for e in racuni:
        provjeri_eracun(e)
    korijen = _korijen(NS_EIZ, "EvidentirajIsporukuZaKojuNijeIzdanERacunZahtjev",
                       zahtjev_id or str(uuid.uuid4()))
    z = _dodaj(korijen, NS_EIZ, "Zaglavlje")
    _dodaj(z, NS_EIZ, "datumVrijemeSlanja", vrijeme_slanja(dt))
    _dodaj(z, NS_EIZ, "vrstaRacuna", VRSTA_RACUNA_BEZ_ERACUNA)
    for e in racuni:
        _dodaj_eracun(korijen, NS_EIZ, e, "Racun", "StavkaRacuna", "valutaRacuna")
    return korijen


def gradi_naplatu(naplate, zahtjev_id=None, dt=None):
    """EvidentirajNaplatuZahtjev. Storno/ispravak: negativan naplaceni iznos."""
    _provjeri_broj_zapisa(naplate)
    korijen = _korijen(NS_EIZ, "EvidentirajNaplatuZahtjev",
                       zahtjev_id or str(uuid.uuid4()))
    z = _dodaj(korijen, NS_EIZ, "Zaglavlje")
    _dodaj(z, NS_EIZ, "datumVrijemeSlanja", vrijeme_slanja(dt))
    for n in naplate:
        p = []
        if not oib_ispravan(n.get("oib_izdavatelja")):
            p.append("OIB izdavatelja nije formalno ispravan")
        if not oib_ispravan(n.get("oib_primatelja")):
            p.append("OIB primatelja nije formalno ispravan")
        if n.get("nacin_placanja") not in ("T", "O", "Z"):
            p.append("nacinPlacanja mora biti T, O ili Z")
        if p:
            raise ERacunGreska("; ".join(p))
        x = _dodaj(korijen, NS_EIZ, "Naplata")
        _dodaj(x, NS_EIZ, "brojDokumenta", n["broj_dokumenta"])
        _dodaj(x, NS_EIZ, "datumIzdavanja", datum_iso(n["datum_izdavanja"]))
        _dodaj(x, NS_EIZ, "oibPorezniBrojIzdavatelja", n["oib_izdavatelja"])
        _dodaj(x, NS_EIZ, "oibPorezniBrojPrimatelja", n["oib_primatelja"])
        _dodaj(x, NS_EIZ, "datumNaplate", datum_iso(n["datum_naplate"]))
        _dodaj(x, NS_EIZ, "naplaceniIznos", iznos(n["iznos"]))
        _dodaj(x, NS_EIZ, "nacinPlacanja", n["nacin_placanja"])
    return korijen


def gradi_odbijanje(odbijanja, zahtjev_id=None, dt=None):
    """EvidentirajOdbijanjeZahtjev (po jedan zapis odbijanja po eRačunu)."""
    _provjeri_broj_zapisa(odbijanja)
    korijen = _korijen(NS_EIZ, "EvidentirajOdbijanjeZahtjev",
                       zahtjev_id or str(uuid.uuid4()))
    z = _dodaj(korijen, NS_EIZ, "Zaglavlje")
    _dodaj(z, NS_EIZ, "datumVrijemeSlanja", vrijeme_slanja(dt))
    for o in odbijanja:
        p = []
        if not oib_ispravan(o.get("oib_izdavatelja")):
            p.append("OIB izdavatelja nije formalno ispravan")
        if not oib_ispravan(o.get("oib_primatelja")):
            p.append("OIB primatelja nije formalno ispravan")
        if o.get("vrsta_razloga") not in ("N", "U", "O"):
            p.append("vrstaRazlogaOdbijanja mora biti N, U ili O")
        razlog = str(o.get("razlog", ""))
        if not 1 <= len(razlog) <= 1024:
            p.append("razlogOdbijanja mora imati 1–1024 znakova")
        if p:
            raise ERacunGreska("; ".join(p))
        x = _dodaj(korijen, NS_EIZ, "Odbijanje")
        _dodaj(x, NS_EIZ, "brojDokumenta", o["broj_dokumenta"])
        _dodaj(x, NS_EIZ, "datumIzdavanja", datum_iso(o["datum_izdavanja"]))
        _dodaj(x, NS_EIZ, "oibPorezniBrojIzdavatelja", o["oib_izdavatelja"])
        _dodaj(x, NS_EIZ, "oibPorezniBrojPrimatelja", o["oib_primatelja"])
        _dodaj(x, NS_EIZ, "datumOdbijanja", datum_iso(o["datum_odbijanja"]))
        _dodaj(x, NS_EIZ, "vrstaRazlogaOdbijanja", o["vrsta_razloga"])
        _dodaj(x, NS_EIZ, "razlogOdbijanja", razlog)
    return korijen


def gradi_ovlastenja(oib=None, zahtjev_id=None, dt=None):
    """OvlastenjaFiskalizacijeZahtjev (popis OIB-ova ovlaštenih za obveznika)."""
    if oib is not None and not oib_ispravan(oib):
        raise ERacunGreska("OIB nije formalno ispravan")
    korijen = _korijen(NS_EIZ, "OvlastenjaFiskalizacijeZahtjev",
                       zahtjev_id or str(uuid.uuid4()))
    z = _dodaj(korijen, NS_EIZ, "Zaglavlje")
    _dodaj(z, NS_EIZ, "datumVrijemeSlanja", vrijeme_slanja(dt))
    _neobavezno(korijen, NS_EIZ, "oib", oib)
    return korijen


def _provjeri_broj_zapisa(zapisi):
    if not zapisi:
        raise ERacunGreska("Poruka mora sadržavati barem jedan zapis")
    if len(zapisi) > MAX_ZAPISA:
        raise ERacunGreska(f"Najviše {MAX_ZAPISA} zapisa po poruci")


# ── XAdES-B potpis ───────────────────────────────────────────────────────

def _der(tag: int, sadrzaj: bytes) -> bytes:
    n = len(sadrzaj)
    if n < 0x80:
        duljina = bytes([n])
    else:
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        duljina = bytes([0x80 | len(b)]) + b
    return bytes([tag]) + duljina + sadrzaj


def issuer_serial_v2(cert) -> bytes:
    """DER od IssuerSerial (RFC 5035): SEQUENCE { GeneralNames, serial }."""
    ime = cert.issuer.public_bytes()
    general_names = _der(0x30, _der(0xA4, ime))          # directoryName [4]
    s = cert.serial_number
    sb = s.to_bytes(max(1, (s.bit_length() + 8) // 8), "big")   # +0x00 ako je visoki bit
    return _der(0x30, general_names + _der(0x02, sb))


def c14n(el) -> bytes:
    return etree.tostring(el, method="c14n", exclusive=True, with_comments=False)


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def potpisi_xades_b(korijen, private_key, certificate, vrijeme=None):
    """Dodaje XAdES-B enveloped potpis na kraj korijenskog elementa zahtjeva.

    Dvije reference (spec. pogl. 11): korijen preko id atributa (enveloped +
    exc-c14n) i SignedProperties (exc-c14n). Sve sa SHA-256 / RSA-SHA256."""
    ns = etree.QName(korijen).namespace
    zahtjev_id = korijen.get(_id_atribut(ns))
    if not zahtjev_id:
        raise ERacunGreska("Korijenski element nema id atribut")
    slucajni = uuid.uuid4().hex
    sig_id = f"id-{slucajni}"
    props_id = f"xades-id-{slucajni}"
    ref_id = f"ref-body-{slucajni}"
    vrijeme = vrijeme or datetime.now(timezone.utc)

    # 1) digest korijena (bez potpisa = enveloped-signature transformacija)
    digest_tijela = _b64(hashlib.sha256(c14n(korijen)).digest())

    # 2) Signature kostur + XAdES SignedProperties
    sig = etree.Element(_q(NS_DS, "Signature"), nsmap={"ds": NS_DS})
    sig.set("Id", sig_id)
    signed_info = _dodaj(sig, NS_DS, "SignedInfo")
    _dodaj(signed_info, NS_DS, "CanonicalizationMethod", Algorithm=ALG_C14N)
    _dodaj(signed_info, NS_DS, "SignatureMethod", Algorithm=ALG_RSA_SHA256)

    obj = etree.SubElement(sig, _q(NS_DS, "Object"))
    qp = etree.SubElement(obj, _q(NS_XADES, "QualifyingProperties"),
                          nsmap={"xades": NS_XADES})
    qp.set("Target", f"#{sig_id}")
    sp = _dodaj(qp, NS_XADES, "SignedProperties", Id=props_id)
    ssp = _dodaj(sp, NS_XADES, "SignedSignatureProperties")
    _dodaj(ssp, NS_XADES, "SigningTime",
           vrijeme.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    scv2 = _dodaj(ssp, NS_XADES, "SigningCertificateV2")
    cert_el = _dodaj(scv2, NS_XADES, "Cert")
    cd = _dodaj(cert_el, NS_XADES, "CertDigest")
    _dodaj(cd, NS_DS, "DigestMethod", Algorithm=ALG_SHA512)
    der = certificate.public_bytes(serialization.Encoding.DER)
    _dodaj(cd, NS_DS, "DigestValue", _b64(hashlib.sha512(der).digest()))
    _dodaj(cert_el, NS_XADES, "IssuerSerialV2", _b64(issuer_serial_v2(certificate)))
    sdop = _dodaj(sp, NS_XADES, "SignedDataObjectProperties")
    dof = _dodaj(sdop, NS_XADES, "DataObjectFormat", ObjectReference=f"#{ref_id}")
    _dodaj(dof, NS_XADES, "MimeType", "text/xml")

    # privremeno u stablu, da c14n bude kao u konačnom dokumentu
    korijen.append(sig)
    digest_props = _b64(hashlib.sha256(c14n(sp)).digest())

    # 3) Reference u SignedInfo
    ref1 = _dodaj(signed_info, NS_DS, "Reference", Id=ref_id, URI=f"#{zahtjev_id}")
    t = _dodaj(ref1, NS_DS, "Transforms")
    _dodaj(t, NS_DS, "Transform", Algorithm=ALG_ENVELOPED)
    _dodaj(t, NS_DS, "Transform", Algorithm=ALG_C14N)
    _dodaj(ref1, NS_DS, "DigestMethod", Algorithm=ALG_SHA256)
    _dodaj(ref1, NS_DS, "DigestValue", digest_tijela)
    ref2 = _dodaj(signed_info, NS_DS, "Reference", Type=TYPE_SIGNED_PROPS,
                  URI=f"#{props_id}")
    t = _dodaj(ref2, NS_DS, "Transforms")
    _dodaj(t, NS_DS, "Transform", Algorithm=ALG_C14N)
    _dodaj(ref2, NS_DS, "DigestMethod", Algorithm=ALG_SHA256)
    _dodaj(ref2, NS_DS, "DigestValue", digest_props)

    # 4) SignatureValue + KeyInfo (redoslijed: SignedInfo, SignatureValue, KeyInfo, Object)
    potpis = private_key.sign(c14n(signed_info), padding.PKCS1v15(), hashes.SHA256())
    sv = etree.Element(_q(NS_DS, "SignatureValue"))
    sv.set("Id", f"value-id-{slucajni}")
    sv.text = _b64(potpis)
    ki = etree.Element(_q(NS_DS, "KeyInfo"))
    x509 = _dodaj(ki, NS_DS, "X509Data")
    _dodaj(x509, NS_DS, "X509Certificate", _b64(der))
    signed_info.addnext(sv)
    sv.addnext(ki)
    return korijen


def provjeri_potpis(korijen, javni_kljuc=None):
    """Samoprovjera potpisa: oba digesta i vrijednost potpisa.
    Vraća None ako je sve u redu, inače baca ValueError. Ako nema javnog
    ključa, uzima ga iz X509Certificate u potpisu."""
    ns = {"ds": NS_DS, "xades": NS_XADES}
    sig = korijen.find("ds:Signature", ns)
    if sig is None:
        raise ValueError("Nema potpisa")
    si = sig.find("ds:SignedInfo", ns)
    refs = si.findall("ds:Reference", ns)
    if len(refs) != 2:
        raise ValueError("Očekivane su dvije reference")
    zahtjev_id = korijen.get(_id_atribut(etree.QName(korijen).namespace))
    for ref in refs:
        uri = ref.get("URI")
        zapisani = ref.find("ds:DigestValue", ns).text
        if ref.get("Type") == TYPE_SIGNED_PROPS:
            meta = sig.xpath(f"//xades:SignedProperties[@Id='{uri[1:]}']", namespaces=ns)
            if not meta:
                raise ValueError("SignedProperties nije pronađen")
            izracunati = _b64(hashlib.sha256(c14n(meta[0])).digest())
        else:
            if uri != f"#{zahtjev_id}":
                raise ValueError("Reference ne pokazuje na korijen zahtjeva")
            kopija = etree.fromstring(etree.tostring(korijen))
            kopija.remove(kopija.find("ds:Signature", ns))
            izracunati = _b64(hashlib.sha256(c14n(kopija)).digest())
        if zapisani != izracunati:
            raise ValueError(f"Digest se ne podudara za {uri}")
    if javni_kljuc is None:
        from cryptography.x509 import load_der_x509_certificate
        der = base64.b64decode(sig.find(".//ds:X509Certificate", ns).text)
        javni_kljuc = load_der_x509_certificate(der).public_key()
    javni_kljuc.verify(base64.b64decode(sig.find("ds:SignatureValue", ns).text),
                       c14n(si), padding.PKCS1v15(), hashes.SHA256())


# ── SOAP i odgovori ──────────────────────────────────────────────────────

def u_soap(korijen) -> bytes:
    env = etree.Element(_q(NS_SOAP, "Envelope"),
                        nsmap={"soapenv": NS_SOAP, "xades": NS_XADES})
    etree.SubElement(env, _q(NS_SOAP, "Header"))
    body = etree.SubElement(env, _q(NS_SOAP, "Body"))
    body.append(korijen)
    return etree.tostring(env, xml_declaration=True, encoding="UTF-8")


def _lokalno(el):
    return etree.QName(el).localname


def _tekst(el, ime):
    for c in el.iter():
        if isinstance(c.tag, str) and _lokalno(c) == ime:
            return (c.text or "").strip()
    return None


def parsiraj_odgovor(sadrzaj: bytes, http_status=None) -> dict:
    """Odgovor servisa -> {prihvacen, id_zahtjeva, greske[], ovlasteni_oib[], ...}.
    Radi bez obzira na prefikse; prepoznaje i SOAP Fault."""
    rez = {"prihvacen": False, "id_zahtjeva": None, "greske": [],
           "ovlasteni_oib": [], "http_status": http_status, "fault": None}
    try:
        root = etree.fromstring(sadrzaj)
    except etree.XMLSyntaxError as e:
        rez["greske"].append({"sifra": "XML", "redni_broj": 0,
                              "opis": f"Odgovor nije valjan XML: {e}"})
        return rez
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        ime = _lokalno(el)
        if ime == "Fault":
            rez["fault"] = _tekst(el, "faultstring") or "SOAP Fault"
            rez["greske"].append({"sifra": "FAULT", "redni_broj": 0,
                                  "opis": rez["fault"]})
        elif ime == "idZahtjeva":
            rez["id_zahtjeva"] = (el.text or "").strip()
        elif ime == "prihvacenZahtjev":
            rez["prihvacen"] = (el.text or "").strip().lower() == "true"
        elif ime == "greska":
            rez["greske"].append({
                "sifra": _tekst(el, "sifra"),
                "redni_broj": int(_tekst(el, "redniBrojZapisa") or 0),
                "opis": _tekst(el, "opis")})
        elif ime == "ovlasteniOib" and (el.text or "").strip():
            rez["ovlasteni_oib"].append(el.text.strip())
    if rez["fault"]:
        rez["prihvacen"] = False
    return rez


def opis_gresaka(odgovor: dict) -> str:
    return "; ".join(
        f"{g['sifra']}: {g['opis']}" + (f" (zapis {g['redni_broj']})"
                                        if g.get("redni_broj") else "")
        for g in odgovor.get("greske", []))


# ── klijent ──────────────────────────────────────────────────────────────

class EFiskalizacija:
    """Slanje poruka eFiskalizacije i eIzvještavanja.

    verify: putanja do CA datoteke, True/False. Zadano: za produkciju True
    (sustavni CA-ovi), za test False dok se ne postavi `ca_path`
    (testni poslužitelj koristi FINA DEMO CA)."""

    def __init__(self, cert_path, key_path, key_password=None, demo=True,
                 arhiva_mapa=None, ca_path=None, xsd_path=None, soap_action="",
                 url=None):
        self.arhiva_mapa = arhiva_mapa or os.getcwd()
        self.url = url or (URL_TEST if demo else URL_PROD)
        self.demo = demo
        self.soap_action = soap_action
        # sheme po namespaceu poruke: eFiskalizacija + (u istoj mapi) eIzvještavanje
        self.xsd = {}
        if xsd_path and os.path.exists(xsd_path):
            mapa = os.path.dirname(os.path.abspath(xsd_path))
            for ns, ime in ((NS_EFIS, "eFiskalizacijaSchema.xsd"),
                            (NS_EIZ, "eIzvjestavanjeSchema.xsd")):
                put = os.path.join(mapa, ime)
                if ime == os.path.basename(xsd_path):
                    put = os.path.abspath(xsd_path)
                if os.path.exists(put):
                    self.xsd[ns] = etree.XMLSchema(etree.parse(put))
        if ca_path:
            self.verify = ca_path
        else:
            self.verify = False if demo else True
        with open(key_path, "rb") as f:
            self.private_key = serialization.load_pem_private_key(
                f.read(), password=key_password.encode() if key_password else None,
                backend=default_backend())
        with open(cert_path, "rb") as f:
            self.certificate = load_pem_x509_certificate(f.read(), default_backend())
        self.zadnji_odgovor = None
        self.zadnja_greska = None

    # građenje i potpis (bez mreže)
    def pripremi(self, korijen) -> bytes:
        potpisi_xades_b(korijen, self.private_key, self.certificate)
        xsd = self.xsd.get(etree.QName(korijen).namespace)
        if xsd is not None and not xsd.validate(korijen):
            raise ERacunGreska(f"XSD: {xsd.error_log.last_error}")
        return u_soap(korijen)

    def _sesija(self):
        import ssl
        import requests
        import urllib3
        from requests.adapters import HTTPAdapter

        verify_off = self.verify is False

        class TLS12(HTTPAdapter):
            """Najmanje TLS 1.2 (TLS 1.1 se ukida)."""
            def init_poolmanager(self, *a, **kw):
                ctx = ssl.create_default_context()
                ctx.minimum_version = ssl.TLSVersion.TLSv1_2
                if verify_off:
                    ctx.check_hostname = False
                    ctx.verify_mode = ssl.CERT_NONE
                kw["ssl_context"] = ctx
                return super().init_poolmanager(*a, **kw)

        if verify_off:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        s = requests.Session()
        s.mount("https://", TLS12())
        return s

    def posalji(self, korijen, oznaka="eracun", godina=None) -> dict:
        """Potpiše, arhivira, pošalje i vrati parsirani odgovor."""
        godina = godina or datetime.now().year
        self.zadnja_greska = None
        try:
            soap = self.pripremi(korijen)
        except ERacunGreska as e:
            self.zadnja_greska = str(e)
            return {"prihvacen": False, "greske": [
                {"sifra": "LOKALNO", "redni_broj": 0, "opis": str(e)}],
                "id_zahtjeva": None, "ovlasteni_oib": [], "http_status": None,
                "fault": None}
        spremi_poruku(self.arhiva_mapa, godina, oznaka, "zahtjev", soap)
        try:
            odg = self._sesija().post(
                self.url, data=soap,
                headers={"Content-Type": "text/xml; charset=UTF-8",
                         "SOAPAction": self.soap_action},
                verify=self.verify, timeout=30)
            spremi_poruku(self.arhiva_mapa, godina, oznaka,
                          f"odgovor_http{odg.status_code}", odg.content)
            rez = parsiraj_odgovor(odg.content, odg.status_code)
        except Exception as e:                      # mreža, TLS, timeout
            self.zadnja_greska = f"Slanje nije uspjelo: {e}"
            spremi_poruku(self.arhiva_mapa, godina, oznaka, "greska",
                          self.zadnja_greska, "txt")
            return {"prihvacen": False, "greske": [
                {"sifra": "MREZA", "redni_broj": 0, "opis": self.zadnja_greska}],
                "id_zahtjeva": None, "ovlasteni_oib": [], "http_status": None,
                "fault": None}
        if not rez["prihvacen"]:
            self.zadnja_greska = (opis_gresaka(rez)
                                  or f"Zahtjev nije prihvaćen (HTTP {rez['http_status']})")
        self.zadnji_odgovor = rez
        return rez

    # javne metode po specifikaciji
    def evidentiraj_eracun(self, eracuni, vrsta="I", oznaka=None):
        oznaka = oznaka or _oznaka(eracuni[0]["broj_dokumenta"], "eracun")
        return self.posalji(gradi_evidentiraj_eracun(eracuni, vrsta), oznaka)

    def evidentiraj_naplatu(self, naplate, oznaka=None):
        oznaka = oznaka or _oznaka(naplate[0]["broj_dokumenta"], "naplata")
        return self.posalji(gradi_naplatu(naplate), oznaka)

    def evidentiraj_odbijanje(self, odbijanja, oznaka=None):
        oznaka = oznaka or _oznaka(odbijanja[0]["broj_dokumenta"], "odbijanje")
        return self.posalji(gradi_odbijanje(odbijanja), oznaka)

    def evidentiraj_isporuku_bez_eracuna(self, racuni, oznaka=None):
        oznaka = oznaka or _oznaka(racuni[0]["broj_dokumenta"], "isporuka")
        return self.posalji(gradi_isporuku_bez_eracuna(racuni), oznaka)

    def ovlastenja_fiskalizacije(self, oib=None):
        return self.posalji(gradi_ovlastenja(oib), "ovlastenja")


def _oznaka(broj_dokumenta, prefiks):
    return f"{prefiks}-{broj_dokumenta}"


# ── mapiranje računa iz aplikacije u eRačun ──────────────────────────────

# Neoporezivo (paušalni obrt izvan sustava PDV-a): kategorija 'O'.
KATEGORIJA_NEOPOREZIVO = "O"
KATEGORIJA_PDV = "S"


def _kategorija(u_pdv, stopa):
    """S = PDV, Z = nulta stopa u sustavu PDV-a, O = neoporezivo (izvan sustava)."""
    if not u_pdv:
        return KATEGORIJA_NEOPOREZIVO
    return KATEGORIJA_PDV if stopa > 0 else "Z"


def _hr_oznaka(kat, stopa):
    """HR:PDV25 i sl. za oporezive stope; za ostale se ne šalje."""
    if kat == KATEGORIJA_PDV and stopa == stopa.to_integral():
        return f"HR:PDV{int(stopa)}"
    return None


def _d(v):
    return v if isinstance(v, Decimal) else Decimal(str(v if v is not None else 0))


def _cent(v):
    return _d(v).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def eracun_iz_racuna(racun: dict, stavke: list, cfg: dict, kpd=None,
                     tekst_oslobodenja="", vrsta_dokumenta="380") -> dict:
    """Pretvara račun iz baze u rječnik za `gradi_evidentiraj_eracun`.

    racun : redak `invoices` (datum 'dd.MM.yyyy', broj_racuna, oznaka_pp, oznaka_nu,
            oib_izdavatelja, naziv_izdavatelja, oib_kupca, naziv_kupca, iban,
            rok_placanja, u_sustavu_pdv, operater/oib operatera)
    stavke: reci `invoice_stavke` (naziv, kolicina, jedinica, cijena_cent,
            pdv_stopa, popust)
    cfg   : config (oib, operater_oib po želji)
    kpd   : zadana KPD oznaka (npr. '62.01.11') za sve stavke, ili None
    tekst_oslobodenja: tekst razloga kad izdavatelj nije u sustavu PDV-a
    Iznosi se računaju po istim pravilima kao u novac.py (cent po stavci)."""
    from novac import izracun_stavke, iz_centi

    u_pdv = bool(racun.get("u_sustavu_pdv"))
    ukupno_osn = Decimal(0)
    ukupno_pdv = Decimal(0)
    grupe = {}                                   # (kategorija, stopa) -> [osnovica, pdv]
    st_out = []
    for s in stavke:
        cijena = _d(iz_centi(s["cijena_cent"]))
        kol = _d(s["kolicina"])
        stopa = _d(s.get("pdv_stopa") or 0) if u_pdv else Decimal(0)
        pop = _d(s.get("popust") or 0)
        r = izracun_stavke(kol, cijena, stopa, pop)
        osn, pdv = _d(r["osnovica"]), _d(r["pdv_iznos"])
        ukupno_osn += osn
        ukupno_pdv += pdv
        kat = _kategorija(u_pdv, stopa)
        g = grupe.setdefault((kat, stopa), [Decimal(0), Decimal(0)])
        g[0] += osn
        g[1] += pdv
        st = {
            "kolicina": kol, "jedinica": jedinica_mjere(s.get("jedinica")),
            "neto": osn, "neto_cijena": cijena * (Decimal(100) - pop) / Decimal(100),
            "bruto_cijena": cijena if pop else None,
            "kategorija": kat, "stopa": stopa,
            "naziv": s["naziv"], "hr_oznaka": _hr_oznaka(kat, stopa),
        }
        if kpd and vrsta_dokumenta not in ("381", "386"):
            st["klasifikacije"] = [{"id": kpd, "shema": "CG"}]
        st_out.append(st)

    raspodjela = []
    for (kat, stopa), (o, p) in sorted(grupe.items(), key=lambda x: (x[0][0], x[0][1])):
        rp = {"kategorija": kat, "oporezivi": _cent(o), "iznos": _cent(p),
              "stopa": stopa, "hr_oznaka": _hr_oznaka(kat, stopa)}
        if kat == KATEGORIJA_NEOPOREZIVO and tekst_oslobodenja:
            rp["tekst"] = tekst_oslobodenja
        raspodjela.append(rp)

    osn, pdv = _cent(ukupno_osn), _cent(ukupno_pdv)
    ukupno = osn + pdv
    iban = (racun.get("iban") or "").replace(" ", "")
    return {
        "broj_dokumenta": str(racun.get("broj_racuna")
                              or f"{racun.get('id')}"),
        "datum_izdavanja": racun["datum"],
        "vrsta_dokumenta": vrsta_dokumenta,
        "valuta": "EUR",
        "datum_dospijeca": racun.get("rok_placanja") or None,
        "vrsta_procesa": "P1",
        "datum_isporuke": racun["datum"],
        "izdavatelj": {
            "ime": racun.get("naziv_izdavatelja") or cfg.get("naziv_tvrtke", ""),
            "oib": racun.get("oib_izdavatelja") or cfg.get("oib", ""),
            "oib_operatera": cfg.get("oib_operatera") or racun.get("oib_izdavatelja")
            or cfg.get("oib", ""),
        },
        "primatelj": {"ime": racun.get("naziv_kupca", ""),
                      "oib": racun.get("oib_kupca", "")},
        "prijenos_sredstava": ([{"iban": iban}] if iban else []),
        "ukupno": {"neto": osn, "bez_pdv": osn, "pdv": pdv,   # XSD: pdv je obavezan (0.00 izvan PDV-a)
                   "s_pdv": ukupno, "dospijeva": ukupno},
        "raspodjela_pdv": raspodjela,
        "stavke": st_out,
        "kopija": False,
    }
