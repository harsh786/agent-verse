"""Real generated OCR inputs: valid, corrupt, truncated, encrypted and zero-page
PDFs (Pillow writes the scanned-page PDF, pypdf encrypts / writes the empty one).
"""

from __future__ import annotations

import io

import pypdf
from PIL import Image, ImageDraw


def page_image(n: int) -> Image.Image:
    img = Image.new("L", (400, 200), 255)
    ImageDraw.Draw(img).text((10, 10), f"Page {n} invoice total 42", fill=0)
    img.info["page"] = n
    return img


def valid_pdf(pages: int = 3) -> bytes:
    imgs = [page_image(n) for n in range(1, pages + 1)]
    buf = io.BytesIO()
    imgs[0].save(buf, format="PDF", save_all=True, append_images=imgs[1:])
    return buf.getvalue()


def corrupt_pdf() -> bytes:
    return b"%PDF-1.4\n" + b"\x00garbage\xff" * 200


def truncated_pdf() -> bytes:
    data = valid_pdf()
    return data[: len(data) // 2]


def encrypted_pdf(user_password: str = "secret") -> bytes:
    writer = pypdf.PdfWriter()
    writer.append(pypdf.PdfReader(io.BytesIO(valid_pdf())))
    writer.encrypt(user_password, owner_password="owner-pw")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def zero_page_pdf() -> bytes:
    buf = io.BytesIO()
    pypdf.PdfWriter().write(buf)
    return buf.getvalue()


def png_bytes() -> bytes:
    buf = io.BytesIO()
    page_image(1).save(buf, format="PNG")
    return buf.getvalue()


class PDFPageCountError(Exception):
    """Stand-in for ``pdf2image.exceptions.PDFPageCountError`` (poppler's
    ``pdfinfo`` exited non-zero; its stderr follows the first line)."""


class PDFPopplerTimeoutError(Exception):
    """Stand-in for ``pdf2image.exceptions.PDFPopplerTimeoutError``."""


def poppler_refuses(_path: object) -> int:
    """What poppler 22.12 answers for the corrupt / truncated / encrypted /
    zero-page inputs above (probed in the backend image)."""
    raise PDFPageCountError(
        "Unable to get page count.\nSyntax Error: Couldn't find trailer dictionary\n"
        "Syntax Error: Couldn't read xref table\n"
    )
