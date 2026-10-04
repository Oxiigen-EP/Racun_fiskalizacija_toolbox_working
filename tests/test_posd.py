"""PO-SD: razredi, obračun iz KPR-a, provjere, PDF i arhivski paket."""
import os
import zipfile
from datetime import date

import pytest

import db
import posd


@pytest.fixture
def baza(tmp_path):
    conn, cur = db.otvori_bazu(str(tmp_path / "t.db"))
    return conn


def racun(conn, broj, datum, iznos, placeno=1, datum_naplate=None,
          fisk=1, pravna=0, rok=None):
    c = conn.execute(
        "INSERT INTO invoices (broj_racuna, datum, rok_placanja, "
        "ukupan_iznos_cent, placeno, datum_naplate, fiskaliziran, pravna_osoba)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (broj, datum, rok, iznos, placeno, datum_naplate, fisk, pravna))
    conn.commit()
    return c.lastrowid


def kpr(conn, godina, rb, got=0, vir=0, invoice_id=None, rucno=0,
        datum="01.02.2026"):
    conn.execute(
        "INSERT INTO kpr (godina, redni_broj, datum, gotovina_cent, "
        "virmanski_cent, invoice_id, rucno_dodano) VALUES (?,?,?,?,?,?,?)",
        (godina, rb, datum, got, vir, invoice_id, rucno))
    conn.commit()


def naslovi(rez):
    return [n["naslov"] for n in rez["provjere"]]


# ── razredi ──────────────────────────────────────────────────────────────

def test_razredi_2026_slijede_pravilo_1_8_posto(baza):
    lista, g = posd.razredi(baza, 2026)
    assert g == 2026 and len(lista) == 7
    for r in lista:
        assert r["porez"] == r["do"] * 18 // 1000       # gornja granica × 1,8 %
        assert r["porez"] % 4 == 0                      # djeljivo na kvartale
    assert lista[1]["porez"] == 27540                   # razred 2: 275,40 EUR
    assert lista[-1]["do"] == 6000000                   # limit 60.000 EUR


def test_granice_razreda(baza):
    lista, _ = posd.razredi(baza, 2026)
    r = lambda c: (posd.odredi_razred(lista, c) or {}).get("razred")
    assert r(0) == 1
    assert r(1130000) == 1
    assert r(1130001) == 2
    assert r(6000000) == 7
    assert r(6000001) is None


def test_ponovno_otvaranje_ne_duplira_razrede(tmp_path):
    p = str(tmp_path / "t.db")
    conn, _ = db.otvori_bazu(p)
    conn.close()
    conn, _ = db.otvori_bazu(p)
    assert conn.execute("SELECT COUNT(*) FROM porezni_razredi").fetchone()[0] == 7


# ── obračun ──────────────────────────────────────────────────────────────

def test_obracun_iz_kpr_a(baza):
    kpr(baza, 2026, 1, got=300000)                  # 3.000,00 gotovina/kartice
    kpr(baza, 2026, 2, vir=1000000)                 # 10.000,00 virmanski
    kpr(baza, 2025, 1, vir=999999)                  # druga godina se ne broji
    posd.spremi_predujam(baza, 2026, 1, 5085)
    posd.spremi_predujam(baza, 2026, 2, 5085)
    r = posd.izracunaj(baza, 2026)
    assert (r["gotovina"], r["bezgotovinski"], r["ukupno"]) == (300000, 1000000, 1300000)
    assert r["razred"] == 2                          # 13.000 EUR
    assert r["porez"] == 27540 and r["kvartalni_predujam"] == 6885
    assert r["uplaceno"] == 10170
    assert r["razlika"] == 27540 - 10170            # za uplatu


def test_povrat_kad_je_uplaceno_vise(baza):
    kpr(baza, 2026, 1, got=100000)                  # razred 1, porez 203,40
    for q in (1, 2, 3, 4):
        posd.spremi_predujam(baza, 2026, q, 6000)
    r = posd.izracunaj(baza, 2026)
    assert r["razlika"] == 20340 - 24000 < 0


def test_predujam_se_prepisuje_a_ne_dupla(baza):
    posd.spremi_predujam(baza, 2026, 3, 100)
    posd.spremi_predujam(baza, 2026, 3, 250)
    assert posd.predujmovi(baza, 2026)[3] == 250
    with pytest.raises(ValueError):
        posd.spremi_predujam(baza, 2026, 5, 1)


def test_prazna_godina(baza):
    r = posd.izracunaj(baza, 2026)
    assert r["ukupno"] == 0 and r["razred"] == 1 and r["status_limita"] == "u redu"


def test_limit_80_95_i_prekoracenje(baza):
    kpr(baza, 2026, 1, vir=4800000)                 # 80 %
    assert posd.izracunaj(baza, 2026)["status_limita"] == "iznad 80 %"
    kpr(baza, 2026, 2, vir=900000)                  # 95 %
    assert posd.izracunaj(baza, 2026)["status_limita"] == "iznad 95 %"
    kpr(baza, 2026, 3, vir=400000)                  # 61.000
    r = posd.izracunaj(baza, 2026)
    assert r["status_limita"] == "prekoračen" and r["razred"] is None
    assert any(n["razina"] == "greska" and "limit" in n["naslov"]
               for n in r["provjere"])


def test_godina_bez_razreda_koristi_raniju_uz_upozorenje(baza):
    r = posd.izracunaj(baza, 2027)
    assert r["godina_razreda"] == 2026
    assert any("Razredi za 2027" in n for n in naslovi(r))


# ── provjere ─────────────────────────────────────────────────────────────

def test_nefiskalizirani_racun_je_greska_ali_ne_pravna_osoba(baza):
    racun(baza, "5/PP1/1", "10.03.2026", 1000, fisk=0)
    racun(baza, "6/PP1/1", "10.03.2026", 1000, fisk=0, pravna=1)
    r = posd.izracunaj(baza, 2026)
    nalaz = [n for n in r["provjere"] if "JIR" in n["naslov"]]
    assert len(nalaz) == 1 and nalaz[0]["razina"] == "greska"
    assert "5/PP1/1" in nalaz[0]["detalj"] and "6/PP1/1" not in nalaz[0]["detalj"]


def test_neplaceni_i_dospjeli(baza):
    racun(baza, "1/PP1/1", "10.01.2026", 5000, placeno=0, rok="25.01.2026")
    racun(baza, "2/PP1/1", "10.01.2026", 5000, placeno=0, rok="25.12.2030")
    r = posd.izracunaj(baza, 2026, danas=date(2026, 6, 1))
    n = [x for x in r["provjere"] if "naplaćeno" in x["naslov"]][0]
    assert n["razina"] == "upozorenje" and "1/PP1/1" in n["detalj"]
    assert "100,00 EUR" in n["detalj"]


def test_rupe_u_rednim_brojevima(baza):
    kpr(baza, 2026, 1, got=100)
    kpr(baza, 2026, 4, got=100)
    n = [x for x in posd.izracunaj(baza, 2026)["provjere"] if "Rupe" in x["naslov"]][0]
    assert "2, 3" in n["detalj"] and n["razina"] == "upozorenje"


def test_dvostruki_unos_istog_racuna(baza):
    i = racun(baza, "1/PP1/1", "10.01.2026", 1000, datum_naplate="11.01.2026")
    kpr(baza, 2026, 1, got=1000, invoice_id=i)
    kpr(baza, 2026, 2, got=1000, invoice_id=i)
    n = [x for x in posd.izracunaj(baza, 2026)["provjere"]
         if "više puta" in x["naslov"]]
    assert n and n[0]["razina"] == "greska"


def test_zbroj_kpr_mora_odgovarati_naplacenim_racunima(baza):
    i = racun(baza, "1/PP1/1", "10.01.2026", 1000, datum_naplate="11.01.2026")
    kpr(baza, 2026, 1, got=900, invoice_id=i)       # u KPR-u manje nego račun
    r = posd.izracunaj(baza, 2026)
    assert any("ne slaže" in n for n in naslovi(r))
    # ispravno stanje: nema te greške
    baza.execute("UPDATE kpr SET gotovina_cent=1000")
    baza.commit()
    assert not any("ne slaže" in n for n in naslovi(posd.izracunaj(baza, 2026)))


def test_naplata_u_drugoj_godini_i_storno_i_rucni_unosi(baza):
    i = racun(baza, "9/PP1/1", "20.12.2025", 2000, datum_naplate="05.01.2026")
    kpr(baza, 2026, 1, vir=2000, invoice_id=i)
    kpr(baza, 2026, 2, got=-500, rucno=1)
    r = posd.izracunaj(baza, 2026)
    t = naslovi(r)
    assert any("drugoj godini" in x for x in t)
    assert any("negativnih" in x for x in t)
    assert any("ručnih" in x for x in t)
    assert r["ukupno"] == 1500                      # storno smanjuje primitke


def test_demo_nacin_je_upozorenje(baza):
    assert any("demo" in n for n in naslovi(posd.izracunaj(baza, 2026, demo=True)))
    assert not any("demo" in n for n in naslovi(posd.izracunaj(baza, 2026)))


def test_nalazi_su_poredani_po_tezini(baza):
    racun(baza, "1/PP1/1", "10.01.2026", 1000, placeno=0, fisk=0)
    razine = [n["razina"] for n in posd.izracunaj(baza, 2026)["provjere"]]
    assert razine == sorted(razine, key=posd.RAZINE.index)


# ── pomoćne funkcije i izlazi ────────────────────────────────────────────

def test_format_za_epornu():
    assert posd.cent_za_unos(123456) == "1234,56"
    assert posd.cent_za_unos(5) == "0,05"
    assert posd.cent_za_unos(-20340) == "-203,40"
    assert posd.fmt_cent(123456) == "1.234,56"


def test_podsjetnik():
    assert posd.podsjetnik(date(2026, 7, 1)) == ""
    assert "15.01.2027" in posd.podsjetnik(date(2026, 12, 5))
    assert "15.01.2027" in posd.podsjetnik(date(2027, 1, 10))
    assert posd.podsjetnik(date(2027, 1, 20)) == ""


def test_pdf_i_arhivski_paket(baza, tmp_path):
    kpr(baza, 2026, 1, got=100000, vir=50000)
    rez = posd.izracunaj(baza, 2026)
    pdf = str(tmp_path / "posd.pdf")
    posd.napravi_pdf(rez, pdf, "Obrt", "12345678903")
    assert os.path.getsize(pdf) > 1000
    arh = tmp_path / "arhiva" / "2026" / "1-PP1-1"
    arh.mkdir(parents=True)
    (arh / "zahtjev.xml").write_text("<x/>")
    zip_put = str(tmp_path / "paket.zip")
    broj = posd.arhivski_paket(baza, 2026, str(tmp_path), zip_put, pdf)
    with zipfile.ZipFile(zip_put) as z:
        imena = z.namelist()
        csv_tekst = z.read("KPR_2026.csv").decode("utf-8-sig")
    assert broj == 3 and "PO-SD_2026.pdf" in imena
    assert any(i.startswith("cis_poruke") for i in imena)
    assert "1000,00;500,00;1500,00" in csv_tekst


# ── Administracija razreda ───────────────────────────────────────────────────

def _lista_razreda():
    return [dict(razred=1, od=0, do=1000000, porez=18000),
            dict(razred=2, od=0, do=2000000, porez=36000)]


def test_spremi_razrede_uskladi_granice(baza):
    posd.spremi_razrede(baza, 2027, _lista_razreda())
    r = posd.dohvati_razrede(baza, 2027)
    assert [x["od"] for x in r] == [0, 1000001]
    assert [x["razred"] for x in r] == [1, 2]
    assert 2027 in posd.godine_razreda(baza)


def test_spremi_razrede_zamjenjuje_postojece(baza):
    posd.spremi_razrede(baza, 2027, _lista_razreda())
    posd.spremi_razrede(baza, 2027, _lista_razreda()[:1])
    assert len(posd.dohvati_razrede(baza, 2027)) == 1


def test_provjeri_razrede_greske():
    g, _ = posd.provjeri_razrede([dict(od=0, do=2000, porez=10),
                                  dict(od=0, do=1000, porez=20)])
    assert g
    g, _ = posd.provjeri_razrede([dict(od=0, do=1000, porez=-1)])
    assert g
    g, _ = posd.provjeri_razrede([])
    assert g


def test_spremi_neispravno_ne_mijenja_bazu(baza):
    prije = posd.dohvati_razrede(baza, 2026)
    with pytest.raises(ValueError):
        posd.spremi_razrede(baza, 2026, [dict(do=2000, porez=1),
                                         dict(do=1000, porez=2)])
    assert posd.dohvati_razrede(baza, 2026) == prije


def test_kopiraj_i_obrisi_razrede(baza):
    posd.kopiraj_razrede(baza, 2026, 2027)
    assert posd.dohvati_razrede(baza, 2027) == posd.dohvati_razrede(baza, 2026)
    with pytest.raises(ValueError):
        posd.kopiraj_razrede(baza, 2026, 2027)
    posd.obrisi_razrede(baza, 2027)
    assert not posd.dohvati_razrede(baza, 2027)


def test_ne_moze_obrisati_zadnju_godinu(baza):
    with pytest.raises(ValueError):
        posd.obrisi_razrede(baza, 2026)


def test_izmjena_razreda_utjece_na_obracun(baza):
    posd.spremi_razrede(baza, 2026, [dict(do=100000000, porez=4000)])
    assert posd.izracunaj(baza, 2026)["porez"] == 4000
