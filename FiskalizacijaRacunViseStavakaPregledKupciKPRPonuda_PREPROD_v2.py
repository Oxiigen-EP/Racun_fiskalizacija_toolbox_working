import sys
import os
import sqlite3
import json
import hashlib
import uuid
import tempfile
from datetime import datetime
from io import BytesIO

from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QLineEdit, QPushButton, QMessageBox,
                               QGroupBox, QFormLayout, QCheckBox, QComboBox,
                               QTableWidget, QTableWidgetItem, QHeaderView,
                               QDateEdit, QTextEdit, QScrollArea, QFrame)
from PySide6.QtCore import Qt, QDate, Signal
from PySide6.QtGui import QFont, QColor

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.platypus import Table, TableStyle

import lxml.etree as etree
import base64
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.backends import default_backend
from cryptography.x509 import load_pem_x509_certificate

try:
    import qrcode
    QR_DOSTUPAN = True
except ImportError:
    QR_DOSTUPAN = False
    print("⚠️  qrcode nije instaliran: pip install qrcode[pil]")

from kpr_module import inicijaliziraj_kpr_tablicu, KPRWidget, _dodaj_u_kpr
from detalji_racuna import DetaljiRacunaDialog
from ponude_module import (inicijaliziraj_ponude_tablice, PonudeWidget,
                            STATUS_PRETVORENO)
from theme_module import ThemeManager


CONFIG_PATH = "config.json"

# Zakonski tekstovi oslobođenja od PDV-a (indeks = redoslijed u dropdownu)
PDV_OSLOBODENJE_TEKSTOVI = [
    "Oslobođeno PDV-a temeljem članka 90. st. 1 Zakona o PDV-u",
    "Oslobođeno PDV-a temeljem članka 90. st. 2 Zakona o PDV-u",
    "Oslobođeno PDV-a temeljem članka 17. st. 1 Zakona o PDV-u - reverse charge",
]

def učitaj_config():
    """Učitava konfiguraciju iz JSON datoteke."""
    defaults = {
        "oib": "",
        "naziv_tvrtke": "",
        "adresa": "",
        "iban": "",
        "oznaka_pp": "PP1",
        "oznaka_nu": "1",
        "cert_path": "",
        "key_path": "",
        "key_password": "",
        "demo_mode": True,
        "operater": ""
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                defaults.update(data)
                print(f"✅ Konfiguracija učitana iz {CONFIG_PATH}")
        except Exception as e:
            print(f"⚠️  Greška pri čitanju konfiguracije: {e}")
    return defaults

def spremi_config(data):
    """Sprema konfiguraciju u JSON datoteku."""
    try:
        with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"✅ Konfiguracija spremljena u {CONFIG_PATH}")
    except Exception as e:
        print(f"⚠️  Greška pri spremanju konfiguracije: {e}")

# Na početku datoteke, nakon importa — dva mala helper razreda

class NoScrollDateEdit(QDateEdit):
    """QDateEdit bez promjene vrijednosti na scroll."""
    def wheelEvent(self, event):
        event.ignore()

class NoScrollComboBox(QComboBox):
    """QComboBox bez promjene vrijednosti na scroll."""
    def wheelEvent(self, event):
        event.ignore()

class Fiskalizacija:
    FISKAL_URL_DEMO = "https://cistest.apis-it.hr:8449/FiskalizacijaServiceTest"
    FISKAL_URL_PROD = "https://cis.porezna-uprava.hr:8449/FiskalizacijaService"

    def __init__(self, cert_path, key_path, key_password=None, demo=True):
        self.cert_path = cert_path
        self.key_path = key_path
        self.key_password = key_password
        self.url = self.FISKAL_URL_DEMO if demo else self.FISKAL_URL_PROD

        with open(key_path, 'rb') as f:
            self.private_key = serialization.load_pem_private_key(
                f.read(),
                password=key_password.encode() if key_password else None,
                backend=default_backend()
            )
        with open(cert_path, 'rb') as f:
            self.certificate = load_pem_x509_certificate(
                f.read(), default_backend())

    def generiraj_zki(self, oib, datum_vrijeme, broj_racuna,
                      oznaka_poslovnog_prostora, oznaka_naplatnog_uredaja,
                      ukupan_iznos):
        iznos_str = f"{ukupan_iznos:.2f}".replace('.', ',')
        data = (f"{oib}{datum_vrijeme}{broj_racuna}"
                f"{oznaka_poslovnog_prostora}{oznaka_naplatnog_uredaja}{iznos_str}")
        signature = self.private_key.sign(
            data.encode('utf-8'), padding.PKCS1v15(), hashes.SHA1())
        return hashlib.md5(signature).hexdigest()

    # def generiraj_qr_kod(self, jir=None, zki=None, datum_vrijeme=None,
    #                      ukupan_iznos=None):
    #     if not QR_DOSTUPAN:
    #         return None
    #     if not (jir or zki):
    #         raise ValueError("Mora biti naveden JIR ili ZKI!")
    #
    #     dt = (datetime.strptime(datum_vrijeme, "%d.%m.%YT%H:%M:%S")
    #           if isinstance(datum_vrijeme, str) else datum_vrijeme)
    #
    #     datv = dt.strftime("%Y%m%d_%H%M")
    #     iznos_str = f"{ukupan_iznos:.2f}".replace('.', ',')
    #
    #     if jir:
    #         qr_url = f"https://porezna.gov.hr/rn?jir={jir}&datv={datv}&izn={iznos_str}"
    #     else:
    #         qr_url = f"https://porezna.gov.hr/rn?zki={zki}&datv={datv}&izn={iznos_str}"
    #
    #     print(f"📱 QR URL: {qr_url}")
    #     qr = qrcode.QRCode(
    #         version=None,
    #         error_correction=qrcode.constants.ERROR_CORRECT_L,
    #         box_size=10,
    #         border=4,
    #     )
    #     qr.add_data(qr_url)
    #     qr.make(fit=True)
    #     return qr.make_image(fill_color="black", back_color="white")
    #
    # def qr_kod_kao_bytes(self, jir=None, zki=None, datum_vrijeme=None,
    #                      ukupan_iznos=None):
    #     img = self.generiraj_qr_kod(jir, zki, datum_vrijeme, ukupan_iznos)
    #     if img is None:
    #         return None
    #     buf = BytesIO()
    #     img.save(buf, format='PNG')
    #     buf.seek(0)
    #     return buf
    def generiraj_qr_kod(self, jir=None, zki=None, datum_vrijeme=None,
                         ukupan_iznos=None):
        """
        Generira QR kod prema Pravilniku o fiskalizaciji (čl. 18.a–18.c).

        Format:
          S JIR-om : https://porezna.gov.hr/rn?jir=<JIR>&datv=<YYYYMMDD_HHMM>&izn=<iznos>
          Sa ZKI-om: https://porezna.gov.hr/rn?zki=<ZKI>&datv=<YYYYMMDD_HHMM>&izn=<iznos>
        """
        if not QR_DOSTUPAN:
            return None
        if not (jir or zki):
            raise ValueError("Mora biti naveden JIR ili ZKI!")
        if datum_vrijeme is None or ukupan_iznos is None:
            raise ValueError("Datum/vrijeme i iznos su obavezni!")

        if isinstance(datum_vrijeme, str):
            dt = datetime.strptime(datum_vrijeme, "%d.%m.%YT%H:%M:%S")
        else:
            dt = datum_vrijeme

        datv = dt.strftime("%Y%m%d_%H%M")
        iznos_str = f"{ukupan_iznos:.2f}".replace('.', ',')

        if jir:
            qr_url = f"https://porezna.gov.hr/rn?jir={jir}&datv={datv}&izn={iznos_str}"
        else:
            qr_url = f"https://porezna.gov.hr/rn?zki={zki}&datv={datv}&izn={iznos_str}"

        print(f"📱 QR URL: {qr_url}")

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_url)
        qr.make(fit=True)
        return qr.make_image(fill_color="black", back_color="white")

    def qr_kod_kao_bytes(self, jir=None, zki=None, datum_vrijeme=None,
                         ukupan_iznos=None):
        img = self.generiraj_qr_kod(jir, zki, datum_vrijeme, ukupan_iznos)
        if img is None:
            return None
        buf = BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        return buf

    def _potpiši_xml_enveloped(self, xml_string):
        try:
            print("🔐 Potpisujem XML (RSA-SHA1)...")
            root = etree.fromstring(xml_string.encode('utf-8'))
            canonical_root = etree.tostring(root, method='c14n', exclusive=True)
            sha1_hash = hashlib.sha1(canonical_root).digest()
            digest_b64 = base64.b64encode(sha1_hash).decode('utf-8')

            sig_ns = '{http://www.w3.org/2000/09/xmldsig#}'
            signature_elem = etree.Element(f'{sig_ns}Signature')
            signed_info = etree.SubElement(signature_elem, f'{sig_ns}SignedInfo')

            canon = etree.SubElement(signed_info, f'{sig_ns}CanonicalizationMethod')
            canon.set('Algorithm', 'http://www.w3.org/2001/10/xml-exc-c14n#')

            sig_method = etree.SubElement(signed_info, f'{sig_ns}SignatureMethod')
            sig_method.set('Algorithm',
                           'http://www.w3.org/2000/09/xmldsig#rsa-sha1')

            ref = etree.SubElement(signed_info, f'{sig_ns}Reference')
            ref.set('URI', '#RacunZahtjev')
            transforms = etree.SubElement(ref, f'{sig_ns}Transforms')

            t1 = etree.SubElement(transforms, f'{sig_ns}Transform')
            t1.set('Algorithm',
                   'http://www.w3.org/2000/09/xmldsig#enveloped-signature')
            t2 = etree.SubElement(transforms, f'{sig_ns}Transform')
            t2.set('Algorithm', 'http://www.w3.org/2001/10/xml-exc-c14n#')

            digest_method = etree.SubElement(ref, f'{sig_ns}DigestMethod')
            digest_method.set('Algorithm',
                              'http://www.w3.org/2000/09/xmldsig#sha1')
            digest_value = etree.SubElement(ref, f'{sig_ns}DigestValue')
            digest_value.text = digest_b64

            canonical_signed_info = etree.tostring(
                signed_info, method='c14n', exclusive=True)
            signature = self.private_key.sign(
                canonical_signed_info, padding.PKCS1v15(), hashes.SHA1())
            signature_b64 = base64.b64encode(signature).decode('utf-8')

            sig_value = etree.SubElement(signature_elem,
                                         f'{sig_ns}SignatureValue')
            sig_value.text = signature_b64

            key_info = etree.SubElement(signature_elem, f'{sig_ns}KeyInfo')
            x509_data = etree.SubElement(key_info, f'{sig_ns}X509Data')
            x509_cert = etree.SubElement(x509_data, f'{sig_ns}X509Certificate')

            with open(self.cert_path, 'r') as f:
                cert_lines = [l.strip() for l in f.read().split('\n')
                              if l.strip() and not l.startswith('-----')]
                x509_cert.text = ''.join(cert_lines)

            root.append(signature_elem)
            print("✅ XML potpisao!")
            return etree.tostring(root, encoding='utf-8')

        except Exception as e:
            print(f"⚠️  Greška pri potpisivanju: {e}")
            import traceback; traceback.print_exc()
            return xml_string.encode('utf-8')

    def fiskaliziraj_racun(self, racun_data):
        try:
            import requests
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            msg_id = str(uuid.uuid4())
            now = datetime.now()
            datum_vrijeme = now.strftime("%d.%m.%YT%H:%M:%S")

            zki = self.generiraj_zki(
                oib=racun_data['oib'],
                datum_vrijeme=datum_vrijeme,
                broj_racuna=racun_data['broj_racuna'],
                oznaka_poslovnog_prostora=racun_data['oznaka_pp'],
                oznaka_naplatnog_uredaja=racun_data['oznaka_nu'],
                ukupan_iznos=racun_data['ukupan_iznos']
            )

            u_sustavu_pdv = racun_data.get('u_sustavu_pdv', True)

            # PDV blok — grupira po stopi
            if u_sustavu_pdv and racun_data.get('pdv'):
                pdv_xml = "<tns:Pdv>"
                for porez in racun_data['pdv']:
                    pdv_xml += f"""
            <tns:Porez>
                <tns:Stopa>{porez['Stopa']}</tns:Stopa>
                <tns:Osnovica>{porez['Osnovica']}</tns:Osnovica>
                <tns:Iznos>{porez['Iznos']}</tns:Iznos>
            </tns:Porez>"""
                pdv_xml += "\n        </tns:Pdv>"
            else:
                pdv_xml = ""

            racun_zahtjev_xml = f'''<tns:RacunZahtjev Id="RacunZahtjev" xmlns:tns="http://www.apis-it.hr/fin/2012/types/f73"
xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <tns:Zaglavlje>
        <tns:IdPoruke>{msg_id}</tns:IdPoruke>
        <tns:DatumVrijeme>{datum_vrijeme}</tns:DatumVrijeme>
    </tns:Zaglavlje>
    <tns:Racun>
        <tns:Oib>{racun_data['oib']}</tns:Oib>
        <tns:USustPdv>{str(u_sustavu_pdv).lower()}</tns:USustPdv>
        <tns:DatVrijeme>{datum_vrijeme}</tns:DatVrijeme>
        <tns:OznSlijed>P</tns:OznSlijed>
        <tns:BrRac>
            <tns:BrOznRac>{racun_data['broj_racuna']}</tns:BrOznRac>
            <tns:OznPosPr>{racun_data['oznaka_pp']}</tns:OznPosPr>
            <tns:OznNapUr>{racun_data['oznaka_nu']}</tns:OznNapUr>
        </tns:BrRac>
        {pdv_xml}
        <tns:IznosUkupno>{racun_data['ukupan_iznos']:.2f}</tns:IznosUkupno>
        <tns:NacinPlac>{racun_data.get('nacin_placanja', 'G')}</tns:NacinPlac>
        <tns:OibOper>{racun_data.get('oib_operatera', racun_data['oib'])}</tns:OibOper>
        <tns:ZastKod>{zki}</tns:ZastKod>
        <tns:NakDost>false</tns:NakDost>
    </tns:Racun>
</tns:RacunZahtjev>'''

            print(f"📤 Racun zahtjev: {racun_zahtjev_xml}")

            signed_zahtjev = self._potpiši_xml_enveloped(racun_zahtjev_xml)
            soap_body = f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
    <soap:Body>
        {signed_zahtjev.decode('utf-8')}
    </soap:Body>
</soap:Envelope>'''

            print(f"📤 Slanje na: {self.url}")
            print(f"   OIB: {racun_data['oib']}, Iznos: {racun_data['ukupan_iznos']} EUR")
            print(f"   U sustavu PDV-a: {u_sustavu_pdv}, ZKI: {zki}")

            response = requests.post(
                self.url,
                data=soap_body.encode('utf-8'),
                headers={'Content-Type': 'text/xml; charset=UTF-8',
                         'SOAPAction': ''},
                cert=(self.cert_path, self.key_path),
                verify=False,
                timeout=30
            )
            print(f"   HTTP status: {response.status_code}")

            root = etree.fromstring(response.content)
            ns = {'soap': 'http://schemas.xmlsoap.org/soap/envelope/',
                  'tns': 'http://www.apis-it.hr/fin/2012/types/f73'}

            greske = root.findall('.//tns:Greska', ns)
            if greske:
                print("\n⚠️  GREŠKE:")
                for g in greske:
                    s = g.find('tns:SifraGreske', ns)
                    p = g.find('tns:PorukaGreske', ns)
                    if s is not None and p is not None:
                        print(f"   - {s.text}: {p.text}")

            jir_elem = root.find('.//tns:Jir', ns)
            if jir_elem is not None and jir_elem.text:
                print(f"\n✅ JIR: {jir_elem.text}")
                return jir_elem.text, zki
            else:
                print("\n⚠️  Nema JIR-a u odgovoru")
                return None, zki

        except Exception as e:
            print(f"❌ Iznimka: {e}")
            import traceback; traceback.print_exc()
            return None, None


# ---------------------------------------------------------------------------
# Baza podataka
# ---------------------------------------------------------------------------

conn = sqlite3.connect("billing.db")
cursor = conn.cursor()

cursor.execute("""
               CREATE TABLE IF NOT EXISTS invoices (
                                                       id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                                                       datum               TEXT,
                                                       rok_placanja        TEXT,
                                                       nacin_placanja      TEXT,
                                                       oib_izdavatelja     TEXT,
                                                       naziv_izdavatelja   TEXT,
                                                       adresa_izdavatelja  TEXT,
                                                       iban                TEXT,
                                                       oib_kupca           TEXT,
                                                       naziv_kupca         TEXT,
                                                       adresa_kupca        TEXT,
                                                       oznaka_pp           TEXT,
                                                       oznaka_nu           TEXT,
                                                       u_sustavu_pdv       INTEGER DEFAULT 1,
                                                       ukupan_iznos        REAL,
                                                       napomena            TEXT,
                                                       jir                 TEXT,
                                                       zki                 TEXT,
                                                       fiskaliziran        INTEGER DEFAULT 0,
                                                       datum_fiskalizacije TEXT,
                                                       pravna_osoba        INTEGER DEFAULT 0,
                                                       broj_racuna         TEXT
               )
               """)

cursor.execute("""
    CREATE TABLE IF NOT EXISTS brojaci_racuna (
        godina      INTEGER NOT NULL,
        oznaka_pp   TEXT NOT NULL,
        oznaka_nu   TEXT NOT NULL,
        zadnji_broj INTEGER DEFAULT 0,
        PRIMARY KEY (godina, oznaka_pp, oznaka_nu)
    )
""")
conn.commit()

cursor.execute("""
               CREATE TABLE IF NOT EXISTS invoice_stavke (
                                                             id          INTEGER PRIMARY KEY AUTOINCREMENT,
                                                             invoice_id  INTEGER,
                                                             naziv       TEXT,
                                                             kolicina    REAL,
                                                             jedinica    TEXT,
                                                             cijena      REAL,
                                                             pdv_stopa   REAL,
                                                             popust      REAL DEFAULT 0,
                                                             FOREIGN KEY (invoice_id) REFERENCES invoices(id)
                   )
               """)

cursor.execute("""
    CREATE TABLE IF NOT EXISTS kupci (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        naziv         TEXT NOT NULL,
        oib           TEXT,
        adresa        TEXT,
        pravna_osoba  INTEGER DEFAULT 0
    )
""")
conn.commit()

# # Migracije za stare baze
# for col_sql in [
#     "ALTER TABLE invoices ADD COLUMN datum TEXT",
#     "ALTER TABLE invoices ADD COLUMN rok_placanja TEXT",
#     "ALTER TABLE invoices ADD COLUMN nacin_placanja TEXT",
#     "ALTER TABLE invoices ADD COLUMN oib_izdavatelja TEXT",
#     "ALTER TABLE invoices ADD COLUMN naziv_izdavatelja TEXT",
#     "ALTER TABLE invoices ADD COLUMN adresa_izdavatelja TEXT",
#     "ALTER TABLE invoices ADD COLUMN iban TEXT",
#     "ALTER TABLE invoices ADD COLUMN oib_kupca TEXT",
#     "ALTER TABLE invoices ADD COLUMN naziv_kupca TEXT",
#     "ALTER TABLE invoices ADD COLUMN adresa_kupca TEXT",
#     "ALTER TABLE invoices ADD COLUMN oznaka_pp TEXT",
#     "ALTER TABLE invoices ADD COLUMN oznaka_nu TEXT",
#     "ALTER TABLE invoices ADD COLUMN u_sustavu_pdv INTEGER DEFAULT 1",
#     "ALTER TABLE invoices ADD COLUMN ukupan_iznos REAL",
#     "ALTER TABLE invoices ADD COLUMN napomena TEXT",
#     "ALTER TABLE invoices ADD COLUMN jir TEXT",
#     "ALTER TABLE invoices ADD COLUMN zki TEXT",
#     "ALTER TABLE invoices ADD COLUMN fiskaliziran INTEGER DEFAULT 0",
#     "ALTER TABLE invoices ADD COLUMN datum_fiskalizacije TEXT",
# ]:
#     try:
#         cursor.execute(col_sql)
#     except sqlite3.OperationalError:
#         pass

conn.commit()

try:
    cursor.execute("ALTER TABLE invoices ADD COLUMN pravna_osoba INTEGER DEFAULT 0")
    conn.commit()
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE invoices ADD COLUMN broj_racuna TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE invoices ADD COLUMN operater TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass

try:
    cursor.execute("ALTER TABLE invoices ADD COLUMN datum_kreiranja TEXT")
    conn.commit()
except sqlite3.OperationalError:
    pass

inicijaliziraj_kpr_tablicu(conn, cursor)


# ---------------------------------------------------------------------------
# Pomoćne funkcije
# ---------------------------------------------------------------------------

def sljedeci_broj_racuna(oznaka_pp, oznaka_nu):
    """
    Atomarno dohvaća i inkrementira broj računa za dani PP/NU par i godinu.
    Reset na 1 svake nove godine automatski.
    """
    godina = datetime.now().year

    cursor.execute("""
        INSERT OR IGNORE INTO brojaci_racuna
            (godina, oznaka_pp, oznaka_nu, zadnji_broj)
        VALUES (?, ?, ?, 0)
    """, (godina, oznaka_pp, oznaka_nu))

    cursor.execute("""
        UPDATE brojaci_racuna
        SET zadnji_broj = zadnji_broj + 1
        WHERE godina = ? AND oznaka_pp = ? AND oznaka_nu = ?
    """, (godina, oznaka_pp, oznaka_nu))

    row = cursor.execute("""
        SELECT zadnji_broj FROM brojaci_racuna
        WHERE godina = ? AND oznaka_pp = ? AND oznaka_nu = ?
    """, (godina, oznaka_pp, oznaka_nu)).fetchone()

    conn.commit()
    return godina, row[0]


def dohvati_kupce(pretraga=""):
    """Dohvaća kupce iz baze, opcionalno filtrirane po pretrazi."""
    if pretraga:
        return cursor.execute("""
            SELECT id, naziv, oib, adresa, pravna_osoba
            FROM kupci
            WHERE naziv LIKE ? OR oib LIKE ?
            ORDER BY naziv
        """, (f"%{pretraga}%", f"%{pretraga}%")).fetchall()
    return cursor.execute("""
        SELECT id, naziv, oib, adresa, pravna_osoba
        FROM kupci ORDER BY naziv
    """).fetchall()

def spremi_kupca(naziv, oib, adresa, pravna_osoba):
    """Sprema novog kupca ili ažurira postojećeg po OIB-u."""
    if oib:
        postojeci = cursor.execute(
            "SELECT id FROM kupci WHERE oib = ?", (oib,)).fetchone()
        if postojeci:
            cursor.execute("""
                UPDATE kupci SET naziv=?, adresa=?, pravna_osoba=?
                WHERE oib=?
            """, (naziv, adresa, int(pravna_osoba), oib))
            conn.commit()
            return postojeci[0]

    cursor.execute("""
        INSERT INTO kupci (naziv, oib, adresa, pravna_osoba)
        VALUES (?, ?, ?, ?)
    """, (naziv, oib or None, adresa, int(pravna_osoba)))
    conn.commit()
    return cursor.lastrowid


# ---------------------------------------------------------------------------
# Pomoćne funkcije za PDF
# ---------------------------------------------------------------------------

def get_font():
    """Registrira i vraća naziv fonta s podrškom za hrvatska slova."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    import reportlab

    # DejaVu koji dolazi s reportlabom — uvijek dostupan
    reportlab_font_dir = os.path.join(
        os.path.dirname(reportlab.__file__), 'fonts')

    # Kandidati — traži redom, prvi pronađeni par (regular + bold) pobijedi
    # Svaki unos: (ime_fonta, putanja_regular, putanja_bold)
    font_candidates = [
        # ReportLab ugrađeni — najpouzdanije
        # (
        #     "DejaVuSans",
        #     os.path.join(reportlab_font_dir, "DejaVuSans.ttf"),
        #     os.path.join(reportlab_font_dir, "DejaVuSans-Bold.ttf"),
        # ),
        # Windows sistemski fontovi
        (
            "Arial",
            r"C:\Windows\Fonts\arial.ttf",
            r"C:\Windows\Fonts\arialbd.ttf",
        ),
        (
            "Calibri",
            r"C:\Windows\Fonts\calibri.ttf",
            r"C:\Windows\Fonts\calibrib.ttf",
        ),
        (
            "Verdana",
            r"C:\Windows\Fonts\verdana.ttf",
            r"C:\Windows\Fonts\verdanab.ttf",
        ),
        (
            "Tahoma",
            r"C:\Windows\Fonts\tahoma.ttf",
            r"C:\Windows\Fonts\tahomabd.ttf",
        ),
        (
            "TimesNewRoman",
            r"C:\Windows\Fonts\times.ttf",
            r"C:\Windows\Fonts\timesbd.ttf",
        ),
        (
            "Georgia",
            r"C:\Windows\Fonts\georgia.ttf",
            r"C:\Windows\Fonts\georgiab.ttf",
        ),
        (
            "Segoe",
            r"C:\Windows\Fonts\segoeui.ttf",
            r"C:\Windows\Fonts\segoeuib.ttf",
        ),
        # Linux / macOS fallbackovi (ako se aplikacija pokreće i tamo)
        (
            "DejaVuSans",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "DejaVuSans",
            "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        ),
        (
            "Arial",
            "/Library/Fonts/Arial.ttf",
            "/Library/Fonts/Arial Bold.ttf",
        ),
    ]

    for font_name, regular_path, bold_path in font_candidates:
        if not os.path.exists(regular_path):
            continue

        try:
            pdfmetrics.registerFont(TTFont(font_name, regular_path))

            bold_name = font_name + "-Bold"
            if os.path.exists(bold_path):
                pdfmetrics.registerFont(TTFont(bold_name, bold_path))
            else:
                # Nema bold varijante — koristi regular i za bold
                bold_name = font_name
                print(f"⚠️  Bold varijanta nije pronađena za {font_name}, "
                      f"koristim regular")

            print(f"✅ Font: {font_name} ({regular_path})")
            return font_name, bold_name

        except Exception as e:
            print(f"⚠️  Font {font_name} nije učitan ({regular_path}): {e}")
            continue

    # Apsolutni fallback — Helvetica bez hrvatskih slova
    print("⚠️  Nije pronađen nijedan TTF font s hrvatskim slovima!")
    print("   Instalirajte: pip install reportlab[fonts]")
    return 'Helvetica', 'Helvetica-Bold'

# ---------------------------------------------------------------------------
# GUI — widget za unos stavki
# ---------------------------------------------------------------------------

class StavkeWidget(QWidget):
    """Tablica za unos više stavki računa."""

    PDV_STOPE = ["25", "13", "5", "0"]

    def __init__(self, u_sustavu_pdv=True):
        super().__init__()
        self.u_sustavu_pdv = u_sustavu_pdv
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        # Tablica
        self.tabla = QTableWidget(0, 6)
        self.tabla.setHorizontalHeaderLabels(
            ["Naziv", "Količina", "Jed.", "Cijena (EUR)",
             "PDV %", "Ukupno"])
        self.tabla.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 6):
            self.tabla.horizontalHeader().setSectionResizeMode(
                i, QHeaderView.ResizeMode.ResizeToContents)
        self.tabla.setMinimumHeight(150)
        layout.addWidget(self.tabla)

        # Gumbi
        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("➕ Dodaj stavku")
        self.add_btn.setObjectName("addItemBtn")
        self.add_btn.clicked.connect(self.dodaj_stavku)
        self.del_btn = QPushButton("➖ Ukloni stavku")
        self.del_btn.setObjectName("removeItemBtn")
        self.del_btn.clicked.connect(self.ukloni_stavku)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.del_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        # Dodaj jednu praznu stavku na početku
        self.dodaj_stavku()

    def set_pdv_mode(self, u_sustavu_pdv):
        """Ažurira PDV stupac prema statusu obveznika."""
        self.u_sustavu_pdv = u_sustavu_pdv
        for row in range(self.tabla.rowCount()):
            combo = self.tabla.cellWidget(row, 4)
            if combo:
                combo.setEnabled(u_sustavu_pdv)
                if not u_sustavu_pdv:
                    combo.setCurrentText("0")

    def dodaj_stavku(self):
        row = self.tabla.rowCount()
        self.tabla.insertRow(row)

        self.tabla.setItem(row, 0, QTableWidgetItem(""))
        self.tabla.setItem(row, 1, QTableWidgetItem("1"))
        self.tabla.setItem(row, 2, QTableWidgetItem("kom"))

        cijena_item = QTableWidgetItem("0.00")
        cijena_item.setTextAlignment(Qt.AlignmentFlag.AlignRight |
                                     Qt.AlignmentFlag.AlignVCenter)
        self.tabla.setItem(row, 3, cijena_item)

        pdv_combo = NoScrollComboBox()
        pdv_combo.addItems(self.PDV_STOPE)
        pdv_combo.setMinimumWidth(55)
        pdv_combo.setCurrentText("25" if self.u_sustavu_pdv else "0")
        pdv_combo.setEnabled(self.u_sustavu_pdv)
        pdv_combo.currentTextChanged.connect(self.azuriraj_ukupno)
        self.tabla.setCellWidget(row, 4, pdv_combo)

        ukupno_item = QTableWidgetItem("0.00")
        ukupno_item.setTextAlignment(Qt.AlignmentFlag.AlignRight |
                                     Qt.AlignmentFlag.AlignVCenter)
        ukupno_item.setFlags(ukupno_item.flags() &
                             ~Qt.ItemFlag.ItemIsEditable)
        self.tabla.setItem(row, 5, ukupno_item)

        self.tabla.itemChanged.connect(self.azuriraj_ukupno)

    def ukloni_stavku(self):
        row = self.tabla.currentRow()
        if row >= 0 and self.tabla.rowCount() > 1:
            self.tabla.removeRow(row)
            self.azuriraj_ukupno()

    def azuriraj_ukupno(self):
        """Ažurira ukupno po stavci."""
        self.tabla.itemChanged.disconnect(self.azuriraj_ukupno)
        try:
            for row in range(self.tabla.rowCount()):
                try:
                    kol = float(
                        (self.tabla.item(row, 1) or
                         QTableWidgetItem("0")).text().replace(',', '.'))
                    cij = float(
                        (self.tabla.item(row, 3) or
                         QTableWidgetItem("0")).text().replace(',', '.'))
                    pdv_combo = self.tabla.cellWidget(row, 4)
                    pdv = float(pdv_combo.currentText()) if pdv_combo else 0
                    ukupno = kol * cij * (1 + pdv / 100)
                    item = self.tabla.item(row, 5)
                    if item:
                        item.setText(f"{ukupno:.2f}")
                except (ValueError, AttributeError):
                    pass
        finally:
            self.tabla.itemChanged.connect(self.azuriraj_ukupno)

    def get_stavke(self):
        """Vraća listu stavki kao dict."""
        stavke = []
        for row in range(self.tabla.rowCount()):
            naziv = (self.tabla.item(row, 0) or
                     QTableWidgetItem("")).text().strip()
            if not naziv:
                continue
            try:
                kolicina = float(
                    (self.tabla.item(row, 1) or
                     QTableWidgetItem("1")).text().replace(',', '.'))
                jedinica = (self.tabla.item(row, 2) or
                            QTableWidgetItem("kom")).text().strip() or "kom"
                cijena = float(
                    (self.tabla.item(row, 3) or
                     QTableWidgetItem("0")).text().replace(',', '.'))
                pdv_combo = self.tabla.cellWidget(row, 4)
                pdv_stopa = float(
                    pdv_combo.currentText()) if pdv_combo else 0.0

                stavke.append({
                    'naziv': naziv,
                    'kolicina': kolicina,
                    'jedinica': jedinica,
                    'cijena': cijena,
                    'pdv_stopa': pdv_stopa,
                    'ukupno_bez_pdv': round(kolicina * cijena, 2),
                    'pdv_iznos': round(kolicina * cijena * pdv_stopa / 100, 2),
                    'ukupno': round(kolicina * cijena * (1 + pdv_stopa / 100), 2)
                })
            except (ValueError, AttributeError):
                pass
        return stavke

    def get_ukupan_iznos(self):
        return sum(s['ukupno'] for s in self.get_stavke())

    def get_pdv_grupe(self):
        """Grupira PDV po stopi za fiskalizaciju."""
        grupe = {}
        for s in self.get_stavke():
            stopa = s['pdv_stopa']
            if stopa not in grupe:
                grupe[stopa] = {'osnovica': 0.0, 'iznos': 0.0}
            grupe[stopa]['osnovica'] += s['ukupno_bez_pdv']
            grupe[stopa]['iznos'] += s['pdv_iznos']

        return [
            {
                'Stopa': f"{stopa:.2f}",
                'Osnovica': f"{v['osnovica']:.2f}",
                'Iznos': f"{v['iznos']:.2f}"
            }
            for stopa, v in grupe.items() if stopa > 0
        ]



class PregledRacunaWidget(QWidget):
    """Tab s pregledom svih izdanih računa."""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Traka za pretraživanje i filtre ─────────────────────────────
        filter_layout = QHBoxLayout()

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(
            "🔍  Pretraži po kupcu, OIB-u, JIR-u...")
        self.search_input.textChanged.connect(self.filtriraj)
        self.search_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #cdd1d9;
                border-radius: 5px;
                padding: 6px 10px;
                background-color: #ffffff;
                font-size: 11px;
            }
        """)
        filter_layout.addWidget(self.search_input)

        self.filter_fisk = NoScrollComboBox()
        self.filter_fisk.addItems([
            "Svi računi",
            "✅ Fiskalizirani",
            "⚠️ Nisu fiskalizirani"
        ])
        self.filter_fisk.setFixedWidth(180)
        self.filter_fisk.currentIndexChanged.connect(self.filtriraj)
        filter_layout.addWidget(self.filter_fisk)

        osvjezi_btn = QPushButton("🔄  Osvježi")
        osvjezi_btn.setFixedWidth(100)
        osvjezi_btn.clicked.connect(self.osvjezi)
        filter_layout.addWidget(osvjezi_btn)

        layout.addLayout(filter_layout)

        # ── Tablica ──────────────────────────────────────────────────────
        self.tabla = QTableWidget()
        self.tabla.setColumnCount(10)
        self.tabla.setHorizontalHeaderLabels([
            "Br.", "Datum", "Kupac", "Adresa kupca",
            "Iznos (EUR)", "PDV", "Način", "Fisk.", "Operater", "JIR / ZKI"
        ])
        self.tabla.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.tabla.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.setShowGrid(True)
        self.tabla.setSortingEnabled(True)

        # Širine stupaca
        self.tabla.setColumnWidth(0, 50)   # Br.
        self.tabla.setColumnWidth(1, 90)   # Datum
        self.tabla.setColumnWidth(2, 150)  # Kupac
        self.tabla.setColumnWidth(3, 140)  # Adresa
        self.tabla.setColumnWidth(4, 90)   # Iznos
        self.tabla.setColumnWidth(5, 50)   # PDV
        self.tabla.setColumnWidth(6, 60)   # Način
        self.tabla.setColumnWidth(7, 60)   # Fisk.
        self.tabla.setColumnWidth(8, 110)  # Operater
        self.tabla.horizontalHeader().setStretchLastSection(True)  # JIR

        self.tabla.setStyleSheet("""
            QTableWidget {
                border: 1px solid #d0d4db;
                border-radius: 6px;
                background-color: #ffffff;
                alternate-background-color: #f8f9fb;
                gridline-color: #eaecef;
            }
            QTableWidget::item {
                padding: 5px 8px;
            }
            QTableWidget::item:selected {
                background-color: #e8f0fe;
                color: #2c2c2c;
            }
            QHeaderView::section {
                background-color: #2c3e50;
                color: white;
                font-weight: bold;
                padding: 7px 8px;
                border: none;
                font-size: 10px;
            }
        """)

        layout.addWidget(self.tabla)
        self.tabla.doubleClicked.connect(self._otvori_detalje)
        self.tabla.selectionModel().selectionChanged.connect(self._on_selekcija)

        # ── Statusna traka ───────────────────────────────────────────────
        status_layout = QHBoxLayout()

        self.status_label = QLabel("")
        self.status_label.setStyleSheet(
            "color: #7f8c8d; font-size: 10px;")
        status_layout.addWidget(self.status_label)

        self.detalji_btn = QPushButton("🔍  Detalji računa")
        self.detalji_btn.setFixedWidth(150)
        self.detalji_btn.setEnabled(False)
        self.detalji_btn.clicked.connect(self._otvori_detalje)
        status_layout.addWidget(self.detalji_btn)

        status_layout.addStretch()

        self.ukupno_label = QLabel("")
        self.ukupno_label.setStyleSheet(
            "font-weight: bold; font-size: 11px; color: #2c2c2c;")
        status_layout.addWidget(self.ukupno_label)

        layout.addLayout(status_layout)

        # Učitaj podatke
        self.svi_racuni = []
        self.osvjezi()

    def osvjezi(self):
        """Učitava sve račune iz baze."""
        try:
            rows = cursor.execute("""
                SELECT
                    id, broj_racuna, datum, naziv_kupca, adresa_kupca,
                    ukupan_iznos, u_sustavu_pdv, nacin_placanja,
                    fiskaliziran, jir, zki, oznaka_pp, oznaka_nu,
                    operater, datum_kreiranja
                FROM invoices
                ORDER BY id DESC
            """).fetchall()
            self.svi_racuni = rows
            self.filtriraj()
        except Exception as e:
            print(f"⚠️  Greška pri učitavanju računa: {e}")

    def filtriraj(self):
        """Filtrira tablicu prema pretrazi i filtru fiskalizacije."""
        tekst = self.search_input.text().lower().strip()
        fisk_filter = self.filter_fisk.currentIndex()  # 0=svi, 1=fisk, 2=nije

        prikazani = []
        for r in self.svi_racuni:
            (id_, broj_racuna, datum, kupac, adresa, iznos, u_pdv,
             nacin, fiskaliziran, jir, zki, pp, nu, operater, datum_kreiranja) = r

            # Filter fiskalizacije
            if fisk_filter == 1 and not fiskaliziran:
                continue
            if fisk_filter == 2 and fiskaliziran:
                continue

            # Tekstualna pretraga
            if tekst:
                pretrazivo = " ".join(filter(None, [
                    str(id_), broj_racuna or "", datum or "", kupac or "",
                    adresa or "", jir or "", zki or "", operater or ""
                ])).lower()
                if tekst not in pretrazivo:
                    continue

            prikazani.append(r)

        self._popuni_tablicu(prikazani)

    def _popuni_tablicu(self, racuni):
        """Puni tablicu s listom računa."""
        self.tabla.setSortingEnabled(False)
        self.tabla.setRowCount(0)

        nacin_map = {
            'G': 'Gotovina', 'K': 'Kartica',
            'T': 'Transakcija', 'O': 'Ostalo'
        }

        ukupno_iznos = 0.0

        for r in racuni:
            (id_, broj_racuna, datum, kupac, adresa, iznos, u_pdv,
             nacin, fiskaliziran, jir, zki, pp, nu, operater, datum_kreiranja) = r

            row = self.tabla.rowCount()
            self.tabla.insertRow(row)

            # Br. računa
            br = broj_racuna or f"{pp or ''}-{nu or ''}-{id_}"
            item_br = QTableWidgetItem(br)
            item_br.setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            item_br.setData(Qt.ItemDataRole.UserRole, id_)
            self.tabla.setItem(row, 0, item_br)

            # Datum
            self._set_item(row, 1, datum or "")

            # Kupac
            self._set_item(row, 2, kupac or "", align_right=False)

            # Adresa kupca
            self._set_item(row, 3, adresa or "", align_right=False)

            # Iznos
            iznos_val = iznos or 0.0
            self._set_item(row, 4, f"{iznos_val:.2f}", align_right=True)
            ukupno_iznos += iznos_val

            # PDV
            pdv_tekst = "DA" if u_pdv else "NE"
            item_pdv = QTableWidgetItem(pdv_tekst)
            item_pdv.setTextAlignment(
                Qt.AlignmentFlag.AlignCenter)
            item_pdv.setForeground(
                QColor("#2e7d32") if u_pdv else QColor("#757575"))
            self.tabla.setItem(row, 5, item_pdv)

            # Način plaćanja
            self._set_item(
                row, 6, nacin_map.get(nacin or 'G', nacin or ''))

            # Fiskaliziran
            fisk_tekst = "✅" if fiskaliziran else "⚠️"
            item_fisk = QTableWidgetItem(fisk_tekst)
            item_fisk.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.tabla.setItem(row, 7, item_fisk)

            # Operater
            self._set_item(row, 8, operater or "", align_right=False)

            # JIR / ZKI
            identifikator = jir if jir else (
                f"ZKI: {zki[:16]}..." if zki else "N/A")
            self._set_item(row, 9, identifikator, align_right=False)

        self.tabla.setSortingEnabled(True)

        # Statusna traka
        n = len(racuni)
        fisk_n = sum(1 for r in racuni if r[7])
        self.status_label.setText(
            f"{n} račun{'a' if n != 1 else ''}  •  "
            f"{fisk_n} fiskaliziran{'ih' if fisk_n != 1 else ''}")
        self.ukupno_label.setText(
            f"Ukupno: {ukupno_iznos:,.2f} EUR")

    def _set_item(self, row, col, text, align_right=False):
        item = QTableWidgetItem(str(text))
        item.setTextAlignment(
            (Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            if align_right
            else (Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        )
        self.tabla.setItem(row, col, item)

    def _on_selekcija(self):
        self.detalji_btn.setEnabled(self.tabla.currentRow() >= 0)

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

class OdabirKupcaDialog(QWidget):
    """Popup prozor za pretragu, odabir, uređivanje i brisanje kupca."""

    kupac_odabran = Signal(dict)  # naziv, oib, adresa, pravna_osoba

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Odabir kupca")
        self.setMinimumSize(650, 500)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        self._edit_id = None  # None = novi kupac, int = uređivanje postojećeg

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        # ── Traka za pretragu + Novi kupac ───────────────────────────────
        search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("🔍  Pretraži po nazivu ili OIB-u...")
        self.search_input.textChanged.connect(self.filtriraj)
        search_layout.addWidget(self.search_input)

        novi_btn = QPushButton("➕  Novi kupac")
        novi_btn.setFixedWidth(120)
        novi_btn.clicked.connect(self.novi_kupac_forma)
        search_layout.addWidget(novi_btn)
        layout.addLayout(search_layout)

        # ── Tablica kupaca ───────────────────────────────────────────────
        self.tabla = QTableWidget()
        self.tabla.setColumnCount(4)
        self.tabla.setHorizontalHeaderLabels(
            ["Naziv", "OIB", "Adresa", "Tip"])
        self.tabla.setEditTriggers(
            QTableWidget.EditTrigger.NoEditTriggers)
        self.tabla.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows)
        self.tabla.setAlternatingRowColors(True)
        self.tabla.verticalHeader().setVisible(False)
        self.tabla.doubleClicked.connect(self.odaberi_kupca)
        self.tabla.horizontalHeader().setStretchLastSection(True)
        self.tabla.setColumnWidth(0, 200)
        self.tabla.setColumnWidth(1, 110)
        self.tabla.setColumnWidth(2, 180)
        self.tabla.setColumnWidth(3, 80)
        layout.addWidget(self.tabla)

        # ── Forma za novog/uređivanje kupca (skrivena po defaultu) ───────
        self.forma_widget = QWidget()
        self.forma_widget.setStyleSheet(
            "background-color: #f8f9fb; border-radius: 6px;")
        forma_layout = QFormLayout(self.forma_widget)
        forma_layout.setContentsMargins(10, 10, 10, 10)
        forma_layout.setSpacing(7)

        self.forma_naslov = QLabel("Novi kupac")
        self.forma_naslov.setStyleSheet(
            "font-weight: bold; font-size: 11px; color: #2c3e50;"
            "background-color: transparent;")
        forma_layout.addRow(self.forma_naslov)

        self.novi_naziv = QLineEdit()
        self.novi_naziv.setPlaceholderText("Naziv kupca / ime i prezime")
        forma_layout.addRow("Naziv *:", self.novi_naziv)

        self.novi_oib = QLineEdit()
        self.novi_oib.setPlaceholderText("OIB (opcionalno)")
        self.novi_oib.setMaxLength(11)
        forma_layout.addRow("OIB:", self.novi_oib)

        self.nova_adresa = QLineEdit()
        self.nova_adresa.setPlaceholderText("Ulica i broj, Grad")
        forma_layout.addRow("Adresa:", self.nova_adresa)

        self.nova_pravna = QCheckBox("Pravna osoba")
        forma_layout.addRow("", self.nova_pravna)

        forma_gumbi = QHBoxLayout()
        self.spremi_btn = QPushButton("💾  Spremi kupca")
        self.spremi_btn.clicked.connect(self.spremi_kupca_forma)
        odustani_btn = QPushButton("Odustani")
        odustani_btn.setObjectName("dangerBtn")
        odustani_btn.clicked.connect(self._zatvori_formu)
        forma_gumbi.addWidget(self.spremi_btn)
        forma_gumbi.addWidget(odustani_btn)
        forma_gumbi.addStretch()
        forma_layout.addRow(forma_gumbi)

        self.forma_widget.setVisible(False)
        layout.addWidget(self.forma_widget)

        # ── Gumbi na dnu ─────────────────────────────────────────────────
        gumbi_layout = QHBoxLayout()

        self.uredi_btn = QPushButton("✏️  Uredi")
        self.uredi_btn.setFixedWidth(100)
        self.uredi_btn.setEnabled(False)
        self.uredi_btn.clicked.connect(self.uredi_kupca_forma)
        gumbi_layout.addWidget(self.uredi_btn)

        self.obrisi_btn = QPushButton("🗑️  Obriši")
        self.obrisi_btn.setFixedWidth(100)
        self.obrisi_btn.setObjectName("dangerBtn")
        self.obrisi_btn.setEnabled(False)
        self.obrisi_btn.clicked.connect(self.obrisi_kupca)
        gumbi_layout.addWidget(self.obrisi_btn)

        gumbi_layout.addStretch()

        self.odaberi_btn = QPushButton("✅  Odaberi")
        self.odaberi_btn.setFixedWidth(110)
        self.odaberi_btn.clicked.connect(self.odaberi_kupca)
        self.odaberi_btn.setEnabled(False)
        gumbi_layout.addWidget(self.odaberi_btn)

        zatvori_btn = QPushButton("Zatvori")
        zatvori_btn.setObjectName("dangerBtn")
        zatvori_btn.setFixedWidth(90)
        zatvori_btn.clicked.connect(self.close)
        gumbi_layout.addWidget(zatvori_btn)
        layout.addLayout(gumbi_layout)

        # Ažuriraj gumbe pri promjeni selekcije
        self.tabla.selectionModel().selectionChanged.connect(
            self._on_selection_changed)

        self.osvjezi()
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)

    def _on_selection_changed(self):
        has_row = self.tabla.currentRow() >= 0
        self.odaberi_btn.setEnabled(has_row)
        self.uredi_btn.setEnabled(has_row)
        self.obrisi_btn.setEnabled(has_row)

    def _zatvori_formu(self):
        self.forma_widget.setVisible(False)
        self._edit_id = None

    def _dohvati_odabrani_id(self):
        """Vraća DB id odabranog kupca iz UserRole podataka."""
        row = self.tabla.currentRow()
        if row < 0:
            return None
        podaci = self.tabla.item(row, 0).data(Qt.ItemDataRole.UserRole)
        return podaci.get('id') if podaci else None

    def show_above_parent(self):
        """Prikaži dialog centriran iznad roditeljskog prozora."""
        self.show()
        self.raise_()
        self.activateWindow()
        if self.parent():
            parent_geo = self.parent().geometry()
            x = parent_geo.x() + (parent_geo.width() - self.width()) // 2
            y = parent_geo.y() + (parent_geo.height() - self.height()) // 2
            self.move(x, y)

    def osvjezi(self, pretraga=""):
        kupci = dohvati_kupce(pretraga)
        self.tabla.setRowCount(0)
        for k in kupci:
            id_, naziv, oib, adresa, pravna = k
            row = self.tabla.rowCount()
            self.tabla.insertRow(row)
            self.tabla.setItem(row, 0, QTableWidgetItem(naziv or ""))
            self.tabla.setItem(row, 1, QTableWidgetItem(oib or ""))
            self.tabla.setItem(row, 2, QTableWidgetItem(adresa or ""))

            tip = "Pravna" if pravna else "Fizička"
            tip_item = QTableWidgetItem(tip)
            tip_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            tip_item.setForeground(
                QColor("#1565c0") if pravna else QColor("#2e7d32"))
            self.tabla.setItem(row, 3, tip_item)

            # Spremi ID i ostale podatke u UserRole
            self.tabla.item(row, 0).setData(
                Qt.ItemDataRole.UserRole,
                {'id': id_, 'naziv': naziv, 'oib': oib or '',
                 'adresa': adresa or '', 'pravna_osoba': bool(pravna)})

        self._on_selection_changed()

    def filtriraj(self, tekst):
        self.osvjezi(tekst)

    def novi_kupac_forma(self):
        """Otvori formu za unos novog kupca."""
        self._edit_id = None
        self.forma_naslov.setText("➕  Novi kupac")
        self.spremi_btn.setText("💾  Spremi kupca")
        self.novi_naziv.clear()
        self.novi_oib.clear()
        self.nova_adresa.clear()
        self.nova_pravna.setChecked(False)
        self.forma_widget.setVisible(True)
        self.novi_naziv.setFocus()

    def uredi_kupca_forma(self):
        """Popuni formu podacima odabranog kupca za uređivanje."""
        row = self.tabla.currentRow()
        if row < 0:
            return
        podaci = self.tabla.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not podaci:
            return

        self._edit_id = podaci['id']
        self.forma_naslov.setText(f"✏️  Uredi kupca: {podaci['naziv']}")
        self.spremi_btn.setText("💾  Ažuriraj kupca")
        self.novi_naziv.setText(podaci['naziv'])
        self.novi_oib.setText(podaci['oib'])
        self.nova_adresa.setText(podaci['adresa'])
        self.nova_pravna.setChecked(podaci['pravna_osoba'])
        self.forma_widget.setVisible(True)
        self.novi_naziv.setFocus()

    def spremi_kupca_forma(self):
        """Sprema novog ili ažurira postojećeg kupca."""
        naziv = self.novi_naziv.text().strip()
        if not naziv:
            QMessageBox.warning(self, "Greška", "Naziv je obavezan!")
            return

        oib = self.novi_oib.text().strip()
        if oib and (len(oib) != 11 or not oib.isdigit()):
            QMessageBox.warning(self, "Greška",
                                "OIB mora imati 11 znamenki!")
            return

        adresa = self.nova_adresa.text().strip()
        pravna = self.nova_pravna.isChecked()

        if self._edit_id is not None:
            # Ažuriraj postojećeg kupca direktno po ID-u
            cursor.execute("""
                UPDATE kupci SET naziv=?, oib=?, adresa=?, pravna_osoba=?
                WHERE id=?
            """, (naziv, oib or None, adresa, int(pravna), self._edit_id))
            conn.commit()
        else:
            spremi_kupca(naziv=naziv, oib=oib, adresa=adresa,
                         pravna_osoba=pravna)

        self._zatvori_formu()
        self.osvjezi()

    def obrisi_kupca(self):
        """Briše odabranog kupca uz potvrdu."""
        row = self.tabla.currentRow()
        if row < 0:
            return
        podaci = self.tabla.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if not podaci:
            return

        naziv = podaci['naziv']
        kupac_id = podaci['id']

        odgovor = QMessageBox.question(
            self, "Brisanje kupca",
            f"Jeste li sigurni da želite obrisati kupca:\n\n"
            f"  {naziv}\n\n"
            f"Ova radnja se ne može poništiti.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if odgovor == QMessageBox.StandardButton.Yes:
            cursor.execute("DELETE FROM kupci WHERE id=?", (kupac_id,))
            conn.commit()
            self._zatvori_formu()
            self.osvjezi()

    def odaberi_kupca(self):
        row = self.tabla.currentRow()
        if row < 0:
            return
        podaci = self.tabla.item(row, 0).data(Qt.ItemDataRole.UserRole)
        if podaci:
            self.kupac_odabran.emit(podaci)
            self.close()


class BillingApp(QWidget):
    # def __init__(self):
    #     super().__init__()
    #     self.setWindowTitle("Fiskalizacija računa")
    #     self.setMinimumWidth(900)
    #
    #     self.fiskalizacija = None
    #     self.demo_mode = True
    #
    #     # Scroll area za cijeli sadržaj
    #     scroll = QScrollArea()
    #     scroll.setWidgetResizable(True)
    #     scroll.setFrameShape(QFrame.Shape.NoFrame)
    #
    #     container = QWidget()
    #     main_layout = QVBoxLayout(container)
    #     main_layout.setSpacing(8)
    #
    #     # # ── Certifikat ──────────────────────────────────────────────────────
    #     # cert_group = QGroupBox("Certifikat")
    #     # cert_layout = QFormLayout()
    #     #
    #     # self.cert_path_input = QLineEdit()
    #     # self.cert_path_input.setText(
    #     #     r"F:\Projekti\RacunFiskalizacijaToolbox\FiskalizacijaDemo\cert\certificate.pem")
    #     # cert_layout.addRow("Certifikat (.pem):", self.cert_path_input)
    #     #
    #     # self.key_path_input = QLineEdit()
    #     # self.key_path_input.setText(
    #     #     r"F:\Projekti\RacunFiskalizacijaToolbox\FiskalizacijaDemo\cert\privatni_kljuc.pem")
    #     # cert_layout.addRow("Privatni ključ (.pem):", self.key_path_input)
    #     #
    #     # self.key_password_input = QLineEdit()
    #     # self.key_password_input.setEchoMode(QLineEdit.EchoMode.Password)
    #     # self.key_password_input.setPlaceholderText(
    #     #     "Ostavite prazno ako nema lozinke")
    #     # cert_layout.addRow("Lozinka ključa:", self.key_password_input)
    #     #
    #     # cert_layout.addRow(
    #     #     "Okolina:",
    #     #     QLabel("🧪 DEMO" if self.demo_mode else "🚀 PRODUKCIJA"))
    #     #
    #     # self.init_cert_btn = QPushButton("🔐 Inicijaliziraj certifikat")
    #     # self.init_cert_btn.clicked.connect(self.init_certifikat)
    #     # cert_layout.addRow(self.init_cert_btn)
    #     #
    #     # self.cert_status_label = QLabel("⚠️ Certifikat nije inicijaliziran")
    #     # self.cert_status_label.setStyleSheet("color: orange;")
    #     # cert_layout.addRow("Status:", self.cert_status_label)
    #     #
    #     # cert_group.setLayout(cert_layout)
    #     # main_layout.addWidget(cert_group)
    #
    #     # # ── Izdavatelj ───────────────────────────────────────────────────────
    #     # firma_group = QGroupBox("Podaci o izdavatelju")
    #     # firma_layout = QFormLayout()
    #     #
    #     # self.oib_input = QLineEdit()
    #     # self.oib_input.setText("91424659795")
    #     # firma_layout.addRow("OIB:", self.oib_input)
    #     #
    #     # self.naziv_tvrtke_input = QLineEdit()
    #     # self.naziv_tvrtke_input.setPlaceholderText("Moja tvrtka d.o.o.")
    #     # firma_layout.addRow("Naziv:", self.naziv_tvrtke_input)
    #     #
    #     # self.adresa_input = QLineEdit()
    #     # self.adresa_input.setPlaceholderText("Ulica i broj, Grad")
    #     # firma_layout.addRow("Adresa:", self.adresa_input)
    #     #
    #     # self.iban_input = QLineEdit()
    #     # self.iban_input.setPlaceholderText("HR12 1234 5678 9012 3456 7")
    #     # firma_layout.addRow("IBAN:", self.iban_input)
    #     #
    #     # self.pp_input = QLineEdit()
    #     # self.pp_input.setText("PP1")
    #     # firma_layout.addRow("Oznaka posl. prostora:", self.pp_input)
    #     #
    #     # self.nu_input = QLineEdit()
    #     # self.nu_input.setText("1")
    #     # firma_layout.addRow("Oznaka napl. uređaja:", self.nu_input)
    #     #
    #     # firma_group.setLayout(firma_layout)
    #     # main_layout.addWidget(firma_group)
    #     #
    #     # # ── Kupac ────────────────────────────────────────────────────────────
    #     # kupac_group = QGroupBox("Podaci o kupcu")
    #     # kupac_layout = QFormLayout()
    #     #
    #     # self.kupac_naziv_input = QLineEdit()
    #     # self.kupac_naziv_input.setPlaceholderText("Naziv kupca / ime i prezime")
    #     # kupac_layout.addRow("Naziv:", self.kupac_naziv_input)
    #     #
    #     # self.kupac_oib_input = QLineEdit()
    #     # self.kupac_oib_input.setPlaceholderText(
    #     #     "OIB kupca (opcionalno)")
    #     # kupac_layout.addRow("OIB kupca:", self.kupac_oib_input)
    #     #
    #     # self.kupac_adresa_input = QLineEdit()
    #     # self.kupac_adresa_input.setPlaceholderText("Ulica i broj, Grad")
    #     # kupac_layout.addRow("Adresa:", self.kupac_adresa_input)
    #     #
    #     # kupac_group.setLayout(kupac_layout)
    #     # main_layout.addWidget(kupac_group)
    #
    #     # ── Certifikat (sklopivi) ────────────────────────────────────────────
    #     cert_group = QGroupBox("Certifikat")
    #     cert_outer_layout = QVBoxLayout()
    #
    #     # Status bar — uvijek vidljiv
    #     status_bar = QHBoxLayout()
    #
    #     self.cert_status_label = QLabel("⚠️ Certifikat nije inicijaliziran")
    #     self.cert_status_label.setStyleSheet("color: orange;")
    #     status_bar.addWidget(self.cert_status_label)
    #
    #     status_bar.addStretch()
    #
    #     okolina_label = QLabel("🧪 DEMO" if self.demo_mode else "🚀 PRODUKCIJA")
    #     okolina_label.setStyleSheet(
    #         "color: #b8860b; font-weight: bold;" if self.demo_mode
    #         else "color: green; font-weight: bold;")
    #     status_bar.addWidget(okolina_label)
    #
    #     self.toggle_cert_btn = QPushButton("⚙️ Postavke")
    #     self.toggle_cert_btn.setFixedWidth(100)
    #     self.toggle_cert_btn.setCheckable(True)
    #     self.toggle_cert_btn.clicked.connect(self.toggle_cert_panel)
    #     status_bar.addWidget(self.toggle_cert_btn)
    #
    #     cert_outer_layout.addLayout(status_bar)
    #
    #     # Skriveni panel s detaljima
    #     self.cert_panel = QWidget()
    #     cert_layout = QFormLayout(self.cert_panel)
    #     cert_layout.setContentsMargins(0, 8, 0, 0)
    #
    #     self.cert_path_input = QLineEdit()
    #     self.cert_path_input.setText(
    #         r"F:\Projekti\RacunFiskalizacijaToolbox\FiskalizacijaDemo\cert\certificate.pem")
    #     cert_layout.addRow("Certifikat (.pem):", self.cert_path_input)
    #
    #     self.key_path_input = QLineEdit()
    #     self.key_path_input.setText(
    #         r"F:\Projekti\RacunFiskalizacijaToolbox\FiskalizacijaDemo\cert\privatni_kljuc.pem")
    #     cert_layout.addRow("Privatni ključ (.pem):", self.key_path_input)
    #
    #     self.key_password_input = QLineEdit()
    #     self.key_password_input.setEchoMode(QLineEdit.EchoMode.Password)
    #     self.key_password_input.setPlaceholderText("Ostavite prazno ako nema lozinke")
    #     cert_layout.addRow("Lozinka ključa:", self.key_password_input)
    #
    #     self.init_cert_btn = QPushButton("🔐 Inicijaliziraj certifikat")
    #     self.init_cert_btn.clicked.connect(self.init_certifikat)
    #     cert_layout.addRow(self.init_cert_btn)
    #
    #     self.cert_panel.setVisible(False)  # Skriveno po defaultu
    #     cert_outer_layout.addWidget(self.cert_panel)
    #
    #     cert_group.setLayout(cert_outer_layout)
    #     main_layout.addWidget(cert_group)
    #
    #     # ── Izdavatelj + Kupac (jedan pored drugog) ──────────────────────────
    #     stranke_layout = QHBoxLayout()
    #
    #     # Izdavatelj
    #     firma_group = QGroupBox("Podaci o izdavatelju")
    #     firma_layout = QFormLayout()
    #
    #     self.oib_input = QLineEdit()
    #     self.oib_input.setText("91424659795")
    #     firma_layout.addRow("OIB:", self.oib_input)
    #
    #     self.naziv_tvrtke_input = QLineEdit()
    #     self.naziv_tvrtke_input.setPlaceholderText("Moja tvrtka d.o.o.")
    #     firma_layout.addRow("Naziv:", self.naziv_tvrtke_input)
    #
    #     self.adresa_input = QLineEdit()
    #     self.adresa_input.setPlaceholderText("Ulica i broj, Grad")
    #     firma_layout.addRow("Adresa:", self.adresa_input)
    #
    #     self.iban_input = QLineEdit()
    #     self.iban_input.setPlaceholderText("HR12 1234 5678 9012 3456 7")
    #     firma_layout.addRow("IBAN:", self.iban_input)
    #
    #     self.pp_input = QLineEdit()
    #     self.pp_input.setText("PP1")
    #     firma_layout.addRow("Oznaka PP:", self.pp_input)
    #
    #     self.nu_input = QLineEdit()
    #     self.nu_input.setText("1")
    #     firma_layout.addRow("Oznaka NU:", self.nu_input)
    #
    #     firma_group.setLayout(firma_layout)
    #     stranke_layout.addWidget(firma_group)
    #
    #     # Kupac
    #     kupac_group = QGroupBox("Podaci o kupcu")
    #     kupac_layout = QFormLayout()
    #
    #     self.kupac_naziv_input = QLineEdit()
    #     self.kupac_naziv_input.setPlaceholderText("Naziv kupca / ime i prezime")
    #     kupac_layout.addRow("Naziv:", self.kupac_naziv_input)
    #
    #     self.kupac_oib_input = QLineEdit()
    #     self.kupac_oib_input.setPlaceholderText("OIB kupca (opcionalno)")
    #     kupac_layout.addRow("OIB:", self.kupac_oib_input)
    #
    #     self.kupac_adresa_input = QLineEdit()
    #     self.kupac_adresa_input.setPlaceholderText("Ulica i broj, Grad")
    #     kupac_layout.addRow("Adresa:", self.kupac_adresa_input)
    #
    #     kupac_group.setLayout(kupac_layout)
    #     stranke_layout.addWidget(kupac_group)
    #
    #     main_layout.addLayout(stranke_layout)
    #
    #     # ── Račun ────────────────────────────────────────────────────────────
    #     racun_group = QGroupBox("Podaci o računu")
    #     racun_layout = QFormLayout()
    #
    #     # # Datum
    #     # self.datum_edit = QDateEdit()
    #     # self.datum_edit.setDate(QDate.currentDate())
    #     # self.datum_edit.setCalendarPopup(True)
    #     # self.datum_edit.setDisplayFormat("dd.MM.yyyy")
    #     # racun_layout.addRow("Datum računa:", self.datum_edit)
    #     #
    #     # # Rok plaćanja
    #     # self.rok_edit = QDateEdit()
    #     # self.rok_edit.setDate(QDate.currentDate().addDays(15))
    #     # self.rok_edit.setCalendarPopup(True)
    #     # self.rok_edit.setDisplayFormat("dd.MM.yyyy")
    #     # racun_layout.addRow("Rok plaćanja:", self.rok_edit)
    #     #
    #     # # Način plaćanja
    #     # self.nacin_placanja_combo = QComboBox()
    #     # self.nacin_placanja_combo.addItems([
    #     #     "G - Gotovina",
    #     #     "K - Kartica",
    #     #     "T - Transakcijski račun",
    #     #     "O - Ostalo"
    #     # ])
    #     # self.nacin_placanja_combo.currentIndexChanged.connect(
    #     #     self.on_nacin_placanja_changed)
    #     # racun_layout.addRow("Način plaćanja:", self.nacin_placanja_combo)
    #
    #     # Datum
    #     self.datum_edit = NoScrollDateEdit()
    #     self.datum_edit.setDate(QDate.currentDate())
    #     self.datum_edit.setCalendarPopup(True)
    #     self.datum_edit.setDisplayFormat("dd.MM.yyyy")
    #     racun_layout.addRow("Datum računa:", self.datum_edit)
    #
    #     # Rok plaćanja
    #     self.rok_edit = NoScrollDateEdit()
    #     self.rok_edit.setDate(QDate.currentDate().addDays(15))
    #     self.rok_edit.setCalendarPopup(True)
    #     self.rok_edit.setDisplayFormat("dd.MM.yyyy")
    #     racun_layout.addRow("Rok plaćanja:", self.rok_edit)
    #
    #     # Način plaćanja
    #     self.nacin_placanja_combo = NoScrollComboBox()
    #     self.nacin_placanja_combo.addItems([
    #         "G - Gotovina",
    #         "K - Kartica",
    #         "T - Transakcijski račun",
    #         "O - Ostalo"
    #     ])
    #     self.nacin_placanja_combo.currentIndexChanged.connect(
    #         self.on_nacin_placanja_changed)
    #     racun_layout.addRow("Način plaćanja:", self.nacin_placanja_combo)
    #
    #     # PDV status
    #     self.u_sustavu_pdv_check = QCheckBox("U sustavu PDV-a")
    #     self.u_sustavu_pdv_check.setChecked(True)
    #     self.u_sustavu_pdv_check.stateChanged.connect(self.on_pdv_changed)
    #     racun_layout.addRow("PDV:", self.u_sustavu_pdv_check)
    #
    #     self.pdv_info_label = QLabel("PDV se obračunava po stavci")
    #     self.pdv_info_label.setStyleSheet("color: gray; font-size: 10px;")
    #     racun_layout.addRow("", self.pdv_info_label)
    #
    #     racun_group.setLayout(racun_layout)
    #     main_layout.addWidget(racun_group)
    #
    #     # ── Stavke ───────────────────────────────────────────────────────────
    #     stavke_group = QGroupBox("Stavke računa")
    #     stavke_layout = QVBoxLayout()
    #     self.stavke_widget = StavkeWidget(u_sustavu_pdv=True)
    #     stavke_layout.addWidget(self.stavke_widget)
    #     stavke_group.setLayout(stavke_layout)
    #     main_layout.addWidget(stavke_group)
    #
    #     # ── Napomena ─────────────────────────────────────────────────────────
    #     napomena_group = QGroupBox("Napomena")
    #     napomena_layout = QVBoxLayout()
    #     self.napomena_input = QTextEdit()
    #     self.napomena_input.setMaximumHeight(70)
    #     self.napomena_input.setPlaceholderText(
    #         "Slobodan tekst na računu (opcionalno)")
    #     napomena_layout.addWidget(self.napomena_input)
    #     napomena_group.setLayout(napomena_layout)
    #     main_layout.addWidget(napomena_group)
    #
    #     # ── Gumb ─────────────────────────────────────────────────────────────
    #     self.save_btn = QPushButton("💾 Spremi i fiskaliziraj račun")
    #     self.save_btn.clicked.connect(self.save_invoice)
    #     self.save_btn.setStyleSheet(
    #         "padding: 12px; font-weight: bold; font-size: 13px;")
    #     main_layout.addWidget(self.save_btn)
    #
    #     scroll.setWidget(container)
    #
    #     outer = QVBoxLayout(self)
    #     outer.addWidget(scroll)
    #
    #     self.init_certifikat(silent=True)

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fiskalizacija računa")
        self.setMinimumWidth(960)
        self.setMinimumHeight(700)

        self.fiskalizacija = None
        self.config = učitaj_config()
        self.demo_mode = self.config.get("demo_mode", True)
        self._ponuda_izvor_id = None

        import tempfile

        svg_content = b'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 16 16">
          <polyline points="3,8 6,12 13,4" stroke="white" stroke-width="2.5"
                    fill="none" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>'''

        self._checkmark_path = os.path.join(
            tempfile.gettempdir(), "fisk_checkmark.svg").replace("\\", "/")

        with open(self._checkmark_path, 'wb') as f:
            f.write(svg_content)

        # ThemeManager — inicijalizira se odmah, prije gradnje UI-a
        self.theme_manager = ThemeManager(self, self._checkmark_path)

        # ── Scroll area ──────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        container = QWidget()
        main_layout = QVBoxLayout(container)
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # ── Certifikat (sklopivi) ────────────────────────────────────────────
        cert_group = QGroupBox("Certifikat")
        cert_outer_layout = QVBoxLayout()
        cert_outer_layout.setSpacing(0)

        status_bar = QHBoxLayout()
        status_bar.setSpacing(8)

        self.cert_status_label = QLabel("⚠️  Certifikat nije inicijaliziran")
        self.cert_status_label.setStyleSheet(
            "color: #e67e22; font-weight: bold;")
        status_bar.addWidget(self.cert_status_label)
        status_bar.addStretch()

        okolina_badge = QLabel(
            "  🧪 DEMO  " if self.demo_mode else "  🚀 PRODUKCIJA  ")
        okolina_badge.setStyleSheet(
            "background-color: #fff3cd; color: #856404; font-weight: bold;"
            "border-radius: 10px; padding: 2px 8px; font-size: 10px;"
            if self.demo_mode else
            "background-color: #d4edda; color: #155724; font-weight: bold;"
            "border-radius: 10px; padding: 2px 8px; font-size: 10px;")
        status_bar.addWidget(okolina_badge)

        self.toggle_cert_btn = QPushButton("⚙️  Postavke")
        self.toggle_cert_btn.setFixedWidth(110)
        self.toggle_cert_btn.setCheckable(True)
        self.toggle_cert_btn.setObjectName("dangerBtn")
        self.toggle_cert_btn.clicked.connect(self.toggle_cert_panel)
        status_bar.addWidget(self.toggle_cert_btn)

        status_bar.addWidget(self.theme_manager.toggle_btn)

        cert_outer_layout.addLayout(status_bar)

        self.cert_panel = QWidget()
        self.cert_panel.setStyleSheet(
            "background-color: #f8f9fb; border-radius: 6px;")
        cert_layout = QFormLayout(self.cert_panel)
        cert_layout.setContentsMargins(8, 12, 8, 8)
        cert_layout.setSpacing(8)

        self.cert_path_input = QLineEdit()
        self.cert_path_input.setText(self.config.get("cert_path", ""))
        cert_layout.addRow("Certifikat (.pem):", self.cert_path_input)

        self.key_path_input = QLineEdit()
        self.key_path_input.setText(self.config.get("key_path", ""))
        cert_layout.addRow("Privatni ključ (.pem):", self.key_path_input)

        self.key_password_input = QLineEdit()
        self.key_password_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_password_input.setText(self.config.get("key_password", ""))
        self.key_password_input.setPlaceholderText(
            "Ostavite prazno ako nema lozinke")
        cert_layout.addRow("Lozinka ključa:", self.key_password_input)

        self.init_cert_btn = QPushButton("🔐  Inicijaliziraj certifikat")
        self.init_cert_btn.clicked.connect(self.init_certifikat)
        cert_layout.addRow(self.init_cert_btn)

        self.cert_panel.setVisible(False)
        cert_outer_layout.addWidget(self.cert_panel)

        cert_group.setLayout(cert_outer_layout)
        main_layout.addWidget(cert_group)

        # ── Izdavatelj + Kupac ───────────────────────────────────────────────
        stranke_layout = QHBoxLayout()
        stranke_layout.setSpacing(10)

        # Izdavatelj
        firma_group = QGroupBox("Izdavatelj")
        firma_layout = QFormLayout()
        firma_layout.setSpacing(7)
        firma_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.oib_input = QLineEdit()
        self.oib_input.setText(self.config.get("oib", ""))
        self.oib_input.setMaxLength(11)
        firma_layout.addRow("OIB:", self.oib_input)

        self.naziv_tvrtke_input = QLineEdit()
        self.naziv_tvrtke_input.setText(self.config.get("naziv_tvrtke", ""))
        self.naziv_tvrtke_input.setPlaceholderText("Moja tvrtka d.o.o.")
        firma_layout.addRow("Naziv:", self.naziv_tvrtke_input)

        self.adresa_input = QLineEdit()
        self.adresa_input.setText(self.config.get("adresa", ""))
        self.adresa_input.setPlaceholderText("Ulica i broj, Grad")
        firma_layout.addRow("Adresa:", self.adresa_input)

        self.iban_input = QLineEdit()
        self.iban_input.setText(self.config.get("iban", ""))
        self.iban_input.setPlaceholderText("HR12 1234 5678 9012 3456 7")
        firma_layout.addRow("IBAN:", self.iban_input)

        pp_nu_widget = QWidget()
        pp_nu_widget.setStyleSheet("background-color: transparent;")
        pp_nu_layout = QHBoxLayout(pp_nu_widget)
        pp_nu_layout.setSpacing(6)
        pp_nu_layout.setContentsMargins(0, 0, 0, 0)

        self.pp_input = QLineEdit()
        self.pp_input.setText(self.config.get("oznaka_pp", "PP1"))
        self.pp_input.setPlaceholderText("PP1")

        self.nu_input = QLineEdit()
        self.nu_input.setText(self.config.get("oznaka_nu", "1"))
        self.nu_input.setPlaceholderText("1")

        nu_label = QLabel("NU:")
        nu_label.setStyleSheet("background-color: transparent;")
        pp_nu_layout.addWidget(self.pp_input)
        pp_nu_layout.addWidget(nu_label)
        pp_nu_layout.addWidget(self.nu_input)
        firma_layout.addRow("PP / NU:", pp_nu_widget)

        # Gumb za spremanje konfiguracije
        spremi_config_btn = QPushButton("💾  Spremi kao zadano")
        spremi_config_btn.setObjectName("dangerBtn")
        spremi_config_btn.clicked.connect(self.spremi_konfiguraciju)
        firma_layout.addRow(spremi_config_btn)

        firma_group.setLayout(firma_layout)
        stranke_layout.addWidget(firma_group)

        # Kupac
        kupac_group = QGroupBox("Kupac")
        kupac_layout = QFormLayout()
        kupac_layout.setSpacing(7)
        kupac_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Red s inputom i gumbom za odabir
        kupac_naziv_row = QWidget()
        kupac_naziv_row.setStyleSheet("background-color: transparent;")
        kupac_naziv_hl = QHBoxLayout(kupac_naziv_row)
        kupac_naziv_hl.setContentsMargins(0, 0, 0, 0)
        kupac_naziv_hl.setSpacing(6)

        self.kupac_naziv_input = QLineEdit()
        self.kupac_naziv_input.setPlaceholderText("Naziv kupca / ime i prezime")
        kupac_naziv_hl.addWidget(self.kupac_naziv_input)

        self.odaberi_btn = QPushButton("📋")
        self.odaberi_btn.setObjectName("kupacBtn")
        self.odaberi_btn.setToolTip("Odaberi kupca iz imenika")
        self.odaberi_btn.setFixedWidth(36)
        self.odaberi_btn.setStyleSheet("""
            QPushButton {
                background-color: #e8f0fe;
                color: #3367d6;
                border: 1px solid #4a90d9;
                border-radius: 6px;
                padding: 5px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #4a90d9;
                color: white;
            }
            QPushButton:pressed {
                background-color: #357abd;
            }
            QToolTip {
                background-color: #ffffff;
                color: #2c2c2c;
                border: 1px solid #cdd1d9;
                border-radius: 4px;
                padding: 5px 8px;
                font-size: 11px;
            }
        """)
        self.odaberi_btn.clicked.connect(self.otvori_odabir_kupca)
        kupac_naziv_hl.addWidget(self.odaberi_btn)

        kupac_layout.addRow("Naziv:", kupac_naziv_row)

        self.kupac_oib_input = QLineEdit()
        self.kupac_oib_input.setPlaceholderText("OIB kupca (opcionalno)")
        self.kupac_oib_input.setMaxLength(11)
        kupac_layout.addRow("OIB:", self.kupac_oib_input)

        self.kupac_adresa_input = QLineEdit()
        self.kupac_adresa_input.setPlaceholderText("Ulica i broj, Grad")
        kupac_layout.addRow("Adresa:", self.kupac_adresa_input)

        self.pravna_osoba_check = QCheckBox("Pravna osoba — bez fiskalizacije")
        self.pravna_osoba_check.setChecked(False)
        self.pravna_osoba_check.stateChanged.connect(self.on_pravna_osoba_changed)
        kupac_layout.addRow("", self.pravna_osoba_check)

        self.fisk_info_label = QLabel("Račun će biti fiskaliziran")
        self.fisk_info_label.setStyleSheet(
            "color: #2e7d32; font-size: 10px; font-style: italic;")
        kupac_layout.addRow("", self.fisk_info_label)

        kupac_group.setLayout(kupac_layout)
        stranke_layout.addWidget(kupac_group)

        main_layout.addLayout(stranke_layout)

        # ── Račun ────────────────────────────────────────────────────────────
        racun_group = QGroupBox("Račun")
        racun_layout = QFormLayout()
        racun_layout.setSpacing(7)
        racun_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        # Datum + Rok u jednom redu
        datum_rok_widget = QWidget()
        datum_rok_widget.setStyleSheet("background-color: transparent;")
        datum_rok_layout = QHBoxLayout(datum_rok_widget)
        datum_rok_layout.setSpacing(6)
        datum_rok_layout.setContentsMargins(0, 0, 0, 0)

        self.datum_edit = NoScrollDateEdit()
        self.datum_edit.setDate(QDate.currentDate())
        self.datum_edit.setCalendarPopup(True)
        self.datum_edit.setDisplayFormat("dd.MM.yyyy")
        self.datum_edit.setFixedWidth(120)
        self.datum_edit.setStyleSheet("""
            QCalendarWidget {
                background-color: #ffffff;
                color: #2c2c2c;
            }
            QCalendarWidget QAbstractItemView {
                background-color: #ffffff;
                color: #2c2c2c;
                selection-background-color: #4a90d9;
                selection-color: #ffffff;
            }
            QCalendarWidget QAbstractItemView:disabled {
                color: #aaaaaa;
            }
            QCalendarWidget QWidget {
                background-color: #ffffff;
                color: #2c2c2c;
            }
            QCalendarWidget QToolButton {
                background-color: #ffffff;
                color: #2c2c2c;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
            }
            QCalendarWidget QToolButton:hover {
                background-color: #e8f0fe;
                color: #3367d6;
            }
            QCalendarWidget QSpinBox {
                background-color: #ffffff;
                color: #2c2c2c;
                border: 1px solid #cdd1d9;
                border-radius: 4px;
            }
            QCalendarWidget QMenu {
                background-color: #ffffff;
                color: #2c2c2c;
            }
            #qt_calendar_navigationbar {
                background-color: #f0f2f5;
                border-bottom: 1px solid #d0d4db;
                padding: 4px;
            }
        """)

        datum_rok_layout.addWidget(self.datum_edit)

        rok_label = QLabel("Rok plaćanja:")
        rok_label.setStyleSheet("background-color: transparent;")
        datum_rok_layout.addWidget(rok_label)

        self.rok_edit = NoScrollDateEdit()
        self.rok_edit.setDate(QDate.currentDate().addDays(15))
        self.rok_edit.setCalendarPopup(True)
        self.rok_edit.setDisplayFormat("dd.MM.yyyy")
        self.rok_edit.setFixedWidth(120)
        self.rok_edit.setStyleSheet("""
            QCalendarWidget {
                background-color: #ffffff;
                color: #2c2c2c;
            }
            QCalendarWidget QAbstractItemView {
                background-color: #ffffff;
                color: #2c2c2c;
                selection-background-color: #4a90d9;
                selection-color: #ffffff;
            }
            QCalendarWidget QAbstractItemView:disabled {
                color: #aaaaaa;
            }
            QCalendarWidget QWidget {
                background-color: #ffffff;
                color: #2c2c2c;
            }
            QCalendarWidget QToolButton {
                background-color: #ffffff;
                color: #2c2c2c;
                border: none;
                border-radius: 4px;
                padding: 4px 8px;
                font-weight: bold;
            }
            QCalendarWidget QToolButton:hover {
                background-color: #e8f0fe;
                color: #3367d6;
            }
            QCalendarWidget QSpinBox {
                background-color: #ffffff;
                color: #2c2c2c;
                border: 1px solid #cdd1d9;
                border-radius: 4px;
            }
            QCalendarWidget QMenu {
                background-color: #ffffff;
                color: #2c2c2c;
            }
            #qt_calendar_navigationbar {
                background-color: #f0f2f5;
                border-bottom: 1px solid #d0d4db;
                padding: 4px;
            }
        """)
        datum_rok_layout.addWidget(self.rok_edit)
        datum_rok_layout.addStretch()

        racun_layout.addRow("Datum računa:", datum_rok_widget)

        self.nacin_placanja_combo = NoScrollComboBox()
        self.nacin_placanja_combo.addItems([
            "G - Gotovina",
            "K - Kartica",
            "T - Transakcijski račun",
            "O - Ostalo"
        ])
        self.nacin_placanja_combo.setCurrentText("T - Transakcijski račun")
        self.nacin_placanja_combo.setFixedWidth(220)
        self.nacin_placanja_combo.currentIndexChanged.connect(
            self.on_nacin_placanja_changed)
        racun_layout.addRow("Način plaćanja:", self.nacin_placanja_combo)

        # PDV checkbox + info u jednom redu
        pdv_row = QHBoxLayout()
        pdv_row.setSpacing(12)
        self.u_sustavu_pdv_check = QCheckBox("U sustavu PDV-a")
        self.u_sustavu_pdv_check.setChecked(False)
        self.u_sustavu_pdv_check.stateChanged.connect(self.on_pdv_changed)
        pdv_row.addWidget(self.u_sustavu_pdv_check)

        self.pdv_info_label = QLabel("PDV se obračunava po stavci")
        self.pdv_info_label.setStyleSheet(
            "color: #7f8c8d; font-size: 10px; font-style: italic;")
        pdv_row.addWidget(self.pdv_info_label)
        pdv_row.addStretch()
        racun_layout.addRow("PDV:", pdv_row)

        # Osnova oslobođenja od PDV-a (vidljiva samo kad nije u sustavu PDV-a)
        self.pdv_oslobodenje_combo = NoScrollComboBox()
        self.pdv_oslobodenje_combo.addItems([
            "Čl. 90. st. 1 — usluge/roba, HR kupac, prodavatelj HR rezident",
            "Čl. 90. st. 2 — usluge/roba, HR kupac, prodavatelj EU rezident",
            "Čl. 17. st. 1 — inozemni klijent (reverse charge)",
        ])
        self.pdv_oslobodenje_combo.setFixedWidth(380)
        self.pdv_oslobodenje_combo.setToolTip(
            "Osnova oslobođenja od PDV-a koja će biti ispisana na računu")

        self.pdv_oslobodenje_label_row = QLabel("Osnova oslobođenja:")
        self.pdv_oslobodenje_label_row.setStyleSheet(
            "background-color: transparent;")
        racun_layout.addRow(self.pdv_oslobodenje_label_row,
                            self.pdv_oslobodenje_combo)

        self.operater_input = QLineEdit()
        self.operater_input.setPlaceholderText("Ime i prezime operatera")
        self.operater_input.setFixedWidth(250)
        self.operater_input.setText(self.config.get("operater", ""))
        racun_layout.addRow("Operater:", self.operater_input)

        racun_group.setLayout(racun_layout)
        main_layout.addWidget(racun_group)

        # ── Stavke ───────────────────────────────────────────────────────────
        stavke_group = QGroupBox("Stavke računa")
        stavke_layout = QVBoxLayout()
        stavke_layout.setContentsMargins(6, 6, 6, 6)
        self.stavke_widget = StavkeWidget(u_sustavu_pdv=self.u_sustavu_pdv_check.isChecked())
        stavke_layout.addWidget(self.stavke_widget)
        stavke_group.setLayout(stavke_layout)
        main_layout.addWidget(stavke_group)

        # Inicijalno ažuriranje labela
        self.on_pdv_changed()
        self.on_nacin_placanja_changed()

        # ── Napomena ─────────────────────────────────────────────────────────
        napomena_group = QGroupBox("Napomena")
        napomena_layout = QVBoxLayout()
        napomena_layout.setContentsMargins(6, 6, 6, 6)
        self.napomena_input = QTextEdit()
        self.napomena_input.setMaximumHeight(65)
        self.napomena_input.setPlaceholderText(
            "Slobodan tekst na računu (opcionalno)")
        napomena_layout.addWidget(self.napomena_input)
        napomena_group.setLayout(napomena_layout)
        main_layout.addWidget(napomena_group)

        # ── Gumb za spremanje ────────────────────────────────────────────────
        self.save_btn = QPushButton("💾   Spremi i fiskaliziraj račun")
        self.save_btn.setObjectName("saveBtn")
        self.save_btn.clicked.connect(self.save_invoice)
        main_layout.addWidget(self.save_btn)

        # ── Tab widget ───────────────────────────────────────────────────────
        from PySide6.QtWidgets import QTabWidget

        tabs = QTabWidget()
        tabs.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #d0d4db;
                border-radius: 8px;
                background-color: #f0f2f5;
            }
            QTabBar::tab {
                background-color: #e0e4ea;
                color: #555;
                padding: 8px 20px;
                border-top-left-radius: 6px;
                border-top-right-radius: 6px;
                margin-right: 2px;
                font-weight: bold;
            }
            QTabBar::tab:selected {
                background-color: #4a90d9;
                color: white;
            }
            QTabBar::tab:hover:!selected {
                background-color: #c8d0dc;
            }
        """)

        scroll.setWidget(container)
        tabs.addTab(scroll, "📄  Novi račun")

        # ── Tab pregleda računa ──────────────────────────────────────────────
        self.pregled_widget = PregledRacunaWidget()
        tabs.addTab(self.pregled_widget, "📋  Pregled računa")

        # ── KPR tab ──────────────────────────────────────────────────
        self.kpr_widget = KPRWidget()
        tabs.addTab(self.kpr_widget, "📒  KPR")

        # Osvježi tabove kad se prebaci
        def _on_tab_changed(i):
            if i == 1:
                self.pregled_widget.osvjezi()
            elif i == 2:
                self.kpr_widget.osvjezi()

        tabs.currentChanged.connect(_on_tab_changed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(tabs)

        self.init_certifikat(silent=True)

    # -----------------------------------------------------------------------

    def toggle_cert_panel(self, checked):
        self.cert_panel.setVisible(checked)
        self.toggle_cert_btn.setText("✖️ Zatvori" if checked else "⚙️ Postavke")

    def on_pravna_osoba_changed(self):
        if self.pravna_osoba_check.isChecked():
            self.fisk_info_label.setText(
                "Pravna osoba — račun NEĆE biti fiskaliziran")
            self.fisk_info_label.setStyleSheet(
                "color: #e67e22; font-size: 10px; font-style: italic;")
            self.save_btn.setText("💾   Spremi račun (bez fiskalizacije)")
            self.save_btn.setStyleSheet(
                "padding: 12px; font-weight: bold; font-size: 13px;"
                "background-color: #e67e22;")
        else:
            self.fisk_info_label.setText("Račun će biti fiskaliziran")
            self.fisk_info_label.setStyleSheet(
                "color: #2e7d32; font-size: 10px; font-style: italic;")
            self.save_btn.setText("💾   Spremi i fiskaliziraj račun")
            self.save_btn.setStyleSheet("")  # Reset na stylesheet default

    def on_pdv_changed(self):
        checked = self.u_sustavu_pdv_check.isChecked()
        self.stavke_widget.set_pdv_mode(checked)
        # Polje osnove oslobođenja vidljivo samo kad NIJE u sustavu PDV-a
        self.pdv_oslobodenje_combo.setVisible(not checked)
        self.pdv_oslobodenje_label_row.setVisible(not checked)
        if checked:
            self.pdv_info_label.setText("PDV se obračunava po stavci")
            self.pdv_info_label.setStyleSheet("color: gray; font-size: 10px;")
        else:
            self.pdv_info_label.setText(
                "Nije obveznik PDV-a — PDV nije obračunan")
            self.pdv_info_label.setStyleSheet(
                "color: blue; font-size: 10px;")

    def on_nacin_placanja_changed(self):
        tekst = self.nacin_placanja_combo.currentText()
        # Rok plaćanja ima smisla samo kod transakcijskog
        ima_rok = tekst.startswith("T")
        self.rok_edit.setEnabled(ima_rok)

        # Postavljanje NU ovisno o načinu plaćanja
        if tekst.startswith("T"):
            self.nu_input.setText("1")
        elif tekst.startswith("G") or tekst.startswith("K"):
            self.nu_input.setText("2")

    def init_certifikat(self, silent=False):
        cert_path = self.cert_path_input.text().strip()
        key_path = self.key_path_input.text().strip()
        key_password = self.key_password_input.text().strip() or None

        if not cert_path or not key_path:
            if not silent:
                QMessageBox.warning(self, "Greška",
                                    "Unesite putanje do certifikata i ključa!")
            return

        if not os.path.exists(cert_path):
            self.cert_status_label.setText("❌ Certifikat nije pronađen")
            self.cert_status_label.setStyleSheet("color: red;")
            if not silent:
                QMessageBox.warning(self, "Greška",
                                    f"Certifikat nije pronađen:\n{cert_path}")
            return

        if not os.path.exists(key_path):
            self.cert_status_label.setText("❌ Ključ nije pronađen")
            self.cert_status_label.setStyleSheet("color: red;")
            if not silent:
                QMessageBox.warning(self, "Greška",
                                    f"Ključ nije pronađen:\n{key_path}")
            return

        try:
            self.fiskalizacija = Fiskalizacija(
                cert_path=cert_path,
                key_path=key_path,
                key_password=key_password,
                demo=self.demo_mode
            )
            okolina = "DEMO" if self.demo_mode else "PRODUKCIJA"
            self.cert_status_label.setText(f"✅ Certifikat aktivan ({okolina})")
            self.cert_status_label.setStyleSheet("color: green;")

            # Zatvori panel nakon uspješne inicijalizacije
            self.cert_panel.setVisible(False)
            self.toggle_cert_btn.setChecked(False)
            self.toggle_cert_btn.setText("⚙️ Postavke")

            self.config["cert_path"] = cert_path
            self.config["key_path"] = key_path
            spremi_config(self.config)

            if not silent:
                QMessageBox.information(self, "Uspjeh",
                                        "Certifikat uspješno učitan!")
        except Exception as e:
            self.fiskalizacija = None
            self.cert_status_label.setText("❌ Greška pri učitavanju")
            self.cert_status_label.setStyleSheet("color: red;")
            if not silent:
                QMessageBox.critical(self, "Greška certifikata",
                                     f"Nije moguće učitati certifikat:\n{e}")
    # -----------------------------------------------------------------------

    def spremi_konfiguraciju(self):
        """Sprema trenutne podatke izdavatelja i certifikata u config.json."""
        data = {
            "oib": self.oib_input.text().strip(),
            "naziv_tvrtke": self.naziv_tvrtke_input.text().strip(),
            "adresa": self.adresa_input.text().strip(),
            "iban": self.iban_input.text().strip(),
            "oznaka_pp": self.pp_input.text().strip(),
            "oznaka_nu": self.nu_input.text().strip(),
            "cert_path": self.cert_path_input.text().strip(),
            "key_path": self.key_path_input.text().strip(),
            "key_password": self.key_password_input.text().strip(),
            "demo_mode": self.demo_mode,
            "operater": self.operater_input.text().strip()
        }
        self.config.update(data)
        spremi_config(self.config)
        QMessageBox.information(
            self, "Spremljeno",
            f"Konfiguracija spremljena u {CONFIG_PATH}\n"
            "Podaci će biti automatski učitani pri sljedećem pokretanju.")

    def otvori_odabir_kupca(self):
        """Otvara dijaloški prozor za odabir kupca."""
        self.dialog_kupac = OdabirKupcaDialog(self)
        self.dialog_kupac.kupac_odabran.connect(self.popuni_kupca)
        # self.dialog_kupac.show()
        self.dialog_kupac.show_above_parent()

    def popuni_kupca(self, podaci):
        """Popunjava polja kupca s odabranim podacima."""
        self.kupac_naziv_input.setText(podaci.get('naziv', ''))
        self.kupac_oib_input.setText(podaci.get('oib', ''))
        self.kupac_adresa_input.setText(podaci.get('adresa', ''))
        self.pravna_osoba_check.setChecked(
            podaci.get('pravna_osoba', False))

    def save_invoice(self):
        # Validacija izdavatelja
        oib = self.oib_input.text().strip()
        naziv_tvrtke = self.naziv_tvrtke_input.text().strip()
        adresa = self.adresa_input.text().strip()
        iban = self.iban_input.text().strip()
        oznaka_pp = self.pp_input.text().strip()
        oznaka_nu = self.nu_input.text().strip()

        # Validacija kupca
        kupac_naziv = self.kupac_naziv_input.text().strip()
        kupac_oib = self.kupac_oib_input.text().strip()
        kupac_adresa = self.kupac_adresa_input.text().strip()

        # Račun
        u_sustavu_pdv = self.u_sustavu_pdv_check.isChecked()
        pravna_osoba = self.pravna_osoba_check.isChecked()
        nacin_tekst = self.nacin_placanja_combo.currentText()
        nacin_kod = nacin_tekst[0]  # G, K, T ili O
        datum = self.datum_edit.date().toString("dd.MM.yyyy")
        rok = self.rok_edit.date().toString("dd.MM.yyyy")
        napomena = self.napomena_input.toPlainText().strip()
        operater = self.operater_input.text().strip()
        # Osnova oslobođenja — relevantna samo kad nije u sustavu PDV-a
        if not u_sustavu_pdv:
            pdv_oslobodenje = PDV_OSLOBODENJE_TEKSTOVI[
                self.pdv_oslobodenje_combo.currentIndex()]
        else:
            pdv_oslobodenje = ""

        # Stavke
        stavke = self.stavke_widget.get_stavke()

        # Validacija
        if len(oib) != 11 or not oib.isdigit():
            QMessageBox.warning(self, "Greška", "OIB mora imati 11 znamenki!")
            return
        if not naziv_tvrtke:
            QMessageBox.warning(self, "Greška",
                                "Unesite naziv izdavatelja!")
            return
        if not kupac_naziv:
            QMessageBox.warning(self, "Greška", "Unesite naziv kupca!")
            return
        if not stavke:
            QMessageBox.warning(self, "Greška",
                                "Dodajte barem jednu stavku!")
            return
        if kupac_oib and (len(kupac_oib) != 11 or not kupac_oib.isdigit()):
            QMessageBox.warning(self, "Greška",
                                "OIB kupca mora imati 11 znamenki!")
            return

        ukupan_iznos = round(sum(s['ukupno'] for s in stavke), 2)
        pdv_grupe = self.stavke_widget.get_pdv_grupe() if u_sustavu_pdv else []

        now = datetime.now()

        print("conn.in_transaction =", conn.in_transaction)
        # Dohvati sljedeći broj za ovaj PP/NU
        godina, broj_racuna = sljedeci_broj_racuna(oznaka_pp, oznaka_nu)
        broj_racuna_str = f"{broj_racuna}/{oznaka_pp}/{oznaka_nu}"

        # Spremi račun u bazu
        cursor.execute("""
                       INSERT INTO invoices (
                           datum, rok_placanja, nacin_placanja,
                           oib_izdavatelja, naziv_izdavatelja, adresa_izdavatelja, iban,
                           oib_kupca, naziv_kupca, adresa_kupca,
                           oznaka_pp, oznaka_nu, u_sustavu_pdv, ukupan_iznos,
                           napomena, pravna_osoba, broj_racuna, operater, datum_kreiranja
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       """, (datum, rok, nacin_kod,
                             oib, naziv_tvrtke, adresa, iban,
                             kupac_oib, kupac_naziv, kupac_adresa,
                             oznaka_pp, oznaka_nu, int(u_sustavu_pdv),
                             ukupan_iznos, napomena, int(pravna_osoba), broj_racuna_str,
                             operater, now.isoformat()))
        conn.commit()
        invoice_id = cursor.lastrowid

        # Nakon conn.commit() za invoice_id — spremi/ažuriraj kupca
        if kupac_naziv:
            spremi_kupca(
                naziv=kupac_naziv,
                oib=kupac_oib,
                adresa=kupac_adresa,
                pravna_osoba=pravna_osoba
            )

        # Spremi stavke
        for s in stavke:
            cursor.execute("""
                           INSERT INTO invoice_stavke
                           (invoice_id, naziv, kolicina, jedinica,
                            cijena, pdv_stopa)
                           VALUES (?, ?, ?, ?, ?, ?)
                           """, (invoice_id, s['naziv'], s['kolicina'],
                                 s['jedinica'], s['cijena'], s['pdv_stopa']))
        conn.commit()

        # Fiskalizacija
        racun_data = {
            'oib': oib,
            'broj_racuna': str(broj_racuna),
            'oznaka_pp': oznaka_pp,
            'oznaka_nu': oznaka_nu,
            'ukupan_iznos': ukupan_iznos,
            'oib_operatera': oib,
            'u_sustavu_pdv': u_sustavu_pdv,
            'nacin_placanja': nacin_kod,
            'pdv': pdv_grupe
        }

        jir = None
        zki = None

        if pravna_osoba:
            # Pravna osoba — samo spremi, bez fiskalizacije
            print(f"📄 Račun #{invoice_id} kreiran bez fiskalizacije (pravna osoba)")
            zki = hashlib.md5(
                f"{oib}{invoice_id}{ukupan_iznos}".encode()).hexdigest()
            cursor.execute("""
                UPDATE invoices
                SET zki=?, fiskaliziran=0, datum_fiskalizacije=?
                WHERE id=?
            """, (zki, now.isoformat(), invoice_id))
            conn.commit()

        elif self.fiskalizacija:
            print(f"\n📤 Fiskalizacija računa #{invoice_id}...")
            jir, zki = self.fiskalizacija.fiskaliziraj_racun(racun_data)

            if jir:
                cursor.execute("""
                    UPDATE invoices
                    SET jir=?, zki=?, fiskaliziran=1,
                        datum_fiskalizacije=?
                    WHERE id=?
                """, (jir, zki, now.isoformat(), invoice_id))
            elif zki:
                cursor.execute("""
                    UPDATE invoices
                    SET zki=?, fiskaliziran=0,
                        datum_fiskalizacije=?
                    WHERE id=?
                """, (zki, now.isoformat(), invoice_id))
            conn.commit()

        else:
            # Demo bez certifikata
            zki = hashlib.md5(
                f"{oib}{invoice_id}{ukupan_iznos}".encode()).hexdigest()
            QMessageBox.warning(self, "Upozorenje",
                                "Certifikat nije inicijaliziran.\n"
                                "Račun NIJE fiskaliziran.")

        # Poruka o statusu
        if pravna_osoba:
            status = "📄 Račun kreiran (pravna osoba, bez fiskalizacije)"
        elif jir:
            status = f"✅ Fiskalizirano!\nJIR: {jir}"
        elif self.fiskalizacija:
            status = "⚠️ JIR nije dobiven"
        else:
            status = "⚠️ Demo — certifikat nije konfiguriran"

        # Generiraj PDF
        self.generate_pdf(
            invoice_id=f"{broj_racuna}_{oznaka_pp}_{oznaka_nu}",
            broj_racuna_str=broj_racuna_str,
            datum=datum,
            rok_placanja=rok,
            nacin_placanja=nacin_tekst,
            oib=oib,
            naziv_tvrtke=naziv_tvrtke,
            adresa=adresa,
            iban=iban,
            kupac_naziv=kupac_naziv,
            kupac_oib=kupac_oib,
            kupac_adresa=kupac_adresa,
            oznaka_pp=oznaka_pp,
            oznaka_nu=oznaka_nu,
            stavke=stavke,
            ukupan_iznos=ukupan_iznos,
            napomena=napomena,
            jir=jir,
            zki=zki,
            u_sustavu_pdv=u_sustavu_pdv,
            datum_vrijeme=now,
            operater=operater,
            pdv_oslobodenje=pdv_oslobodenje
        )

        if jir:
            status = f"✅ Fiskalizirano!\nJIR: {jir}"
        elif self.fiskalizacija:
            status = "⚠️ JIR nije dobiven"
        else:
            status = "⚠️ Demo — certifikat nije konfiguriran"

        # Automatski unos u KPR
        _dodaj_u_kpr(conn, cursor, invoice_id, nacin_kod, datum,
                     broj_racuna_str, ukupan_iznos)

        QMessageBox.information(
            self, "Račun spremljen",
            f"Račun {broj_racuna_str} spremljen!\n"
            f"Iznos: {ukupan_iznos:.2f} EUR\n"
            f"{status}"
        )

        # Reset kupca i stavki
        self.kupac_naziv_input.clear()
        self.kupac_oib_input.clear()
        self.kupac_adresa_input.clear()
        self.pravna_osoba_check.setChecked(False)
        self.napomena_input.clear()
        # Reset tablice stavki
        self.stavke_widget.tabla.setRowCount(0)
        self.stavke_widget.dodaj_stavku()

    def prefill_storno(self, podaci: dict):
        """Prefilla formu za storno račun i prebacuje na tab 'Novi račun'."""
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
        nacin_map = {
            'G': 'G - Gotovina',
            'K': 'K - Kartica',
            'T': 'T - Transakcijski račun',
            'O': 'O - Ostalo'
        }
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
            self.stavke_widget.tabla.item(row, 1).setText(
                str(s['kolicina']))
            self.stavke_widget.tabla.item(row, 2).setText(s['jedinica'])
            self.stavke_widget.tabla.item(row, 3).setText(
                f"{s['cijena']:.2f}")
            combo = self.stavke_widget.tabla.cellWidget(row, 4)
            if combo:
                combo.setCurrentText(str(int(s['pdv_stopa'])))
        self.stavke_widget.azuriraj_ukupno()

    # -----------------------------------------------------------------------

    def generate_pdf(self, invoice_id, broj_racuna_str, datum, rok_placanja, nacin_placanja,
                     oib, naziv_tvrtke, adresa, iban,
                     kupac_naziv, kupac_oib, kupac_adresa,
                     oznaka_pp, oznaka_nu, stavke, ukupan_iznos,
                     napomena, jir, zki, u_sustavu_pdv, datum_vrijeme,
                     operater="", pdv_oslobodenje=""):
        """Generira PDF računa s punim sadržajem."""
        # Koristi centralnu funkciju za font
        fn, fn_b = get_font()

        filename = f"invoice_{invoice_id}.pdf"
        c = canvas.Canvas(filename, pagesize=A4)
        W, H = A4  # 595 x 842 pt

        y = H - 40  # Početna y pozicija

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

        # ── Zaglavlje ────────────────────────────────────────────────────────
        text(40, y, naziv_tvrtke, fn_b, 14)
        text(40, y - 16, adresa, fn, 9, (0.4, 0.4, 0.4))
        text(40, y - 28, f"OIB: {oib}", fn, 9, (0.4, 0.4, 0.4))
        if iban:
            text(40, y - 40, f"IBAN: {iban}", fn, 9, (0.4, 0.4, 0.4))

        # Broj računa desno
        rtext(W - 40, y, f"RAČUN", fn_b, 18)
        rtext(W - 40, y - 20, broj_racuna_str, fn_b, 12)
        rtext(W - 40, y - 35, f"Datum: {datum}", fn, 9)

        nacin_tekst = nacin_placanja.split(" - ")[-1] if " - " in nacin_placanja else nacin_placanja
        rtext(W - 40, y - 47, f"Plaćanje: {nacin_tekst}", fn, 9)
        if nacin_placanja.startswith("T"):
            rtext(W - 40, y - 59, f"Rok: {rok_placanja}", fn, 9)

        # Vrijeme kreiranja i operater desno, ispod rok plaćanja
        vrijemeY = y - 71 if nacin_placanja.startswith("T") else y - 59
        vrijemeStr = (datum_vrijeme.strftime("%d.%m.%Y  %H:%M:%S")
                      if isinstance(datum_vrijeme, datetime) else str(datum_vrijeme))
        rtext(W - 40, vrijemeY, f"Kreirano: {vrijemeStr}", fn, 8)
        if operater:
            rtext(W - 40, vrijemeY - 11, f"Operater: {operater}", fn, 8)

        y -= 75
        line(y)
        y -= 15

        # ── Kupac ────────────────────────────────────────────────────────────
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

        # ── Tablica stavki ───────────────────────────────────────────────────
        text(40, y, "STAVKE RAČUNA", fn_b, 9, (0.5, 0.5, 0.5))
        y -= 14

        # Zaglavlje tablice
        col_x = [40, 210, 260, 315, 370, 450, W - 40]
        # Naziv(170) | Jed.(50) | Kol.(55) | Cijena(60) | PDV%(63) | Osnov.(62) | Ukupno

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

        # Redovi stavki
        for idx, s in enumerate(stavke):
            if idx % 2 == 0:
                c.setFillColorRGB(0.97, 0.97, 0.97)
                c.rect(40, y - 4, W - 80, 15, fill=1, stroke=0)
                c.setFillColorRGB(0, 0, 0)

            c.setFont(fn, 8)
            # Skrati naziv ako je predugačak
            naziv = s['naziv']
            if len(naziv) > 35:
                naziv = naziv[:33] + "..."
            c.drawString(col_x[0], y, naziv)
            c.drawString(col_x[1], y, s['jedinica'])
            c.drawRightString(col_x[2] + 40, y, f"{s['kolicina']:.2f}")
            c.drawRightString(col_x[3] + 50, y, f"{s['cijena']:.2f}")

            if u_sustavu_pdv:
                c.drawRightString(col_x[4] + 40, y,
                                  f"{s['pdv_stopa']:.0f}%")
                c.drawRightString(col_x[5] + 50, y,
                                  f"{s['ukupno_bez_pdv']:.2f}")
            else:
                c.drawRightString(col_x[4] + 40, y, "-")
                c.drawRightString(col_x[5] + 50, y, "-")

            c.drawRightString(col_x[6], y, f"{s['ukupno']:.2f}")
            y -= 16

            if y < 180:  # Nova stranica ako nema mjesta
                c.showPage()
                y = H - 60

        line(y)
        y -= 15

        # ── Rekapitulacija PDV-a ─────────────────────────────────────────────
        if u_sustavu_pdv:
            pdv_grupe = {}
            for s in stavke:
                st = s['pdv_stopa']
                if st not in pdv_grupe:
                    pdv_grupe[st] = {'osnov': 0, 'pdv': 0, 'ukupno': 0}
                pdv_grupe[st]['osnov'] += s['ukupno_bez_pdv']
                pdv_grupe[st]['pdv'] += s['pdv_iznos']
                pdv_grupe[st]['ukupno'] += s['ukupno']

            text(40, y, "REKAPITULACIJA PDV-a", fn_b, 8, (0.5, 0.5, 0.5))
            y -= 13
            text(40, y, "Stopa", fn_b, 8)
            text(120, y, "Osnovica", fn_b, 8)
            text(220, y, "PDV iznos", fn_b, 8)
            text(320, y, "Ukupno", fn_b, 8)
            y -= 12

            for stopa, v in sorted(pdv_grupe.items()):
                text(40, y, f"{stopa:.0f}%", fn, 8)
                text(120, y, f"{v['osnov']:.2f} EUR", fn, 8)
                text(220, y, f"{v['pdv']:.2f} EUR", fn, 8)
                text(320, y, f"{v['ukupno']:.2f} EUR", fn, 8)
                y -= 12

            y -= 5
            line(y)
            y -= 15
        else:
            if pdv_oslobodenje:
                text(40, y, pdv_oslobodenje, fn, 9, (0.3, 0.3, 0.7))
            else:
                text(40, y,
                     "Nije obveznik PDV-a — PDV nije obračunan",
                     fn, 9, (0.3, 0.3, 0.7))
            y -= 20

        # ── Ukupan iznos ─────────────────────────────────────────────────────
        c.setFillColorRGB(0.1, 0.1, 0.1)
        c.rect(W - 200, y - 6, 160, 22, fill=1, stroke=0)
        c.setFillColorRGB(1, 1, 1)
        c.setFont(fn_b, 13)
        c.drawString(W - 195, y, "UKUPNO:")
        c.drawRightString(W - 45, y, f"{ukupan_iznos:.2f} EUR")
        c.setFillColorRGB(0, 0, 0)
        y -= 30

        # IBAN za transakcijsko plaćanje
        if nacin_placanja.startswith("T") and iban:
            text(40, y, f"Uplatite na IBAN: {iban}", fn_b, 9)
            text(40, y - 12,
                 f"Rok plaćanja: {rok_placanja}", fn, 9)
            y -= 30

        # ── Napomena ─────────────────────────────────────────────────────────
        if napomena:
            line(y)
            y -= 12
            text(40, y, "Napomena:", fn_b, 9)
            y -= 13
            # Prelom teksta napomene
            words = napomena.split()
            line_text = ""
            for word in words:
                test_line = line_text + (" " if line_text else "") + word
                c.setFont(fn, 9)
                if c.stringWidth(test_line) > W - 100:
                    text(40, y, line_text, fn, 9)
                    y -= 12
                    line_text = word
                else:
                    line_text = test_line
            if line_text:
                text(40, y, line_text, fn, 9)
                y -= 15

        # ── Fiskalizacijski podaci + QR kod ──────────────────────────────────
        line(y)
        y -= 15

        qr_x = 40
        qr_y = y - 85
        qr_size = 80

        # QR kod
        if self.fiskalizacija and (jir or zki):
            try:
                qr_bytes = self.fiskalizacija.qr_kod_kao_bytes(
                    jir=jir,
                    zki=zki if not jir else None,
                    datum_vrijeme=datum_vrijeme,
                    ukupan_iznos=ukupan_iznos
                )
                if qr_bytes:
                    with tempfile.NamedTemporaryFile(
                            suffix='.png', delete=False) as tmp:
                        tmp.write(qr_bytes.read())
                        tmp_path = tmp.name
                    try:
                        c.drawImage(tmp_path, qr_x, qr_y,
                                    width=qr_size, height=qr_size)
                        text(qr_x, qr_y - 10,
                             "Skenirajte za provjeru računa", fn, 7,
                             (0.5, 0.5, 0.5))
                    finally:
                        os.unlink(tmp_path)
            except Exception as e:
                print(f"⚠️  QR greška: {e}")

        # Fiskalizacijski tekst desno od QR koda
        fx = qr_x + qr_size + 15
        text(fx, y - 5, "Fiskalizacijski podaci", fn_b, 8,
             (0.5, 0.5, 0.5))
        text(fx, y - 18, f"ZKI: {zki or 'N/A'}", fn, 7)

        jir_tekst = jir if jir else "Račun nije fiskaliziran"
        text(fx, y - 30, f"JIR: {jir_tekst}", fn, 7)

       # fisk_status = "[OK] FISKALIZIRANO" if jir else "[X] NIJE FISKALIZIRANO"
        fisk_status = "✓ FISKALIZIRANO" if jir else "✗ NIJE FISKALIZIRANO"
        boja = (0, 0.5, 0) if jir else (0.7, 0, 0)
        text(fx, y - 44, fisk_status, fn_b, 8, boja)

        c.showPage()
        c.save()
        print(f"✅ PDF generiran: {filename}")


# ---------------------------------------------------------------------------
# Pokretanje
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import faulthandler
    faulthandler.enable()

    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "0"
    os.environ["QT_SCALE_FACTOR"] = "1"

    app = QApplication(sys.argv)
    from PySide6.QtGui import QPalette, QColor
    tooltip_palette = app.palette()
    tooltip_palette.setColor(QPalette.ColorRole.ToolTipBase, QColor("#ffffff"))
    tooltip_palette.setColor(QPalette.ColorRole.ToolTipText, QColor("#2c2c2c"))
    app.setPalette(tooltip_palette)

    app.setStyleSheet("""
        QToolTip {
            background-color: #ffffff;
            color: #2c2c2c;
            border: 1px solid #cdd1d9;
            border-radius: 4px;
            padding: 5px 8px;
            font-size: 11px;
        }
    """)
    window = BillingApp()
    window.show()
    sys.exit(app.exec())