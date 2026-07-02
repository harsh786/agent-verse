from app.services.result_artifacts import build_result_artifact


def test_build_result_artifact_accepts_positional_arguments() -> None:
    artifact = build_result_artifact("fetch jira", "complete", [])

    assert artifact["title"] == "fetch jira"
    assert artifact["status"] == "empty"


def test_builds_jira_table_artifact_from_tool_output() -> None:
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "server_id": "jira-1",
            "success": True,
            "output": {
                "issues": [
                    {"key": "PCF-58608", "summary": "Deployment fix", "status": "Closed"},
                    {"key": "OPP-32778", "summary": "Invoice tables", "status": "Open"},
                ]
            },
        },
        {"type": "verification_done", "success": True, "reason": "Jira returned issues."},
        {"type": "goal_complete"},
    ]

    artifact = build_result_artifact(goal="fetch jira", status="complete", events=events)

    assert artifact["kind"] == "table"
    assert artifact["status"] == "success"
    assert artifact["summary"] == "Found 2 Jira issues."
    assert artifact["tables"][0]["rows"][0]["key"] == "PCF-58608"
    assert artifact["evidence"]["tools"][0]["name"] == "jira_search_issues"


def test_builds_jira_table_artifact_from_string_output() -> None:
    output = (
        "{'issues': [{'key': 'PCF-58608', 'summary': 'Deployment fix', "
        "'status': 'Closed'}]}"
    )
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": True,
            "output": output,
        },
    ]

    artifact = build_result_artifact(goal="fetch jira", status="complete", events=events)

    assert artifact["kind"] == "table"
    assert artifact["summary"] == "Found 1 Jira issue."
    assert artifact["tables"][0]["rows"] == [
        {
            "key": "PCF-58608",
            "summary": "Deployment fix",
            "status": "Closed",
            "priority": "",
            "updated": "",
        }
    ]


def test_builds_jira_table_artifact_from_json_string_output() -> None:
    output = (
        '{"issues": [{"key": "PCF-58608", "summary": null, "status": "Closed", '
        '"flagged": true, "blocked": false}]}'
    )
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": True,
            "output": output,
        },
    ]

    artifact = build_result_artifact(goal="fetch jira", status="complete", events=events)

    assert artifact["kind"] == "table"
    assert artifact["summary"] == "Found 1 Jira issue."
    assert artifact["tables"][0]["rows"] == [
        {
            "key": "PCF-58608",
            "summary": None,
            "status": "Closed",
            "priority": "",
            "updated": "",
        }
    ]


def test_builds_empty_jira_table_artifact() -> None:
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": True,
            "output": {"issues": []},
        },
    ]

    artifact = build_result_artifact(goal="fetch jira", status="complete", events=events)

    assert artifact["kind"] == "table"
    assert artifact["status"] == "empty"
    assert artifact["summary"] == "Found 0 Jira issues."
    assert artifact["tables"][0]["rows"] == []


def test_uses_most_recent_successful_jira_event_for_table_rows() -> None:
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": False,
            "output": {"issues": []},
        },
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": True,
            "output": {"issues": [{"key": "PCF-58608", "summary": "Deployment fix"}]},
        },
    ]

    artifact = build_result_artifact(goal="fetch jira", status="complete", events=events)

    assert artifact["status"] == "success"
    assert artifact["summary"] == "Found 1 Jira issue."
    assert artifact["tables"][0]["rows"][0]["key"] == "PCF-58608"


def test_uses_most_recent_jira_event_and_failed_status_when_no_jira_call_succeeds() -> None:
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": False,
            "output": {"issues": [{"key": "OLD-1", "summary": "Stale failure"}]},
        },
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": False,
            "output": {"issues": []},
        },
    ]

    artifact = build_result_artifact(goal="fetch jira", status="complete", events=events)

    assert artifact["status"] == "failed"
    assert artifact["summary"] == "Found 0 Jira issues."
    assert artifact["tables"][0]["rows"] == []


def test_jira_table_artifact_includes_columns_and_downloads() -> None:
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": True,
            "output": {"issues": [{"key": "PCF-58608"}]},
        },
    ]

    artifact = build_result_artifact(goal="fetch jira", status="complete", events=events)

    assert [column["key"] for column in artifact["tables"][0]["columns"]] == [
        "key",
        "summary",
        "status",
        "priority",
        "updated",
    ]
    assert artifact["downloads"] == ["json", "csv", "markdown"]


def test_jira_table_artifact_columns_and_downloads_are_mutation_isolated() -> None:
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": True,
            "output": {"issues": [{"key": "PCF-58608"}]},
        },
    ]
    artifact = build_result_artifact(goal="fetch jira", status="complete", events=events)

    artifact["downloads"].append("xlsx")
    artifact["tables"][0]["columns"][0]["key"] = "mutated"
    artifact["tables"][0]["columns"].append({"key": "extra", "label": "Extra"})

    next_artifact = build_result_artifact(goal="fetch jira", status="complete", events=events)

    assert next_artifact["downloads"] == ["json", "csv", "markdown"]
    assert [column["key"] for column in next_artifact["tables"][0]["columns"]] == [
        "key",
        "summary",
        "status",
        "priority",
        "updated",
    ]


def test_builds_generic_text_artifact_from_step_output() -> None:
    events = [
        {"type": "step_complete", "output": "Generated a deployment report."},
        {"type": "verification_done", "reason": "Report exists."},
    ]

    artifact = build_result_artifact(goal="write report", status="complete", events=events)

    assert artifact["kind"] == "text"
    assert artifact["status"] == "success"
    assert artifact["summary"] == "Generated a deployment report."


def test_builds_generic_text_artifact_from_zero_step_output() -> None:
    events = [
        {"type": "step_complete", "output": 0},
        {"type": "verification_done", "reason": "Step produced zero."},
    ]

    artifact = build_result_artifact(goal="count results", status="complete", events=events)

    assert artifact["kind"] == "text"
    assert artifact["status"] == "success"
    assert artifact["summary"] == "0"


def test_builds_generic_empty_artifact_without_output() -> None:
    artifact = build_result_artifact(goal="do work", status="complete", events=[])

    assert artifact["kind"] == "empty"
    assert artifact["status"] == "empty"
    assert artifact["summary"] == "No structured result was produced."


def test_builds_jira_table_from_tool_output_field_not_output_string() -> None:
    """tool_output raw dict takes priority over the sanitized output string."""
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": True,
            "output": "[dict omitted from event payload]",
            "tool_output": {
                "total": 10,
                "issues": [
                    {"key": "OPP-1", "summary": "Bug fix", "status": "Open",
                     "priority": "High", "updated": "2026-07-01"},
                    {"key": "OPP-2", "summary": "Feature", "status": "Closed",
                     "priority": "Medium", "updated": "2026-07-01"},
                ],
            },
        }
    ]
    artifact = build_result_artifact(goal="find jira", status="complete", events=events)
    assert artifact["status"] == "success"
    assert artifact["tables"][0]["rows"][0]["key"] == "OPP-1"
    assert len(artifact["tables"][0]["rows"]) == 2
    assert artifact["metrics"][0] == {"label": "Issues", "value": 2}


def test_builds_jira_table_falls_back_to_output_when_tool_output_absent() -> None:
    """Backward compat: if tool_output not present, parse output dict as before."""
    events = [
        {
            "type": "tool_call_complete",
            "tool": "jira_search_issues",
            "success": True,
            "output": {"issues": [{"key": "PCF-1", "summary": "Old path"}]},
        }
    ]
    artifact = build_result_artifact(goal="find jira", status="complete", events=events)
    assert artifact["tables"][0]["rows"][0]["key"] == "PCF-1"
