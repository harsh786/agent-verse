"""RPA automation primitives and CI-safe local runner."""

from app.rpa.artifacts import RPAArtifact, RPAArtifactStore
from app.rpa.executor import RPAExecutor, RPAResult
from app.rpa.runner import LocalRPARunner, RPARunner, execute_rpa_tool
from app.rpa.session import RPAManagedSession, RPASession, RPASessionStore
from app.rpa.tools import RPA_TOOLS, classify_rpa_tool_risk

__all__ = [
    "RPA_TOOLS",
    "LocalRPARunner",
    "RPAArtifact",
    "RPAArtifactStore",
    "RPAExecutor",
    "RPAManagedSession",
    "RPAResult",
    "RPARunner",
    "RPASession",
    "RPASessionStore",
    "classify_rpa_tool_risk",
    "execute_rpa_tool",
]
