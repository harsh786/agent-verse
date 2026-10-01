"""SAML-02: python3-saml ships with the backend and really validates signatures.

The validation library was an optional extra that neither the dev env nor the
Docker image installed, so signature validation could never run (every SAML
login was a 501). It is now a core dependency; these tests drive the strict
python3-saml path with a local fixture IdP: a signed assertion is accepted, a
tampered, unsigned or foreign-signed one is rejected.
"""

from __future__ import annotations

import base64
import tomllib
from pathlib import Path

import fakeredis.aioredis
import pytest

from app.auth import saml_provider
from app.auth.saml_provider import SAMLProvider
from tests.auth._saml_idp import (
    IDP_ENTITY,
    IDP_SSO,
    encode,
    make_idp,
    response_xml,
    sign_assertion,
    signed_response,
)

BACKEND = Path(__file__).resolve().parents[2]
ACS = "https://sp.test/enterprise/saml/acs/t1"
SP = "https://sp.test/saml/metadata"


def _provider(cert: str | None = None) -> SAMLProvider:
    return SAMLProvider(
        tenant_id="t1",
        idp_entity_id=IDP_ENTITY,
        idp_sso_url=IDP_SSO,
        idp_cert=cert or make_idp().cert_b64,
        sp_entity_id=SP,
        acs_url=ACS,
        redis=fakeredis.aioredis.FakeRedis(),
    )


def test_python3_saml_is_a_core_dependency() -> None:
    project = tomllib.loads((BACKEND / "pyproject.toml").read_text())["project"]
    assert any(d.startswith("python3-saml") for d in project["dependencies"])
    assert saml_provider.SAML_AVAILABLE is True


def test_docker_image_installs_the_xmlsec_runtime() -> None:
    dockerfile = (BACKEND / "Dockerfile").read_text()
    assert "libxmlsec1-openssl" in dockerfile


async def test_signed_assertion_is_accepted() -> None:
    resp = signed_response(
        acs_url=ACS,
        sp_entity=SP,
        name_id="alice@corp.test",
        attributes={"email": ["alice@corp.test"], "firstName": ["Alice"]},
    )
    ident = await _provider().process_acs(resp)
    assert ident.email == "alice@corp.test"
    assert ident.first_name == "Alice"


async def test_tampered_assertion_is_rejected() -> None:
    signed = sign_assertion(
        response_xml(acs_url=ACS, sp_entity=SP, name_id="alice@corp.test"), make_idp()
    )
    forged = signed.replace("alice@corp.test", "admin@corp.test")
    with pytest.raises(ValueError):
        await _provider().process_acs(encode(forged))


async def test_unsigned_assertion_is_rejected() -> None:
    unsigned = response_xml(acs_url=ACS, sp_entity=SP, name_id="alice@corp.test")
    with pytest.raises(ValueError):
        await _provider().process_acs(encode(unsigned))


async def test_assertion_signed_by_another_key_is_rejected() -> None:
    rogue = make_idp("rogue")
    resp = signed_response(rogue, acs_url=ACS, sp_entity=SP, name_id="alice@corp.test")
    with pytest.raises(ValueError):
        await _provider().process_acs(resp)


async def test_wrong_audience_is_rejected() -> None:
    resp = signed_response(acs_url=ACS, sp_entity="https://other-sp.test", name_id="a@corp.test")
    with pytest.raises(ValueError):
        await _provider().process_acs(resp)


def test_fixture_response_is_base64_xml() -> None:
    raw = base64.b64decode(signed_response(acs_url=ACS, sp_entity=SP, name_id="a@b.test"))
    assert raw.startswith(b"<samlp:Response")
