# Fiskalizacija – aplikacija za račune

Pokretanje: `python main.py` (iz ove mape; `config.json` i `billing.db` su u njoj).
Testovi: `python -m pytest tests`

## Struktura
| Datoteka | Sadržaj |
|---|---|
| `main.py` | glavni ekran (PySide6): račun, pregled, kupci, PDF |
| `fiskalizacija.py` | CIS: ZKI, potpis XML-a (RSA-SHA256), slanje, QR kod – bez UI-ja |
| `db.py` | shema, migracije, backup, brojač računa, kupci – bez UI-ja |
| `novac.py` | sav novčani izračun (Decimal, cent po stavci, popust, PDV) |
| `postavke.py` | `config.json`, putanje, tekstovi oslobođenja od PDV-a |
| `kpr_module.py`, `ponude_module.py`, `detalji_racuna.py`, `theme_module.py` | KPR, ponude, detalji računa, tema |
| `arhiva_util.py`, `format_util.py`, `fontovi.py` | backup/arhiva XML-a, format `1.234,50`, font za PDF |
| `tests/` | testovi ZKI-ja, potpisa, izračuna i baze |

## Verzioniranje
Samo jedna aktualna datoteka (`main.py`); verzije se prate gitom (grane i tagovi,
npr. `git tag v3.1`), a ne kopijama `_v1`, `_v2`. Stare kopije skripti su u
`_stare_verzije/` (ignorirano gitom, mapu možeš obrisati).
Tag `pred-refaktor` je stanje neposredno prije ovog preuređenja.

## Pravila
- Iznosi se uvijek računaju kroz `novac.py`, nikad izravno u floatu.
- Moduli ne smiju uvoziti iz `main.py`; bazu dohvaćaju preko `db.conn`.
- Tajne (`config.json`, `cert/*.p12|pem`, `billing.db`) nikad u git.
