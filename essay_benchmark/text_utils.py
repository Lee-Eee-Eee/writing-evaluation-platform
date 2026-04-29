from __future__ import annotations

import io
from pathlib import Path

from pypdf import PdfReader


def read_text_bytes(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return extract_pdf_text(content)
    return content.decode("utf-8", errors="ignore").strip()


def extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(page.strip() for page in pages if page.strip()).strip()


def word_count(text: str) -> int:
    return len([token for token in text.split() if token.strip()])

