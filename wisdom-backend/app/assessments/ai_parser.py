"""Assessment PDF parser — local rule-based extraction.

Parses clinical assessment PDFs into structured JSON using pdfminer.six
text extraction + regex pattern matching. Zero API calls required.

Supports: DSM-5, PHQ-9, GAD-7, PCL-5, and generic numbered questionnaires.
"""

import logging
import re
from uuid import UUID, uuid4

from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.assessments.models import (
    AnswerOption,
    Assessment,
    AssessmentDomain,
    AssessmentQuestion,
    AssessmentSection,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema for parsed output validation
# ---------------------------------------------------------------------------

class ParsedAnswerOption(BaseModel):
    option_text: str
    value: int
    order_index: int


class ParsedQuestion(BaseModel):
    order_index: int
    question_text: str
    question_type: str
    domain_code: str
    is_risk_flag: bool = False
    answer_options: list[ParsedAnswerOption] = []


class ParsedDomain(BaseModel):
    domain_name: str
    domain_code: str
    threshold_further_inquiry: int
    threshold_type: str = "score"
    is_safety_critical: bool = False


class ParsedSection(BaseModel):
    title: str
    description: str | None = None
    order_index: int = 0
    questions: list[ParsedQuestion] = []


class AssessmentSchema(BaseModel):
    title: str
    description: str | None = None
    version: str | None = None
    age_range_min: int | None = None
    age_range_max: int | None = None
    domains: list[ParsedDomain]
    sections: list[ParsedSection]


# ---------------------------------------------------------------------------
# Answer scale templates
# ---------------------------------------------------------------------------

LIKERT_5_SCALE = [
    {"option_text": "None — Not at all", "value": 0, "order_index": 0},
    {"option_text": "Slight — Rare, less than a day or two", "value": 1, "order_index": 1},
    {"option_text": "Mild — Several days", "value": 2, "order_index": 2},
    {"option_text": "Moderate — More than half the days", "value": 3, "order_index": 3},
    {"option_text": "Severe — Nearly every day", "value": 4, "order_index": 4},
]

YES_NO_SCALE = [
    {"option_text": "Yes", "value": 1, "order_index": 0},
    {"option_text": "No", "value": 0, "order_index": 1},
]

FREQUENCY_4_SCALE = [
    {"option_text": "Not at all", "value": 0, "order_index": 0},
    {"option_text": "Several days", "value": 1, "order_index": 1},
    {"option_text": "More than half the days", "value": 2, "order_index": 2},
    {"option_text": "Nearly every day", "value": 3, "order_index": 3},
]

SAFETY_KEYWORDS = [
    "suicide", "suicidal", "killing yourself", "kill yourself",
    "self-harm", "self harm", "hurt yourself", "end your life",
    "thoughts of death", "better off dead", "wish you were dead",
    "harming yourself", "tried to kill",
]


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

def _is_safety_critical(text: str) -> bool:
    return any(kw in text.lower() for kw in SAFETY_KEYWORDS)


def _extract_title(text: str) -> str:
    """Extract the assessment title — first substantial non-boilerplate line."""
    skip_words = [
        "page", "copyright", "\u00a9", "all rights reserved", "http",
        "www.", "instructions:", "date:", "name:", "today",
        "clinician", "office use", "scoring",
    ]
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines[:15]:
        if len(line) < 10:
            continue
        if re.match(r"^\d+[.):]", line):
            continue
        if any(s in line.lower() for s in skip_words):
            continue
        # Good candidate
        return line.strip()
    return lines[0] if lines else "Untitled Assessment"


def _extract_version(text: str) -> str | None:
    patterns = [
        r"(TR-\d{4})",
        r"(PHQ-\d+)",
        r"(GAD-\d+)",
        r"(PCL-\d+)",
        r"(PSC-\d+)",
        r"(SCARED)",
        r"(SDQ)",
        r"(ACE[Ss]?\s*\d*)",
        r"(?:version|ver\.?|v)\s*[:\s]?\s*([A-Za-z0-9.\-]+)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _extract_age_range(text: str) -> tuple[int | None, int | None]:
    patterns = [
        r"(?:child(?:ren)?|youth|adolescent)?\s*age[s]?\s*(\d{1,2})\s*(?:to|–|-|—)\s*(\d{1,2})",
        r"(\d{1,2})\s*(?:to|–|-|—)\s*(\d{1,2})\s*(?:years|yrs|year)",
        r"ages?\s*(\d{1,2})\s*(?:and\s+)?(?:older|above|up|\+)",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            lo = int(m.group(1))
            hi = int(m.group(2)) if m.lastindex >= 2 else 99
            return lo, hi
    return None, None


def _extract_description(text: str) -> str | None:
    """Find the instruction/prompt line (e.g. 'During the past two weeks...')."""
    patterns = [
        r"((?:during|over|in)\s+the\s+(?:past|last)\s+.{10,120}?[.?])",
        r"(how\s+(?:much|often|many).{10,100}?[.?])",
        r"(please\s+(?:answer|rate|indicate).{10,100}?[.?])",
        r"(below\s+is\s+a\s+list.{10,100}?[.?])",
    ]
    for p in patterns:
        m = re.search(p, text, re.IGNORECASE)
        if m:
            return m.group(1).strip()
    return None


def _detect_answer_scale(text: str) -> tuple[str, list[dict]]:
    """Detect which answer scale the PDF uses by scanning full text."""
    t = text.lower()

    # DSM-5 style: None / Slight / Mild / Moderate / Severe
    if "slight" in t and "severe" in t:
        return "likert_5", LIKERT_5_SCALE

    # PHQ/GAD style: Not at all / Several days / More than half / Nearly every day
    if "not at all" in t and "several days" in t:
        return "likert_4", FREQUENCY_4_SCALE

    # Yes/No only
    yes_count = len(re.findall(r"\byes\b", t))
    no_count = len(re.findall(r"\bno\b", t))
    if yes_count > 3 and no_count > 3:
        return "yes_no", YES_NO_SCALE

    # Never / Rarely / Sometimes / Often / Always
    if "rarely" in t and "always" in t:
        return "likert_5", [
            {"option_text": "Never", "value": 0, "order_index": 0},
            {"option_text": "Rarely", "value": 1, "order_index": 1},
            {"option_text": "Sometimes", "value": 2, "order_index": 2},
            {"option_text": "Often", "value": 3, "order_index": 3},
            {"option_text": "Always", "value": 4, "order_index": 4},
        ]

    # Default
    return "likert_5", LIKERT_5_SCALE


def _extract_questions(text: str, tables: list) -> list[dict]:
    """Extract questions from both text and tables, merge best result."""

    # --- Strategy 1: Numbered lines from text ---
    text_questions = []
    # Match: "1. text", "1) text", "Q1. text", "1 - text", "1  text..."
    # Use MULTILINE so ^ matches each line start
    for m in re.finditer(
        r"^[ \t]*(?:Q\.?\s*)?(\d{1,2})\s*[.):\-–—]?\s+(.+)",
        text,
        re.MULTILINE,
    ):
        num = int(m.group(1))
        q_text = m.group(2).strip()
        # Remove trailing scale labels that leaked into question text
        q_text = re.split(
            r"\b(?:None|Not at all|Slight|Mild|Moderate|Severe|Yes|No|Never|Rarely|Sometimes|Often|Always)\s*(?:—|–|-|$)",
            q_text,
        )[0].strip()
        # Clean up whitespace
        q_text = re.sub(r"\s+", " ", q_text).strip()
        if len(q_text) >= 10 and num <= 100:
            text_questions.append({"order_index": num, "question_text": q_text})

    # --- Strategy 2: Questions from table rows ---
    table_questions = []
    for table in tables:
        for row in table.get("rows", []):
            if not row:
                continue
            first_cell = str(row[0] or "").strip()
            if not first_cell:
                continue
            nm = re.match(r"^(\d{1,2})\s*[.):\-]?\s*(.*)", first_cell)
            if nm and len(nm.group(2).strip()) >= 10:
                table_questions.append({
                    "order_index": int(nm.group(1)),
                    "question_text": nm.group(2).strip(),
                })
            elif len(first_cell) > 15 and any(
                first_cell.lower().startswith(w)
                for w in [
                    "been", "felt", "had", "have", "has", "do", "did",
                    "were", "was", "are", "is", "not been", "started",
                    "little", "feeling", "trouble", "poor", "moving",
                    "thoughts", "worried", "heard", "slept",
                ]
            ):
                table_questions.append({
                    "order_index": len(table_questions) + 1,
                    "question_text": first_cell,
                })

    # Pick the richer source
    questions = text_questions if len(text_questions) >= len(table_questions) else table_questions

    # Deduplicate by order_index, keeping first occurrence
    seen: set[int] = set()
    unique: list[dict] = []
    for q in sorted(questions, key=lambda x: x["order_index"]):
        if q["order_index"] not in seen:
            seen.add(q["order_index"])
            unique.append(q)

    return unique


def _build_domains(questions: list[dict], text: str) -> tuple[list[dict], list[dict]]:
    """Detect domain structure and assign questions.

    Returns (updated_questions, domains_list).
    """
    # --- Try to find explicit domain markers in text ---
    # DSM-5 style: "Domain I", "I.", "II.", or roman-numeral headers
    domain_header_re = re.compile(
        r"(?:Domain\s+)?(I{1,3}V?|VI{0,3}|IX|X{1,3}I{0,2})\s*[.:\-–—]\s*([A-Z][A-Za-z &/\-()]+)",
    )
    found_domains: list[tuple[str, str]] = domain_header_re.findall(text)

    # Filter to valid roman numerals only
    valid_roman = {
        "I", "II", "III", "IV", "V", "VI", "VII", "VIII",
        "IX", "X", "XI", "XII", "XIII", "XIV", "XV",
    }
    found_domains = [(c, n.strip().rstrip(".")) for c, n in found_domains if c in valid_roman]

    # Remove duplicates preserving order
    seen_codes: set[str] = set()
    domains: list[dict] = []
    for code, name in found_domains:
        if code not in seen_codes and len(name) > 2:
            seen_codes.add(code)
            domains.append({
                "domain_name": name,
                "domain_code": code,
                "threshold_further_inquiry": 2,
                "threshold_type": "score",
                "is_safety_critical": _is_safety_critical(name),
            })

    # If no domains found, create one based on the assessment type
    if not domains:
        title_lower = text[:500].lower()
        if "phq" in title_lower:
            domains = [{"domain_name": "Depression", "domain_code": "I",
                        "threshold_further_inquiry": 2, "threshold_type": "score",
                        "is_safety_critical": False}]
        elif "gad" in title_lower:
            domains = [{"domain_name": "Anxiety", "domain_code": "I",
                        "threshold_further_inquiry": 2, "threshold_type": "score",
                        "is_safety_critical": False}]
        else:
            domains = [{"domain_name": "General", "domain_code": "I",
                        "threshold_further_inquiry": 2, "threshold_type": "score",
                        "is_safety_critical": False}]

    # Assign questions to domains
    if len(domains) == 1:
        for q in questions:
            q["domain_code"] = domains[0]["domain_code"]
    elif len(domains) > 1:
        # Distribute questions across domains proportionally
        per_domain = max(1, len(questions) // len(domains))
        for i, q in enumerate(questions):
            idx = min(i // per_domain, len(domains) - 1)
            q["domain_code"] = domains[idx]["domain_code"]

    # Safety-critical adjustments
    for d in domains:
        if d["is_safety_critical"]:
            d["threshold_type"] = "yes_no"
            d["threshold_further_inquiry"] = 1

    # Check individual questions for safety and create a safety domain if needed
    safety_questions = [q for q in questions if _is_safety_critical(q["question_text"])]
    if safety_questions:
        # Check if there's already a safety-critical domain
        has_safety_domain = any(d["is_safety_critical"] for d in domains)
        if not has_safety_domain:
            safety_code = "S"
            domains.append({
                "domain_name": "Suicidal Ideation / Self-Harm",
                "domain_code": safety_code,
                "threshold_further_inquiry": 1,
                "threshold_type": "yes_no",
                "is_safety_critical": True,
            })
            for q in safety_questions:
                q["domain_code"] = safety_code

    return questions, domains


# ---------------------------------------------------------------------------
# Main local parser
# ---------------------------------------------------------------------------

async def parse_assessment_locally(raw_text: str, tables: list) -> dict:
    """Parse a clinical assessment PDF using local rules — no API calls."""

    title = _extract_title(raw_text)
    version = _extract_version(raw_text)
    age_min, age_max = _extract_age_range(raw_text)
    description = _extract_description(raw_text)
    default_type, default_scale = _detect_answer_scale(raw_text)

    # Extract questions
    questions = _extract_questions(raw_text, tables)

    # Assign types and options to each question
    for q in questions:
        if _is_safety_critical(q["question_text"]):
            q["is_risk_flag"] = True
            q["question_type"] = "yes_no"
            q["answer_options"] = YES_NO_SCALE
        else:
            q["is_risk_flag"] = False
            q["question_type"] = default_type
            q["answer_options"] = default_scale

    # Build domains and assign questions to them
    questions, domains = _build_domains(questions, raw_text)

    section_title = title.split("\u2014")[0].strip() if "\u2014" in title else title
    result = {
        "title": title,
        "description": description,
        "version": version,
        "age_range_min": age_min,
        "age_range_max": age_max,
        "domains": domains,
        "sections": [{
            "title": section_title,
            "description": description,
            "order_index": 0,
            "questions": questions,
        }],
    }

    try:
        validated = AssessmentSchema(**result)
        return validated.model_dump()
    except ValidationError as e:
        logger.warning(f"Local parse validation issue: {e}")
        return result


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def parse_assessment_with_ai(raw_text: str, tables: list) -> dict:
    """Parse assessment locally (free). AI fallback only if 0 questions found."""
    result = await parse_assessment_locally(raw_text, tables)

    total_q = sum(len(s.get("questions", [])) for s in result.get("sections", []))
    if total_q > 0:
        logger.info(f"Local parser: {total_q} questions, no API needed")
        return result

    # AI fallback (optional)
    from app.config import get_settings
    settings = get_settings()
    if not settings.LITELLM_API_KEY:
        return result

    logger.info("Local parser: 0 questions — trying AI fallback")
    try:
        import json as _json
        import litellm

        prompt = (
            "Parse this clinical assessment into JSON: title, version, "
            "age_range_min, age_range_max, domains[], sections[{questions[]}]. "
            f"TEXT:\n{raw_text}\nTABLES:\n{_json.dumps(tables)}"
        )
        resp = await litellm.acompletion(
            model=settings.LITELLM_MODEL,
            messages=[{"role": "user", "content": prompt}],
            api_key=settings.LITELLM_API_KEY,
            response_format={"type": "json_object"},
            max_tokens=4000,
            temperature=0.1,
        )
        parsed = _json.loads(resp.choices[0].message.content)
        return AssessmentSchema(**parsed).model_dump()
    except Exception as e:
        logger.error(f"AI fallback failed: {e}")
        return result


async def calculate_confidence_score(parsed: dict, raw_text: str) -> float:
    """Calculate confidence 0.0–1.0 based on extraction completeness."""
    points = 0.0
    max_points = 0.0

    # Title (required, 15 pts)
    max_points += 15
    title = parsed.get("title", "")
    if title and len(title) > 5 and title != "Untitled Assessment":
        points += 15

    # Questions extracted (required, 35 pts)
    max_points += 35
    sections = parsed.get("sections", [])
    total_q = sum(len(s.get("questions", [])) for s in sections)
    if total_q >= 1:
        points += 10
    if total_q >= 3:
        points += 10
    if total_q >= 5:
        points += 10
    if total_q >= 9:
        points += 5

    # Answer options present (15 pts)
    max_points += 15
    has_options = any(
        q.get("answer_options") for s in sections for q in s.get("questions", [])
    )
    if has_options:
        points += 15

    # Domains (10 pts)
    max_points += 10
    domains = parsed.get("domains", [])
    if len(domains) >= 1:
        points += 5
    if len(domains) >= 2:
        points += 5

    # Version detected (10 pts)
    max_points += 10
    if parsed.get("version"):
        points += 10

    # Age range (5 pts)
    max_points += 5
    if parsed.get("age_range_min") is not None:
        points += 5

    # Description / instructions (5 pts)
    max_points += 5
    if parsed.get("description"):
        points += 5

    # Safety flags detected where relevant (5 pts)
    max_points += 5
    has_safety = any(d.get("is_safety_critical") for d in domains)
    has_risk_q = any(
        q.get("is_risk_flag") for s in sections for q in s.get("questions", [])
    )
    if has_safety or has_risk_q or not any(_is_safety_critical(raw_text) for _ in [1]):
        # Give points if safety correctly detected OR no safety content in PDF
        text_has_safety = _is_safety_critical(raw_text)
        if text_has_safety and (has_safety or has_risk_q):
            points += 5
        elif not text_has_safety:
            points += 5  # No safety content — correct to not flag

    return round(points / max_points, 2) if max_points > 0 else 0.0


# ---------------------------------------------------------------------------
# DB writer (unchanged)
# ---------------------------------------------------------------------------

async def create_assessment_from_parsed(
    parsed: dict,
    uploaded_by: UUID,
    db: AsyncSession,
    is_active: bool = True,
) -> Assessment:
    """Create Assessment + sections + domains + questions + answer options from parsed JSON."""
    assessment_id = uuid4()
    assessment = Assessment(
        id=assessment_id,
        title=parsed["title"],
        description=parsed.get("description"),
        version=parsed.get("version"),
        is_active=is_active,
        created_by=uploaded_by,
        age_range_min=parsed.get("age_range_min"),
        age_range_max=parsed.get("age_range_max"),
    )
    db.add(assessment)

    domain_map = {}
    for d in parsed.get("domains", []):
        domain_id = uuid4()
        domain_map[d["domain_code"]] = domain_id
        db.add(AssessmentDomain(
            id=domain_id,
            assessment_id=assessment_id,
            domain_name=d["domain_name"],
            domain_code=d["domain_code"],
            threshold_further_inquiry=d.get("threshold_further_inquiry", 2),
            threshold_type=d.get("threshold_type", "score"),
            is_safety_critical=d.get("is_safety_critical", False),
        ))

    for section_data in parsed.get("sections", []):
        section_id = uuid4()
        db.add(AssessmentSection(
            id=section_id,
            assessment_id=assessment_id,
            title=section_data["title"],
            description=section_data.get("description"),
            order_index=section_data.get("order_index", 0),
        ))

        for q in section_data.get("questions", []):
            question_id = uuid4()
            domain_id = domain_map.get(q.get("domain_code"))
            db.add(AssessmentQuestion(
                id=question_id,
                section_id=section_id,
                question_text=q["question_text"],
                question_type=q["question_type"],
                order_index=q.get("order_index", 0),
                domain_id=domain_id,
                is_required=True,
                is_risk_flag=q.get("is_risk_flag", False),
            ))
            for opt in q.get("answer_options", []):
                db.add(AnswerOption(
                    id=uuid4(),
                    question_id=question_id,
                    option_text=opt["option_text"],
                    value=opt["value"],
                    order_index=opt.get("order_index", 0),
                ))

    await db.commit()
    await db.refresh(assessment)
    return assessment
