"""Komunikacija s CIS-om (Porezna uprava): ZKI, potpis XML-a, slanje, QR kod.

Modul ne ovisi o korisničkom sučelju (Qt) ni o bazi, pa se može testirati
samostalno (vidi tests/test_fiskalizacija.py).
"""
import base64
import hashlib
import os
import uuid
from datetime import datetime
from io import BytesIO

import lxml.etree as etree
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.x509 import load_pem_x509_certificate

from arhiva_util import spremi_poruku

try:
    import qrcode
    QR_DOSTUPAN = True
except ImportError:
    QR_DOSTUPAN = False
    print("⚠️  qrcode nije instaliran: pip install qrcode[pil]")


class Fiskalizacija:
    FISKAL_URL_DEMO = "https://cistest.apis-it.hr:8449/FiskalizacijaServiceTest"
    FISKAL_URL_PROD = "https://cis.porezna-uprava.hr:8449/FiskalizacijaService"

    def __init__(self, cert_path, key_path, key_password=None, demo=True,
                 arhiva_mapa=None):
        self.arhiva_mapa = arhiva_mapa or os.getcwd()
        self.cert_path = cert_path
        self.key_path = key_path
        self.key_password = key_password
        self.url = self.FISKAL_URL_DEMO if demo else self.FISKAL_URL_PROD
        self.zadnje_vrijeme = None
        self.zadnja_greska = None

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
        # Spec 2.7, pogl. 12: datVrij = 'dd.MM.yyyy HH:mm:ss' (razmak, bez 'T'),
        # decimalni separator je točka, potpis RSA-SHA256
        datv_zki = datum_vrijeme.replace('T', ' ')
        iznos_str = f"{ukupan_iznos:.2f}"
        data = (f"{oib}{datv_zki}{broj_racuna}"
                f"{oznaka_poslovnog_prostora}{oznaka_naplatnog_uredaja}{iznos_str}")
        signature = self.private_key.sign(
            data.encode('utf-8'), padding.PKCS1v15(), hashes.SHA256())
        return hashlib.md5(signature).hexdigest()


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
        # iznos u centima, bez separatora (npr. 125,00 EUR -> 12500)
        iznos_str = str(int(round(float(ukupan_iznos) * 100)))

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
            print("🔐 Potpisujem XML (RSA-SHA256)...")
            root = etree.fromstring(xml_string.encode('utf-8'))
            canonical_root = etree.tostring(root, method='c14n', exclusive=True)
            sha1_hash = hashlib.sha256(canonical_root).digest()
            digest_b64 = base64.b64encode(sha1_hash).decode('utf-8')

            sig_ns = '{http://www.w3.org/2000/09/xmldsig#}'
            signature_elem = etree.Element(f'{sig_ns}Signature')
            signed_info = etree.SubElement(signature_elem, f'{sig_ns}SignedInfo')

            canon = etree.SubElement(signed_info, f'{sig_ns}CanonicalizationMethod')
            canon.set('Algorithm', 'http://www.w3.org/2001/10/xml-exc-c14n#')

            sig_method = etree.SubElement(signed_info, f'{sig_ns}SignatureMethod')
            sig_method.set('Algorithm',
                           'http://www.w3.org/2001/04/xmldsig-more#rsa-sha256')

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
                              'http://www.w3.org/2001/04/xmlenc#sha256')
            digest_value = etree.SubElement(ref, f'{sig_ns}DigestValue')
            digest_value.text = digest_b64

            canonical_signed_info = etree.tostring(
                signed_info, method='c14n', exclusive=True)
            signature = self.private_key.sign(
                canonical_signed_info, padding.PKCS1v15(), hashes.SHA256())
            signature_b64 = base64.b64encode(signature).decode('utf-8')

            sig_value = etree.SubElement(signature_elem,
                                         f'{sig_ns}SignatureValue')
            sig_value.text = signature_b64

            key_info = etree.SubElement(signature_elem, f'{sig_ns}KeyInfo')
            x509_data = etree.SubElement(key_info, f'{sig_ns}X509Data')
            x509_cert = etree.SubElement(x509_data, f'{sig_ns}X509Certificate')

            x509_cert.text = base64.b64encode(
                self.certificate.public_bytes(serialization.Encoding.DER)
            ).decode('ascii')
            issuer_serial = etree.SubElement(x509_data, f'{sig_ns}X509IssuerSerial')
            etree.SubElement(issuer_serial, f'{sig_ns}X509IssuerName').text = \
                self.certificate.issuer.rfc4514_string()
            etree.SubElement(issuer_serial, f'{sig_ns}X509SerialNumber').text = \
                str(self.certificate.serial_number)

            root.append(signature_elem)
            print("✅ XML potpisao!")
            return etree.tostring(root, encoding='utf-8')

        except Exception as e:
            print(f"⚠️  Greška pri potpisivanju: {e}")
            import traceback; traceback.print_exc()
            return xml_string.encode('utf-8')

    def izgradi_zahtjev(self, racun_data):
        """Gradi (nepotpisani) RacunZahtjev XML bez ikakvog mrežnog poziva.
        Vraća (xml, zki, datum_vrijeme izdavanja)."""
        msg_id = str(uuid.uuid4())
        # Vrijeme slanja poruke (zaglavlje) i vrijeme izdavanja računa su
        # različiti pojmovi: kod ponovnog slanja račun zadržava izvorno
        # vrijeme izdavanja i ZKI, a NakDost je true.
        vrijeme_slanja = datetime.now().strftime("%d.%m.%YT%H:%M:%S")
        datum_vrijeme = racun_data.get('datum_vrijeme') or vrijeme_slanja
        nak_dost = str(bool(racun_data.get('naknadna_dostava'))).lower()

        zki = racun_data.get('zki') or self.generiraj_zki(
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
    <tns:DatumVrijeme>{vrijeme_slanja}</tns:DatumVrijeme>
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
    <tns:NakDost>{nak_dost}</tns:NakDost>
</tns:Racun>
</tns:RacunZahtjev>'''
        return racun_zahtjev_xml, zki, datum_vrijeme

    def fiskaliziraj_racun(self, racun_data):
        zki = racun_data.get('zki')
        try:
            import requests
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            self.zadnje_vrijeme = None
            self.zadnja_greska = None
            racun_zahtjev_xml, zki, datum_vrijeme = self.izgradi_zahtjev(
                racun_data)
            self.zadnje_vrijeme = datum_vrijeme
            u_sustavu_pdv = racun_data.get('u_sustavu_pdv', True)

            print(f"📤 Racun zahtjev: {racun_zahtjev_xml}")

            signed_zahtjev = self._potpiši_xml_enveloped(racun_zahtjev_xml)
            soap_body = f'''<?xml version="1.0" encoding="UTF-8"?>
<soap:Envelope xmlns:soap="http://schemas.xmlsoap.org/soap/envelope/">
    <soap:Body>
        {signed_zahtjev.decode('utf-8')}
    </soap:Body>
</soap:Envelope>'''

            oznaka_arhive = (f"{racun_data['broj_racuna']}-"
                             f"{racun_data['oznaka_pp']}-{racun_data['oznaka_nu']}")
            godina_arhive = datum_vrijeme[6:10]
            spremi_poruku(self.arhiva_mapa, godina_arhive, oznaka_arhive, "zahtjev",
                          soap_body)

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
            spremi_poruku(self.arhiva_mapa, godina_arhive, oznaka_arhive,
                          f"odgovor_http{response.status_code}", response.content)

            root = etree.fromstring(response.content)
            ns = {'soap': 'http://schemas.xmlsoap.org/soap/envelope/',
                  'tns': 'http://www.apis-it.hr/fin/2012/types/f73'}

            greske = root.findall('.//tns:Greska', ns)
            if greske:
                print("\n⚠️  GREŠKE:")
                tekstovi = []
                for g in greske:
                    s = g.find('tns:SifraGreske', ns)
                    p = g.find('tns:PorukaGreske', ns)
                    if s is not None and p is not None:
                        print(f"   - {s.text}: {p.text}")
                        tekstovi.append(f"{s.text}: {(p.text or '').strip()}")
                self.zadnja_greska = "; ".join(tekstovi) or "Nepoznata greška"

            jir_elem = root.find('.//tns:Jir', ns)
            if jir_elem is not None and jir_elem.text:
                print(f"\n✅ JIR: {jir_elem.text}")
                return jir_elem.text, zki
            else:
                print("\n⚠️  Nema JIR-a u odgovoru")
                if not self.zadnja_greska:
                    self.zadnja_greska = (f"Nema JIR-a u odgovoru "
                                          f"(HTTP {response.status_code})")
                return None, zki

        except Exception as e:
            print(f"❌ Iznimka: {e}")
            import traceback; traceback.print_exc()
            self.zadnja_greska = f"Slanje nije uspjelo: {e}"
            try:
                spremi_poruku(self.arhiva_mapa, godina_arhive, oznaka_arhive, "greska",
                              self.zadnja_greska, "txt")
            except NameError:
                pass
            return None, zki
