"""Lozinka ključa ide u keyring, ne u config.json."""
import json

import keyring
import pytest
from keyring.backend import KeyringBackend

import postavke


class MemorijskiKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.podaci = {}

    def get_password(self, servis, korisnik):
        return self.podaci.get((servis, korisnik))

    def set_password(self, servis, korisnik, lozinka):
        self.podaci[(servis, korisnik)] = lozinka

    def delete_password(self, servis, korisnik):
        self.podaci.pop((servis, korisnik), None)


@pytest.fixture
def okolina(tmp_path, monkeypatch):
    kr = MemorijskiKeyring()
    keyring.set_keyring(kr)
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(postavke, "CONFIG_PATH", str(cfg))
    return kr, cfg


def test_stara_lozinka_se_premjesta_u_keyring(okolina):
    kr, cfg = okolina
    cfg.write_text(json.dumps({"oib": "1", "key_password": "tajna"}))
    c = postavke.učitaj_config()
    assert c["key_password"] == "tajna"                  # aplikacija je i dalje vidi
    assert kr.podaci[(postavke.KEYRING_SERVIS, postavke.KEYRING_KORISNIK)] == "tajna"
    assert json.loads(cfg.read_text())["key_password"] == ""   # u datoteci je nema
    assert "tajna" not in cfg.read_text()


def test_sljedece_pokretanje_cita_iz_keyringa(okolina):
    kr, cfg = okolina
    cfg.write_text(json.dumps({"key_password": "tajna"}))
    postavke.učitaj_config()
    assert postavke.učitaj_config()["key_password"] == "tajna"


def test_spremanje_ne_pise_lozinku_u_datoteku(okolina):
    kr, cfg = okolina
    postavke.spremi_config({"oib": "1", "key_password": "nova"})
    assert "nova" not in cfg.read_text()
    assert postavke.učitaj_config()["key_password"] == "nova"


def test_brisanje_lozinke_brise_i_iz_keyringa(okolina):
    kr, cfg = okolina
    postavke.spremi_config({"key_password": "nova"})
    postavke.spremi_config({"key_password": ""})
    assert kr.podaci == {}
    assert postavke.učitaj_config()["key_password"] == ""


def test_bez_keyringa_lozinka_ostaje_u_datoteci(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    monkeypatch.setattr(postavke, "CONFIG_PATH", str(cfg))
    monkeypatch.setattr(postavke, "keyring", None)
    postavke.spremi_config({"key_password": "tajna"})
    assert json.loads(cfg.read_text())["key_password"] == "tajna"   # ne gubi se
    assert postavke.učitaj_config()["key_password"] == "tajna"


def test_neuspjelo_spremanje_ne_brise_lozinku_iz_datoteke(okolina, monkeypatch):
    kr, cfg = okolina
    monkeypatch.setattr(kr, "set_password",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("zaključan")))
    cfg.write_text(json.dumps({"key_password": "tajna"}))
    c = postavke.učitaj_config()
    assert c["key_password"] == "tajna"
    assert json.loads(cfg.read_text())["key_password"] == "tajna"
