---
title: "OCR Engine — Deep Dives"
description: "Complete technical reference for AgentVerse's OCR engine: pipeline, classification, extractors, validation, API, and global document support."
outline: deep
---

# OCR Engine — Deep Dives

This folder contains the full technical documentation for the `app/ocr/` package.

| File | Topic |
|---|---|
| [01-architecture-and-pipeline.md](01-architecture-and-pipeline.md) | Pipeline stages, image preprocessing, Tesseract, LLM vision fallback |
| [02-document-classification.md](02-document-classification.md) | Keyword/regex scoring, 13 type detection, extensibility |
| [03-field-extractors.md](03-field-extractors.md) | All 12 structured extractors — patterns and field breakdown |
| [04-validation-and-compliance.md](04-validation-and-compliance.md) | Verhoeff, Luhn, Aadhaar masking (legal basis), ISO 8601 dates |
| [05-api-and-tool-integration.md](05-api-and-tool-integration.md) | REST API, batch endpoint, agent tool, PII audit log |
| [06-global-documents-and-llm-extraction.md](06-global-documents-and-llm-extraction.md) | LlmStructuredExtractor — any language, any country |

## Summary

The OCR engine is a **layered extraction pipeline** that:

1. Preprocesses input images/PDFs for maximum Tesseract accuracy
2. Runs Tesseract OCR with multilingual support (`hin+eng` default)
3. Falls back to LLM vision when confidence is low (< 0.6) or Tesseract is unavailable
4. Classifies the document type using keyword/regex scoring (no LLM call required)
5. Routes to a type-specific structured extractor (12 known types) or the `LlmStructuredExtractor` (any unknown type)
6. Validates and normalizes all extracted fields
7. Returns a typed `OcrResult` with per-field confidence, validity flags, and legal masking applied

**Test coverage:** 99 tests · 0 failures · 2.2s runtime
