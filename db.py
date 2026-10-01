"""Baza podataka (SQLite): shema, migracije, sigurnosna kopija, brojači računa
i kupci. Bez ovisnosti o korisničkom sučelju."""
import json
import os
import sqlite3
from datetime import datetime

from arhiva_util import napravi_backup
from kpr_module import inicijaliziraj_kpr_tablicu, migriraj_naplatu
from ponude_module import inicijaliziraj_ponude_tablice

# Stupci dodani naknadno; stare baze ih dobiju migracijom (ALTER TABLE).
_MIGRACIJE_INVOICES = [
    "ALTER TABLE invoices ADD COLUMN pravna_osoba INTEGER DEFAULT 0",
    "ALTER TABLE invoices ADD COLUMN broj_racuna TEXT",
    "ALTER TABLE invoices ADD COLUMN operater TEXT",
    "ALTER TABLE invoices ADD COLUMN datum_kreiranja TEXT",
]
_MIGRACIJE_FISKALIZACIJA = [
    "ALTER TABLE invoices ADD COLUMN vrijeme_izdavanja TEXT",
    "ALTER TABLE invoices ADD COLUMN greska_fisk TEXT",
    "ALTER TABLE invoices ADD COLUMN pokusaja INTEGER DEFAULT 0",
]


def _pokusaj(cursor, sql):
    try:
        cursor.execute(sql)
    except sqlite3.OperationalError:
        pass            # stupac već postoji


def inicijaliziraj_shemu(conn, cursor):
    """Stvara tablice koje ne postoje i migrira stare baze."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoices (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            datum               TEXT,
            rok_placanja        TEXT,
            nacin_placanja      TEXT,
            oib_izdavatelja     TEXT,
            naziv_izdavatelja   TEXT,
            adresa_izdavatelja  TEXT,
            iban                TEXT,
            oib_kupca           TEXT,
            naziv_kupca         TEXT,
            adresa_kupca        TEXT,
            oznaka_pp           TEXT,
            oznaka_nu           TEXT,
            u_sustavu_pdv       INTEGER DEFAULT 1,
            ukupan_iznos        REAL,
            napomena            TEXT,
            jir                 TEXT,
            zki                 TEXT,
            fiskaliziran        INTEGER DEFAULT 0,
            datum_fiskalizacije TEXT,
            pravna_osoba        INTEGER DEFAULT 0,
            broj_racuna         TEXT
        )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS brojaci_racuna (
            godina      INTEGER NOT NULL,
            oznaka_pp   TEXT NOT NULL,
            oznaka_nu   TEXT NOT NULL,
            zadnji_broj INTEGER DEFAULT 0,
            PRIMARY KEY (godina, oznaka_pp, oznaka_nu)
        )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS invoice_stavke (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id  INTEGER,
            naziv       TEXT,
            kolicina    REAL,
            jedinica    TEXT,
            cijena      REAL,
            pdv_stopa   REAL,
            popust      REAL DEFAULT 0,
            FOREIGN KEY (invoice_id) REFERENCES invoices(id)
        )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kupci (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            naziv         TEXT NOT NULL,
            oib           TEXT,
            adresa        TEXT,
            pravna_osoba  INTEGER DEFAULT 0
        )""")
    conn.commit()

    for sql in _MIGRACIJE_INVOICES:
        _pokusaj(cursor, sql)
    conn.commit()

    inicijaliziraj_kpr_tablicu(conn, cursor)
    migriraj_naplatu(conn, cursor)
    for sql in _MIGRACIJE_FISKALIZACIJA:
        _pokusaj(cursor, sql)
    conn.commit()
    inicijaliziraj_ponude_tablice(conn, cursor)


# Zadnja otvorena veza; moduli sučelja (KPR, detalji računa) je dohvaćaju
# preko db.conn / db.cursor umjesto iz glavne skripte.
conn = None
cursor = None


def otvori_bazu(putanja):
    """Otvara (i po potrebi stvara/migrira) bazu. Vraća (conn, cursor)."""
    global conn, cursor
    conn = sqlite3.connect(putanja)
    cursor = conn.cursor()
    inicijaliziraj_shemu(conn, cursor)
    return conn, cursor


def backup_baze(conn, config_path, base_dir):
    """Snimka baze u backup mapu (config: backup_mapa). Jedna datoteka po
    danu, prepisuje se zadnjim stanjem tog dana."""
    mapa = ""
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            mapa = (json.load(f).get("backup_mapa") or "").strip()
    except Exception:
        pass
    putanja = napravi_backup(conn, mapa or os.path.join(base_dir, "backup"))
    if putanja:
        print(f"💾 Backup baze: {putanja}")
    return putanja


def sljedeci_broj_racuna(conn, oznaka_pp, oznaka_nu):
    """Atomarno dohvaća i inkrementira broj računa za dani PP/NU par i
    godinu. Reset na 1 svake nove godine automatski."""
    cursor = conn.cursor()
    godina = datetime.now().year
    cursor.execute("""
        INSERT OR IGNORE INTO brojaci_racuna
            (godina, oznaka_pp, oznaka_nu, zadnji_broj)
        VALUES (?, ?, ?, 0)""", (godina, oznaka_pp, oznaka_nu))
    cursor.execute("""
        UPDATE brojaci_racuna SET zadnji_broj = zadnji_broj + 1
        WHERE godina = ? AND oznaka_pp = ? AND oznaka_nu = ?""",
                   (godina, oznaka_pp, oznaka_nu))
    row = cursor.execute("""
        SELECT zadnji_broj FROM brojaci_racuna
        WHERE godina = ? AND oznaka_pp = ? AND oznaka_nu = ?""",
                         (godina, oznaka_pp, oznaka_nu)).fetchone()
    conn.commit()
    return godina, row[0]


def dohvati_kupce(conn, pretraga=""):
    """Dohvaća kupce iz baze, opcionalno filtrirane po pretrazi."""
    cursor = conn.cursor()
    if pretraga:
        return cursor.execute("""
            SELECT id, naziv, oib, adresa, pravna_osoba FROM kupci
            WHERE naziv LIKE ? OR oib LIKE ? ORDER BY naziv
        """, (f"%{pretraga}%", f"%{pretraga}%")).fetchall()
    return cursor.execute("""
        SELECT id, naziv, oib, adresa, pravna_osoba
        FROM kupci ORDER BY naziv""").fetchall()


def spremi_kupca(conn, naziv, oib, adresa, pravna_osoba):
    """Sprema novog kupca ili ažurira postojećeg po OIB-u."""
    cursor = conn.cursor()
    if oib:
        postojeci = cursor.execute(
            "SELECT id FROM kupci WHERE oib = ?", (oib,)).fetchone()
        if postojeci:
            cursor.execute("""
                UPDATE kupci SET naziv=?, adresa=?, pravna_osoba=?
                WHERE oib=?""", (naziv, adresa, int(pravna_osoba), oib))
            conn.commit()
            return postojeci[0]

    postojeci = cursor.execute("""
        SELECT id FROM kupci
        WHERE lower(trim(naziv)) = lower(trim(?))
          AND lower(trim(COALESCE(adresa, ''))) = lower(trim(?))
          AND lower(trim(COALESCE(oib, ''))) = lower(trim(?))
    """, (naziv, adresa or "", oib or "")).fetchone()
    if postojeci:
        return postojeci[0]

    cursor.execute("""
        INSERT INTO kupci (naziv, oib, adresa, pravna_osoba)
        VALUES (?, ?, ?, ?)""", (naziv, oib or None, adresa, int(pravna_osoba)))
    conn.commit()
    return cursor.lastrowid
