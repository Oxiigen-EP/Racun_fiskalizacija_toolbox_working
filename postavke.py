"""Postavke aplikacije (config.json) i zajedničke konstante."""
import json
import os

CONFIG_PATH = "config.json"
# Baza, backup i arhiva poruka su u istoj mapi kao config.json
BASE_DIR = os.path.dirname(os.path.abspath(CONFIG_PATH))

# Zakonski tekstovi oslobođenja od PDV-a (indeks = redoslijed u dropdownu)
PDV_OSLOBODENJE_TEKSTOVI = [
    "Oslobođeno PDV-a temeljem članka 90. st. 1 Zakona o PDV-u",
    "Oslobođeno PDV-a temeljem članka 90. st. 2 Zakona o PDV-u",
    "Oslobođeno PDV-a temeljem članka 17. st. 1 Zakona o PDV-u - reverse charge",
]

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
    return defaults

def spremi_config(data):
    """Sprema konfiguraciju u JSON datoteku."""
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ Konfiguracija spremljena u {CONFIG_PATH}")
    except Exception as e:
        print(f"⚠️  Greška pri spremanju konfiguracije: {e}")
