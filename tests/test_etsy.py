"""Etsy: mapiranje narudžbe u račun, baza narudžbi, klijent (lažni odgovori)."""
import json
import sqlite3

import pytest

import etsy


def money(iznos, valuta="EUR", div=100):
    return {"amount": int(round(iznos * div)), "divisor": div, "currency_code": valuta}


def narudzba(**kw):
    r = {
        "receipt_id": 1001, "name": "Anna Müller", "first_line": "Hauptstr. 5",
        "city": "Berlin", "zip": "10115", "country_iso": "DE",
        "was_paid": True, "status": "paid", "created_timestamp": 1790000000,
        "transactions": [
            {"title": "Drvena figurica", "quantity": 2, "price": money(15.00)},
            {"title": "Privjesak", "quantity": 1, "price": money(4.50)}],
        "total_shipping_cost": money(6.00), "discount_amt": money(0),
        "total_tax_cost": money(0), "grandtotal": money(40.50),
    }
    r.update(kw)
    return r


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY AUTOINCREMENT, "
              "broj_racuna TEXT, datum TEXT, naziv_kupca TEXT, "
              "ukupan_iznos_cent INTEGER, fiskaliziran INTEGER)")
    c.execute("PRAGMA foreign_keys=ON")
    etsy.inicijaliziraj_etsy(c)
    return c


# ── Mapiranje ────────────────────────────────────────────────────────────────

def test_racun_iz_narudzbe_osnovno():
    r = etsy.racun_iz_narudzbe(narudzba())
    assert [s["naziv"] for s in r["stavke"]] == ["Drvena figurica", "Privjesak", "Poštarina"]
    assert [s["kolicina"] for s in r["stavke"]] == [2, 1, 1]
    assert r["ukupno"] == 40.50
    assert r["valuta"] == "EUR" and r["oib_kupca"] == ""
    assert "1001" in r["napomena"]
    assert r["upozorenja"] == []
    assert "Berlin" in r["adresa_kupca"]


def test_popust_je_negativna_stavka_s_upozorenjem():
    r = etsy.racun_iz_narudzbe(narudzba(discount_amt=money(5.00), grandtotal=money(35.50)))
    assert r["stavke"][-1]["cijena"] == -5
    assert r["ukupno"] == 35.50
    assert any("popust" in u.lower() for u in r["upozorenja"])


def test_porez_koji_je_naplatio_etsy_nije_u_racunu():
    r = etsy.racun_iz_narudzbe(narudzba(total_tax_cost=money(7.70), grandtotal=money(48.20)))
    assert r["ukupno"] == 40.50
    assert any("porez" in u.lower() for u in r["upozorenja"])
    assert not any("razlikuje" in u for u in r["upozorenja"])


def test_neslaganje_iznosa_upozorava():
    r = etsy.racun_iz_narudzbe(narudzba(grandtotal=money(99.00)))
    assert any("razlikuje" in u for u in r["upozorenja"])


def test_strana_valuta_traži_tecaj():
    n = narudzba(
        transactions=[{"title": "X", "quantity": 1, "price": money(20, "USD")}],
        total_shipping_cost=money(0, "USD"), grandtotal=money(20, "USD"))
    with pytest.raises(etsy.EtsyGreska):
        etsy.racun_iz_narudzbe(n)
    r = etsy.racun_iz_narudzbe(n, tecaj="0.9")
    assert r["stavke"][0]["cijena"] == 18
    assert r["ukupno"] == 18
    assert "USD" in r["napomena"]


def test_tecaj_mora_biti_pozitivan():
    n = narudzba(transactions=[{"title": "X", "quantity": 1, "price": money(20, "USD")}],
                 grandtotal=money(20, "USD"))
    with pytest.raises(etsy.EtsyGreska):
        etsy.racun_iz_narudzbe(n, tecaj="0")


def test_narudzba_bez_stavki():
    with pytest.raises(etsy.EtsyGreska):
        etsy.racun_iz_narudzbe(narudzba(transactions=[]))


def test_iznosi_u_centima_bez_float_gresaka():
    n = narudzba(transactions=[{"title": "X", "quantity": 3, "price": money(0.10)}],
                 total_shipping_cost=money(0), grandtotal=money(0.30))
    from decimal import Decimal
    assert etsy.racun_iz_narudzbe(n)["ukupno"] == Decimal("0.30")


# ── Baza ─────────────────────────────────────────────────────────────────────

def test_spremi_narudzbe_ne_duplicira_i_cuva_status(conn):
    assert etsy.spremi_narudzbe(conn, [narudzba()]) == 1
    etsy.preskoci(conn, 1001)
    assert etsy.spremi_narudzbe(conn, [narudzba()]) == 0
    assert etsy.popis_narudzbi(conn)[0]["status"] == etsy.STATUS_PRESKOCENO


def test_nepaćene_i_otkazane_se_ne_uvoze(conn):
    n = etsy.spremi_narudzbe(conn, [narudzba(receipt_id=1, was_paid=False),
                                    narudzba(receipt_id=2, status="canceled"),
                                    narudzba(receipt_id=3)])
    assert n == 1


def test_iznos_u_popisu_je_bez_poreza(conn):
    etsy.spremi_narudzbe(conn, [narudzba(total_tax_cost=money(7.70), grandtotal=money(48.20))])
    assert etsy.popis_narudzbi(conn)[0]["iznos_cent"] == 4050


def test_poveži_racun_i_preskoci_ne_dira_izdane(conn):
    conn.execute("INSERT INTO invoices (id) VALUES (7)")
    etsy.spremi_narudzbe(conn, [narudzba()])
    etsy.poveži_racun(conn, 1001, 7)
    etsy.preskoci(conn, 1001)
    red = etsy.popis_narudzbi(conn)[0]
    assert red["status"] == etsy.STATUS_RACUN and red["invoice_id"] == 7


def test_brisanje_racuna_ne_rusi_narudzbu(conn):
    conn.execute("INSERT INTO invoices (id) VALUES (7)")
    etsy.spremi_narudzbe(conn, [narudzba()])
    etsy.poveži_racun(conn, 1001, 7)
    conn.execute("DELETE FROM invoices WHERE id=7")
    assert etsy.popis_narudzbi(conn)[0]["invoice_id"] is None


def test_dohvati_narudzbu_vraca_izvorni_zapis(conn):
    etsy.spremi_narudzbe(conn, [narudzba()])
    assert etsy.dohvati_narudzbu(conn, 1001)["name"] == "Anna Müller"
    assert etsy.dohvati_narudzbu(conn, 5) is None


# ── Klijent ──────────────────────────────────────────────────────────────────

class Odg:
    def __init__(self, status=200, js=None):
        self.status_code, self._js, self.text = status, js or {}, json.dumps(js or {})

    def json(self):
        return self._js


class LazniSession:
    def __init__(self, get_odgovori=(), post_odgovori=()):
        self.get_odgovori, self.post_odgovori = list(get_odgovori), list(post_odgovori)
        self.pozivi = []

    def get(self, url, **kw):
        self.pozivi.append(("get", url, kw))
        return self.get_odgovori.pop(0)

    def post(self, url, **kw):
        self.pozivi.append(("post", url, kw))
        return self.post_odgovori.pop(0)


def klijent(sess, istice=10**10, spremljeno=None):
    return etsy.EtsyKlijent(
        "kljuc", {"access_token": "42.abc", "refresh_token": "r", "istice": istice},
        spremi_tokene=(spremljeno.append if spremljeno is not None else None),
        session=sess, sada=lambda: 1000)


def test_user_id_iz_tokena():
    assert klijent(LazniSession()).user_id == "42"


def test_trgovina():
    s = LazniSession([Odg(200, {"shop_id": 99, "shop_name": "Moja"})])
    assert klijent(s).trgovina() == (99, "Moja")
    assert s.pozivi[0][1].endswith("/users/42/shops")
    assert s.pozivi[0][2]["headers"]["x-api-key"] == "kljuc"


def test_straničenje_narudzbi():
    s = LazniSession([Odg(200, {"count": 3, "results": [{"receipt_id": 1}, {"receipt_id": 2}]}),
                      Odg(200, {"count": 3, "results": [{"receipt_id": 3}]})])
    sve = klijent(s).placene_narudzbe(99)
    assert [r["receipt_id"] for r in sve] == [1, 2, 3]
    assert s.pozivi[1][2]["params"]["offset"] == 2
    assert s.pozivi[0][2]["params"]["was_paid"] == "true"


def test_istekli_token_se_osvjezava_i_sprema():
    spremljeno = []
    s = LazniSession(
        get_odgovori=[Odg(200, {"count": 0, "results": []})],
        post_odgovori=[Odg(200, {"access_token": "42.nova", "refresh_token": "r2",
                                 "expires_in": 3600})])
    k = klijent(s, istice=1010, spremljeno=spremljeno)    # istječe za 10 s
    k.placene_narudzbe(99)
    assert spremljeno[0]["access_token"] == "42.nova"
    assert s.pozivi[1][2]["headers"]["Authorization"] == "Bearer 42.nova"


def test_401_jednom_osvjezi_pa_ponovi():
    s = LazniSession(
        get_odgovori=[Odg(401), Odg(200, {"count": 0, "results": []})],
        post_odgovori=[Odg(200, {"access_token": "42.n", "refresh_token": "r",
                                 "expires_in": 3600})])
    assert klijent(s).placene_narudzbe(99) == []


def test_neuspjelo_osvjezavanje_trazi_ponovnu_prijavu():
    s = LazniSession(get_odgovori=[Odg(401)], post_odgovori=[Odg(400)])
    with pytest.raises(etsy.EtsyGreska, match="ponovno"):
        klijent(s).placene_narudzbe(99)


def test_429_i_greske():
    with pytest.raises(etsy.EtsyGreska, match="ograničava"):
        klijent(LazniSession([Odg(429)])).trgovina()
    with pytest.raises(etsy.EtsyGreska, match="500"):
        klijent(LazniSession([Odg(500)])).trgovina()


def test_bez_prijave():
    k = etsy.EtsyKlijent("k", {}, session=LazniSession())
    with pytest.raises(etsy.EtsyGreska, match="povezan"):
        k.trgovina()


def test_sinkroniziraj_upisuje_nove(conn):
    s = LazniSession([Odg(200, {"count": 1, "results": [narudzba()]})])
    assert etsy.sinkroniziraj(conn, klijent(s), 99) == 1


# ── PKCE / prijava ───────────────────────────────────────────────────────────

def test_pkce_challenge_odgovara_verifieru():
    import base64, hashlib
    v, c = etsy.napravi_pkce()
    assert 43 <= len(v) <= 128
    assert c == base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()


def test_url_prijave_ima_pkce_i_state():
    u = etsy.url_prijave("kljuc", etsy.ZADANI_REDIRECT, "CH", "ST")
    assert "code_challenge=CH" in u and "state=ST" in u and "S256" in u
    assert "client_id=kljuc" in u


def test_prijava_end_to_end_s_lokalnim_poslužiteljem():
    """Stvarni lokalni HTTP poslužitelj; 'preglednik' je GET na redirect."""
    import threading
    import urllib.request
    from urllib.parse import parse_qs, urlparse
    redirect = "http://localhost:38765/callback"

    def lazni_preglednik(url):
        q = parse_qs(urlparse(url).query)
        def idi():
            urllib.request.urlopen(f"{redirect}?code=KOD&state={q['state'][0]}").read()
        threading.Thread(target=idi, daemon=True).start()

    s = LazniSession(post_odgovori=[Odg(200, {"access_token": "42.t", "refresh_token": "r",
                                              "expires_in": 3600})])
    tok = etsy.prijava("kljuc", redirect, otvori=lazni_preglednik, session=s,
                       sada=lambda: 5, timeout=10)
    assert tok["access_token"] == "42.t" and tok["istice"] == 3605
    podaci = s.pozivi[0][2]["data"]
    assert podaci["code"] == "KOD" and podaci["code_verifier"]


def test_prijava_odbija_krivi_state():
    import threading
    import urllib.request
    redirect = "http://localhost:38766/callback"

    def lazni_preglednik(url):
        threading.Thread(target=lambda: urllib.request.urlopen(
            f"{redirect}?code=KOD&state=KRIVO").read(), daemon=True).start()

    with pytest.raises(etsy.EtsyGreska, match="state"):
        etsy.prijava("kljuc", redirect, otvori=lazni_preglednik,
                     session=LazniSession(), timeout=10)


def _racun(conn, broj="1/PP1/1", iznos=4050):
    cur = conn.execute("INSERT INTO invoices (broj_racuna, datum, naziv_kupca, "
                       "ukupan_iznos_cent, fiskaliziran) VALUES (?,?,?,?,1)",
                       (broj, "01.10.2026", "Anna", iznos))
    conn.commit()
    return cur.lastrowid


def test_povezi_postojeci_racun(conn):
    etsy.spremi_narudzbe(conn, [narudzba()])
    inv = _racun(conn)
    assert [r["id"] for r in etsy.nepovezani_racuni(conn)] == [inv]
    etsy.poveži_postojeci(conn, 1001, inv)
    n = etsy.popis_narudzbi(conn)[0]
    assert n["status"] == etsy.STATUS_RACUN and n["invoice_id"] == inv
    assert etsy.nepovezani_racuni(conn) == []


def test_povezi_postojeci_odbija_zauzet_racun(conn):
    etsy.spremi_narudzbe(conn, [narudzba(), narudzba(receipt_id=1002)])
    inv = _racun(conn)
    etsy.poveži_postojeci(conn, 1001, inv)
    with pytest.raises(ValueError):
        etsy.poveži_postojeci(conn, 1002, inv)
    with pytest.raises(ValueError):
        etsy.poveži_postojeci(conn, 1002, 9999)


def test_odvezi_racun_vraca_u_novu(conn):
    etsy.spremi_narudzbe(conn, [narudzba()])
    inv = _racun(conn)
    etsy.poveži_postojeci(conn, 1001, inv)
    etsy.odvezi_racun(conn, 1001)
    n = etsy.popis_narudzbi(conn)[0]
    assert n["status"] == etsy.STATUS_NOVA and n["invoice_id"] is None
    assert [r["id"] for r in etsy.nepovezani_racuni(conn)] == [inv]


# ── Više trgovina ────────────────────────────────────────────────────────────

def test_narudzbe_po_trgovinama(conn):
    etsy.spremi_trgovinu(conn, 11, "Shop A")
    etsy.spremi_trgovinu(conn, 22, "Shop B")
    etsy.spremi_narudzbe(conn, [narudzba(receipt_id=1)], 11)
    etsy.spremi_narudzbe(conn, [narudzba(receipt_id=2)], 22)
    sve = etsy.popis_narudzbi(conn)
    assert {(r["receipt_id"], r["shop_naziv"]) for r in sve} == {(1, "Shop A"), (2, "Shop B")}
    assert [r["receipt_id"] for r in etsy.popis_narudzbi(conn, shop_id=22)] == [2]
    assert etsy.popis_trgovina(conn) == [(11, "Shop A"), (22, "Shop B")]


def test_spremi_trgovinu_azurira_naziv(conn):
    etsy.spremi_trgovinu(conn, 11, "Staro")
    etsy.spremi_trgovinu(conn, 11, "Novo")
    assert etsy.popis_trgovina(conn) == [(11, "Novo")]


def test_stari_zapisi_dobiju_shop_id_iz_payloada():
    c = sqlite3.connect(":memory:")
    c.execute("CREATE TABLE invoices (id INTEGER PRIMARY KEY)")
    c.execute("CREATE TABLE etsy_narudzbe (receipt_id INTEGER PRIMARY KEY, kupac TEXT, "
              "drzava TEXT, iznos_cent INTEGER, valuta TEXT, datum TEXT, "
              "status TEXT DEFAULT 'nova', invoice_id INTEGER, uvezeno TEXT, payload TEXT)")
    c.execute("INSERT INTO etsy_narudzbe (receipt_id, payload) VALUES (5, ?)",
              (json.dumps({"shop_id": 77}),))
    etsy.inicijaliziraj_etsy(c)
    assert etsy.popis_narudzbi(c)[0]["shop_id"] == 77


def test_tokeni_trgovina_migracija_starog_oblika():
    stari = json.dumps({"access_token": "1.x", "refresh_token": "r"})
    assert etsy.tokeni_trgovina(stari, 11) == {"11": {"tokeni": json.loads(stari)}}
    assert etsy.tokeni_trgovina(stari, None) == {}
    novi = json.dumps({"11": {"tokeni": {"access_token": "a"}}})
    assert etsy.tokeni_trgovina(novi) == json.loads(novi)
    assert etsy.tokeni_trgovina("ne json") == {}


def test_odspoji_tokene_brise_samo_odabranu_trgovinu():
    raw = json.dumps({"11": {"tokeni": {"access_token": "a"}},
                      "22": {"tokeni": {"access_token": "b"}}})
    ostalo = json.loads(etsy.odspoji_tokene(raw, 11))
    assert list(ostalo) == ["22"]
    assert json.loads(etsy.odspoji_tokene(raw, 999)) == json.loads(raw)
    stari = json.dumps({"access_token": "a"})
    assert json.loads(etsy.odspoji_tokene(stari, 11, 11)) == {}


# ── Naknade (ledger) ─────────────────────────────────────────────────────────
from datetime import datetime as _dt


def _unos(i, opis, iznos, dan=10, tip="fee"):
    ts = int(_dt(2025, 8, dan, 12).timestamp())
    return {"entry_id": i, "ledger_type": tip, "description": opis, "amount": iznos,
            "currency": "EUR", "create_date": ts}


KOLOVOZ = [_unos(1, "Listing fee", -17), _unos(2, "Listing fee", -17),
           _unos(3, "Transaction fee: Drvena figurica", -717),
           _unos(4, "Renew sold", -17, tip="renew_sold"), _unos(5, "Renew sold", -51),
           _unos(6, "Processing fee", -558), _unos(7, "Payment for order", 4050, tip="payment"),
           _unos(8, "Deposit to bank", -4000, tip="deposit")]


def test_kategorija_naknade():
    k = etsy.kategorija_naknade
    assert k("fee", "Listing fee") == "listing"
    assert k("", "Transaction fee: x") == "transaction"
    assert k("", "Renew sold") == "renew"
    assert k("", "Processing fee") == "processing"
    assert k("", "Renew expired listing") == "renew_expired"
    assert k("", "Etsy Ads") == "ads"
    assert k("", "Offsite Ads fee") == "ads"
    assert k("", "Transaction fee: Heads sign") == "transaction"
    assert k("payment", "Payment for order") is None
    assert k("deposit", "Deposit to bank") is None


def test_naknade_mjeseca_zbroj_po_kategorijama(conn):
    assert etsy.spremi_naknade(conn, KOLOVOZ, 11) == 8
    assert etsy.spremi_naknade(conn, KOLOVOZ, 11) == 0          # bez duplikata
    r = etsy.naknade_mjeseca(conn, "2025-08")
    assert r["kategorije"] == {"listing": 34, "transaction": 717, "renew": 68,
                               "renew_expired": 0, "ads": 0, "processing": 558}
    assert r["ukupno"] == 1377                                  # EUR 13,77 kao na fakturi
    assert r["valute"] == ["EUR"]
    assert etsy.naknade_mjeseca(conn, "2025-07")["ukupno"] == 0
    assert etsy.naknade_mjeseca(conn, "2025-08", shop_id=99)["ukupno"] == 0


def test_ledger_unosi_straniči_i_salje_razdoblje():
    s = LazniSession([Odg(200, {"count": 3, "results": [{"entry_id": 1}, {"entry_id": 2}]}),
                      Odg(200, {"count": 3, "results": [{"entry_id": 3}]})])
    k = klijent(s)
    unosi = k.ledger_unosi(5, _dt(2025, 8, 1), _dt(2025, 8, 31, 23, 59, 59))
    assert [u["entry_id"] for u in unosi] == [1, 2, 3]
    url, kw = s.pozivi[0][1], s.pozivi[0][2]
    assert url.endswith("/shops/5/payment-account/ledger-entries")
    assert kw["params"]["min_created"] < kw["params"]["max_created"]
    assert s.pozivi[1][2]["params"]["offset"] == 2


def test_naknade_kolovoz_2026_s_etsy_ads(conn):
    """Faktura 08/2026: ukupno EUR 30,75 (Etsy zaokružuje po stavci, razlika 1 cent)."""
    ts = int(_dt(2026, 8, 15, 12).timestamp())
    def u(i, opis, c):
        return {"entry_id": i, "ledger_type": "fee", "description": opis, "amount": -c,
                "currency": "EUR", "create_date": ts}
    etsy.spremi_naknade(conn, [u(1, "Listing fee", 68), u(2, "Listing fee", 16),
                               u(3, "Transaction fee", 806), u(4, "Renew sold", 85),
                               u(5, "Etsy Ads", 1453), u(6, "Processing fee", 646)], 7)
    r = etsy.naknade_mjeseca(conn, "2026-08")
    assert r["kategorije"] == {"listing": 84, "transaction": 806, "renew": 85,
                               "renew_expired": 0, "ads": 1453, "processing": 646}
    assert abs(r["ukupno"] - 3075) <= 1


def test_naknade_s_renew_expired(conn):
    """Faktura s Renew Expired: ukupno EUR 52,63."""
    ts = int(_dt(2026, 9, 15, 12).timestamp())
    def u(i, opis, c):
        return {"entry_id": i, "ledger_type": "fee", "description": opis, "amount": -c,
                "currency": "EUR", "create_date": ts}
    stavke = [("Listing fee", 216), ("Listing fee", 13), ("Transaction fee", 1471),
              ("Renew expired", 180), ("Renew expired", 9), ("Renew sold", 108),
              ("Renew sold", 12), ("Etsy Ads", 2116), ("Processing fee", 1138)]
    etsy.spremi_naknade(conn, [u(i, o, c) for i, (o, c) in enumerate(stavke, 1)], 7)
    r = etsy.naknade_mjeseca(conn, "2026-09")
    assert r["kategorije"] == {"listing": 229, "transaction": 1471, "renew": 120,
                               "renew_expired": 189, "ads": 2116, "processing": 1138}
    assert r["ukupno"] == 5263
