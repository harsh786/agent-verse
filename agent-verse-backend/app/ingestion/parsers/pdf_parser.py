"""PDFParser — extracts text with page numbers using pdfminer.six → pymupdf → text fallback."""
from __future__ import annotations
import io
from dataclasses import dataclass, field
from typing import Any


@dataclass
class PDFPage:
    page_number: int
    content: str
    width: float = 0.0
    height: float = 0.0
    has_tables: bool = False
    has_images: bool = False


@dataclass
class PDFParseResult:
    source_name: str
    pages: list[PDFPage] = field(default_factory=list)
    error: str | None = None

    @property
    def full_text(self) -> str:
        return "\n\n".join(p.content for p in self.pages if p.content)

    def to_chunks(self) -> list[dict[str, Any]]:
        chunks = []
        for i, page in enumerate(self.pages):
            if not page.content.strip():
                continue
            chunks.append({
                "content": page.content.strip(),
                "chunk_index": i,
                "page_number": page.page_number,
                "source_name": self.source_name,
                "content_type": "pdf",
            })
        return chunks or (
            [{"content": self.full_text, "chunk_index": 0, "page_number": 1,
              "source_name": self.source_name, "content_type": "pdf"}]
            if self.full_text else []
        )


class PDFParser:
    def parse_bytes(self, pdf_bytes: bytes, source_name: str = "document.pdf") -> PDFParseResult:
        if not pdf_bytes:
            return PDFParseResult(source_name=source_name)
        result = self._parse_with_pymupdf(pdf_bytes, source_name)
        if result and result.pages:
            return result
        result = self._parse_with_pdfminer(pdf_bytes, source_name)
        if result and result.pages:
            return result
        try:
            text = pdf_bytes.decode("utf-8", errors="replace")
            return self.parse_text(text, source_name)
        except Exception as exc:
            return PDFParseResult(source_name=source_name, error=str(exc))

    def parse_text(self, text: str, source_name: str = "document.pdf") -> PDFParseResult:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            return PDFParseResult(source_name=source_name)
        pages = []
        current: list[str] = []
        current_len = 0
        page_num = 1
        for para in paragraphs:
            if current_len + len(para) > 3000 and current:
                pages.append(PDFPage(page_number=page_num, content="\n\n".join(current)))
                page_num += 1
                current = [para]
                current_len = len(para)
            else:
                current.append(para)
                current_len += len(para)
        if current:
            pages.append(PDFPage(page_number=page_num, content="\n\n".join(current)))
        return PDFParseResult(source_name=source_name, pages=pages)

    def _parse_with_pymupdf(self, pdf_bytes: bytes, source_name: str) -> PDFParseResult | None:
        try:
            import fitz  # type: ignore[import]
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            pages = []
            for page_num, page in enumerate(doc, start=1):
                text = page.get_text("text")
                if text.strip():
                    pages.append(PDFPage(
                        page_number=page_num, content=text.strip(),
                        width=page.rect.width, height=page.rect.height,
                    ))
            doc.close()
            return PDFParseResult(source_name=source_name, pages=pages) if pages else None
        except (ImportError, Exception):
            return None

    def _parse_with_pdfminer(self, pdf_bytes: bytes, source_name: str) -> PDFParseResult | None:
        try:
            from pdfminer.high_level import extract_pages  # type: ignore[import]
            from pdfminer.layout import LTTextContainer, LTFigure  # type: ignore[import]
            pages = []
            for page_num, page_layout in enumerate(extract_pages(io.BytesIO(pdf_bytes)), start=1):
                text_parts: list[str] = []
                has_images = False
                for element in page_layout:
                    if isinstance(element, LTTextContainer):
                        text_parts.append(element.get_text())
                    elif isinstance(element, LTFigure):
                        has_images = True
                page_text = "".join(text_parts).strip()
                if page_text:
                    pages.append(PDFPage(page_number=page_num, content=page_text, has_images=has_images))
            return PDFParseResult(source_name=source_name, pages=pages) if pages else None
        except (ImportError, Exception):
            return None
