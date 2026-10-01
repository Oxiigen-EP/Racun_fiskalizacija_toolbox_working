"""Iznosi u bazi su cijeli brojevi centi (stupci *_cent)."""
import os
import sqlite3

import pytest

import db
from novac import u_centima, iz_centi


def test_pretvorba_centi_je_tocna():
    assert u_centima(19.99) == 1999
    assert u_centima(0.1 + 0.2) == 30          # float 0.30000000000000004
    assert u_centima(-100.5) == -10050
    assert u_centima(1.005) == 101             # half-up, ne float pogreška
    assert iz_centi(1999) == 19.99
    assert iz_centi(None) == 0.0
    assert iz_centi(-10050) == -100.5
    for euri in (0.01, 0.07, 1234.56, 59999.99):
        assert iz_centi(u_centima(euri)) == euri


def _stara_baza(putanja):
    """Baza u starom obliku: iznosi kao REAL u eurima."""
    c = sqlite3.connect(putanja)
    c.executescript("""
        CREATE TABLE invoices (id INTEGER PRIMARY KEY AUTOINCREMENT,
            datum TEXT, ukupan_iznos REAL, nacin_placanja TEXT);
        CREATE TABLE invoice_stavke (id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER, naziv TEXT, kolicina REAL, jedinica TEXT,
            cijena REAL, pdv_stopa REAL, popust REAL DEFAULT 0,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id));
        CREATE TABLE kpr (id INTEGER PRIMARY KEY AUTOINCREMENT,
            godina INTEGER NOT NULL, redni_broj INTEGER NOT NULL,
            datum TEXT NOT NULL, broj_temeljnice TEXT, opis TEXT,
            gotovina REAL DEFAULT 0, virmanski REAL DEFAULT 0,
            invoice_id INTEGER, rucno_dodano INTEGER DEFAULT 0,
            UNIQUE(godina, redni_broj));
        CREATE TABLE ponude (id INTEGER PRIMARY KEY AUTOINCREMENT,
            broj_ponude TEXT, ukupan_iznos REAL DEFAULT 0.0);
        CREATE TABLE ponuda_stavke (id INTEGER PRIMARY KEY AUTOINCREMENT,
            ponuda_id INTEGER, naziv TEXT, kolicina REAL, jedinica TEXT,
            cijena REAL, pdv_stopa REAL DEFAULT 0);
        INSERT INTO invoices (datum, ukupan_iznos) VALUES ('01.01.2026', 67.46);
        INSERT INTO invoices (datum, ukupan_iznos) VALUES ('02.01.2026', -100.5);
        INSERT INTO invoices (datum, ukupan_iznos) VALUES ('03.01.2026', NULL);
        INSERT INTO invoice_stavke (invoice_id, naziv, kolicina, cijena)
            VALUES (1, 'A', 3, 19.99);
        INSERT INTO kpr (godina, redni_broj, datum, gotovina, virmanski)
            VALUES (2026, 1, '01.01.2026', 0, 59.97);
        INSERT INTO ponude (broj_ponude, ukupan_iznos) VALUES ('P1', 1234.56);
        INSERT INTO ponuda_stavke (ponuda_id, naziv, cijena) VALUES (1, 'B', 0.07);
    """)
    c.commit()
    c.close()


def test_migracija_pretvara_sve_iznose_u_cente(tmp_path):
    p = str(tmp_path / "stara.db")
    _stara_baza(p)
    conn, cur = db.otvori_bazu(p)

    assert os.path.exists(p + ".pred-centi.bak")
    assert [r[0] for r in cur.execute(
        "SELECT ukupan_iznos_cent FROM invoices ORDER BY id")] == [6746, -10050, 0]
    assert cur.execute("SELECT cijena_cent FROM invoice_stavke").fetchone()[0] == 1999
    assert cur.execute("SELECT gotovina_cent, virmanski_cent FROM kpr"
                       ).fetchone() == (0, 5997)
    assert cur.execute("SELECT ukupan_iznos_cent FROM ponude").fetchone()[0] == 123456
    assert cur.execute("SELECT cijena_cent FROM ponuda_stavke").fetchone()[0] == 7
    # stvarno cijeli brojevi, ne REAL
    assert cur.execute("SELECT typeof(ukupan_iznos_cent) FROM invoices WHERE id=1"
                       ).fetchone()[0] == "integer"
    # stari stupci su nestali (zaboravljen upit javlja grešku)
    with pytest.raises(sqlite3.OperationalError):
        cur.execute("SELECT ukupan_iznos FROM invoices")


def test_migracija_je_jednokratna_i_ne_dira_podatke(tmp_path):
    p = str(tmp_path / "stara.db")
    _stara_baza(p)
    conn, cur = db.otvori_bazu(p)
    conn.close()
    conn, cur = db.otvori_bazu(p)      # drugi put: ništa se ne mijenja
    assert cur.execute("SELECT ukupan_iznos_cent FROM invoices WHERE id=1"
                       ).fetchone()[0] == 6746


def test_nova_baza_odmah_ima_cent_stupce(tmp_path):
    conn, cur = db.otvori_bazu(str(tmp_path / "nova.db"))
    for tablica, stupac in [("invoices", "ukupan_iznos_cent"),
                            ("invoice_stavke", "cijena_cent"),
                            ("kpr", "gotovina_cent"), ("kpr", "virmanski_cent"),
                            ("ponude", "ukupan_iznos_cent"),
                            ("ponuda_stavke", "cijena_cent")]:
        assert stupac in {r[1] for r in cur.execute(f"PRAGMA table_info({tablica})")}
    assert not os.path.exists(str(tmp_path / "nova.db.pred-centi.bak"))


def test_kpr_upis_i_naplata_u_centima(tmp_path):
    from kpr_module import oznaci_naplaceno
    conn, cur = db.otvori_bazu(str(tmp_path / "t.db"))
    cur.execute("INSERT INTO invoices (broj_racuna, nacin_placanja, ukupan_iznos_cent)"
                " VALUES ('1/PP1/1', 'T', 6746)")
    conn.commit()
    ok, poruka = oznaci_naplaceno(conn, cur, 1, "05.01.2026")
    assert ok, poruka
    assert cur.execute("SELECT gotovina_cent, virmanski_cent FROM kpr"
                       ).fetchone() == (0, 6746)       # T -> virmanski
