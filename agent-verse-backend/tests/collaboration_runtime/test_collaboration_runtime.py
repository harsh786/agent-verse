from __future__ import annotations

from app.collaboration_runtime.clarification import ClarificationEngine, ClarificationRequest
from app.collaboration_runtime.human_decision_trace import HumanDecisionTrace
from app.collaboration_runtime.missing_input_request import MissingInputEngine
from app.collaboration_runtime.preference_capture import PreferenceCapture, PreferenceOption


def test_clarification_request_created():
    engine = ClarificationEngine()
    req = engine.create_clarification("g1", "Which system?", ["Jira", "GitHub"])
    assert isinstance(req, ClarificationRequest)
    assert req.goal_id == "g1"
    assert len(req.options) == 2
    assert req.requires_pause is True


def test_missing_input_request():
    engine = MissingInputEngine()
    req = engine.create("g1", "GitHub API token", "Tool requires auth")
    assert req.missing_item == "GitHub API token"


def test_preference_capture():
    capture = PreferenceCapture()
    session = capture.create(
        "g1",
        "How thorough?",
        [PreferenceOption("fast", "Fast"), PreferenceOption("thorough", "Thorough")],
    )
    assert len(session.options) == 2


def test_human_decision_trace_records():
    trace = HumanDecisionTrace(goal_id="g1")
    trace.record("clarification", "Which system?", "Jira", 45.0)
    assert len(trace.decisions) == 1
    assert trace.decisions[0]["human_response"] == "Jira"


def test_human_decision_trace_serializable():
    import json
    trace = HumanDecisionTrace(goal_id="g1")
    trace.record("approval", "Proceed?", "yes", 120.0)
    json.dumps(trace.to_dict())
