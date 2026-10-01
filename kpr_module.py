"""
kpr_module.py — Knjiga Prometa (KPR) modul

Dodaj u glavnu datoteku:

1) Na vrh (nakon postojećih importa):
   from kpr_module import inicijaliziraj_kpr_tablicu, KPRWidget

2) Nakon conn.commit() pri kraju inicijalizacije baze (~redak 486):
   inicijaliziraj_kpr_tablicu(conn, cursor)

3) U BillingApp.__init__, tamo gdje se dodaju tabovi (~redak 2468):
   self.kpr_widget = KPRWidget()
   tabs.addTab(self.kpr_widget, "📒  KPR")
   tabs.currentChanged.connect(
       lambda i: self.kpr_widget.osvjezi() if i == 2 else None)

4) Na kraju save_invoice(), neposredno prije QMessageBox.information (~redak 2827):
   _dodaj_u_kpr(conn, cursor, invoice_id, nacin_kod, datum, broj_racuna_str, ukupan_iznos)
"""

import os
import io
import re
import zipfile
import sqlite3
import tempfile
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QComboBox,
    QMessageBox, QDialog, QFormLayout, QLineEdit, QDialogButtonBox,
    QAbstractItemView, QApplication
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from format_util import fmt_iznos, parse_iznos
from novac import u_centima


def _tc():
    """Vrati COLORS rječnik aktivne teme."""
    app = QApplication.instance()
    if app:
        for w in app.topLevelWidgets():
            tm = getattr(w, 'theme_manager', None)
            if tm:
                return tm.colors
    try:
        from theme_module import LIGHT_COLORS
        return LIGHT_COLORS
    except Exception:
        return {'bg': '#f0f2f5', 'bg_widget': '#ffffff', 'bg_panel': '#f8f9fb',
                'text': '#2c2c2c', 'text_dim': '#7f8c8d', 'text_muted': '#555',
                'border': '#d0d4db', 'table_header': '#2c3e50',
                'table_alt': '#f8f9fb', 'table_selected': '#e8f0fe',
                'table_sel_text': '#2c2c2c', 'accent': '#4a90d9',
                'success': '#2e7d32', 'danger_text': '#c0392b', 'warning': '#e67e22'}

# ── Logika načina plaćanja ───────────────────────────────────────────────────
# G (gotovina) i K (kartica) → kol. 5 (gotovina i čekovi)
# T (transakcijski) → kol. 6 (virmanski)
# O (ostalo) → kol. 5 (tretira kao gotovina)

def _rasporedi_iznos(nacin_kod: str, iznos: float):
    """Vraća (gotovina, virmanski) prema kodu načina plaćanja."""
    if nacin_kod == 'T':
        return 0.0, iznos
    return iznos, 0.0



# ── XML popravak za openpyxl boje (applyFill/applyFont bug) ─────────────────

def _fix_xlsx_styles(path):
    """
    openpyxl ne zapisuje applyFill/applyFont atribute pa Excel ignorira boje.
    Ova funkcija direktno popravlja styles.xml unutar xlsx datoteke.
    """
    out = io.BytesIO()
    with zipfile.ZipFile(path, 'r') as zin:
        with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zout:
            for name in zin.namelist():
                data = zin.read(name)
                if name == 'xl/styles.xml':
                    xml = data.decode('utf-8')

                    def fix_xf(m):
                        tag = m.group(0)
                        self_close = tag.endswith('/>')
                        base = tag[:-2].rstrip() if self_close else tag[:-1].rstrip()
                        fi = re.search(r'fillId="(\d+)"', tag)
                        fo = re.search(r'fontId="(\d+)"', tag)
                        bi = re.search(r'borderId="(\d+)"', tag)
                        if fi and int(fi.group(1)) > 1 and 'applyFill' not in tag:
                            base += ' applyFill="1"'
                        if fo and int(fo.group(1)) > 0 and 'applyFont' not in tag:
                            base += ' applyFont="1"'
                        if bi and int(bi.group(1)) > 0 and 'applyBorder' not in tag:
                            base += ' applyBorder="1"'
                        return base + ('/>' if self_close else '>')

                    def fix_block(m):
                        block = m.group(0)
                        block = re.sub(r'<xf [^>]+/>', fix_xf, block)
                        block = re.sub(r'<xf [^/>][^>]*>', fix_xf, block)
                        return block

                    xml = re.sub(
                        r'<cellXfs[^>]*>.*?</cellXfs>',
                        fix_block, xml, flags=re.DOTALL
                    )
                    data = xml.encode('utf-8')
                zout.writestr(name, data)

    with open(path, 'wb') as f:
        out.seek(0)
        f.write(out.read())


# ── Inicijalizacija tablice ──────────────────────────────────────────────────

def inicijaliziraj_kpr_tablicu(conn, cursor):
    """Kreira kpr tablicu ako ne postoji. Pozovi jednom pri startu."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS kpr (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            godina          INTEGER NOT NULL,
            redni_broj      INTEGER NOT NULL,
            datum           TEXT NOT NULL,
            broj_temeljnice TEXT,
            opis            TEXT,
            gotovina_cent   INTEGER DEFAULT 0,
            virmanski_cent  INTEGER DEFAULT 0,
            invoice_id      INTEGER,
            rucno_dodano    INTEGER DEFAULT 0,
            UNIQUE(godina, redni_broj)
        )
    """)
    conn.commit()

    # Migracija: dodaj stupac ako nedostaje (za starije baze)
    try:
        cursor.execute("ALTER TABLE kpr ADD COLUMN rucno_dodano INTEGER DEFAULT 0")
        conn.commit()
    except Exception:
        pass


def _sljedeci_redni_broj_kpr(cursor, godina: int) -> int:
    row = cursor.execute(
        "SELECT MAX(redni_broj) FROM kpr WHERE godina=?", (godina,)
    ).fetchone()
    return (row[0] or 0) + 1


def _dodaj_u_kpr(conn, cursor, invoice_id: int, nacin_kod: str,
                 datum: str, broj_racuna_str: str, ukupan_iznos: float):
    """
    Automatski unosi račun u KPR.
    Pozovi na kraju save_invoice() u BillingApp.
    """
    try:
        # Izvuci godinu iz datuma (format dd.MM.yyyy)
        try:
            godina = int(datum.split('.')[-1])
        except Exception:
            godina = datetime.now().year

        redni = _sljedeci_redni_broj_kpr(cursor, godina)
        gotovina, virmanski = _rasporedi_iznos(nacin_kod, ukupan_iznos)

        cursor.execute("""
            INSERT INTO kpr
                (godina, redni_broj, datum, broj_temeljnice, opis,
                 gotovina_cent, virmanski_cent, invoice_id, rucno_dodano)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (
            godina, redni, datum,
            broj_racuna_str,
            f"Račun {broj_racuna_str}",
            u_centima(gotovina), u_centima(virmanski),
            invoice_id
        ))
        conn.commit()
        print(f"✅ KPR: unos {redni}/{godina} za račun {broj_racuna_str}")
        return True
    except Exception as e:
        print(f"⚠️  KPR greška pri unosu: {e}")
        return False


# ── Naplata računa ───────────────────────────────────────────────────────────
# KPR (i PO-SD) se temelji na NAPLAĆENIM primicima. Gotovina, kartica i "ostalo"
# smatraju se naplaćenima pri izdavanju; transakcijski račun (T) ulazi u KPR tek
# kad se označi da je uplata legla, s datumom naplate.

NACINI_ODMAH_NAPLACENO = ('G', 'K', 'O')


def migriraj_naplatu(conn, cursor):
    """Dodaje invoices.placeno i invoices.datum_naplate. Računi koji već imaju
    KPR unos (stari način rada) označavaju se kao naplaćeni tim datumom."""
    dodano = False
    for sql in ("ALTER TABLE invoices ADD COLUMN placeno INTEGER DEFAULT 0",
                "ALTER TABLE invoices ADD COLUMN datum_naplate TEXT"):
        try:
            cursor.execute(sql)
            dodano = True
        except sqlite3.OperationalError:
            pass
    conn.commit()
    if dodano:
        cursor.execute("""
            UPDATE invoices
            SET placeno = 1,
                datum_naplate = (SELECT k.datum FROM kpr k
                                 WHERE k.invoice_id = invoices.id
                                 ORDER BY k.id LIMIT 1)
            WHERE EXISTS (SELECT 1 FROM kpr k WHERE k.invoice_id = invoices.id)
        """)
        conn.commit()


def oznaci_naplaceno(conn, cursor, invoice_id: int, datum_naplate: str):
    """Označava račun naplaćenim i upisuje ga u KPR s datumom naplate
    (dd.MM.yyyy). Vraća (uspjeh, poruka_greške)."""
    r = cursor.execute(
        "SELECT broj_racuna, nacin_placanja, ukupan_iznos_cent / 100.0, placeno "
        "FROM invoices WHERE id=?", (invoice_id,)).fetchone()
    if not r:
        return False, "Račun nije pronađen."
    broj, nacin, iznos, placeno = r
    if placeno:
        return False, "Račun je već označen kao naplaćen."
    cursor.execute(
        "UPDATE invoices SET placeno=1, datum_naplate=? WHERE id=?",
        (datum_naplate, invoice_id))
    conn.commit()
    if not _dodaj_u_kpr(conn, cursor, invoice_id, nacin or 'G', datum_naplate,
                        broj or str(invoice_id), iznos or 0.0):
        cursor.execute(
            "UPDATE invoices SET placeno=0, datum_naplate=NULL WHERE id=?",
            (invoice_id,))
        conn.commit()
        return False, "Upis u KPR nije uspio, naplata nije spremljena."
    return True, ""


def ponisti_naplatu(conn, cursor, invoice_id: int):
    """Poništava naplatu: briše KPR unos računa i vraća račun u 'čeka naplatu'.
    Redni brojevi KPR-a se ne renumeriraju."""
    cursor.execute("DELETE FROM kpr WHERE invoice_id=?", (invoice_id,))
    cursor.execute(
        "UPDATE invoices SET placeno=0, datum_naplate=NULL WHERE id=?",
        (invoice_id,))
    conn.commit()


# ── Dijalog za ručni unos / ispravak ────────────────────────────────────────

class KPRUnos(QDialog):
    """Dijalog za ručni unos ili ispravak KPR stavke."""

    def __init__(self, parent=None, podaci: dict = None):
        super().__init__(parent)
        self.setWindowTitle("Ručni unos / ispravak KPR stavke")
        self.setMinimumWidth(420)
        self.setModal(True)

        layout = QVBoxLayout(self)
        forma = QFormLayout()
        forma.setSpacing(8)

        self.datum_input = QLineEdit()
        self.datum_input.setPlaceholderText("dd.MM.yyyy")
        forma.addRow("Datum:", self.datum_input)

        self.temeljnica_input = QLineEdit()
        self.temeljnica_input.setPlaceholderText("Broj računa / izvoda")
        forma.addRow("Br. temeljnice:", self.temeljnica_input)

        self.opis_input = QLineEdit()
        self.opis_input.setPlaceholderText("Kratak opis")
        forma.addRow("Opis:", self.opis_input)

        self.gotovina_input = QLineEdit()
        self.gotovina_input.setPlaceholderText("0.00")
        forma.addRow("Gotovina + kartice (kol. 5):", self.gotovina_input)

        self.virmanski_input = QLineEdit()
        self.virmanski_input.setPlaceholderText("0.00")
        forma.addRow("Virmanski (kol. 6):", self.virmanski_input)

        layout.addLayout(forma)

        gumbi = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok |
            QDialogButtonBox.StandardButton.Cancel
        )
        gumbi.accepted.connect(self._provjeri_i_prihvati)
        gumbi.rejected.connect(self.reject)
        layout.addWidget(gumbi)

        if podaci:
            self.datum_input.setText(podaci.get('datum', ''))
            self.temeljnica_input.setText(podaci.get('broj_temeljnice', ''))
            self.opis_input.setText(podaci.get('opis', ''))
            self.gotovina_input.setText(str(podaci.get('gotovina', '0.00')))
            self.virmanski_input.setText(str(podaci.get('virmanski', '0.00')))

    def _provjeri_i_prihvati(self):
        datum = self.datum_input.text().strip()
        if not datum:
            QMessageBox.warning(self, "Greška", "Datum je obavezan!")
            return
        try:
            datetime.strptime(datum, "%d.%m.%Y")
        except ValueError:
            QMessageBox.warning(self, "Greška", "Format datuma mora biti dd.MM.yyyy!")
            return
        try:
            parse_iznos(self.gotovina_input.text() or '0')
            parse_iznos(self.virmanski_input.text() or '0')
        except ValueError:
            QMessageBox.warning(self, "Greška", "Iznosi moraju biti brojevi!")
            return
        self.accept()

    def get_podaci(self) -> dict:
        return {
            'datum': self.datum_input.text().strip(),
            'broj_temeljnice': self.temeljnica_input.text().strip(),
            'opis': self.opis_input.text().strip(),
            'gotovina': round(parse_iznos(self.gotovina_input.text() or '0'), 2),
            'virmanski': round(parse_iznos(self.virmanski_input.text() or '0'), 2),
        }


# ── Glavni widget ────────────────────────────────────────────────────────────

class KPRWidget(QWidget):
    """Tab s prikazom i upravljanjem Knjigom Prometa (KPR)."""

    def __init__(self, conn=None, cursor=None):
        super().__init__()
        # Ako nisu proslijeđeni, uvezi globalne iz __main__
        if conn is None or cursor is None:
            import db as _db
            self._conn = _db.conn
            self._cursor = _db.cursor
        else:
            self._conn = conn
            self._cursor = cursor

        self._godina = datetime.now().year
        self._kpr_podaci = []  # cache za export

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Zaglavlje s kontrolama ───────────────────────────────────────────
        ctrl = QHBoxLayout()

        ctrl.addWidget(QLabel("Godina:"))
        self.godina_combo = QComboBox()
        self.godina_combo.setFixedWidth(90)
        self._popuni_godine()
        self.godina_combo.currentTextChanged.connect(self._on_godina_changed)
        ctrl.addWidget(self.godina_combo)

        ctrl.addStretch()

        self.dodaj_btn = QPushButton("➕  Ručni unos")
        self.dodaj_btn.clicked.connect(self._rucni_unos)
        ctrl.addWidget(self.dodaj_btn)

        self.uredi_btn = QPushButton("✏️  Ispravi")
        self.uredi_btn.clicked.connect(self._uredi_stavku)
        self.uredi_btn.setEnabled(False)
        ctrl.addWidget(self.uredi_btn)

        self.obrisi_btn = QPushButton("🗑️  Obriši")
        self.obrisi_btn.setObjectName("dangerBtn")
        self.obrisi_btn.clicked.connect(self._obrisi_stavku)
        self.obrisi_btn.setEnabled(False)
        ctrl.addWidget(self.obrisi_btn)

        ctrl.addWidget(QLabel("  "))  # razmak

        self.xlsx_btn = QPushButton("📊  Excel")
        self.xlsx_btn.clicked.connect(self._export_xlsx)
        ctrl.addWidget(self.xlsx_btn)

        self.pdf_btn = QPushButton("📄  PDF")
        self.pdf_btn.clicked.connect(self._export_pdf)
        ctrl.addWidget(self.pdf_btn)

        layout.addLayout(ctrl)

        # ── Tablica ──────────────────────────────────────────────────────────
        self.tabla = QTableWidget()
        self.tabla.setColumnCount(8)
        self.tabla.setHorizontalHeaderLabels([
            "Red. br.", "Nadnevak", "Br. temeljnice",
            "Opis isprava o primicima",
            "Gotovina i čekovi (5)",
            "Virmanski (6)",
            "Ukupno (7=5+6)",
            "Vrsta"
        ])
        self.tabla.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabla.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setSortingEnabled(False)

        hdr = self.tabla.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        hdr.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)

        _c = _tc()
        self.tabla.setStyleSheet(f"""
            QTableWidget {{
                border: 1px solid {_c['border']}; border-radius: 6px;
                background-color: {_c['bg_widget']};
                alternate-background-color: {_c['table_alt']};
                gridline-color: {_c['border']}; color: {_c['text']};
            }}
            QTableWidget::item {{ padding: 5px 8px; color: {_c['text']}; }}
            QTableWidget::item:selected {{
                background-color: {_c['table_selected']}; color: {_c['table_sel_text']};
            }}
            QHeaderView::section {{
                background-color: {_c['table_header']}; color: white;
                font-weight: bold; padding: 7px 6px; border: none; font-size: 10px;
            }}
        """)

        self.tabla.selectionModel().selectionChanged.connect(self._on_selekcija)
        layout.addWidget(self.tabla)

        # ── Statusna traka ───────────────────────────────────────────────────
        status = QHBoxLayout()

        self.info_label = QLabel("")
        self.info_label.setStyleSheet(
            f"color: {_tc()['text_dim']}; font-size: 10px;")
        status.addWidget(self.info_label)

        status.addStretch()

        self.zbroj_label = QLabel("")
        self.zbroj_label.setStyleSheet(
            f"font-weight: bold; font-size: 11px; color: {_tc()['text']};")
        status.addWidget(self.zbroj_label)

        layout.addLayout(status)

        self.osvjezi()

        # Poveži s ThemeManagerom ako postoji
        _app = QApplication.instance()
        if _app:
            for _w in _app.topLevelWidgets():
                _tm = getattr(_w, 'theme_manager', None)
                if _tm:
                    _tm.theme_changed.connect(self._apply_theme)
                    break

    def _apply_theme(self, c: dict):
        """Ažurira lokalne stylesheetove pri promjeni teme."""
        self.tabla.setStyleSheet(f"""
            QTableWidget {{
                border: 1px solid {c['border']}; border-radius: 6px;
                background-color: {c['bg_widget']};
                alternate-background-color: {c['table_alt']};
                gridline-color: {c['border']}; color: {c['text']};
            }}
            QTableWidget::item {{ padding: 5px 8px; color: {c['text']}; }}
            QTableWidget::item:selected {{
                background-color: {c['table_selected']}; color: {c['table_sel_text']};
            }}
            QHeaderView::section {{
                background-color: {c['table_header']}; color: white;
                font-weight: bold; padding: 7px 6px; border: none; font-size: 10px;
            }}
        """)
        self.info_label.setStyleSheet(
            f"color: {c['text_dim']}; font-size: 10px;")
        self.zbroj_label.setStyleSheet(
            f"font-weight: bold; font-size: 11px; color: {c['text']};")

    def _popuni_godine(self):
        """Popunjava dropdown godina iz baze (+ tekuća)."""
        self.godina_combo.blockSignals(True)
        self.godina_combo.clear()
        try:
            rows = self._cursor.execute(
                "SELECT DISTINCT godina FROM kpr ORDER BY godina DESC"
            ).fetchall()
            godine = [str(r[0]) for r in rows]
        except Exception:
            godine = []
        tekuca = str(datetime.now().year)
        if tekuca not in godine:
            godine.insert(0, tekuca)
        for g in godine:
            self.godina_combo.addItem(g)
        idx = self.godina_combo.findText(tekuca)
        if idx >= 0:
            self.godina_combo.setCurrentIndex(idx)
        self.godina_combo.blockSignals(False)

    def _on_godina_changed(self, tekst):
        try:
            self._godina = int(tekst)
        except ValueError:
            pass
        self.osvjezi()

    def _on_selekcija(self):
        ima = self.tabla.currentRow() >= 0
        self.uredi_btn.setEnabled(ima)
        self.obrisi_btn.setEnabled(ima)

    def osvjezi(self):
        """Učitava KPR stavke iz baze i osvježava tablicu."""
        try:
            rows = self._cursor.execute("""
                SELECT id, redni_broj, datum, broj_temeljnice, opis,
                       gotovina_cent / 100.0, virmanski_cent / 100.0, rucno_dodano
                FROM kpr
                WHERE godina=?
                ORDER BY redni_broj
            """, (self._godina,)).fetchall()
        except Exception as e:
            print(f"⚠️  KPR osvjezi greška: {e}")
            rows = []

        self._kpr_podaci = rows
        self.tabla.setRowCount(0)

        uk_got = uk_vir = 0.0

        for r in rows:
            (db_id, rb, datum, temeljnica, opis,
             gotovina, virmanski, rucno) = r

            row = self.tabla.rowCount()
            self.tabla.insertRow(row)

            def si(col, val, right=False, bold=False, color=None):
                item = QTableWidgetItem(str(val))
                align = (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                         if right else
                         Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                item.setTextAlignment(align)
                if bold:
                    f = item.font()
                    f.setBold(True)
                    item.setFont(f)
                if color:
                    item.setForeground(QColor(color))
                item.setData(Qt.ItemDataRole.UserRole, db_id)
                self.tabla.setItem(row, col, item)

            ukupno = round((gotovina or 0) + (virmanski or 0), 2)
            uk_got += gotovina or 0
            uk_vir += virmanski or 0

            si(0, rb, right=True)
            si(1, datum)
            si(2, temeljnica or "")
            si(3, opis or "")
            si(4, f"{fmt_iznos(gotovina)}" if gotovina else "-", right=True)
            si(5, f"{fmt_iznos(virmanski)}" if virmanski else "-", right=True)
            si(6, f"{fmt_iznos(ukupno)}", right=True, bold=True)
            vrsta = "✏️ ručno" if rucno else "🔄 auto"
            si(7, vrsta)

        # Redak s ukupnim iznosima
        if rows:
            row = self.tabla.rowCount()
            self.tabla.insertRow(row)
            uk_ukupno = round(uk_got + uk_vir, 2)

            def si_uk(col, val, right=True):
                item = QTableWidgetItem(str(val))
                item.setTextAlignment(
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                    if right else
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                f = item.font()
                f.setBold(True)
                item.setFont(f)
                item.setBackground(QColor("#2c3e50"))
                item.setForeground(QColor("#ffffff"))
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
                self.tabla.setItem(row, col, item)

            si_uk(0, "", right=False)
            si_uk(1, "UKUPNO", right=False)
            si_uk(2, "")
            si_uk(3, "")
            si_uk(4, f"{fmt_iznos(uk_got)} EUR")
            si_uk(5, f"{fmt_iznos(uk_vir)} EUR")
            si_uk(6, f"{fmt_iznos(uk_ukupno)} EUR")
            si_uk(7, "")

        n = len(rows)
        self.info_label.setText(
            f"{n} {'unos' if n == 1 else 'unosa'}  •  godina {self._godina}")
        uk = round(uk_got + uk_vir, 2)
        self.zbroj_label.setText(
            f"Gotovina: {fmt_iznos(uk_got)} EUR   |   Virman: {fmt_iznos(uk_vir)} EUR   |   "
            f"Ukupno: {fmt_iznos(uk)} EUR")

    def _rucni_unos(self):
        dlg = KPRUnos(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        p = dlg.get_podaci()
        try:
            datum = p['datum']
            try:
                godina = int(datum.split('.')[-1])
            except Exception:
                godina = self._godina

            redni = _sljedeci_redni_broj_kpr(self._cursor, godina)
            self._cursor.execute("""
                INSERT INTO kpr
                    (godina, redni_broj, datum, broj_temeljnice, opis,
                     gotovina_cent, virmanski_cent, rucno_dodano)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
            """, (godina, redni, datum, p['broj_temeljnice'],
                  p['opis'], u_centima(p['gotovina']),
                  u_centima(p['virmanski'])))
            self._conn.commit()

            # Ažuriraj dropdown ako je nova godina
            if str(godina) not in [self.godina_combo.itemText(i)
                                    for i in range(self.godina_combo.count())]:
                self._popuni_godine()
            self._godina = godina
            self.godina_combo.setCurrentText(str(godina))
            self.osvjezi()
        except Exception as e:
            QMessageBox.critical(self, "Greška", f"Greška pri unosu:\n{e}")

    def _dohvati_odabrani_db_id(self):
        row = self.tabla.currentRow()
        if row < 0:
            return None
        item = self.tabla.item(row, 0)
        if item is None:
            return None
        return item.data(Qt.ItemDataRole.UserRole)

    def _uredi_stavku(self):
        db_id = self._dohvati_odabrani_db_id()
        if db_id is None:
            return
        try:
            r = self._cursor.execute(
                "SELECT datum, broj_temeljnice, opis, "
                "gotovina_cent / 100.0, virmanski_cent / 100.0 "
                "FROM kpr WHERE id=?", (db_id,)
            ).fetchone()
            if not r:
                return
        except Exception:
            return

        podaci = {
            'datum': r[0], 'broj_temeljnice': r[1], 'opis': r[2],
            'gotovina': r[3], 'virmanski': r[4]
        }
        dlg = KPRUnos(self, podaci=podaci)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        p = dlg.get_podaci()
        try:
            self._cursor.execute("""
                UPDATE kpr SET datum=?, broj_temeljnice=?, opis=?,
                    gotovina_cent=?, virmanski_cent=?, rucno_dodano=1
                WHERE id=?
            """, (p['datum'], p['broj_temeljnice'], p['opis'],
                  u_centima(p['gotovina']), u_centima(p['virmanski']),
                  db_id))
            self._conn.commit()
            self.osvjezi()
        except Exception as e:
            QMessageBox.critical(self, "Greška", f"Greška pri ažuriranju:\n{e}")

    def _obrisi_stavku(self):
        db_id = self._dohvati_odabrani_db_id()
        if db_id is None:
            return
        odg = QMessageBox.question(
            self, "Brisanje stavke",
            "Jeste li sigurni da želite obrisati ovu KPR stavku?\n"
            "Redni brojevi se neće automatski renumerirati.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if odg != QMessageBox.StandardButton.Yes:
            return
        try:
            r = self._cursor.execute(
                "SELECT invoice_id FROM kpr WHERE id=?", (db_id,)).fetchone()
            self._cursor.execute("DELETE FROM kpr WHERE id=?", (db_id,))
            if r and r[0]:
                self._cursor.execute(
                    "UPDATE invoices SET placeno=0, datum_naplate=NULL "
                    "WHERE id=? AND NOT EXISTS "
                    "(SELECT 1 FROM kpr WHERE invoice_id=?)", (r[0], r[0]))
            self._conn.commit()
            self.osvjezi()
        except Exception as e:
            QMessageBox.critical(self, "Greška", f"Greška pri brisanju:\n{e}")

    # ── Export ───────────────────────────────────────────────────────────────

    def _dohvati_config(self):
        """Pokušava dohvatiti podatke o tvrtki iz config.json."""
        import json
        try:
            with open("config.json", 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}

    def _export_xlsx(self):
        """Generira KPR u Excel formatu (Obrazac KPR)."""
        try:
            from openpyxl import Workbook
            from openpyxl.styles import (Font, PatternFill, Alignment,
                                         Border, Side)
        except ImportError:
            QMessageBox.critical(self, "Greška",
                                 "openpyxl nije instaliran!\npip install openpyxl")
            return

        rows = self._kpr_podaci
        if not rows:
            QMessageBox.information(self, "Nema podataka",
                                    f"Nema KPR unosa za godinu {self._godina}.")
            return

        cfg = self._dohvati_config()
        wb = Workbook()
        ws = wb.active
        ws.title = f"KPR {self._godina}"

        # ── Stilovi ──────────────────────────────────────────────────────────
        def border(thin=True):
            s = Side(style='thin' if thin else 'medium')
            return Border(left=s, right=s, top=s, bottom=s)

        hdr_fill = PatternFill(fill_type="solid", start_color="FF2C3E50", end_color="FF2C3E50")
        hdr_font = Font(name="Arial", bold=True, color="FFFFFFFF", size=9)
        title_font = Font(name="Arial", bold=True, size=13)
        bold_font = Font(name="Arial", bold=True, size=9)
        norm_font = Font(name="Arial", size=9)
        total_fill = PatternFill(fill_type="solid", start_color="FFD5E8F5", end_color="FFD5E8F5")
        center = Alignment(horizontal="center", vertical="center", wrap_text=True)
        right = Alignment(horizontal="right", vertical="center")
        left_a = Alignment(horizontal="left", vertical="center")

        # ── Naslov ───────────────────────────────────────────────────────────
        ws.merge_cells("A1:G1")
        ws["A1"] = "KNJIGA PROMETA"
        ws["A1"].font = title_font
        ws["A1"].alignment = center

        ws.merge_cells("A2:D2")
        ws["A2"] = f"Obrazac - KPR   |   Godina: {self._godina}"
        ws["A2"].font = Font(name="Arial", size=9, italic=True)
        ws["A2"].alignment = left_a

        # Podaci o tvrtki
        ws.merge_cells("A3:G3")
        ws["A3"] = (f"Porezni obveznik: {cfg.get('naziv_tvrtke', '')}   |   "
                    f"OIB: {cfg.get('oib', '')}   |   "
                    f"Adresa: {cfg.get('adresa', '')}")
        ws["A3"].font = norm_font
        ws["A3"].alignment = left_a

        # Prazni redak
        ws.row_dimensions[4].height = 6

        # ── Zaglavlje tablice (redak 5) ───────────────────────────────────────
        zaglavlja = [
            "REDNI\nBROJ", "NADNEVAK", "BROJ TEMELJNICE\n(broj izvoda)",
            "OPIS ISPRAVA O PRIMICIMA\n(broj računa)",
            "IZNOS NAPLAĆEN U GOTOVINI\nI ČEKOVIMA\n(kol. 5)",
            "IZNOS NAPLAĆEN\nVIRMANSKI\n(kol. 6)",
            "UKUPNO NAPLAĆEN\nIZNOS\n(7=5+6)"
        ]
        for col, naslov in enumerate(zaglavlja, 1):
            c = ws.cell(row=5, column=col, value=naslov)
            c.font = hdr_font
            c.fill = hdr_fill
            c.alignment = center
            c.border = border()

        ws.row_dimensions[5].height = 42

        # Broj redaka (redak 6)
        for col, br in enumerate(["1", "2", "3", "4", "5", "6", "7"], 1):
            c = ws.cell(row=6, column=col, value=br)
            c.font = Font(name="Arial", bold=True, color="FF167C28", size=9)
            c.alignment = center
            c.border = border()

        # ── Podatkovni redci ──────────────────────────────────────────────────
        data_start = 7
        for i, r in enumerate(rows):
            (db_id, rb, datum, temeljnica, opis,
             gotovina, virmanski, rucno) = r
            row_n = data_start + i
            ukupno = round((gotovina or 0) + (virmanski or 0), 2)

            vals = [rb, datum, temeljnica or "", opis or "",
                    gotovina or 0, virmanski or 0, ukupno]
            for col, val in enumerate(vals, 1):
                c = ws.cell(row=row_n, column=col, value=val)
                c.font = norm_font
                c.border = border()
                if col == 1:
                    c.alignment = center
                elif col in (5, 6, 7):
                    c.alignment = right
                    c.number_format = '#,##0.00'
                    if val == 0:
                        c.value = None
                else:
                    c.alignment = left_a

        # ── Redak ukupno ──────────────────────────────────────────────────────
        uk_row = data_start + len(rows)
        uk_got = sum(r[5] or 0 for r in rows)
        uk_vir = sum(r[6] or 0 for r in rows)
        uk_uk = round(uk_got + uk_vir, 2)

        for col in range(1, 8):
            c = ws.cell(row=uk_row, column=col)
            c.font = bold_font
            c.fill = total_fill
            c.border = border()
            c.alignment = right

        ws.cell(row=uk_row, column=1, value="UKUPNO").alignment = center
        ws.cell(row=uk_row, column=1).font = bold_font
        ws.cell(row=uk_row, column=1).fill = total_fill

        for col, val in [(5, uk_got), (6, uk_vir), (7, uk_uk)]:
            c = ws.cell(row=uk_row, column=col, value=val)
            c.number_format = '#,##0.00 "EUR"'
            c.font = bold_font
            c.fill = total_fill
            c.alignment = right

        # ── Širine stupaca ────────────────────────────────────────────────────
        from openpyxl.utils import get_column_letter
        sirine = [9, 12, 22, 42, 18, 18, 18]
        for i, w in enumerate(sirine, 1):
            ws.column_dimensions[get_column_letter(i)].width = w

        # ── Spremi ───────────────────────────────────────────────────────────
        naziv = f"KPR_{self._godina}.xlsx"
        wb.save(naziv)
        _fix_xlsx_styles(naziv)
        QMessageBox.information(
            self, "Excel exportiran",
            f"KPR za godinu {self._godina} exportirana u:\n{os.path.abspath(naziv)}")

    def _export_pdf(self):
        """Generira KPR u PDF formatu (Obrazac KPR)."""
        try:
            from reportlab.lib.pagesizes import A4, landscape
            from reportlab.pdfgen import canvas as rl_canvas
            from reportlab.lib.units import mm
            from reportlab.pdfbase import pdfmetrics
            from reportlab.pdfbase.ttfonts import TTFont
        except ImportError:
            QMessageBox.critical(self, "Greška",
                                 "reportlab nije instaliran!\npip install reportlab")
            return

        rows = self._kpr_podaci
        if not rows:
            QMessageBox.information(self, "Nema podataka",
                                    f"Nema KPR unosa za godinu {self._godina}.")
            return

        cfg = self._dohvati_config()

        # Font (isti kandidati kao u glavnoj aplikaciji)
        fn = 'Helvetica'
        fn_b = 'Helvetica-Bold'
        font_candidates = [
            ("Arial", r"C:\Windows\Fonts\arial.ttf", r"C:\Windows\Fonts\arialbd.ttf"),
            ("Calibri", r"C:\Windows\Fonts\calibri.ttf", r"C:\Windows\Fonts\calibrib.ttf"),
            ("DejaVuSans", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
            ("DejaVuSans", "/usr/share/fonts/dejavu/DejaVuSans.ttf",
             "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf"),
            ("Arial", "/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
        ]
        for fname, reg, bold in font_candidates:
            if os.path.exists(reg):
                try:
                    pdfmetrics.registerFont(TTFont(fname, reg))
                    fn = fname
                    if os.path.exists(bold):
                        pdfmetrics.registerFont(TTFont(fname + "-Bold", bold))
                        fn_b = fname + "-Bold"
                    break
                except Exception:
                    pass

        naziv = f"KPR_{self._godina}.pdf"
        W, H = landscape(A4)
        c = rl_canvas.Canvas(naziv, pagesize=landscape(A4))

        MARGIN = 18 * mm
        # Širine stupaca u mm: Red.br, Datum, Temeljnica, Opis, Gotovina, Virman, Ukupno
        _cw = [12, 22, 38, 0, 42, 42, 42]  # 0 = stretch za Opis
        _fixed = sum(w for w in _cw if w > 0) * mm
        _cw[3] = (W - 2 * MARGIN - _fixed) / mm  # Opis dobiva ostatak
        col_w = [w * mm for w in _cw]
        col_x = [MARGIN]
        for w in col_w[:-1]:
            col_x.append(col_x[-1] + w)

        def draw_header(y_start):
            """Crta zaglavlje tablice, vraća y ispod zaglavlja."""
            headers = [
                "Red.br.", "Nadnevak", "Br. temeljnice",
                "Opis isprava o primicima\n(broj računa)",
                "Gotovina i\nčekovi (5)",
                "Virmanski\n(6)",
                "Ukupno\n(7=5+6)"
            ]
            hdr_h = 24
            c.setFillColorRGB(0.17, 0.24, 0.31)
            c.rect(MARGIN, y_start - hdr_h, W - 2 * MARGIN, hdr_h, fill=1, stroke=0)
            c.setFillColorRGB(1, 1, 1)
            c.setFont(fn_b, 7)
            for i, hdr in enumerate(headers):
                cx = col_x[i] + col_w[i] / 2
                lines = hdr.split('\n')
                base_y = y_start - 8 if len(lines) == 1 else y_start - 5
                for j, line in enumerate(lines):
                    c.drawCentredString(cx, base_y - j * 8, line)
            c.setFillColorRGB(0, 0, 0)
            return y_start - hdr_h - 2

        def draw_row(y, rb, datum, temeljnica, opis, gotovina, virmanski, idx):
            rh = 14
            if idx % 2 == 0:
                c.setFillColorRGB(0.97, 0.97, 0.97)
                c.rect(MARGIN, y - rh + 2, W - 2 * MARGIN, rh, fill=1, stroke=0)
                c.setFillColorRGB(0, 0, 0)

            ukupno = round((gotovina or 0) + (virmanski or 0), 2)
            c.setFont(fn, 7.5)

            def tc(col_i, txt, right=False):
                x = col_x[col_i]
                w = col_w[col_i]
                if right:
                    c.drawRightString(x + w - 2, y - rh + 4, str(txt))
                else:
                    c.drawString(x + 2, y - rh + 4, str(txt))

            tc(0, rb, right=True)
            tc(1, datum or "")
            tc(2, temeljnica or "")
            # Skrati opis
            opis_str = str(opis or "")
            if len(opis_str) > 50:
                opis_str = opis_str[:48] + "…"
            tc(3, opis_str)

            c.setFont(fn_b, 7.5)
            tc(4, f"{fmt_iznos(gotovina)}" if gotovina else "-", right=True)
            tc(5, f"{fmt_iznos(virmanski)}" if virmanski else "-", right=True)
            tc(6, f"{fmt_iznos(ukupno)}", right=True)
            c.setFont(fn, 7.5)

            # Linija ispod retka
            c.setStrokeColorRGB(0.87, 0.87, 0.87)
            c.line(MARGIN, y - rh + 2, W - MARGIN, y - rh + 2)
            c.setStrokeColorRGB(0, 0, 0)
            return y - rh

        # ── Prva stranica ─────────────────────────────────────────────────────
        y = H - 15 * mm

        # Naslov
        c.setFont(fn_b, 14)
        c.drawCentredString(W / 2, y, "KNJIGA PROMETA")
        y -= 14

        c.setFont(fn, 8)
        c.setFillColorRGB(0.4, 0.4, 0.4)
        c.drawRightString(W - MARGIN, y, f"Obrazac - KPR   |   Godina: {self._godina}")
        c.setFillColorRGB(0, 0, 0)

        y -= 10
        c.setFont(fn, 7.5)
        c.drawString(MARGIN, y,
                     f"Porezni obveznik: {cfg.get('naziv_tvrtke', '')}   |   "
                     f"OIB: {cfg.get('oib', '')}   |   "
                     f"Adresa: {cfg.get('adresa', '')}")
        y -= 12

        # Linija
        c.setStrokeColorRGB(0.6, 0.6, 0.6)
        c.line(MARGIN, y, W - MARGIN, y)
        c.setStrokeColorRGB(0, 0, 0)
        y -= 6

        y = draw_header(y)

        # ── Redci ─────────────────────────────────────────────────────────────
        for idx, r in enumerate(rows):
            (db_id, rb, datum, temeljnica, opis,
             gotovina, virmanski, rucno) = r

            if y < 30 * mm:
                c.showPage()
                y = H - 15 * mm
                y = draw_header(y)

            y = draw_row(y, rb, datum, temeljnica, opis,
                         gotovina or 0, virmanski or 0, idx)

        # ── Ukupno ────────────────────────────────────────────────────────────
        uk_got = sum(r[5] or 0 for r in rows)
        uk_vir = sum(r[6] or 0 for r in rows)
        uk_uk = round(uk_got + uk_vir, 2)

        if y < 35 * mm:
            c.showPage()
            y = H - 15 * mm

        y -= 4
        c.setFillColorRGB(0.17, 0.24, 0.31)
        c.rect(MARGIN, y - 14, W - 2 * MARGIN, 16, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont(fn_b, 8)
        c.drawString(MARGIN + 4, y - 10, "UKUPNO")
        c.drawRightString(col_x[4] + col_w[4] - 2, y - 10, f"{fmt_iznos(uk_got)} EUR")
        c.drawRightString(col_x[5] + col_w[5] - 2, y - 10, f"{fmt_iznos(uk_vir)} EUR")
        c.drawRightString(col_x[6] + col_w[6] - 2, y - 10, f"{fmt_iznos(uk_uk)} EUR")
        c.setFillColorRGB(0, 0, 0)

        # Datum ispisa
        y -= 20
        c.setFont(fn, 7)
        c.setFillColorRGB(0.5, 0.5, 0.5)
        c.drawRightString(W - MARGIN, y,
                          f"Ispisano: {datetime.now().strftime('%d.%m.%Y %H:%M')}")
        c.setFillColorRGB(0, 0, 0)

        c.save()
        QMessageBox.information(
            self, "PDF exportiran",
            f"KPR za godinu {self._godina} exportirana u:\n{os.path.abspath(naziv)}")
