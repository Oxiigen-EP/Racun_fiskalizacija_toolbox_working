from novac import izracun_stavke, ukupno_stavki, pdv_grupe_iz_stavki


def test_popust_i_pdv_zaokruzivanje():
    r = izracun_stavke(3, 19.99, 25, 10)
    assert r["bruto"] == 59.97
    assert r["popust_iznos"] == 6.0
    assert r["osnovica"] == 53.97
    assert r["pdv_iznos"] == 13.49
    assert r["ukupno"] == 67.46


def test_nema_float_pogreske():
    # 0.1 + 0.2 u floatu nije 0.3; ovdje mora biti točno
    assert ukupno_stavki([{"ukupno": 0.1}, {"ukupno": 0.2}]) == 0.3
    assert izracun_stavke(3, 0.1, 0, 0)["ukupno"] == 0.3


def test_popust_se_ogranicava_na_0_100():
    assert izracun_stavke(1, 100, 0, 150)["ukupno"] == 0.0
    assert izracun_stavke(1, 100, 0, -5)["ukupno"] == 100.0


def test_pdv_grupe_zbrajaju_po_stopi():
    st = [izracun_stavke(1, 10.05, 25, 0) | {"pdv_stopa": 25},
          izracun_stavke(1, 10.05, 25, 0) | {"pdv_stopa": 25},
          izracun_stavke(1, 50, 0, 0) | {"pdv_stopa": 0}]
    for s in st:
        s["ukupno_bez_pdv"] = s["osnovica"]
    g = pdv_grupe_iz_stavki(st)
    assert g == [{"Stopa": "25.00", "Osnovica": "20.10", "Iznos": "5.02"}]
