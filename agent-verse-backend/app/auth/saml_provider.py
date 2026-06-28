"""SAML 2.0 provider — enterprise SSO integration.

Supports python3-saml (onelogin/python3-saml) when installed.
Falls back to a minimal stub implementation when the library is absent,
so the module can always be imported in environments without python3-saml.

Amendment 8.4: SAML replay protection via Redis assertion-ID cache.

Flow:
  1. GET /api/enterprise/saml/login → redirect to IdP SSO URL
  2. IdP authenticates → POST to /api/enterprise/saml/acs
  3. ACS validates assertion, checks replay, extracts attributes,
     JIT-provisions user, returns session JWT
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from typing import Any

from app.observability.logging import get_logger

logger = get_logger(__name__)

try:
    from onelogin.saml2.auth import OneLogin_Saml2_Auth  # type: ignore[import]
    from onelogin.saml2.metadata import OneLogin_Saml2_Metadata  # type: ignore[import]

    SAML_AVAILABLE = True
except ImportError:
    SAML_AVAILABLE = False
    logger.info("python3_saml_not_installed; SAML SSO unavailable until installed")


@dataclass
class SAMLIdentity:
    """User identity extracted from a SAML assertion."""

    email: str
    name_id: str
    first_name: str | None = None
    last_name: str | None = None
    department: str | None = None
    attributes: dict[str, list[str]] = field(default_factory=dict)


class SAMLProvider:
    """
    SAML 2.0 single sign-on provider for a single tenant.

    Constructed per-tenant using the IdP configuration stored in saml_configs.
    """

    def __init__(
        self,
        tenant_id: str,
        idp_entity_id: str,
        idp_sso_url: str,
        idp_cert: str,
        sp_entity_id: str,
        acs_url: str,
        attribute_mapping: dict[str, str] | None = None,
        name_id_format: str = (
            "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress"
        ),
        jit_provisioning: bool = True,
        redis: Any = None,
    ) -> None:
        self._tenant_id = tenant_id
        self._idp_entity_id = idp_entity_id
        self._idp_sso_url = idp_sso_url
        self._idp_cert = idp_cert
        self._sp_entity_id = sp_entity_id
        self._acs_url = acs_url
        self._attribute_mapping = attribute_mapping or {}
        self._name_id_format = name_id_format
        self._jit_provisioning = jit_provisioning
        self._redis = redis

    # ── Public API ────────────────────────────────────────────────────────

    def initiate_login(self, relay_state: str = "") -> str:
        """Return the redirect URL to the IdP SSO endpoint."""
        if not SAML_AVAILABLE:
            return self._idp_sso_url
        try:
            settings = self._build_saml_settings()
            request_data = self._build_empty_request_data()
            auth = OneLogin_Saml2_Auth(request_data, old_settings=settings)
            return auth.login(return_to=relay_state or None)
        except Exception as exc:
            logger.warning("saml_login_initiation_failed", error=str(exc))
            return self._idp_sso_url

    async def process_acs(self, saml_response: str) -> SAMLIdentity:
        """
        Validate SAML assertion and extract user identity.

        Amendment 8.4: Checks assertion replay via Redis.
        Raises ValueError on authentication failure or replay.
        """
        if not SAML_AVAILABLE:
            raise RuntimeError(
                "python3-saml is not installed. "
                "Run: pip install python3-saml"
            )

        settings = self._build_saml_settings()
        # python3-saml version-compatible https flag
        https_val: Any = True if sys.version_info >= (3, 11) else "on"
        request_data = {
            "http_host": self._acs_url.split("/")[2] if "/" in self._acs_url else "localhost",
            "script_name": "/api/enterprise/saml/acs",
            "post_data": {"SAMLResponse": saml_response},
            "https": https_val,
        }
        auth = OneLogin_Saml2_Auth(request_data, old_settings=settings)
        auth.process_response()

        if not auth.is_authenticated():
            errors = auth.get_errors()
            raise ValueError(f"SAML authentication failed: {errors}")

        # Replay protection (Amendment 8.4)
        session_index = auth.get_session_index() or ""
        name_id = auth.get_nameid() or ""
        assertion_id = f"{name_id}:{session_index}"
        if self._redis is not None and assertion_id and await self._check_saml_replay(assertion_id):
            raise ValueError("SAML assertion replay detected")

        attrs = auth.get_attributes() or {}
        mapping = self._attribute_mapping

        def _first(key_alias: str, fallback_key: str) -> str | None:
            mapped_key = mapping.get(key_alias, fallback_key)
            vals = attrs.get(mapped_key, attrs.get(fallback_key, []))
            return vals[0] if vals else None

        return SAMLIdentity(
            email=_first("email", "email") or name_id,
            name_id=name_id,
            first_name=_first("first_name", "firstName"),
            last_name=_first("last_name", "lastName"),
            department=_first("department", "department"),
            attributes=attrs,
        )

    def get_sp_metadata(self) -> str:
        """Return SP metadata XML for IdP registration."""
        if SAML_AVAILABLE:
            try:
                settings = self._build_saml_settings()
                metadata = OneLogin_Saml2_Metadata.builder(settings["sp"])
                return metadata
            except Exception as exc:
                logger.warning("saml_metadata_build_failed", error=str(exc))

        # Minimal fallback XML
        return (
            '<?xml version="1.0"?>\n'
            '<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"\n'
            f'    entityID="{self._sp_entity_id}">\n'
            '  <md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true"\n'
            '      protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">\n'
            '    <md:AssertionConsumerService'
            ' Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"\n'
            f'        Location="{self._acs_url}" index="1"/>\n'
            "  </md:SPSSODescriptor>\n"
            "</md:EntityDescriptor>"
        )

    # ── Private helpers ──────────────────────────────────────────────────

    async def _check_saml_replay(self, assertion_id: str) -> bool:
        """
        Return True if assertion_id was already seen (replay).

        Amendment 8.4: Redis key expires after 1 hour (SAML assertion validity window).
        """
        key = f"saml_assertion:{self._tenant_id}:{assertion_id}"
        try:
            is_replay = bool(await self._redis.exists(key))
            if not is_replay:
                await self._redis.setex(key, 3600, "used")
            return is_replay
        except Exception as exc:
            logger.warning("saml_replay_check_failed", error=str(exc))
            return False  # fail-open on Redis error to not block SSO

    def _build_saml_settings(self) -> dict[str, Any]:
        return {
            "sp": {
                "entityId": self._sp_entity_id,
                "assertionConsumerService": {
                    "url": self._acs_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
                "NameIDFormat": self._name_id_format,
            },
            "idp": {
                "entityId": self._idp_entity_id,
                "singleSignOnService": {
                    "url": self._idp_sso_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "x509cert": self._idp_cert,
            },
            "security": {
                "wantAssertionsSigned": True,
                "requestedAuthnContext": False,
            },
            "strict": False,
        }

    @staticmethod
    def _build_empty_request_data() -> dict[str, Any]:
        https_val: Any = True if sys.version_info >= (3, 11) else "on"
        return {
            "http_host": "localhost",
            "script_name": "/api/enterprise/saml/login",
            "post_data": {},
            "https": https_val,
        }


# ---------------------------------------------------------------------------
# Factory helper: build SAMLProvider from DB config row
# ---------------------------------------------------------------------------


def build_saml_provider_from_config(
    tenant_id: str,
    config_row: Any,  # DB row with idp_entity_id, idp_sso_url, etc.
    base_url: str,
    redis: Any = None,
) -> SAMLProvider:
    """Construct a SAMLProvider from a saml_configs DB row."""
    acs_url = f"{base_url.rstrip('/')}/api/enterprise/saml/acs"
    return SAMLProvider(
        tenant_id=tenant_id,
        idp_entity_id=config_row.idp_entity_id,
        idp_sso_url=config_row.idp_sso_url,
        idp_cert=config_row.idp_cert,
        sp_entity_id=config_row.sp_entity_id,
        acs_url=acs_url,
        attribute_mapping=config_row.attribute_mapping or {},
        name_id_format=getattr(
            config_row,
            "name_id_format",
            "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
        ),
        jit_provisioning=getattr(config_row, "jit_provisioning", True),
        redis=redis,
    )
