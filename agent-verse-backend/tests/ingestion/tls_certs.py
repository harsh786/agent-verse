"""Throwaway PKI for TLS / mTLS connector tests against real containers.

Generates a CA, a server certificate valid for ``localhost`` / ``127.0.0.1`` and
a client certificate, all signed by the CA, as PEM strings.
"""

from __future__ import annotations

import datetime
import ipaddress
from dataclasses import dataclass

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


@dataclass(frozen=True)
class Pki:
    ca_cert: str
    server_cert: str
    server_key: str
    client_cert: str
    client_key: str


def _key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def _pem_key(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()


def _pem_cert(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


def make_pki(server_names: tuple[str, ...] = ("localhost",)) -> Pki:
    now = datetime.datetime.now(datetime.UTC)
    ca_key = _key()
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "agentverse-test-ca")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )

    def _leaf(cn: str, usage: x509.ObjectIdentifier, sans: list[x509.GeneralName]) -> tuple[
        x509.Certificate, ec.EllipticCurvePrivateKey
    ]:
        key = _key()
        builder = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
            .issuer_name(ca_name)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
                critical=False,
            )
        )
        if sans:
            builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
        return builder.sign(ca_key, hashes.SHA256()), key

    sans: list[x509.GeneralName] = [x509.DNSName(n) for n in server_names]
    sans.append(x509.IPAddress(ipaddress.ip_address("127.0.0.1")))
    server_cert, server_key = _leaf(server_names[0], ExtendedKeyUsageOID.SERVER_AUTH, sans)
    client_cert, client_key = _leaf("agentverse-client", ExtendedKeyUsageOID.CLIENT_AUTH, [])
    return Pki(
        ca_cert=_pem_cert(ca_cert),
        server_cert=_pem_cert(server_cert),
        server_key=_pem_key(server_key),
        client_cert=_pem_cert(client_cert),
        client_key=_pem_key(client_key),
    )
