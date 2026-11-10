"""Testovi toka eRačuna: račun u bazi -> zahtjev -> status u bazi (bez mreže)."""
import lxml.etree as etree
import pytest

import db
import eracun_fisk as ef
import eracun_tok as tok

OIB_A = "02994650199"
OIB_B = "12345678903"
NS = {"efis": ef.NS_EFIS}


class LazniKlijent:
    """Hvata zahtjev i vraća zadani odgovor kao pravi klijent."""
    def __init__(self, odgovor):
        self.odgovor = odgovor
        self.zahtjevi = []
        self.oznake = []

    def posalji(self, korijen, oznaka="x", godina=None):
        self.zahtjevi.append(korijen)
        self.oznake.append(oznaka)
        return self.odgovor


PRIHVACEN = {"prihvacen": True, "greske": [], "id_zahtjeva": "1",
             "ovlasteni_oib": [], "http_status": 200, "fault": None}


def _greska(sifra, opis="x"):
    return {"prihvacen": False, "id_zahtjeva": "1", "ovlasteni_oib": [],
            "http_status": 200, "fault": None,
            "greske": [{"sifra": sifra, "redni_broj": 1, "opis": opis}]}


@pytest.fixture
def baza(tmp_path):
    conn, cur = db.otvori_bazu(str(tmp_path / "t.db"))
    return conn, cur


def _racun(cur, conn, **kw):
    podaci = dict(datum="30.03.2027", rok_placanja="14.04.2027", nacin_placanja="T",
                  oib_izdavatelja=OIB_A, naziv_izdavatelja="Remi vl. Emir Pretnar",
                  oib_kupca=OIB_B, naziv_kupca="Kupac d.o.o.",
                  iban="HR1210010051863000160", oznaka_pp="PP1", oznaka_nu="1",
                  u_sustavu_pdv=0, ukupan_iznos_cent=45000, pravna_osoba=1,
                  broj_racuna="17/PP1/1", fiskaliziran=0)
    podaci.update(kw)
    stupci = ", ".join(podaci)
    cur.execute(f"INSERT INTO invoices ({stupci}) VALUES "
                f"({', '.join('?' * len(podaci))})", tuple(podaci.values()))
    iid = cur.lastrowid
    cur.execute("INSERT INTO invoice_stavke (invoice_id, naziv, kolicina, jedinica, "
                "cijena_cent, pdv_stopa, popust) VALUES (?,?,?,?,?,?,?)",
                (iid, "Izrada aplikacije", 10, "sat", 4500, 0, 0))
    conn.commit()
    return iid


def _status(cur, iid):
    return cur.execute("SELECT eracun_status, eracun_greska FROM invoices WHERE id=?",
                       (iid,)).fetchone()


def test_migracija_dodaje_stupce_za_eracun(baza):
    _, cur = baza
    stupci = {r[1] for r in cur.execute("PRAGMA table_info(invoices)")}
    assert {"eracun_status", "eracun_greska", "eracun_vrijeme"} <= stupci


def test_uspjesno_slanje_postavlja_status_i_gradi_ispravan_zahtjev(baza):
    conn, cur = baza
    iid = _racun(cur, conn)
    k = LazniKlijent(PRIHVACEN)
    ok, poruka = tok.posalji_eracun(conn, iid, k, {"eracun_kpd": "62.01.11"})
    assert ok and "evidentiran" in poruka
    assert _status(cur, iid) == ("poslan", None)
    r = k.zahtjevi[0].find("efis:ERacun", NS)
    assert r.find("efis:brojDokumenta", NS).text == "17/PP1/1"
    assert r.find("efis:datumIzdavanja", NS).text == "2027-03-30"
    assert r.find("efis:Primatelj/efis:oibPorezniBroj", NS).text == OIB_B
    assert r.find("efis:DokumentUkupanIznos/efis:iznosSPdv", NS).text == "450.00"
    assert r.find("efis:RaspodjelaPdv/efis:kategorijaPdv", NS).text == "O"
    assert "članka 90" in r.find("efis:RaspodjelaPdv/efis:tekstRazlogaOslobodenja",
                                 NS).text
    assert r.find(".//efis:identifikatorKlasifikacije", NS).text == "62.01.11"
    assert k.oznake == ["eracun-17/PP1/1"]


def test_greska_porezne_se_zapisuje_i_dopusta_ponovno_slanje(baza):
    conn, cur = baza
    iid = _racun(cur, conn)
    ok, poruka = tok.posalji_eracun(conn, iid, LazniKlijent(_greska("S005", "shema")), {})
    assert not ok and "S005" in poruka
    assert _status(cur, iid)[0] == "greska"
    ok, _ = tok.posalji_eracun(conn, iid, LazniKlijent(PRIHVACEN), {})
    assert ok and _status(cur, iid) == ("poslan", None)


def test_s008_je_uspjeh_jer_je_eracun_vec_evidentiran(baza):
    conn, cur = baza
    iid = _racun(cur, conn)
    ok, _ = tok.posalji_eracun(conn, iid, LazniKlijent(_greska("S008")), {})
    assert ok and _status(cur, iid)[0] == "poslan"


def test_vec_poslan_se_ne_salje_ponovno(baza):
    conn, cur = baza
    iid = _racun(cur, conn)
    k = LazniKlijent(PRIHVACEN)
    tok.posalji_eracun(conn, iid, k, {})
    ok, poruka = tok.posalji_eracun(conn, iid, k, {})
    assert not ok and "već evidentiran" in poruka and len(k.zahtjevi) == 1


def test_kupac_bez_oiba_daje_gresku_bez_slanja(baza):
    conn, cur = baza
    iid = _racun(cur, conn, oib_kupca="")
    k = LazniKlijent(PRIHVACEN)
    ok, poruka = tok.posalji_eracun(conn, iid, k, {})
    assert not ok and "OIB primatelja" in poruka and not k.zahtjevi
    assert _status(cur, iid)[0] == "greska"


def test_fiskalizirani_racun_u_krajnjoj_potrosnji_se_ne_salje_kao_eracun(baza):
    conn, cur = baza
    iid = _racun(cur, conn, fiskaliziran=1, pravna_osoba=0)
    ok, poruka = tok.posalji_eracun(conn, iid, LazniKlijent(PRIHVACEN), {})
    assert not ok and "krajnjoj potrošnji" in poruka


def test_nepostojeci_racun(baza):
    conn, _ = baza
    ok, poruka = tok.posalji_eracun(conn, 999, LazniKlijent(PRIHVACEN), {})
    assert not ok and "nije pronađen" in poruka


def test_obveznik_pdv_a_salje_kategoriju_s(baza):
    conn, cur = baza
    iid = _racun(cur, conn, u_sustavu_pdv=1, ukupan_iznos_cent=56250)
    cur.execute("UPDATE invoice_stavke SET pdv_stopa=25 WHERE invoice_id=?", (iid,))
    conn.commit()
    k = LazniKlijent(PRIHVACEN)
    assert tok.posalji_eracun(conn, iid, k, {})[0]
    r = k.zahtjevi[0].find("efis:ERacun", NS)
    assert r.find("efis:RaspodjelaPdv/efis:kategorijaPdv", NS).text == "S"
    assert r.find("efis:DokumentUkupanIznos/efis:pdv", NS).text == "112.50"
    assert r.find("efis:DokumentUkupanIznos/efis:iznosSPdv", NS).text == "562.50"


def test_opis_statusa_za_pregled():
    assert tok.opis_statusa("poslan")[0] == "✅"
    sim, opis = tok.opis_statusa("greska", "S005: shema")
    assert sim == "⚠️" and "S005" in opis
    assert tok.opis_statusa(None)[0] == "—"


def test_napravi_klijent_iz_objekta_fiskalizacije(testni_certifikat, tmp_path):
    class Fisk:
        cert_path, key_path = testni_certifikat["cert"], testni_certifikat["key"]
        key_password = None
    k = tok.napravi_klijent(Fisk, True, {}, str(tmp_path))
    assert k.url == ef.URL_TEST
    p = tok.napravi_klijent(Fisk, False, {}, str(tmp_path))
    assert p.url == ef.URL_PROD


# ── eIzvještavanje: naplata i odbijanje ──────────────────────────────────

def _poslan_i_naplacen(conn, cur, **kw):
    iid = _racun(cur, conn, **kw)
    assert tok.posalji_eracun(conn, iid, LazniKlijent(PRIHVACEN), {})[0]
    cur.execute("UPDATE invoices SET placeno=1, datum_naplate='14.04.2027' WHERE id=?",
                (iid,))
    conn.commit()
    return iid


def _naplata_status(cur, iid):
    return cur.execute("SELECT eracun_naplata_status, eracun_naplata_greska "
                       "FROM invoices WHERE id=?", (iid,)).fetchone()


def test_migracija_dodaje_stupce_naplate(baza):
    _, cur = baza
    stupci = {r[1] for r in cur.execute("PRAGMA table_info(invoices)")}
    assert {"eracun_naplata_status", "eracun_naplata_greska"} <= stupci


def test_naplata_se_salje_i_zapisuje(baza):
    conn, cur = baza
    iid = _poslan_i_naplacen(conn, cur)
    k = LazniKlijent(PRIHVACEN)
    ok, poruka = tok.posalji_naplatu(conn, iid, k)
    assert ok and "naplata" in poruka.lower()
    assert _naplata_status(cur, iid) == ("poslan", None)
    n = k.zahtjevi[0].find("eiz:Naplata", {"eiz": ef.NS_EIZ})
    t = lambda x: n.find(f"eiz:{x}", {"eiz": ef.NS_EIZ}).text
    assert t("brojDokumenta") == "17/PP1/1" and t("datumNaplate") == "2027-04-14"
    assert t("naplaceniIznos") == "450.00" and t("nacinPlacanja") == "T"
    assert t("oibPorezniBrojPrimatelja") == OIB_B
    # drugi put se ne šalje
    ok, poruka = tok.posalji_naplatu(conn, iid, k)
    assert not ok and "već evidentirana" in poruka and len(k.zahtjevi) == 1


def test_naplata_zahtijeva_poslan_eracun_i_naplacen_racun(baza):
    conn, cur = baza
    iid = _racun(cur, conn)
    ok, poruka = tok.posalji_naplatu(conn, iid, LazniKlijent(PRIHVACEN))
    assert not ok and "eRačun još nije" in poruka
    tok.posalji_eracun(conn, iid, LazniKlijent(PRIHVACEN), {})
    ok, poruka = tok.posalji_naplatu(conn, iid, LazniKlijent(PRIHVACEN))
    assert not ok and "naplaćen" in poruka


def test_greska_naplate_se_zapisuje_i_dopusta_ponovno_slanje(baza):
    conn, cur = baza
    iid = _poslan_i_naplacen(conn, cur)
    ok, poruka = tok.posalji_naplatu(conn, iid, LazniKlijent(_greska("S009", "nema")))
    assert not ok and "S009" in poruka
    assert _naplata_status(cur, iid)[0] == "greska"
    assert tok.posalji_naplatu(conn, iid, LazniKlijent(PRIHVACEN))[0]


def test_nacin_placanja_za_eizvjestavanje():
    assert tok.nacin_placanja_za_eizvjestavanje("T") == "T"
    for n in ("G", "K", "O", None):
        assert tok.nacin_placanja_za_eizvjestavanje(n) == "Z"


def test_odbijanje_se_salje():
    k = LazniKlijent(PRIHVACEN)
    podaci = dict(broj_dokumenta="55/1/1", datum_izdavanja="30.03.2027",
                  oib_izdavatelja=OIB_B, oib_primatelja=OIB_A,
                  datum_odbijanja="15.04.2027", vrsta_razloga="U", razlog="Krivi iznos")
    ok, poruka = tok.posalji_odbijanje(k, podaci)
    assert ok and "evidentirano" in poruka
    o = k.zahtjevi[0].find("eiz:Odbijanje", {"eiz": ef.NS_EIZ})
    assert o.find("eiz:vrstaRazlogaOdbijanja", {"eiz": ef.NS_EIZ}).text == "U"
    assert k.oznake == ["odbijanje-55/1/1"]


def test_odbijanje_bez_razloga_ili_s_krivim_oibom_se_ne_salje():
    k = LazniKlijent(PRIHVACEN)
    podaci = dict(broj_dokumenta="55/1/1", datum_izdavanja="30.03.2027",
                  oib_izdavatelja=OIB_B, oib_primatelja=OIB_A,
                  datum_odbijanja="15.04.2027", vrsta_razloga="N", razlog="")
    ok, poruka = tok.posalji_odbijanje(k, podaci)
    assert not ok and "razlogOdbijanja" in poruka and not k.zahtjevi
    ok, poruka = tok.posalji_odbijanje(k, dict(podaci, razlog="x", oib_izdavatelja="123"))
    assert not ok and "OIB" in poruka and not k.zahtjevi


def test_odbijanje_greska_porezne_se_vraca():
    ok, poruka = tok.posalji_odbijanje(
        LazniKlijent(_greska("S005", "shema")),
        dict(broj_dokumenta="55/1/1", datum_izdavanja="30.03.2027",
             oib_izdavatelja=OIB_B, oib_primatelja=OIB_A,
             datum_odbijanja="15.04.2027", vrsta_razloga="O", razlog="x"))
    assert not ok and "S005" in poruka


def test_adresa_servisa_iz_postavki(testni_certifikat, tmp_path):
    class Fisk:
        cert_path, key_path = testni_certifikat["cert"], testni_certifikat["key"]
        key_password = None
    k = tok.napravi_klijent(Fisk, True, {"eracun_url": f" {ef.URL_CT_PORTAL} "},
                            str(tmp_path))
    assert k.url == ef.URL_CT_PORTAL == "https://cis.porezna-uprava.hr:8511/FiskalizacijaService"
    assert tok.napravi_klijent(Fisk, True, {"eracun_url": ""}, str(tmp_path)).url == ef.URL_TEST
