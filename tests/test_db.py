import sqlite3

import db


def test_nova_baza_ima_sve_tablice(tmp_path):
    conn, cur = db.otvori_bazu(str(tmp_path / "t.db"))
    tablice = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"invoices", "invoice_stavke", "kupci", "brojaci_racuna"} <= tablice
    stupci = {r[1] for r in cur.execute("PRAGMA table_info(invoice_stavke)")}
    assert "popust" in stupci
    inv = {r[1] for r in cur.execute("PRAGMA table_info(invoices)")}
    assert {"vrijeme_izdavanja", "greska_fisk", "pokusaja", "pravna_osoba"} <= inv


def test_otvaranje_je_idempotentno_i_cuva_podatke(tmp_path):
    p = str(tmp_path / "t.db")
    conn, cur = db.otvori_bazu(p)
    cur.execute("INSERT INTO kupci (naziv) VALUES ('A')")
    conn.commit()
    conn.close()
    conn, cur = db.otvori_bazu(p)           # drugi put: bez grešaka
    assert cur.execute("SELECT COUNT(*) FROM kupci").fetchone()[0] == 1


def test_migracija_stare_baze(tmp_path):
    p = str(tmp_path / "stara.db")
    c = sqlite3.connect(p)
    c.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY, datum TEXT, "
              "ukupan_iznos REAL)")
    c.execute("INSERT INTO invoices (datum, ukupan_iznos) VALUES ('01.01.2026', 5)")
    c.commit()
    c.close()
    conn, cur = db.otvori_bazu(p)
    stupci = {r[1] for r in cur.execute("PRAGMA table_info(invoices)")}
    assert {"pravna_osoba", "broj_racuna", "vrijeme_izdavanja"} <= stupci
    assert cur.execute("SELECT ukupan_iznos FROM invoices").fetchone()[0] == 5


def test_brojac_racuna_raste_po_paru_pp_nu(tmp_path):
    conn, _ = db.otvori_bazu(str(tmp_path / "t.db"))
    g, a = db.sljedeci_broj_racuna(conn, "PP1", "1")
    _, b = db.sljedeci_broj_racuna(conn, "PP1", "1")
    _, c = db.sljedeci_broj_racuna(conn, "PP1", "2")
    assert (a, b, c) == (1, 2, 1)


def test_kupci_bez_duplikata(tmp_path):
    conn, _ = db.otvori_bazu(str(tmp_path / "t.db"))
    id1 = db.spremi_kupca(conn, "Ana", "11111111119", "Ulica 1", False)
    id2 = db.spremi_kupca(conn, "Ana d.o.o.", "11111111119", "Ulica 1", True)
    assert id1 == id2                       # isti OIB -> ažurira
    id3 = db.spremi_kupca(conn, "Ivo", "", "Ulica 2", False)
    id4 = db.spremi_kupca(conn, " ivo ", "", "ulica 2", False)
    assert id3 == id4                       # isti naziv+adresa bez OIB-a
    assert [k[1] for k in db.dohvati_kupce(conn, "Iv")] == ["Ivo"]
    assert db.dohvati_kupce(conn)[0][4] in (0, 1)


def test_backup_baze(tmp_path):
    conn, _ = db.otvori_bazu(str(tmp_path / "t.db"))
    cfg = tmp_path / "config.json"
    cfg.write_text('{"backup_mapa": ""}')
    putanja = db.backup_baze(conn, str(cfg), str(tmp_path))
    assert putanja and (tmp_path / "backup").exists()


def test_strani_kljucevi_su_ukljuceni(tmp_path):
    import pytest
    conn, cur = db.otvori_bazu(str(tmp_path / "t.db"))
    assert cur.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        cur.execute("INSERT INTO invoice_stavke (invoice_id, naziv) "
                    "VALUES (9999, 'siroče')")


def test_indeksi_postoje_i_migracija_ih_ne_dupla(tmp_path):
    p = str(tmp_path / "t.db")
    conn, cur = db.otvori_bazu(p)
    conn.close()
    conn, cur = db.otvori_bazu(p)
    indeksi = {r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_stavke_racun", "idx_racuni_fisk", "idx_kpr_racun",
            "idx_kupci_oib", "idx_ponude_stavke"} <= indeksi
