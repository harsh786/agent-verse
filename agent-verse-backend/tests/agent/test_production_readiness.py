"""Production-readiness tests for critical and high-severity fixes."""
import pytest
import os


def test_dockerfile_runs_migrations():
    """Dockerfile CMD must run alembic upgrade head before starting server."""
    dockerfile_path = "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/Dockerfile"
    with open(dockerfile_path) as f:
        content = f.read()
    assert "alembic upgrade head" in content, \
        "Dockerfile CMD must run 'alembic upgrade head' before starting uvicorn"


def test_minio_healthcheck_uses_mc():
    """MinIO healthcheck must use mc not curl (curl not in minio image)."""
    import yaml
    compose_path = "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/infra/docker-compose.yml"
    with open(compose_path) as f:
        compose = yaml.safe_load(f)
    minio_health = compose.get("services", {}).get("minio", {}).get("healthcheck", {})
    test_cmd = str(minio_health.get("test", ""))
    assert "curl" not in test_cmd, "MinIO healthcheck must not use curl (not in image)"
    assert "mc" in test_cmd or "wget" in test_cmd, "MinIO healthcheck must use mc or wget"


def test_all_aws_servers_registered():
    """AWS, Azure, K8s, Docker, Heroku, DigitalOcean servers must be registered."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs
    configs = get_builtin_server_configs()
    server_ids = {c["server_id"] for c in configs}
    required = {
        "builtin-aws-cloudwatch", "builtin-aws-iam", "builtin-aws-lambda", "builtin-aws-s3",
        "builtin-azure-devops", "builtin-digitalocean", "builtin-docker",
        "builtin-heroku", "builtin-kubernetes", "builtin-netlify", "builtin-vercel",
    }
    missing = required - server_ids
    assert not missing, f"These servers must be registered: {missing}"


def test_no_duplicate_server_ids():
    """registry_wiring.py must not have duplicate server_id entries."""
    from app.mcp.servers.registry_wiring import get_builtin_server_configs
    configs = get_builtin_server_configs()
    ids = [c["server_id"] for c in configs]
    from collections import Counter
    dupes = {k: v for k, v in Counter(ids).items() if v > 1}
    assert not dupes, f"Duplicate server_id entries found: {dupes}"


def test_worker_has_llm_key_guidance():
    """docker-compose.yml worker service must document LLM key configuration."""
    compose_path = "/Users/harsh.kumar01/Documents/Learning/Agent-Verse/agent-verse-backend/infra/docker-compose.yml"
    with open(compose_path) as f:
        content = f.read()
    assert "ANTHROPIC_API_KEY" in content, \
        "docker-compose.yml worker must document ANTHROPIC_API_KEY"


def test_oauth_redirect_uri_not_hardcoded():
    """OAuth callback must not hardcode localhost:8000 as redirect URI."""
    import inspect
    from app.api import connectors
    src = inspect.getsource(connectors)
    # Should not have a hardcoded default localhost in the function signature
    assert 'redirect_uri: str = "http://localhost:8000' not in src, \
        "OAuth callback must not hardcode localhost:8000 as default redirect URI"


def test_sse_bridge_handles_missing_goal_record():
    """Celery event bridge must create stub record when goal not yet in _goals."""
    import inspect
    from app.services import goal_service
    src = inspect.getsource(goal_service)
    assert "stub" in src.lower() or "GoalRecord" in src and "bridge" in src.lower(), \
        "Event bridge must handle missing goal records by creating stubs"


def test_agent_max_iterations_applied():
    """_make_agent_loop_for_tenant must apply max_iterations from agent config."""
    import inspect
    from app.services import goal_service
    src = inspect.getsource(goal_service)
    assert "max_iterations" in src, \
        "_make_agent_loop_for_tenant must pass max_iterations from agent config"
