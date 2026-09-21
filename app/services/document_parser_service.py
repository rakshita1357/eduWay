"""Extracts plain text from an uploaded resume (PDF / DOCX / TXT)."""
import io
import logging
from fastapi import UploadFile

logger = logging.getLogger("uvicorn.error")

MAX_RESUME_CHARS = 15000  # keep prompt payloads sane


async def extract_resume_text(file: UploadFile) -> str:
    filename = (file.filename or "").lower()
    raw = await file.read()

    if filename.endswith(".pdf"):
        text = _extract_pdf(raw)
    elif filename.endswith(".docx"):
        text = _extract_docx(raw)
    elif filename.endswith(".txt") or filename.endswith(".md"):
        text = raw.decode("utf-8", errors="ignore")
    else:
        # Best-effort fallback: try PDF, then plain decode
        try:
            text = _extract_pdf(raw)
        except Exception:
            text = raw.decode("utf-8", errors="ignore")

    text = text.strip()
    if not text:
        raise ValueError(
            "Could not extract any text from the uploaded resume. "
            "Please upload a text-based PDF or DOCX (not a scanned image)."
        )
    if len(text) > MAX_RESUME_CHARS:
        logger.warning(f"Resume text truncated from {len(text)} to {MAX_RESUME_CHARS} chars")
        text = text[:MAX_RESUME_CHARS]
    return text


def _extract_pdf(raw: bytes) -> str:
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(raw))
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception:
            continue
    return "\n".join(parts)


def _extract_docx(raw: bytes) -> str:
    import docx
    doc = docx.Document(io.BytesIO(raw))
    return "\n".join(p.text for p in doc.paragraphs)