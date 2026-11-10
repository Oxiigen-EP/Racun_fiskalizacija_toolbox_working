"""Postavke aplikacije (config.json) i zajedničke konstante."""
import json
import os

try:
    import keyring
except ImportError:          # bez keyringa lozinka ostaje u config.json
    keyring = None

CONFIG_PATH = "config.json"
# Baza, backup i arhiva poruka su u istoj mapi kao config.json
BASE_DIR = os.path.dirname(os.path.abspath(CONFIG_PATH))

# Zakonski tekstovi oslobođenja od PDV-a (indeks = redoslijed u dropdownu)
PDV_OSLOBODENJE_TEKSTOVI = [
    "Oslobođeno PDV-a temeljem članka 90. st. 1 Zakona o PDV-u",
    "Oslobođeno PDV-a temeljem članka 90. st. 2 Zakona o PDV-u",
    "Oslobođeno PDV-a temeljem članka 17. st. 1 Zakona o PDV-u - reverse charge",
]

# ── Lozinka privatnog ključa: Windows Credential Manager (paket keyring) ─────
# U config.json se lozinka ne drži; u memoriji (self.config) ostaje pod
# ključem "key_password" pa ostatak aplikacije radi kao i prije.
KEYRING_SERVIS = "FiskalApp"
KEYRING_KORISNIK = "lozinka_kljuca"
KEYRING_KORISNIK_PROD = "lozinka_kljuca_produkcija"
# ključ u config rječniku -> korisnik u keyringu (DEMO i PRODUKCIJSKI certifikat)
LOZINKE = {"key_password": KEYRING_KORISNIK,
           "prod_key_password": KEYRING_KORISNIK_PROD,
           "etsy_api_kljuc": "etsy_api_kljuc"}


def _keyring_dohvati(korisnik=KEYRING_KORISNIK):
    """Lozinka iz Credential Managera; '' ako je nema, None ako je keyring
    nedostupan."""
    if keyring is None:
        return None
    try:
        return keyring.get_password(KEYRING_SERVIS, korisnik) or ""
    except Exception as e:
        print(f"⚠️  Keyring nedostupan: {e}")
        return None


def _keyring_spremi(lozinka, korisnik=KEYRING_KORISNIK):
    """Sprema (ili briše, ako je prazna) i provjerava čitanjem. True = uspjelo."""
    if keyring is None:
        return False
    try:
        if lozinka:
            keyring.set_password(KEYRING_SERVIS, korisnik, lozinka)
            return keyring.get_password(KEYRING_SERVIS, korisnik) == lozinka
        try:
            keyring.delete_password(KEYRING_SERVIS, korisnik)
        except Exception:
            pass                 # nije ni bila spremljena
        return True
    except Exception as e:
        print(f"⚠️  Spremanje u keyring nije uspjelo: {e}")
        return False


def _zapisi_config(data):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def učitaj_config():
    """Učitava konfiguraciju iz JSON datoteke."""
    defaults = {
        "oib": "",
        "naziv_tvrtke": "",
        "adresa": "",
        "iban": "",
        "oznaka_pp": "PP1",
        "oznaka_nu": "1",
        "cert_path": "",
        "key_path": "",
        "key_password": "",          # lozinka DEMO certifikata
        "prod_cert_path": "",        # PRODUKCIJSKI certifikat
        "prod_key_path": "",
        "prod_key_password": "",
        "demo_mode": True,           # True = aktivan DEMO certifikat
        "operater": "",
        "etsy_api_kljuc": "",        # Etsy keystring (u Credential Manageru)
        "etsy_redirect": "http://localhost:3003/callback",
        "etsy_shop_id": "",
        "pdv_ispostava": "3532",         # 4-znamenkasta šifra ispostave Porezne uprave
        "etsy_pdv_id": "9777587C",           # PDV ID Etsy Ireland (bez 'IE')
        "eracun_fiskalizacija": False,   # Fiskalizacija 2.0: B2B račun šalje se kao eRačun
        "eracun_kpd": "",            # zadana KPD oznaka artikla (npr. 62.01.11)
        "eracun_ca": "",             # CA datoteka za TLS test okoline (prazno = bez provjere u testu)
        "eracun_url": "",            # adresa servisa Fiskalizacije 2.0 (prazno = zadana)
        "fisk2_xsd": "",             # neobavezno: XSD eFiskalizacije za provjeru prije slanja
        "pdv_odbija_pretporez": False,
        "pdv_generirano": {},        # {"2026-08": {"PDV": "datum", "PDV-S": "datum"}}
        "backup_mapa": ""      # prazno = mapa "backup" uz bazu; može biti npr. OneDrive
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                defaults.update(data)
                print(f"✅ Konfiguracija učitana iz {CONFIG_PATH}")
        except Exception as e:
            print(f"⚠️  Greška pri čitanju konfiguracije: {e}")

    # Lozinke ključeva čuvamo u keyringu, ne u config.json.
    premjesteno = {}
    for kljuc, korisnik in LOZINKE.items():
        if keyring is None:
            if defaults.get(kljuc):
                print("⚠️  keyring nije instaliran (pip install keyring): lozinka "
                      "ključa ostaje u config.json")
        elif defaults.get(kljuc):
            # stari način: premjesti u keyring, a iz datoteke makni tek kad je
            # spremanje potvrđeno čitanjem
            if _keyring_spremi(defaults[kljuc], korisnik):
                premjesteno[kljuc] = ""
                _zapisi_config({**defaults, **premjesteno})
                print("🔐 Lozinka ključa premještena u Windows Credential Manager")
        else:
            iz_keyringa = _keyring_dohvati(korisnik)
            if iz_keyringa:
                defaults[kljuc] = iz_keyringa
    return defaults

def spremi_config(data):
    """Sprema konfiguraciju u JSON datoteku."""
    try:
        zapis = dict(data)
        for kljuc, korisnik in LOZINKE.items():
            if (keyring is not None and kljuc in zapis
                    and _keyring_spremi(zapis.get(kljuc, ""), korisnik)):
                zapis[kljuc] = ""             # lozinka je u keyringu
        _zapisi_config(zapis)
        print(f"✅ Konfiguracija spremljena u {CONFIG_PATH}")
    except Exception as e:
        print(f"⚠️  Greška pri spremanju konfiguracije: {e}")


def certifikat_okoline(config, demo):
    """(cert_path, key_path, lozinka) certifikata za DEMO ili PRODUKCIJU."""
    pref = "" if demo else "prod_"
    return (config.get(pref + "cert_path", ""),
            config.get(pref + "key_path", ""),
            config.get(pref + "key_password", ""))


def je_demo_certifikat(cert_path):
    """True/False prema izdavatelju certifikata (FINA Demo CA), None ako se
    ne može pročitati."""
    try:
        from cryptography import x509
        with open(cert_path, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read())
        return "demo" in cert.issuer.rfc4514_string().lower()
    except Exception:
        return None
