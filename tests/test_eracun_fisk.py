"""Testovi Fiskalizacije 2.0 (eRačun + eIzvještavanje), bez mreže."""
import base64
import re
from decimal import Decimal

import lxml.etree as etree
import pytest

import eracun_fisk as ef
from eracun_fisk import (NS_DS, NS_EFIS, NS_EIZ, NS_XADES, ERacunGreska,
                         EFiskalizacija)

NS = {"efis": NS_EFIS, "eiz": NS_EIZ, "ds": NS_DS, "xades": NS_XADES}
OIB_A = "02994650199"      # OIB iz primjera u specifikaciji
OIB_B = "12345678903"


def ERACUN(**izmjene):
    e = {
        "broj_dokumenta": "INV-2025-FM-1058",
        "datum_izdavanja": "2025-03-30",
        "vrsta_dokumenta": "380",
        "valuta": "EUR",
        "vrsta_procesa": "P1",
        "datum_isporuke": "2025-03-30",
        "izdavatelj": {"ime": "Tvrtka A", "oib": OIB_A, "oib_operatera": OIB_A},
        "primatelj": {"ime": "Tvrtka B", "oib": OIB_B},
        "ukupno": {"neto": "1000.00", "bez_pdv": "1000.00", "pdv": "250.00",
                   "s_pdv": "1250.00", "dospijeva": "1250.00"},
        "raspodjela_pdv": [{"kategorija": "S", "oporezivi": "1000.00",
                            "iznos": "250.00", "stopa": 25,
                            "hr_oznaka": "HR:PDV25"}],
        "stavke": [{"kolicina": 2, "jedinica": "H87", "neto": "1000.00",
                    "neto_cijena": "500.00", "kategorija": "S", "stopa": 25,
                    "naziv": "naziv artikla", "hr_oznaka": "HR:PDV25",
                    "klasifikacije": [{"id": "11.07.01", "shema": "CG"}]}],
        "kopija": False,
    }
    e.update(izmjene)
    return e


@pytest.fixture
def ef_klijent(testni_certifikat, tmp_path):
    return EFiskalizacija(testni_certifikat["cert"], testni_certifikat["key"],
                          demo=True, arhiva_mapa=str(tmp_path))


def _oznaceno(korijen):
    return [etree.QName(c).localname for c in korijen]


# ── pomoćne funkcije ─────────────────────────────────────────────────────

def test_oib_kontrolna_znamenka():
    assert ef.oib_ispravan(OIB_A)
    assert ef.oib_ispravan(OIB_B)
    assert not ef.oib_ispravan("02994650198")
    assert not ef.oib_ispravan("1234567890")
    assert not ef.oib_ispravan("abcdefghijk")
    assert not ef.oib_ispravan(None)


def test_format_vremena_slanja():
    from datetime import datetime
    s = ef.vrijeme_slanja(datetime(2024, 4, 1, 8, 4, 2, 123456))
    assert s == "2024-04-01T08:04:02.1234"
    assert re.fullmatch(
        r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{4}", s)


def test_iznosi_i_brojevi():
    assert ef.iznos(59.9) == "59.90"
    assert ef.iznos(Decimal("0.005")) == "0.01"
    assert ef.broj(25) == "25"
    assert ef.broj("2.50") == "2.5"
    assert ef.broj(Decimal("8.33333333333")) == "8.3333333333"


def test_datumi_iz_aplikacije():
    assert ef.datum_iso("30.03.2025") == "2025-03-30"
    assert ef.datum_iso("2025-03-30") == "2025-03-30"
    with pytest.raises(ERacunGreska):
        ef.datum_iso("30/03/2025")


def test_jedinice_mjere():
    assert ef.jedinica_mjere("kom") == "H87"
    assert ef.jedinica_mjere("sat") == "HUR"
    assert ef.jedinica_mjere("HUR") == "HUR"
    assert ef.jedinica_mjere("nepoznato") == "C62"


# ── EvidentirajERacun ────────────────────────────────────────────────────

def test_evidentiraj_eracun_slijedi_primjer_iz_specifikacije():
    k = ef.gradi_evidentiraj_eracun([ERACUN()], vrsta="I", zahtjev_id="id-1")
    assert k.tag == f"{{{NS_EFIS}}}EvidentirajERacunZahtjev"
    assert k.get(f"{{{NS_EFIS}}}id") == "id-1"
    assert _oznaceno(k) == ["Zaglavlje", "ERacun"]
    z = k.find("efis:Zaglavlje", NS)
    assert z.find("efis:vrstaERacuna", NS).text == "I"
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{4}",
                        z.find("efis:datumVrijemeSlanja", NS).text)
    r = k.find("efis:ERacun", NS)
    assert _oznaceno(r) == [
        "brojDokumenta", "datumIzdavanja", "vrstaDokumenta", "valutaERacuna",
        "vrstaPoslovnogProcesa", "datumIsporuke", "Izdavatelj", "Primatelj",
        "DokumentUkupanIznos", "RaspodjelaPdv", "StavkaERacuna", "indikatorKopije"]
    assert _oznaceno(r.find("efis:DokumentUkupanIznos", NS)) == [
        "neto", "iznosBezPdv", "pdv", "iznosSPdv", "iznosKojiDospijevaZaPlacanje"]
    assert _oznaceno(r.find("efis:RaspodjelaPdv", NS)) == [
        "kategorijaPdv", "oporeziviIznos", "iznosPoreza", "stopa",
        "hrOznakaKategorijaPdv"]
    assert _oznaceno(r.find("efis:StavkaERacuna", NS)) == [
        "kolicina", "jedinicaMjere", "neto", "artiklNetoCijena",
        "artiklKategorijaPdv", "artiklStopaPdv", "artiklNaziv",
        "artiklHrOznakaKategorijaPdv", "ArtiklIdentifikatorKlasifikacija"]
    assert r.find("efis:RaspodjelaPdv/efis:stopa", NS).text == "25"
    assert r.find("efis:indikatorKopije", NS).text == "false"


def test_ulazni_eracun_vrsta_u():
    k = ef.gradi_evidentiraj_eracun([ERACUN()], vrsta="U")
    assert k.find("efis:Zaglavlje/efis:vrstaERacuna", NS).text == "U"
    with pytest.raises(ERacunGreska):
        ef.gradi_evidentiraj_eracun([ERACUN()], vrsta="X")


def test_ulazni_eracuni_moraju_imati_istog_kupca():
    drugi = ERACUN(broj_dokumenta="2", primatelj={"ime": "C", "oib": OIB_A})
    with pytest.raises(ERacunGreska, match="OIB kupca"):
        ef.gradi_evidentiraj_eracun([ERACUN(), drugi], vrsta="U")
    ef.gradi_evidentiraj_eracun([ERACUN(), drugi], vrsta="I")     # za izlazne može


def test_neispravni_podaci_se_odbijaju_prije_slanja():
    losi = ERACUN(izdavatelj={"ime": "A", "oib": "02994650198", "oib_operatera": OIB_A},
                  stavke=[], raspodjela_pdv=[])
    with pytest.raises(ERacunGreska) as e:
        ef.gradi_evidentiraj_eracun([losi])
    poruka = str(e.value)
    assert "OIB izdavatelja" in poruka
    assert "barem jednu stavku" in poruka
    assert "raspodjelu PDV-a" in poruka


def test_kategorija_ae_traži_razlog_i_tekst():
    e = ERACUN()
    e["raspodjela_pdv"][0].update(kategorija="AE")
    with pytest.raises(ERacunGreska, match="AE"):
        ef.gradi_evidentiraj_eracun([e])
    e["raspodjela_pdv"][0].update(razlog="VATEX-EU-AE", tekst="Prijenos porezne obveze")
    ef.gradi_evidentiraj_eracun([e])


def test_klasifikacija_se_ne_unosi_za_odobrenje_i_predujam():
    for vrsta in ("381", "386"):
        with pytest.raises(ERacunGreska, match="klasifikacija"):
            ef.gradi_evidentiraj_eracun([ERACUN(vrsta_dokumenta=vrsta)])


def test_najvise_100_zapisa_i_barem_jedan():
    with pytest.raises(ERacunGreska):
        ef.gradi_evidentiraj_eracun([])
    ef.gradi_evidentiraj_eracun([ERACUN()] * 100)
    with pytest.raises(ERacunGreska, match="100"):
        ef.gradi_evidentiraj_eracun([ERACUN()] * 101)


def test_popust_trosak_prijenos_sredstava_i_prethodni():
    e = ERACUN(
        prethodni=[{"broj_dokumenta": "1/PP1/1", "datum_izdavanja": "2025-03-01"}],
        prijenos_sredstava=[{"iban": "HR1210010051863000160", "naziv": "Racun"}],
        popusti=[{"iznos": 25, "kategorija": "S", "stopa": 25,
                  "tekst": "popust", "razlog": "98"}],
        troskovi=[{"iznos": 25, "kategorija": "S", "stopa": 25,
                   "hr_oznaka": "HR:PDV25"}])
    r = ef.gradi_evidentiraj_eracun([e]).find("efis:ERacun", NS)
    assert r.find("efis:PrethodniERacun/efis:brojDokumenta", NS).text == "1/PP1/1"
    assert r.find("efis:PrijenosSredstava/efis:identifikatorRacunaZaPlacanje",
                  NS).text == "HR1210010051863000160"
    assert _oznaceno(r.find("efis:DokumentPopust", NS)) == [
        "iznosPopust", "kategorijaPdv", "stopaPdv", "tekstRazlogaPopusta",
        "razlogPopusta"]
    assert r.find("efis:DokumentPopust/efis:iznosPopust", NS).text == "25.00"
    assert _oznaceno(r.find("efis:DokumentTrosak", NS)) == [
        "iznosTrosak", "kategorijaPdv", "hrOznakaPorezneKategorije", "stopaPdv"]


# ── isporuka bez eRačuna, naplata, odbijanje, ovlaštenja ─────────────────

def test_isporuka_bez_eracuna_ima_svoje_nazive_elemenata():
    k = ef.gradi_isporuku_bez_eracuna([ERACUN()])
    assert k.tag == f"{{{NS_EIZ}}}EvidentirajIsporukuZaKojuNijeIzdanERacunZahtjev"
    assert k.find("eiz:Zaglavlje/eiz:vrstaRacuna", NS).text == "IR"
    r = k.find("eiz:Racun", NS)
    assert r.find("eiz:valutaRacuna", NS).text == "EUR"
    assert r.find("eiz:StavkaRacuna", NS) is not None
    assert r.find("eiz:StavkaERacuna", NS) is None


def test_naplata():
    n = {"broj_dokumenta": "1234-2024-06", "datum_izdavanja": "27.02.2024",
         "oib_izdavatelja": OIB_A, "oib_primatelja": OIB_B,
         "datum_naplate": "2024-06-17", "iznos": 110, "nacin_placanja": "T"}
    k = ef.gradi_naplatu([n])
    p = k.find("eiz:Naplata", NS)
    assert _oznaceno(p) == [
        "brojDokumenta", "datumIzdavanja", "oibPorezniBrojIzdavatelja",
        "oibPorezniBrojPrimatelja", "datumNaplate", "naplaceniIznos", "nacinPlacanja"]
    assert p.find("eiz:naplaceniIznos", NS).text == "110.00"
    assert p.find("eiz:datumIzdavanja", NS).text == "2024-02-27"
    # storno naplate: negativan iznos
    k = ef.gradi_naplatu([dict(n, iznos=-110)])
    assert k.find("eiz:Naplata/eiz:naplaceniIznos", NS).text == "-110.00"
    for krivo in (dict(n, nacin_placanja="G"), dict(n, oib_primatelja="1")):
        with pytest.raises(ERacunGreska):
            ef.gradi_naplatu([krivo])


def test_odbijanje():
    o = {"broj_dokumenta": "1234/2024/06", "datum_izdavanja": "2024-02-27",
         "oib_izdavatelja": OIB_B, "oib_primatelja": OIB_A,
         "datum_odbijanja": "2024-02-28", "vrsta_razloga": "N",
         "razlog": "Krivi primatelj eRačuna."}
    k = ef.gradi_odbijanje([o])
    assert _oznaceno(k.find("eiz:Odbijanje", NS))[-2:] == [
        "vrstaRazlogaOdbijanja", "razlogOdbijanja"]
    for krivo in (dict(o, vrsta_razloga="X"), dict(o, razlog="")):
        with pytest.raises(ERacunGreska):
            ef.gradi_odbijanje([krivo])


def test_ovlastenja():
    k = ef.gradi_ovlastenja(OIB_A)
    assert k.find("eiz:oib", NS).text == OIB_A
    assert ef.gradi_ovlastenja().find("eiz:oib", NS) is None
    with pytest.raises(ERacunGreska):
        ef.gradi_ovlastenja("123")


# ── XAdES-B potpis ───────────────────────────────────────────────────────

def _potpisan(klijent, korijen=None):
    korijen = korijen if korijen is not None else ef.gradi_evidentiraj_eracun([ERACUN()])
    ef.potpisi_xades_b(korijen, klijent.private_key, klijent.certificate)
    return korijen


def test_potpis_ima_strukturu_iz_specifikacije(ef_klijent):
    k = _potpisan(ef_klijent)
    sig = k.find("ds:Signature", NS)
    assert sig is not None and sig.get("Id", "").startswith("id-")
    assert _oznaceno(sig) == ["SignedInfo", "SignatureValue", "KeyInfo", "Object"]
    si = sig.find("ds:SignedInfo", NS)
    assert si.find("ds:CanonicalizationMethod", NS).get("Algorithm").endswith("xml-exc-c14n#")
    assert si.find("ds:SignatureMethod", NS).get("Algorithm").endswith("rsa-sha256")
    r1, r2 = si.findall("ds:Reference", NS)
    assert r1.get("URI") == "#" + k.get(f"{{{NS_EFIS}}}id")
    assert [t.get("Algorithm") for t in r1.findall(".//ds:Transform", NS)] == [
        "http://www.w3.org/2000/09/xmldsig#enveloped-signature",
        "http://www.w3.org/2001/10/xml-exc-c14n#"]
    assert r2.get("Type") == "http://uri.etsi.org/01903#SignedProperties"
    assert len(r2.findall(".//ds:Transform", NS)) == 1
    for r in (r1, r2):
        assert r.find("ds:DigestMethod", NS).get("Algorithm").endswith("#sha256")
    assert b"sha1" not in etree.tostring(k).lower()
    # XAdES-B: SigningTime, SigningCertificateV2, DataObjectFormat
    qp = sig.find("ds:Object/xades:QualifyingProperties", NS)
    assert qp.get("Target") == "#" + sig.get("Id")
    sp = qp.find("xades:SignedProperties", NS)
    assert r2.get("URI") == "#" + sp.get("Id")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                        sp.find(".//xades:SigningTime", NS).text)
    assert sp.find(".//xades:SigningCertificateV2/xades:Cert/xades:IssuerSerialV2", NS) is not None
    dof = sp.find(".//xades:DataObjectFormat", NS)
    assert dof.get("ObjectReference") == "#" + r1.get("Id")


def test_potpis_se_provjerava_i_nakon_serijalizacije(ef_klijent, testni_certifikat):
    k = _potpisan(ef_klijent)
    ef.provjeri_potpis(k, testni_certifikat["public"])
    soap = ef.u_soap(k)
    primljeno = etree.fromstring(soap)
    tijelo = primljeno.find(f"{{{ef.NS_SOAP}}}Body")[0]
    ef.provjeri_potpis(tijelo, testni_certifikat["public"])
    ef.provjeri_potpis(tijelo)                 # javni ključ iz KeyInfo


def test_izmjena_zahtjeva_kvari_potpis(ef_klijent):
    k = _potpisan(ef_klijent)
    k.find(".//efis:iznosSPdv", NS).text = "1.00"
    with pytest.raises(ValueError, match="Digest"):
        ef.provjeri_potpis(k)


def test_izmjena_signed_properties_kvari_potpis(ef_klijent):
    k = _potpisan(ef_klijent)
    k.find(".//xades:SigningTime", NS).text = "2000-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="Digest"):
        ef.provjeri_potpis(k)


def test_izmjena_signed_info_kvari_potpis(ef_klijent):
    from cryptography.exceptions import InvalidSignature
    k = _potpisan(ef_klijent)
    k.find(".//ds:SignedInfo/ds:Reference/ds:DigestMethod", NS).set(
        "Algorithm", "http://www.w3.org/2001/04/xmlenc#sha512")
    with pytest.raises((InvalidSignature, ValueError)):
        ef.provjeri_potpis(k)


def test_potpis_radi_za_sve_vrste_poruka(ef_klijent):
    poruke = [
        ef.gradi_isporuku_bez_eracuna([ERACUN()]),
        ef.gradi_ovlastenja(OIB_A),
        ef.gradi_naplatu([{"broj_dokumenta": "1", "datum_izdavanja": "2025-01-01",
                           "oib_izdavatelja": OIB_A, "oib_primatelja": OIB_B,
                           "datum_naplate": "2025-01-02", "iznos": 1,
                           "nacin_placanja": "O"}]),
    ]
    for k in poruke:
        ef.provjeri_potpis(_potpisan(ef_klijent, k))


def test_issuer_serial_v2_je_ispravan_der(testni_certifikat):
    from cryptography import x509
    cert = x509.load_pem_x509_certificate(open(testni_certifikat["cert"], "rb").read())
    der = ef.issuer_serial_v2(cert)
    assert der[0] == 0x30                                     # SEQUENCE
    assert cert.issuer.public_bytes() in der                  # Name je unutra
    # serijski broj 12345 = 0x3039 zapisan kao INTEGER na kraju
    assert der.endswith(bytes([0x02, 0x02, 0x30, 0x39]))
    # duljina u zaglavlju odgovara sadržaju
    if der[1] & 0x80:
        n = der[1] & 0x7F
        assert int.from_bytes(der[2:2 + n], "big") == len(der) - 2 - n
    else:
        assert der[1] == len(der) - 2


def test_issuer_serial_visoki_bit_dobiva_vodecu_nulu():
    class Lazni:
        serial_number = 0x80
        class issuer:
            @staticmethod
            def public_bytes():
                return b"\x30\x00"
    der = ef.issuer_serial_v2(Lazni)
    assert der.endswith(bytes([0x02, 0x02, 0x00, 0x80]))


def test_neovisna_biblioteka_potvrduje_potpis(ef_klijent, testni_certifikat, monkeypatch):
    """signxml (neovisna implementacija) provjerava obje reference i potpis.
    Ona ne razumije atribut s imenskim prostorom (efis:id), pa se za ovaj
    test korijen potpisuje s običnim 'Id'; ostalo je isto."""
    signxml = pytest.importorskip("signxml")
    from cryptography.hazmat.primitives import serialization
    monkeypatch.setattr(ef, "_id_atribut", lambda ns: "Id")
    k = ef.gradi_evidentiraj_eracun([ERACUN()])
    ef.potpisi_xades_b(k, ef_klijent.private_key, ef_klijent.certificate)
    xml = etree.tostring(k)
    pem = ef_klijent.certificate.public_bytes(serialization.Encoding.PEM).decode()
    signxml.XMLVerifier().verify(xml, x509_cert=pem, id_attribute="Id",
                                 expect_references=2)
    with pytest.raises(signxml.exceptions.InvalidSignature):
        signxml.XMLVerifier().verify(xml.replace(b"1250.00", b"1.00", 1),
                                     x509_cert=pem, id_attribute="Id",
                                     expect_references=2)


# ── odgovori ─────────────────────────────────────────────────────────────

ODG_OK = """<fis:EvidentirajERacunOdgovor fis:Id="unique-request-45"
 xmlns:fis="http://www.porezna-uprava.gov.hr/fin/2024/types/eFiskalizacija"
 xmlns:xd="http://www.w3.org/2000/09/xmldsig#">
 <fis:datumVrijemeSlanja>2024-12-10T10:53:02+01:00</fis:datumVrijemeSlanja>
 <fis:Odgovor><fis:idZahtjeva>ef9629b1</fis:idZahtjeva>
 <fis:prihvacenZahtjev>true</fis:prihvacenZahtjev></fis:Odgovor></fis:EvidentirajERacunOdgovor>""".encode("utf-8")

ODG_GRESKA = """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body>
 <fis:EvidentirajERacunOdgovor xmlns:fis="http://www.porezna-uprava.gov.hr/fin/2024/types/eFiskalizacija">
 <fis:Odgovor><fis:idZahtjeva>b8b50de9</fis:idZahtjeva>
 <fis:prihvacenZahtjev>false</fis:prihvacenZahtjev>
 <fis:greska><fis:sifra>S008</fis:sifra><fis:redniBrojZapisa>1</fis:redniBrojZapisa>
 <fis:opis>Već postoji zabilježen eRačun s istim identifikatorom.</fis:opis></fis:greska>
 </fis:Odgovor></fis:EvidentirajERacunOdgovor></soapenv:Body></soapenv:Envelope>""".encode("utf-8")

ODG_OVL = """<eizv:OvlastenjaFiskalizacijeOdgovor xmlns:eizv="http://www.porezna-uprava.gov.hr/fin/2024/types/eIzvjestavanje">
 <eizv:datumVrijemeSlanja>2026-02-06T10:15:31</eizv:datumVrijemeSlanja>
 <eizv:ovlasteniOib>00000000001</eizv:ovlasteniOib>
 <eizv:ovlasteniOib>00000000002</eizv:ovlasteniOib></eizv:OvlastenjaFiskalizacijeOdgovor>""".encode("utf-8")

ODG_FAULT = """<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"><soapenv:Body>
 <soapenv:Fault><faultcode>soapenv:Server</faultcode><faultstring>Greška servera</faultstring>
 </soapenv:Fault></soapenv:Body></soapenv:Envelope>""".encode("utf-8")


def test_parsiranje_uspjesnog_odgovora():
    o = ef.parsiraj_odgovor(ODG_OK, 200)
    assert o["prihvacen"] is True
    assert o["id_zahtjeva"] == "ef9629b1"
    assert o["greske"] == []


def test_parsiranje_odgovora_s_greskom():
    o = ef.parsiraj_odgovor(ODG_GRESKA, 200)
    assert o["prihvacen"] is False
    assert o["greske"][0]["sifra"] == "S008"
    assert o["greske"][0]["redni_broj"] == 1
    assert "identifikatorom" in o["greske"][0]["opis"]
    assert ef.opis_gresaka(o).startswith("S008: Već postoji")


def test_parsiranje_ovlastenja_i_faulta_i_smeca():
    assert ef.parsiraj_odgovor(ODG_OVL)["ovlasteni_oib"] == ["00000000001", "00000000002"]
    f = ef.parsiraj_odgovor(ODG_FAULT, 500)
    assert f["prihvacen"] is False and f["fault"] == "Greška servera"
    s = ef.parsiraj_odgovor(b"<html>nije xml", 502)
    assert s["prihvacen"] is False and s["greske"][0]["sifra"] == "XML"


# ── slanje (s lažnom sesijom) ────────────────────────────────────────────

class LaznaSesija:
    def __init__(self, sadrzaj, status=200, iznimka=None):
        self.sadrzaj, self.status, self.iznimka = sadrzaj, status, iznimka
        self.pozivi = []

    def post(self, url, data=None, headers=None, verify=None, timeout=None):
        self.pozivi.append({"url": url, "data": data, "headers": headers,
                            "verify": verify, "timeout": timeout})
        if self.iznimka:
            raise self.iznimka

        class Odg:
            status_code = self.status
            content = self.sadrzaj
        return Odg


def test_slanje_potpisuje_arhivira_i_vraca_odgovor(ef_klijent, tmp_path, monkeypatch):
    sesija = LaznaSesija(ODG_OK)
    monkeypatch.setattr(ef_klijent, "_sesija", lambda: sesija)
    o = ef_klijent.evidentiraj_eracun([ERACUN()])
    assert o["prihvacen"] is True and ef_klijent.zadnja_greska is None
    poziv = sesija.pozivi[0]
    assert poziv["url"] == ef.URL_TEST
    assert poziv["headers"]["Content-Type"].startswith("text/xml")
    # poslano je potpisano SOAP tijelo
    tijelo = etree.fromstring(poziv["data"]).find(f"{{{ef.NS_SOAP}}}Body")[0]
    ef.provjeri_potpis(tijelo)
    # zahtjev i odgovor su arhivirani
    arhiva = list((tmp_path / "arhiva").rglob("*.xml"))
    imena = " ".join(p.name for p in arhiva)
    assert "zahtjev" in imena and "odgovor_http200" in imena


def test_slanje_neprihvacenog_zahtjeva_postavlja_gresku(ef_klijent, monkeypatch):
    monkeypatch.setattr(ef_klijent, "_sesija", lambda: LaznaSesija(ODG_GRESKA))
    o = ef_klijent.evidentiraj_eracun([ERACUN()])
    assert o["prihvacen"] is False
    assert "S008" in ef_klijent.zadnja_greska


def test_mrezna_greska_ne_ruši_aplikaciju(ef_klijent, tmp_path, monkeypatch):
    monkeypatch.setattr(ef_klijent, "_sesija",
                        lambda: LaznaSesija(b"", iznimka=ConnectionError("nema mreže")))
    o = ef_klijent.evidentiraj_eracun([ERACUN()])
    assert o["prihvacen"] is False and o["greske"][0]["sifra"] == "MREZA"
    assert "nema mreže" in ef_klijent.zadnja_greska
    assert list((tmp_path / "arhiva").rglob("*greska*.txt"))


def test_lokalna_greska_se_vraca_bez_slanja(ef_klijent, monkeypatch):
    sesija = LaznaSesija(ODG_OK)
    monkeypatch.setattr(ef_klijent, "_sesija", lambda: sesija)
    k = ef.gradi_evidentiraj_eracun([ERACUN()])
    ef_klijent.xsd = {ef.NS_EFIS: type("X", (), {
        "validate": lambda self, el: False,
        "error_log": type("L", (), {"last_error": "element nije valjan"})()})()}
    o = ef_klijent.posalji(k, "x")
    assert o["greske"][0]["sifra"] == "LOKALNO" and not sesija.pozivi


def test_url_i_tls_postavke(testni_certifikat, tmp_path):
    test = EFiskalizacija(testni_certifikat["cert"], testni_certifikat["key"],
                          demo=True, arhiva_mapa=str(tmp_path))
    prod = EFiskalizacija(testni_certifikat["cert"], testni_certifikat["key"],
                          demo=False, arhiva_mapa=str(tmp_path))
    assert test.url == "https://cistest.apis-it.hr:8509/FiskalizacijaServiceEprod"
    assert prod.url == "https://cis.porezna-uprava.hr:8509/FiskalizacijaService"
    assert prod.verify is True                    # produkcija uvijek provjerava TLS
    ca = tmp_path / "ca.pem"
    ca.write_text("x")
    s = EFiskalizacija(testni_certifikat["cert"], testni_certifikat["key"],
                       demo=True, arhiva_mapa=str(tmp_path), ca_path=str(ca))
    assert s.verify == str(ca)
    sesija = prod._sesija()
    adapter = sesija.get_adapter("https://cis.porezna-uprava.hr:8509/")
    ctx = adapter.poolmanager.connection_pool_kw["ssl_context"]
    import ssl
    assert ctx.minimum_version == ssl.TLSVersion.TLSv1_2


# ── mapiranje iz računa u aplikaciji ─────────────────────────────────────

RACUN_DB = {
    "id": 17, "broj_racuna": "17/PP1/1", "datum": "30.03.2026",
    "rok_placanja": "14.04.2026", "oib_izdavatelja": OIB_A,
    "naziv_izdavatelja": "Remi vl. Emir Pretnar", "oib_kupca": OIB_B,
    "naziv_kupca": "Kupac d.o.o.", "iban": "HR12 1001 0051 8630 0016 0",
    "u_sustavu_pdv": 0,
}
STAVKE_DB = [
    {"naziv": "Izrada aplikacije", "kolicina": 10, "jedinica": "sat",
     "cijena_cent": 4500, "pdv_stopa": 0, "popust": 0},
    {"naziv": "Hosting", "kolicina": 1, "jedinica": "kom",
     "cijena_cent": 2000, "pdv_stopa": 0, "popust": 10},
]


def test_paušalist_izvan_pdv_a_dobiva_kategoriju_o():
    e = ef.eracun_iz_racuna(RACUN_DB, STAVKE_DB, {}, kpd="62.01.11",
                            tekst_oslobodenja="Oslobođeno PDV-a temeljem članka 90. st. 1")
    assert e["broj_dokumenta"] == "17/PP1/1"
    assert e["ukupno"]["bez_pdv"] == Decimal("468.00")      # 450 + 18
    assert e["ukupno"]["s_pdv"] == Decimal("468.00")
    assert e["ukupno"]["pdv"] == Decimal("0.00")
    assert [s["kategorija"] for s in e["stavke"]] == ["O", "O"]
    assert e["stavke"][0]["jedinica"] == "HUR"
    assert e["stavke"][1]["bruto_cijena"] == Decimal("20.00")
    assert e["stavke"][1]["neto_cijena"] == Decimal("18.00")
    rp, = e["raspodjela_pdv"]
    assert rp["kategorija"] == "O" and rp["oporezivi"] == Decimal("468.00")
    assert "članka 90" in rp["tekst"]
    assert e["prijenos_sredstava"] == [{"iban": "HR1210010051863000160"}]
    # i potpuno valjan zahtjev prema pravilima specifikacije
    k = ef.gradi_evidentiraj_eracun([e])
    r = k.find("efis:ERacun", NS)
    assert r.find("efis:datumIzdavanja", NS).text == "2026-03-30"
    assert r.find("efis:datumDospijecaPlacanja", NS).text == "2026-04-14"
    assert r.find("efis:DokumentUkupanIznos/efis:iznosSPdv", NS).text == "468.00"
    assert len(r.findall("efis:StavkaERacuna", NS)) == 2


def test_obveznik_pdv_a_raspodjela_po_stopama():
    racun = dict(RACUN_DB, u_sustavu_pdv=1)
    stavke = [
        {"naziv": "A", "kolicina": 1, "jedinica": "kom", "cijena_cent": 10000,
         "pdv_stopa": 25, "popust": 0},
        {"naziv": "B", "kolicina": 2, "jedinica": "kom", "cijena_cent": 5000,
         "pdv_stopa": 13, "popust": 0},
        {"naziv": "C", "kolicina": 1, "jedinica": "kom", "cijena_cent": 1000,
         "pdv_stopa": 0, "popust": 0},
    ]
    e = ef.eracun_iz_racuna(racun, stavke, {})
    assert e["ukupno"]["bez_pdv"] == Decimal("210.00")
    assert e["ukupno"]["pdv"] == Decimal("38.00")              # 25 + 13
    assert e["ukupno"]["s_pdv"] == Decimal("248.00")
    kat = {(r["kategorija"], r["stopa"]): r for r in e["raspodjela_pdv"]}
    assert kat[("S", Decimal(25))]["iznos"] == Decimal("25.00")
    assert kat[("S", Decimal(13))]["oporezivi"] == Decimal("100.00")
    assert ("Z", Decimal(0)) in kat
    assert kat[("S", Decimal(25))]["hr_oznaka"] == "HR:PDV25"
    # zbroj raspodjele se slaže s ukupnim iznosima
    assert sum(r["oporezivi"] for r in e["raspodjela_pdv"]) == e["ukupno"]["bez_pdv"]
    assert sum(r["iznos"] for r in e["raspodjela_pdv"]) == e["ukupno"]["pdv"]
    ef.gradi_evidentiraj_eracun([e])


def test_kupac_bez_oib_a_ne_prolazi_kao_eracun():
    e = ef.eracun_iz_racuna(dict(RACUN_DB, oib_kupca=""), STAVKE_DB, {})
    with pytest.raises(ERacunGreska, match="OIB primatelja"):
        ef.gradi_evidentiraj_eracun([e])


# ── provjera protiv službenog XSD-a (fisk2_schema/) ──────────────────────

XSD = __import__("os").path.join(__import__("os").path.dirname(__file__), "..",
                                 "fisk2_schema", "eFiskalizacijaSchema.xsd")


@pytest.fixture
def xsd():
    import os
    if not os.path.exists(XSD):
        pytest.skip("nema fisk2_schema/")
    return etree.XMLSchema(etree.parse(XSD))


@pytest.mark.parametrize("u_pdv", [0, 1])
def test_zahtjev_je_valjan_prema_xsd_u(xsd, ef_klijent, u_pdv):
    racun = dict(RACUN_DB, u_sustavu_pdv=u_pdv, datum="30.03.2027")
    stavke = [dict(s, pdv_stopa=25 if u_pdv else 0) for s in STAVKE_DB]
    e = ef.eracun_iz_racuna(racun, stavke, {}, kpd="62.01.11",
                            tekst_oslobodenja="Oslobođeno PDV-a temeljem članka 90.")
    k = ef.gradi_evidentiraj_eracun([e])
    ef.potpisi_xades_b(k, ef_klijent.private_key, ef_klijent.certificate)
    assert xsd.validate(k), xsd.error_log.last_error


def test_klijent_s_xsd_om_prihvaca_valjan_i_odbija_nevaljan(testni_certifikat, tmp_path):
    import os
    if not os.path.exists(XSD):
        pytest.skip("nema fisk2_schema/")
    kl = EFiskalizacija(testni_certifikat["cert"], testni_certifikat["key"], demo=True,
                        arhiva_mapa=str(tmp_path), xsd_path=XSD)
    e = ef.eracun_iz_racuna(dict(RACUN_DB, datum="30.03.2027"), STAVKE_DB, {})
    kl.pripremi(ef.gradi_evidentiraj_eracun([e]))          # ne baca
    e["stavke"][0]["naziv"] = ""                            # prazno -> lokalna provjera
    with pytest.raises(ERacunGreska):
        kl.pripremi(ef.gradi_evidentiraj_eracun([e]))


# ── eIzvještavanje: provjera protiv službenog XSD-a ──────────────────────

XSD_EIZ = __import__("os").path.join(__import__("os").path.dirname(__file__), "..",
                                     "fisk2_schema", "eIzvjestavanjeSchema.xsd")


@pytest.fixture
def xsd_eiz():
    import os
    if not os.path.exists(XSD_EIZ):
        pytest.skip("nema fisk2_schema/")
    return etree.XMLSchema(etree.parse(XSD_EIZ))


def _naplata(**kw):
    return dict(dict(broj_dokumenta="17/PP1/1", datum_izdavanja="30.03.2027",
                     oib_izdavatelja="02994650199", oib_primatelja="12345678903",
                     datum_naplate="14.04.2027", iznos="450.00",
                     nacin_placanja="T"), **kw)


def _odbijanje(**kw):
    return dict(dict(broj_dokumenta="17/PP1/1", datum_izdavanja="30.03.2027",
                     oib_izdavatelja="02994650199", oib_primatelja="12345678903",
                     datum_odbijanja="15.04.2027", vrsta_razloga="N",
                     razlog="Neispravan iznos"), **kw)


@pytest.mark.parametrize("nacin", ["T", "O", "Z"])
def test_naplata_valjana_prema_xsd_u(xsd_eiz, ef_klijent, nacin):
    k = ef.gradi_naplatu([_naplata(nacin_placanja=nacin), _naplata(iznos="-10.00")])
    ef.potpisi_xades_b(k, ef_klijent.private_key, ef_klijent.certificate)
    assert xsd_eiz.validate(k), xsd_eiz.error_log.last_error


@pytest.mark.parametrize("vrsta", ["N", "U", "O"])
def test_odbijanje_valjano_prema_xsd_u(xsd_eiz, ef_klijent, vrsta):
    k = ef.gradi_odbijanje([_odbijanje(vrsta_razloga=vrsta)])
    ef.potpisi_xades_b(k, ef_klijent.private_key, ef_klijent.certificate)
    assert xsd_eiz.validate(k), xsd_eiz.error_log.last_error


@pytest.mark.parametrize("oib", [None, "02994650199"])
def test_ovlastenja_valjana_prema_xsd_u(xsd_eiz, ef_klijent, oib):
    k = ef.gradi_ovlastenja(oib)
    ef.potpisi_xades_b(k, ef_klijent.private_key, ef_klijent.certificate)
    assert xsd_eiz.validate(k), xsd_eiz.error_log.last_error


@pytest.mark.parametrize("u_pdv", [0, 1])
def test_isporuka_bez_eracuna_valjana_prema_xsd_u(xsd_eiz, ef_klijent, u_pdv):
    racun = dict(RACUN_DB, u_sustavu_pdv=u_pdv, datum="30.03.2027")
    stavke = [dict(s, pdv_stopa=25 if u_pdv else 0) for s in STAVKE_DB]
    e = ef.eracun_iz_racuna(racun, stavke, {}, kpd="62.01.11",
                            tekst_oslobodenja="Oslobođeno PDV-a temeljem članka 90.")
    k = ef.gradi_isporuku_bez_eracuna([e])
    ef.potpisi_xades_b(k, ef_klijent.private_key, ef_klijent.certificate)
    assert xsd_eiz.validate(k), xsd_eiz.error_log.last_error
    assert k.find(f"{{{ef.NS_EIZ}}}Zaglavlje/{{{ef.NS_EIZ}}}vrstaRacuna").text == "IR"


def test_odgovori_iz_sluzbenih_primjera_se_parsiraju():
    """Primjeri iz dokumentacije koriste krivi prefiks/namespace (eizjestavanje);
    parser mora raditi neovisno o njemu."""
    ok = (b'<e:Envelope xmlns:e="http://schemas.xmlsoap.org/soap/envelope/" '
          b'xmlns:eiz="http://www.porezna-uprava.gov.hr/fin/2024/types/eizjestavanje">'
          b'<e:Body><eiz:EvidentirajNaplatuOdgovor eiz:id="x"><eiz:datumVrijemeSlanja>'
          b'2024-06-20T09:15:43+02:00</eiz:datumVrijemeSlanja><eiz:Odgovor>'
          b'<eiz:idZahtjeva>3ac87da8-8056-40ec-9e68-2a0cfcc086b3</eiz:idZahtjeva>'
          b'<eiz:prihvacenZahtjev>true</eiz:prihvacenZahtjev></eiz:Odgovor>'
          b'</eiz:EvidentirajNaplatuOdgovor></e:Body></e:Envelope>')
    r = ef.parsiraj_odgovor(ok)
    assert r["prihvacen"] and r["id_zahtjeva"].startswith("3ac87da8")
    bad = ok.replace(b"true</eiz:prihvacenZahtjev>",
                     "false</eiz:prihvacenZahtjev><eiz:greska><eiz:sifra>S009</eiz:sifra>"
                     "<eiz:redniBrojZapisa>1</eiz:redniBrojZapisa><eiz:opis>Ne postoji "
                     "evidentirana fiskalizacija eRačuna.</eiz:opis></eiz:greska>".encode())
    r = ef.parsiraj_odgovor(bad)
    assert not r["prihvacen"] and r["greske"][0]["sifra"] == "S009"


def test_klijent_bira_xsd_prema_namespaceu_poruke(testni_certifikat, tmp_path):
    import os
    if not os.path.exists(XSD) or not os.path.exists(XSD_EIZ):
        pytest.skip("nema fisk2_schema/")
    kl = EFiskalizacija(testni_certifikat["cert"], testni_certifikat["key"], demo=True,
                        arhiva_mapa=str(tmp_path), xsd_path=XSD)
    kl.pripremi(ef.gradi_naplatu([_naplata()]))           # ne baca
    kl.pripremi(ef.gradi_odbijanje([_odbijanje()]))
    kl.pripremi(ef.gradi_ovlastenja())
