"""Točan izračun iznosa računa (Decimal, zaokruživanje na cent, half-up).

Pravila, ista na svim mjestima (unos, spremanje, PDF, XML, detalji):
  bruto      = količina × cijena                      (zaokruženo na cent)
  popust     = količina × cijena × popust% / 100      (zaokruženo na cent)
  osnovica   = bruto − popust
  PDV        = osnovica × stopa / 100                 (zaokruženo na cent)
  ukupno     = osnovica + PDV
Zbrojevi su zbrojevi već zaokruženih redaka, pa se redci, PDV razrada i
ukupni iznos uvijek točno slažu. Vrijednosti se vraćaju kao float (točno
2 decimale) jer baza i ostatak koda koriste REAL.
"""
from decimal import Decimal, ROUND_HALF_UP

CENT = Decimal("0.01")


def D(vrijednost) -> Decimal:
    return vrijednost if isinstance(vrijednost, Decimal) else Decimal(str(vrijednost))


def cent(vrijednost) -> Decimal:
    return D(vrijednost).quantize(CENT, rounding=ROUND_HALF_UP)


def _f(d: Decimal) -> float:
    return float(d) + 0.0          # -0.0 postaje 0.0


def izracun_stavke(kolicina, cijena, pdv_stopa=0, popust=0) -> dict:
    kol, cij = D(kolicina), D(cijena)
    stopa = D(pdv_stopa or 0)
    pop = min(max(D(popust or 0), Decimal(0)), Decimal(100))
    bruto = cent(kol * cij)
    popust_iznos = cent(kol * cij * pop / 100)
    osnovica = bruto - popust_iznos
    pdv = cent(osnovica * stopa / 100)
    return {
        "bruto": _f(bruto),
        "popust": _f(pop),
        "popust_iznos": _f(popust_iznos),
        "osnovica": _f(osnovica),
        "pdv_iznos": _f(pdv),
        "ukupno": _f(osnovica + pdv),
    }


def zbroji(vrijednosti) -> float:
    return _f(cent(sum((D(v) for v in vrijednosti), Decimal(0))))


def ukupno_stavki(stavke) -> float:
    """Ukupan iznos računa iz stavki (svaka ima ključ 'ukupno')."""
    return zbroji(s["ukupno"] for s in stavke)


def pdv_grupe_iz_stavki(stavke) -> list:
    """PDV razrada po stopi u obliku za XML (samo stope > 0)."""
    grupe = {}
    for s in stavke:
        stopa = D(s["pdv_stopa"] or 0)
        g = grupe.setdefault(stopa, [Decimal(0), Decimal(0)])
        g[0] += D(s["ukupno_bez_pdv"])
        g[1] += D(s["pdv_iznos"])
    return [{"Stopa": f"{stopa:.2f}",
             "Osnovica": f"{cent(o):.2f}",
             "Iznos": f"{cent(p):.2f}"}
            for stopa, (o, p) in grupe.items() if stopa > 0]
