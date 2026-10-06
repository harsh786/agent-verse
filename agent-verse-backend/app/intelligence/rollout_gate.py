"""The fully-autonomous rollout gate: a golden-suite run that exercised THIS agent.

MEM-52: golden goals used to run without an agent (auto-routed) and runs
recorded no agent, agent config or dataset version, so the gate promoted an
agent on the suite's latest run whoever it exercised, however small the suite,
and however the agent had changed since. The gate now requires the latest
completed run of the suite that

* ran against this agent (``agent_id``),
* with the agent's CURRENT behaviour config (:func:`agent_config_hash`),
* on the suite's CURRENT golden dataset version,
* over at least ``rollout_min_suite_size`` tasks (Settings),

to reach ``rollout_min_pass_rate`` (Settings). Any store error propagates:
callers fail closed.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

# The agent fields that decide how it behaves. A change to any of them is a
# different agent as far as the gate is concerned. (Name, autonomy mode, the
# attached suite and triggers do not change how a goal is executed.)
AGENT_CONFIG_FIELDS: tuple[str, ...] = (
    "system_prompt",
    "goal_template",
    "model_override",
    "connector_ids",
    "policy_ids",
    "allowed_collection_ids",
    "max_iterations",
    "timeout_seconds",
    "pattern_flags",
)
_SET_FIELDS = frozenset({"connector_ids", "policy_ids", "allowed_collection_ids"})

# Library default; the enforced values come from Settings (rollout_min_*).
ROLLOUT_MIN_PASS_RATE = 0.8


def agent_config_hash(agent: dict[str, Any]) -> str:
    """Stable sha256 of the agent's behaviour config."""
    material: dict[str, Any] = {}
    for key in AGENT_CONFIG_FIELDS:
        value = agent.get(key)
        if key in _SET_FIELDS:
            value = sorted(str(v) for v in (value or []))
        elif key == "pattern_flags":
            value = {k: bool(v) for k, v in sorted((value or {}).items()) if v}
        elif key in {"max_iterations", "timeout_seconds"}:
            value = int(value) if value is not None else None
        else:
            value = str(value or "")
        material[key] = value
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def behaviour_config_changed(current: dict[str, Any], proposed: dict[str, Any]) -> bool:
    """Would writing *proposed* over *current* change the config the gate pins?"""
    return agent_config_hash(current) != agent_config_hash(proposed)


def eval_gate_enabled() -> bool:
    """Owner decision: is the fully-autonomous eval rollout gate enforced?"""
    from app.core.config import get_settings

    enabled = bool(getattr(get_settings(), "fully_autonomous_eval_gate_enabled", True))
    if not enabled:
        from app.observability.logging import get_logger

        get_logger(__name__).warning("fully_autonomous_eval_gate_disabled_by_setting")
    return enabled


def gate_settings() -> tuple[float, int]:
    """``(min_pass_rate, min_suite_size)`` from Settings."""
    from app.core.config import get_settings

    s = get_settings()
    return (
        float(getattr(s, "rollout_min_pass_rate", ROLLOUT_MIN_PASS_RATE)),
        int(getattr(s, "rollout_min_suite_size", 5)),
    )


async def check_agent_rollout_gate(
    *,
    agent_id: str | None,
    eval_suite_id: str | None,
    tenant_id: str,
    db: Any,
    agent_config: dict[str, Any] | None = None,
    min_pass_rate: float | None = None,
    min_suite_size: int | None = None,
) -> dict[str, Any]:
    """Does a current golden-suite run vouch for this agent's config?

    ``agent_config`` is the configuration to vouch for (the agent's current
    record, or the record a PUT is about to write); its hash must match the run's.
    """
    from app.intelligence.eval_suite_store import EvalSuiteStore

    default_rate, default_size = gate_settings()
    rate = default_rate if min_pass_rate is None else float(min_pass_rate)
    size = default_size if min_suite_size is None else int(min_suite_size)
    config_hash = agent_config_hash(agent_config) if agent_config is not None else None
    report: dict[str, Any] = {
        "agent_id": agent_id,
        "eval_suite_id": eval_suite_id or None,
        "min_pass_rate_required": rate,
        "min_suite_size": size,
        "agent_config_hash": config_hash,
        "gate_passed": False,
        "run_id": None,
        "run_at": None,
        "run_count": 0,
        "total_tasks": 0,
        "passed_tasks": 0,
        "pass_rate": 0.0,
        "dataset_version": None,
        "current_dataset_version": None,
        "run_agent_config_hash": None,
    }
    if not eval_suite_id:
        report["reason"] = "No eval suite is attached to this agent."
        return report
    if not agent_id or config_hash is None:
        report["reason"] = (
            "The gate needs an existing agent: create it bounded-autonomous, run the "
            "eval suite against it, then promote it."
        )
        return report
    store = EvalSuiteStore(db, tenant_id)
    meta = await store.get_meta(eval_suite_id)
    if meta is None:
        report["reason"] = f"Eval suite {eval_suite_id} not found."
        return report
    current_version = int(meta["dataset_version"])
    report["current_dataset_version"] = current_version
    runs = await store.list_runs(eval_suite_id, agent_id=agent_id)
    completed = [r for r in runs if r.get("status") == "completed"]
    report["run_count"] = len(completed)
    if not completed:
        report["reason"] = (
            f"Eval suite {eval_suite_id} has no completed run against agent {agent_id}. "
            "Run it against this agent before enabling fully-autonomous mode."
        )
        return report
    latest = completed[0]  # newest first
    total = int(latest.get("total") or 0)
    pass_rate = float(latest.get("pass_rate") or 0.0)
    report.update(
        run_id=latest.get("run_id"),
        run_at=latest.get("run_at"),
        total_tasks=total,
        passed_tasks=int(latest.get("passed") or 0),
        pass_rate=pass_rate,
        dataset_version=latest.get("dataset_version"),
        run_agent_config_hash=latest.get("agent_config_hash"),
    )
    if latest.get("agent_config_hash") != config_hash:
        report["reason"] = (
            "The agent's configuration changed since its latest run of eval suite "
            f"{eval_suite_id}; run the suite again against the current configuration."
        )
        return report
    if latest.get("dataset_version") != current_version:
        report["reason"] = (
            f"The golden dataset of eval suite {eval_suite_id} changed (now v{current_version}, "
            f"the latest run used v{latest.get('dataset_version')}); run it again."
        )
        return report
    if total < size:
        report["reason"] = (
            f"The latest run had {total} task(s); the gate requires at least {size}."
        )
        return report
    report["gate_passed"] = pass_rate >= rate
    verdict = "meets" if report["gate_passed"] else "is below"
    report["reason"] = (
        f"Latest run of eval suite {eval_suite_id} against agent {agent_id} "
        f"(dataset v{current_version}): pass rate {pass_rate:.1%} {verdict} the "
        f"{rate:.1%} threshold."
    )
    return report
