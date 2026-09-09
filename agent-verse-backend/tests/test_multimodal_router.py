# tests/test_multimodal_router.py
"""Multimodal (PDF/audio/vision) + AI model router tests."""
from __future__ import annotations

import base64

import pytest

# ── PDF PARSER ────────────────────────────────────────────────────────────────

def test_pdf_parser_with_text():
    from app.ingestion.parsers.pdf_parser import PDFParser

    parser = PDFParser()
    result = parser.parse_text(
        "Chapter 1: Introduction to Machine Learning.\n\nMachine learning is a field of AI.",
        source_name="ml_guide.pdf",
    )
    chunks = result.to_chunks()
    assert len(chunks) >= 1
    assert any("Machine learning" in c["content"] for c in chunks)


def test_pdf_parser_handles_empty():
    from app.ingestion.parsers.pdf_parser import PDFParser

    parser = PDFParser()
    result = parser.parse_bytes(b"", source_name="empty.pdf")
    # Empty bytes → no pages (graceful, no exception)
    assert result.pages == [] or result.error is not None


def test_pdf_parser_chunks_contain_source_name():
    from app.ingestion.parsers.pdf_parser import PDFParser

    parser = PDFParser()
    result = parser.parse_text(
        "First paragraph of content.\n\nSecond paragraph of content.",
        source_name="myfile.pdf",
    )
    chunks = result.to_chunks()
    assert all(c["source_name"] == "myfile.pdf" for c in chunks)
    assert all(c["content_type"] == "pdf" for c in chunks)


def test_pdf_parse_result_full_text():
    from app.ingestion.parsers.pdf_parser import PDFPage, PDFParser, PDFParseResult

    result = PDFParseResult(
        source_name="test.pdf",
        pages=[
            PDFPage(page_number=1, content="Hello world."),
            PDFPage(page_number=2, content="Second page content."),
        ],
    )
    assert "Hello world." in result.full_text
    assert "Second page content." in result.full_text


# ── AUDIO PARSER ──────────────────────────────────────────────────────────────

async def test_audio_parser_fallback_no_key():
    from unittest.mock import AsyncMock, patch

    from app.ingestion.parsers.audio_parser import AudioParser

    parser = AudioParser()
    with patch.object(parser, "_transcribe_with_whisper", AsyncMock(side_effect=Exception("No key"))):
        result = await parser.parse_bytes(b"fake_audio", "test.mp3", "audio/mpeg")
    # Graceful fallback — must not crash
    assert result is not None
    assert result.error is not None


async def test_audio_parser_empty_bytes():
    from app.ingestion.parsers.audio_parser import AudioParser

    parser = AudioParser()
    result = await parser.parse_bytes(b"", "silent.mp3", "audio/mpeg")
    assert result.error is not None


def test_audio_parser_segment_chunking():
    from app.ingestion.parsers.audio_parser import AudioParseResult, AudioSegment

    result = AudioParseResult(
        source_name="test.mp3",
        transcript="Hello world. This is a test.",
        segments=[
            AudioSegment(start=0.0, end=5.0, text="Hello world."),
            AudioSegment(start=5.0, end=10.0, text="This is a test."),
        ],
    )
    chunks = result.to_chunks(chunk_duration_seconds=30.0)
    assert len(chunks) >= 1
    assert all("start_time" in c for c in chunks)
    assert all(c["content_type"] == "audio" for c in chunks)


def test_audio_parser_segment_chunking_splits_correctly():
    from app.ingestion.parsers.audio_parser import AudioParseResult, AudioSegment

    # 3 segments: first two in window 0-30s, third past 30s
    result = AudioParseResult(
        source_name="recording.wav",
        transcript="A B C",
        segments=[
            AudioSegment(start=0.0, end=10.0, text="A"),
            AudioSegment(start=10.0, end=20.0, text="B"),
            AudioSegment(start=35.0, end=45.0, text="C"),
        ],
    )
    chunks = result.to_chunks(chunk_duration_seconds=30.0)
    assert len(chunks) == 2
    assert "A" in chunks[0]["content"] and "B" in chunks[0]["content"]
    assert "C" in chunks[1]["content"]


# ── VISION PARSER ─────────────────────────────────────────────────────────────

async def test_vision_parser_fallback_no_key():
    from unittest.mock import AsyncMock, patch

    from app.ingestion.parsers.vision_parser import VisionParser

    # 1x1 white PNG
    tiny_png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI6QAAAABJRU5ErkJggg=="
    )
    parser = VisionParser()
    with (
        patch.object(parser, "_describe_with_openai", AsyncMock(side_effect=Exception("No key"))),
        patch.object(parser, "_describe_with_anthropic", AsyncMock(side_effect=Exception("No key"))),
    ):
        result = await parser.parse_image_bytes(tiny_png, "test.png")
    # Must return placeholder, not crash
    assert result is not None
    assert result.description  # Non-empty placeholder


async def test_vision_parser_empty_bytes():
    from app.ingestion.parsers.vision_parser import VisionParser

    parser = VisionParser()
    result = await parser.parse_image_bytes(b"", "empty.png")
    assert result.error is not None


def test_vision_parser_result_chunks():
    from app.ingestion.parsers.vision_parser import VisionParseResult

    result = VisionParseResult(
        source_name="diagram.png",
        description="A flowchart showing the ML pipeline stages.",
    )
    chunks = result.to_chunks()
    assert len(chunks) == 1
    assert chunks[0]["content_type"] == "image"
    assert "flowchart" in chunks[0]["content"]
    assert chunks[0]["source_name"] == "diagram.png"


def test_vision_parser_fallback_description():
    from app.ingestion.parsers.vision_parser import VisionParseResult

    result = VisionParseResult(source_name="noname.png", description="")
    chunks = result.to_chunks()
    # Empty description → falls back to "[Image: noname.png]"
    assert len(chunks) == 1
    assert "noname.png" in chunks[0]["content"]


# ── CONTENT CLASSIFIER ────────────────────────────────────────────────────────

def test_content_classifier_all_types():
    from app.ingestion.content_classifier import ContentClassifier, ContentType

    clf = ContentClassifier()
    tests = [
        ("def foo(): return 1\nimport os", ContentType.CODE),
        ("<html><body><h1>Title</h1></body></html>", ContentType.HTML),
        ('{"key": "value", "list": [1, 2]}', ContentType.JSON),
        ("# Heading\n\n## Sub\n\n- bullet", ContentType.MARKDOWN),
        ("This is plain text about machine learning.", ContentType.TEXT),
    ]
    for content, expected in tests:
        result = clf.classify(content)
        assert result == expected, f"Expected {expected} for content, got {result}"


def test_content_classifier_by_filename():
    from app.ingestion.content_classifier import ContentClassifier, ContentType

    clf = ContentClassifier()
    assert clf.classify_by_filename("report.pdf") == ContentType.PDF
    assert clf.classify_by_filename("script.py") == ContentType.CODE
    assert clf.classify_by_filename("photo.jpg") == ContentType.IMAGE
    assert clf.classify_by_filename("recording.mp3") == ContentType.AUDIO
    assert clf.classify_by_filename("video.mp4") == ContentType.VIDEO
    assert clf.classify_by_filename("data.csv") == ContentType.CSV
    assert clf.classify_by_filename("doc.docx") == ContentType.DOCX


def test_content_classifier_code_variants():
    from app.ingestion.content_classifier import ContentClassifier, ContentType

    clf = ContentClassifier()
    assert clf.classify_by_filename("app.js") == ContentType.CODE
    assert clf.classify_by_filename("service.ts") == ContentType.CODE
    assert clf.classify_by_filename("main.go") == ContentType.CODE
    assert clf.classify_by_filename("query.sql") == ContentType.CODE


# ── AI MODEL ROUTER ───────────────────────────────────────────────────────────

def test_model_orchestrator_adapter_all_roles():
    from app.ai_router.model_orchestrator import ModelOrchestratorAdapter

    adapter = ModelOrchestratorAdapter(default_tier="medium")
    for role in ["planning", "execution", "verification", "reflection", "think", "classification"]:
        model = adapter.model_for(role)
        assert isinstance(model, str)
        assert len(model) > 3, f"Expected non-trivial model name for role={role}, got '{model}'"


def test_model_orchestrator_tier_high():
    from app.ai_router.model_orchestrator import ModelOrchestratorAdapter

    adapter = ModelOrchestratorAdapter(default_tier="high")
    model = adapter.model_for("planning")
    assert len(model) > 0
    # High tier should produce a premium model (not the cheapest)
    assert model != "gpt-4o-mini"


def test_model_orchestrator_tier_low():
    from app.ai_router.model_orchestrator import ModelOrchestratorAdapter

    adapter = ModelOrchestratorAdapter(default_tier="low")
    model = adapter.model_for("planning")
    # Low tier should use gpt-4o-mini
    assert model == "gpt-4o-mini"


def test_ai_router_model_selection():
    from app.ai_router.models import TaskType
    from app.ai_router.router import AIRouter, ai_router

    result = ai_router.select_model(TaskType.PLANNING, "t1")
    # May be None if no models configured in registry, both are valid
    if result is not None:
        assert hasattr(result, "model_id")
        assert hasattr(result, "provider")


def test_ai_router_cheapest_routing():
    from app.ai_router.models import ModelRoutePolicy, RoutingMode, TaskType
    from app.ai_router.registry import model_registry
    from app.ai_router.router import AIRouter

    router = AIRouter()
    # Set a cheapest policy for test tenant
    model_registry.set_route_policy(
        "test_cheapest_t",
        TaskType.PLANNING,
        ModelRoutePolicy(
            task_type=TaskType.PLANNING,
            routing_mode=RoutingMode.CHEAPEST,
        ),
    )
    result = router.select_model(TaskType.PLANNING, "test_cheapest_t")
    # Must not raise; result may be None if no candidates match


def test_model_router_openai_defaults():
    from app.agent.model_router import ModelRouter

    router = ModelRouter("openai")
    assert router.model_for("planning") in ("gpt-5.2", "gpt-4o", "gpt-4o-mini", "")
    # Execution uses cheaper model
    assert router.model_for("execution") == "gpt-4o-mini"


def test_model_router_anthropic_defaults():
    from app.agent.model_router import ModelRouter

    router = ModelRouter("anthropic")
    planning_model = router.model_for("planning")
    assert "claude" in planning_model.lower()


def test_model_router_model_for_goal_downgrade():
    from app.agent.model_router import ModelRouter

    router = ModelRouter("openai")
    # Simple goal → downgrade planning to execution model
    simple_model = router.model_for_goal("planning", "list all Jira tickets")
    complex_model = router.model_for_goal("planning", "architect and implement distributed cache")
    # For simple goals, planning model downgrades to execution model (gpt-4o-mini)
    assert simple_model == "gpt-4o-mini"
    # Complex goals keep the planning model
    assert complex_model == "gpt-5.2"


def test_multi_model_orchestrator_budget_downgrade():
    from app.agent.pattern_config import Complexity, GoalProperties, PatternConfig, RiskLevel
    from app.ai_router.model_orchestrator import ModelOrchestrator

    orchestrator = ModelOrchestrator()
    config = PatternConfig(
        goal_properties=GoalProperties(complexity=Complexity.EXPERT, risk=RiskLevel.LOW),
        model_planner="",
        model_executor="",
        model_verifier="",
        model_classifier="",
    )

    # Normal: expert complexity → high tier
    normal = orchestrator.select_models(config, budget_spent_ratio=0.0)
    # Over budget (>90%): should downgrade to low
    downgraded = orchestrator.select_models(config, budget_spent_ratio=0.95)

    assert normal.quality_tier == "high"
    assert downgraded.quality_tier == "low"


def test_multi_model_orchestrator_medium_budget_downgrade():
    from app.agent.pattern_config import Complexity, GoalProperties, PatternConfig, RiskLevel
    from app.ai_router.model_orchestrator import ModelOrchestrator

    orchestrator = ModelOrchestrator()
    config = PatternConfig(
        goal_properties=GoalProperties(complexity=Complexity.EXPERT, risk=RiskLevel.LOW),
        model_planner="",
        model_executor="",
        model_verifier="",
        model_classifier="",
    )
    # 75-90% budget: high → medium
    partial = orchestrator.select_models(config, budget_spent_ratio=0.80)
    assert partial.quality_tier == "medium"
