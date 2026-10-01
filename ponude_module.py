"""
ponude_module.py — Modul za kreiranje i upravljanje ponudama.

Ponuda sadrži iste podatke kao račun (kupac, stavke, iznos, IBAN)
ali nema fiskalizacije. Može se pretvoriti u račun jednim klikom.
"""
import os
import sys
import tempfile
from datetime import datetime

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QGroupBox, QFormLayout,
    QLineEdit, QCheckBox, QTextEdit, QComboBox, QMessageBox,
    QDateEdit, QDialog, QDialogButtonBox, QFrame, QScrollArea,
    QSizePolicy, QHeaderView
)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QFont, QColor
from format_util import fmt_iznos, parse_iznos
from novac import izracun_stavke, ukupno_stavki, u_centima, iz_centi

from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas as rl_canvas

from fontovi import get_font as _get_font


def _font():
    """Vraća (regular, bold) naziv fonta s podrškom za hrvatska slova."""
    if _get_font:
        try:
            return _get_font()
        except Exception:
            pass
    # Lokalni fallback — isti kandidati kao u glavnom modulu
    import os, reportlab
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    kandidati = [
        ("Arial",   r"C:\Windows\Fonts\arial.ttf",   r"C:\Windows\Fonts\arialbd.ttf"),
        ("Calibri", r"C:\Windows\Fonts\calibri.ttf", r"C:\Windows\Fonts\calibrib.ttf"),
        ("Segoe",   r"C:\Windows\Fonts\segoeui.ttf", r"C:\Windows\Fonts\segoeuib.ttf"),
        ("Verdana", r"C:\Windows\Fonts\verdana.ttf", r"C:\Windows\Fonts\verdanab.ttf"),
        ("DejaVuSans",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ("Arial", "/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
    ]
    for name, reg, bold in kandidati:
        if not os.path.exists(reg):
            continue
        try:
            pdfmetrics.registerFont(TTFont(name, reg))
            bold_name = name + "-Bold"
            if os.path.exists(bold):
                pdfmetrics.registerFont(TTFont(bold_name, bold))
            else:
                bold_name = name
            return name, bold_name
        except Exception:
            continue
    return "Helvetica", "Helvetica-Bold"

# ── Konstante statusa ponude ────────────────────────────────────────────────
STATUS_DRAFT      = "draft"
STATUS_POSLANO    = "poslano"
STATUS_PRETVORENO = "pretvoreno"
STATUS_OTKAZANO   = "otkazano"

STATUS_EMOJI = {
    STATUS_DRAFT:      "📝 Nacrt",
    STATUS_POSLANO:    "📤 Poslano",
    STATUS_PRETVORENO: "✅ Pretvoreno",
    STATUS_OTKAZANO:   "❌ Otkazano",
}

STATUS_BOJA = {
    STATUS_DRAFT:      QColor("#f0ad4e"),
    STATUS_POSLANO:    QColor("#5bc0de"),
    STATUS_PRETVORENO: QColor("#5cb85c"),
    STATUS_OTKAZANO:   QColor("#d9534f"),
}


# ── Inicijalizacija baze ────────────────────────────────────────────────────

def inicijaliziraj_ponude_tablice(conn, cursor):
    """Kreira tablice ponude i ponuda_stavke ako ne postoje."""
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ponude (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            broj_ponude     TEXT,
            datum           TEXT,
            rok_valjanosti  TEXT,
            status          TEXT DEFAULT 'draft',
            oib_izdavatelja TEXT,
            naziv_izdavatelja TEXT,
            adresa_izdavatelja TEXT,
            iban            TEXT,
            oib_kupca       TEXT,
            naziv_kupca     TEXT,
            adresa_kupca    TEXT,
            u_sustavu_pdv   INTEGER DEFAULT 0,
            ukupan_iznos_cent INTEGER DEFAULT 0,
            napomena        TEXT,
            operater        TEXT,
            datum_kreiranja TEXT,
            invoice_id      INTEGER,
            pdv_oslobodenje TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ponuda_stavke (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            ponuda_id   INTEGER,
            naziv       TEXT,
            kolicina    REAL,
            jedinica    TEXT,
            cijena_cent INTEGER,
            pdv_stopa   REAL DEFAULT 0,
            FOREIGN KEY (ponuda_id) REFERENCES ponude(id)
        )
    """)
    conn.commit()


def sljedeci_broj_ponude(cursor):
    """Generira sljedeći broj ponude u formatu PON-YYYY-NNN."""
    god = datetime.now().year
    row = cursor.execute("""
        SELECT COUNT(*) FROM ponude WHERE datum LIKE ?
    """, (f"%.{god}",)).fetchone()
    # Fallback — brojimo sve ponude ove godine po datumu kreacije
    row2 = cursor.execute("""
        SELECT COUNT(*) FROM ponude
        WHERE datum_kreiranja LIKE ?
    """, (f"{god}%",)).fetchone()
    n = (row2[0] if row2 else 0) + 1
    return f"PON-{god}-{n:03d}"


def _ponuda_u_dict(opis_stupaca, redak):
    """Red iz 'SELECT * FROM ponude' kao rječnik; iznos je u eurima
    ('ukupan_iznos'), a u bazi je u centima ('ukupan_iznos_cent')."""
    d = dict(zip([c[0] for c in opis_stupaca], redak))
    d['ukupan_iznos'] = iz_centi(d.get('ukupan_iznos_cent'))
    return d


def dohvati_stavke_ponude(cursor, ponuda_id):
    """Vraća stavke za danu ponudu kao listu dict-ova."""
    rows = cursor.execute("""
        SELECT naziv, kolicina, jedinica, cijena_cent / 100.0, pdv_stopa
        FROM ponuda_stavke WHERE ponuda_id=?
    """, (ponuda_id,)).fetchall()
    stavke = []
    for naziv, kolicina, jedinica, cijena, pdv_stopa in rows:
        red = izracun_stavke(kolicina, cijena, pdv_stopa or 0)
        stavke.append({
            'naziv': naziv,
            'kolicina': kolicina,
            'jedinica': jedinica,
            'cijena': cijena,
            'pdv_stopa': pdv_stopa,
            'ukupno_bez_pdv': red['osnovica'],
            'pdv_iznos': red['pdv_iznos'],
            'ukupno': red['ukupno'],
        })
    return stavke


# ── PDF za ponudu ───────────────────────────────────────────────────────────

def generiraj_pdf_ponude(ponuda: dict, stavke: list, config: dict,
                          fiskalizacija_obj=None):
    """
    Generira PDF za ponudu. Layout isti kao račun, ali:
    - Naslov: PONUDA  (ne RAČUN)
    - Prikazuje rok valjanosti (ne rok plaćanja)
    - IBAN uplatnica za transakcijsko plaćanje
    - Nema ZKI/JIR bloka
    - Pečat "PONUDA" u uglu
    """
    naziv_tvrtke = config.get('naziv_tvrtke', '')
    adresa       = config.get('adresa', '')
    oib          = config.get('oib', '')
    iban         = config.get('iban', '')

    broj_ponude    = ponuda.get('broj_ponude', '')
    datum          = ponuda.get('datum', '')
    rok_valjanosti = ponuda.get('rok_valjanosti', '')
    kupac_naziv    = ponuda.get('naziv_kupca', '')
    kupac_oib      = ponuda.get('oib_kupca', '')
    kupac_adresa   = ponuda.get('adresa_kupca', '')
    napomena       = ponuda.get('napomena', '')
    operater       = ponuda.get('operater', '')
    u_sustavu_pdv  = bool(ponuda.get('u_sustavu_pdv', 0))
    pdv_oslobodenje = ponuda.get('pdv_oslobodenje', '')
    ukupan_iznos   = ponuda.get('ukupan_iznos', 0.0)
    datum_kreiranja = ponuda.get('datum_kreiranja', '')
    if datum_kreiranja:
        try:
            dt_obj = datetime.fromisoformat(datum_kreiranja)
            vrijemeStr = dt_obj.strftime("%d.%m.%Y  %H:%M:%S")
        except Exception:
            vrijemeStr = datum_kreiranja
    else:
        vrijemeStr = datetime.now().strftime("%d.%m.%Y  %H:%M:%S")

    safe_br = broj_ponude.replace('/', '-').replace(' ', '_')
    filename = f"ponuda_{safe_br}.pdf"

    W, H = A4
    c = rl_canvas.Canvas(filename, pagesize=A4)

    fn, fn_b = _font()
    y    = H - 50

    def line(y_pos):
        c.setStrokeColorRGB(0.8, 0.8, 0.8)
        c.line(40, y_pos, W - 40, y_pos)
        c.setStrokeColorRGB(0, 0, 0)

    def text(x, y_pos, txt, font=fn, size=10, color=(0, 0, 0)):
        c.setFont(font, size)
        c.setFillColorRGB(*color)
        c.drawString(x, y_pos, str(txt))
        c.setFillColorRGB(0, 0, 0)

    def rtext(x, y_pos, txt, font=fn, size=10):
        c.setFont(font, size)
        c.drawRightString(x, y_pos, str(txt))

    # ── Zaglavlje ────────────────────────────────────────────────────────────
    text(40, y, naziv_tvrtke, fn_b, 14)
    text(40, y - 16, adresa, fn, 9, (0.4, 0.4, 0.4))
    text(40, y - 28, f"OIB: {oib}", fn, 9, (0.4, 0.4, 0.4))
    if iban:
        text(40, y - 40, f"IBAN: {iban}", fn, 9, (0.4, 0.4, 0.4))

    # Desno — naslov PONUDA
    rtext(W - 40, y,      "PONUDA",       fn_b, 18)
    rtext(W - 40, y - 20, broj_ponude,    fn_b, 12)
    rtext(W - 40, y - 35, f"Datum: {datum}", fn, 9)
    if rok_valjanosti:
        rtext(W - 40, y - 47, f"Vrijedi do: {rok_valjanosti}", fn, 9)
    rtext(W - 40, y - 59, f"Kreirano: {vrijemeStr}", fn, 8)
    if operater:
        rtext(W - 40, y - 70, f"Operater: {operater}", fn, 8)

    y -= 80
    line(y)
    y -= 15

    # ── Kupac ────────────────────────────────────────────────────────────────
    text(40, y, "KUPAC:", fn_b, 9, (0.5, 0.5, 0.5))
    y -= 14
    text(40, y, kupac_naziv, fn_b, 11)
    y -= 14
    if kupac_oib:
        text(40, y, f"OIB: {kupac_oib}", fn, 9)
        y -= 12
    if kupac_adresa:
        text(40, y, kupac_adresa, fn, 9)
        y -= 12

    y -= 8
    line(y)
    y -= 20

    # ── Tablica stavki ───────────────────────────────────────────────────────
    text(40, y, "STAVKE PONUDE", fn_b, 9, (0.5, 0.5, 0.5))
    y -= 14

    col_x = [40, 210, 260, 315, 370, 450, W - 40]

    c.setFillColorRGB(0.15, 0.15, 0.15)
    c.rect(40, y - 4, W - 80, 16, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(fn_b, 8)
    hdrs = ["Naziv / Opis", "Jed.", "Kol.", "Cijena", "PDV%",
            "Osnov.", "Ukupno"]
    for i, hdr in enumerate(hdrs):
        if i == len(hdrs) - 1:
            c.drawRightString(col_x[i], y + 1, hdr)
        else:
            c.drawString(col_x[i], y + 1, hdr)

    c.setFillColorRGB(0, 0, 0)
    y -= 18

    for idx, s in enumerate(stavke):
        if idx % 2 == 0:
            c.setFillColorRGB(0.97, 0.97, 0.97)
            c.rect(40, y - 4, W - 80, 15, fill=1, stroke=0)
            c.setFillColorRGB(0, 0, 0)

        c.setFont(fn, 8)
        naziv = s['naziv']
        if len(naziv) > 35:
            naziv = naziv[:33] + "..."
        c.drawString(col_x[0], y, naziv)
        c.drawString(col_x[1], y, s['jedinica'])
        c.drawRightString(col_x[2] + 40, y, f"{fmt_iznos(s['kolicina'])}")
        c.drawRightString(col_x[3] + 50, y, f"{fmt_iznos(s['cijena'])}")

        if u_sustavu_pdv:
            c.drawRightString(col_x[4] + 40, y, f"{s['pdv_stopa']:.0f}%")
            c.drawRightString(col_x[5] + 50, y, f"{fmt_iznos(s['ukupno_bez_pdv'])}")
        else:
            c.drawRightString(col_x[4] + 40, y, "-")
            c.drawRightString(col_x[5] + 50, y, "-")

        c.drawRightString(col_x[6], y, f"{fmt_iznos(s['ukupno'])}")
        y -= 16

        if y < 180:
            c.showPage()
            y = H - 60

    line(y)
    y -= 15

    # ── Rekapitulacija / PDV oslobođenje ─────────────────────────────────────
    if u_sustavu_pdv:
        pdv_grupe = {}
        for s in stavke:
            st = s['pdv_stopa']
            if st not in pdv_grupe:
                pdv_grupe[st] = {'osnov': 0, 'pdv': 0, 'ukupno': 0}
            pdv_grupe[st]['osnov']  += s['ukupno_bez_pdv']
            pdv_grupe[st]['pdv']    += s['pdv_iznos']
            pdv_grupe[st]['ukupno'] += s['ukupno']

        text(40, y, "REKAPITULACIJA PDV-a", fn_b, 8, (0.5, 0.5, 0.5))
        y -= 13
        for label, x in [("Stopa", 40), ("Osnovica", 120),
                          ("PDV iznos", 220), ("Ukupno", 320)]:
            text(x, y, label, fn_b, 8)
        y -= 12
        for stopa, v in sorted(pdv_grupe.items()):
            text(40,  y, f"{stopa:.0f}%",        fn, 8)
            text(120, y, f"{fmt_iznos(v['osnov'])} EUR", fn, 8)
            text(220, y, f"{fmt_iznos(v['pdv'])} EUR",   fn, 8)
            text(320, y, f"{fmt_iznos(v['ukupno'])} EUR",fn, 8)
            y -= 12
        y -= 5
        line(y)
        y -= 15
    else:
        napomena_pdv = (pdv_oslobodenje
                        or "Nije obveznik PDV-a — PDV nije obračunan")
        text(40, y, napomena_pdv, fn, 9, (0.3, 0.3, 0.7))
        y -= 20

    # ── Ukupan iznos ─────────────────────────────────────────────────────────
    c.setFillColorRGB(0.1, 0.1, 0.1)
    c.rect(W - 200, y - 6, 160, 22, fill=1, stroke=0)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(fn_b, 13)
    c.drawString(W - 195, y, "UKUPNO:")
    c.drawRightString(W - 45, y, f"{fmt_iznos(ukupan_iznos)} EUR")
    c.setFillColorRGB(0, 0, 0)
    y -= 30

    # ── IBAN uplatnica ───────────────────────────────────────────────────────
    if iban:
        line(y)
        y -= 12
        text(40, y, "Za uplatu koristite:", fn_b, 9)
        y -= 13
        text(40, y, f"IBAN: {iban}", fn_b, 10)
        y -= 13
        text(40, y, f"Poziv na broj: {broj_ponude}", fn, 9)
        if rok_valjanosti:
            y -= 13
            text(40, y, f"Ponuda vrijedi do: {rok_valjanosti}", fn, 9,
                 (0.6, 0.0, 0.0))
        y -= 15

    # ── Napomena ─────────────────────────────────────────────────────────────
    if napomena:
        line(y)
        y -= 12
        text(40, y, "Napomena:", fn_b, 9)
        y -= 13
        words = napomena.split()
        line_txt = ""
        for word in words:
            test = line_txt + (" " if line_txt else "") + word
            c.setFont(fn, 9)
            if c.stringWidth(test) > W - 100:
                text(40, y, line_txt, fn, 9)
                y -= 12
                line_txt = word
            else:
                line_txt = test
        if line_txt:
            text(40, y, line_txt, fn, 9)
            y -= 15

    # ── Podnožje ─────────────────────────────────────────────────────────────
    line(y)
    y -= 12
    text(40, y,
         "Ova ponuda nije fiskalni dokument. Plaćanjem prihvaćate uvjete ponude.",
         fn, 7, (0.6, 0.6, 0.6))

    c.showPage()
    c.save()
    print(f"✅ PDF ponude generiran: {filename}")
    return filename


# ── Dialog za novu / uređivanje ponude ─────────────────────────────────────

class NoScrollComboBox(QComboBox):
    def wheelEvent(self, e): e.ignore()


class NovaPonudaDialog(QDialog):
    """Dialog za kreiranje ili uređivanje ponude."""

    def __init__(self, conn, cursor, config, parent=None,
                 ponuda_data=None, stavke_data=None):
        super().__init__(parent)
        self.conn   = conn
        self.cursor = cursor
        self.config = config
        self._edit_id = ponuda_data.get('id') if ponuda_data else None

        self.setWindowTitle(
            "Uredi ponudu" if self._edit_id else "Nova ponuda")
        self.setMinimumSize(1100, 700)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        c_layout = QVBoxLayout(container)
        c_layout.setSpacing(8)

        # ── Kupac + Detalji ponude — jedan do drugog ────────────────────
        gornji_red = QHBoxLayout()
        gornji_red.setSpacing(10)

        # Kupac (lijevo)
        kupac_group = QGroupBox("Kupac")
        kl = QFormLayout()
        kl.setSpacing(5)
        kl.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.naziv_input   = QLineEdit(ponuda_data.get('naziv_kupca', '') if ponuda_data else '')
        self.oib_input     = QLineEdit(ponuda_data.get('oib_kupca', '')   if ponuda_data else '')
        self.adresa_input  = QLineEdit(ponuda_data.get('adresa_kupca', '') if ponuda_data else '')
        self.oib_input.setMaxLength(11)
        naziv_row = QWidget()
        naziv_layout = QHBoxLayout(naziv_row)
        naziv_layout.setContentsMargins(0, 0, 0, 0)
        naziv_layout.setSpacing(6)
        naziv_layout.addWidget(self.naziv_input)
        odaberi_kupca_btn = QPushButton("📋")
        odaberi_kupca_btn.setFixedWidth(36)
        odaberi_kupca_btn.setToolTip("Odaberi kupca iz imenika")
        odaberi_kupca_btn.clicked.connect(self._otvori_odabir_kupca)
        naziv_layout.addWidget(odaberi_kupca_btn)
        kl.addRow("Naziv *:", naziv_row)
        kl.addRow("OIB:",     self.oib_input)
        kl.addRow("Adresa:",  self.adresa_input)
        kupac_group.setLayout(kl)
        gornji_red.addWidget(kupac_group, stretch=1)

        # Detalji ponude (desno)
        det_group = QGroupBox("Detalji ponude")
        dl = QFormLayout()
        dl.setSpacing(5)
        dl.setFieldGrowthPolicy(
            QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        self.datum_input = QDateEdit()
        self.datum_input.setCalendarPopup(True)
        self.datum_input.setDisplayFormat("dd.MM.yyyy")
        if ponuda_data and ponuda_data.get('datum'):
            try:
                dt = datetime.strptime(ponuda_data['datum'], "%d.%m.%Y")
                self.datum_input.setDate(QDate(dt.year, dt.month, dt.day))
            except Exception:
                self.datum_input.setDate(QDate.currentDate())
        else:
            self.datum_input.setDate(QDate.currentDate())

        self.rok_input = QDateEdit()
        self.rok_input.setCalendarPopup(True)
        self.rok_input.setDisplayFormat("dd.MM.yyyy")
        if ponuda_data and ponuda_data.get('rok_valjanosti'):
            try:
                dt = datetime.strptime(ponuda_data['rok_valjanosti'], "%d.%m.%Y")
                self.rok_input.setDate(QDate(dt.year, dt.month, dt.day))
            except Exception:
                self.rok_input.setDate(QDate.currentDate().addDays(30))
        else:
            self.rok_input.setDate(QDate.currentDate().addDays(30))

        self.operater_input = QLineEdit(
            ponuda_data.get('operater', config.get('operater', ''))
            if ponuda_data else config.get('operater', ''))

        self.pdv_check = QCheckBox("U sustavu PDV-a")
        self.pdv_check.setChecked(
            bool(ponuda_data.get('u_sustavu_pdv', 0)) if ponuda_data else False)

        dl.addRow("Datum ponude:", self.datum_input)
        dl.addRow("Rok valjanosti:", self.rok_input)
        dl.addRow("Operater:", self.operater_input)
        dl.addRow("PDV:", self.pdv_check)
        det_group.setLayout(dl)
        gornji_red.addWidget(det_group, stretch=1)

        c_layout.addLayout(gornji_red)

        # ── Stavke ───────────────────────────────────────────────────────
        stavke_group = QGroupBox("Stavke")
        sl = QVBoxLayout()

        self.stavke_tabla = QTableWidget()
        self.stavke_tabla.setColumnCount(6)
        self.stavke_tabla.setHorizontalHeaderLabels(
            ["Naziv", "Kol.", "Jed.", "Cijena (EUR)", "PDV %", "Ukupno"])
        self.stavke_tabla.setMinimumHeight(190)
        self.stavke_tabla.verticalHeader().setDefaultSectionSize(46)
        self.stavke_tabla.horizontalHeader().setMinimumSectionSize(60)
        self.stavke_tabla.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        self.stavke_tabla.setColumnWidth(1, 75)
        self.stavke_tabla.setColumnWidth(2, 70)
        self.stavke_tabla.setColumnWidth(3, 115)
        self.stavke_tabla.setColumnWidth(4, 75)
        self.stavke_tabla.setColumnWidth(5, 105)
        self.stavke_tabla.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.Fixed)
        self.stavke_tabla.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.Fixed)
        self.stavke_tabla.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.ResizeMode.Fixed)
        self.stavke_tabla.horizontalHeader().setSectionResizeMode(
            4, QHeaderView.ResizeMode.Fixed)
        self.stavke_tabla.horizontalHeader().setSectionResizeMode(
            5, QHeaderView.ResizeMode.Fixed)
        sl.addWidget(self.stavke_tabla)

        stavke_btn_row = QHBoxLayout()
        dodaj_btn = QPushButton("➕ Dodaj stavku")
        dodaj_btn.setObjectName("addItemBtn")
        dodaj_btn.clicked.connect(self._dodaj_stavku_red)
        ukloni_btn = QPushButton("🗑️ Ukloni stavku")
        ukloni_btn.setObjectName("removeItemBtn")
        ukloni_btn.clicked.connect(self._ukloni_stavku)
        stavke_btn_row.addWidget(dodaj_btn)
        stavke_btn_row.addWidget(ukloni_btn)
        stavke_btn_row.addStretch()
        sl.addLayout(stavke_btn_row)
        stavke_group.setLayout(sl)
        c_layout.addWidget(stavke_group)

        # ── Napomena ─────────────────────────────────────────────────────
        nap_group = QGroupBox("Napomena")
        nl = QVBoxLayout()
        self.napomena_input = QTextEdit()
        self.napomena_input.setMaximumHeight(70)
        self.napomena_input.setPlaceholderText(
            "Posebni uvjeti, napomene, rok isporuke...")
        if ponuda_data and ponuda_data.get('napomena'):
            self.napomena_input.setPlainText(ponuda_data['napomena'])
        nl.addWidget(self.napomena_input)
        nap_group.setLayout(nl)
        c_layout.addWidget(nap_group)

        scroll.setWidget(container)
        layout.addWidget(scroll)

        # ── Gumbi ────────────────────────────────────────────────────────
        btn_box = QDialogButtonBox()
        self.spremi_btn = btn_box.addButton(
            "💾  Spremi ponudu", QDialogButtonBox.ButtonRole.AcceptRole)
        self.spremi_pdf_btn = btn_box.addButton(
            "💾 + 📄  Spremi i generiraj PDF",
            QDialogButtonBox.ButtonRole.ActionRole)
        odustani_btn = btn_box.addButton(
            "Odustani", QDialogButtonBox.ButtonRole.RejectRole)

        self.spremi_btn.clicked.connect(lambda: self._spremi(pdf=False))
        self.spremi_pdf_btn.clicked.connect(lambda: self._spremi(pdf=True))
        odustani_btn.clicked.connect(self.reject)
        layout.addWidget(btn_box)

        # Popuni stavke ako se uređuje
        if stavke_data:
            for s in stavke_data:
                self._dodaj_stavku_red(s)
        else:
            self._dodaj_stavku_red()

        self._rezultat = None  # ponuda_id nakon snimanja

    def _otvori_odabir_kupca(self):
        """Otvara postojeći imenik kupaca iz glavne aplikacije."""
        billing_app = self.parent().window() if self.parent() else None
        if billing_app is None:
            QMessageBox.warning(
                self, "Odabir kupca",
                "Nije moguće pronaći glavni prozor aplikacije.")
            return

        module = sys.modules.get(billing_app.__class__.__module__)
        dialog_class = getattr(module, "OdabirKupcaDialog", None) \
            if module else None
        if dialog_class is None:
            QMessageBox.warning(
                self, "Odabir kupca",
                "Dijalog za odabir kupca nije dostupan.")
            return

        self._kupac_dialog = dialog_class(self)
        self._kupac_dialog.kupac_odabran.connect(self._popuni_kupca)
        self._kupac_dialog.show_above_parent()

    def _popuni_kupca(self, podaci):
        """Popunjava podatke ponude odabranim kupcem."""
        self.naziv_input.setText(podaci.get("naziv", ""))
        self.oib_input.setText(podaci.get("oib", ""))
        self.adresa_input.setText(podaci.get("adresa", ""))

    def _dodaj_stavku_red(self, s=None):
        row = self.stavke_tabla.rowCount()
        self.stavke_tabla.insertRow(row)
        self.stavke_tabla.setItem(row, 0, QTableWidgetItem(
            s['naziv'] if s else ''))
        self.stavke_tabla.setItem(row, 1, QTableWidgetItem(
            str(s['kolicina']) if s else '1'))
        self.stavke_tabla.setItem(row, 2, QTableWidgetItem(
            s['jedinica'] if s else 'kom'))
        self.stavke_tabla.setItem(row, 3, QTableWidgetItem(
            str(s['cijena']) if s else '0.00'))
        self.stavke_tabla.setItem(row, 4, QTableWidgetItem(
            str(s['pdv_stopa']) if s else '0'))
        ukupno = s['ukupno'] if s else 0.0
        item = QTableWidgetItem(f"{fmt_iznos(ukupno)}")
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.stavke_tabla.setItem(row, 5, item)
        self.stavke_tabla.cellChanged.connect(self._osvjezi_ukupno)

    def _ukloni_stavku(self):
        row = self.stavke_tabla.currentRow()
        if row >= 0:
            self.stavke_tabla.removeRow(row)

    def _osvjezi_ukupno(self, row, col):
        if col not in (1, 3, 4):
            return
        try:
            kol    = parse_iznos(self.stavke_tabla.item(row, 1).text() or 0)
            cijena = parse_iznos(self.stavke_tabla.item(row, 3).text() or 0)
            pdv    = parse_iznos(self.stavke_tabla.item(row, 4).text() or 0)
            ukupno = izracun_stavke(kol, cijena, pdv)['ukupno']
            self.stavke_tabla.blockSignals(True)
            self.stavke_tabla.item(row, 5).setText(f"{fmt_iznos(ukupno)}")
            self.stavke_tabla.blockSignals(False)
        except Exception:
            pass

    def _get_stavke(self):
        stavke = []
        for row in range(self.stavke_tabla.rowCount()):
            def cell(c):
                item = self.stavke_tabla.item(row, c)
                return item.text().strip() if item else ''
            naziv = cell(0)
            if not naziv:
                continue
            try:
                kolicina = parse_iznos(cell(1) or 1)
                cijena   = parse_iznos(cell(3) or 0)
                pdv      = parse_iznos(cell(4) or 0)
            except ValueError:
                kolicina, cijena, pdv = 1.0, 0.0, 0.0
            _r       = izracun_stavke(kolicina, cijena, pdv)
            osnov, pdv_izn = _r['osnovica'], _r['pdv_iznos']
            stavke.append({
                'naziv': naziv,
                'kolicina': kolicina,
                'jedinica': cell(2) or 'kom',
                'cijena': cijena,
                'pdv_stopa': pdv,
                'ukupno_bez_pdv': osnov,
                'pdv_iznos': pdv_izn,
                'ukupno': _r['ukupno'],
            })
        return stavke

    def _spremi(self, pdf=False):
        naziv = self.naziv_input.text().strip()
        if not naziv:
            QMessageBox.warning(self, "Greška", "Naziv kupca je obavezan!")
            return
        stavke = self._get_stavke()
        if not stavke:
            QMessageBox.warning(self, "Greška", "Dodajte barem jednu stavku!")
            return

        oib_kupca  = self.oib_input.text().strip()
        adresa     = self.adresa_input.text().strip()
        datum      = self.datum_input.date().toString("dd.MM.yyyy")
        rok_val    = self.rok_input.date().toString("dd.MM.yyyy")
        operater   = self.operater_input.text().strip()
        u_pdv      = int(self.pdv_check.isChecked())
        napomena   = self.napomena_input.toPlainText().strip()
        ukupno     = ukupno_stavki(stavke)
        now_iso    = datetime.now().isoformat()

        if self._edit_id:
            self.cursor.execute("""
                UPDATE ponude SET
                    datum=?, rok_valjanosti=?, oib_kupca=?, naziv_kupca=?,
                    adresa_kupca=?, u_sustavu_pdv=?, ukupan_iznos_cent=?,
                    napomena=?, operater=?
                WHERE id=?
            """, (datum, rok_val, oib_kupca, naziv, adresa, u_pdv,
                  u_centima(ukupno), napomena, operater, self._edit_id))
            self.conn.commit()
            self.cursor.execute(
                "DELETE FROM ponuda_stavke WHERE ponuda_id=?",
                (self._edit_id,))
            ponuda_id = self._edit_id
        else:
            broj = sljedeci_broj_ponude(self.cursor)
            oib_izd   = self.config.get('oib', '')
            naziv_izd = self.config.get('naziv_tvrtke', '')
            adresa_izd= self.config.get('adresa', '')
            iban_izd  = self.config.get('iban', '')
            self.cursor.execute("""
                INSERT INTO ponude (
                    broj_ponude, datum, rok_valjanosti,
                    oib_izdavatelja, naziv_izdavatelja, adresa_izdavatelja, iban,
                    oib_kupca, naziv_kupca, adresa_kupca,
                    u_sustavu_pdv, ukupan_iznos_cent, napomena,
                    operater, datum_kreiranja, status
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (broj, datum, rok_val,
                  oib_izd, naziv_izd, adresa_izd, iban_izd,
                  oib_kupca, naziv, adresa,
                  u_pdv, u_centima(ukupno), napomena,
                  operater, now_iso, STATUS_DRAFT))
            self.conn.commit()
            ponuda_id = self.cursor.lastrowid

        for s in stavke:
            self.cursor.execute("""
                INSERT INTO ponuda_stavke
                (ponuda_id, naziv, kolicina, jedinica, cijena_cent, pdv_stopa)
                VALUES (?,?,?,?,?,?)
            """, (ponuda_id, s['naziv'], s['kolicina'],
                  s['jedinica'], u_centima(s['cijena']), s['pdv_stopa']))
        self.conn.commit()

        self._rezultat = ponuda_id

        if pdf:
            ponuda_row = self.cursor.execute(
                "SELECT * FROM ponude WHERE id=?", (ponuda_id,)
            ).fetchone()
            ponuda_dict = _ponuda_u_dict(self.cursor.description, ponuda_row)
            generiraj_pdf_ponude(ponuda_dict, stavke, self.config)
            QMessageBox.information(
                self, "Uspjeh",
                f"Ponuda {ponuda_dict['broj_ponude']} spremljena!\n"
                f"PDF generiran: ponuda_{ponuda_dict['broj_ponude'].replace('/', '-')}.pdf"
            )
        else:
            QMessageBox.information(
                self, "Uspjeh", "Ponuda uspješno spremljena!")

        self.accept()


# ── Glavni widget za pregled ponuda ────────────────────────────────────────

class PonudeWidget(QWidget):
    """Tab za pregled i upravljanje ponudama."""

    # Signal za BillingApp — traži kreiranje računa iz ponude
    pretvori_u_racun = Signal(dict, list)

    def __init__(self, conn, cursor, config_getter, parent=None):
        super().__init__(parent)
        self._conn         = conn
        self._cursor       = cursor
        self._config_getter = config_getter  # callable koji vraća trenutni config

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Filter / akcijska traka ──────────────────────────────────────
        top_row = QHBoxLayout()

        self.filter_combo = NoScrollComboBox()
        self.filter_combo.addItems(["Sve ponude", "Nacrt", "Poslano",
                                    "Pretvoreno", "Otkazano"])
        self.filter_combo.setFixedWidth(150)
        self.filter_combo.currentIndexChanged.connect(self.osvjezi)
        top_row.addWidget(QLabel("Filtriraj:"))
        top_row.addWidget(self.filter_combo)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍 Pretraži po kupcu, broju...")
        self.search_input.textChanged.connect(self.osvjezi)
        top_row.addWidget(self.search_input)

        top_row.addStretch()

        nova_btn = QPushButton("➕  Nova ponuda")
        nova_btn.clicked.connect(self.otvori_novu_ponudu)
        top_row.addWidget(nova_btn)

        layout.addLayout(top_row)

        # ── Tablica ponuda ───────────────────────────────────────────────
        self.tabla = QTableWidget()
        self.tabla.setColumnCount(8)
        self.tabla.setHorizontalHeaderLabels([
            "Br. ponude", "Datum", "Vrijedi do", "Kupac",
            "Iznos (EUR)", "Status", "Operater", "Račun br."
        ])
        self.tabla.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.tabla.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setShowGrid(True)
        self.tabla.setSortingEnabled(True)
        self.tabla.doubleClicked.connect(self._uredi_ponudu)

        self.tabla.setColumnWidth(0, 120)
        self.tabla.setColumnWidth(1, 90)
        self.tabla.setColumnWidth(2, 90)
        self.tabla.setColumnWidth(3, 170)
        self.tabla.setColumnWidth(4, 90)
        self.tabla.setColumnWidth(5, 100)
        self.tabla.setColumnWidth(6, 100)
        self.tabla.horizontalHeader().setStretchLastSection(True)

        self.tabla.setStyleSheet("""
            QTableWidget { border: 1px solid #d0d4db; border-radius: 6px; }
            QHeaderView::section {
                background-color: #2c3e50; color: white;
                font-weight: bold; padding: 5px 4px;
                border: none; font-size: 9px;
            }
        """)
        layout.addWidget(self.tabla)

        # ── Gumbi na dnu ─────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self.uredi_btn = QPushButton("✏️  Uredi")
        self.uredi_btn.setEnabled(False)
        self.uredi_btn.clicked.connect(self._uredi_ponudu)
        btn_row.addWidget(self.uredi_btn)

        self.pdf_btn = QPushButton("📄  Generiraj PDF")
        self.pdf_btn.setEnabled(False)
        self.pdf_btn.clicked.connect(self._generiraj_pdf)
        btn_row.addWidget(self.pdf_btn)

        self.status_btn = QPushButton("📤  Označi kao poslano")
        self.status_btn.setEnabled(False)
        self.status_btn.clicked.connect(self._oznaci_poslano)
        btn_row.addWidget(self.status_btn)

        self.otkazi_btn = QPushButton("❌  Otkaži")
        self.otkazi_btn.setEnabled(False)
        self.otkazi_btn.setObjectName("dangerBtn")
        self.otkazi_btn.clicked.connect(self._otkazi_ponudu)
        btn_row.addWidget(self.otkazi_btn)

        btn_row.addStretch()

        self.pretvori_btn = QPushButton("🧾  Pretvori u račun")
        self.pretvori_btn.setEnabled(False)
        self.pretvori_btn.setObjectName("saveBtn")
        self.pretvori_btn.clicked.connect(self._pretvori_u_racun)
        btn_row.addWidget(self.pretvori_btn)

        layout.addLayout(btn_row)

        self.tabla.selectionModel().selectionChanged.connect(
            self._on_selection_changed)

        self.osvjezi()

    def _on_selection_changed(self):
        row = self.tabla.currentRow()
        has = row >= 0
        self.uredi_btn.setEnabled(has)
        self.pdf_btn.setEnabled(has)

        if has:
            status = self._get_status(row)
            self.status_btn.setEnabled(status == STATUS_DRAFT)
            pretvorivo = status in (STATUS_DRAFT, STATUS_POSLANO)
            self.pretvori_btn.setEnabled(pretvorivo)
            otkazi_se = status in (STATUS_DRAFT, STATUS_POSLANO)
            self.otkazi_btn.setEnabled(otkazi_se)
        else:
            self.status_btn.setEnabled(False)
            self.pretvori_btn.setEnabled(False)
            self.otkazi_btn.setEnabled(False)

    def _get_ponuda_id(self, row=None):
        if row is None:
            row = self.tabla.currentRow()
        if row < 0:
            return None
        item = self.tabla.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def _get_status(self, row=None):
        if row is None:
            row = self.tabla.currentRow()
        if row < 0:
            return None
        item = self.tabla.item(row, 5)
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def osvjezi(self):
        filter_idx = self.filter_combo.currentIndex()
        status_filter = [None, STATUS_DRAFT, STATUS_POSLANO,
                         STATUS_PRETVORENO, STATUS_OTKAZANO][filter_idx]
        tekst = self.search_input.text().strip().lower()

        rows = self._cursor.execute("""
            SELECT id, broj_ponude, datum, rok_valjanosti, naziv_kupca,
                   ukupan_iznos_cent / 100.0, status, operater, invoice_id
            FROM ponude ORDER BY id DESC
        """).fetchall()

        self.tabla.setRowCount(0)
        for (id_, broj, datum, rok, kupac, iznos,
             status, operater, invoice_id) in rows:

            if status_filter and status != status_filter:
                continue
            if tekst:
                pretr = " ".join(filter(None, [
                    str(id_), broj or '', datum or '',
                    kupac or '', operater or ''])).lower()
                if tekst not in pretr:
                    continue

            r = self.tabla.rowCount()
            self.tabla.insertRow(r)

            def ci(txt, align=Qt.AlignmentFlag.AlignLeft):
                item = QTableWidgetItem(str(txt))
                item.setTextAlignment(align | Qt.AlignmentFlag.AlignVCenter)
                return item

            br_item = ci(broj or str(id_))
            br_item.setData(Qt.ItemDataRole.UserRole, id_)
            self.tabla.setItem(r, 0, br_item)
            self.tabla.setItem(r, 1, ci(datum or ''))
            self.tabla.setItem(r, 2, ci(rok or ''))
            self.tabla.setItem(r, 3, ci(kupac or ''))
            self.tabla.setItem(r, 4, ci(
                f"{fmt_iznos(iznos)}" if iznos else '0,00',
                Qt.AlignmentFlag.AlignRight))

            st_tekst = STATUS_EMOJI.get(status or STATUS_DRAFT, status or '')
            st_item = QTableWidgetItem(st_tekst)
            st_item.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter)
            st_item.setForeground(STATUS_BOJA.get(
                status or STATUS_DRAFT, QColor("#555")))
            st_item.setData(Qt.ItemDataRole.UserRole, status)
            self.tabla.setItem(r, 5, st_item)

            self.tabla.setItem(r, 6, ci(operater or ''))

            racun_br = ''
            if invoice_id:
                row_inv = self._cursor.execute(
                    "SELECT broj_racuna FROM invoices WHERE id=?",
                    (invoice_id,)).fetchone()
                racun_br = row_inv[0] if row_inv else str(invoice_id)
            self.tabla.setItem(r, 7, ci(racun_br))

        self._on_selection_changed()

    def otvori_novu_ponudu(self):
        dlg = NovaPonudaDialog(
            self._conn, self._cursor,
            self._config_getter(), parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.osvjezi()

    def _uredi_ponudu(self):
        ponuda_id = self._get_ponuda_id()
        if not ponuda_id:
            return
        row_data = self._cursor.execute(
            "SELECT * FROM ponude WHERE id=?", (ponuda_id,)).fetchone()
        if not row_data:
            return
        ponuda_dict = _ponuda_u_dict(self._cursor.description, row_data)
        stavke = dohvati_stavke_ponude(self._cursor, ponuda_id)

        dlg = NovaPonudaDialog(
            self._conn, self._cursor,
            self._config_getter(),
            parent=self,
            ponuda_data=ponuda_dict,
            stavke_data=stavke)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self.osvjezi()

    def _generiraj_pdf(self):
        ponuda_id = self._get_ponuda_id()
        if not ponuda_id:
            return
        row_data = self._cursor.execute(
            "SELECT * FROM ponude WHERE id=?", (ponuda_id,)).fetchone()
        ponuda_dict = _ponuda_u_dict(self._cursor.description, row_data)
        stavke = dohvati_stavke_ponude(self._cursor, ponuda_id)
        fname = generiraj_pdf_ponude(ponuda_dict, stavke, self._config_getter())
        QMessageBox.information(self, "PDF generiran",
                                f"PDF ponude spremnjen:\n{fname}")

    def _oznaci_poslano(self):
        ponuda_id = self._get_ponuda_id()
        if not ponuda_id:
            return
        self._cursor.execute(
            "UPDATE ponude SET status=? WHERE id=?",
            (STATUS_POSLANO, ponuda_id))
        self._conn.commit()
        self.osvjezi()

    def _otkazi_ponudu(self):
        ponuda_id = self._get_ponuda_id()
        if not ponuda_id:
            return
        odg = QMessageBox.question(
            self, "Otkazivanje ponude",
            "Jeste li sigurni da želite otkazati ovu ponudu?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if odg == QMessageBox.StandardButton.Yes:
            self._cursor.execute(
                "UPDATE ponude SET status=? WHERE id=?",
                (STATUS_OTKAZANO, ponuda_id))
            self._conn.commit()
            self.osvjezi()

    def _pretvori_u_racun(self):
        ponuda_id = self._get_ponuda_id()
        if not ponuda_id:
            return
        status = self._get_status()
        if status == STATUS_PRETVORENO:
            QMessageBox.information(
                self, "Info", "Ova ponuda već je pretvorena u račun.")
            return

        row_data = self._cursor.execute(
            "SELECT * FROM ponude WHERE id=?", (ponuda_id,)).fetchone()
        ponuda_dict = _ponuda_u_dict(self._cursor.description, row_data)
        stavke = dohvati_stavke_ponude(self._cursor, ponuda_id)

        # Emitira signal prema BillingApp koji će popuniti formu
        self.pretvori_u_racun.emit(ponuda_dict, stavke)
