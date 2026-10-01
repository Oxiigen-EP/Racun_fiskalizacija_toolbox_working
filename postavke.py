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


def _keyring_dohvati():
    """Lozinka iz Credential Managera; '' ako je nema, None ako je keyring
    nedostupan."""
    if keyring is None:
        return None
    try:
        return keyring.get_password(KEYRING_SERVIS, KEYRING_KORISNIK) or ""
    except Exception as e:
        print(f"⚠️  Keyring nedostupan: {e}")
        return None


def _keyring_spremi(lozinka):
    """Sprema (ili briše, ako je prazna) i provjerava čitanjem. True = uspjelo."""
    if keyring is None:
        return False
    try:
        if lozinka:
            keyring.set_password(KEYRING_SERVIS, KEYRING_KORISNIK, lozinka)
            return keyring.get_password(KEYRING_SERVIS, KEYRING_KORISNIK) == lozinka
        try:
            keyring.delete_password(KEYRING_SERVIS, KEYRING_KORISNIK)
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
        "key_password": "",
        "demo_mode": True,
        "operater": "",
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

    # Lozinku ključa čuvamo u keyringu, ne u config.json.
    if keyring is None:
        if defaults.get("key_password"):
            print("⚠️  keyring nije instaliran (pip install keyring): lozinka "
                  "ključa ostaje u config.json")
    elif defaults.get("key_password"):
        # stari način: premjesti u keyring, a iz datoteke makni tek kad je
        # spremanje potvrđeno čitanjem
        if _keyring_spremi(defaults["key_password"]):
            _zapisi_config({**defaults, "key_password": ""})
            print("🔐 Lozinka ključa premještena u Windows Credential Manager")
    else:
        iz_keyringa = _keyring_dohvati()
        if iz_keyringa:
            defaults["key_password"] = iz_keyringa
    return defaults

def spremi_config(data):
    """Sprema konfiguraciju u JSON datoteku."""
    try:
        zapis = dict(data)
        if keyring is not None and _keyring_spremi(zapis.get("key_password", "")):
            zapis["key_password"] = ""        # lozinka je u keyringu
        _zapisi_config(zapis)
        print(f"✅ Konfiguracija spremljena u {CONFIG_PATH}")
    except Exception as e:
        print(f"⚠️  Greška pri spremanju konfiguracije: {e}")
