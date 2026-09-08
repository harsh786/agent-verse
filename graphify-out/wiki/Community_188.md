# Community 188

> 31 nodes · cohesion 0.11

## Key Concepts

- **OcrEngine** (18 connections) — `agent-verse-backend/app/ocr/engine.py`
- **ocr/engine.py** (15 connections) — `agent-verse-backend/app/ocr/engine.py`
- **DocumentType enum** (12 connections) — `agent-verse-backend/app/ocr/models.py`
- **ocr_tool.py** (8 connections) — `agent-verse-backend/app/tools/ocr_tool.py`
- **.extract()** (7 connections) — `agent-verse-backend/app/ocr/engine.py`
- **._llm_vision_ocr()** (7 connections) — `agent-verse-backend/app/ocr/engine.py`
- **DocumentClassifier** (6 connections) — `agent-verse-backend/app/ocr/classifier.py`
- **._ocr_page()** (6 connections) — `agent-verse-backend/app/ocr/engine.py`
- **Any** (6 connections)
- **ocr/classifier.py** (5 connections) — `agent-verse-backend/app/ocr/classifier.py`
- **OcrResult** (5 connections) — `agent-verse-backend/app/ocr/models.py`
- **._preprocess_image()** (4 connections) — `agent-verse-backend/app/ocr/engine.py`
- **._to_images()** (4 connections) — `agent-verse-backend/app/ocr/engine.py`
- **.classify()** (3 connections) — `agent-verse-backend/app/ocr/classifier.py`
- **._image_to_base64()** (3 connections) — `agent-verse-backend/app/ocr/engine.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/ocr/engine.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/ocr/extractors/financial.py`
- **.__init__()** (2 connections) — `agent-verse-backend/app/ocr/extractors/id_docs.py`
- **Document type classifier using keyword and regex scoring.** (1 connections) — `agent-verse-backend/app/ocr/classifier.py`
- **Classify a document type from extracted OCR text using keyword/regex scoring.** (1 connections) — `agent-verse-backend/app/ocr/classifier.py`
- **Return the most likely DocumentType for the given OCR text. Scoring: - +1 per…** (1 connections) — `agent-verse-backend/app/ocr/classifier.py`
- **OCR image preprocessing (grayscale/sharpen/autocontrast)** (1 connections) — `agent-verse-backend/app/ocr/engine.py`
- **OCR engine: Tesseract primary with LLM vision fallback.** (1 connections) — `agent-verse-backend/app/ocr/engine.py`
- **Run OCR on a single page image. Returns (text, confidence, engine_name).** (1 connections) — `agent-verse-backend/app/ocr/engine.py`
- **Use LLM vision to extract text from an image.** (1 connections) — `agent-verse-backend/app/ocr/engine.py`
- *... and 6 more nodes in this community*

## Relationships

- [Community 380](Community_380.md) (10 shared connections)
- [Self-Refine & Model Routing](Self-Refine_&_Model_Routing.md) (4 shared connections)
- [Community 442](Community_442.md) (3 shared connections)
- [Community 546](Community_546.md) (2 shared connections)
- [Content Parsing & MCP Servers](Content_Parsing_&_MCP_Servers.md) (2 shared connections)
- [Community 588](Community_588.md) (2 shared connections)
- [Community 407](Community_407.md) (2 shared connections)
- [Community 290](Community_290.md) (2 shared connections)
- [Community 187](Community_187.md) (1 shared connections)
- [Community 305](Community_305.md) (1 shared connections)
- [Agent LangGraph Kernel](Agent_LangGraph_Kernel.md) (1 shared connections)

## Source Files

- `agent-verse-backend/app/ocr/classifier.py`
- `agent-verse-backend/app/ocr/engine.py`
- `agent-verse-backend/app/ocr/extractors/financial.py`
- `agent-verse-backend/app/ocr/extractors/id_docs.py`
- `agent-verse-backend/app/ocr/models.py`
- `agent-verse-backend/app/tools/ocr_tool.py`

## Audit Trail

- EXTRACTED: 77 (97%)
- INFERRED: 2 (3%)
- AMBIGUOUS: 0 (0%)

---

*Part of the graphify knowledge wiki. See [index](index.md) to navigate.*