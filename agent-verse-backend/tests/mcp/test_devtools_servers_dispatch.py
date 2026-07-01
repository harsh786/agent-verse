"""Dispatch-level tests for dev-tools MCP servers.

Exercises every call_tool() branch by mocking httpx.AsyncClient.
Targets: github, gitlab, jira, linear, bitbucket, azure_devops,
         confluence, sentry, jenkins, docker.
"""
from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# Helpers (same pattern as crm tests)
# ---------------------------------------------------------------------------


def make_resp(status: int = 200, data: Any = None) -> MagicMock:
    m = MagicMock()
    m.status_code = status
    m.json.return_value = data if data is not None else {}
    m.text = str(data or "")
    m.content = b"ok"
    m.raise_for_status = MagicMock()
    return m


def mk_client(**kwargs: MagicMock) -> AsyncMock:
    """Return a mock AsyncClient context manager.
    
    All HTTP method mocks are explicitly set to AsyncMock so that
    awaiting them works correctly regardless of Python version.
    """
    mc = AsyncMock()
    mc.__aenter__ = AsyncMock(return_value=mc)
    mc.__aexit__ = AsyncMock(return_value=False)
    _default = make_resp()
    for method in ("get", "post", "put", "patch", "delete"):
        setattr(mc, method, AsyncMock(return_value=kwargs.get(method, _default)))
    return mc


# ---------------------------------------------------------------------------
# GitHub
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_github_list_repos():
    from app.mcp.servers.github_server import call_tool

    repos = [{"name": "repo1", "full_name": "org/repo1", "html_url": "https://gh/repo1", "description": None}]
    mc = mk_client(get=make_resp(data=repos))
    with patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("github_list_repos", {"owner": "org"})
    assert "repos" in result
    assert result["repos"][0]["name"] == "repo1"


@pytest.mark.asyncio
async def test_github_get_file():
    from app.mcp.servers.github_server import call_tool

    import base64

    content_b64 = base64.b64encode(b"print('hello')").decode()
    data = {"path": "main.py", "sha": "abc123", "size": 14, "content": content_b64 + "\n", "encoding": "base64"}
    mc = mk_client(get=make_resp(data=data))
    with patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("github_get_file", {"owner": "org", "repo": "repo1", "path": "main.py"})
    assert result["path"] == "main.py"
    assert "print" in result["content"]


@pytest.mark.asyncio
async def test_github_list_issues():
    from app.mcp.servers.github_server import call_tool

    issues = [{"number": 1, "title": "Bug fix", "state": "open", "html_url": "https://gh/1", "body": "desc"}]
    mc = mk_client(get=make_resp(data=issues))
    with patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("github_list_issues", {"owner": "org", "repo": "repo1"})
    assert "issues" in result
    assert result["issues"][0]["number"] == 1


@pytest.mark.asyncio
async def test_github_create_issue():
    from app.mcp.servers.github_server import call_tool

    data = {"number": 42, "html_url": "https://gh/42", "title": "New Issue"}
    mc = mk_client(post=make_resp(data=data))
    with patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "github_create_issue",
            {"owner": "org", "repo": "repo1", "title": "New Issue"},
        )
    assert result["issue_number"] == 42


@pytest.mark.asyncio
async def test_github_create_pr():
    from app.mcp.servers.github_server import call_tool

    data = {"number": 10, "html_url": "https://gh/pr/10", "title": "Feature PR"}
    mc = mk_client(post=make_resp(data=data))
    with patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "github_create_pr",
            {"owner": "org", "repo": "repo1", "title": "Feature PR", "head": "feature"},
        )
    assert result["pr_number"] == 10


@pytest.mark.asyncio
async def test_github_search_code():
    from app.mcp.servers.github_server import call_tool

    data = {
        "total_count": 1,
        "items": [
            {
                "name": "main.py",
                "path": "src/main.py",
                "html_url": "https://gh/blob/main.py",
                "repository": {"full_name": "org/repo1"},
            }
        ],
    }
    mc = mk_client(get=make_resp(data=data))
    with patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("github_search_code", {"query": "def hello"})
    assert result["total_count"] == 1


@pytest.mark.asyncio
async def test_github_unknown_tool():
    from app.mcp.servers.github_server import call_tool

    mc = mk_client()
    with patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("github_nonexistent", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# GitLab
# ---------------------------------------------------------------------------

_GL = {"GITLAB_TOKEN": "gl-token", "GITLAB_BASE_URL": "https://gitlab.example.com"}


@pytest.mark.asyncio
async def test_gitlab_list_projects():
    from app.mcp.servers.gitlab_server import call_tool

    data = [{"id": 1, "name": "Project A", "path_with_namespace": "group/project-a", "http_url_to_repo": "https://gl/g/p.git", "description": "desc"}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gitlab_list_projects", {})
    assert "projects" in result


@pytest.mark.asyncio
async def test_gitlab_list_issues():
    from app.mcp.servers.gitlab_server import call_tool

    data = [{"iid": 1, "title": "Bug", "state": "opened", "web_url": "https://gl/i/1", "description": "d", "labels": [], "assignees": []}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gitlab_list_issues", {"project_id": "1"})
    assert "issues" in result


@pytest.mark.asyncio
async def test_gitlab_create_issue():
    from app.mcp.servers.gitlab_server import call_tool

    data = {"iid": 2, "title": "New Issue", "web_url": "https://gl/i/2", "state": "opened"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gitlab_create_issue", {"project_id": "1", "title": "New Issue"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_gitlab_update_issue():
    from app.mcp.servers.gitlab_server import call_tool

    data = {"iid": 2, "state": "closed", "title": "Issue"}
    mc = mk_client(put=make_resp(data=data))
    with patch.dict("os.environ", _GL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gitlab_update_issue", {"project_id": "1", "issue_iid": 2, "state_event": "close"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_gitlab_list_merge_requests():
    from app.mcp.servers.gitlab_server import call_tool

    data = [{"iid": 1, "title": "MR 1", "state": "opened", "web_url": "url", "source_branch": "feature", "target_branch": "main", "author": {"name": "Alice"}}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gitlab_list_merge_requests", {"project_id": "1"})
    assert "merge_requests" in result


@pytest.mark.asyncio
async def test_gitlab_create_merge_request():
    from app.mcp.servers.gitlab_server import call_tool

    data = {"iid": 2, "title": "New MR", "web_url": "url", "state": "opened"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gitlab_create_merge_request",
            {"project_id": "1", "title": "New MR", "source_branch": "feature", "target_branch": "main"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_gitlab_get_file():
    from app.mcp.servers.gitlab_server import call_tool

    import base64

    content_b64 = base64.b64encode(b"content").decode()
    data = {"file_path": "README.md", "ref": "main", "content": content_b64, "encoding": "base64", "size": 7}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gitlab_get_file",
            {"project_id": "1", "file_path": "README.md"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_gitlab_list_pipelines():
    from app.mcp.servers.gitlab_server import call_tool

    data = [{"id": 1, "status": "success", "ref": "main", "sha": "abc", "web_url": "url"}]
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _GL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gitlab_list_pipelines", {"project_id": "1"})
    assert "pipelines" in result


@pytest.mark.asyncio
async def test_gitlab_trigger_pipeline():
    from app.mcp.servers.gitlab_server import call_tool

    data = {"id": 2, "status": "pending", "web_url": "url", "ref": "main"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gitlab_trigger_pipeline", {"project_id": "1", "ref": "main"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_gitlab_add_comment():
    from app.mcp.servers.gitlab_server import call_tool

    data = {"id": 1, "body": "LGTM", "created_at": "2024-01-01"}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _GL), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "gitlab_add_comment",
            {"project_id": "1", "issue_iid": 1, "body": "LGTM"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_gitlab_missing_env():
    from app.mcp.servers.gitlab_server import call_tool

    # GitLab has no explicit env-var guard; with empty token the mock response is returned
    mc = mk_client(get=make_resp(data=[]))
    with patch.dict("os.environ", {"GITLAB_TOKEN": ""}), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("gitlab_list_projects", {})
    # Result is still processed (empty projects list), no hard error
    assert "projects" in result


# ---------------------------------------------------------------------------
# Jira
# ---------------------------------------------------------------------------

_JIRA = {
    "JIRA_BASE_URL": "https://myco.atlassian.net",
    "JIRA_EMAIL": "user@example.com",
    "JIRA_API_TOKEN": "jira-tok",
}

_JIRA_ISSUE_FIELDS = {
    "summary": "Fix bug",
    "description": None,
    "status": {"name": "Open"},
    "priority": {"name": "Medium"},
    "assignee": None,
    "reporter": None,
    "issuetype": {"name": "Bug"},
    "created": "2024-01-01T00:00:00",
    "updated": "2024-01-01T00:00:00",
    "labels": [],
}


@pytest.mark.asyncio
async def test_jira_search_issues():
    from app.mcp.servers.jira_server import call_tool

    resp_data = {
        "total": 1,
        "startAt": 0,
        "maxResults": 50,
        "issues": [{"id": "1", "key": "PROJ-1", "fields": _JIRA_ISSUE_FIELDS}],
    }
    mc = mk_client(post=make_resp(data=resp_data))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jira_search_issues", {"jql": "project = PROJ"})
    assert result["total"] == 1
    assert result["issues"][0]["key"] == "PROJ-1"


@pytest.mark.asyncio
async def test_jira_search_issues_accepts_base_url_without_protocol():
    from app.mcp.servers.jira_server import call_tool

    resp_data = {"total": 0, "issues": []}
    mc = mk_client(post=make_resp(data=resp_data))
    env = {**_JIRA, "JIRA_BASE_URL": "myco.atlassian.net"}
    with patch.dict("os.environ", env), patch("httpx.AsyncClient") as cls:
        cls.return_value = mc
        result = await call_tool("jira_search_issues", {"jql": "project = PROJ"})

    assert result["total"] == 0
    assert cls.call_args.kwargs["base_url"] == "https://myco.atlassian.net"


@pytest.mark.asyncio
async def test_jira_get_issue():
    from app.mcp.servers.jira_server import call_tool

    resp_data = {"id": "1", "key": "PROJ-1", "fields": _JIRA_ISSUE_FIELDS}
    mc = mk_client(get=make_resp(data=resp_data))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jira_get_issue", {"issue_id_or_key": "PROJ-1"})
    assert result["key"] == "PROJ-1"


@pytest.mark.asyncio
async def test_jira_create_issue():
    from app.mcp.servers.jira_server import call_tool

    resp_data = {"id": "1", "key": "PROJ-2", "self": "https://atlassian.net/issue/1"}
    mc = mk_client(post=make_resp(data=resp_data))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "jira_create_issue",
            {"project_key": "PROJ", "summary": "New Bug"},
        )
    assert result["key"] == "PROJ-2"


@pytest.mark.asyncio
async def test_jira_create_issue_with_optional_fields():
    from app.mcp.servers.jira_server import call_tool

    resp_data = {"id": "2", "key": "PROJ-3", "self": "https://atlassian.net/issue/2"}
    mc = mk_client(post=make_resp(data=resp_data))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "jira_create_issue",
            {
                "project_key": "PROJ",
                "summary": "Story",
                "issue_type": "Story",
                "description": "Full desc",
                "priority": "High",
                "labels": ["frontend"],
                "components": ["UI"],
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_jira_update_issue():
    from app.mcp.servers.jira_server import call_tool

    mc = mk_client(put=make_resp(status=204))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "jira_update_issue",
            {"issue_id_or_key": "PROJ-1", "summary": "Updated Summary"},
        )
    assert result["updated"] is True


@pytest.mark.asyncio
async def test_jira_add_comment():
    from app.mcp.servers.jira_server import call_tool

    resp_data = {"id": "comment-1", "created": "2024-01-01T00:00:00"}
    mc = mk_client(post=make_resp(data=resp_data))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "jira_add_comment", {"issue_id_or_key": "PROJ-1", "body": "LGTM!"}
        )
    assert result["comment_id"] == "comment-1"


@pytest.mark.asyncio
async def test_jira_transition_issue():
    from app.mcp.servers.jira_server import call_tool

    mc = mk_client(post=make_resp(status=204))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "jira_transition_issue",
            {"issue_id_or_key": "PROJ-1", "transition_id": "31"},
        )
    assert result["transitioned"] is True


@pytest.mark.asyncio
async def test_jira_transition_issue_with_comment():
    from app.mcp.servers.jira_server import call_tool

    mc = mk_client(post=make_resp(status=204))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "jira_transition_issue",
            {"issue_id_or_key": "PROJ-1", "transition_id": "31", "comment": "Moving to done"},
        )
    assert result["transitioned"] is True


@pytest.mark.asyncio
async def test_jira_get_transitions():
    from app.mcp.servers.jira_server import call_tool

    resp_data = {
        "transitions": [
            {"id": "1", "name": "To Do", "to": {"name": "To Do"}},
            {"id": "2", "name": "In Progress", "to": {"name": "In Progress"}},
        ]
    }
    mc = mk_client(get=make_resp(data=resp_data))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jira_get_transitions", {"issue_id_or_key": "PROJ-1"})
    assert len(result["transitions"]) == 2


@pytest.mark.asyncio
async def test_jira_assign_issue():
    from app.mcp.servers.jira_server import call_tool

    mc = mk_client(put=make_resp(status=204))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "jira_assign_issue", {"issue_id_or_key": "PROJ-1", "account_id": "user-123"}
        )
    assert result["assigned"] is True


@pytest.mark.asyncio
async def test_jira_list_projects():
    from app.mcp.servers.jira_server import call_tool

    resp_data = [{"id": "1", "key": "PROJ", "name": "My Project", "projectTypeKey": "software"}]
    mc = mk_client(get=make_resp(data=resp_data))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jira_list_projects", {})
    assert len(result["projects"]) == 1


@pytest.mark.asyncio
async def test_jira_create_sprint():
    from app.mcp.servers.jira_server import call_tool

    resp_data = {"id": 1, "name": "Sprint 1", "state": "future"}
    mc = mk_client(post=make_resp(data=resp_data))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "jira_create_sprint", {"board_id": 1, "name": "Sprint 1"}
        )
    assert result["sprint_id"] == 1


@pytest.mark.asyncio
async def test_jira_get_board_sprints():
    from app.mcp.servers.jira_server import call_tool

    resp_data = {
        "values": [{"id": 1, "name": "Sprint 1", "state": "active", "startDate": "2024-01-01", "endDate": "2024-01-15", "goal": ""}]
    }
    mc = mk_client(get=make_resp(data=resp_data))
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jira_get_board_sprints", {"board_id": 1})
    assert result["sprints"][0]["id"] == 1


@pytest.mark.asyncio
async def test_jira_missing_env():
    from app.mcp.servers.jira_server import call_tool

    with patch.dict("os.environ", {"JIRA_BASE_URL": ""}):
        os.environ.pop("JIRA_BASE_URL", None)
        result = await call_tool("jira_search_issues", {"jql": "project = PROJ"})
    assert "error" in result


@pytest.mark.asyncio
async def test_jira_unknown_tool():
    from app.mcp.servers.jira_server import call_tool

    mc = mk_client()
    with patch.dict("os.environ", _JIRA), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jira_nonexistent_tool", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Linear (GraphQL)
# ---------------------------------------------------------------------------

_LINEAR = {"LINEAR_API_KEY": "lin-key"}


@pytest.mark.asyncio
async def test_linear_list_issues():
    from app.mcp.servers.linear_server import call_tool

    resp = {"data": {"issues": {"nodes": [{"id": "i1", "title": "Bug", "identifier": "LIN-1", "state": {"name": "Todo"}, "assignee": None, "priority": 2, "url": "https://linear.app/i1"}]}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _LINEAR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linear_list_issues", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linear_get_issue():
    from app.mcp.servers.linear_server import call_tool

    resp = {"data": {"issue": {"id": "i1", "title": "Bug", "identifier": "LIN-1", "description": "desc", "state": {"name": "Todo"}, "assignee": None, "priority": 2, "url": "https://linear.app/i1", "createdAt": "2024-01-01", "updatedAt": "2024-01-01"}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _LINEAR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linear_get_issue", {"issue_id": "i1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linear_create_issue():
    from app.mcp.servers.linear_server import call_tool

    resp = {"data": {"issueCreate": {"success": True, "issue": {"id": "i2", "identifier": "LIN-2", "url": "https://linear.app/i2"}}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _LINEAR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "linear_create_issue", {"title": "New Issue", "team_id": "team-1"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_linear_update_issue():
    from app.mcp.servers.linear_server import call_tool

    resp = {"data": {"issueUpdate": {"success": True, "issue": {"id": "i1", "identifier": "LIN-1", "url": "url"}}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _LINEAR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "linear_update_issue", {"issue_id": "i1", "title": "Updated"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_linear_list_teams():
    from app.mcp.servers.linear_server import call_tool

    resp = {"data": {"teams": {"nodes": [{"id": "t1", "name": "Engineering", "key": "ENG"}]}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _LINEAR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linear_list_teams", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linear_list_projects():
    from app.mcp.servers.linear_server import call_tool

    resp = {"data": {"projects": {"nodes": [{"id": "p1", "name": "Q1 Goals", "state": "started", "url": "url"}]}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _LINEAR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linear_list_projects", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linear_add_comment():
    from app.mcp.servers.linear_server import call_tool

    resp = {"data": {"commentCreate": {"success": True, "comment": {"id": "c1", "createdAt": "2024-01-01"}}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _LINEAR), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("linear_add_comment", {"issue_id": "i1", "body": "LGTM"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_linear_missing_env():
    from app.mcp.servers.linear_server import call_tool

    with patch.dict("os.environ", {"LINEAR_API_KEY": ""}):
        os.environ.pop("LINEAR_API_KEY", None)
        result = await call_tool("linear_list_issues", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Bitbucket
# ---------------------------------------------------------------------------

_BB = {"BITBUCKET_USERNAME": "user", "BITBUCKET_APP_PASSWORD": "pass"}


@pytest.mark.asyncio
async def test_bitbucket_list_repos():
    from app.mcp.servers.bitbucket_server import call_tool

    data = {"values": [{"slug": "repo1", "name": "Repo 1", "full_name": "ws/repo1", "links": {"html": {"href": "url"}}}], "next": None}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _BB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bitbucket_list_repos", {"workspace": "ws"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_bitbucket_list_issues():
    from app.mcp.servers.bitbucket_server import call_tool

    # Server reads i["status"] not i["state"]
    data = {"values": [{"id": 1, "title": "Issue 1", "status": "new", "links": {"html": {"href": "url"}}, "kind": "bug"}], "next": None}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _BB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "bitbucket_list_issues", {"workspace": "ws", "repo_slug": "repo1"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_bitbucket_create_issue():
    from app.mcp.servers.bitbucket_server import call_tool

    data = {"id": 2, "title": "New Issue", "state": "new", "links": {"html": {"href": "url"}}}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _BB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "bitbucket_create_issue",
            {"workspace": "ws", "repo_slug": "repo1", "title": "New Issue"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_bitbucket_list_pull_requests():
    from app.mcp.servers.bitbucket_server import call_tool

    data = {"values": [{"id": 1, "title": "PR 1", "state": "OPEN", "links": {"html": {"href": "url"}}, "source": {"branch": {"name": "feature"}}, "destination": {"branch": {"name": "main"}}}], "next": None}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _BB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "bitbucket_list_pull_requests", {"workspace": "ws", "repo_slug": "repo1"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_bitbucket_create_pull_request():
    from app.mcp.servers.bitbucket_server import call_tool

    data = {"id": 2, "title": "New PR", "state": "OPEN", "links": {"html": {"href": "url"}}}
    mc = mk_client(post=make_resp(data=data))
    with patch.dict("os.environ", _BB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "bitbucket_create_pull_request",
            {
                "workspace": "ws",
                "repo_slug": "repo1",
                "title": "New PR",
                "source_branch": "feature",
                "destination_branch": "main",
            },
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_bitbucket_list_pipelines():
    from app.mcp.servers.bitbucket_server import call_tool

    data = {"values": [{"uuid": "{abc}", "state": {"name": "COMPLETED", "result": {"name": "SUCCESSFUL"}}, "target": {"ref_name": "main"}, "created_on": "2024-01-01"}], "next": None}
    mc = mk_client(get=make_resp(data=data))
    with patch.dict("os.environ", _BB), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "bitbucket_list_pipelines", {"workspace": "ws", "repo_slug": "repo1"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_bitbucket_missing_env():
    from app.mcp.servers.bitbucket_server import call_tool

    # Bitbucket has no explicit env-var guard; uses basic auth with empty creds
    mc = mk_client(get=make_resp(data={"values": [], "next": None}))
    with patch.dict("os.environ", {"BITBUCKET_USERNAME": "", "BITBUCKET_APP_PASSWORD": ""}), \
         patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("bitbucket_list_repos", {"workspace": "ws"})
    # Mock responds fine even with empty auth
    assert "repos" in result


# ---------------------------------------------------------------------------
# Azure DevOps
# ---------------------------------------------------------------------------

_ADO = {
    "AZURE_DEVOPS_ORG": "myorg",
    "AZURE_DEVOPS_PROJECT": "myproject",
    "AZURE_DEVOPS_TOKEN": "ado-tok",
}


@pytest.mark.asyncio
async def test_azure_list_work_items():
    from app.mcp.servers.azure_devops_server import call_tool

    # Server calls WIQL first then batch-fetches items. Use side_effect for two POST calls.
    resp_wiql = {"workItems": [{"id": 1, "url": "url"}]}
    resp_batch = {"value": [{"id": 1, "fields": {"System.Title": "Bug", "System.State": "Active", "System.WorkItemType": "Bug", "System.AssignedTo": None}}]}
    mc = mk_client()
    mc.post = AsyncMock(side_effect=[make_resp(data=resp_wiql), make_resp(data=resp_batch)])
    with patch.dict("os.environ", _ADO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        # Server requires "wiql_query" argument
        result = await call_tool("azure_list_work_items", {"wiql_query": "SELECT [System.Id] FROM WorkItems WHERE [System.TeamProject] = 'myproject'"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_azure_create_work_item():
    from app.mcp.servers.azure_devops_server import call_tool

    resp = {"id": 2, "fields": {"System.Title": "New Bug", "System.State": "New", "System.WorkItemType": "Bug"}, "url": "url", "_links": {"html": {"href": "html_url"}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _ADO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "azure_create_work_item", {"title": "New Bug", "work_item_type": "Bug"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_azure_list_pull_requests():
    from app.mcp.servers.azure_devops_server import call_tool

    resp = {"value": [{"pullRequestId": 1, "title": "PR 1", "status": "active", "createdBy": {"displayName": "Alice"}, "sourceRefName": "feature", "targetRefName": "main"}], "count": 1}
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _ADO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("azure_list_pull_requests", {"repo_id": "repo1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_azure_list_pipelines():
    from app.mcp.servers.azure_devops_server import call_tool

    resp = {"value": [{"id": 1, "name": "CI", "url": "url", "folder": "\\"}], "count": 1}
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _ADO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("azure_list_pipelines", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_azure_run_pipeline():
    from app.mcp.servers.azure_devops_server import call_tool

    resp = {"id": 1, "state": "inProgress", "_links": {"web": {"href": "url"}}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _ADO), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("azure_run_pipeline", {"pipeline_id": 1})
    assert "error" not in result


@pytest.mark.asyncio
async def test_azure_missing_env():
    from app.mcp.servers.azure_devops_server import call_tool

    with patch.dict("os.environ", {"AZURE_DEVOPS_TOKEN": ""}):
        os.environ.pop("AZURE_DEVOPS_TOKEN", None)
        result = await call_tool("azure_list_pipelines", {})
    assert "error" in result


# ---------------------------------------------------------------------------
# Confluence
# ---------------------------------------------------------------------------

_CONF = {
    "CONFLUENCE_BASE_URL": "https://myco.atlassian.net",
    "CONFLUENCE_EMAIL": "user@example.com",
    "CONFLUENCE_API_TOKEN": "conf-tok",
}


@pytest.mark.asyncio
async def test_confluence_search_pages():
    from app.mcp.servers.confluence_server import call_tool

    # Tool is "confluence_search" with "cql" argument (not "query")
    resp = {"results": [{"content": {"id": "1", "title": "Home", "type": "page"}, "resultGlobalContainer": {"title": "Dev Space"}, "url": "/wiki/spaces/DS/pages/1", "excerpt": "Home page"}], "totalSize": 1}
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _CONF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("confluence_search", {"cql": "title = 'home'"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_confluence_get_page():
    from app.mcp.servers.confluence_server import call_tool

    resp = {"id": "1", "title": "Home", "space": {"key": "DS"}, "version": {"number": 1}, "body": {"storage": {"value": "<p>Content</p>"}}, "_links": {"webui": "/pages/1"}}
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _CONF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("confluence_get_page", {"page_id": "1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_confluence_create_page():
    from app.mcp.servers.confluence_server import call_tool

    resp = {"id": "2", "title": "New Page", "_links": {"webui": "/pages/2"}, "version": {"number": 1}, "space": {"key": "DS"}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _CONF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "confluence_create_page",
            {"space_key": "DS", "title": "New Page", "body": "<p>content</p>"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_confluence_update_page():
    from app.mcp.servers.confluence_server import call_tool

    resp = {"id": "1", "title": "Updated", "_links": {"webui": "/pages/1"}, "version": {"number": 2}}
    mc = mk_client(put=make_resp(data=resp))
    with patch.dict("os.environ", _CONF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "confluence_update_page",
            # Use "version_number" (not "version") so server skips the auto-fetch GET
            {"page_id": "1", "title": "Updated", "body": "<p>new</p>", "version_number": 1},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_confluence_list_spaces():
    from app.mcp.servers.confluence_server import call_tool

    resp = {"results": [{"id": 1, "key": "DS", "name": "Dev Space", "type": "global", "_links": {"webui": "/spaces/DS"}}], "size": 1}
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _CONF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("confluence_list_spaces", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_confluence_add_comment():
    from app.mcp.servers.confluence_server import call_tool

    resp = {"id": "c1", "title": "", "version": {"number": 1}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _CONF), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "confluence_add_comment", {"page_id": "1", "body": "Nice doc!"}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_confluence_missing_env():
    from app.mcp.servers.confluence_server import call_tool

    with patch.dict("os.environ", {"CONFLUENCE_BASE_URL": ""}):
        os.environ.pop("CONFLUENCE_BASE_URL", None)
        result = await call_tool("confluence_search_pages", {"query": "home"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Sentry
# ---------------------------------------------------------------------------

_SENTRY = {"SENTRY_AUTH_TOKEN": "sentry-tok", "SENTRY_ORG_SLUG": "myorg"}


@pytest.mark.asyncio
async def test_sentry_list_issues():
    from app.mcp.servers.sentry_server import call_tool

    # Server uses arguments["project_slug"], not "project"
    resp = [{"id": "1", "title": "Error", "status": "unresolved", "level": "error", "count": "5", "lastSeen": "2024-01-02", "firstSeen": "2024-01-01"}]
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _SENTRY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sentry_list_issues", {"project_slug": "myproject"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sentry_get_issue():
    from app.mcp.servers.sentry_server import call_tool

    resp = {"id": "1", "title": "Error", "status": "unresolved", "culprit": "app/views.py", "shortId": "MYORG-1", "count": "5", "userCount": 2, "firstSeen": "2024-01-01", "lastSeen": "2024-01-02", "project": {"slug": "myproject"}}
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _SENTRY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sentry_get_issue", {"issue_id": "1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sentry_update_issue():
    from app.mcp.servers.sentry_server import call_tool

    resp = {"id": "1", "status": "resolved"}
    mc = mk_client(put=make_resp(data=resp))
    with patch.dict("os.environ", _SENTRY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sentry_update_issue", {"issue_id": "1", "status": "resolved"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sentry_list_events():
    from app.mcp.servers.sentry_server import call_tool

    resp = [{"id": "e1", "message": "NullPointerException", "dateCreated": "2024-01-01", "user": None, "tags": []}]
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _SENTRY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("sentry_list_events", {"issue_id": "1"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_sentry_create_release():
    from app.mcp.servers.sentry_server import call_tool

    resp = {"version": "1.0.0", "projects": [{"slug": "myproject"}], "dateCreated": "2024-01-01"}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _SENTRY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "sentry_create_release",
            {"version": "1.0.0", "projects": ["myproject"]},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_sentry_query():
    from app.mcp.servers.sentry_server import call_tool

    # sentry_query uses POST, requires "fields" list argument
    resp = {"data": [{"count()": 10}], "meta": {}}
    mc = mk_client(post=make_resp(data=resp))
    with patch.dict("os.environ", _SENTRY), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "sentry_query",
            {"fields": ["count()"], "query": "is:unresolved"},
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_sentry_missing_env():
    from app.mcp.servers.sentry_server import call_tool

    with patch.dict("os.environ", {"SENTRY_AUTH_TOKEN": ""}):
        os.environ.pop("SENTRY_AUTH_TOKEN", None)
        result = await call_tool("sentry_list_issues", {"project": "myproject"})
    assert "error" in result


# ---------------------------------------------------------------------------
# Jenkins
# ---------------------------------------------------------------------------

_JENKINS = {
    "JENKINS_URL": "https://jenkins.example.com",
    "JENKINS_USER": "admin",
    "JENKINS_TOKEN": "jenkins-tok",
}


@pytest.mark.asyncio
async def test_jenkins_list_jobs():
    from app.mcp.servers.jenkins_server import call_tool

    resp = {"jobs": [{"name": "my-pipeline", "url": "url", "color": "blue"}]}
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _JENKINS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jenkins_list_jobs", {})
    assert "error" not in result


@pytest.mark.asyncio
async def test_jenkins_get_job():
    from app.mcp.servers.jenkins_server import call_tool

    # Server uses arguments["name"] not arguments["job_name"]
    resp = {"name": "my-pipeline", "url": "url", "color": "blue", "nextBuildNumber": 42, "buildable": True, "lastBuild": None, "lastSuccessfulBuild": None, "lastFailedBuild": None}
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _JENKINS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jenkins_get_job", {"name": "my-pipeline"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_jenkins_trigger_build():
    from app.mcp.servers.jenkins_server import call_tool

    # Server checks resp.status_code in (201, 200, 302) and reads resp.headers.get("Location")
    mock_resp = make_resp(status=201)
    mock_resp.headers = {"Location": "https://jenkins.example.com/queue/item/1/"}
    mc = mk_client(post=mock_resp)
    with patch.dict("os.environ", _JENKINS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jenkins_trigger_build", {"name": "my-pipeline"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_jenkins_get_build_status():
    from app.mcp.servers.jenkins_server import call_tool

    # Server uses arguments["name"] and arguments["number"]
    resp = {"number": 41, "result": "SUCCESS", "duration": 120000, "timestamp": 1704067200000, "url": "url/41", "building": False}
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _JENKINS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool(
            "jenkins_get_build_status", {"name": "my-pipeline", "number": 41}
        )
    assert "error" not in result


@pytest.mark.asyncio
async def test_jenkins_list_builds():
    from app.mcp.servers.jenkins_server import call_tool

    # Server uses arguments["name"]
    resp = {"builds": [{"number": 41, "url": "url/41", "result": "SUCCESS", "duration": 120000, "timestamp": 1704067200000, "building": False}]}
    mc = mk_client(get=make_resp(data=resp))
    with patch.dict("os.environ", _JENKINS), patch("httpx.AsyncClient") as Cls:
        Cls.return_value = mc
        result = await call_tool("jenkins_list_builds", {"name": "my-pipeline"})
    assert "error" not in result


@pytest.mark.asyncio
async def test_jenkins_missing_env():
    from app.mcp.servers.jenkins_server import call_tool

    with patch.dict("os.environ", {"JENKINS_URL": ""}):
        os.environ.pop("JENKINS_URL", None)
        result = await call_tool("jenkins_list_jobs", {})
    assert "error" in result
