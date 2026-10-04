"""Pripremni obračun PO-SD za paušalni obrt (bez ovisnosti o sučelju).

Aplikacija ne predaje obrazac. Ona iz KPR-a priprema brojke kojima se
ispunjava PO-SD u ePoreznoj, provjerava ih i izrađuje PDF i arhivski paket.

Svi iznosi su cijeli brojevi centi. Porezni razredi nisu u kodu nego u tablici
`porezni_razredi` (po godini), da ih se može ažurirati bez izmjene programa.
"""
import csv
import io
import os
import zipfile
from datetime import date, datetime

from format_util import fmt_iznos
from novac import iz_centi

# Razredi za 2026. (izvor: vodiči fiskalopedija.hr, fiskai.hr, mojafirma.hr).
# Godišnji porez = gornja granica razreda × 1,8 %. Vrijednosti prije svake
# godine provjeriti s Poreznom upravom ili knjigovođom.
_RAZREDI_2026 = [
    # razred, od_cent, do_cent, porez_cent
    (1, 0,        1130000, 20340),
    (2, 1130001,  1530000, 27540),
    (3, 1530001,  1990000, 35820),
    (4, 1990001,  3060000, 55080),
    (5, 3060001,  4000000, 72000),
    (6, 4000001,  5000000, 90000),
    (7, 5000001,  6000000, 108000),
]

KVARTALI_ROKOVI = {1: "31.03.", 2: "30.06.", 3: "30.09.", 4: "31.12."}
RAZINE = ("greska", "upozorenje", "info")


def inicijaliziraj_posd(conn, cursor):
    """Stvara tablice za PO-SD i upisuje razrede za 2026. ako ih nema."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS porezni_razredi (
            godina      INTEGER NOT NULL,
            razred      INTEGER NOT NULL,
            od_cent     INTEGER NOT NULL,
            do_cent     INTEGER NOT NULL,
            porez_cent  INTEGER NOT NULL,
            izvor       TEXT,
            PRIMARY KEY (godina, razred)
        )""")
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posd_predujmovi (
            godina      INTEGER NOT NULL,
            kvartal     INTEGER NOT NULL,
            iznos_cent  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (godina, kvartal)
        )""")
    ima = cursor.execute(
        "SELECT COUNT(*) FROM porezni_razredi WHERE godina=2026").fetchone()[0]
    if not ima:
        cursor.executemany(
            "INSERT INTO porezni_razredi "
            "(godina, razred, od_cent, do_cent, porez_cent, izvor) "
            "VALUES (2026, ?, ?, ?, ?, 'vodiči 2026., provjeriti')",
            _RAZREDI_2026)
    conn.commit()


# ── Formatiranje ─────────────────────────────────────────────────────────────

def fmt_cent(centi) -> str:
    """Za prikaz: 123456 -> '1.234,56'."""
    return fmt_iznos(iz_centi(centi))


def cent_za_unos(centi) -> str:
    """Za prepisivanje u ePoreznu: 123456 -> '1234,56' (bez točke za tisućice)."""
    centi = int(centi or 0)
    predznak = "-" if centi < 0 else ""
    centi = abs(centi)
    return f"{predznak}{centi // 100},{centi % 100:02d}"


# ── Razredi i obračun ────────────────────────────────────────────────────────

def razredi(conn, godina):
    """(lista razreda, godina_razreda). Ako za godinu nema razreda, uzima se
    najbliža ranija godina; ako ni nje nema, lista je prazna."""
    cur = conn.cursor()
    g = cur.execute("SELECT MAX(godina) FROM porezni_razredi WHERE godina<=?",
                    (godina,)).fetchone()[0]
    if g is None:
        return [], None
    rows = cur.execute(
        "SELECT razred, od_cent, do_cent, porez_cent FROM porezni_razredi "
        "WHERE godina=? ORDER BY razred", (g,)).fetchall()
    return [dict(razred=r[0], od=r[1], do=r[2], porez=r[3]) for r in rows], g


def odredi_razred(lista, ukupno_cent):
    """Razred za dane primitke ili None ako je prekoračena gornja granica."""
    for r in lista:
        if ukupno_cent <= r["do"]:
            return r
    return None


def predujmovi(conn, godina):
    """{kvartal: iznos_cent} za kvartale 1-4."""
    rez = {q: 0 for q in (1, 2, 3, 4)}
    for q, iznos in conn.execute(
            "SELECT kvartal, iznos_cent FROM posd_predujmovi WHERE godina=?",
            (godina,)):
        rez[q] = iznos
    return rez


def spremi_predujam(conn, godina, kvartal, iznos_cent):
    if kvartal not in (1, 2, 3, 4):
        raise ValueError("kvartal mora biti 1-4")
    conn.execute(
        "INSERT INTO posd_predujmovi (godina, kvartal, iznos_cent) "
        "VALUES (?, ?, ?) ON CONFLICT(godina, kvartal) "
        "DO UPDATE SET iznos_cent=excluded.iznos_cent",
        (godina, kvartal, int(iznos_cent)))
    conn.commit()


def izracunaj(conn, godina, demo=False, danas=None):
    """Pripremni obračun za godinu. Primici su naplaćeni primici iz KPR-a
    (godina u KPR-u je godina naplate)."""
    cur = conn.cursor()
    got, vir = cur.execute(
        "SELECT COALESCE(SUM(gotovina_cent), 0), COALESCE(SUM(virmanski_cent), 0) "
        "FROM kpr WHERE godina=?", (godina,)).fetchone()
    ukupno = got + vir

    lista, g_razreda = razredi(conn, godina)
    razred = odredi_razred(lista, ukupno) if lista else None
    limit = lista[-1]["do"] if lista else 0
    porez = razred["porez"] if razred else 0

    pr = predujmovi(conn, godina)
    uplaceno = sum(pr.values())

    postotak = (ukupno / limit * 100) if limit else 0.0
    if limit and ukupno > limit:
        status_limita = "prekoračen"
    elif postotak >= 95:
        status_limita = "iznad 95 %"
    elif postotak >= 80:
        status_limita = "iznad 80 %"
    else:
        status_limita = "u redu"

    rez = {
        "godina": godina,
        "gotovina": got,
        "bezgotovinski": vir,
        "ukupno": ukupno,
        "razred": razred["razred"] if razred else None,
        "porez": porez,
        "kvartalni_predujam": porez // 4,
        "predujmovi": pr,
        "uplaceno": uplaceno,
        "razlika": porez - uplaceno,           # > 0 uplata, < 0 povrat
        "limit": limit,
        "postotak_limita": postotak,
        "status_limita": status_limita,
        "godina_razreda": g_razreda,
    }
    rez["provjere"] = provjere(conn, godina, rez, demo=demo, danas=danas)
    return rez


# ── Administracija poreznih razreda ──────────────────────────────────────────

def godine_razreda(conn):
    """Godine za koje postoje razredi, od najnovije."""
    return [g for (g,) in conn.execute(
        "SELECT DISTINCT godina FROM porezni_razredi ORDER BY godina DESC")]


def dohvati_razrede(conn, godina):
    """Razredi točno za tu godinu (bez zamjene ranijom). Lista dict-ova."""
    rows = conn.execute(
        "SELECT razred, od_cent, do_cent, porez_cent FROM porezni_razredi "
        "WHERE godina=? ORDER BY razred", (godina,)).fetchall()
    return [dict(razred=r[0], od=r[1], do=r[2], porez=r[3]) for r in rows]


def uskladi_razrede(lista):
    """Redni brojevi 1..n i donja granica = prethodna gornja + 1 cent
    (prvi razred počinje od 0). Vraća novu listu."""
    izlaz, prethodni_do = [], None
    for i, r in enumerate(lista, 1):
        izlaz.append(dict(razred=i,
                          od=0 if prethodni_do is None else prethodni_do + 1,
                          do=int(r["do"]), porez=int(r["porez"])))
        prethodni_do = int(r["do"])
    return izlaz


def provjeri_razrede(lista):
    """(greške, upozorenja) za listu razreda. Greške sprječavaju spremanje."""
    greske, upoz = [], []
    if not lista:
        return ["Mora postojati barem jedan razred."], upoz
    prethodni = None
    for i, r in enumerate(lista, 1):
        if r["do"] <= 0:
            greske.append(f"Razred {i}: gornja granica mora biti veća od 0.")
        if prethodni is not None and r["do"] <= prethodni:
            greske.append(
                f"Razred {i}: gornja granica ({fmt_cent(r['do'])}) mora biti "
                f"veća od granice prethodnog razreda ({fmt_cent(prethodni)}).")
        if r["porez"] < 0:
            greske.append(f"Razred {i}: porez ne smije biti negativan.")
        elif r["porez"] % 4:
            upoz.append(f"Razred {i}: porez nije djeljiv s 4, tromjesečni "
                        "predujam će biti zaokružen prema dolje.")
        if i > 1 and r["porez"] < lista[i - 2]["porez"]:
            upoz.append(f"Razred {i}: porez je manji nego u prethodnom razredu.")
        prethodni = r["do"]
    return greske, upoz


def spremi_razrede(conn, godina, lista, izvor=None):
    """Zamjenjuje razrede za godinu (u jednoj transakciji). Baca ValueError
    ako lista nije valjana."""
    lista = uskladi_razrede(lista)
    greske, _ = provjeri_razrede(lista)
    if greske:
        raise ValueError("\n".join(greske))
    izvor = izvor or f"ručno uneseno {date.today():%d.%m.%Y}"
    try:
        conn.execute("DELETE FROM porezni_razredi WHERE godina=?", (godina,))
        conn.executemany(
            "INSERT INTO porezni_razredi "
            "(godina, razred, od_cent, do_cent, porez_cent, izvor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(godina, r["razred"], r["od"], r["do"], r["porez"], izvor)
             for r in lista])
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def kopiraj_razrede(conn, iz_godine, u_godinu):
    """Nova godina kao kopija postojeće (da se samo prepravi što se promijenilo)."""
    if dohvati_razrede(conn, u_godinu):
        raise ValueError(f"Razredi za {u_godinu}. već postoje.")
    izvor = dohvati_razrede(conn, iz_godine)
    if not izvor:
        raise ValueError(f"Nema razreda za {iz_godine}.")
    spremi_razrede(conn, u_godinu, izvor,
                   izvor=f"kopija iz {iz_godine}., provjeriti")


def obrisi_razrede(conn, godina):
    """Briše razrede godine, ali nikad zadnju preostalu godinu."""
    if godine_razreda(conn) == [godina]:
        raise ValueError("Ne može se obrisati zadnja preostala godina razreda.")
    conn.execute("DELETE FROM porezni_razredi WHERE godina=?", (godina,))
    conn.commit()


# ── Provjere prije predaje ───────────────────────────────────────────────────

def _god(tekst):
    """Godina iz datuma 'dd.MM.yyyy' ili None."""
    try:
        return int(str(tekst).strip()[-4:])
    except (TypeError, ValueError):
        return None


def _datum(tekst):
    try:
        return datetime.strptime(str(tekst).strip(), "%d.%m.%Y").date()
    except (TypeError, ValueError):
        return None


def _nalaz(razina, naslov, detalj=""):
    assert razina in RAZINE
    return {"razina": razina, "naslov": naslov, "detalj": detalj}


def _popis(stavke, najvise=8):
    s = ", ".join(str(x) for x in stavke[:najvise])
    return s + (f" … (+{len(stavke) - najvise})" if len(stavke) > najvise else "")


def provjere(conn, godina, rez=None, demo=False, danas=None):
    """Lista nalaza {razina, naslov, detalj}, najteži prvi."""
    danas = danas or date.today()
    cur = conn.cursor()
    n = []

    if demo:
        n.append(_nalaz(
            "upozorenje", "Aplikacija je u testnom (demo) načinu",
            "Računi izdani u testnom okruženju ne smiju ući u pravi PO-SD."))

    racuni = [r for r in cur.execute(
        "SELECT id, broj_racuna, datum, rok_placanja, ukupan_iznos_cent, "
        "fiskaliziran, pravna_osoba, placeno, datum_naplate FROM invoices")
        if _god(r[2]) == godina]

    # 1. fiskalizacija
    nef = [r[1] or f"#{r[0]}" for r in racuni if not r[5] and not r[6]]
    if nef:
        n.append(_nalaz(
            "greska", f"{len(nef)} računa nema JIR (nije fiskalizirano)",
            _popis(nef) + ". Pošalji ih ponovno prije predaje."))

    # 2. neplaćeni
    nepl = [r for r in racuni if not r[7]]
    if nepl:
        iznos = sum(r[4] or 0 for r in nepl)
        dospjeli = [r[1] or f"#{r[0]}" for r in nepl
                    if (_datum(r[3]) and _datum(r[3]) < danas)]
        det = (f"Ukupno {fmt_cent(iznos)} EUR ne ulazi u primitke dok se ne "
               "označi naplata.")
        if dospjeli:
            det += f" Dospjeli: {_popis(dospjeli)}."
        n.append(_nalaz(
            "upozorenje" if dospjeli else "info",
            f"{len(nepl)} računa iz {godina}. nije označeno kao naplaćeno", det))

    # 3. naplata u drugoj godini od računa
    preko = []
    for r in cur.execute(
            "SELECT broj_racuna, datum, datum_naplate FROM invoices "
            "WHERE placeno=1"):
        gr, gn = _god(r[1]), _god(r[2])
        if gr and gn and gr != gn and godina in (gr, gn):
            preko.append(f"{r[0]} (račun {gr}., naplata {gn}.)")
    if preko:
        n.append(_nalaz(
            "info", "Računi naplaćeni u drugoj godini od izdavanja",
            _popis(preko) + ". Ulaze u godinu naplate."))

    # 4. KPR: negativni unosi, rupe, duplikati, siročad, zbroj
    kpr = cur.execute(
        "SELECT id, redni_broj, gotovina_cent, virmanski_cent, invoice_id, "
        "rucno_dodano FROM kpr WHERE godina=? ORDER BY redni_broj",
        (godina,)).fetchall()

    neg = [k[1] for k in kpr if (k[2] or 0) < 0 or (k[3] or 0) < 0]
    if neg:
        n.append(_nalaz("info", f"{len(neg)} negativnih unosa u KPR-u (storno)",
                        "Redni brojevi: " + _popis(neg) +
                        ". Smanjuju primitke; provjeri da su ispravni."))

    brojevi = [k[1] for k in kpr]
    if brojevi:
        rupe = sorted(set(range(1, max(brojevi) + 1)) - set(brojevi))
        if rupe:
            n.append(_nalaz(
                "upozorenje", "Rupe u rednim brojevima KPR-a",
                "Nedostaju: " + _popis(rupe) + ". Nastaju kad se naplata "
                "poništi; knjiga mora imati objašnjenje."))

    po_racunu = {}
    for k in kpr:
        if k[4] is not None:
            po_racunu.setdefault(k[4], []).append(k[1])
    dup = {i: b for i, b in po_racunu.items() if len(b) > 1}
    if dup:
        n.append(_nalaz(
            "greska", "Isti račun je u KPR-u više puta",
            "; ".join(f"račun #{i}: unosi {_popis(b)}" for i, b in dup.items())))

    postoje = {r[0] for r in cur.execute("SELECT id FROM invoices")}
    sirote = [k[1] for k in kpr if k[4] is not None and k[4] not in postoje]
    if sirote:
        n.append(_nalaz("upozorenje", "KPR unos pokazuje na račun koji ne postoji",
                        "Redni brojevi: " + _popis(sirote)))

    ruc = [k for k in kpr if k[5]]
    if ruc:
        n.append(_nalaz(
            "info", f"{len(ruc)} ručnih unosa u KPR-u",
            f"Ukupno {fmt_cent(sum((k[2] or 0) + (k[3] or 0) for k in ruc))} EUR; "
            "nisu vezani uz račun."))

    # 5. KPR spram naplaćenih računa
    iz_racuna = sum(
        (r[0] or 0) for r in cur.execute(
            "SELECT ukupan_iznos_cent, datum_naplate FROM invoices WHERE placeno=1")
        if _god(r[1]) == godina)
    iz_kpr = sum((k[2] or 0) + (k[3] or 0) for k in kpr if k[4] is not None)
    if iz_racuna != iz_kpr:
        n.append(_nalaz(
            "greska", "Zbroj KPR-a se ne slaže s naplaćenim računima",
            f"Naplaćeni računi: {fmt_cent(iz_racuna)} EUR, računi u KPR-u: "
            f"{fmt_cent(iz_kpr)} EUR (razlika {fmt_cent(iz_racuna - iz_kpr)} EUR)."))

    # 6. razredi i limit
    if rez is not None:
        if rez["godina_razreda"] is None:
            n.append(_nalaz("greska", "Nema tablice poreznih razreda",
                            "Upiši razrede u tablicu porezni_razredi."))
        elif rez["godina_razreda"] != godina:
            n.append(_nalaz(
                "upozorenje",
                f"Razredi za {godina}. nisu uneseni, korišteni su razredi za "
                f"{rez['godina_razreda']}.",
                "Provjeri iznose s Poreznom upravom."))
        if rez["status_limita"] == "prekoračen":
            n.append(_nalaz(
                "greska", "Prekoračen je limit primitaka paušalnog obrta",
                f"Primici {fmt_cent(rez['ukupno'])} EUR, limit "
                f"{fmt_cent(rez['limit'])} EUR. Posavjetuj se s knjigovođom."))
        elif rez["status_limita"] != "u redu":
            n.append(_nalaz(
                "upozorenje", f"Primici su {rez['status_limita']} limita",
                f"{rez['postotak_limita']:.1f} % od {fmt_cent(rez['limit'])} EUR."))

    n.sort(key=lambda x: RAZINE.index(x["razina"]))
    return n


def podsjetnik(danas=None):
    """Tekst podsjetnika od 1. prosinca do roka 15. siječnja, inače ''."""
    danas = danas or date.today()
    if danas.month == 12:
        return (f"Približava se PO-SD za {danas.year}. godinu "
                f"(rok 15.01.{danas.year + 1}.). Provjeri naplate i KPR.")
    if danas.month == 1 and danas.day <= 15:
        return (f"PO-SD za {danas.year - 1}. godinu predaje se do "
                f"15.01.{danas.year}. ({15 - danas.day} dana).")
    return ""


# ── Izlazi: PDF i arhivski paket ─────────────────────────────────────────────

def redovi_za_ep(rez):
    """Vrijednosti redom kojim se upisuju u ePoreznu (naziv polja, tekst)."""
    return [
        ("Primici naplaćeni u gotovini (uključujući kartice)",
         cent_za_unos(rez["gotovina"])),
        ("Primici naplaćeni bezgotovinskim putem",
         cent_za_unos(rez["bezgotovinski"])),
        ("Ukupno naplaćeni primici", cent_za_unos(rez["ukupno"])),
        ("Porezni razred (izračunava sustav)",
         str(rez["razred"]) if rez["razred"] else "iznad limita"),
        ("Iznos obveze paušalnog poreza na dohodak (izračunava sustav)",
         cent_za_unos(rez["porez"])),
        ("Ukupno uplaćeni paušalni porez na dohodak",
         cent_za_unos(rez["uplaceno"])),
        ("Razlika za uplatu / povrat (izračunava sustav)",
         cent_za_unos(rez["razlika"])),
    ]


def napravi_pdf(rez, putanja, izdavatelj="", oib=""):
    """Jednostranični PDF s brojkama za prepisivanje u ePoreznu."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    from fontovi import get_font

    fn, fn_b = get_font()
    W, H = A4
    c = canvas.Canvas(putanja, pagesize=A4)
    y = H - 50
    c.setFont(fn_b, 16)
    c.drawString(40, y, f"PO-SD: pripremni obračun za {rez['godina']}.")
    y -= 18
    c.setFont(fn, 9)
    c.drawString(40, y, f"{izdavatelj}  OIB: {oib}".strip())
    c.drawRightString(W - 40, y, f"Izrađeno {datetime.now():%d.%m.%Y %H:%M}")
    y -= 28

    c.setFont(fn_b, 10)
    c.drawString(40, y, "Vrijednosti redom kojim se upisuju u ePoreznu (EUR)")
    y -= 6
    c.line(40, y, W - 40, y)
    y -= 16
    c.setFont(fn, 10)
    for naziv, vrijednost in redovi_za_ep(rez):
        c.drawString(40, y, naziv)
        c.setFont(fn_b, 10)
        c.drawRightString(W - 40, y, vrijednost)
        c.setFont(fn, 10)
        y -= 16

    y -= 8
    c.setFont(fn_b, 10)
    c.drawString(40, y, "Uplaćeni predujmovi")
    y -= 14
    c.setFont(fn, 9)
    for q in (1, 2, 3, 4):
        c.drawString(50, y, f"{q}. kvartal (rok {KVARTALI_ROKOVI[q]}{rez['godina']}.)")
        c.drawRightString(W - 40, y, fmt_cent(rez["predujmovi"][q]))
        y -= 13
    y -= 4
    c.drawString(
        40, y,
        f"Limit primitaka: {fmt_cent(rez['limit'])} EUR, iskorišteno "
        f"{rez['postotak_limita']:.1f} % ({rez['status_limita']}).")
    y -= 24

    c.setFont(fn_b, 10)
    c.drawString(40, y, "Provjere prije predaje")
    y -= 14
    oznake = {"greska": "[GREŠKA]", "upozorenje": "[UPOZORENJE]", "info": "[INFO]"}
    c.setFont(fn, 8.5)
    if not rez["provjere"]:
        c.drawString(50, y, "Nema nalaza.")
        y -= 12
    for nal in rez["provjere"]:
        if y < 90:
            break
        c.drawString(50, y, f"{oznake[nal['razina']]} {nal['naslov']}")
        y -= 11
        if nal["detalj"]:
            c.setFillColorRGB(0.35, 0.35, 0.35)
            c.drawString(62, y, nal["detalj"][:120])
            c.setFillColorRGB(0, 0, 0)
            y -= 12

    c.setFont(fn, 7.5)
    c.setFillColorRGB(0.4, 0.4, 0.4)
    c.drawString(
        40, 40,
        "Pripremni obračun iz knjige prometa (naplaćeni primici). Nije predani "
        "obrazac. Razrede i iznose provjeri s Poreznom upravom ili knjigovođom.")
    c.showPage()
    c.save()
    return putanja


def arhivski_paket(conn, godina, osnovna_mapa, putanja_zip, pdf_putanja=None):
    """ZIP s KPR-om (CSV), PO-SD PDF-om i arhivom poslanih poruka CIS-a za
    godinu. Vraća broj datoteka u paketu."""
    cur = conn.cursor()
    redci = cur.execute(
        "SELECT redni_broj, datum, broj_temeljnice, opis, gotovina_cent, "
        "virmanski_cent FROM kpr WHERE godina=? ORDER BY redni_broj",
        (godina,)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Redni broj", "Datum", "Broj dokumenta", "Opis",
                "Gotovina i kartice", "Virmanski", "Ukupno"])
    for rb, dat, tem, opis, g, v in redci:
        w.writerow([rb, dat, tem or "", opis or "",
                    cent_za_unos(g or 0), cent_za_unos(v or 0),
                    cent_za_unos((g or 0) + (v or 0))])

    broj = 0
    with zipfile.ZipFile(putanja_zip, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"KPR_{godina}.csv", "﻿" + buf.getvalue())
        broj += 1
        if pdf_putanja and os.path.exists(pdf_putanja):
            z.write(pdf_putanja, f"PO-SD_{godina}.pdf")
            broj += 1
        korijen = os.path.join(os.path.abspath(osnovna_mapa), "arhiva", str(godina))
        if os.path.isdir(korijen):
            for mapa, _, datoteke in os.walk(korijen):
                for d in datoteke:
                    puni = os.path.join(mapa, d)
                    z.write(puni, os.path.join(
                        "cis_poruke", os.path.relpath(puni, korijen)))
                    broj += 1
    return broj
