import os
import sys
from datetime import date
from decimal import Decimal

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pdv

OBV = {"naziv": "Remi vl. Emir Pretnar", "oib": "91424659795",
       "adresa": "Jezerski put 8, 42240 Ivanec"}
SHEME = os.environ.get("EPOREZNA_SHEME", pdv.ZADANA_MAPA_SHEMA)
ima_sheme = os.path.isfile(os.path.join(SHEME, "PDV", "ObrazacPDV-v11-0.xsd"))
try:
    import lxml  # noqa: F401
    ima_lxml = True
except ImportError:
    ima_lxml = False
treba_xsd = pytest.mark.skipif(not (ima_sheme and ima_lxml), reason="nema XSD-a ili lxml")


def test_razdoblje_i_rok():
    assert pdv.razdoblje_mjeseca("2026-08") == (date(2026, 8, 1), date(2026, 8, 31))
    assert pdv.razdoblje_mjeseca("2026-12")[1] == date(2026, 12, 31)
    assert pdv.razdoblje_mjeseca("2028-02")[1] == date(2028, 2, 29)
    assert pdv.rok_predaje(date(2026, 8, 31)) == date(2026, 9, 30)
    assert pdv.rok_predaje(date(2026, 12, 31)) == date(2027, 1, 31)
    with pytest.raises(pdv.PdvGreska):
        pdv.razdoblje_mjeseca("kriv")


def test_adresa():
    assert pdv.parsiraj_adresu("Jezerski put 8, 42240 Ivanec") == ("Ivanec", "Jezerski put", "8")
    with pytest.raises(pdv.PdvGreska):
        pdv.parsiraj_adresu("bez zareza")


def test_izracun():
    r = pdv.izracun(5263)
    assert r["porez"] == Decimal("13.16") and r["obveza"] == Decimal("13.16")
    r = pdv.izracun(5263, pretporez=True)
    assert r["pretporez"] == Decimal("13.16") and r["obveza"] == Decimal("0.00")


def test_pdvid_ciscenje():
    assert pdv.ocisti_pdvid("IE 3206488lh", "IE") == "3206488LH"
    assert pdv.ocisti_pdvid("3206488LH", "IE") == "3206488LH"


def test_greske_unosa():
    with pytest.raises(pdv.PdvGreska):
        pdv.xml_pdv(OBV, "2026-08", 100, "12", "Emir Pretnar")
    with pytest.raises(pdv.PdvGreska):
        pdv.xml_pdv(OBV, "2026-08", 100, "1234", "Emir")
    with pytest.raises(pdv.PdvGreska):
        pdv.xml_pdvs(OBV, "2026-08", 100, "1234", "Emir Pretnar", "")
    with pytest.raises(pdv.PdvGreska):
        pdv.xml_pdvs(OBV, "2026-08", 0, "1234", "Emir Pretnar", "IE123")


def test_sadrzaj_pdv():
    x = pdv.xml_pdv(OBV, "2026-09", 5263, "1234", "Emir Pretnar")
    assert "<Podatak400>13.16</Podatak400>" in x
    assert "<Podatak630>0.00</Podatak630>" in x
    assert "<DatumDo>2026-09-30</DatumDo>" in x
    assert "<Podatak310>\n            <Vrijednost>0.00" in x


@treba_xsd
@pytest.mark.parametrize("pretporez", [False, True])
@pytest.mark.parametrize("osn", [0, 1, 3075, 5263, 123456789])
def test_pdv_xsd(osn, pretporez):
    x = pdv.xml_pdv(OBV, "2026-08", osn, "1234", "Emir Pretnar", pretporez=pretporez)
    assert pdv.validiraj(x, "PDV", SHEME) == ("ok", [])


@treba_xsd
def test_pdvs_xsd():
    x = pdv.xml_pdvs(OBV, "2026-08", 3075, "1234", "Emir Pretnar", "IE3206488LH")
    assert pdv.validiraj(x, "PDV-S", SHEME) == ("ok", [])


@treba_xsd
def test_validacija_hvata_gresku():
    x = pdv.xml_pdv(OBV, "2026-08", 3075, "1234", "Emir Pretnar").replace(
        "<Ispostava>1234", "<Ispostava>12")
    st, g = pdv.validiraj(x, "PDV", SHEME)
    assert st == "greske" and g


def test_validacija_bez_sheme(tmp_path):
    assert pdv.validiraj("<a/>", "PDV", str(tmp_path))[0] == "preskoceno"


# ── usporedba sa stvarnim obrascima preuzetima iz ePorezne ───────────────────
def _tijelo(xml):
    import re
    t = re.search(r"<Tijelo>(.*)</Tijelo>", xml, re.S).group(1)
    return re.sub(r"\s+(?=<)", "", t).strip()


def _zaglavlje_bez_razmaka(xml):
    import re
    z = re.search(r"<Zaglavlje>(.*)</Zaglavlje>", xml, re.S).group(1)
    return re.sub(r"\s+(?=<)", "", z).strip()


PRIM = os.path.join(os.path.dirname(os.path.abspath(__file__)), "primjeri")
OBV_FO = {"naziv": "Remi vl. Emir Pretnar", "ime": "EMIR", "prezime": "PRETNAR",
          "oib": "91424659795", "adresa": "Ulica Jezerski put 8, 42240 Ivanec"}


def test_pdv_jednak_primjeru_iz_eporezne():
    ref = open(os.path.join(PRIM, "PDV_2026-08.xml"), encoding="utf-8").read()
    x = pdv.xml_pdv(OBV_FO, "2026-08", 3075, "3532", "EMIR PRETNAR")
    assert _tijelo(x) == _tijelo(ref)
    assert _zaglavlje_bez_razmaka(x) == _zaglavlje_bez_razmaka(ref)


def test_pdvs_jednak_primjeru_iz_eporezne():
    ref = open(os.path.join(PRIM, "PDVS_2026-08.xml"), encoding="utf-8").read()
    x = pdv.xml_pdvs(OBV_FO, "2026-08", 3075, "3532", "EMIR PRETNAR", "IE9777587C")
    assert _tijelo(x) == _tijelo(ref)


def test_evidencija_generiranih():
    pytest.importorskip("PySide6")
    from pdv_module import generirano
    cfg = {"pdv_generirano": {"2026-08": {"PDV": "20.10.2026. 13:20"}}}
    assert generirano(cfg, "2026-08") == {"PDV": "20.10.2026. 13:20"}
    assert generirano(cfg, "2026-09") == {}
    assert generirano({}, "2026-08") == {}
