"""Hrvatski format iznosa: 1.234,50 (točka za tisućice, zarez za decimale)."""


def fmt_iznos(vrijednost, decimale: int = 2) -> str:
    """1234.5 -> '1.234,50', -0.5 -> '-0,50'. Samo za prikaz (UI, PDF),
    nikad za XML prema CIS-u, ZKI ili QR kod."""
    try:
        v = float(vrijednost)
    except (TypeError, ValueError):
        return str(vrijednost)
    if v == 0:
        v = 0.0                       # bez "-0,00"
    s = f"{v:,.{decimale}f}"          # 1,234.50
    return s.replace(",", "\0").replace(".", ",").replace("\0", ".")


def parse_iznos(tekst) -> float:
    """Čita iznos koji korisnik upiše: '1.234,50', '1234,50' ili '1234.50'.
    Ako postoji zarez, točke su tisućice. Samo točka (npr. '12.5') je decimalna.
    Baca ValueError kao float() za prazan ili neispravan unos."""
    t = str(tekst).strip().replace(" ", "").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    return float(t)
