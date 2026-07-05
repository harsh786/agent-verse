"""World-class tests for the Agent Identity Layer."""
from __future__ import annotations

import pytest
from app.auth.agent_credentials import AgentCredentialStore, is_agent_key, generate_agent_api_key
from app.auth.goal_tokens import mint_goal_token, verify_goal_token
from app.auth.agent_manifest import build_manifest, sign_manifest, verify_manifest
from app.auth.delegation import DelegationChain, DelegationLink


class TestAgentCredentials:
    def test_generates_agent_prefixed_key(self):
        raw, _ = generate_agent_api_key("agent-abc123")
        assert is_agent_key(raw)
        assert raw.startswith("av_agent_")

    def test_is_agent_key_false_for_tenant_key(self):
        assert not is_agent_key("av_free_somekey")

    def test_create_and_resolve_key(self):
        store = AgentCredentialStore()
        result = store.create_key(
            agent_id="a1", tenant_id="t1", name="test-key",
            allowed_tools=["jira_*", "slack_send_message"],
        )
        raw_key = result["raw_key"]
        resolved = store.resolve(raw_key)
        assert resolved is not None
        assert resolved["agent_id"] == "a1"

    def test_revoked_key_not_resolvable(self):
        store = AgentCredentialStore()
        result = store.create_key(agent_id="a1", tenant_id="t1", name="key")
        store.revoke(result["key_id"], "a1")
        assert store.resolve(result["raw_key"]) is None

    def test_expired_key_not_resolvable(self):
        import time
        store = AgentCredentialStore()
        result = store.create_key(agent_id="a1", tenant_id="t1", name="key", expires_at=time.time() - 1)
        assert store.resolve(result["raw_key"]) is None

    def test_tool_allowlist_blocks_unlisted_tool(self):
        store = AgentCredentialStore()
        result = store.create_key(agent_id="a1", tenant_id="t1", name="key", allowed_tools=["jira_*"])
        raw = result["raw_key"]
        record = store.resolve(raw)
        assert store.check_tool_allowed(record, "jira_search_issues") is True
        assert store.check_tool_allowed(record, "github_create_pr") is False

    def test_tool_denylist_blocks_explicitly_denied(self):
        store = AgentCredentialStore()
        result = store.create_key(agent_id="a1", tenant_id="t1", name="key", denied_tools=["delete_*"])
        record = store.resolve(result["raw_key"])
        assert store.check_tool_allowed(record, "delete_file") is False
        assert store.check_tool_allowed(record, "jira_search_issues") is True

    def test_wildcard_allowed_tool(self):
        store = AgentCredentialStore()
        result = store.create_key(agent_id="a1", tenant_id="t1", name="key", allowed_tools=["jira_*"])
        record = store.resolve(result["raw_key"])
        assert store.check_tool_allowed(record, "jira_create_issue") is True
        assert store.check_tool_allowed(record, "jira_delete_issue") is True

    def test_no_allowlist_means_all_tools_allowed(self):
        store = AgentCredentialStore()
        result = store.create_key(agent_id="a1", tenant_id="t1", name="key", allowed_tools=None)
        record = store.resolve(result["raw_key"])
        assert store.check_tool_allowed(record, "any_tool_ever") is True

    def test_list_for_agent(self):
        store = AgentCredentialStore()
        store.create_key(agent_id="a1", tenant_id="t1", name="key-1")
        store.create_key(agent_id="a1", tenant_id="t1", name="key-2")
        store.create_key(agent_id="a2", tenant_id="t1", name="other")
        keys = store.list_for_agent("a1")
        assert len(keys) == 2
        for k in keys:
            assert "key_hash" not in k  # never expose hash


class TestGoalTokens:
    def test_mint_and_verify(self):
        token = mint_goal_token(goal_id="g1", tenant_id="t1", agent_id="a1")
        payload = verify_goal_token(token)
        assert payload is not None
        assert "goal:g1" == payload["sub"]
        assert payload["tenant_id"] == "t1"

    def test_expired_token_invalid(self):
        token = mint_goal_token(goal_id="g1", tenant_id="t1", agent_id="a1", ttl=-1)
        assert verify_goal_token(token) is None

    def test_tampered_token_invalid(self):
        token = mint_goal_token(goal_id="g1", tenant_id="t1", agent_id="a1")
        tampered = token[:-5] + "XXXXX"
        assert verify_goal_token(tampered) is None

    def test_wrong_format_returns_none(self):
        assert verify_goal_token("not.a.valid.token.format") is None

    def test_token_contains_agent_id(self):
        token = mint_goal_token(goal_id="g1", tenant_id="t1", agent_id="agent-abc")
        payload = verify_goal_token(token)
        assert payload["agent_id"] == "agent-abc"

    def test_token_has_jti(self):
        """Each token must have a unique ID for anti-replay."""
        t1 = mint_goal_token(goal_id="g1", tenant_id="t1", agent_id="a1")
        t2 = mint_goal_token(goal_id="g1", tenant_id="t1", agent_id="a1")
        p1 = verify_goal_token(t1)
        p2 = verify_goal_token(t2)
        assert p1["jti"] != p2["jti"]


class TestAgentManifest:
    def test_build_manifest_has_required_fields(self):
        from unittest.mock import MagicMock
        tenant = MagicMock()
        tenant.tenant_id = "t1"
        tenant.plan = "professional"
        manifest = build_manifest({"id": "a1", "name": "Test Agent"}, tenant)
        assert manifest["agent_id"] == "a1"
        assert manifest["issuer"] == "agentverse.io"
        assert "valid_until" in manifest
        assert "capabilities" in manifest

    def test_sign_and_verify_manifest(self):
        from unittest.mock import MagicMock
        tenant = MagicMock()
        tenant.tenant_id = "t1"
        manifest = build_manifest({"id": "a1", "name": "Agent"}, tenant)
        signed = sign_manifest(manifest.copy(), secret="test-secret")
        assert signed["_signed"] is True
        assert verify_manifest(signed, secret="test-secret") is True

    def test_tampered_manifest_fails_verification(self):
        from unittest.mock import MagicMock
        tenant = MagicMock()
        tenant.tenant_id = "t1"
        manifest = build_manifest({"id": "a1"}, tenant)
        signed = sign_manifest(manifest.copy(), secret="test-secret")
        signed["autonomy_mode"] = "fully-autonomous"  # tamper
        assert verify_manifest(signed, secret="test-secret") is False


class TestDelegationChain:
    def test_direct_goal_chain(self):
        chain = DelegationChain.for_direct_goal(
            user_id="user-alice", tenant_id="t1",
            agent_id="a1", agent_name="Sales Agent", goal="find tickets"
        )
        assert chain.depth() == 2
        assert chain.root_user_id == "user-alice"

    def test_extend_for_spawn(self):
        parent = DelegationChain.for_direct_goal(
            user_id="u1", tenant_id="t1", agent_id="a1", agent_name="CEO", goal="analyze"
        )
        child = parent.extend_for_spawn(child_agent_id="a2", child_agent_name="CTO")
        assert child.depth() == 3
        assert child.current_actor().actor_name == "CTO"
        assert parent.depth() == 2  # original unchanged

    def test_to_string(self):
        chain = DelegationChain.for_direct_goal(
            user_id="alice", tenant_id="t1", agent_id="a1", agent_name="Sales", goal="x"
        )
        s = chain.to_string()
        assert "user:alice" in s
        assert "agent:Sales" in s
        assert "→" in s

    def test_audit_dict(self):
        chain = DelegationChain.for_direct_goal(
            user_id="u1", tenant_id="t1", agent_id="a1", agent_name="Bot", goal="x"
        )
        d = chain.to_audit_dict()
        assert "depth" in d
        assert "chain" in d
        assert len(d["chain"]) == 2
