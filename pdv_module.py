"""Dijalog za izradu PDV i PDV-S obrasca (XML za ePorezna) iz Etsy naknada."""
import os
from datetime import datetime

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit, QCheckBox,
    QDoubleSpinBox, QPushButton, QMessageBox, QFileDialog, QHBoxLayout)

import pdv
from format_util import fmt_iznos
from novac import D, cent


def generirano(cfg, mjesec):
    """{'PDV': 'datum', 'PDV-S': 'datum'} za mjesece za koje su obrasci već spremljeni."""
    return dict((cfg.get("pdv_generirano") or {}).get(mjesec) or {})


class PdvDijalog(QDialog):
    def __init__(self, widget, mjesec, ukupno_cent, parent=None):
        super().__init__(parent or widget)
        self._w = widget
        self.mjesec = mjesec
        self.setWindowTitle(f"PDV i PDV-S – {mjesec[5:]}.{mjesec[:4]}.")
        self.resize(480, 360)
        cfg = widget._cfg()
        lay = QVBoxLayout(self)
        forma = QFormLayout()
        self.osnovica = QDoubleSpinBox()
        self.osnovica.setRange(0, 10_000_000)
        self.osnovica.setDecimals(2)
        self.osnovica.setSuffix(" EUR")
        self.osnovica.setValue(float(ukupno_cent) / 100)
        self.osnovica.valueChanged.connect(self.osvjezi)
        forma.addRow("Osnovica (Etsy naknade):", self.osnovica)
        self.pretporez = QCheckBox("Imam pravo na odbitak pretporeza")
        self.pretporez.setChecked(bool(cfg.get("pdv_odbija_pretporez", False)))
        self.pretporez.toggled.connect(self.osvjezi)
        forma.addRow("", self.pretporez)
        self.ispostava = QLineEdit(str(cfg.get("pdv_ispostava", "")))
        self.ispostava.setMaxLength(4)
        self.ispostava.setPlaceholderText("4 znamenke, šifra ispostave")
        forma.addRow("Ispostava Porezne uprave:", self.ispostava)
        self.etsy_id = QLineEdit(str(cfg.get("etsy_pdv_id", "")))
        self.etsy_id.setPlaceholderText("PDV ID s Etsy fakture, bez 'IE'")
        forma.addRow("Etsy Ireland PDV ID:", self.etsy_id)
        lay.addLayout(forma)
        self.rezultat = QLabel("")
        self.rezultat.setWordWrap(True)
        lay.addWidget(self.rezultat)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color: gray; font-size: 11px;")
        lay.addWidget(self.status)
        red = QHBoxLayout()
        self.oznake = QLabel("")
        self.oznake.setWordWrap(True)
        lay.addWidget(self.oznake)
        self.pdv_btn = QPushButton("Spremi PDV XML…")
        self.pdv_btn.clicked.connect(lambda: self.spremi("PDV"))
        self.pdvs_btn = QPushButton("Spremi PDV-S XML…")
        self.pdvs_btn.clicked.connect(lambda: self.spremi("PDV-S"))
        red.addWidget(self.pdv_btn)
        red.addWidget(self.pdvs_btn)
        lay.addLayout(red)
        self.osvjezi()
        self.osvjezi_oznake()

    def osvjezi_oznake(self):
        g = generirano(self._w._cfg(), self.mjesec)
        redovi = []
        for tip in ("PDV", "PDV-S"):
            if tip in g:
                redovi.append(f"✔ {tip} generiran {g[tip]}")
                (self.pdv_btn if tip == "PDV" else self.pdvs_btn).setText(
                    f"Ponovno spremi {tip} XML…")
            else:
                redovi.append(f"○ {tip} još nije generiran")
        self.oznake.setText("<br>".join(redovi) + ("<br><i>Možeš ih generirati ponovno.</i>"
                                                  if g else ""))

    def _osnovica_cent(self):
        return int(round(self.osnovica.value() * 100))

    def osvjezi(self, _=None):
        r = pdv.izracun(self._osnovica_cent(), self.pretporez.isChecked())
        od, do = pdv.razdoblje_mjeseca(self.mjesec)
        self.rezultat.setText(
            f"PDV 25 %: {fmt_iznos(r['porez'])} EUR · pretporez: {fmt_iznos(r['pretporez'])} EUR · "
            f"<b>obveza: {fmt_iznos(r['obveza'])} EUR</b><br>"
            f"Rok predaje: {pdv.rok_predaje(do):%d.%m.%Y.}")

    def _obveznik(self):
        c = self._w._cfg()
        o = {"naziv": c.get("naziv_tvrtke", ""), "oib": c.get("oib", ""),
             "adresa": c.get("adresa", "")}
        dijelovi = str(c.get("operater", "")).split()
        if len(dijelovi) >= 2:      # obrtnik: kao u obrascu iz ePorezne, Ime + Prezime
            o["ime"], o["prezime"] = dijelovi[0], " ".join(dijelovi[1:])
        return o

    def generiraj(self, tip):
        c = self._w._cfg()
        isp = self.ispostava.text().strip()
        osn = self._osnovica_cent()
        if tip == "PDV":
            return pdv.xml_pdv(self._obveznik(), self.mjesec, osn, isp,
                               c.get("operater", ""), self.pretporez.isChecked())
        return pdv.xml_pdvs(self._obveznik(), self.mjesec, osn, isp,
                            c.get("operater", ""), self.etsy_id.text())

    def spremi(self, tip):
        try:
            xml = self.generiraj(tip)
        except pdv.PdvGreska as e:
            QMessageBox.warning(self, "PDV", str(e))
            return
        status, greske = pdv.validiraj(xml, tip, self._w._cfg().get("eporezna_sheme") or None)
        if status == "greske":
            QMessageBox.warning(self, "PDV",
                                "XML ne odgovara shemi Porezne uprave:\n" + "\n".join(greske[:5]))
            return
        mapa = "pdv_obrasci"
        os.makedirs(mapa, exist_ok=True)
        ime = f"{tip.replace('-', '')}_{self.mjesec}.xml"
        putanja, _ = QFileDialog.getSaveFileName(
            self, f"Spremi {tip} XML", os.path.join(mapa, ime), "XML (*.xml)")
        if not putanja:
            return
        with open(putanja, "w", encoding="utf-8") as f:
            f.write(xml)
        evidencija = dict(self._w._cfg().get("pdv_generirano") or {})
        mj = dict(evidencija.get(self.mjesec) or {})
        mj[tip] = datetime.now().strftime("%d.%m.%Y. %H:%M")
        evidencija[self.mjesec] = mj
        self._w._config_setter({
            "pdv_generirano": evidencija,
            "pdv_ispostava": self.ispostava.text().strip(),
            "etsy_pdv_id": self.etsy_id.text().strip(),
            "pdv_odbija_pretporez": self.pretporez.isChecked()})
        napomena = ("provjereno prema XSD shemi" if status == "ok"
                    else f"validacija preskočena ({greske[0]})")
        self.osvjezi_oznake()
        self.status.setText(f"Spremljeno: {putanja} ({napomena}).")
