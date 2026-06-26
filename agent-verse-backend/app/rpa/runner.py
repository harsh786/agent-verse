"""RPA runner interface, local adapter, and tool execution dispatch."""

from __future__ import annotations

from typing import Any, Protocol

from app.rpa.artifacts import RPAArtifactStore
from app.rpa.session import RPASession
from app.rpa.tools import RPA_TOOLS

RPAExecutionResult = dict[str, str | bool | None]


class RPARunner(Protocol):
    """Adapter interface for local and browser-backed RPA implementations."""

    def open_url(self, url: str) -> str:
        """Open a URL and return a user-facing result string."""

    def type(self, selector: str, text: str) -> str:
        """Type text into an element and return a user-facing result string."""

    def click(self, *, selector: str | None = None, text: str | None = None) -> str:
        """Click an element by selector or text and return a result string."""

    def extract_text(self, selector: str | None = None) -> str:
        """Extract page text and return it."""

    def screenshot(self, *, artifact_store: RPAArtifactStore, name: str) -> str:
        """Capture a screenshot artifact and return its URI."""


class LocalRPARunner:
    """CI-safe RPA adapter with deterministic in-memory page state.

    This adapter does not pretend to automate a real browser. It provides the same
    runner interface agents use so a future Playwright-backed adapter can replace
    it without changing RPA tool names, arguments, or execution results.
    """

    def __init__(self, session: RPASession) -> None:
        self.session = session
        self.typed_values: dict[str, str] = {}
        self.clicked_targets: list[str] = []

    def open_url(self, url: str) -> str:
        self.session.current_url = url
        self.session.status = "running"
        return f"Opened {url}"

    def type(self, selector: str, text: str) -> str:
        self.typed_values[selector] = text
        self.session.status = "running"
        return f"Typed into {selector}"

    def click(self, *, selector: str | None = None, text: str | None = None) -> str:
        if selector is not None:
            target = selector
        elif text is not None:
            target = f"text:{text}"
        else:
            target = "<missing>"
        self.clicked_targets.append(target)
        self.session.status = "running"
        return f"Clicked {target}"

    def extract_text(self, selector: str | None = None) -> str:
        selected = selector or "<page>"
        url = self.session.current_url or "<none>"
        values = _format_items(self.typed_values)
        clicks = ",".join(self.clicked_targets) if self.clicked_targets else "<none>"
        return f"LocalRPARunner text selector={selected} url={url} values={values} clicks={clicks}"

    def screenshot(self, *, artifact_store: RPAArtifactStore, name: str) -> str:
        url = self.session.current_url or "<none>"
        content = (
            b"agentverse-local-rpa-screenshot\n"
            + f"session={self.session.session_id}\n".encode()
            + f"url={url}\n".encode()
        )
        artifact = artifact_store.write_bytes(
            goal_id=self.session.goal_id,
            name=name,
            content=content,
        )
        self.session.screenshots.append(artifact.uri)
        self.session.status = "running"
        return artifact.uri


def execute_rpa_tool(
    tool_name: str,
    arguments: dict[str, Any],
    session: RPASession,
    runner: RPARunner,
    artifact_store: RPAArtifactStore,
) -> RPAExecutionResult:
    """Execute a built-in RPA tool and return a structured agent-facing result."""

    if tool_name not in {str(tool["name"]) for tool in RPA_TOOLS}:
        return _failure(
            error=f"Unknown RPA tool: {tool_name}",
            current_url=session.current_url,
        )

    try:
        if tool_name == "rpa_open_url":
            output = runner.open_url(_required_str(arguments, "url"))
            return _result(success=True, output=output, current_url=session.current_url)
        if tool_name == "rpa_type":
            output = runner.type(
                _required_str(arguments, "selector"),
                _required_str(arguments, "text"),
            )
            return _result(success=True, output=output, current_url=session.current_url)
        if tool_name == "rpa_click":
            selector = _optional_str(arguments, "selector")
            text = _optional_str(arguments, "text")
            if not selector and not text:
                raise ValueError("Missing required RPA argument: selector or text")
            output = runner.click(
                selector=selector,
                text=text,
            )
            return _result(success=True, output=output, current_url=session.current_url)
        if tool_name == "rpa_extract_text":
            output = runner.extract_text(_optional_str(arguments, "selector"))
            return _result(success=True, output=output, current_url=session.current_url)

        artifact_uri = runner.screenshot(
            artifact_store=artifact_store,
            name=_optional_str(arguments, "name") or "screenshot.png",
        )
    except ValueError as exc:
        return _failure(error=str(exc), current_url=session.current_url)
    return _result(
        success=True,
        output="Screenshot captured",
        artifact_uri=artifact_uri,
        current_url=session.current_url,
    )


def _failure(
    *,
    error: str,
    current_url: str | None,
) -> RPAExecutionResult:
    return {
        "success": False,
        "error": error,
        "artifact_uri": None,
        "current_url": current_url,
    }


def _format_items(items: dict[str, str]) -> str:
    if not items:
        return "<none>"
    return ",".join(f"{key}={value}" for key, value in sorted(items.items()))


def _optional_str(arguments: dict[str, Any], key: str) -> str | None:
    value = arguments.get(key)
    if value is None:
        return None
    return str(value)


def _required_str(arguments: dict[str, Any], key: str) -> str:
    value = _optional_str(arguments, key)
    if value is None or value == "":
        raise ValueError(f"Missing required RPA argument: {key}")
    return value


def _result(
    *,
    success: bool,
    output: str,
    current_url: str | None,
    artifact_uri: str | None = None,
) -> RPAExecutionResult:
    return {
        "success": success,
        "output": output,
        "artifact_uri": artifact_uri,
        "current_url": current_url,
    }
