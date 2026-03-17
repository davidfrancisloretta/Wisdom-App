"""Tests for the PDF→Survey pipeline.

Tests PDF text extraction and validates the parsed structure
against expected DSM-5 assessment format.
"""

import os
import pytest
import pytest_asyncio

from app.assessments.pdf_parser import extract_tables_from_pdf, extract_text_from_pdf

# Path to the DSM-5 PDF (expected to be in the project root or tests directory)
DSM5_PDF_PATHS = [
    os.path.join(os.path.dirname(__file__), "..", "APA-DSM5TR-Level1MeasureChildAge11To17.pdf"),
    os.path.join(os.path.dirname(__file__), "APA-DSM5TR-Level1MeasureChildAge11To17.pdf"),
    os.path.join(os.path.dirname(__file__), "..", "seeds", "APA-DSM5TR-Level1MeasureChildAge11To17.pdf"),
]


def _find_dsm5_pdf() -> str | None:
    for path in DSM5_PDF_PATHS:
        if os.path.exists(path):
            return path
    return None


@pytest.fixture
def dsm5_pdf_bytes():
    """Load the real DSM-5 PDF if available."""
    path = _find_dsm5_pdf()
    if not path:
        pytest.skip("DSM-5 PDF not found — place APA-DSM5TR-Level1MeasureChildAge11To17.pdf in project root")
    with open(path, "rb") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Text extraction tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_text_returns_string(dsm5_pdf_bytes):
    """PDF text extraction should return a non-empty string."""
    text = await extract_text_from_pdf(dsm5_pdf_bytes)
    assert isinstance(text, str)
    assert len(text) > 100


@pytest.mark.asyncio
async def test_extract_text_contains_dsm5_keywords(dsm5_pdf_bytes):
    """Extracted text should contain DSM-5 assessment keywords."""
    text = await extract_text_from_pdf(dsm5_pdf_bytes)
    text_lower = text.lower()

    # Must contain key terms from the assessment
    assert "level 1" in text_lower or "cross-cutting" in text_lower
    assert "symptom" in text_lower or "measure" in text_lower


@pytest.mark.asyncio
async def test_extract_text_contains_questions(dsm5_pdf_bytes):
    """Extracted text should contain recognizable question content."""
    text = await extract_text_from_pdf(dsm5_pdf_bytes)
    text_lower = text.lower()

    # Check for some key question fragments
    key_phrases = [
        "headaches",
        "sleep",
        "attention",
        "sad",
        "angry",
        "nervous",
        "anxious",
        "suicide",
    ]
    found = sum(1 for p in key_phrases if p in text_lower)
    assert found >= 4, (
        f"Expected at least 4 key phrases from DSM-5 questions, found {found}"
    )


@pytest.mark.asyncio
async def test_extract_text_has_suicidal_ideation(dsm5_pdf_bytes):
    """Domain XII safety-critical items must be present in extracted text."""
    text = await extract_text_from_pdf(dsm5_pdf_bytes)
    text_lower = text.lower()

    assert "suicide" in text_lower or "killing yourself" in text_lower, (
        "Suicidal ideation questions (Q24/Q25) must be extractable from PDF"
    )


# ---------------------------------------------------------------------------
# Table extraction tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_extract_tables_returns_list(dsm5_pdf_bytes):
    """Table extraction should return a list."""
    tables = await extract_tables_from_pdf(dsm5_pdf_bytes)
    assert isinstance(tables, list)


@pytest.mark.asyncio
async def test_extract_tables_have_structure(dsm5_pdf_bytes):
    """Each table should have page, headers, and rows keys."""
    tables = await extract_tables_from_pdf(dsm5_pdf_bytes)
    if not tables:
        pytest.skip("No tables detected in PDF — table detection may vary by PyMuPDF version")

    for table in tables:
        assert "page" in table
        assert "headers" in table
        assert "rows" in table


# ---------------------------------------------------------------------------
# AI parser validation (structure-only, no API call)
# ---------------------------------------------------------------------------

def test_assessment_schema_validates_correct_input():
    """AssessmentSchema should accept valid parsed data."""
    from app.assessments.ai_parser import AssessmentSchema

    valid_data = {
        "title": "Test Assessment",
        "version": "1.0",
        "age_range_min": 11,
        "age_range_max": 17,
        "domains": [
            {
                "domain_name": "Test Domain",
                "domain_code": "I",
                "threshold_further_inquiry": 2,
                "threshold_type": "score",
                "is_safety_critical": False,
            }
        ],
        "sections": [
            {
                "title": "Section 1",
                "order_index": 0,
                "questions": [
                    {
                        "order_index": 1,
                        "question_text": "Test question?",
                        "question_type": "likert_5",
                        "domain_code": "I",
                        "is_risk_flag": False,
                        "answer_options": [
                            {"option_text": "None", "value": 0, "order_index": 0},
                            {"option_text": "Severe", "value": 4, "order_index": 4},
                        ],
                    }
                ],
            }
        ],
    }

    schema = AssessmentSchema(**valid_data)
    assert schema.title == "Test Assessment"
    assert len(schema.domains) == 1
    assert len(schema.sections) == 1
    assert len(schema.sections[0].questions) == 1


def test_assessment_schema_safety_critical_detection():
    """Schema should correctly capture safety-critical domain flags."""
    from app.assessments.ai_parser import AssessmentSchema

    data = {
        "title": "DSM-5 Test",
        "domains": [
            {
                "domain_name": "Safe Domain",
                "domain_code": "I",
                "threshold_further_inquiry": 2,
                "threshold_type": "score",
                "is_safety_critical": False,
            },
            {
                "domain_name": "Suicidal Ideation",
                "domain_code": "XII",
                "threshold_further_inquiry": 1,
                "threshold_type": "yes_no",
                "is_safety_critical": True,
            },
        ],
        "sections": [],
    }

    schema = AssessmentSchema(**data)
    safe = next(d for d in schema.domains if d.domain_code == "I")
    critical = next(d for d in schema.domains if d.domain_code == "XII")

    assert safe.is_safety_critical is False
    assert critical.is_safety_critical is True
    assert critical.threshold_type == "yes_no"


def test_assessment_schema_question_types():
    """Schema should correctly identify likert_5 vs yes_no question types."""
    from app.assessments.ai_parser import AssessmentSchema

    data = {
        "title": "Type Test",
        "domains": [
            {"domain_name": "D1", "domain_code": "I", "threshold_further_inquiry": 2, "threshold_type": "score", "is_safety_critical": False},
        ],
        "sections": [
            {
                "title": "S1",
                "order_index": 0,
                "questions": [
                    {"order_index": 1, "question_text": "Likert Q", "question_type": "likert_5", "domain_code": "I", "is_risk_flag": False},
                    {"order_index": 2, "question_text": "YesNo Q", "question_type": "yes_no", "domain_code": "I", "is_risk_flag": True},
                ],
            }
        ],
    }

    schema = AssessmentSchema(**data)
    questions = schema.sections[0].questions
    assert questions[0].question_type == "likert_5"
    assert questions[1].question_type == "yes_no"
    assert questions[0].is_risk_flag is False
    assert questions[1].is_risk_flag is True
