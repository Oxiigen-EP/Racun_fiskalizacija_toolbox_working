"""Testovi ZKI-ja, potpisa XML-a i građenja zahtjeva (bez mreže)."""
import base64
import hashlib
import re

import lxml.etree as etree
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding

DS = "http://www.w3.org/2000/09/xmldsig#"
NS = {"ds": DS, "tns": "http://www.apis-it.hr/fin/2012/types/f73"}

RACUN = {
    "oib": "12345678903", "broj_racuna": "17", "oznaka_pp": "PP1",
    "oznaka_nu": "1", "ukupan_iznos": 125.0, "u_sustavu_pdv": True,
    "nacin_placanja": "T", "datum_vrijeme": "01.10.2026T12:00:00",
    "pdv": [{"Stopa": "25.00", "Osnovica": "100.00", "Iznos": "25.00"}],
}


# ── ZKI ──────────────────────────────────────────────────────────────────

def test_zki_ulazni_niz_prema_specifikaciji(fisk):
    """OIB + 'dd.MM.yyyy HH:mm:ss' (razmak, ne 'T') + broj + PP + NU + iznos
    s točkom i dvije decimale."""
    uhvaceno = {}

    class Lazni:
        def sign(self, data, pad, alg):
            uhvaceno["data"] = data
            uhvaceno["alg"] = alg
            return b"potpis"

    fisk.private_key = Lazni()
    zki = fisk.generiraj_zki("12345678903", "01.10.2026T12:00:00",
                             "17", "PP1", "1", 125)
    assert uhvaceno["data"] == b"1234567890301.10.2026 12:00:0017PP11125.00"
    assert isinstance(uhvaceno["alg"], hashes.SHA256)
    assert zki == hashlib.md5(b"potpis").hexdigest()


def test_zki_je_md5_rsa_sha256_potpisa(fisk, testni_certifikat):
    zki = fisk.generiraj_zki("12345678903", "01.10.2026T12:00:00",
                             "17", "PP1", "1", 125.0)
    data = b"1234567890301.10.2026 12:00:0017PP11125.00"
    potpis = testni_certifikat["private"].sign(
        data, padding.PKCS1v15(), hashes.SHA256())
    assert zki == hashlib.md5(potpis).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{32}", zki)


def test_zki_se_mijenja_s_iznosom_i_stabilan_je(fisk):
    a = fisk.generiraj_zki("12345678903", "01.10.2026T12:00:00", "17", "PP1", "1", 125.0)
    b = fisk.generiraj_zki("12345678903", "01.10.2026T12:00:00", "17", "PP1", "1", 125.0)
    c = fisk.generiraj_zki("12345678903", "01.10.2026T12:00:00", "17", "PP1", "1", 125.01)
    assert a == b
    assert a != c


# ── Potpis XML-a ─────────────────────────────────────────────────────────

def _potpisano(fisk, racun=RACUN):
    xml, zki, _ = fisk.izgradi_zahtjev(racun)
    return etree.fromstring(fisk._potpiši_xml_enveloped(xml)), zki


def test_potpis_koristi_sha256_a_ne_sha1(fisk):
    root, _ = _potpisano(fisk)
    sig = root.find("ds:Signature", NS)
    assert sig is not None
    metoda = sig.find(".//ds:SignatureMethod", NS).get("Algorithm")
    digest = sig.find(".//ds:DigestMethod", NS).get("Algorithm")
    assert metoda.endswith("rsa-sha256")
    assert digest.endswith("#sha256")
    assert b"sha1" not in etree.tostring(root).lower()


def test_digest_odgovara_sadrzaju_zahtjeva(fisk):
    root, _ = _potpisano(fisk)
    sig = root.find("ds:Signature", NS)
    zapisani = sig.find(".//ds:DigestValue", NS).text
    root.remove(sig)                      # enveloped-signature transform
    c14n = etree.tostring(root, method="c14n", exclusive=True)
    ocekivani = base64.b64encode(hashlib.sha256(c14n).digest()).decode()
    assert zapisani == ocekivani


def test_potpis_se_provjerava_javnim_kljucem(fisk, testni_certifikat):
    root, _ = _potpisano(fisk)
    sig = root.find("ds:Signature", NS)
    signed_info = sig.find("ds:SignedInfo", NS)
    c14n = etree.tostring(signed_info, method="c14n", exclusive=True)
    vrijednost = base64.b64decode(sig.find("ds:SignatureValue", NS).text)
    # baca iznimku ako potpis nije ispravan
    testni_certifikat["public"].verify(
        vrijednost, c14n, padding.PKCS1v15(), hashes.SHA256())


def test_potpis_pada_ako_se_zahtjev_izmijeni(fisk, testni_certifikat):
    root, _ = _potpisano(fisk)
    root.find(".//tns:IznosUkupno", NS).text = "1.00"
    sig = root.find("ds:Signature", NS)
    zapisani = sig.find(".//ds:DigestValue", NS).text
    root.remove(sig)
    c14n = etree.tostring(root, method="c14n", exclusive=True)
    assert zapisani != base64.b64encode(hashlib.sha256(c14n).digest()).decode()


def test_potpis_sadrzi_certifikat(fisk):
    root, _ = _potpisano(fisk)
    cert = root.find(".//ds:X509Certificate", NS).text
    assert base64.b64decode(cert)       # valjan base64 DER


# ── Građenje zahtjeva ────────────────────────────────────────────────────

def test_zahtjev_sadrzi_zki_iznos_i_pdv(fisk):
    xml, zki, dv = fisk.izgradi_zahtjev(RACUN)
    root = etree.fromstring(xml.encode())
    assert dv == "01.10.2026T12:00:00"
    assert root.find(".//tns:ZastKod", NS).text == zki
    assert root.find(".//tns:IznosUkupno", NS).text == "125.00"
    assert root.find(".//tns:NacinPlac", NS).text == "T"
    assert root.find(".//tns:NakDost", NS).text == "false"
    assert root.find(".//tns:Pdv/tns:Porez/tns:Stopa", NS).text == "25.00"


def test_naknadna_dostava_zadrzava_izvorni_zki_i_vrijeme(fisk):
    r = dict(RACUN, zki="a" * 32, naknadna_dostava=True)
    xml, zki, dv = fisk.izgradi_zahtjev(r)
    root = etree.fromstring(xml.encode())
    assert zki == "a" * 32
    assert root.find(".//tns:ZastKod", NS).text == "a" * 32
    assert root.find(".//tns:DatVrijeme", NS).text == "01.10.2026T12:00:00"
    assert root.find(".//tns:NakDost", NS).text == "true"


def test_bez_pdv_sustava_nema_pdv_bloka(fisk):
    r = dict(RACUN, u_sustavu_pdv=False, pdv=[])
    xml, _, _ = fisk.izgradi_zahtjev(r)
    root = etree.fromstring(xml.encode())
    assert root.find(".//tns:Pdv", NS) is None
    assert root.find(".//tns:USustPdv", NS).text == "false"


# ── QR kod ───────────────────────────────────────────────────────────────

def test_qr_iznos_u_centima_i_format_datuma(fisk, monkeypatch):
    import fiskalizacija
    uhvaceno = {}

    class LazniQR:
        constants = type("c", (), {"ERROR_CORRECT_L": 1})

        class QRCode:
            def __init__(self, **kw): pass
            def add_data(self, d): uhvaceno["url"] = d
            def make(self, fit=True): pass
            def make_image(self, **kw): return object()

    monkeypatch.setattr(fiskalizacija, "qrcode", LazniQR, raising=False)
    monkeypatch.setattr(fiskalizacija, "QR_DOSTUPAN", True)
    fisk.generiraj_qr_kod(jir="JIR-1", datum_vrijeme="01.10.2026T12:34:56",
                          ukupan_iznos=67.46)
    assert uhvaceno["url"] == ("https://porezna.gov.hr/rn?jir=JIR-1"
                               "&datv=20261001_1234&izn=6746")
