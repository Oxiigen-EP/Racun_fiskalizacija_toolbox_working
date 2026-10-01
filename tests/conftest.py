import datetime
import os
import sys

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="session")
def testni_certifikat(tmp_path_factory):
    """Privremeni RSA ključ i samopotpisani certifikat (nisu pravi)."""
    mapa = tmp_path_factory.mktemp("cert")
    kljuc = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ime = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TEST"),
                     x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Test")])
    cert = (x509.CertificateBuilder()
            .subject_name(ime).issuer_name(ime)
            .public_key(kljuc.public_key()).serial_number(12345)
            .not_valid_before(datetime.datetime(2026, 1, 1))
            .not_valid_after(datetime.datetime(2035, 1, 1))
            .sign(kljuc, hashes.SHA256()))
    key_path = mapa / "test.key"
    cert_path = mapa / "test.crt"
    key_path.write_bytes(kljuc.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()))
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return {"key": str(key_path), "cert": str(cert_path),
            "private": kljuc, "public": kljuc.public_key()}


@pytest.fixture
def fisk(testni_certifikat, tmp_path):
    from fiskalizacija import Fiskalizacija
    return Fiskalizacija(testni_certifikat["cert"], testni_certifikat["key"],
                         key_password=None, demo=True,
                         arhiva_mapa=str(tmp_path))
