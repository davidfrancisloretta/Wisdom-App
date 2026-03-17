"""PDF text and table extraction using multiple engines.

Uses pdfminer.six (via pdfplumber) as the primary extractor for superior
layout-aware text extraction, with PyMuPDF as fallback for table detection.
"""

import io
import logging

import fitz  # PyMuPDF — for tables
import pdfplumber  # pdfminer.six wrapper — for text

logger = logging.getLogger(__name__)


async def extract_text_from_pdf(file_bytes: bytes) -> str:
    """Extract text from PDF using pdfminer.six (via pdfplumber).

    pdfminer.six preserves reading order and spatial layout far better than
    PyMuPDF's default text extraction, which improves question detection.
    Falls back to PyMuPDF if pdfplumber fails.
    """
    try:
        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text(
                    x_tolerance=2,
                    y_tolerance=3,
                    layout=False,
                )
                if page_text and page_text.strip():
                    text_parts.append(page_text.strip())

        if text_parts:
            result = "\n\n".join(text_parts)
            logger.info(f"pdfplumber extracted {len(result)} chars from {len(text_parts)} pages")
            return result
    except Exception as e:
        logger.warning(f"pdfplumber extraction failed, falling back to PyMuPDF: {e}")

    # Fallback to PyMuPDF
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    pages = []
    for page in doc:
        text = page.get_text("text")
        if text.strip():
            pages.append(text.strip())
    doc.close()
    result = "\n\n".join(pages)
    logger.info(f"PyMuPDF fallback extracted {len(result)} chars from {len(pages)} pages")
    return result


async def extract_text_with_layout(file_bytes: bytes) -> str:
    """Extract text preserving spatial layout (columns, indentation).

    Useful for PDFs with multi-column layouts or rating scale tables
    embedded as text rather than actual table objects.
    """
    try:
        text_parts = []
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text(
                    x_tolerance=2,
                    y_tolerance=3,
                    layout=True,
                )
                if page_text and page_text.strip():
                    text_parts.append(page_text.strip())
        return "\n\n".join(text_parts)
    except Exception as e:
        logger.warning(f"Layout extraction failed: {e}")
        return await extract_text_from_pdf(file_bytes)


async def extract_tables_from_pdf(file_bytes: bytes) -> list[dict]:
    """Extract structured table data from PDF pages.

    Uses pdfplumber for table detection first (more accurate for clinical
    assessment rating scales), falls back to PyMuPDF.
    """
    tables = []

    # Try pdfplumber first
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page_num, page in enumerate(pdf.pages):
                page_tables = page.extract_tables(
                    table_settings={
                        "vertical_strategy": "lines",
                        "horizontal_strategy": "lines",
                        "snap_y_tolerance": 5,
                        "snap_x_tolerance": 5,
                    }
                )
                for table_data in page_tables:
                    if not table_data or len(table_data) < 2:
                        continue
                    # Clean None values
                    cleaned = [
                        [str(cell).strip() if cell else "" for cell in row]
                        for row in table_data
                    ]
                    tables.append({
                        "page": page_num + 1,
                        "headers": cleaned[0],
                        "rows": cleaned[1:],
                    })

        if tables:
            logger.info(f"pdfplumber found {len(tables)} tables")
            return tables
    except Exception as e:
        logger.warning(f"pdfplumber table extraction failed: {e}")

    # Fallback to PyMuPDF table detection
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    for page_num, page in enumerate(doc):
        try:
            page_tables = page.find_tables()
            for table in page_tables:
                extracted = table.extract()
                if not extracted or len(extracted) < 2:
                    continue
                tables.append({
                    "page": page_num + 1,
                    "headers": extracted[0],
                    "rows": extracted[1:],
                })
        except Exception as e:
            logger.warning(f"PyMuPDF table extraction failed on page {page_num + 1}: {e}")
    doc.close()

    logger.info(f"PyMuPDF found {len(tables)} tables")
    return tables


async def extract_all(file_bytes: bytes) -> dict:
    """Full extraction: text (plain + layout) and tables.

    Returns all extracted content for the parser to use.
    """
    plain_text = await extract_text_from_pdf(file_bytes)
    layout_text = await extract_text_with_layout(file_bytes)
    tables = await extract_tables_from_pdf(file_bytes)

    return {
        "plain_text": plain_text,
        "layout_text": layout_text,
        "tables": tables,
        "text_length": len(plain_text),
        "table_count": len(tables),
    }
