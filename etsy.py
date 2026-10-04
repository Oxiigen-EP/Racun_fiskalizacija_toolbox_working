"""Etsy: prijava (OAuth 2.0 + PKCE), dohvat plaćenih narudžbi i pretvorba u
nacrt računa. Bez UI-ja i bez ovisnosti o db/main modulima.

Tok: Etsy narudžba (receipt) -> tablica etsy_narudzbe (status 'nova') ->
korisnik klikne "Izdaj račun" -> nacrt računa se učita u formu 'Novi račun' ->
nakon spremanja i fiskalizacije narudžba dobiva status 'racun' i invoice_id.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
from datetime import datetime, timedelta
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

from novac import D, cent, u_centima

API_URL = "https://openapi.etsy.com/v3/application"
AUTH_URL = "https://www.etsy.com/oauth/connect"
TOKEN_URL = "https://api.etsy.com/v3/public/oauth/token"
ZADANI_REDIRECT = "http://localhost:3003/callback"
SCOPE = "transactions_r shops_r"
KEYRING_TOKENI = "etsy_tokeni"
NASA_VALUTA = "EUR"

STATUS_NOVA = "nova"
STATUS_RACUN = "racun"
STATUS_PRESKOCENO = "preskoceno"


class EtsyGreska(Exception):
    """Greška u komunikaciji s Etsyjem (poruka je namijenjena korisniku)."""


# ── Baza ─────────────────────────────────────────────────────────────────────

def inicijaliziraj_etsy(conn, cursor=None):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etsy_narudzbe (
            receipt_id   INTEGER PRIMARY KEY,
            kupac        TEXT,
            drzava       TEXT,
            iznos_cent   INTEGER,
            valuta       TEXT,
            datum        TEXT,
            status       TEXT NOT NULL DEFAULT 'nova',
            invoice_id   INTEGER REFERENCES invoices(id) ON DELETE SET NULL,
            uvezeno      TEXT,
            payload      TEXT
        )""")
    kolone = {r[1] for r in conn.execute("PRAGMA table_info(etsy_narudzbe)")}
    if "shop_id" not in kolone:
        conn.execute("ALTER TABLE etsy_narudzbe ADD COLUMN shop_id INTEGER")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS etsy_trgovine (
            shop_id INTEGER PRIMARY KEY,
            naziv   TEXT
        )""")
    try:   # stare narudžbe: shop_id je u Etsyjevu receiptu
        conn.execute("UPDATE etsy_narudzbe SET shop_id = "
                     "CAST(json_extract(payload, '$.shop_id') AS INTEGER) "
                     "WHERE shop_id IS NULL")
    except Exception:
        pass
    conn.execute("CREATE INDEX IF NOT EXISTS idx_etsy_status "
                 "ON etsy_narudzbe(status)")
    conn.commit()


def spremi_trgovinu(conn, shop_id, naziv):
    conn.execute("INSERT INTO etsy_trgovine (shop_id, naziv) VALUES (?,?) "
                 "ON CONFLICT(shop_id) DO UPDATE SET naziv=excluded.naziv",
                 (int(shop_id), naziv or ""))
    conn.commit()


def popis_trgovina(conn):
    """[(shop_id, naziv)] svih povezanih trgovina."""
    return [(r[0], r[1] or "") for r in conn.execute(
        "SELECT shop_id, naziv FROM etsy_trgovine ORDER BY naziv, shop_id")]


def tokeni_trgovina(raw, stari_shop_id=None):
    """Iz teksta spremljenog u keyring vraća {shop_id(str): {"tokeni": {...}}}.
    Stari oblik (jedan skup tokena) prebacuje pod `stari_shop_id`."""
    try:
        d = json.loads(raw) if raw else {}
    except ValueError:
        return {}
    if not isinstance(d, dict):
        return {}
    if "access_token" in d:
        return {str(stari_shop_id): {"tokeni": d}} if stari_shop_id else {}
    return d


def _novac(m):
    """Etsyjev Money {amount, divisor, currency_code} -> Decimal."""
    if not m:
        return D(0)
    return D(m.get("amount", 0)) / D(m.get("divisor", 100) or 100)


def _valuta(receipt):
    for kljuc in ("grandtotal", "total_price", "subtotal"):
        m = receipt.get(kljuc)
        if m and m.get("currency_code"):
            return m["currency_code"]
    for t in receipt.get("transactions", []):
        if (t.get("price") or {}).get("currency_code"):
            return t["price"]["currency_code"]
    return NASA_VALUTA


def _datum(receipt):
    ts = (receipt.get("created_timestamp") or receipt.get("create_timestamp")
          or receipt.get("creation_timestamp"))
    if not ts:
        return ""
    return datetime.fromtimestamp(int(ts)).strftime("%d.%m.%Y")


def odspoji_tokene(raw, shop_id, stari_shop_id=None):
    """Tekst za keyring bez tokena trgovine `shop_id` (narudžbe i računi ostaju)."""
    sve = tokeni_trgovina(raw, stari_shop_id)
    sve.pop(str(shop_id), None)
    return json.dumps(sve)


def spremi_narudzbe(conn, receipts, shop_id=None):
    """Upisuje nove narudžbe; postojeće (i njihov status) ne dira.
    Vraća broj novih."""
    novih = 0
    for r in receipts:
        if not r.get("receipt_id"):
            continue
        if r.get("was_paid") is False or r.get("status") in ("canceled",):
            continue
        sad = datetime.now().isoformat(timespec="seconds")
        neto = _novac(r.get("grandtotal")) - _novac(r.get("total_tax_cost")) \
            - _novac(r.get("total_vat_cost"))
        cur = conn.execute(
            "INSERT OR IGNORE INTO etsy_narudzbe (receipt_id, kupac, drzava, "
            "iznos_cent, valuta, datum, status, uvezeno, payload, shop_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (int(r["receipt_id"]), r.get("name") or "", r.get("country_iso") or "",
             u_centima(neto), _valuta(r), _datum(r), STATUS_NOVA, sad,
             json.dumps(r, ensure_ascii=False),
             int(shop_id) if shop_id else (r.get("shop_id") or None)))
        novih += cur.rowcount
    conn.commit()
    return novih


def popis_narudzbi(conn, status=None, shop_id=None):
    sql = ("SELECT n.receipt_id, n.kupac, n.drzava, n.iznos_cent, n.valuta, n.datum, "
           "n.status, n.invoice_id, n.shop_id, t.naziv FROM etsy_narudzbe n "
           "LEFT JOIN etsy_trgovine t ON t.shop_id = n.shop_id")
    uvjeti, par = [], []
    if status:
        uvjeti.append("n.status=?")
        par.append(status)
    if shop_id:
        uvjeti.append("n.shop_id=?")
        par.append(int(shop_id))
    if uvjeti:
        sql += " WHERE " + " AND ".join(uvjeti)
    sql += " ORDER BY n.receipt_id DESC"
    kljucevi = ("receipt_id", "kupac", "drzava", "iznos_cent", "valuta", "datum",
                "status", "invoice_id", "shop_id", "shop_naziv")
    return [dict(zip(kljucevi, r)) for r in conn.execute(sql, tuple(par))]


def dohvati_narudzbu(conn, receipt_id):
    red = conn.execute("SELECT payload FROM etsy_narudzbe WHERE receipt_id=?",
                       (receipt_id,)).fetchone()
    return json.loads(red[0]) if red and red[0] else None


def poveži_racun(conn, receipt_id, invoice_id):
    conn.execute("UPDATE etsy_narudzbe SET status=?, invoice_id=? "
                 "WHERE receipt_id=?", (STATUS_RACUN, invoice_id, receipt_id))
    conn.commit()


def nepovezani_racuni(conn, limit=100):
    """Zadnji računi koji još nisu vezani ni uz jednu Etsy narudžbu."""
    redovi = conn.execute(
        "SELECT id, broj_racuna, datum, naziv_kupca, ukupan_iznos_cent, fiskaliziran "
        "FROM invoices WHERE id NOT IN "
        "(SELECT invoice_id FROM etsy_narudzbe WHERE invoice_id IS NOT NULL) "
        "ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(("id", "broj", "datum", "kupac", "iznos_cent", "fiskaliziran"), r))
            for r in redovi]


def poveži_postojeci(conn, receipt_id, invoice_id):
    """Ručno veže već izdani račun uz narudžbu. ValueError ako nije moguće."""
    if not conn.execute("SELECT 1 FROM invoices WHERE id=?", (invoice_id,)).fetchone():
        raise ValueError("Račun ne postoji.")
    if not conn.execute("SELECT 1 FROM etsy_narudzbe WHERE receipt_id=?",
                        (receipt_id,)).fetchone():
        raise ValueError("Narudžba ne postoji.")
    zauzet = conn.execute("SELECT receipt_id FROM etsy_narudzbe WHERE invoice_id=? "
                          "AND receipt_id<>?", (invoice_id, receipt_id)).fetchone()
    if zauzet:
        raise ValueError(f"Račun je već vezan uz narudžbu {zauzet[0]}.")
    poveži_racun(conn, receipt_id, invoice_id)


def odvezi_racun(conn, receipt_id):
    """Vraća narudžbu u 'nova'. Sam račun se ne briše."""
    conn.execute("UPDATE etsy_narudzbe SET status=?, invoice_id=NULL WHERE receipt_id=?",
                 (STATUS_NOVA, receipt_id))
    conn.commit()


def preskoci(conn, receipt_id, preskoceno=True):
    """Označi da za narudžbu ne treba račun (ili vrati u 'nova').
    Narudžba s već izdanim računom se ne dira."""
    conn.execute(
        "UPDATE etsy_narudzbe SET status=? WHERE receipt_id=? AND invoice_id IS NULL",
        (STATUS_PRESKOCENO if preskoceno else STATUS_NOVA, receipt_id))
    conn.commit()


# ── Pretvorba narudžbe u nacrt računa ────────────────────────────────────────

def _adresa(r):
    if r.get("formatted_address"):
        return " ".join(r["formatted_address"].replace("\r", "").split("\n")[1:]
                        ).strip() or r["formatted_address"].replace("\n", ", ")
    dijelovi = [r.get("first_line"), r.get("second_line"),
                " ".join(x for x in (r.get("zip"), r.get("city")) if x),
                r.get("state"), r.get("country_iso")]
    return ", ".join(d for d in dijelovi if d)


def racun_iz_narudzbe(receipt, tecaj=None):
    """Nacrt računa iz Etsy narudžbe.

    Vraća dict: etsy_id, naziv_kupca, adresa_kupca, oib_kupca, valuta,
    stavke (naziv, kolicina, jedinica, cijena, popust, pdv_stopa),
    napomena, upozorenja. Valuta različita od EUR traži `tecaj`
    (EUR za 1 jedinicu strane valute) jer se račun izdaje u eurima.
    """
    valuta = _valuta(receipt)
    upozorenja = []
    if valuta != NASA_VALUTA:
        if tecaj is None:
            raise EtsyGreska(
                f"Narudžba je u valuti {valuta}. Unesi tečaj (EUR za 1 {valuta}).")
        tecaj = D(tecaj)
        if tecaj <= 0:
            raise EtsyGreska("Tečaj mora biti veći od 0.")
    else:
        tecaj = D(1)

    def u_eur(iznos):
        return cent(D(iznos) * tecaj)

    stavke = []
    zbroj_stavki = D(0)
    for t in receipt.get("transactions", []):
        kol = int(t.get("quantity") or 1)
        cijena = u_eur(_novac(t.get("price")))
        stavke.append(dict(naziv=(t.get("title") or "Artikl")[:150], kolicina=kol,
                           jedinica="kom", cijena=cijena, popust=0, pdv_stopa=0))
        zbroj_stavki += cijena * kol
    if not stavke:
        raise EtsyGreska("Narudžba nema stavki.")

    dostava = u_eur(_novac(receipt.get("total_shipping_cost")))
    if dostava > 0:
        stavke.append(dict(naziv="Poštarina", kolicina=1, jedinica="kom",
                           cijena=dostava, popust=0, pdv_stopa=0))
        zbroj_stavki += dostava
    pakiranje = u_eur(_novac(receipt.get("gift_wrap_price")))
    if pakiranje > 0:
        stavke.append(dict(naziv="Poklon pakiranje", kolicina=1, jedinica="kom",
                           cijena=pakiranje, popust=0, pdv_stopa=0))
        zbroj_stavki += pakiranje

    popust = u_eur(_novac(receipt.get("discount_amt")))
    if popust > 0:
        stavke.append(dict(naziv="Popust (Etsy kupon)", kolicina=1, jedinica="kom",
                           cijena=-popust, popust=0, pdv_stopa=0))
        zbroj_stavki -= popust
        upozorenja.append("Narudžba ima popust: dodan je kao negativna stavka. "
                          "Provjeri račun prije fiskalizacije.")

    porez = _novac(receipt.get("total_tax_cost")) + _novac(receipt.get("total_vat_cost"))
    if porez > 0:
        upozorenja.append(
            f"Etsy je kupcu naplatio porez ({cent(porez)} {valuta}). Nije uključen "
            "u račun: provjeri s knjigovođom kako se evidentira.")

    ocekivano = u_eur(_novac(receipt.get("grandtotal")) - porez)
    if receipt.get("grandtotal") and abs(ocekivano - zbroj_stavki) > D("0.02"):
        upozorenja.append(
            f"Zbroj stavki ({cent(zbroj_stavki)}) razlikuje se od Etsy ukupnog "
            f"iznosa bez poreza ({ocekivano}). Provjeri iznose.")

    if valuta != NASA_VALUTA:
        upozorenja.append(f"Iznosi preračunati iz {valuta} po tečaju {tecaj}.")
    if receipt.get("status") not in (None, "paid", "completed"):
        upozorenja.append(f"Status narudžbe na Etsyju: {receipt.get('status')}.")

    napomena = (f"Etsy narudžba #{receipt['receipt_id']}. "
                "Plaćeno putem Etsyja, isplata na bankovni račun.")
    if valuta != NASA_VALUTA:
        napomena += f" ({valuta}, tečaj {tecaj})"

    return dict(
        etsy_id=int(receipt["receipt_id"]),
        naziv_kupca=receipt.get("name") or "Kupac s Etsyja",
        adresa_kupca=_adresa(receipt),
        oib_kupca="",
        valuta=valuta,
        stavke=stavke,
        ukupno=sum((s["cijena"] * s["kolicina"] for s in stavke), D(0)),
        napomena=napomena,
        upozorenja=upozorenja,
    )


# ── Tokeni ───────────────────────────────────────────────────────────────────

def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def napravi_pkce():
    verifier = _b64url(secrets.token_bytes(48))
    return verifier, _b64url(hashlib.sha256(verifier.encode()).digest())


def keystring(api_kljuc):
    """Etsy traži u x-api-key 'keystring:shared_secret'; OAuth client_id je samo keystring."""
    return (api_kljuc or "").split(":", 1)[0].strip()


def url_prijave(api_kljuc, redirect, challenge, state):
    return AUTH_URL + "?" + urlencode({
        "response_type": "code", "client_id": keystring(api_kljuc), "redirect_uri": redirect,
        "scope": SCOPE, "state": state, "code_challenge": challenge,
        "code_challenge_method": "S256"})


def _tokeni_iz_odgovora(js, sada):
    return {"access_token": js["access_token"],
            "refresh_token": js["refresh_token"],
            "istice": sada + int(js.get("expires_in", 3600))}


def _kod_s_preglednika(redirect, state, timeout=180):
    """Pokreće privremeni lokalni poslužitelj i čeka preusmjerenje s Etsyja."""
    u = urlparse(redirect)
    rezultat = {}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            q = parse_qs(urlparse(self.path).query)
            if q.get("state", [""])[0] != state:
                rezultat["greska"] = "Neispravan state (moguć pokušaj krivotvorenja)."
            elif "code" in q:
                rezultat["kod"] = q["code"][0]
            else:
                rezultat["greska"] = q.get("error_description",
                                            q.get("error", ["Prijava odbijena."]))[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h3>Gotovo. Možeš zatvoriti ovaj prozor i "
                             "vratiti se u aplikaciju.</h3>".encode())

        def log_message(self, *a):
            pass

    try:
        srv = HTTPServer((u.hostname or "localhost", u.port or 80), Handler)
    except OSError as e:
        raise EtsyGreska(f"Port {u.port} nije slobodan: {e}")
    srv.timeout = 1
    kraj = time.time() + timeout
    try:
        while not rezultat and time.time() < kraj:
            srv.handle_request()
    finally:
        srv.server_close()
    if "kod" in rezultat:
        return rezultat["kod"]
    raise EtsyGreska(rezultat.get("greska") or "Prijava nije dovršena na vrijeme.")


def prijava(api_kljuc, redirect=ZADANI_REDIRECT, otvori=None, session=None,
            sada=time.time, timeout=180):
    """Cijeli OAuth tok. `otvori(url)` otvara preglednik (zadano webbrowser)."""
    import requests
    session = session or requests
    if otvori is None:
        import webbrowser
        otvori = webbrowser.open
    verifier, challenge = napravi_pkce()
    state = _b64url(secrets.token_bytes(16))
    # poslužitelj mora slušati prije nego korisnik stigne kliknuti
    rez = {}

    def cekaj():
        try:
            rez["kod"] = _kod_s_preglednika(redirect, state, timeout)
        except EtsyGreska as e:
            rez["greska"] = e
    nit = threading.Thread(target=cekaj, daemon=True)
    nit.start()
    time.sleep(0.2)
    otvori(url_prijave(api_kljuc, redirect, challenge, state))
    nit.join(timeout + 5)
    if "greska" in rez:
        raise rez["greska"]
    if "kod" not in rez:
        raise EtsyGreska("Prijava nije dovršena na vrijeme.")
    odg = session.post(TOKEN_URL, data={
        "grant_type": "authorization_code", "client_id": keystring(api_kljuc),
        "redirect_uri": redirect, "code": rez["kod"],
        "code_verifier": verifier}, timeout=30)
    if odg.status_code != 200:
        raise EtsyGreska(f"Etsy je odbio prijavu ({odg.status_code}): {odg.text[:200]}")
    return _tokeni_iz_odgovora(odg.json(), sada())


class EtsyKlijent:
    """Tanki klijent za Etsy Open API v3. Sam osvježava access token."""

    def __init__(self, api_kljuc, tokeni, spremi_tokene=None, session=None,
                 sada=time.time):
        import requests
        self.api_kljuc = api_kljuc
        self.tokeni = dict(tokeni or {})
        self._spremi = spremi_tokene or (lambda t: None)
        self.session = session or requests
        self.sada = sada

    @property
    def user_id(self):
        # access token ima oblik "<user_id>.<tajna>"
        return (self.tokeni.get("access_token") or "").split(".")[0]

    def _osvjezi(self):
        odg = self.session.post(TOKEN_URL, data={
            "grant_type": "refresh_token", "client_id": keystring(self.api_kljuc),
            "refresh_token": self.tokeni.get("refresh_token", "")}, timeout=30)
        if odg.status_code != 200:
            raise EtsyGreska("Prijava na Etsy je istekla. Poveži se ponovno.")
        self.tokeni = _tokeni_iz_odgovora(odg.json(), self.sada())
        self._spremi(self.tokeni)

    def _get(self, putanja, params=None, ponovljeno=False):
        if not self.tokeni.get("access_token"):
            raise EtsyGreska("Nisi povezan s Etsyjem.")
        if self.tokeni.get("istice", 0) - self.sada() < 60:
            self._osvjezi()
        odg = self.session.get(
            API_URL + putanja, params=params, timeout=30,
            headers={"x-api-key": self.api_kljuc,
                     "Authorization": f"Bearer {self.tokeni['access_token']}"})
        if odg.status_code == 401 and not ponovljeno:
            self._osvjezi()
            return self._get(putanja, params, ponovljeno=True)
        if odg.status_code == 429:
            raise EtsyGreska("Etsy ograničava broj zahtjeva. Pokušaj za nekoliko minuta.")
        if odg.status_code != 200:
            raise EtsyGreska(f"Etsy greška {odg.status_code}: {odg.text[:200]}")
        return odg.json()

    def trgovina(self):
        """(shop_id, naziv) trgovine prijavljenog korisnika."""
        js = self._get(f"/users/{self.user_id}/shops")
        if "shop_id" not in js and js.get("results"):
            js = js["results"][0]
        if "shop_id" not in js:
            raise EtsyGreska("Etsy račun nema trgovinu.")
        return int(js["shop_id"]), js.get("shop_name", "")

    def placene_narudzbe(self, shop_id, dana=30):
        """Plaćene narudžbe iz zadnjih `dana` dana (sa straničenjem)."""
        od = int((datetime.now() - timedelta(days=dana)).timestamp())
        sve, offset = [], 0
        while True:
            js = self._get(f"/shops/{shop_id}/receipts", {
                "was_paid": "true", "min_created": od, "limit": 100,
                "offset": offset})
            stranica = js.get("results", [])
            sve.extend(stranica)
            offset += len(stranica)
            if not stranica or offset >= js.get("count", 0):
                return sve


def sinkroniziraj(conn, klijent, shop_id, dana=30):
    """Dohvati plaćene narudžbe i upiši nove. Vraća broj novih."""
    return spremi_narudzbe(conn, klijent.placene_narudzbe(shop_id, dana))
