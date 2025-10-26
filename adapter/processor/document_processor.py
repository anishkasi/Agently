import io
import os
import zipfile
import tempfile
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET
import subprocess
from pypdf import PdfReader
import docx  # python-docx
import docx2txt  # type: ignore


def _read_pdf_with_pypdf(pdf_path: str) -> str:
    """Extract text from PDF using pypdf library."""
    try:
        reader = PdfReader(pdf_path)
        text_parts = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)
        return "\n\n".join(text_parts)
    except Exception as e:
        raise RuntimeError(f"Failed to read PDF: {e}") from e


def _extract_text_from_docx_bytes(file_bytes: bytes) -> str:
    # Prefer python-docx
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "tmp.docx"
            p.write_bytes(file_bytes)
            doc = docx.Document(str(p))
            return "\n".join(par.text for par in doc.paragraphs)
    except Exception:
        try:
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "tmp.docx"
                p.write_bytes(file_bytes)
                text = docx2txt.process(str(p))
                return text or ""
        except Exception:
            return file_bytes.decode("utf-8", errors="ignore")


def _extract_text_from_doc_bytes(file_bytes: bytes) -> str:
    # Old .doc binary format — best effort using textract if present
    try:
        import textract  # type: ignore
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "tmp.doc"
            p.write_bytes(file_bytes)
            txt = textract.process(str(p))
            return txt.decode("utf-8", errors="ignore")
    except Exception:
        try:
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / "tmp.doc"
                src.write_bytes(file_bytes)
                subprocess.run([
                    "soffice", "--headless", "--convert-to", "txt:Text", str(src), "--outdir", td
                ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                out = Path(td) / "tmp.txt"
                if out.exists():
                    return out.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            pass
        return file_bytes.decode("utf-8", errors="ignore")


def _extract_text_from_pdf_bytes(file_bytes: bytes) -> str:
    """Extract text from PDF bytes."""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "tmp.pdf"
        p.write_bytes(file_bytes)
        return _read_pdf_with_pypdf(str(p))


def extract_text_from_document(file_bytes: bytes, filename: str) -> str:
    """Return textual content from common document types.

    Supported:
    - PDF (via pypdf)
    - .docx (python-docx / docx2txt)
    - .doc (textract or soffice fallback)
    """
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        return _extract_text_from_pdf_bytes(file_bytes)
    if name.endswith(".docx"):
        return _extract_text_from_docx_bytes(file_bytes)
    if name.endswith(".doc"):
        return _extract_text_from_doc_bytes(file_bytes)
    # Default: try utf-8 decode
    return file_bytes.decode("utf-8", errors="ignore")


