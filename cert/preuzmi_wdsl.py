
import requests
from urllib3.exceptions import InsecureRequestWarning

# Isključi SSL upozorenja
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)

# Preuzmi WSDL datoteku
url = "https://cistest.apis-it.hr:8449/FiskalizacijaServiceTest?wsdl"
cert_path = r"F:\Projekti\RacunFiskalizacijaToolbox\FiskalizacijaDemo\cert\certificate.pem"
key_path = r"F:\Projekti\RacunFiskalizacijaToolbox\FiskalizacijaDemo\cert\privatni_kljuc.pem"

print(f"📥 Preuzimam WSDL s {url}...")

try:
    response = requests.get(
        url,
        cert=(cert_path, key_path),
        verify=False,
        timeout=10
    )

    if response.status_code == 200:
        with open("FiskalizacijaServiceTest.wsdl", "wb") as f:
            f.write(response.content)
        print("✅ WSDL preuzet!")
        print(f"   Datoteka: FiskalizacijaServiceTest.wsdl")
        print(f"   Veličina: {len(response.content)} bajtova")
    else:
        print(f"❌ Greška: Status {response.status_code}")
        print(f"   Tekst: {response.text[:500]}")

except Exception as e:
    print(f"❌ Greška: {e}")