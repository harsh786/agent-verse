"""Tests for the PlatformError hierarchy and retry_with_backoff()."""

import pytest

from app.core.errors import (
    AuthorizationError,
    ExternalServiceError,
    PlatformError,
    Severity,
    ValidationError,
    retry_with_backoff,
)


class TestPlatformError:
    def test_carries_code_message_severity_and_error_id(self) -> None:
        err = PlatformError("boom", code="GENERIC", severity=Severity.HIGH)
        assert err.message == "boom"
        assert err.code == "GENERIC"
        assert err.severity is Severity.HIGH
        assert err.error_id  # auto-generated, non-empty

    def test_to_dict_is_structured_for_api_responses(self) -> None:
        err = ValidationError("bad email", details={"field": "email"})
        payload = err.to_dict()
        assert payload["error"]["code"] == "VALIDATION_ERROR"
        assert payload["error"]["message"] == "bad email"
        assert payload["error"]["details"] == {"field": "email"}
        assert "error_id" in payload["error"]

    def test_subclasses_have_correct_http_status_and_retryability(self) -> None:
        assert ValidationError("x").http_status == 422
        assert ValidationError("x").retryable is False
        assert AuthorizationError("x").http_status == 403
        assert ExternalServiceError("x").http_status == 502
        assert ExternalServiceError("x").retryable is True

    def test_preserves_cause(self) -> None:
        root = ValueError("root")
        err = ExternalServiceError("upstream failed", cause=root)
        assert err.__cause__ is root


class TestRetryWithBackoff:
    async def test_returns_immediately_on_success(self) -> None:
        sleeps: list[float] = []

        async def op() -> str:
            return "ok"

        result = await retry_with_backoff(op, sleep=_recorder(sleeps))
        assert result == "ok"
        assert sleeps == []

    async def test_retries_retryable_error_then_succeeds(self) -> None:
        sleeps: list[float] = []
        attempts = {"n": 0}

        async def op() -> str:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ExternalServiceError("transient")
            return "ok"

        result = await retry_with_backoff(
            op, base_delay=0.1, sleep=_recorder(sleeps), rng=lambda: 1.0
        )
        assert result == "ok"
        assert attempts["n"] == 3
        # full-jitter, rng()==1.0 → delay == base * 2**attempt: 0.1, 0.2
        assert sleeps == [pytest.approx(0.1), pytest.approx(0.2)]

    async def test_non_retryable_error_raised_without_retry(self) -> None:
        sleeps: list[float] = []
        attempts = {"n": 0}

        async def op() -> str:
            attempts["n"] += 1
            raise ValidationError("permanent")

        with pytest.raises(ValidationError):
            await retry_with_backoff(op, sleep=_recorder(sleeps))
        assert attempts["n"] == 1
        assert sleeps == []

    async def test_exhausts_attempts_and_reraises_last(self) -> None:
        attempts = {"n": 0}

        async def op() -> str:
            attempts["n"] += 1
            raise ExternalServiceError("always fails")

        with pytest.raises(ExternalServiceError):
            await retry_with_backoff(
                op, max_attempts=3, sleep=_recorder([]), rng=lambda: 1.0
            )
        assert attempts["n"] == 3

    async def test_delay_capped_at_max_delay(self) -> None:
        sleeps: list[float] = []
        attempts = {"n": 0}

        async def op() -> str:
            attempts["n"] += 1
            if attempts["n"] < 4:
                raise ExternalServiceError("transient")
            return "ok"

        await retry_with_backoff(
            op, base_delay=1.0, max_delay=2.5, max_attempts=5,
            sleep=_recorder(sleeps), rng=lambda: 1.0,
        )
        # 1.0, 2.0, then capped at 2.5 (would be 4.0)
        assert sleeps == [pytest.approx(1.0), pytest.approx(2.0), pytest.approx(2.5)]


def _recorder(store: list[float]):
    async def _sleep(seconds: float) -> None:
        store.append(seconds)

    return _sleep
