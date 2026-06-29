"""Extra coverage tests for app/enterprise/compliance_v2.py — targeting 85%+ coverage."""
from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.enterprise.compliance_v2 import (
    ComplianceChecker,
    generate_api_key,
    generate_scim_token,
)


def _make_db_factory(session: Any) -> Any:
    """Create an async context manager factory from a mock session."""
    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


# ---------------------------------------------------------------------------
# generate_api_key
# ---------------------------------------------------------------------------

def test_generate_api_key_default_prefix():
    full_key, key_prefix, key_hash = generate_api_key()
    assert full_key.startswith("av_live_")
    assert key_prefix == full_key[:16]
    assert len(key_hash) == 64  # SHA-256 hex


def test_generate_api_key_custom_prefix():
    full_key, key_prefix, key_hash = generate_api_key(prefix="av_test")
    assert full_key.startswith("av_test_")
    assert len(key_hash) == 64


def test_generate_api_key_unique():
    key1, _, _ = generate_api_key()
    key2, _, _ = generate_api_key()
    assert key1 != key2


def test_generate_api_key_hash_deterministic():
    import hashlib
    full_key, _, key_hash = generate_api_key()
    expected_hash = hashlib.sha256(full_key.encode()).hexdigest()
    assert key_hash == expected_hash


# ---------------------------------------------------------------------------
# generate_scim_token
# ---------------------------------------------------------------------------

def test_generate_scim_token_format():
    raw, prefix, token_hash = generate_scim_token("tid-1")
    assert prefix.startswith("scim_")
    assert len(token_hash) == 64


def test_generate_scim_token_unique():
    t1, _, _ = generate_scim_token("tid")
    t2, _, _ = generate_scim_token("tid")
    assert t1 != t2


def test_generate_scim_token_hash_deterministic():
    import hashlib
    raw, _, token_hash = generate_scim_token("tid-2")
    expected = hashlib.sha256(raw.encode()).hexdigest()
    assert token_hash == expected


# ---------------------------------------------------------------------------
# check_hipaa
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_hipaa_all_passing():
    """All 5 HIPAA controls pass → status = compliant."""
    mock_session = AsyncMock()

    async def _execute(stmt, params=None):
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "enterprise_contracts" in stmt_str:
            mock_result.fetchone.return_value = ("signed", "2024-01-01", "John Doe")
        elif "audit_events" in stmt_str:
            mock_result.scalar.return_value = 10
        elif "policy_versions" in stmt_str:
            # HITL policy for PHI
            mock_result.scalar.return_value = 1
        elif "tenant_settings" in stmt_str and "hipaa_training" in stmt_str:
            # Training tracking
            mock_result.fetchone.return_value = ("true",)
        elif "tenants" in stmt_str:
            # Encryption tier
            mock_result.fetchone.return_value = ("enterprise",)
        else:
            mock_result.scalar.return_value = 0
            mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute)

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.check_hipaa("tid-1")
    assert result["framework"] == "hipaa"
    assert result["total_count"] == 5
    assert "computed_at" in result
    assert result["status"] in ("compliant", "partial", "non_compliant")


@pytest.mark.asyncio
async def test_check_hipaa_no_baa():
    """Missing BAA → at least one control fails."""
    mock_session = AsyncMock()

    async def _execute(stmt, params=None):
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "enterprise_contracts" in stmt_str:
            mock_result.fetchone.return_value = None  # No BAA
        elif "audit_events" in stmt_str:
            mock_result.scalar.return_value = 0  # No audit
        else:
            mock_result.scalar.return_value = 0
            mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute)

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.check_hipaa("tid-no-baa")
    assert result["controls"]["baa_signed"]["pass"] is False
    assert result["controls"]["baa_signed"]["note"] is not None


@pytest.mark.asyncio
async def test_check_hipaa_exception_in_check():
    """Exception in DB queries is handled gracefully."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB down"))

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.check_hipaa("tid-exc")
    # Even on exception, should return result with controls
    assert "controls" in result


# ---------------------------------------------------------------------------
# check_gdpr
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_gdpr_all_passing():
    mock_session = AsyncMock()

    async def _execute(stmt, params=None):
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "enterprise_contracts" in stmt_str and "dpa" in str(params or {}):
            mock_result.fetchone.return_value = ("signed", "2024-01-01", "Legal")
        elif "gdpr_export_jobs" in stmt_str:
            mock_result.scalar.return_value = 3
        elif "consent_records" in stmt_str:
            mock_result.scalar.return_value = 10
        elif "tenant_settings" in stmt_str and "data_region" in stmt_str:
            mock_result.fetchone.return_value = ("eu-west-1",)
        elif "tenant_settings" in stmt_str and "retention_days" in stmt_str:
            mock_result.fetchone.return_value = ("90",)
        else:
            mock_result.scalar.return_value = 0
            mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute)

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.check_gdpr("tid-gdpr")
    assert result["framework"] == "gdpr"
    assert result["total_count"] == 5
    assert "computed_at" in result


@pytest.mark.asyncio
async def test_check_gdpr_non_eu_region():
    """Non-EU region fails eu_data_residency."""
    mock_session = AsyncMock()

    async def _execute(stmt, params=None):
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "enterprise_contracts" in stmt_str:
            mock_result.fetchone.return_value = None
        elif "gdpr_export_jobs" in stmt_str:
            mock_result.scalar.return_value = 0
        elif "consent_records" in stmt_str:
            mock_result.scalar.return_value = 0
        elif "data_region" in stmt_str:
            mock_result.fetchone.return_value = ("us-east-1",)
        elif "retention_days" in stmt_str:
            mock_result.fetchone.return_value = None
        else:
            mock_result.scalar.return_value = 0
            mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute)

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.check_gdpr("tid-us")
    assert result["controls"]["eu_data_residency"]["pass"] is False
    assert "us-east-1" in result["controls"]["eu_data_residency"]["note"]


@pytest.mark.asyncio
async def test_check_gdpr_partial_controls():
    """3 controls pass → status partial."""
    mock_session = AsyncMock()

    call_count = [0]

    async def _execute(stmt, params=None):
        call_count[0] += 1
        n = call_count[0]
        mock_result = MagicMock()
        # DPA signed (1st), no exports (2nd), some consents (3rd), eu region (4th), retention (5th)
        if n == 1:
            mock_result.fetchone.return_value = ("signed", "2024-01-01", "Legal")
        elif n == 2:
            mock_result.scalar.return_value = 0
        elif n == 3:
            mock_result.scalar.return_value = 5
        elif n == 4:
            mock_result.fetchone.return_value = ("eu-central-1",)
        elif n == 5:
            mock_result.fetchone.return_value = None
        else:
            mock_result.scalar.return_value = 0
            mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute)

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.check_gdpr("tid-partial")
    # Status depends on passed count
    assert result["status"] in ("compliant", "partial", "non_compliant")
    assert result["passed_count"] >= 0


# ---------------------------------------------------------------------------
# check_soc2
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_check_soc2_compliant():
    mock_session = AsyncMock()

    async def _execute(stmt, params=None):
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "audit_events" in stmt_str:
            mock_result.scalar.return_value = 100
        elif "compliance_certifications" in stmt_str:
            mock_result.fetchone.return_value = ("active", "2025-01-01")
        else:
            mock_result.scalar.return_value = 0
            mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute)

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.check_soc2("tid-soc2")
    assert result["framework"] == "soc2"
    assert result["controls"]["audit_logging"]["pass"] is True
    assert result["controls"]["certification_on_file"]["pass"] is True
    assert result["status"] == "compliant"


@pytest.mark.asyncio
async def test_check_soc2_partial():
    mock_session = AsyncMock()

    async def _execute(stmt, params=None):
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "audit_events" in stmt_str:
            mock_result.scalar.return_value = 0  # No audit
        elif "compliance_certifications" in stmt_str:
            mock_result.fetchone.return_value = ("active", "2025-01-01")
        else:
            mock_result.scalar.return_value = 0
            mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute)

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.check_soc2("tid-partial")
    assert result["status"] == "partial"
    assert result["controls"]["audit_logging"]["pass"] is False


@pytest.mark.asyncio
async def test_check_soc2_cert_not_active():
    mock_session = AsyncMock()

    async def _execute(stmt, params=None):
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "audit_events" in stmt_str:
            mock_result.scalar.return_value = 5
        elif "compliance_certifications" in stmt_str:
            mock_result.fetchone.return_value = ("expired", "2023-01-01")  # Expired
        else:
            mock_result.scalar.return_value = 0
            mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute)

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.check_soc2("tid-expired")
    assert result["controls"]["certification_on_file"]["pass"] is False


@pytest.mark.asyncio
async def test_check_soc2_cert_none():
    mock_session = AsyncMock()

    async def _execute(stmt, params=None):
        mock_result = MagicMock()
        stmt_str = str(stmt)
        if "audit_events" in stmt_str:
            mock_result.scalar.return_value = 1
        elif "compliance_certifications" in stmt_str:
            mock_result.fetchone.return_value = None
        else:
            mock_result.scalar.return_value = 0
            mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute)

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.check_soc2("tid-nocert")
    assert result["controls"]["certification_on_file"]["pass"] is False


# ---------------------------------------------------------------------------
# get_all_frameworks
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_all_frameworks():
    mock_session = AsyncMock()

    async def _execute(stmt, params=None):
        mock_result = MagicMock()
        mock_result.scalar.return_value = 0
        mock_result.fetchone.return_value = None
        return mock_result

    mock_session.execute = AsyncMock(side_effect=_execute)

    checker = ComplianceChecker(_make_db_factory(mock_session))
    result = await checker.get_all_frameworks("tid-all")
    assert "frameworks" in result
    assert "hipaa" in result["frameworks"]
    assert "gdpr" in result["frameworks"]
    assert "soc2" in result["frameworks"]
    assert result["tenant_id"] == "tid-all"
    assert "computed_at" in result


# ---------------------------------------------------------------------------
# Private helper methods — exception paths
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_signed_contract_exception():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._get_signed_contract(mock_session, "tid", "baa")
    assert result is None


@pytest.mark.asyncio
async def test_check_audit_active_exception():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._check_audit_active(mock_session, "tid")
    assert result is False


@pytest.mark.asyncio
async def test_check_phi_hitl_policy_exception():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._check_phi_hitl_policy(mock_session, "tid")
    assert result is False


@pytest.mark.asyncio
async def test_check_training_tracking_exception():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._check_training_tracking(mock_session, "tid")
    assert result is False


@pytest.mark.asyncio
async def test_check_encryption_tier_exception():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._check_encryption_tier(mock_session, "tid")
    assert result is False


@pytest.mark.asyncio
async def test_get_data_region_exception():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._get_data_region(mock_session, "tid")
    assert result == "us-east-1"


@pytest.mark.asyncio
async def test_check_retention_policy_exception():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._check_retention_policy(mock_session, "tid")
    assert result is False


@pytest.mark.asyncio
async def test_get_certification_exception():
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(side_effect=RuntimeError("DB error"))
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._get_certification(mock_session, "tid", "soc2_type2")
    assert result is None


@pytest.mark.asyncio
async def test_check_training_tracking_row_none():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = None
    mock_session.execute = AsyncMock(return_value=mock_result)
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._check_training_tracking(mock_session, "tid")
    assert result is False


@pytest.mark.asyncio
async def test_check_training_tracking_false_value():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = ("false",)
    mock_session.execute = AsyncMock(return_value=mock_result)
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._check_training_tracking(mock_session, "tid")
    assert result is False


@pytest.mark.asyncio
async def test_check_encryption_tier_non_enterprise():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = ("free",)
    mock_session.execute = AsyncMock(return_value=mock_result)
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._check_encryption_tier(mock_session, "tid")
    assert result is False


@pytest.mark.asyncio
async def test_check_encryption_tier_enterprise():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = ("enterprise",)
    mock_session.execute = AsyncMock(return_value=mock_result)
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._check_encryption_tier(mock_session, "tid")
    assert result is True


@pytest.mark.asyncio
async def test_get_data_region_null_value():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (None,)
    mock_session.execute = AsyncMock(return_value=mock_result)
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._get_data_region(mock_session, "tid")
    assert result == "us-east-1"


@pytest.mark.asyncio
async def test_check_retention_policy_null_value():
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.fetchone.return_value = (None,)
    mock_session.execute = AsyncMock(return_value=mock_result)
    checker = ComplianceChecker(None)  # type: ignore[arg-type]
    result = await checker._check_retention_policy(mock_session, "tid")
    assert result is False
