"""Sigurnosna kopija baze i arhiva poruka fiskalizacije.

Evidenciju računa treba čuvati 11 godina. Zato:
  * backup/billing_YYYY-MM-DD.db  - snimka baze (jedna po danu, zadnje stanje tog dana)
  * arhiva/GODINA/BROJ-PP-NU/     - svaki poslani zahtjev i primljeni odgovor CIS-a
Ništa od ovoga ne smije srušiti aplikaciju: greške se samo ispišu.
"""
import os
import re
import sqlite3
from datetime import datetime, timedelta


def _siguran_naziv(tekst: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "-", str(tekst)).strip("-") or "racun"


def napravi_backup(conn, mapa: str, zadrzi_dnevnih: int = 30):
    """Snima konzistentnu kopiju baze u mapa/billing_YYYY-MM-DD.db.
    Dnevne kopije starije od `zadrzi_dnevnih` dana brišu se, osim onih
    od 1. u mjesecu koje ostaju trajno. Vraća putanju ili None."""
    try:
        mapa = os.path.abspath(mapa)
        os.makedirs(mapa, exist_ok=True)
        danas = datetime.now()
        odrediste = os.path.join(mapa, f"billing_{danas:%Y-%m-%d}.db")
        privremena = odrediste + ".tmp"
        if os.path.exists(privremena):
            os.remove(privremena)
        dst = sqlite3.connect(privremena)
        try:
            conn.backup(dst)
        finally:
            dst.close()
        provjera = sqlite3.connect(privremena)
        try:
            rez = provjera.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            provjera.close()
        if rez != "ok":
            os.remove(privremena)
            print(f"⚠️  Backup baze odbačen, provjera integriteta: {rez}")
            return None
        os.replace(privremena, odrediste)
        _ocisti_stare(mapa, zadrzi_dnevnih)
        return odrediste
    except Exception as e:
        print(f"⚠️  Backup baze nije uspio: {e}")
        return None


def _ocisti_stare(mapa: str, zadrzi_dnevnih: int):
    granica = datetime.now() - timedelta(days=zadrzi_dnevnih)
    for ime in os.listdir(mapa):
        m = re.fullmatch(r"billing_(\d{4})-(\d{2})-(\d{2})\.db", ime)
        if not m:
            continue
        god, mj, dan = map(int, m.groups())
        if dan == 1:
            continue                      # mjesečne kopije se čuvaju
        try:
            if datetime(god, mj, dan) < granica:
                os.remove(os.path.join(mapa, ime))
        except Exception:
            pass


def spremi_poruku(osnovna_mapa: str, godina, oznaka_racuna: str,
                  vrsta: str, sadrzaj, ekstenzija: str = "xml"):
    """Sprema poruku (zahtjev, odgovor, greška) uz račun. Vraća putanju ili None.
    Svaki pokušaj slanja daje nove datoteke, ništa se ne prepisuje."""
    try:
        mapa = os.path.join(os.path.abspath(osnovna_mapa), "arhiva",
                            str(godina), _siguran_naziv(oznaka_racuna))
        os.makedirs(mapa, exist_ok=True)
        osnova = f"{datetime.now():%Y%m%d_%H%M%S}_{_siguran_naziv(vrsta)}"
        putanja = os.path.join(mapa, f"{osnova}.{ekstenzija}")
        n = 1
        while os.path.exists(putanja):
            n += 1
            putanja = os.path.join(mapa, f"{osnova}_{n}.{ekstenzija}")
        if isinstance(sadrzaj, str):
            sadrzaj = sadrzaj.encode("utf-8")
        with open(putanja, "wb") as f:
            f.write(sadrzaj)
        return putanja
    except Exception as e:
        print(f"⚠️  Arhiviranje poruke nije uspjelo: {e}")
        return None
