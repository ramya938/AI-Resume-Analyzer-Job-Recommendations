"""
resume_parser.py — Resume Text Extraction & Processing
========================================================
Handles extraction, cleanup, and storage of text content
from PDF and DOCX resume files.

Core Functions:
    - extract_pdf_text(file)       → Extract text from a PDF file
    - extract_docx_text(file)      → Extract text from a DOCX file
    - clean_text(raw_text)         → Normalize and sanitize extracted text
    - extract_resume_text(file)    → Auto-detect format, extract, and clean
    - check_file_corruption(file)  → Verify file integrity before parsing

Storage Functions:
    - save_resume_file(user_id, file) → Persist to data/resumes/
    - delete_resume_file(path)        → Remove from filesystem

Dependencies:
    - PyPDF2      (PDF reading)
    - python-docx (DOCX reading)
"""

import os
import io
import re
import logging
from datetime import datetime
from typing import Optional

import PyPDF2
from docx import Document

from utils.database import update_user_resume_path, save_resume_analysis

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_UPLOAD_DIR = os.path.join("data", "resumes")


def _get_upload_dir() -> str:
    """Return the resume upload directory from env or default."""
    return os.getenv("RESUME_UPLOAD_DIR", DEFAULT_UPLOAD_DIR)


# ===========================================================================
# 1. PDF Text Extraction
# ===========================================================================

def extract_pdf_text(uploaded_file) -> tuple[str, dict]:
    """
    Extract raw text content from a PDF file.

    Reads every page sequentially and concatenates the text.
    Handles per-page extraction errors gracefully.

    Args:
        uploaded_file: A Streamlit UploadedFile object or file-like
                       object with a .getvalue() / .read() method.

    Returns:
        Tuple of (raw_text, metadata).
        metadata keys: page_count, char_count, word_count, format.
    """
    text_parts = []
    metadata = {
        "page_count": 0,
        "char_count": 0,
        "word_count": 0,
        "format": "PDF",
    }

    try:
        # Get file bytes
        if hasattr(uploaded_file, "getvalue"):
            file_bytes = uploaded_file.getvalue()
        else:
            file_bytes = uploaded_file.read()

        reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
        metadata["page_count"] = len(reader.pages)

        if metadata["page_count"] == 0:
            logger.warning("PDF has zero pages.")
            return "", metadata

        for i, page in enumerate(reader.pages):
            try:
                page_text = page.extract_text()
                if page_text:
                    text_parts.append(page_text)
                else:
                    logger.debug("Page %d: no extractable text (image?)", i + 1)
            except Exception as e:
                logger.warning("Failed to extract text from PDF page %d: %s", i + 1, e)

        raw_text = "\n".join(text_parts)
        metadata["char_count"] = len(raw_text)
        metadata["word_count"] = len(raw_text.split())

        if not raw_text.strip():
            logger.warning(
                "PDF text extraction returned empty text — "
                "this may be a scanned/image-based PDF."
            )

        logger.info(
            "PDF extracted: %d pages, %d words, %d chars",
            metadata["page_count"],
            metadata["word_count"],
            metadata["char_count"],
        )
        return raw_text, metadata

    except PyPDF2.errors.PdfReadError as e:
        logger.error("PDF read error — file may be corrupted: %s", e)
        return "", metadata

    except Exception as e:
        logger.error("Unexpected error extracting PDF text: %s", e)
        return "", metadata

    finally:
        # Reset file pointer for potential subsequent reads
        if hasattr(uploaded_file, "seek"):
            try:
                uploaded_file.seek(0)
            except Exception:
                pass


# ===========================================================================
# 2. DOCX Text Extraction
# ===========================================================================

def extract_docx_text(uploaded_file) -> tuple[str, dict]:
    """
    Extract raw text content from a DOCX file.

    Reads paragraphs, headers, and table cells.

    Args:
        uploaded_file: A Streamlit UploadedFile object or file-like
                       object with a .getvalue() / .read() method.

    Returns:
        Tuple of (raw_text, metadata).
        metadata keys: paragraph_count, table_count, char_count,
                       word_count, format.
    """
    text_parts = []
    metadata = {
        "paragraph_count": 0,
        "table_count": 0,
        "char_count": 0,
        "word_count": 0,
        "format": "DOCX",
    }

    try:
        # Get file bytes
        if hasattr(uploaded_file, "getvalue"):
            file_bytes = uploaded_file.getvalue()
        else:
            file_bytes = uploaded_file.read()

        doc = Document(io.BytesIO(file_bytes))

        # ── Paragraphs ───────────────────────────────────────────
        metadata["paragraph_count"] = len(doc.paragraphs)
        for para in doc.paragraphs:
            text = para.text.strip()
            if text:
                text_parts.append(text)

        # ── Tables ────────────────────────────────────────────────
        metadata["table_count"] = len(doc.tables)
        for table in doc.tables:
            for row in table.rows:
                row_cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if row_cells:
                    text_parts.append(" | ".join(row_cells))

        raw_text = "\n".join(text_parts)
        metadata["char_count"] = len(raw_text)
        metadata["word_count"] = len(raw_text.split())

        logger.info(
            "DOCX extracted: %d paragraphs, %d tables, %d words",
            metadata["paragraph_count"],
            metadata["table_count"],
            metadata["word_count"],
        )
        return raw_text, metadata

    except Exception as e:
        logger.error("Error extracting DOCX text: %s", e)
        return "", metadata

    finally:
        if hasattr(uploaded_file, "seek"):
            try:
                uploaded_file.seek(0)
            except Exception:
                pass


# ===========================================================================
# 3. Text Cleanup
# ===========================================================================

def clean_text(raw_text: str) -> str:
    """
    Normalize and sanitize raw extracted resume text.

    Processing steps:
        1. Replace non-breaking spaces and special whitespace
        2. Normalize Unicode characters (smart quotes, dashes, bullets)
        3. Remove null bytes and control characters
        4. Collapse multiple blank lines into a single separator
        5. Collapse multiple spaces into one
        6. Strip leading/trailing whitespace per line
        7. Final strip

    Args:
        raw_text: The raw text from PDF/DOCX extraction.

    Returns:
        Cleaned, normalized plain text string.
    """
    if not raw_text:
        return ""

    text = raw_text

    # ── Step 1: Replace special whitespace ────────────────────────
    text = text.replace("\xa0", " ")       # Non-breaking space
    text = text.replace("\u200b", "")      # Zero-width space
    text = text.replace("\ufeff", "")      # BOM
    text = text.replace("\t", "    ")      # Tabs → 4 spaces

    # ── Step 2: Normalize Unicode punctuation ─────────────────────
    # Smart quotes → straight quotes
    text = text.replace("\u2018", "'").replace("\u2019", "'")
    text = text.replace("\u201c", '"').replace("\u201d", '"')
    # Em/en dashes → hyphens
    text = text.replace("\u2013", "-").replace("\u2014", "-")
    # Bullet points → standard bullets
    text = text.replace("\u2022", "*")     # •
    text = text.replace("\u25cf", "*")     # ●
    text = text.replace("\u25cb", "*")     # ○
    text = text.replace("\u25aa", "*")     # ▪
    text = text.replace("\u25ab", "*")     # ▫
    text = text.replace("\u2023", "*")     # ‣

    # ── Step 3: Remove control characters ─────────────────────────
    # Keep newlines (\n), carriage returns (\r), and normal printable chars
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

    # ── Step 4: Normalize line endings ────────────────────────────
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    # ── Step 5: Collapse excessive blank lines ────────────────────
    # 3+ consecutive newlines → 2 (one blank line separator)
    text = re.sub(r"\n{3,}", "\n\n", text)

    # ── Step 6: Collapse multiple spaces per line ─────────────────
    text = re.sub(r"[ ]{2,}", " ", text)

    # ── Step 7: Strip whitespace from each line ───────────────────
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)

    # ── Step 8: Final strip ───────────────────────────────────────
    text = text.strip()

    logger.debug("Text cleaned: %d chars → %d chars", len(raw_text), len(text))
    return text


# ===========================================================================
# 4. Universal Resume Extractor (auto-detect + clean + store)
# ===========================================================================

def extract_resume_text(
    uploaded_file,
    user_id: Optional[str] = None,
    store_in_db: bool = False,
) -> tuple[str, dict]:
    """
    Auto-detect file type, extract text, clean it, and optionally
    store the extracted text in the database.

    This is the main entry point for resume text extraction.

    Args:
        uploaded_file:  A Streamlit UploadedFile object (.pdf or .docx).
        user_id:        The user's UUID (required if store_in_db=True).
        store_in_db:    If True, saves the extracted text to the
                        resume_analysis table with a score of 0
                        (to be updated by the analysis module).

    Returns:
        Tuple of (cleaned_text, metadata).
        metadata includes: format, filename, file_size, page_count,
                          word_count, char_count, cleaned_word_count,
                          cleaned_char_count.
    """
    if uploaded_file is None:
        logger.error("extract_resume_text called with None file.")
        return "", {"error": "No file provided."}

    # ── Detect file type ──────────────────────────────────────────
    filename = getattr(uploaded_file, "name", "unknown")
    filename_lower = filename.lower()

    if filename_lower.endswith(".pdf"):
        raw_text, metadata = extract_pdf_text(uploaded_file)
    elif filename_lower.endswith(".docx"):
        raw_text, metadata = extract_docx_text(uploaded_file)
    else:
        logger.error("Unsupported file type: %s", filename)
        return "", {"error": f"Unsupported file type: {filename}"}

    # ── Enrich metadata ───────────────────────────────────────────
    metadata["filename"] = filename
    if hasattr(uploaded_file, "size"):
        metadata["file_size"] = uploaded_file.size
    elif hasattr(uploaded_file, "getvalue"):
        metadata["file_size"] = len(uploaded_file.getvalue())
    else:
        metadata["file_size"] = 0

    # ── Clean the text ────────────────────────────────────────────
    cleaned_text = clean_text(raw_text)

    # Add post-cleanup stats to metadata
    metadata["raw_char_count"] = len(raw_text)
    metadata["cleaned_char_count"] = len(cleaned_text)
    metadata["cleaned_word_count"] = len(cleaned_text.split()) if cleaned_text else 0

    logger.info(
        "Resume processed: '%s' | %s | %d raw chars → %d clean chars (%d words)",
        filename,
        metadata.get("format", "?"),
        metadata["raw_char_count"],
        metadata["cleaned_char_count"],
        metadata["cleaned_word_count"],
    )

    # ── Optionally store extracted text in database ───────────────
    if store_in_db and user_id and cleaned_text:
        try:
            result = save_resume_analysis(
                user_id=user_id,
                extracted_text=cleaned_text,
                resume_score=0.0,       # Will be updated by analysis module
                strengths=[],
                weaknesses=[],
                identified_skills=[],
                recommended_skills=[],
            )
            if result["success"]:
                metadata["analysis_id"] = result["analysis_id"]
                logger.info(
                    "Extracted text stored in DB: analysis_id=%d",
                    result["analysis_id"],
                )
            else:
                logger.warning("Failed to store extracted text: %s", result["message"])
        except Exception as e:
            logger.error("Error storing extracted text in DB: %s", e)

    return cleaned_text, metadata


# ===========================================================================
# 5. File Corruption Check
# ===========================================================================

def check_file_corruption(uploaded_file) -> tuple[bool, str]:
    """
    Verify that an uploaded file is not corrupted by attempting to parse it.

    Attempts to open and read the file using the appropriate library.
    Does NOT extract full text — only validates structural integrity.

    Args:
        uploaded_file: A Streamlit UploadedFile object.

    Returns:
        Tuple of (is_valid, error_message).
        is_valid is True if the file can be parsed successfully.
    """
    if uploaded_file is None:
        return False, "No file provided."

    filename = getattr(uploaded_file, "name", "").lower()

    try:
        if hasattr(uploaded_file, "getvalue"):
            file_bytes = uploaded_file.getvalue()
        else:
            file_bytes = uploaded_file.read()
    except Exception as e:
        return False, f"Cannot read file: {e}"

    if not file_bytes or len(file_bytes) == 0:
        return False, "File is empty (0 bytes)."

    try:
        if filename.endswith(".pdf"):
            reader = PyPDF2.PdfReader(io.BytesIO(file_bytes))
            page_count = len(reader.pages)
            if page_count == 0:
                return False, "PDF has no pages."
            # Try extracting text from the first page as a deeper check
            _ = reader.pages[0].extract_text()
            logger.debug("PDF integrity check passed: %d pages", page_count)
            return True, ""

        elif filename.endswith(".docx"):
            doc = Document(io.BytesIO(file_bytes))
            para_count = len(doc.paragraphs)
            if para_count == 0 and len(doc.tables) == 0:
                return False, "DOCX file has no readable content."
            logger.debug("DOCX integrity check passed: %d paragraphs", para_count)
            return True, ""

        else:
            return False, f"Unsupported file format: {filename}"

    except PyPDF2.errors.PdfReadError as e:
        logger.warning("Corrupted PDF detected: %s", e)
        return False, "File appears to be corrupted or is not a valid PDF."

    except Exception as e:
        logger.warning("File corruption check failed: %s", e)
        return False, f"File appears to be corrupted: {e}"

    finally:
        if hasattr(uploaded_file, "seek"):
            try:
                uploaded_file.seek(0)
            except Exception:
                pass


# ===========================================================================
# 6. File Storage
# ===========================================================================

def save_resume_file(user_id: str, uploaded_file) -> tuple[bool, str, str]:
    """
    Save an uploaded resume to the filesystem and update the user record.

    Naming convention: {user_id_first8}_{YYYYMMDD_HHMMSS}.{ext}

    Args:
        user_id:       The user's UUID string.
        uploaded_file: A Streamlit UploadedFile object.

    Returns:
        Tuple of (success, file_path, message).
    """
    if not user_id:
        return False, "", "User ID is required."

    if uploaded_file is None:
        return False, "", "No file to save."

    upload_dir = _get_upload_dir()
    os.makedirs(upload_dir, exist_ok=True)

    # Build safe filename
    original_name = getattr(uploaded_file, "name", "resume.pdf")
    _, ext = os.path.splitext(original_name)
    ext = ext.lower() if ext else ".pdf"

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid_short = user_id.replace("-", "")[:8]
    safe_name = f"{uid_short}_{timestamp}{ext}"
    file_path = os.path.join(upload_dir, safe_name)

    try:
        # Delete previous resume for this user
        _delete_existing_resume(user_id, upload_dir)

        # Write the new file
        file_bytes = uploaded_file.getvalue()
        with open(file_path, "wb") as f:
            f.write(file_bytes)

        # Update the user's resume_path in database
        update_user_resume_path(user_id, file_path)

        logger.info("Resume saved: %s (%d bytes)", file_path, len(file_bytes))
        return True, file_path, "Resume uploaded successfully!"

    except IOError as e:
        logger.error("Failed to save resume file: %s", e)
        return False, "", f"Failed to save file: {e}"

    except Exception as e:
        logger.error("Unexpected error saving resume: %s", e)
        return False, "", f"Unexpected error: {e}"


def _delete_existing_resume(user_id: str, upload_dir: str) -> None:
    """
    Delete any previously uploaded resume for this user.

    Scans the upload directory for files starting with the user's
    short UUID prefix and removes them.
    """
    uid_short = user_id.replace("-", "")[:8]
    try:
        for filename in os.listdir(upload_dir):
            if filename.startswith(uid_short):
                old_path = os.path.join(upload_dir, filename)
                os.remove(old_path)
                logger.info("Deleted old resume: %s", old_path)
    except OSError as e:
        logger.warning("Error cleaning old resumes: %s", e)


def delete_resume_file(file_path: str) -> tuple[bool, str]:
    """
    Delete a resume file from the filesystem.

    Args:
        file_path: Path to the file to delete.

    Returns:
        Tuple of (success, message).
    """
    if not file_path:
        return False, "No file path provided."

    try:
        if os.path.exists(file_path):
            os.remove(file_path)
            logger.info("Deleted resume file: %s", file_path)
            return True, "File deleted successfully."
        else:
            logger.warning("File not found for deletion: %s", file_path)
            return False, "File not found."

    except OSError as e:
        logger.error("Failed to delete file '%s': %s", file_path, e)
        return False, f"Failed to delete file: {e}"


# ===========================================================================
# Self-Test — run with: python -m backend.resume_parser
# ===========================================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s | %(message)s")

    print("=" * 55)
    print("  Resume Parser — Self Test")
    print("=" * 55)

    # --- Test 1: Module loads ---
    print("\n[OK] resume_parser module loaded.")
    print(f"[OK] Upload directory: {_get_upload_dir()}")

    # --- Test 2: clean_text() ---
    dirty = (
        "  John\xa0Doe  \n\n\n\n"
        "Python \u2022 Java \u2022 SQL  \n"
        "\u201cExcellent\u201d skills\n"
        "2020\u20132023  experience\n"
        "  \x00hidden\x07chars  "
    )
    cleaned = clean_text(dirty)
    assert "John Doe" in cleaned, "Non-breaking space not handled"
    assert "\xa0" not in cleaned, "NBSP still present"
    assert "\x00" not in cleaned, "Null byte still present"
    assert "\x07" not in cleaned, "Control char still present"
    assert '\"Excellent\"' in cleaned, "Smart quotes not normalized"
    assert "* Java" in cleaned, "Bullet not normalized"
    assert "2020-2023" in cleaned, "En-dash not normalized"
    assert "\n\n\n" not in cleaned, "Excess newlines not collapsed"
    print("[OK] clean_text: all normalizations passed")
    print(f"     Input:  {len(dirty)} chars")
    print(f"     Output: {len(cleaned)} chars")
    print(f"     Preview: {cleaned[:80]}...")

    # --- Test 3: extract_resume_text with None ---
    text, meta = extract_resume_text(None)
    assert text == "", "Should return empty string for None"
    assert "error" in meta, "Should return error in metadata"
    print("[OK] extract_resume_text(None): handled gracefully")

    # --- Test 4: check_file_corruption with None ---
    valid, msg = check_file_corruption(None)
    assert not valid
    print(f"[OK] check_file_corruption(None): {msg}")

    # --- Test 5: delete_resume_file with empty path ---
    ok, msg = delete_resume_file("")
    assert not ok
    print(f"[OK] delete_resume_file(''): {msg}")

    print("\n=== All resume_parser tests passed ===")
