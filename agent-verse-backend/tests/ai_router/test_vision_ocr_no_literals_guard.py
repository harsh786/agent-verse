"""Guard: vision / OCR code never hardcodes a model or bypasses the dispatch.

Every vision / OCR call takes its model from the Model Registry
(``app.ai_router.resolve.resolve_vision`` / ``resolve_ocr``) and goes through
``ModelDispatchProvider`` (the model's own endpoint and key). This scans the
vision / OCR modules for vendor model literals, the literal model id
``"default"``, model-resolver calls with a literal fallback, and raw vendor SDK
clients. A hit fails the test unless it is in ``ALLOWLIST`` with a reason.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"

# Modules that pick or call a vision / OCR model.
VISION_OCR_MODULES = (
    "ai_router/resolve.py",
    "ai_router/selection.py",
    "providers/model_defaults.py",
    "ingestion/parsers/vision_parser.py",
    "ingestion/document_text.py",
    "perception/browser_agent.py",
    "perception/page_analyzer.py",
    "multimodal/pipeline.py",
    "rpa/executor.py",
    "workflow/steps/rpa_step.py",
    "ocr/**/*.py",
)

_VENDOR = (
    r"gpt-[0-9a-z]|claude-|gemini-|glm-ocr|llava|qwen2(\.5)?-?vl|llama3\.2-vision|pixtral"
    r"|nemoretriever|nvidia/[a-z]|meta/llama|whisper-1"
)
PATTERNS: dict[str, re.Pattern[str]] = {
    "vendor model literal": re.compile(rf"[\"'](?:{_VENDOR})[^\"']*[\"']", re.IGNORECASE),
    "literal model id 'default'": re.compile(r"model\s*=\s*[\"']default[\"']"),
    "resolver with a literal fallback": re.compile(
        r"(?:configured_vision_model|configured_ocr_model|resolve_vision_model|resolve_ocr_model)"
        r"\(\s*[\"'][^\"']+[\"']"
    ),
    "raw vendor SDK client": re.compile(
        r"\b(?:Async)?OpenAI\(|\b(?:Async)?Anthropic\(|async_openai_client\(|\bopenai_client\("
    ),
    "env-only vendor vision model": re.compile(r"ANTHROPIC_VISION_MODEL"),
}

# (relative path, pattern name, substring of the line) → reason.
ALLOWLIST: dict[tuple[str, str, str], str] = {
    ("multimodal/pipeline.py", "vendor model literal", '"extractor": "whisper-1"'): (
        "speech-to-text metadata label (STT resolver is a separate capability/branch)"
    ),
    ("providers/model_defaults.py", "vendor model literal", 'configured_audio_model(fallback'): (
        "speech-to-text env default (STT resolver is a separate capability/branch)"
    ),
}


def _files() -> list[Path]:
    out: list[Path] = []
    for pattern in VISION_OCR_MODULES:
        out.extend(sorted(APP.glob(pattern)))
    return out


def _code_lines(path: Path) -> list[tuple[int, str, str]]:
    """``(lineno, line, code)`` for lines outside docstrings, trailing comment cut."""
    out: list[tuple[int, str, str]] = []
    in_doc = False
    for lineno, line in enumerate(path.read_text().splitlines(), 1):
        quotes = line.count('"""') + line.count("'''")
        if in_doc or (quotes and line.strip().startswith(('"""', "'''", 'r"""'))):
            if quotes % 2 == 1:
                in_doc = not in_doc
            continue
        if line.strip().startswith("#"):
            continue
        out.append((lineno, line, re.split(r"\s+#\s", line, maxsplit=1)[0]))
    return out


def test_the_scanned_modules_exist() -> None:
    files = {str(p.relative_to(APP)) for p in _files()}
    for module in VISION_OCR_MODULES:
        if "*" not in module:
            assert module in files, f"guard scans a module that no longer exists: {module}"
    assert any(f.startswith("ocr/") for f in files)


def test_no_vision_ocr_model_literals_or_raw_sdk_clients() -> None:
    hits: list[str] = []
    used: set[tuple[str, str, str]] = set()
    for path in _files():
        rel = str(path.relative_to(APP))
        for lineno, line, code in _code_lines(path):
            for name, pattern in PATTERNS.items():
                if not pattern.search(code):
                    continue
                allowed = next(
                    (k for k in ALLOWLIST if k[0] == rel and k[1] == name and k[2] in line), None
                )
                if allowed is not None:
                    used.add(allowed)
                    continue
                hits.append(f"{rel}:{lineno}: {name}: {line.strip()}")
    assert not hits, (
        "vision/OCR code must resolve its model through app.ai_router.resolve "
        "(resolve_vision / resolve_ocr) and call it through ModelDispatchProvider "
        "(dispatch_provider) — or add an ALLOWLIST entry with a reason:\n" + "\n".join(hits)
    )
    stale = set(ALLOWLIST) - used
    assert not stale, f"stale ALLOWLIST entries (remove them): {sorted(stale)}"


def test_multimodal_vision_selection_has_no_per_provider_literals() -> None:
    from app.ai_router import model_orchestrator as mo
    from app.org.model_gateway import MODEL_PROFILES

    assert not hasattr(mo, "_PROVIDER_VISION_MODEL")
    for modality in ("image", "video"):
        assert mo._MULTIMODAL_MODELS[modality]["extractor"] == "", modality
    vision_profiles = [p for p in MODEL_PROFILES.values() if p.vision]
    assert vision_profiles
    for profile in vision_profiles:
        assert (profile.primary, profile.fallback) == ("", ""), profile


def test_the_patterns_catch_the_old_offenders() -> None:
    offenders = {
        "vendor model literal": [
            'ocr_model = resolve_vision_model_x("gpt-4o")',
            'or "claude-3-5-sonnet-20241022",',
            'model=_configured_vision_model("claude-opus-4-5"),',
        ],
        "literal model id 'default'": ['model="default",'],
        "resolver with a literal fallback": ['resolve_vision_model("gpt-4o")'],
        "raw vendor SDK client": [
            "client = anthropic.AsyncAnthropic()",
            "client = async_openai_client()",
            "AsyncOpenAI(base_url=os.getenv('OPENAI_BASE_URL'))",
        ],
        "env-only vendor vision model": ['os.getenv("ANTHROPIC_VISION_MODEL")'],
    }
    for name, lines in offenders.items():
        for line in lines:
            assert PATTERNS[name].search(line), (name, line)
