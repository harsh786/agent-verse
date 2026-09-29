"""Comprehensive tests for app/api/perception.py — targets the 34% baseline."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.perception import router as perception_router


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve every host to a public IP: these tests exercise the browser paths,
    not the SSRF guard (tested in tests/perception/test_perception_ssrf.py)."""
    monkeypatch.setattr("app.net.ssrf_guard._resolve_host", lambda host: ["93.184.216.34"])
from app.tenancy.context import PlanTier, TenantContext
from app.tenancy.middleware import TenantMiddleware

_CTX = TenantContext(tenant_id="perc-t1", plan=PlanTier.PROFESSIONAL, api_key_id="perc-key")
_VALID_KEY = "perc-key"
_HEADERS = {"X-API-Key": _VALID_KEY}


def _make_app(browser_agent: Any = None) -> FastAPI:
    app = FastAPI()

    async def _resolve(key: str) -> TenantContext | None:
        return _CTX if key == _VALID_KEY else None

    app.add_middleware(TenantMiddleware, key_resolver=_resolve)
    app.include_router(perception_router)
    if browser_agent is not None:
        app.state.browser_agent = browser_agent
    return app


def _make_screenshot_result(*, success: bool = True, b64: str = "abc123", error: str | None = None):
    return SimpleNamespace(success=success, screenshot_b64=b64, error=error, output="")


def _make_action_result(*, success: bool = True, output: str = "text content", error: str | None = None):
    return SimpleNamespace(success=success, output=output, error=error, screenshot_b64="")


# ── Auth guard ────────────────────────────────────────────────────────────────

def test_get_status_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.get("/perception/status")
    assert resp.status_code == 401


def test_screenshot_requires_auth() -> None:
    client = TestClient(_make_app())
    resp = client.post("/perception/screenshot", json={"url": "https://example.com"})
    assert resp.status_code == 401


# ── GET /perception/status ────────────────────────────────────────────────────

def test_get_status_playwright_not_available(monkeypatch) -> None:
    import app.perception.browser_agent as ba
    monkeypatch.setattr(ba, "_PLAYWRIGHT_AVAILABLE", False)
    client = TestClient(_make_app())
    resp = client.get("/perception/status", headers=_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["playwright_available"] is False
    assert isinstance(data["browser_actions"], list)
    assert len(data["browser_actions"]) > 0


def test_get_status_playwright_available(monkeypatch) -> None:
    import app.perception.browser_agent as ba
    monkeypatch.setattr(ba, "_PLAYWRIGHT_AVAILABLE", True)
    client = TestClient(_make_app())
    resp = client.get("/perception/status", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["playwright_available"] is True


def test_get_status_no_vision_provider() -> None:
    client = TestClient(_make_app())
    resp = client.get("/perception/status", headers=_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["vision_available"] is False


def test_get_status_with_vision_provider() -> None:
    from app.perception.browser_agent import BrowserAgent

    vision = MagicMock()
    vision.supports_vision.return_value = True
    app = _make_app(BrowserAgent(vision_provider=vision))
    client = TestClient(app)
    resp = client.get("/perception/status", headers=_HEADERS)
    assert resp.json()["vision_available"] is True


# ── POST /perception/screenshot ───────────────────────────────────────────────

def test_screenshot_invalid_url_returns_400() -> None:
    client = TestClient(_make_app())
    resp = client.post("/perception/screenshot", json={"url": "ftp://bad.com"}, headers=_HEADERS)
    assert resp.status_code == 400


def test_screenshot_valid_url_success() -> None:
    agent = MagicMock()
    agent.take_screenshot = AsyncMock(return_value=_make_screenshot_result())
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/screenshot",
        json={"url": "https://example.com"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["url"] == "https://example.com"
    assert data["screenshot_b64"] == "abc123"
    assert data["error"] is None


def test_screenshot_failure_returns_200_with_error() -> None:
    agent = MagicMock()
    agent.take_screenshot = AsyncMock(
        return_value=_make_screenshot_result(success=False, b64="", error="Timeout")
    )
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/screenshot",
        json={"url": "https://timeout.example.com"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["error"] == "Timeout"


def test_screenshot_full_page_param() -> None:
    agent = MagicMock()
    agent.take_screenshot = AsyncMock(return_value=_make_screenshot_result())
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/screenshot",
        json={"url": "https://example.com", "full_page": True},
        headers=_HEADERS,
    )
    assert resp.status_code == 200


# ── POST /perception/analyze ──────────────────────────────────────────────────

def test_analyze_no_screenshot_no_url_returns_400() -> None:
    agent = MagicMock()
    client = TestClient(_make_app(agent))
    resp = client.post("/perception/analyze", json={}, headers=_HEADERS)
    assert resp.status_code == 400


def test_analyze_invalid_url_returns_400() -> None:
    agent = MagicMock()
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/analyze",
        json={"url": "file:///etc/passwd"},
        headers=_HEADERS,
    )
    assert resp.status_code == 400


def test_analyze_with_screenshot_b64() -> None:
    agent = MagicMock()
    agent.analyze_screenshot = AsyncMock(return_value="This is a login page")
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/analyze",
        json={"screenshot_b64": "base64data", "question": "What is this?"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "analysis" in data
    assert data["screenshot_provided"] is True


def test_analyze_with_url_captures_then_analyzes() -> None:
    agent = MagicMock()
    agent.take_screenshot = AsyncMock(return_value=_make_screenshot_result(b64="captured"))
    agent.analyze_screenshot = AsyncMock(return_value="Homepage with navigation")
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/analyze",
        json={"url": "https://example.com"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["screenshot_provided"] is False


def test_analyze_url_screenshot_failure_returns_502() -> None:
    agent = MagicMock()
    agent.take_screenshot = AsyncMock(
        return_value=_make_screenshot_result(success=False, b64="", error="Timed out")
    )
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/analyze",
        json={"url": "https://unreachable.example.com"},
        headers=_HEADERS,
    )
    assert resp.status_code == 502


def test_analyze_default_question() -> None:
    agent = MagicMock()
    agent.analyze_screenshot = AsyncMock(return_value="Description")
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/analyze",
        json={"screenshot_b64": "data"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    assert "question" in resp.json()


# ── POST /perception/extract ──────────────────────────────────────────────────

def test_extract_invalid_url_returns_400() -> None:
    agent = MagicMock()
    client = TestClient(_make_app(agent))
    resp = client.post("/perception/extract", json={"url": "not-a-url"}, headers=_HEADERS)
    assert resp.status_code == 400


def test_extract_valid_url_success() -> None:
    agent = MagicMock()
    agent.extract_text = AsyncMock(return_value=_make_action_result(output="Page content here"))
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/extract",
        json={"url": "https://example.com", "selector": "#main"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["text"] == "Page content here"
    assert data["char_count"] == len("Page content here")
    assert data["selector"] == "#main"


def test_extract_failure_returns_empty_text() -> None:
    agent = MagicMock()
    agent.extract_text = AsyncMock(return_value=_make_action_result(success=False, output="", error="404"))
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/extract",
        json={"url": "https://example.com/missing"},
        headers=_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is False
    assert data["text"] == ""
    assert data["char_count"] == 0
    assert data["error"] == "404"


# ── POST /perception/goal-with-image ─────────────────────────────────────────

def _vision_agent(analysis: str = "a revenue dashboard") -> MagicMock:
    agent = MagicMock()
    agent.has_vision = True
    agent.analyze_screenshot = AsyncMock(return_value=analysis)
    return agent


def test_goal_with_image_no_goal_service_returns_503() -> None:
    agent = _vision_agent()
    app = _make_app(agent)
    # No goal_service on app.state
    client = TestClient(app)
    resp = client.post(
        "/perception/goal-with-image",
        json={"goal": "Analyze this", "image_b64": "abc"},
        headers=_HEADERS,
    )
    assert resp.status_code == 503


def test_goal_with_image_with_image_b64() -> None:
    """The attached image is analysed and its description reaches the goal
    (it used to be dropped, leaving only an "[Image attached]" note)."""
    agent = _vision_agent("a revenue dashboard showing Q3 down 12%")
    app = _make_app(agent)
    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "g1", "status": "planning"})
    app.state.goal_service = mock_svc
    client = TestClient(app)
    resp = client.post(
        "/perception/goal-with-image",
        json={
            "goal": "Describe what you see",
            "image_b64": "base64encodedimage",
            "image_description": "dashboard screenshot",
        },
        headers=_HEADERS,
    )
    assert resp.status_code == 202
    data = resp.json()
    assert data["goal_id"] == "g1"
    assert data["has_visual_context"] is True
    assert data["original_goal"] == "Describe what you see"
    assert agent.analyze_screenshot.await_args.args[0] == "base64encodedimage"
    submitted_goal = mock_svc.submit_goal.await_args.kwargs["goal"]
    assert "Q3 down 12%" in submitted_goal


def test_goal_with_image_b64_without_vision_is_501() -> None:
    agent = MagicMock()
    agent.has_vision = False
    app = _make_app(agent)
    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock()
    app.state.goal_service = mock_svc
    client = TestClient(app)
    resp = client.post(
        "/perception/goal-with-image",
        json={"goal": "Describe", "image_b64": "abc"},
        headers=_HEADERS,
    )
    assert resp.status_code == 501
    mock_svc.submit_goal.assert_not_called()


def test_analyze_without_vision_is_501_not_fake_analysis() -> None:
    agent = MagicMock()
    agent.has_vision = False
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/analyze", json={"screenshot_b64": "abc"}, headers=_HEADERS
    )
    assert resp.status_code == 501


def test_analyze_provider_failure_is_502() -> None:
    agent = MagicMock()
    agent.has_vision = True
    agent.analyze_screenshot = AsyncMock(side_effect=RuntimeError("rate limited"))
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/analyze", json={"screenshot_b64": "abc"}, headers=_HEADERS
    )
    assert resp.status_code == 502
    assert "rate limited" in resp.json()["detail"]


def _real_agent(provider: Any = None) -> Any:
    from app.perception.browser_agent import BrowserAgent, BrowserResult

    agent = BrowserAgent(vision_provider=provider)
    agent.take_screenshot = AsyncMock(  # type: ignore[method-assign]
        return_value=BrowserResult(success=True, action="screenshot", screenshot_b64="img")
    )
    agent.extract_text = AsyncMock(  # type: ignore[method-assign]
        return_value=BrowserResult(success=True, action="extract_text", output="page text")
    )
    return agent


class _NonVisionProvider:
    def supports_vision(self) -> bool:
        return False


def test_batch_analyze_without_vision_is_501_not_fake_analysis() -> None:
    """Regression: each result came back success=True with the analysis
    "No vision provider configured."."""
    agent = _real_agent(_NonVisionProvider())
    client = TestClient(_make_app(agent))
    resp = client.post(
        "/perception/batch-analyze", json={"urls": ["https://example.com"]}, headers=_HEADERS
    )
    assert resp.status_code == 501
    assert "vision" in resp.json()["detail"]
    agent.take_screenshot.assert_not_called()


def test_batch_analyze_vision_failure_marks_result_failed() -> None:
    class _FailingVision:
        def supports_vision(self) -> bool:
            return True

        async def complete(self, request: Any) -> Any:
            raise RuntimeError("rate limited")

    client = TestClient(_make_app(_real_agent(_FailingVision())))
    resp = client.post(
        "/perception/batch-analyze", json={"urls": ["https://example.com"]}, headers=_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["succeeded"] == 0
    [result] = body["results"]
    assert result["success"] is False
    assert result["analysis"] == ""
    assert "rate limited" in result["error"]


def test_status_reports_vision_from_the_browser_agent_not_the_embedder() -> None:
    """An embedder without vision support must not advertise vision_available
    (the analyze endpoints would then answer 501)."""
    app = _make_app(_real_agent(_NonVisionProvider()))
    app.state.embedder = MagicMock()
    client = TestClient(app)
    resp = client.get("/perception/status", headers=_HEADERS)
    assert resp.json()["vision_available"] is False


def test_goal_with_image_url_vision_failure_injects_no_fake_analysis() -> None:
    agent = MagicMock()
    agent.has_vision = True
    agent.take_screenshot = AsyncMock(return_value=_make_screenshot_result(b64="screenshot"))
    agent.analyze_screenshot = AsyncMock(side_effect=RuntimeError("rate limited"))
    app = _make_app(agent)
    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "g4"})
    app.state.goal_service = mock_svc
    client = TestClient(app)
    resp = client.post(
        "/perception/goal-with-image",
        json={"goal": "Check this site", "image_url": "https://example.com"},
        headers=_HEADERS,
    )
    assert resp.status_code == 202
    submitted_goal = mock_svc.submit_goal.await_args.kwargs["goal"]
    assert "Page analysis" not in submitted_goal
    assert "rate limited" not in submitted_goal


def test_goal_with_image_with_invalid_image_url_returns_400() -> None:
    agent = MagicMock()
    app = _make_app(agent)
    mock_svc = MagicMock()
    app.state.goal_service = mock_svc
    client = TestClient(app)
    resp = client.post(
        "/perception/goal-with-image",
        json={"goal": "Analyze this", "image_url": "ftp://bad-protocol.com"},
        headers=_HEADERS,
    )
    assert resp.status_code == 400


def test_goal_with_image_without_image() -> None:
    agent = MagicMock()
    app = _make_app(agent)
    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "g2", "status": "planning"})
    app.state.goal_service = mock_svc
    client = TestClient(app)
    resp = client.post(
        "/perception/goal-with-image",
        json={"goal": "Plain goal no image"},
        headers=_HEADERS,
    )
    assert resp.status_code == 202
    assert resp.json()["has_visual_context"] is False


def test_goal_with_image_url_screenshot_success() -> None:
    agent = MagicMock()
    agent.take_screenshot = AsyncMock(return_value=_make_screenshot_result(b64="screenshot"))
    agent.analyze_screenshot = AsyncMock(return_value="Login page detected")
    app = _make_app(agent)
    mock_svc = MagicMock()
    mock_svc.submit_goal = AsyncMock(return_value={"goal_id": "g3"})
    app.state.goal_service = mock_svc
    client = TestClient(app)
    resp = client.post(
        "/perception/goal-with-image",
        json={"goal": "Check this site", "image_url": "https://example.com"},
        headers=_HEADERS,
    )
    assert resp.status_code == 202
    assert resp.json()["has_visual_context"] is True
