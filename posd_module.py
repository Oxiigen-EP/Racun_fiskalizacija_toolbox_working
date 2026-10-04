"""Tab PO-SD: pripremni obračun za paušalni obrt, provjere i izlazi."""
import os
from datetime import date

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QComboBox, QLineEdit, QGroupBox, QListWidget, QListWidgetItem,
    QProgressBar, QFileDialog, QMessageBox, QDialog, QTableWidget,
    QTableWidgetItem, QHeaderView, QInputDialog)

import posd
from format_util import parse_iznos
from novac import u_centima

_IKONE = {"greska": "❌", "upozorenje": "⚠️", "info": "ℹ️"}


class PoSdWidget(QWidget):
    """Obračun iz KPR-a: primici, razred, predujmovi, razlika i provjere."""

    def __init__(self, conn=None, cursor=None, config_getter=None, parent=None):
        super().__init__(parent)
        if conn is None:
            import db as _db
            conn = _db.conn
        self._conn = conn
        self._config_getter = config_getter or (lambda: {})
        self._rez = None
        self._gradnja = True            # dok se gradi sučelje, ne spremaj

        glavni = QVBoxLayout(self)
        glavni.setContentsMargins(16, 12, 16, 12)

        # gornji red: godina, osvježi, podsjetnik
        gore = QHBoxLayout()
        gore.addWidget(QLabel("Godina:"))
        self.godina_combo = QComboBox()
        self.godina_combo.currentTextChanged.connect(self._osvjezi_ako_spremno)
        gore.addWidget(self.godina_combo)
        osvjezi_btn = QPushButton("🔄  Osvježi")
        osvjezi_btn.clicked.connect(self.osvjezi)
        gore.addWidget(osvjezi_btn)
        razredi_btn = QPushButton("⚙️  Porezni razredi…")
        razredi_btn.clicked.connect(self._uredi_razrede)
        gore.addWidget(razredi_btn)
        gore.addStretch()
        glavni.addLayout(gore)

        self.podsjetnik_label = QLabel("")
        self.podsjetnik_label.setWordWrap(True)
        self.podsjetnik_label.setStyleSheet("font-weight: bold;")
        glavni.addWidget(self.podsjetnik_label)

        # obračun
        obracun = QGroupBox("Obračun (redom kojim se upisuje u ePoreznu)")
        mreza = QGridLayout(obracun)
        self._vrijednosti = []          # (QLabel, ključ)
        nazivi = [
            ("Primici naplaćeni u gotovini (uključujući kartice)", "gotovina"),
            ("Primici naplaćeni bezgotovinskim putem", "bezgotovinski"),
            ("Ukupno naplaćeni primici", "ukupno"),
            ("Porezni razred", "razred"),
            ("Godišnji paušalni porez na dohodak", "porez"),
            ("Tromjesečni predujam", "kvartalni_predujam"),
        ]
        for red, (naziv, kljuc) in enumerate(nazivi):
            mreza.addWidget(QLabel(naziv), red, 0)
            lbl = QLabel("-")
            lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lbl.setStyleSheet("font-weight: bold; font-size: 14px;")
            mreza.addWidget(lbl, red, 1)
            self._vrijednosti.append((lbl, kljuc))
            if kljuc in ("gotovina", "bezgotovinski", "ukupno", "porez"):
                btn = QPushButton("Kopiraj")
                btn.setToolTip("Kopira iznos u obliku za ePoreznu (npr. 1234,56)")
                btn.clicked.connect(lambda _=False, k=kljuc: self._kopiraj(k))
                mreza.addWidget(btn, red, 2)
        mreza.setColumnStretch(0, 1)
        glavni.addWidget(obracun)

        # limit
        self.limit_bar = QProgressBar()
        self.limit_bar.setRange(0, 100)
        self.limit_label = QLabel("")
        glavni.addWidget(self.limit_label)
        glavni.addWidget(self.limit_bar)

        # predujmovi
        pred = QGroupBox("Uplaćeni predujmovi poreza")
        pm = QGridLayout(pred)
        self._predujam_inputi = {}
        for q in (1, 2, 3, 4):
            pm.addWidget(QLabel(f"{q}. kvartal (rok {posd.KVARTALI_ROKOVI[q]})"),
                         0 if q <= 2 else 1, 0 if q % 2 else 2)
            ed = QLineEdit()
            ed.setPlaceholderText("0,00")
            ed.setFixedWidth(110)
            ed.setAlignment(Qt.AlignmentFlag.AlignRight)
            ed.editingFinished.connect(lambda q=q: self._spremi_predujam(q))
            pm.addWidget(ed, 0 if q <= 2 else 1, 1 if q % 2 else 3)
            self._predujam_inputi[q] = ed
        self.uplaceno_label = QLabel("")
        self.razlika_label = QLabel("")
        self.razlika_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        pm.addWidget(self.uplaceno_label, 2, 0, 1, 2)
        pm.addWidget(self.razlika_label, 2, 2, 1, 2)
        glavni.addWidget(pred)

        # provjere
        prov = QGroupBox("Provjere prije predaje")
        pv = QVBoxLayout(prov)
        self.provjere_lista = QListWidget()
        self.provjere_lista.setWordWrap(True)
        self.provjere_lista.setMinimumHeight(120)
        pv.addWidget(self.provjere_lista)
        glavni.addWidget(prov, 1)

        # izlazi
        dolje = QHBoxLayout()
        pdf_btn = QPushButton("📄  Izvezi PDF")
        pdf_btn.clicked.connect(self._izvezi_pdf)
        paket_btn = QPushButton("📦  Arhivski paket godine")
        paket_btn.clicked.connect(self._izvezi_paket)
        dolje.addWidget(pdf_btn)
        dolje.addWidget(paket_btn)
        dolje.addStretch()
        glavni.addLayout(dolje)

        napomena = QLabel(
            "Pripremni obračun iz KPR-a (naplaćeni primici). Aplikacija ne "
            "predaje PO-SD; brojke prepiši u ePoreznu. Razrede i iznose "
            "provjeri s Poreznom upravom ili knjigovođom.")
        napomena.setWordWrap(True)
        napomena.setStyleSheet("color: gray; font-size: 11px;")
        glavni.addWidget(napomena)

        self._popuni_godine()
        self._gradnja = False
        self.osvjezi()

    # ── pomoćno ──────────────────────────────────────────────────────────────

    def _godina(self):
        try:
            return int(self.godina_combo.currentText())
        except ValueError:
            return date.today().year

    def _popuni_godine(self):
        trenutna = date.today().year
        godine = {trenutna, trenutna - 1}
        for (g,) in self._conn.execute("SELECT DISTINCT godina FROM kpr"):
            godine.add(g)
        self.godina_combo.blockSignals(True)
        self.godina_combo.clear()
        for g in sorted(godine, reverse=True):
            self.godina_combo.addItem(str(g))
        # zadano: godina za koju se PO-SD upravo predaje, a u siječnju prošla
        zadana = trenutna - 1 if (date.today().month == 1 and
                                  date.today().day <= 15) else trenutna
        self.godina_combo.setCurrentText(str(zadana))
        self.godina_combo.blockSignals(False)

    def _osvjezi_ako_spremno(self, _=None):
        if not self._gradnja:
            self.osvjezi()

    def _demo(self):
        try:
            return bool(self._config_getter().get("demo_mode", False))
        except Exception:
            return False

    # ── osvježavanje ─────────────────────────────────────────────────────────

    def osvjezi(self):
        godina = self._godina()
        rez = posd.izracunaj(self._conn, godina, demo=self._demo())
        self._rez = rez

        for lbl, kljuc in self._vrijednosti:
            v = rez[kljuc]
            if kljuc == "razred":
                lbl.setText(str(v) if v else "iznad limita")
            else:
                lbl.setText(posd.fmt_cent(v) + " EUR")

        self.limit_bar.setValue(min(100, int(round(rez["postotak_limita"]))))
        self.limit_label.setText(
            f"Limit primitaka {posd.fmt_cent(rez['limit'])} EUR: iskorišteno "
            f"{rez['postotak_limita']:.1f} % ({rez['status_limita']})")

        for q, ed in self._predujam_inputi.items():
            if not ed.hasFocus():
                iznos = rez["predujmovi"][q]
                ed.setText(posd.fmt_cent(iznos) if iznos else "")
        self.uplaceno_label.setText(
            f"Ukupno uplaćeno: {posd.fmt_cent(rez['uplaceno'])} EUR")
        r = rez["razlika"]
        self.razlika_label.setText(
            f"Razlika za uplatu: {posd.fmt_cent(r)} EUR" if r >= 0
            else f"Povrat: {posd.fmt_cent(-r)} EUR")

        self.provjere_lista.clear()
        if not rez["provjere"]:
            self.provjere_lista.addItem("✅  Nema nalaza.")
        for n in rez["provjere"]:
            tekst = f"{_IKONE[n['razina']]}  {n['naslov']}"
            if n["detalj"]:
                tekst += f"\n      {n['detalj']}"
            self.provjere_lista.addItem(QListWidgetItem(tekst))

        self.podsjetnik_label.setText(posd.podsjetnik())
        self.podsjetnik_label.setVisible(bool(self.podsjetnik_label.text()))

    def _uredi_razrede(self, _=None):
        RazrediDialog(self._conn, self._godina(), parent=self).exec()
        self.osvjezi()

    def _kopiraj(self, kljuc):
        if self._rez is None:
            return
        QGuiApplication.clipboard().setText(posd.cent_za_unos(self._rez[kljuc]))

    def _spremi_predujam(self, kvartal):
        ed = self._predujam_inputi[kvartal]
        try:
            iznos = u_centima(parse_iznos(ed.text() or "0"))
            if iznos < 0:
                raise ValueError
        except ValueError:
            QMessageBox.warning(self, "PO-SD", "Upiši iznos, npr. 50,85.")
            self.osvjezi()
            return
        posd.spremi_predujam(self._conn, self._godina(), kvartal, iznos)
        self.osvjezi()

    # ── izlazi ───────────────────────────────────────────────────────────────

    def _izdavatelj(self):
        cfg = self._config_getter() or {}
        return cfg.get("naziv_tvrtke", ""), cfg.get("oib", "")

    def _izvezi_pdf(self, _=None, putanja=None):
        self.osvjezi()
        godina = self._godina()
        if putanja is None:
            putanja, _ = QFileDialog.getSaveFileName(
                self, "Spremi PO-SD obračun", f"PO-SD_{godina}.pdf",
                "PDF datoteke (*.pdf)")
        if not putanja:
            return None
        naziv, oib = self._izdavatelj()
        try:
            posd.napravi_pdf(self._rez, putanja, naziv, oib)
        except Exception as e:
            QMessageBox.critical(self, "PO-SD", f"PDF nije izrađen:\n{e}")
            return None
        QMessageBox.information(self, "PO-SD", f"PDF je spremljen:\n{putanja}")
        return putanja

    def _izvezi_paket(self, _=None, putanja=None):
        self.osvjezi()
        godina = self._godina()
        if putanja is None:
            putanja, _ = QFileDialog.getSaveFileName(
                self, "Spremi arhivski paket", f"arhiva_{godina}.zip",
                "ZIP datoteke (*.zip)")
        if not putanja:
            return None
        from postavke import BASE_DIR
        naziv, oib = self._izdavatelj()
        privremeni = putanja + ".posd.pdf"
        try:
            posd.napravi_pdf(self._rez, privremeni, naziv, oib)
            broj = posd.arhivski_paket(self._conn, godina, BASE_DIR, putanja,
                                       pdf_putanja=privremeni)
        except Exception as e:
            QMessageBox.critical(self, "PO-SD", f"Paket nije izrađen:\n{e}")
            return None
        finally:
            if os.path.exists(privremeni):
                os.remove(privremeni)
        QMessageBox.information(
            self, "PO-SD",
            f"Arhivski paket ({broj} datoteka) je spremljen:\n{putanja}")
        return putanja


class RazrediDialog(QDialog):
    """Pregled i uređivanje poreznih razreda po godini."""

    NASLOVI = ["Razred", "Od (EUR)", "Do (EUR)",
               "Godišnji porez (EUR)", "Predujam / kvartal (EUR)"]

    def __init__(self, conn, godina, parent=None):
        super().__init__(parent)
        self._conn = conn
        self._ucitavanje = False
        self.setWindowTitle("Porezni razredi")
        self.setMinimumSize(760, 460)

        glavni = QVBoxLayout(self)
        gore = QHBoxLayout()
        gore.addWidget(QLabel("Godina:"))
        self.godina_combo = QComboBox()
        gore.addWidget(self.godina_combo)
        nova_btn = QPushButton("➕  Nova godina (kopija)")
        nova_btn.clicked.connect(self._nova_godina)
        gore.addWidget(nova_btn)
        brisi_btn = QPushButton("🗑  Obriši godinu")
        brisi_btn.clicked.connect(self._obrisi_godinu)
        gore.addWidget(brisi_btn)
        gore.addStretch()
        glavni.addLayout(gore)

        self.tabla = QTableWidget(0, len(self.NASLOVI))
        self.tabla.setHorizontalHeaderLabels(self.NASLOVI)
        zaglavlje = self.tabla.horizontalHeader()
        zaglavlje.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.tabla.itemChanged.connect(self._promijenjeno)
        glavni.addWidget(self.tabla, 1)

        red = QHBoxLayout()
        dodaj = QPushButton("➕ Dodaj razred")
        dodaj.clicked.connect(self._dodaj_razred)
        ukloni = QPushButton("➖ Ukloni zadnji")
        ukloni.clicked.connect(self._ukloni_zadnji)
        izracun = QPushButton("Porez = gornja granica × 1,8 %")
        izracun.setToolTip("Popunjava godišnji porez prema pravilu koje "
                           "vrijedi za razrede 2026.")
        izracun.clicked.connect(self._izracunaj_poreze)
        for b in (dodaj, ukloni, izracun):
            red.addWidget(b)
        red.addStretch()
        glavni.addLayout(red)

        self.poruka = QLabel("")
        self.poruka.setWordWrap(True)
        glavni.addWidget(self.poruka)
        self.izvor_label = QLabel("")
        self.izvor_label.setStyleSheet("color: gray; font-size: 11px;")
        glavni.addWidget(self.izvor_label)

        dno = QHBoxLayout()
        dno.addStretch()
        spremi = QPushButton("💾  Spremi")
        spremi.clicked.connect(self._spremi)
        zatvori = QPushButton("Zatvori")
        zatvori.clicked.connect(self.accept)
        dno.addWidget(spremi)
        dno.addWidget(zatvori)
        glavni.addLayout(dno)

        self._popuni_godine(godina)
        self.godina_combo.currentTextChanged.connect(self._ucitaj)
        self._ucitaj()

    # ── učitavanje ───────────────────────────────────────────────────────────

    def _godina(self):
        try:
            return int(self.godina_combo.currentText())
        except ValueError:
            return None

    def _popuni_godine(self, zeljena):
        godine = posd.godine_razreda(self._conn)
        self.godina_combo.blockSignals(True)
        self.godina_combo.clear()
        for g in godine:
            self.godina_combo.addItem(str(g))
        if zeljena in godine:
            self.godina_combo.setCurrentText(str(zeljena))
        self.godina_combo.blockSignals(False)

    def _ucitaj(self, _=None):
        godina = self._godina()
        lista = posd.dohvati_razrede(self._conn, godina) if godina else []
        self._postavi_tablicu(lista)
        izvor = self._conn.execute(
            "SELECT izvor FROM porezni_razredi WHERE godina=? LIMIT 1",
            (godina,)).fetchone()
        self.izvor_label.setText(f"Izvor: {izvor[0]}" if izvor and izvor[0] else "")

    def _postavi_tablicu(self, lista):
        self._ucitavanje = True
        self.tabla.setRowCount(0)
        for r in lista:
            self._dodaj_red(r["do"], r["porez"])
        self._ucitavanje = False
        self._preracunaj()

    def _dodaj_red(self, do_cent, porez_cent):
        red = self.tabla.rowCount()
        self.tabla.insertRow(red)
        for stupac in range(len(self.NASLOVI)):
            it = QTableWidgetItem("")
            it.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if stupac in (0, 1, 4):
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.tabla.setItem(red, stupac, it)
        self.tabla.item(red, 2).setText(posd.fmt_cent(do_cent) if do_cent else "")
        self.tabla.item(red, 3).setText(posd.fmt_cent(porez_cent))

    # ── čitanje tablice ──────────────────────────────────────────────────────

    def _procitaj(self):
        """Lista {do, porez} iz tablice ili ValueError s porukom retka."""
        lista = []
        for red in range(self.tabla.rowCount()):
            try:
                do = u_centima(parse_iznos(self.tabla.item(red, 2).text()))
                porez = u_centima(parse_iznos(self.tabla.item(red, 3).text() or "0"))
            except ValueError:
                raise ValueError(f"Razred {red + 1}: upiši iznose, npr. 11.300,00.")
            lista.append(dict(do=do, porez=porez))
        return lista

    def _preracunaj(self):
        """Redni brojevi, donje granice i predujmovi (izvedeni stupci)."""
        self._ucitavanje = True
        prethodni_do = None
        for red in range(self.tabla.rowCount()):
            self.tabla.item(red, 0).setText(str(red + 1))
            try:
                do = u_centima(parse_iznos(self.tabla.item(red, 2).text()))
            except ValueError:
                do = None
            self.tabla.item(red, 1).setText(
                posd.fmt_cent(0 if prethodni_do is None else prethodni_do + 1)
                if (red == 0 or prethodni_do is not None) else "")
            try:
                porez = u_centima(parse_iznos(self.tabla.item(red, 3).text() or "0"))
                self.tabla.item(red, 4).setText(posd.fmt_cent(porez // 4))
            except ValueError:
                self.tabla.item(red, 4).setText("")
            prethodni_do = do
        self._ucitavanje = False
        self._prikazi_provjeru()

    def _prikazi_provjeru(self):
        try:
            lista = posd.uskladi_razrede(self._procitaj())
        except ValueError as e:
            self.poruka.setText(f"❌ {e}")
            return
        greske, upoz = posd.provjeri_razrede(lista)
        dijelovi = [f"❌ {g}" for g in greske] + [f"⚠️ {u}" for u in upoz]
        self.poruka.setText("\n".join(dijelovi))

    def _promijenjeno(self, _item):
        if not self._ucitavanje:
            self._preracunaj()

    # ── akcije ───────────────────────────────────────────────────────────────

    def _dodaj_razred(self):
        zadnji = 0
        if self.tabla.rowCount():
            try:
                zadnji = u_centima(parse_iznos(
                    self.tabla.item(self.tabla.rowCount() - 1, 2).text()))
            except ValueError:
                pass
        self._ucitavanje = True
        self._dodaj_red(0, 0)
        self._ucitavanje = False
        self._preracunaj()

    def _ukloni_zadnji(self):
        if self.tabla.rowCount():
            self.tabla.removeRow(self.tabla.rowCount() - 1)
            self._preracunaj()

    def _izracunaj_poreze(self):
        self._ucitavanje = True
        for red in range(self.tabla.rowCount()):
            try:
                do = u_centima(parse_iznos(self.tabla.item(red, 2).text()))
            except ValueError:
                continue
            self.tabla.item(red, 3).setText(posd.fmt_cent(do * 18 // 1000))
        self._ucitavanje = False
        self._preracunaj()

    def _spremi(self):
        godina = self._godina()
        if not godina:
            return
        try:
            posd.spremi_razrede(self._conn, godina, self._procitaj())
        except ValueError as e:
            QMessageBox.warning(self, "Porezni razredi", str(e))
            return
        self._ucitaj()
        QMessageBox.information(self, "Porezni razredi",
                                f"Razredi za {godina}. su spremljeni.")

    def _nova_godina(self):
        postojece = posd.godine_razreda(self._conn)
        if not postojece:
            return
        iz = self._godina() or postojece[0]
        godina, ok = QInputDialog.getInt(
            self, "Nova godina", f"Godina (kopija razreda iz {iz}.):",
            max(postojece) + 1, 2000, 2100)
        if not ok:
            return
        try:
            posd.kopiraj_razrede(self._conn, iz, godina)
        except ValueError as e:
            QMessageBox.warning(self, "Porezni razredi", str(e))
            return
        self._popuni_godine(godina)
        self._ucitaj()

    def _obrisi_godinu(self):
        godina = self._godina()
        if not godina:
            return
        odgovor = QMessageBox.question(
            self, "Porezni razredi", f"Obrisati razrede za {godina}.?")
        if odgovor != QMessageBox.StandardButton.Yes:
            return
        try:
            posd.obrisi_razrede(self._conn, godina)
        except ValueError as e:
            QMessageBox.warning(self, "Porezni razredi", str(e))
            return
        self._popuni_godine(0)
        self._ucitaj()
