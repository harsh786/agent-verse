# Multimodal and Multi-Model Routing

**Date:** 2026-07-08

## Purpose

This document explains how AgentVerse handles different content types and chooses different AI models for planning, execution, verification, embeddings, vision, audio, video, OCR, reranking, and judging.

## Multimodal vs Multi-Model

**Multimodal** means different input/content types:

```text
text, PDF, DOCX, HTML, markdown, code, image, audio, video, CSV, JSON, web pages, screenshots
```

**Multi-model** means different AI models for different jobs:

```text
planner, executor, verifier, judge, embedder, vision extractor, audio transcriber, video understanding, OCR, reranker
```

They connect like this:

```text
content type detected
  -> parser/extractor selected
  -> embedding model selected
  -> chunks stored in knowledge/RAG
  -> agent retrieves context
  -> model router selects planner/executor/verifier/vision model
```

## Multimodal Models

Implemented in `app/multimodal/models.py`.

Supported modalities:

```python
class Modality:
    TEXT
    IMAGE
    PDF
    AUDIO
    VIDEO
    OCR
```

Extracted content is normalized as `ExtractedSpan`:

```python
ExtractedSpan(
    content="...",
    modality=Modality.IMAGE,
    source_page=3,
    source_frame=120,
    timestamp_start=65.0,
    timestamp_end=95.0,
    bounding_box={"x": 0.1, "y": 0.2, "width": 0.4, "height": 0.1},
    confidence=0.9,
    language="en",
)
```

## Multimodal Pipeline

Implemented in `app/multimodal/pipeline.py`.

Entry points:

```python
ingest_text()
ingest_image()
ingest_pdf()
ingest_audio()
ingest_video()
```

Each returns an `AssetIngestionJob` containing extracted spans.

## Image Flow

Implemented in:

- `app/ingestion/parsers/vision_parser.py`
- `app/multimodal/pipeline.py`
- `app/perception/multimodal.py`

Workflow:

```text
image bytes
  -> MIME detection
  -> base64
  -> GPT-4o or Claude Vision
  -> detailed description
  -> chunk
  -> embedding
  -> knowledge store
```

Use cases:

- screenshot debugging
- architecture diagram explanation
- UI state analysis
- document image extraction

## Audio Flow

Implemented in `app/ingestion/parsers/audio_parser.py`.

Workflow:

```text
audio bytes
  -> OpenAI Whisper-1
  -> verbose_json transcript
  -> timestamped segments
  -> 60-second chunks
  -> text embedding
```

Use cases:

- meeting summary
- support call analysis
- sales call objection mining
- voice notes to tasks

## Video Flow

Implemented in `app/ingestion/parsers/video_parser.py`.

Workflow today:

```text
video bytes
  -> ffmpeg extracts audio
  -> Whisper transcribes audio
  -> transcript chunks
  -> embeddings
```

Future/full workflow:

```text
video bytes
  -> keyframes
  -> Gemini 2.5 Pro / video model
  -> scene descriptions
  -> transcript + visual chunks
```

Current limitation: visual scene understanding is scaffolded but not fully wired.

## PDF Flow

Implemented in `app/ingestion/parsers/pdf_parser.py`.

Fallback chain:

```text
PyMuPDF
  -> pdfminer.six
  -> raw text decode fallback
```

Metadata preserved:

- page number
- width/height
- table/image hints

## Browser and Perception Flow

Implemented in:

- `app/perception/browser_agent.py`
- `app/perception/page_analyzer.py`
- `app/rpa/`

Workflow:

```text
URL
  -> BrowserAgent opens page
  -> screenshot captured
  -> optional vision model analysis
  -> DOM text extraction
  -> PageAnalysis context block
  -> planner prompt
```

RPA screenshot memory:

```text
rpa_screenshot
  -> visual analysis
  -> LongTermMemory.store_rpa_extraction
  -> embedding
  -> future retrieval
```

## AgentGraph Visual Context

`AgentGraph._node_plan` injects:

```python
image_context = agent_state.context.get("image_context", "")
if image_context:
    extra_parts.append(f"[Visual context]\n{image_context}")
```

This lets the planner use screenshots and image descriptions when planning.

## Direct Vision Messages

`app/providers/base.py` supports image content:

```python
Message(
    role="user",
    content=[{"type": "text", "text": "Analyze this image"}, ...],
    image_data="base64..."
)
```

Provider support:

- OpenAI: image URL content blocks
- Anthropic: base64 image source blocks
- Gemini: vision-capable model check

## AI Router

Implemented in:

- `app/ai_router/models.py`
- `app/ai_router/registry.py`
- `app/ai_router/router.py`

Capabilities:

```text
TEXT_GENERATION
STRUCTURED_OUTPUT
TOOL_USE
VISION
EMBEDDING
RERANK
OCR
SPEECH_TO_TEXT
TEXT_TO_SPEECH
VIDEO_UNDERSTANDING
LLM_JUDGE
```

Routing constraints:

```python
select_model(
    task_type=TaskType.OCR,
    require_vision=True,
    require_tools=False,
    require_structured=True,
    max_cost_per_1k=...
)
```

Routing modes:

```text
CHEAPEST
FASTEST
HIGHEST_QUALITY
COMPLIANCE_REQUIRED
FALLBACK_CHAIN
TENANT_DEFAULT
MODEL_PINNED
```

## Built-In Model Registry

Examples from `app/ai_router/registry.py`:

```text
Claude Opus 4.5       -> text, tools, vision, structured output
Claude Sonnet 4.5     -> text, tools, vision, structured output
GPT-5.2               -> text, tools, vision, structured output
GPT-4o Mini           -> text, tools, vision
Gemini 2.0 Flash      -> text, tools, vision
Gemini 2.5 Pro        -> text, tools, vision, video understanding
Text Embedding 3 Large -> embedding
```

## Current Production ModelRouter

Implemented in `app/agent/model_router.py`.

Routes by role:

```text
planning     -> strongest model
execution    -> cheaper tool model
verification -> cheap structured model
embedding    -> embedding model
classification -> small model
```

OpenAI default:

```text
planning     -> gpt-5.2
execution    -> gpt-4o-mini
verification -> gpt-4o-mini
fallback     -> gpt-5.2
```

## Advanced ModelOrchestrator

Implemented in `app/ai_router/model_orchestrator.py`.

Status from code:

```text
Implemented but not wired into graph.py production path.
```

Content-specific mapping:

```text
image:
  extractor -> gpt-4o
  reasoner  -> gpt-5.2
  requires_vision -> true

audio:
  extractor -> gpt-4o-audio
  reasoner  -> gpt-5.2

video:
  extractor -> gemini-2.5-pro
  reasoner  -> gpt-5.2
  requires_vision -> true

code:
  extractor -> gpt-5.2
  reasoner  -> gpt-5.2
```

## Complete Multimodal Workflow Example

Goal:

```text
Analyze this payment error screenshot and create a Jira issue with likely root cause.
```

Workflow:

```text
ImageAttachment
  -> resize_image_b64
  -> VisionParser / GPT-4o
  -> description: "Checkout screen shows error code PG-402..."
  -> image_context injected into planner
  -> planner creates tool plan
  -> executor searches Jira for PG-402
  -> executor creates Jira issue if needed
  -> verifier checks issue created with screenshot-derived evidence
  -> audit + memory + eval
```

## Honest Implementation Status

Fully implemented:

- content classification
- modality pipeline selection
- PDF parsing
- audio transcription via Whisper
- video audio extraction via ffmpeg + Whisper
- image description via GPT-4o / Claude Vision
- parser registry
- multimodal asset job model
- embedding routing by content type
- direct vision message support
- RPA screenshot memory hook
- AI Router model capability registry

Partially implemented:

- full video scene understanding
- native multimodal image embeddings
- OCR bounding boxes
- text-to-speech
- ModelOrchestrator production wiring
