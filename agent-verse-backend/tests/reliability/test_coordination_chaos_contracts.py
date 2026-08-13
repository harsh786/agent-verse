from pathlib import Path

import yaml

ROOT = Path(__file__).parents[2]


def test_k6_harnesses_have_safety_cleanup_and_threshold_contracts() -> None:
    for name in ("coordination_sessions.js", "coordination_streams.js", "group_chat_ws.js"):
        text = (ROOT / "infra/loadtest" / name).read_text()
        assert "Idempotency-Key" in text
        assert "thresholds" in text
        assert "p(95)<100" in text or "rate<0.01" in text
        assert "PROGRAM13_TEST_TENANT" in text
        assert "cleanup" in text.lower()


def test_chaos_manifests_are_namespace_scoped_and_bounded() -> None:
    names = (
        "coordination-pod-kill.yaml",
        "coordination-redis-loss.yaml",
        "coordination-worker-loss.yaml",
    )
    for name in names:
        docs = list(yaml.safe_load_all((ROOT / "infra/chaos" / name).read_text()))
        assert docs
        for document in docs:
            assert document["metadata"]["namespace"] == "agentverse-test"
            assert document["spec"]["duration"]
            assert document["spec"]["mode"] in {"one", "fixed"}
