"""Tab Etsy: plaćene narudžbe s Etsyja i izdavanje računa za svaku od njih."""
import json

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QComboBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox, QInputDialog,
    QAbstractItemView, QDialog, QDialogButtonBox)

import etsy
import postavke
from format_util import fmt_iznos
from novac import iz_centi

_STATUSI = {etsy.STATUS_NOVA: "🆕 Čeka račun",
            etsy.STATUS_RACUN: "✅ Račun izdan",
            etsy.STATUS_PRESKOCENO: "⏭ Preskočeno"}


class Radnik(QThread):
    """Mrežni poziv izvan glavne niti (sučelje se ne smrzava)."""
    gotovo = Signal(object)
    greska = Signal(str)

    def __init__(self, fn, parent=None):
        super().__init__(parent)
        self._fn = fn

    def run(self):
        try:
            self.gotovo.emit(self._fn())
        except etsy.EtsyGreska as e:
            self.greska.emit(str(e))
        except Exception as e:                       # mreža, JSON...
            self.greska.emit(f"Neočekivana greška: {e}")


class EtsyWidget(QWidget):
    # nacrt računa (dict iz etsy.racun_iz_narudzbe) -> BillingApp
    izdaj_racun = Signal(dict)

    def __init__(self, conn=None, cursor=None, config_getter=None,
                 config_setter=None, parent=None):
        super().__init__(parent)
        if conn is None:
            import db as _db
            conn = _db.conn
        self._conn = conn
        self._config_getter = config_getter or (lambda: {})
        self._config_setter = config_setter or (lambda d: None)
        self._radnik = None
        self._sinkrono = False          # testovi: bez niti

        glavni = QVBoxLayout(self)
        glavni.setContentsMargins(16, 12, 16, 12)

        gore = QHBoxLayout()
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("font-weight: bold;")
        gore.addWidget(self.status_label)
        gore.addStretch()
        gore.addWidget(QLabel("Trgovina:"))
        self.trgovina_combo = QComboBox()
        self.trgovina_combo.currentIndexChanged.connect(self.osvjezi)
        gore.addWidget(self.trgovina_combo)
        gore.addWidget(QLabel("Zadnjih:"))
        self.dana_combo = QComboBox()
        for dana in (7, 30, 90):
            self.dana_combo.addItem(f"{dana} dana", dana)
        self.dana_combo.setCurrentIndex(1)
        gore.addWidget(self.dana_combo)
        self.povezi_btn = QPushButton("🔗  Dodaj / poveži trgovinu")
        self.povezi_btn.clicked.connect(self.povezi)
        gore.addWidget(self.povezi_btn)
        self.dohvati_btn = QPushButton("🔄  Dohvati narudžbe")
        self.dohvati_btn.clicked.connect(self.dohvati)
        gore.addWidget(self.dohvati_btn)
        glavni.addLayout(gore)

        filtar = QHBoxLayout()
        filtar.addWidget(QLabel("Prikaži:"))
        self.filtar_combo = QComboBox()
        self.filtar_combo.addItem("Sve narudžbe", None)
        self.filtar_combo.addItem("Čekaju račun", etsy.STATUS_NOVA)
        self.filtar_combo.addItem("Račun izdan", etsy.STATUS_RACUN)
        self.filtar_combo.addItem("Preskočene", etsy.STATUS_PRESKOCENO)
        self.filtar_combo.setCurrentIndex(1)
        self.filtar_combo.currentIndexChanged.connect(self.osvjezi)
        filtar.addWidget(self.filtar_combo)
        filtar.addStretch()
        glavni.addLayout(filtar)

        self.tabla = QTableWidget(0, 8)
        self.tabla.setHorizontalHeaderLabels(
            ["Narudžba", "Datum", "Kupac", "Država", "Iznos", "Status", "Račun",
             "Trgovina"])
        self.tabla.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tabla.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tabla.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tabla.verticalHeader().setVisible(False)
        zag = self.tabla.horizontalHeader()
        zag.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        zag.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.tabla.itemSelectionChanged.connect(self._selekcija)
        self.tabla.doubleClicked.connect(lambda _=None: self.izdaj())
        glavni.addWidget(self.tabla, 1)

        dolje = QHBoxLayout()
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: gray; font-size: 11px;")
        dolje.addWidget(self.info_label)
        dolje.addStretch()
        self.postojeci_btn = QPushButton("🔗  Račun već izdan…")
        self.postojeci_btn.setToolTip("Veži već izdani račun uz ovu narudžbu "
                                      "(ili odveži ako je pogrešno vezan)")
        self.postojeci_btn.clicked.connect(self.postojeci_racun)
        dolje.addWidget(self.postojeci_btn)
        self.preskoci_btn = QPushButton("⏭  Preskoči")
        self.preskoci_btn.clicked.connect(self.preskoci)
        dolje.addWidget(self.preskoci_btn)
        self.izdaj_btn = QPushButton("🧾  Izdaj račun")
        self.izdaj_btn.setObjectName("saveBtn")
        self.izdaj_btn.clicked.connect(self.izdaj)
        dolje.addWidget(self.izdaj_btn)
        glavni.addLayout(dolje)

        napomena = QLabel(
            "Račun se ne izdaje sam: za svaku narudžbu se učita nacrt u 'Novi "
            "račun', a ti ga pregledaš i fiskaliziraš. Etsyjev dokument kupcu ne "
            "zamjenjuje tvoj račun. Račun je transakcijski: naplatu označi u "
            "Pregledu računa kad Etsy isplati novac.")
        napomena.setWordWrap(True)
        napomena.setStyleSheet("color: gray; font-size: 11px;")
        glavni.addWidget(napomena)

        self.osvjezi()

    # ── pomoćno ──────────────────────────────────────────────────────────────

    def _cfg(self):
        return self._config_getter() or {}

    def _sve_tokene(self):
        """{shop_id(str): {"tokeni": {...}}} za sve povezane trgovine."""
        return etsy.tokeni_trgovina(
            postavke._keyring_dohvati(etsy.KEYRING_TOKENI),
            self._cfg().get("etsy_shop_id") or None)

    def _spremi_tokene_trgovine(self, shop_id):
        def spremi(tokeni):
            sve = self._sve_tokene()
            sve[str(shop_id)] = {"tokeni": tokeni}
            if not postavke._keyring_spremi(json.dumps(sve), etsy.KEYRING_TOKENI):
                raise etsy.EtsyGreska(
                    "Tokeni se ne mogu spremiti u Credential Manager (je li keyring "
                    "instaliran?).")
        return spremi

    def odspoji_trgovinu(self, shop_id):
        """Briše tokene trgovine iz Credential Managera. Narudžbe i računi ostaju."""
        raw = postavke._keyring_dohvati(etsy.KEYRING_TOKENI)
        novo = etsy.odspoji_tokene(raw, shop_id, self._cfg().get("etsy_shop_id") or None)
        if not postavke._keyring_spremi(novo, etsy.KEYRING_TOKENI):
            raise etsy.EtsyGreska("Tokeni se ne mogu ukloniti iz Credential Managera.")
        if str(self._cfg().get("etsy_shop_id")) == str(shop_id):
            self._config_setter({"etsy_shop_id": ""})
        self.osvjezi()

    def _kljuc(self):
        kljuc = self._cfg().get("etsy_api_kljuc", "").strip()
        if not kljuc:
            raise etsy.EtsyGreska("Upiši Etsy API ključ u Postavke.")
        return kljuc

    def _klijent(self, shop_id, tokeni=None):
        if tokeni is None:
            tokeni = self._sve_tokene().get(str(shop_id), {}).get("tokeni", {})
        return etsy.EtsyKlijent(self._kljuc(), tokeni,
                                self._spremi_tokene_trgovine(shop_id))

    def _povezane(self):
        """[(shop_id, naziv)] trgovina za koje postoje tokeni."""
        imena = dict(etsy.popis_trgovina(self._conn))
        return [(int(sid), imena.get(int(sid), ""))
                for sid, v in self._sve_tokene().items()
                if str(sid).isdigit() and v.get("tokeni", {}).get("access_token")]

    def _pokreni(self, fn, uspjeh):
        """fn radi u niti (samo mreža); uspjeh(rezultat) u glavnoj niti."""
        if self._sinkrono:
            try:
                uspjeh(fn())
            except etsy.EtsyGreska as e:
                self._greska(str(e))
            return
        if self._radnik is not None and self._radnik.isRunning():
            return
        self._radnik = Radnik(fn, self)
        self._radnik.gotovo.connect(uspjeh)
        self._radnik.greska.connect(self._greska)
        self._radnik.finished.connect(lambda: self._zauzeto(False))
        self._zauzeto(True)
        self._radnik.start()

    def _zauzeto(self, da):
        for b in (self.povezi_btn, self.dohvati_btn):
            b.setEnabled(not da)
        if da:
            self.status_label.setText("⏳ Komunikacija s Etsyjem…")
        else:
            self._prikazi_status()

    def _greska(self, poruka):
        self._prikazi_status()
        QMessageBox.warning(self, "Etsy", poruka)

    def _prikazi_status(self):
        if not self._cfg().get("etsy_api_kljuc"):
            self.status_label.setText("⚠️ Etsy API ključ nije postavljen (Postavke)")
            return
        povezane = self._povezane()
        if not povezane:
            self.status_label.setText("⚠️ Nije povezano s Etsyjem")
        else:
            imena = ", ".join(n or str(i) for i, n in povezane)
            self.status_label.setText(f"✅ Povezano: {imena}")

    def _osvjezi_trgovine(self):
        """Popuni combo trgovina (zadrži odabir)."""
        trenutna = self.trgovina_combo.currentData()
        self.trgovina_combo.blockSignals(True)
        self.trgovina_combo.clear()
        self.trgovina_combo.addItem("Sve trgovine", None)
        vidjeni = set()
        for sid, naziv in etsy.popis_trgovina(self._conn):
            self.trgovina_combo.addItem(naziv or str(sid), sid)
            vidjeni.add(sid)
        for (sid,) in self._conn.execute(
                "SELECT DISTINCT shop_id FROM etsy_narudzbe WHERE shop_id IS NOT NULL"):
            if sid not in vidjeni:
                self.trgovina_combo.addItem(str(sid), sid)
        i = self.trgovina_combo.findData(trenutna)
        self.trgovina_combo.setCurrentIndex(max(i, 0))
        self.trgovina_combo.blockSignals(False)
        self.trgovina_combo.setVisible(self.trgovina_combo.count() > 2)
        self.tabla.setColumnHidden(7, self.trgovina_combo.count() <= 2)

    # ── povezivanje i dohvat ─────────────────────────────────────────────────

    def povezi(self, _=None):
        try:
            kljuc = self._kljuc()
        except etsy.EtsyGreska as e:
            self._greska(str(e))
            return
        redirect = self._cfg().get("etsy_redirect") or etsy.ZADANI_REDIRECT

        def posao():
            tokeni = etsy.prijava(kljuc, redirect)
            k = etsy.EtsyKlijent(kljuc, tokeni, lambda t: None)
            shop_id, naziv = k.trgovina()
            self._spremi_tokene_trgovine(shop_id)(tokeni)
            return shop_id, naziv

        def uspjeh(trgovina):
            shop_id, naziv = trgovina
            etsy.spremi_trgovinu(self._conn, shop_id, naziv)
            self._config_setter({"etsy_shop_id": str(shop_id)})
            self.osvjezi()
            QMessageBox.information(
                self, "Etsy",
                f"Povezano s trgovinom {naziv or shop_id}.\n\nZa dodavanje druge "
                "trgovine prvo se odjavi s Etsyja u pregledniku (ili se prijavi na "
                "drugi Etsy račun), pa ponovno klikni 'Dodaj / poveži trgovinu'.")
        self._pokreni(posao, uspjeh)

    def dohvati(self, _=None):
        povezane = self._povezane()
        odabrana = self.trgovina_combo.currentData()
        if odabrana:
            povezane = [p for p in povezane if p[0] == odabrana]
        if not povezane:
            self._greska("Nema povezane trgovine. Klikni 'Dodaj / poveži trgovinu'.")
            return
        try:
            klijenti = [(sid, naziv, self._klijent(sid)) for sid, naziv in povezane]
        except etsy.EtsyGreska as e:
            self._greska(str(e))
            return
        dana = self.dana_combo.currentData()

        def posao():
            rezultat, greske, imena = [], [], {}
            for sid, naziv, k in klijenti:
                try:
                    if not naziv:       # povezano prije spremanja imena
                        try:
                            imena[sid] = k.trgovina()[1]
                        except etsy.EtsyGreska:
                            pass
                    rezultat.append((sid, k.placene_narudzbe(sid, dana)))
                except etsy.EtsyGreska as e:
                    greske.append(f"{naziv or sid}: {e}")
            return rezultat, greske, imena

        def uspjeh(odgovor):
            rezultat, greske, imena = odgovor
            for sid, naziv in imena.items():
                if naziv:
                    etsy.spremi_trgovinu(self._conn, sid, naziv)
            ukupno = novih = 0
            for sid, narudzbe in rezultat:
                ukupno += len(narudzbe)
                novih += etsy.spremi_narudzbe(self._conn, narudzbe, sid)
            self.osvjezi()
            self.info_label.setText(
                f"Dohvaćeno {ukupno} narudžbi ({len(rezultat)} trg.), novih: {novih}.")
            if greske:
                self._greska("Dohvat nije uspio za:\n" + "\n".join(greske))
        self._pokreni(posao, uspjeh)

    # ── popis ────────────────────────────────────────────────────────────────

    def osvjezi(self, _=None):
        self._prikazi_status()
        self._osvjezi_trgovine()
        redovi = etsy.popis_narudzbi(self._conn, self.filtar_combo.currentData(),
                                     self.trgovina_combo.currentData())
        brojevi = {}
        for r in redovi:
            if r["invoice_id"]:
                red = self._conn.execute(
                    "SELECT broj_racuna FROM invoices WHERE id=?",
                    (r["invoice_id"],)).fetchone()
                brojevi[r["invoice_id"]] = red[0] if red else ""
        self.tabla.setRowCount(0)
        for r in redovi:
            row = self.tabla.rowCount()
            self.tabla.insertRow(row)
            iznos = f"{fmt_iznos(iz_centi(r['iznos_cent']))} {r['valuta']}"
            for stupac, tekst in enumerate((
                    str(r["receipt_id"]), r["datum"], r["kupac"], r["drzava"], iznos,
                    _STATUSI.get(r["status"], r["status"]),
                    brojevi.get(r["invoice_id"], ""),
                    r["shop_naziv"] or (str(r["shop_id"]) if r["shop_id"] else ""))):
                it = QTableWidgetItem(tekst)
                if stupac == 4:
                    it.setTextAlignment(Qt.AlignmentFlag.AlignRight |
                                        Qt.AlignmentFlag.AlignVCenter)
                if stupac == 0:
                    it.setData(Qt.ItemDataRole.UserRole, r["receipt_id"])
                    it.setData(Qt.ItemDataRole.UserRole + 1, r["status"])
                self.tabla.setItem(row, stupac, it)
        self._selekcija()

    def _odabrana(self):
        red = self.tabla.currentRow()
        if red < 0 or self.tabla.item(red, 0) is None:
            return None, None
        it = self.tabla.item(red, 0)
        return (it.data(Qt.ItemDataRole.UserRole),
                it.data(Qt.ItemDataRole.UserRole + 1))

    def _selekcija(self):
        rid, status = self._odabrana()
        self.izdaj_btn.setEnabled(rid is not None and status != etsy.STATUS_RACUN)
        self.postojeci_btn.setEnabled(rid is not None)
        self.postojeci_btn.setText("🔗  Odveži račun" if status == etsy.STATUS_RACUN
                                   else "🔗  Račun već izdan…")
        self.preskoci_btn.setEnabled(rid is not None and status != etsy.STATUS_RACUN)
        self.preskoci_btn.setText("↩  Vrati" if status == etsy.STATUS_PRESKOCENO
                                  else "⏭  Preskoči")

    # ── akcije ───────────────────────────────────────────────────────────────

    def preskoci(self, _=None):
        rid, status = self._odabrana()
        if rid is None:
            return
        etsy.preskoci(self._conn, rid, status != etsy.STATUS_PRESKOCENO)
        self.osvjezi()

    def postojeci_racun(self, _=None):
        rid, status = self._odabrana()
        if rid is None:
            return
        if status == etsy.STATUS_RACUN:
            if QMessageBox.question(
                    self, "Odveži račun",
                    "Narudžba će se vratiti među 'Čekaju račun'. Sam račun se ne briše.",
                    ) == QMessageBox.StandardButton.Yes:
                etsy.odvezi_racun(self._conn, rid)
                self.osvjezi()
            return
        racuni = etsy.nepovezani_racuni(self._conn)
        if not racuni:
            QMessageBox.information(self, "Etsy", "Nema računa koji nisu vezani uz narudžbu.")
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Koji je račun izdan za narudžbu {rid}?")
        dlg.resize(640, 420)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Odaberi već izdani račun za ovu narudžbu:"))
        t = QTableWidget(len(racuni), 4)
        t.setHorizontalHeaderLabels(["Račun", "Datum", "Kupac", "Iznos (EUR)"])
        t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        t.verticalHeader().setVisible(False)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for i, r in enumerate(racuni):
            for j, v in enumerate((r["broj"] or str(r["id"]), r["datum"] or "",
                                   r["kupac"] or "",
                                   fmt_iznos(iz_centi(r["iznos_cent"] or 0)))):
                it = QTableWidgetItem(v)
                if j == 0:
                    it.setData(Qt.ItemDataRole.UserRole, r["id"])
                t.setItem(i, j, it)
        t.selectRow(0)
        t.doubleClicked.connect(lambda _=None: dlg.accept())
        lay.addWidget(t, 1)
        gumbi = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                 QDialogButtonBox.StandardButton.Cancel)
        gumbi.accepted.connect(dlg.accept)
        gumbi.rejected.connect(dlg.reject)
        lay.addWidget(gumbi)
        if dlg.exec() != QDialog.DialogCode.Accepted or t.currentRow() < 0:
            return
        invoice_id = t.item(t.currentRow(), 0).data(Qt.ItemDataRole.UserRole)
        try:
            etsy.poveži_postojeci(self._conn, rid, invoice_id)
        except ValueError as e:
            self._greska(str(e))
            return
        self.osvjezi()

    def izdaj(self, _=None):
        rid, status = self._odabrana()
        if rid is None or status == etsy.STATUS_RACUN:
            return
        narudzba = etsy.dohvati_narudzbu(self._conn, rid)
        if not narudzba:
            self._greska("Narudžba nije u bazi. Dohvati narudžbe ponovno.")
            return
        tecaj = None
        valuta = etsy._valuta(narudzba)
        if valuta != etsy.NASA_VALUTA:
            tecaj, ok = QInputDialog.getDouble(
                self, "Tečaj",
                f"Narudžba je u valuti {valuta}.\nTečaj (EUR za 1 {valuta}):",
                1.0, 0.000001, 1000000.0, 6)
            if not ok:
                return
        try:
            racun = etsy.racun_iz_narudzbe(narudzba, tecaj)
        except etsy.EtsyGreska as e:
            self._greska(str(e))
            return
        if racun["upozorenja"]:
            odgovor = QMessageBox.question(
                self, "Provjeri prije izdavanja",
                "\n\n".join(racun["upozorenja"]) + "\n\nNastaviti i učitati nacrt računa?")
            if odgovor != QMessageBox.StandardButton.Yes:
                return
        self.izdaj_racun.emit(racun)
