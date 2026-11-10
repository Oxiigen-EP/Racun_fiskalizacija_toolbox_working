"""Tok izdavanja eRačuna: račun iz baze -> eRačun -> Porezna uprava -> status u bazi.

Bez ovisnosti o Qt-u. Poziva se iz main.py kad je u postavkama uključeno
`eracun_fiskalizacija` (B2B računi izdani pravnim osobama od 1. 1. 2027.).

Napomena: ako eRačun šalješ i preko informacijskog posrednika koji fiskalizira
u tvoje ime, uključi ovu opciju samo ako on to NE radi, inače Porezna vraća
S008 (eRačun s istim identifikatorom već postoji). S008 se ovdje tretira kao
uspjeh jer znači da je račun već evidentiran."""
import os
from datetime import datetime

import eracun_fisk as ef
from novac import iz_centi
from postavke import PDV_OSLOBODENJE_TEKSTOVI

STATUS_POSLAN = "poslan"
STATUS_GRESKA = "greska"
SIFRA_VEC_EVIDENTIRAN = "S008"


def napravi_klijent(fisk, demo, config, arhiva_mapa):
    """EFiskalizacija iz već učitanog certifikata (objekta Fiskalizacija)."""
    xsd = config.get("fisk2_xsd") or os.path.join(
        arhiva_mapa, "fisk2_schema", "eFiskalizacijaSchema.xsd")
    return ef.EFiskalizacija(
        fisk.cert_path, fisk.key_path, key_password=fisk.key_password,
        demo=demo, arhiva_mapa=arhiva_mapa,
        ca_path=config.get("eracun_ca") or None,
        url=(config.get("eracun_url") or "").strip() or None,
        xsd_path=xsd)


def _redovi(cursor, upit, param):
    cursor.execute(upit, param)
    stupci = [d[0] for d in cursor.description]
    return [dict(zip(stupci, r)) for r in cursor.fetchall()]


def ucitaj_racun(cursor, invoice_id):
    """(racun, stavke) kao rječnici ili (None, [])."""
    racuni = _redovi(cursor, "SELECT * FROM invoices WHERE id=?", (invoice_id,))
    if not racuni:
        return None, []
    stavke = _redovi(
        cursor, "SELECT naziv, kolicina, jedinica, cijena_cent, pdv_stopa, popust "
                "FROM invoice_stavke WHERE invoice_id=? ORDER BY id", (invoice_id,))
    return racuni[0], stavke


def posalji_eracun(conn, invoice_id, klijent, config, tekst_oslobodenja=None):
    """Šalje račun kao izlazni eRačun i zapisuje status. Vraća (uspjeh, poruka)."""
    cursor = conn.cursor()
    racun, stavke = ucitaj_racun(cursor, invoice_id)
    if racun is None:
        return False, "Račun nije pronađen."
    if racun.get("eracun_status") == STATUS_POSLAN:
        return False, "eRačun je već evidentiran u Poreznoj upravi."
    if racun.get("fiskaliziran"):
        return False, "Račun je fiskaliziran kao račun u krajnjoj potrošnji."
    if not stavke:
        return False, "Račun nema stavki."

    tekst = None
    if not racun.get("u_sustavu_pdv"):
        tekst = (tekst_oslobodenja or config.get("eracun_tekst_oslobodenja")
                 or PDV_OSLOBODENJE_TEKSTOVI[0])
    try:
        eracun = ef.eracun_iz_racuna(
            racun, stavke, config, kpd=config.get("eracun_kpd") or None,
            tekst_oslobodenja=tekst)
        zahtjev = ef.gradi_evidentiraj_eracun([eracun], vrsta="I")
    except ef.ERacunGreska as e:
        return _zapisi(conn, invoice_id, STATUS_GRESKA, str(e), False)

    odgovor = klijent.posalji(zahtjev, oznaka=f"eracun-{eracun['broj_dokumenta']}")
    if odgovor["prihvacen"]:
        return _zapisi(conn, invoice_id, STATUS_POSLAN, None, True)
    if any(g.get("sifra") == SIFRA_VEC_EVIDENTIRAN for g in odgovor["greske"]):
        return _zapisi(conn, invoice_id, STATUS_POSLAN,
                       "Već evidentiran u Poreznoj upravi (S008)", True)
    return _zapisi(conn, invoice_id, STATUS_GRESKA,
                   ef.opis_gresaka(odgovor) or "Zahtjev nije prihvaćen.", False)


def _odgovor_u_rezultat(odgovor):
    """(uspjeh, poruka) iz odgovora servisa."""
    if odgovor["prihvacen"]:
        return True, None
    return False, ef.opis_gresaka(odgovor) or "Zahtjev nije prihvaćen."


def nacin_placanja_za_eizvjestavanje(nacin):
    """Način plaćanja računa -> šifra iz sheme (T transakcijski, Z ostalo)."""
    return "T" if (nacin or "") == "T" else "Z"


def posalji_naplatu(conn, invoice_id, klijent, config=None):
    """Evidentira naplatu poslanog eRačuna (EvidentirajNaplatu). (uspjeh, poruka)."""
    racun, _ = ucitaj_racun(conn.cursor(), invoice_id)
    if racun is None:
        return False, "Račun nije pronađen."
    if racun.get("eracun_status") != STATUS_POSLAN:
        return False, "eRačun još nije evidentiran u Poreznoj upravi."
    if not racun.get("placeno") or not racun.get("datum_naplate"):
        return False, "Račun nije označen kao naplaćen."
    if racun.get("eracun_naplata_status") == STATUS_POSLAN:
        return False, "Naplata je već evidentirana u Poreznoj upravi."
    try:
        zahtjev = ef.gradi_naplatu([{
            "broj_dokumenta": racun["broj_racuna"],
            "datum_izdavanja": racun["datum"],
            "oib_izdavatelja": racun["oib_izdavatelja"],
            "oib_primatelja": racun["oib_kupca"],
            "datum_naplate": racun["datum_naplate"],
            "iznos": iz_centi(racun["ukupan_iznos_cent"] or 0),
            "nacin_placanja": nacin_placanja_za_eizvjestavanje(
                racun.get("nacin_placanja")),
        }])
    except ef.ERacunGreska as e:
        return _zapisi_naplatu(conn, invoice_id, STATUS_GRESKA, str(e))
    odgovor = klijent.posalji(zahtjev, oznaka=f"naplata-{racun['broj_racuna']}")
    ok, poruka = _odgovor_u_rezultat(odgovor)
    return _zapisi_naplatu(conn, invoice_id,
                           STATUS_POSLAN if ok else STATUS_GRESKA, poruka)


def _zapisi_naplatu(conn, invoice_id, status, greska):
    conn.execute("UPDATE invoices SET eracun_naplata_status=?, "
                 "eracun_naplata_greska=? WHERE id=?", (status, greska, invoice_id))
    conn.commit()
    if status == STATUS_POSLAN:
        return True, "Naplata je evidentirana u Poreznoj upravi."
    return False, greska


def posalji_odbijanje(klijent, podaci):
    """Evidentira odbijanje zaprimljenog eRačuna (EvidentirajOdbijanje).
    podaci: broj_dokumenta, datum_izdavanja, oib_izdavatelja, oib_primatelja,
    datum_odbijanja, vrsta_razloga (N/U/O), razlog. (uspjeh, poruka)."""
    try:
        zahtjev = ef.gradi_odbijanje([podaci])
    except ef.ERacunGreska as e:
        return False, str(e)
    odgovor = klijent.posalji(
        zahtjev, oznaka=f"odbijanje-{podaci.get('broj_dokumenta', '')}")
    ok, poruka = _odgovor_u_rezultat(odgovor)
    return ok, ("Odbijanje je evidentirano u Poreznoj upravi." if ok else poruka)


def _zapisi(conn, invoice_id, status, greska, uspjeh):
    conn.execute(
        "UPDATE invoices SET eracun_status=?, eracun_greska=?, eracun_vrijeme=? "
        "WHERE id=?",
        (status, greska, datetime.now().isoformat(), invoice_id))
    conn.commit()
    if uspjeh:
        return True, greska or "eRačun je evidentiran u Poreznoj upravi."
    return False, greska


def opis_statusa(status, greska=None):
    """(simbol, opis) za prikaz u pregledu računa."""
    if status == STATUS_POSLAN:
        return "✅", "eRačun evidentiran u Poreznoj upravi"
    if status == STATUS_GRESKA:
        return "⚠️", f"eRačun nije evidentiran: {greska or 'nepoznata greška'}"
    return "—", "Pravna osoba: račun se ne fiskalizira"
