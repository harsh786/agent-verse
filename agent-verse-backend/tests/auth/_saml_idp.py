"""A local test IdP: a self-signed certificate and signed SAML 2.0 Responses.

Assertions are signed with python3-saml's own ``OneLogin_Saml2_Utils.add_sign``
(xmlsec, RSA-SHA256), exactly as a real IdP signs them, so the SP code under test
runs the full strict validation path: signature, Destination, Audience,
Recipient, Issuer and validity window.
"""

from __future__ import annotations

import base64
import datetime as dt
import uuid
from dataclasses import dataclass
from functools import lru_cache

IDP_ENTITY = "https://idp.test/metadata"
IDP_SSO = "https://idp.test/sso"


@dataclass(frozen=True)
class TestIdP:
    cert_pem: str
    key_pem: str

    @property
    def cert_b64(self) -> str:
        """The certificate body as stored in saml_configs.idp_cert."""
        return "".join(
            line for line in self.cert_pem.splitlines() if line and "CERTIFICATE" not in line
        )


@lru_cache(maxsize=2)
def make_idp(label: str = "idp") -> TestIdP:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"{label}.test")])
    now = dt.datetime.now(dt.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    return TestIdP(
        cert_pem=cert.public_bytes(serialization.Encoding.PEM).decode(),
        key_pem=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ).decode(),
    )


def _ts(t: dt.datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def response_xml(
    *,
    acs_url: str,
    sp_entity: str,
    name_id: str,
    attributes: dict[str, list[str]] | None = None,
    assertion_id: str | None = None,
    issuer: str = IDP_ENTITY,
    session_index: str = "sess-1",
) -> str:
    now = dt.datetime.now(dt.UTC)
    later = now + dt.timedelta(minutes=5)
    aid = assertion_id or f"_a{uuid.uuid4().hex}"
    attrs = "".join(
        f'<saml:Attribute Name="{k}">'
        + "".join(f"<saml:AttributeValue>{v}</saml:AttributeValue>" for v in vals)
        + "</saml:Attribute>"
        for k, vals in (attributes or {}).items()
    )
    return (
        '<samlp:Response xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        'xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion" '
        f'ID="_r{uuid.uuid4().hex}" Version="2.0" IssueInstant="{_ts(now)}" '
        f'Destination="{acs_url}">'
        f"<saml:Issuer>{issuer}</saml:Issuer>"
        '<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/>'
        "</samlp:Status>"
        f'<saml:Assertion ID="{aid}" Version="2.0" IssueInstant="{_ts(now)}">'
        f"<saml:Issuer>{issuer}</saml:Issuer>"
        "<saml:Subject>"
        '<saml:NameID Format="urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress">'
        f"{name_id}</saml:NameID>"
        '<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
        f'<saml:SubjectConfirmationData NotOnOrAfter="{_ts(later)}" Recipient="{acs_url}"/>'
        "</saml:SubjectConfirmation></saml:Subject>"
        f'<saml:Conditions NotBefore="{_ts(now - dt.timedelta(minutes=1))}" '
        f'NotOnOrAfter="{_ts(later)}">'
        f"<saml:AudienceRestriction><saml:Audience>{sp_entity}</saml:Audience>"
        "</saml:AudienceRestriction></saml:Conditions>"
        f'<saml:AuthnStatement AuthnInstant="{_ts(now)}" SessionIndex="{session_index}">'
        "<saml:AuthnContext><saml:AuthnContextClassRef>"
        "urn:oasis:names:tc:SAML:2.0:ac:classes:Password"
        "</saml:AuthnContextClassRef></saml:AuthnContext></saml:AuthnStatement>"
        + (f"<saml:AttributeStatement>{attrs}</saml:AttributeStatement>" if attrs else "")
        + "</saml:Assertion></samlp:Response>"
    )


def sign_assertion(xml: str, idp: TestIdP) -> str:
    """Sign the Assertion element (enveloped signature), as IdPs do."""
    from lxml import etree
    from onelogin.saml2.utils import OneLogin_Saml2_Utils

    root = etree.fromstring(xml.encode())
    ns = {"saml": "urn:oasis:names:tc:SAML:2.0:assertion"}
    assertion = root.find("saml:Assertion", ns)
    signed = OneLogin_Saml2_Utils.add_sign(etree.tostring(assertion), idp.key_pem, idp.cert_pem)
    root.replace(assertion, etree.fromstring(signed))
    return etree.tostring(root).decode()


def encode(xml: str) -> str:
    return base64.b64encode(xml.encode()).decode()


def signed_response(idp: TestIdP | None = None, **kwargs: object) -> str:
    """Base64 SAMLResponse with a signed assertion, ready to POST to the ACS."""
    return encode(sign_assertion(response_xml(**kwargs), idp or make_idp()))  # type: ignore[arg-type]
