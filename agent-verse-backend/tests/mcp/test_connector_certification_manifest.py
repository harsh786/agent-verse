from app.mcp.certification_manifest import CONNECTOR_CERTIFICATION_TARGETS

SUPPORTED_AUTH_MODES = {"basic", "bearer", "connection_string", "custom_header", "oauth_ac"}


def test_manifest_contains_core_connector_wave() -> None:
    required = {
        "jira",
        "github",
        "slack",
        "google_workspace",
        "hubspot",
        "stripe",
        "datadog",
        "sentry",
        "aws",
        "postgres",
    }

    assert required.issubset(CONNECTOR_CERTIFICATION_TARGETS)


def test_manifest_entries_have_required_fields() -> None:
    for key, entry in CONNECTOR_CERTIFICATION_TARGETS.items():
        assert entry["display_name"]
        assert entry["category"]

        auth_modes = entry["auth_modes"]
        assert auth_modes
        assert set(auth_modes) <= SUPPORTED_AUTH_MODES, key

        assert "required_secrets" in entry, key
        required_secrets = entry["required_secrets"]
        assert isinstance(required_secrets, list), key
        assert required_secrets, key
        assert all(isinstance(secret, str) and secret for secret in required_secrets), key

        assert entry["read_tool"]
        assert isinstance(entry["read_arguments"], dict)
        assert entry["expected_artifact_kind"] in {"table", "cards", "json", "text"}
        assert isinstance(entry["live_env"], list), key
        assert all(isinstance(env_var, str) for env_var in entry["live_env"]), key
