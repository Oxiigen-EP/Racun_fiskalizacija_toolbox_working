"""
detalji_racuna.py — Dijalog za pregled detalja odabranog računa + storno

INTEGRACIJA U GLAVNU DATOTEKU:
================================

1) Na vrh, uz ostale importe:
   from detalji_racuna import DetaljiRacunaDialog

2) U PregledRacunaWidget.__init__, ZAMIJENI:
       layout.addWidget(self.tabla)
   SA:
       layout.addWidget(self.tabla)
       self.tabla.doubleClicked.connect(self._otvori_detalje)
       self.tabla.selectionModel().selectionChanged.connect(self._on_selekcija)

3) U statusnoj traci (status_layout), ZAMIJENI:
       status_layout = QHBoxLayout()
       self.status_label = QLabel("")
       self.status_label.setStyleSheet("color: #7f8c8d; font-size: 10px;")
       status_layout.addWidget(self.status_label)
       status_layout.addStretch()
   SA:
       status_layout = QHBoxLayout()
       self.status_label = QLabel("")
       self.status_label.setStyleSheet("color: #7f8c8d; font-size: 10px;")
       status_layout.addWidget(self.status_label)
       self.detalji_btn = QPushButton("🔍  Detalji računa")
       self.detalji_btn.setFixedWidth(150)
       self.detalji_btn.setEnabled(False)
       self.detalji_btn.clicked.connect(self._otvori_detalje)
       status_layout.addWidget(self.detalji_btn)
       self.pdf_btn = QPushButton("🖨️  Rekreira PDF")
       self.pdf_btn.setFixedWidth(140)
       self.pdf_btn.setEnabled(False)
       self.pdf_btn.setStyleSheet(
           "QPushButton { background-color: #1565c0; color: white; border: none;"
           " border-radius: 6px; padding: 5px 14px; font-weight: bold; }"
           "QPushButton:hover { background-color: #0d47a1; }"
           "QPushButton:disabled { background-color: #90a4ae; color: #eceff1; }")
       self.pdf_btn.clicked.connect(self._rekreira_pdf)
       status_layout.addWidget(self.pdf_btn)
       status_layout.addStretch()

4) Dodaj ove metode u PregledRacunaWidget:

   def _on_selekcija(self):
       ima = self.tabla.currentRow() >= 0
       self.detalji_btn.setEnabled(ima)
       self.pdf_btn.setEnabled(ima)

   def _otvori_detalje(self):
       row = self.tabla.currentRow()
       if row < 0:
           return
       item = self.tabla.item(row, 0)
       if item is None:
           return
       invoice_id = item.data(Qt.ItemDataRole.UserRole)
       if invoice_id is None:
           return
       billing_app = self.window()
       dlg = DetaljiRacunaDialog(invoice_id, parent=self)
       dlg.storno_zahtjevan.connect(billing_app.prefill_storno)
       dlg.exec()

   def _rekreira_pdf(self):
       row = self.tabla.currentRow()
       if row < 0:
           return
       item = self.tabla.item(row, 0)
       if item is None:
           return
       invoice_id = item.data(Qt.ItemDataRole.UserRole)
       if invoice_id is None:
           return
       dlg = DetaljiRacunaDialog(invoice_id, parent=self)
       dlg._rekreiraj_pdf()

5) U _popuni_tablicu, ZAMIJENI:
       br = broj_racuna or f"{pp or ''}-{nu or ''}-{id_}"
       self._set_item(row, 0, br, align_right=False)
   SA:
       br = broj_racuna or f"{pp or ''}-{nu or ''}-{id_}"
       item_br = QTableWidgetItem(br)
       item_br.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
       item_br.setData(Qt.ItemDataRole.UserRole, id_)
       self.tabla.setItem(row, 0, item_br)

6) Dodaj metodu prefill_storno u klasu BillingApp:

   def prefill_storno(self, podaci: dict):
       \"\"\"Prefilla formu za storno račun i prebacuje na tab 'Novi račun'.\"\"\"
       from PySide6.QtWidgets import QTabWidget
       # Prebaci na tab 0 (Novi račun)
       tab_widget = self.findChild(QTabWidget)
       if tab_widget:
           tab_widget.setCurrentIndex(0)

       # Kupac
       self.kupac_naziv_input.setText(podaci.get('kupac_naziv', ''))
       self.kupac_oib_input.setText(podaci.get('kupac_oib', ''))
       self.kupac_adresa_input.setText(podaci.get('kupac_adresa', ''))
       self.pravna_osoba_check.setChecked(podaci.get('pravna_osoba', False))

       # Način plaćanja
       nacin = podaci.get('nacin_placanja', 'G')
       nacin_map = {'G': 'G - Gotovina', 'K': 'K - Kartica',
                    'T': 'T - Transakcijski račun', 'O': 'O - Ostalo'}
       self.nacin_placanja_combo.setCurrentText(
           nacin_map.get(nacin, 'G - Gotovina'))

       # PDV
       u_pdv = podaci.get('u_sustavu_pdv', False)
       self.u_sustavu_pdv_check.setChecked(u_pdv)
       self.on_pdv_changed()

       # Napomena
       self.napomena_input.setPlainText(podaci.get('napomena', ''))

       # Stavke — negativne
       self.stavke_widget.tabla.setRowCount(0)
       for s in podaci.get('stavke', []):
           self.stavke_widget.dodaj_stavku()
           row = self.stavke_widget.tabla.rowCount() - 1
           self.stavke_widget.tabla.item(row, 0).setText(s['naziv'])
           self.stavke_widget.tabla.item(row, 1).setText(str(s['kolicina']))
           self.stavke_widget.tabla.item(row, 2).setText(s['jedinica'])
           self.stavke_widget.tabla.item(row, 3).setText(
               f"{fmt_iznos(s['cijena'])}")
           combo = self.stavke_widget.tabla.cellWidget(row, 4)
           if combo:
               combo.setCurrentText(str(int(s['pdv_stopa'])))
       self.stavke_widget.azuriraj_ukupno()
"""

import os
from datetime import datetime

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QFrame,
    QScrollArea, QWidget, QGroupBox, QGridLayout, QSizePolicy,
    QMessageBox, QApplication
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont
from format_util import fmt_iznos, parse_iznos
from novac import izracun_stavke


def _tc():
    """Vrati COLORS rječnik aktivne teme iz ThemeManagera (ako postoji)."""
    app = QApplication.instance()
    if app:
        for w in app.topLevelWidgets():
            tm = getattr(w, 'theme_manager', None)
            if tm:
                return tm.colors
    # Fallback — svjetla tema
    from theme_module import LIGHT_COLORS
    return LIGHT_COLORS


class DetaljiRacunaDialog(QDialog):
    """Modalni dijalog s punim pregledom jednog računa i njegovih stavki."""

    # Emitira dict s podacima za prefill storno računa u BillingApp
    storno_zahtjevan = Signal(dict)

    def __init__(self, invoice_id: int, parent=None):
        super().__init__(parent)
        self.invoice_id = invoice_id
        self.setWindowTitle("Detalji računa")
        self.setMinimumSize(750, 620)
        self.setModal(True)

        import __main__ as _m
        self._cursor = _m.cursor
        self._conn = _m.conn
        self._racun = self._dohvati_racun()
        self._stavke = self._dohvati_stavke()

        if self._racun:
            br = self._racun[1] or f"#{invoice_id}"
            self.setWindowTitle(f"Detalji računa — {br}")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Zaglavlje ────────────────────────────────────────────────────────
        header = QWidget()
        header.setStyleSheet(f"background-color: {_tc()['table_header']};")
        header.setFixedHeight(56)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(18, 0, 18, 0)

        title_lbl = QLabel("📄  Pregled računa")
        title_lbl.setStyleSheet(f"color: white; font-size: 14px; font-weight: bold; background-color: transparent;")
        hl.addWidget(title_lbl)

        hl.addStretch()

        if self._racun:
            iznos = self._racun[14] or 0.0
            iznos_lbl = QLabel(f"{fmt_iznos(iznos)} EUR")
            iznos_lbl.setStyleSheet(
                f"color: {'#ff9e9e' if iznos < 0 else '#69f0ae'}; font-size: 18px; "
                "font-weight: bold; background-color: transparent;")
            hl.addWidget(iznos_lbl)

        outer.addWidget(header)

        # ── Scroll area ───────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setStyleSheet(f"background-color: {_tc()['bg_widget']};")

        content = QWidget()
        #content.setStyleSheet("background-color: #f0f2f5;")
        cl = QVBoxLayout(content)
        cl.setContentsMargins(16, 16, 16, 16)
        cl.setSpacing(12)

        if not self._racun:
            err = QLabel("⚠️  Račun nije pronađen u bazi.")
            err.setStyleSheet(f"color: {_tc()['danger_text']}; font-size: 13px; padding: 20px;")
            cl.addWidget(err)
        else:
            self._izgraditi_sadrzaj(cl)

        cl.addStretch()
        scroll.setWidget(content)
        outer.addWidget(scroll)

        # ── Footer s gumbima ─────────────────────────────────────────────────
        footer = QWidget()
        footer.setStyleSheet(
            f"background-color: {_tc()['bg_widget']}; border-top: 1px solid {_tc()['border']};")
        footer.setFixedHeight(52)
        fl = QHBoxLayout(footer)
        fl.setContentsMargins(16, 8, 16, 8)

        # Storno gumb — lijevo, crvenkast, vidljiv samo ako račun nije već storniran
        if self._racun:
            vec_storniran = bool(self._racun[18] and
                                 self._je_storniran(self.invoice_id))
            je_storno = self._je_storno_racun(self._racun[15] or "")

            if not vec_storniran and not je_storno:
                self.storno_btn = QPushButton("↩️  Storniraj račun")
                self.storno_btn.setFixedWidth(170)
                self.storno_btn.setStyleSheet(
                    "QPushButton { background-color: #c0392b; color: white; border: none;"
                    " border-radius: 6px; padding: 7px 16px; font-weight: bold; }"
                    "QPushButton:hover { background-color: #a93226; }"
                    "QPushButton:pressed { background-color: #922b21; }")
                self.storno_btn.clicked.connect(self._pokreni_storno)
                fl.addWidget(self.storno_btn)
            elif vec_storniran:
                info = QLabel("✅  Račun je storniran")
                info.setStyleSheet(f"color: {_tc()['danger_text']}; font-weight: bold; font-size: 11px;")
                fl.addWidget(info)
            elif je_storno:
                info = QLabel("↩️  Ovo je storno račun")
                info.setStyleSheet(f"color: {_tc()['warning']}; font-weight: bold; font-size: 11px;")
                fl.addWidget(info)

        fl.addStretch()

        if self._racun:
            pdf_btn = QPushButton("🖨️  Rekreiraj PDF")
            pdf_btn.setFixedWidth(140)
            pdf_btn.setStyleSheet(
                "QPushButton { background-color: #1565c0; color: white; border: none;"
                " border-radius: 6px; padding: 7px 16px; font-weight: bold; }"
                "QPushButton:hover { background-color: #0d47a1; }"
                "QPushButton:pressed { background-color: #0a3272; }")
            pdf_btn.clicked.connect(self._rekreiraj_pdf)
            fl.addWidget(pdf_btn)

        zatvori_btn = QPushButton("Zatvori")
        zatvori_btn.setObjectName("dangerBtn")
        zatvori_btn.setFixedWidth(110)
        zatvori_btn.clicked.connect(self.accept)
        fl.addWidget(zatvori_btn)

        outer.addWidget(footer)

    # ── Baza ──────────────────────────────────────────────────────────────────

    def _dohvati_racun(self):
        try:
            return self._cursor.execute("""
                SELECT
                    id, broj_racuna, datum, rok_placanja, nacin_placanja,
                    oib_izdavatelja, naziv_izdavatelja, adresa_izdavatelja, iban,
                    oib_kupca, naziv_kupca, adresa_kupca,
                    oznaka_pp, oznaka_nu, ukupan_iznos,
                    napomena, jir, zki, fiskaliziran, datum_fiskalizacije,
                    u_sustavu_pdv, pravna_osoba, operater, datum_kreiranja
                FROM invoices WHERE id=?
            """, (self.invoice_id,)).fetchone()
        except Exception as e:
            print(f"⚠️  Detalji - greška dohvata računa: {e}")
            return None

    def _naplata_tekst(self) -> str:
        try:
            r = self._cursor.execute(
                "SELECT placeno, datum_naplate FROM invoices WHERE id=?",
                (self.invoice_id,)).fetchone()
        except Exception:
            return ""
        if not r:
            return ""
        return f"Naplaćeno {r[1] or ''}".strip() if r[0] else "Čeka naplatu"

    def _dohvati_stavke(self):
        try:
            return self._cursor.execute("""
                SELECT naziv, kolicina, jedinica, cijena, pdv_stopa, popust
                FROM invoice_stavke
                WHERE invoice_id=?
                ORDER BY id
            """, (self.invoice_id,)).fetchall()
        except Exception as e:
            print(f"⚠️  Detalji - greška dohvata stavki: {e}")
            return []

    def _je_storniran(self, invoice_id: int) -> bool:
        """Provjeri postoji li storno račun koji referencira ovaj račun."""
        try:
            r = self._cursor.execute("""
                SELECT COUNT(*) FROM invoices
                WHERE napomena LIKE ?
            """, (f"%Storno računa {self._racun[1]}%",)).fetchone()
            return bool(r and r[0] > 0)
        except Exception:
            return False

    @staticmethod
    def _je_storno_racun(napomena: str) -> bool:
        return napomena.strip().startswith("Storno računa")

    # ── Storno logika ─────────────────────────────────────────────────────────

    def _pokreni_storno(self):
        r = self._racun
        broj_racuna = r[1] or f"#{self.invoice_id}"

        odg = QMessageBox.question(
            self, "Potvrda storna",
            f"Želite li stornirati račun  {broj_racuna}?\n\n"
            f"Bit će kreiran novi račun s negativnim iznosima\n"
            f"koji se treba fiskalizirati.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if odg != QMessageBox.StandardButton.Yes:
            return

        # Označi originalni račun kao storniran u napomeni baze
        stara_napomena = r[15] or ""
        nova_napomena = (stara_napomena + "\n[STORNIRANO]").strip()
        try:
            self._cursor.execute(
                "UPDATE invoices SET napomena=? WHERE id=?",
                (nova_napomena, self.invoice_id)
            )
            self._conn.commit()
        except Exception as e:
            print(f"⚠️  Greška pri označavanju storna: {e}")

        # Pripremi stavke s negativnim cijenama
        storno_stavke = []
        for s in self._stavke:
            naziv, kolicina, jedinica, cijena, pdv_stopa, popust = s
            storno_stavke.append({
                'naziv': f"STORNO — {naziv}",
                'kolicina': kolicina,
                'jedinica': jedinica or 'kom',
                'cijena': -abs(cijena),   # negativna cijena
                'popust': popust or 0,
                'pdv_stopa': pdv_stopa or 0,
            })

        podaci = {
            'kupac_naziv':    r[10] or '',
            'kupac_oib':      r[9] or '',
            'kupac_adresa':   r[11] or '',
            'pravna_osoba':   bool(r[21]),
            'nacin_placanja': r[4] or 'G',
            'u_sustavu_pdv':  bool(r[20]),
            'napomena':       f"Storno računa {broj_racuna}",
            'stavke':         storno_stavke,
        }

        self.storno_zahtjevan.emit(podaci)
        self.accept()   # zatvori dijalog

    # ── Rekreiranje PDF-a ─────────────────────────────────────────────────────

    @staticmethod
    def _normaliziraj_datum_vrijeme(dt_str: str) -> str:
        """Pretvara bilo koji ISO/DB format datuma u dd.mm.yyyy HH:MM:SS
        koji očekuje generiraj_pdf / QR generator."""
        if not dt_str:
            return ''
        formati = [
            "%Y-%m-%dT%H:%M:%S.%f",  # 2026-06-09T13:55:50.932112
            "%Y-%m-%dT%H:%M:%S",      # 2026-06-09T13:55:50
            "%Y-%m-%d %H:%M:%S.%f",   # 2026-06-09 13:55:50.932112
            "%Y-%m-%d %H:%M:%S",      # 2026-06-09 13:55:50
            "%Y-%m-%d",               # 2026-06-09
            "%d.%m.%Y %H:%M:%S",      # 09.06.2026 13:55:50
            "%d.%m.%Y",               # 09.06.2026
        ]
        for fmt in formati:
            try:
                return datetime.strptime(dt_str, fmt).strftime("%d.%m.%Y %H:%M:%S")
            except ValueError:
                pass
        return dt_str  # vrati nepromijenjeno ako ništa ne odgovara

    def _rekreiraj_pdf(self):
        """Regenerira PDF za ovaj račun koristeći podatke iz baze."""
        import __main__ as _m
        r = self._racun
        if not r:
            return

        (db_id, broj_racuna, datum, rok_placanja, nacin_placanja,
         oib_izd, naziv_izd, adresa_izd, iban,
         oib_kup, naziv_kup, adresa_kup,
         pp, nu, ukupan_iznos,
         napomena, jir, zki, fiskaliziran, datum_fisk,
         u_pdv, pravna_osoba, operater, datum_kreiranja) = r

        # Pripremi stavke za PDF
        stavke_za_pdf = []
        for s in self._stavke:
            naziv, kolicina, jedinica, cijena, pdv_stopa, popust = s
            red = izracun_stavke(kolicina, cijena, pdv_stopa or 0, popust or 0)
            stavke_za_pdf.append({
                'naziv':         naziv or '',
                'kolicina':      kolicina,
                'jedinica':      jedinica or 'kom',
                'cijena':        cijena,
                'popust':        red['popust'],
                'popust_iznos':  red['popust_iznos'],
                'pdv_stopa':     pdv_stopa or 0.0,
                'ukupno_bez_pdv': red['osnovica'],
                'pdv_iznos':     red['pdv_iznos'],
                'ukupno':        red['ukupno'],
            })

        # Normaliziraj datum/vrijeme za QR i PDF generator
        datum_vrijeme_norm = self._normaliziraj_datum_vrijeme(
            datum_kreiranja or datum or ''
        )

        # PDV oslobođenje tekst (rekonstruiraj iz indeksa ili koristi fallback)
        pdv_oslobodenje = None
        if not u_pdv:
            try:
                pdv_oslobodenje = _m.PDV_OSLOBODENJE_TEKSTOVI[0]
            except Exception:
                pdv_oslobodenje = "Nije obveznik PDV-a — PDV nije obračunan"

        # Naziv datoteke
        br_clean = (broj_racuna or f"racun_{db_id}").replace("/", "-").replace("\\", "-")
        from PySide6.QtWidgets import QFileDialog
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Spremi PDF računa",
            f"racun_{br_clean}.pdf",
            "PDF datoteke (*.pdf)"
        )
        if not filename:
            return

        try:
            billing_app = self.parent()
            while billing_app and not hasattr(billing_app, 'generate_pdf'):
                billing_app = billing_app.parent() if hasattr(billing_app, 'parent') else None

            if billing_app and hasattr(billing_app, 'generate_pdf'):
                billing_app.generate_pdf(
                    invoice_id=db_id,
                    broj_racuna_str=broj_racuna or f"#{db_id}",
                    datum=datum or '',
                    rok_placanja=rok_placanja or '',
                    nacin_placanja=nacin_placanja or 'G',
                    oib=oib_izd or '',
                    naziv_tvrtke=naziv_izd or '',
                    adresa=adresa_izd or '',
                    iban=iban or '',
                    kupac_naziv=naziv_kup or '',
                    kupac_oib=oib_kup or '',
                    kupac_adresa=adresa_kup or '',
                    oznaka_pp=pp or '',
                    oznaka_nu=nu or '',
                    stavke=stavke_za_pdf,
                    ukupan_iznos=ukupan_iznos or 0.0,
                    u_sustavu_pdv=bool(u_pdv),
                    pdv_oslobodenje=pdv_oslobodenje or "",
                    jir=jir,
                    zki=zki,
                    datum_vrijeme=datum_vrijeme_norm,
                    napomena=napomena or '',
                    operater=operater or '',
                    filename=filename
                )
            else:
                # Fallback: direktan poziv iz __main__
                _m.BillingApp.generate_pdf(
                    _m.BillingApp.__new__(_m.BillingApp),
                    invoice_id=db_id,
                    broj_racuna_str=broj_racuna or f"#{db_id}",
                    datum=datum or '',
                    rok_placanja=rok_placanja or '',
                    nacin_placanja=nacin_placanja or 'G',
                    oib=oib_izd or '',
                    naziv_tvrtke=naziv_izd or '',
                    adresa=adresa_izd or '',
                    iban=iban or '',
                    kupac_naziv=naziv_kup or '',
                    kupac_oib=oib_kup or '',
                    kupac_adresa=adresa_kup or '',
                    oznaka_pp=pp or '',
                    oznaka_nu=nu or '',
                    stavke=stavke_za_pdf,
                    ukupan_iznos=ukupan_iznos or 0.0,
                    u_sustavu_pdv=bool(u_pdv),
                    pdv_oslobodenje=pdv_oslobodenje or "",
                    jir=jir,
                    zki=zki,
                    datum_vrijeme=datum_vrijeme_norm,
                    napomena=napomena or '',
                    operater=operater or '',
                    filename=filename
                )

            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(
                self, "PDF kreiran",
                f"PDF je uspješno kreiran:\n{filename}"
            )
        except Exception as e:
            from PySide6.QtWidgets import QMessageBox
            import traceback
            QMessageBox.critical(
                self, "Greška",
                f"Greška pri kreiranju PDF-a:\n{e}\n\n{traceback.format_exc()}"
            )
            print(f"✅ PDF: {e}\n\n{traceback.format_exc()}")

    # ── Gradnja sadržaja ──────────────────────────────────────────────────────

    def _izgraditi_sadrzaj(self, layout):
        r = self._racun
        (db_id, broj_racuna, datum, rok_placanja, nacin_placanja,
         oib_izd, naziv_izd, adresa_izd, iban,
         oib_kup, naziv_kup, adresa_kup,
         pp, nu, ukupan_iznos,
         napomena, jir, zki, fiskaliziran, datum_fisk,
         u_pdv, pravna_osoba, operater, datum_kreiranja) = r

        nacin_map = {
            'G': 'Gotovina', 'K': 'Kartica',
            'T': 'Transakcijski račun', 'O': 'Ostalo'
        }
        nacin_txt = nacin_map.get(nacin_placanja or 'G', nacin_placanja or '')

        je_storno = self._je_storno_racun(napomena or "")
        vec_storniran = self._je_storniran(self.invoice_id)

        # ── Status badge ──────────────────────────────────────────────────────
        status_row = QHBoxLayout()

        br_lbl = QLabel(broj_racuna or f"#{db_id}")
        br_lbl.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {_tc()['text']};")
        status_row.addWidget(br_lbl)

        status_row.addStretch()

        if je_storno:
            badge = QLabel("  ↩️  STORNO RAČUN  ")
            badge.setStyleSheet(
                "background-color: #4a2010; color: #f0a060; font-weight: bold;" if _tc().get('bg')=='#1e1e2e' else "background-color: #fde8d8; color: #a04000; font-weight: bold;",
                "border-radius: 10px; padding: 4px 12px; font-size: 11px;")
        elif vec_storniran:
            badge = QLabel("  🚫  STORNIRANO  ")
            badge.setStyleSheet(
                "background-color: #f8d7da; color: #721c24; font-weight: bold;"
                "border-radius: 10px; padding: 4px 12px; font-size: 11px;")
        elif fiskaliziran:
            badge = QLabel("  ✅  FISKALIZIRANO  ")
            badge.setStyleSheet(
                "background-color: #d4edda; color: #155724; font-weight: bold;"
                "border-radius: 10px; padding: 4px 12px; font-size: 11px;")
        elif pravna_osoba:
            badge = QLabel("  📄  PRAVNA OSOBA  ")
            badge.setStyleSheet(
                "background-color: #fff3cd; color: #856404; font-weight: bold;"
                "border-radius: 10px; padding: 4px 12px; font-size: 11px;")
        else:
            badge = QLabel("  ⚠️  NIJE FISKALIZIRANO  ")
            badge.setStyleSheet(
                "background-color: #f8d7da; color: #721c24; font-weight: bold;"
                "border-radius: 10px; padding: 4px 12px; font-size: 11px;")

        status_row.addWidget(badge)
        layout.addLayout(status_row)

        # ── Izdavatelj + Kupac ────────────────────────────────────────────────
        stranke = QHBoxLayout()
        stranke.setSpacing(12)
        stranke.addWidget(self._info_group("🏢  Izdavatelj", [
            ("Naziv",  naziv_izd or ""),
            ("OIB",    oib_izd or ""),
            ("Adresa", adresa_izd or ""),
            ("IBAN",   iban or ""),
        ]))
        stranke.addWidget(self._info_group("👤  Kupac", [
            ("Naziv",  naziv_kup or ""),
            ("OIB",    oib_kup or ""),
            ("Adresa", adresa_kup or ""),
            ("Tip",    "Pravna osoba" if pravna_osoba else "Fizička osoba"),
        ]))
        layout.addLayout(stranke)

        # ── Podaci računa ─────────────────────────────────────────────────────
        layout.addWidget(self._info_group("📋  Podaci računa", [
            ("Datum",          datum or ""),
            ("Rok plaćanja",   rok_placanja or ""),
            ("Način plaćanja", nacin_txt),
            ("Naplata",        self._naplata_tekst()),
            ("PDV",            "U sustavu PDV-a" if u_pdv else "Nije obveznik PDV-a"),
            ("PP / NU",        f"{pp or ''} / {nu or ''}"),
            ("Operater",       operater or ""),
            ("Kreirano",       self._formatiraj_dt(datum_kreiranja)),
        ], cols=2))

        # ── Stavke ────────────────────────────────────────────────────────────
        stavke_group = QGroupBox("🧾  Stavke računa")
        stavke_group.setStyleSheet(self._group_style())
        sg_layout = QVBoxLayout(stavke_group)
        sg_layout.setContentsMargins(10, 8, 10, 10)

        tbl = QTableWidget()
        cols_hdr = ["Naziv / Opis", "Kol.", "Jed.", "Cijena (EUR)", "Popust",
                    "PDV %", "Osnova (EUR)", "Ukupno (EUR)"]
        tbl.setColumnCount(len(cols_hdr))
        tbl.setHorizontalHeaderLabels(cols_hdr)
        tbl.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        tbl.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        tbl.verticalHeader().setVisible(False)
        tbl.setAlternatingRowColors(True)
        tbl.setShowGrid(True)
        tbl.setMinimumHeight(38 + len(self._stavke) * 28 + 28)
        tbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        hdr = tbl.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, len(cols_hdr)):
            hdr.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)

        _c = _tc()
        tbl.setStyleSheet(f"""
            QTableWidget {{
                border: 1px solid {_c['border']}; border-radius: 6px;
                background-color: {_c['bg_widget']};
                alternate-background-color: {_c['table_alt']};
                gridline-color: {_c['border']}; color: {_c['text']};
            }}
            QTableWidget::item {{ padding: 4px 7px; color: {_c['text']}; }}
            QTableWidget::item:selected {{
                background-color: {_c['table_selected']}; color: {_c['table_sel_text']};
            }}
            QHeaderView::section {{
                background-color: {_c['table_header']}; color: white;
                font-weight: bold; padding: 5px 7px; border: none; font-size: 10px;
            }}
        """)

        uk_osnova = uk_pdv_iznos = uk_ukupno = 0.0

        for s in self._stavke:
            naziv, kolicina, jedinica, cijena, pdv_stopa, popust = s
            red_s = izracun_stavke(kolicina, cijena, pdv_stopa or 0, popust or 0)
            osnova, pdv_iznos, ukupno_s = (
                red_s['osnovica'], red_s['pdv_iznos'], red_s['ukupno'])
            uk_osnova += osnova
            uk_pdv_iznos += pdv_iznos
            uk_ukupno += ukupno_s

            row = tbl.rowCount()
            tbl.insertRow(row)

            def si(col, val, right=False, bold=False, red=False):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(
                    (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                    if right else
                    (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter))
                if bold:
                    f = item.font(); f.setBold(True); item.setFont(f)
                if red:
                    item.setForeground(QColor("#c0392b"))
                tbl.setItem(row, col, item)

            is_neg = cijena < 0
            si(0, naziv or "")
            si(1, f"{fmt_iznos(kolicina)}", right=True)
            si(2, jedinica or "kom")
            si(3, f"{fmt_iznos(cijena)}", right=True, red=is_neg)
            si(4, f"{fmt_iznos(popust)}%" if popust else "-", right=True)
            si(5, f"{pdv_stopa:.0f}%" if u_pdv else "-", right=True)
            si(6, f"{fmt_iznos(osnova)}" if u_pdv else "-", right=True, red=is_neg)
            si(7, f"{fmt_iznos(ukupno_s)}", right=True, bold=True, red=is_neg)

        # Redak zbroja
        if self._stavke:
            row = tbl.rowCount()
            tbl.insertRow(row)
            for col in range(len(cols_hdr)):
                item = QTableWidgetItem("")
                item.setBackground(QColor(_tc()['table_header']))
                item.setForeground(QColor("#ffffff"))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                f = item.font(); f.setBold(True); item.setFont(f)
                tbl.setItem(row, col, item)

            def si_uk(col, val):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                item.setBackground(QColor(_tc()['table_header']))
                item.setForeground(QColor("#ffffff"))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                f = item.font(); f.setBold(True); item.setFont(f)
                tbl.setItem(row, col, item)

            from PySide6.QtWidgets import QStyledItemDelegate

            class _ZbrojDelegate(QStyledItemDelegate):
                def paint(self, painter, option, index):
                    painter.save()
                    painter.fillRect(option.rect, QColor(_tc()['table_header']))
                    painter.setPen(QColor("#ffffff"))
                    f = painter.font(); f.setBold(True); painter.setFont(f)
                    al = index.data(Qt.ItemDataRole.TextAlignmentRole)
                    al = Qt.AlignmentFlag(int(al)) if al is not None else (
                        Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                    painter.drawText(option.rect.adjusted(7, 0, -7, 0), al,
                                     str(index.data() or ""))
                    painter.restore()

            tbl._zbroj_delegate = _ZbrojDelegate(tbl)
            tbl.setItemDelegateForRow(row, tbl._zbroj_delegate)
            tbl.item(row, 0).setText("UKUPNO")
            tbl.item(row, 0).setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            if u_pdv:
                si_uk(6, f"{fmt_iznos(uk_osnova)}")
            si_uk(7, f"{fmt_iznos(uk_ukupno)} EUR")

        sg_layout.addWidget(tbl)
        layout.addWidget(stavke_group)

        # ── PDV rekapitulacija ────────────────────────────────────────────────
        if u_pdv and self._stavke:
            pdv_grupe = {}
            for s in self._stavke:
                naziv, kol, jed, cij, stopa, pop = s
                red_g = izracun_stavke(kol, cij, stopa or 0, pop or 0)
                osnova, pdv_iz, ukupno_s = (
                    red_g['osnovica'], red_g['pdv_iznos'], red_g['ukupno'])
                k = stopa or 0
                if k not in pdv_grupe:
                    pdv_grupe[k] = [0.0, 0.0, 0.0]
                pdv_grupe[k][0] += osnova
                pdv_grupe[k][1] += pdv_iz
                pdv_grupe[k][2] += ukupno_s

            pdv_redci = [
                (f"{stopa:.0f}%", f"{fmt_iznos(osn)} EUR",
                 f"{fmt_iznos(pdv_i)} EUR", f"{fmt_iznos(uk)} EUR")
                for stopa, (osn, pdv_i, uk) in sorted(pdv_grupe.items())
            ]
            layout.addWidget(self._info_group(
                "📊  Rekapitulacija PDV-a", pdv_redci,
                cols=4, headers=["Stopa", "Osnovica", "PDV iznos", "Ukupno"]))

        # ── Ukupan iznos ──────────────────────────────────────────────────────
        uk_widget = QWidget()
        _ck = _tc()
        boja_bg = "#7b241c" if (ukupan_iznos or 0) < 0 else _ck['table_header']
        uk_widget.setStyleSheet(f"background-color: {boja_bg}; border-radius: 8px;")
        uk_hl = QHBoxLayout(uk_widget)
        uk_hl.setContentsMargins(18, 12, 18, 12)

        uk_lbl = QLabel("UKUPAN IZNOS ZA PLATITI:")
        uk_lbl.setStyleSheet(
            "color: #dfe6ee; font-size: 12px; font-weight: bold;"
            "background-color: transparent;")
        uk_hl.addWidget(uk_lbl)
        uk_hl.addStretch()

        uk_iznos = QLabel(f"{fmt_iznos(ukupan_iznos)} EUR")
        uk_iznos.setStyleSheet(
            "color: #ff9e9e; font-size: 22px; font-weight: bold;"
            "background-color: transparent;"
            if (ukupan_iznos or 0) < 0 else
            "color: #69f0ae; font-size: 22px; font-weight: bold;"
            "background-color: transparent;")
        uk_hl.addWidget(uk_iznos)

        layout.addWidget(uk_widget)

        # ── Fiskalizacijski podaci ─────────────────────────────────────────────
        fisk_stavke = []
        if zki:
            fisk_stavke.append(("ZKI", zki))
        if jir:
            fisk_stavke.append(("JIR", jir))
        if datum_fisk:
            fisk_stavke.append(("Datum fiskalizacije", self._formatiraj_dt(datum_fisk)))
        if fisk_stavke:
            layout.addWidget(self._info_group(
                "🔐  Fiskalizacijski podaci", fisk_stavke, mono=True))

        # ── Napomena ──────────────────────────────────────────────────────────
        if napomena and napomena.strip():
            layout.addWidget(self._info_group(
                "📝  Napomena", [("", napomena.strip())]))

    # ── Stilovi i pomoćne metode ───────────────────────────────────────────────

    def _group_style(self):
        c = _tc()
        return f"""
            QGroupBox {{
                font-weight: bold; font-size: 11px; color: {c['text_muted']};
                border: 1px solid {c['border']}; border-radius: 8px;
                margin-top: 10px; padding: 10px 8px 8px 8px;
                background-color: {c['bg_widget']};
            }}
            QGroupBox::title {{
                subcontrol-origin: margin; subcontrol-position: top left;
                padding: 0 6px; left: 12px; color: {c['text_dim']};
                background-color: {c['bg_widget']};
            }}
        """

    def _info_group(self, naslov: str, stavke: list,
                    cols: int = 1, mono: bool = False,
                    headers: list = None) -> QGroupBox:
        group = QGroupBox(naslov)
        group.setStyleSheet(self._group_style())
        grid = QGridLayout(group)
        grid.setSpacing(6)
        grid.setContentsMargins(10, 6, 10, 10)

        c = _tc()
        lbl_style = f"color: {c['text_dim']}; font-size: 10px; background-color: transparent;"
        val_style_base = f"color: {c['text']}; font-size: 11px; background-color: transparent;"
        val_style_mono = (f"color: {c['text']}; font-size: 9px; "
                          "font-family: Consolas, monospace; "
                          f"background-color: {c['bg_panel']}; "
                          "border-radius: 4px; padding: 2px 6px;")
        val_style = val_style_mono if mono else val_style_base

        if headers and cols == 4:
            for ci, h in enumerate(headers):
                lbl = QLabel(h)
                lbl.setStyleSheet(
                    f"font-weight: bold; font-size: 10px; color: {c['text_muted']};"
                    "background-color: transparent;")
                grid.addWidget(lbl, 0, ci)
            for ri, row_data in enumerate(stavke):
                for ci, val in enumerate(row_data):
                    lbl = QLabel(str(val))
                    lbl.setStyleSheet(val_style_base)
                    grid.addWidget(lbl, ri + 1, ci)

        elif cols == 2:
            for i, (k, v) in enumerate(stavke):
                row_i = i // 2
                col_i = (i % 2) * 2
                if k:
                    key_lbl = QLabel(k + ":")
                    key_lbl.setStyleSheet(lbl_style)
                    key_lbl.setAlignment(Qt.AlignmentFlag.AlignRight |
                                         Qt.AlignmentFlag.AlignTop)
                    grid.addWidget(key_lbl, row_i, col_i)
                val_lbl = QLabel(str(v))
                val_lbl.setStyleSheet(val_style)
                val_lbl.setWordWrap(True)
                grid.addWidget(val_lbl, row_i, col_i + 1)

        else:
            for i, (k, v) in enumerate(stavke):
                if k:
                    key_lbl = QLabel(k + ":")
                    key_lbl.setStyleSheet(lbl_style)
                    key_lbl.setFixedWidth(130)
                    key_lbl.setAlignment(Qt.AlignmentFlag.AlignRight |
                                         Qt.AlignmentFlag.AlignTop)
                    grid.addWidget(key_lbl, i, 0)
                val_lbl = QLabel(str(v))
                val_lbl.setStyleSheet(val_style)
                val_lbl.setWordWrap(True)
                val_lbl.setSizePolicy(
                    QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
                grid.addWidget(val_lbl, i, 1 if k else 0, 1, 2 if not k else 1)

        return group

    @staticmethod
    def _formatiraj_dt(dt_str: str) -> str:
        if not dt_str:
            return ""
        for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                    "%Y-%m-%d %H:%M:%S", "%d.%m.%Y"):
            try:
                return datetime.strptime(dt_str, fmt).strftime("%d.%m.%Y  %H:%M:%S")
            except ValueError:
                pass
        return dt_str
