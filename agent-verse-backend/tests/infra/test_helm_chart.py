"""Tests that Helm chart templates are valid YAML."""
import pytest
from pathlib import Path


HELM_DIR = Path(__file__).parents[2] / "helm" / "agentverse"


def test_chart_yaml_exists():
    assert (HELM_DIR / "Chart.yaml").exists()


def test_values_yaml_exists():
    assert (HELM_DIR / "values.yaml").exists()


def test_templates_directory_exists():
    assert (HELM_DIR / "templates").is_dir()


def test_backend_deployment_template_exists():
    assert (HELM_DIR / "templates" / "deployment.yaml").exists()


def test_values_yaml_is_valid():
    import yaml
    content = (HELM_DIR / "values.yaml").read_text()
    data = yaml.safe_load(content)
    assert "backend" in data
    assert "worker" in data


def test_helpers_tpl_exists():
    assert (HELM_DIR / "templates" / "_helpers.tpl").exists()


def test_notes_txt_exists():
    assert (HELM_DIR / "templates" / "NOTES.txt").exists()
