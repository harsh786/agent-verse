from __future__ import annotations

from dataclasses import dataclass

from app.recovery.failure_classifier import FailureClass


@dataclass
class RetryStrategy:
    failure_class: FailureClass
    max_retries: int
    initial_delay_seconds: float
    max_delay_seconds: float
    backoff_factor: float


_STRATEGIES: dict[FailureClass, RetryStrategy] = {
    FailureClass.RATE_LIMIT: RetryStrategy(FailureClass.RATE_LIMIT, 3, 5.0, 60.0, 2.0),
    FailureClass.TIMEOUT: RetryStrategy(FailureClass.TIMEOUT, 2, 2.0, 30.0, 1.5),
    FailureClass.TOOL_UNAVAILABLE: RetryStrategy(FailureClass.TOOL_UNAVAILABLE, 2, 1.0, 10.0, 2.0),
    FailureClass.PROVIDER_UNAVAILABLE: RetryStrategy(
        FailureClass.PROVIDER_UNAVAILABLE, 3, 10.0, 120.0, 2.0
    ),
    FailureClass.CONTEXT_GAP: RetryStrategy(FailureClass.CONTEXT_GAP, 2, 0.5, 5.0, 1.0),
}
_DEFAULT = RetryStrategy(FailureClass.UNKNOWN, 1, 1.0, 10.0, 1.5)


class RetryStrategySelector:
    def select(self, failure_class: FailureClass, attempt: int = 0) -> RetryStrategy:
        return _STRATEGIES.get(failure_class, _DEFAULT)
