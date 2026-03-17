"""Seed the complete DSM-5-TR Level 1 Cross-Cutting Symptom Measure — Child Age 11–17.

All 12 domains, 25 questions with exact text from the APA PDF, and answer options.
Scoring rule: DomainScore stores the HIGHEST item score within the domain (not the sum).
"""

import asyncio
import uuid

from sqlalchemy import select

from app.database import AsyncSessionLocal

# Import all models to ensure SQLAlchemy metadata is fully populated
import app.auth.models  # noqa: F401
import app.cases.models  # noqa: F401
import app.scheduling.models  # noqa: F401
import app.payments.models  # noqa: F401
import app.messaging.models  # noqa: F401

from app.assessments.models import (
    Assessment, AssessmentSection, AssessmentDomain, AssessmentQuestion, AnswerOption,
)

# --- Assessment metadata ---
ASSESSMENT_TITLE = "DSM-5-TR Self-Rated Level 1 Cross-Cutting Symptom Measure — Child Age 11–17"
ASSESSMENT_VERSION = "TR-2022"
AGE_RANGE_MIN = 11
AGE_RANGE_MAX = 17

# --- Likert-5 answer options (questions 1–19) ---
LIKERT_OPTIONS = [
    {"option_text": "None — Not at all", "value": 0, "order_index": 0},
    {"option_text": "Slight — Rare, less than a day or two", "value": 1, "order_index": 1},
    {"option_text": "Mild — Several days", "value": 2, "order_index": 2},
    {"option_text": "Moderate — More than half the days", "value": 3, "order_index": 3},
    {"option_text": "Severe — Nearly every day", "value": 4, "order_index": 4},
]

# --- Yes/No answer options (questions 20–25) ---
YES_NO_OPTIONS = [
    {"option_text": "Yes", "value": 1, "order_index": 0},
    {"option_text": "No", "value": 0, "order_index": 1},
]

# --- 12 Domains ---
DOMAINS = [
    {
        "domain_name": "Somatic Symptoms",
        "domain_code": "I",
        "threshold_further_inquiry": 2,
        "threshold_type": "score",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Sleep Problems",
        "domain_code": "II",
        "threshold_further_inquiry": 2,
        "threshold_type": "score",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Inattention",
        "domain_code": "III",
        "threshold_further_inquiry": 1,
        "threshold_type": "score",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Depression",
        "domain_code": "IV",
        "threshold_further_inquiry": 2,
        "threshold_type": "score",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Anger",
        "domain_code": "V",
        "threshold_further_inquiry": 2,
        "threshold_type": "score",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Irritability",
        "domain_code": "VI",
        "threshold_further_inquiry": 2,
        "threshold_type": "score",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Mania",
        "domain_code": "VII",
        "threshold_further_inquiry": 2,
        "threshold_type": "score",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Anxiety",
        "domain_code": "VIII",
        "threshold_further_inquiry": 2,
        "threshold_type": "score",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Psychosis",
        "domain_code": "IX",
        "threshold_further_inquiry": 1,
        "threshold_type": "score",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Repetitive Thoughts & Behaviors",
        "domain_code": "X",
        "threshold_further_inquiry": 2,
        "threshold_type": "score",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Substance Use",
        "domain_code": "XI",
        "threshold_further_inquiry": 1,
        "threshold_type": "yes_no",
        "is_safety_critical": False,
    },
    {
        "domain_name": "Suicidal Ideation / Suicide Attempts",
        "domain_code": "XII",
        "threshold_further_inquiry": 1,
        "threshold_type": "yes_no",
        "is_safety_critical": True,
    },
]

# --- All 25 questions with exact text from the APA DSM-5-TR PDF ---
# domain_code maps each question to its domain
QUESTIONS = [
    {
        "order_index": 1,
        "domain_code": "I",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Been bothered by stomachaches, headaches, or other aches and pains?",
    },
    {
        "order_index": 2,
        "domain_code": "I",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Worried about your health or about getting sick?",
    },
    {
        "order_index": 3,
        "domain_code": "II",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Been bothered by not being able to fall asleep or stay asleep, or by waking up too early?",
    },
    {
        "order_index": 4,
        "domain_code": "III",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Been bothered by not being able to pay attention when you were in class or doing homework or reading a book or playing a game?",
    },
    {
        "order_index": 5,
        "domain_code": "IV",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Had less fun doing things than you used to?",
    },
    {
        "order_index": 6,
        "domain_code": "IV",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Felt sad or depressed for several hours?",
    },
    {
        "order_index": 7,
        "domain_code": "V",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Felt more irritated or easily annoyed than usual?",
    },
    {
        "order_index": 8,
        "domain_code": "VI",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Felt angry or lost your temper?",
    },
    {
        "order_index": 9,
        "domain_code": "VII",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Started lots more projects than usual or done more risky things than usual?",
    },
    {
        "order_index": 10,
        "domain_code": "VII",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Slept less than usual but still had a lot of energy?",
    },
    {
        "order_index": 11,
        "domain_code": "VIII",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Felt nervous, anxious, or scared?",
    },
    {
        "order_index": 12,
        "domain_code": "VIII",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Not been able to stop worrying?",
    },
    {
        "order_index": 13,
        "domain_code": "VIII",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Not been able to do things you wanted to or should have done, because they made you feel nervous?",
    },
    {
        "order_index": 14,
        "domain_code": "IX",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Heard voices—when there was no one there—speaking about you or telling you what to do or saying bad things to you?",
    },
    {
        "order_index": 15,
        "domain_code": "IX",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Had visions when you were completely awake—that is, seen something or someone that no one else could see?",
    },
    {
        "order_index": 16,
        "domain_code": "X",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Had thoughts that kept coming into your mind that you would do something bad or that something bad would happen to you or to someone else?",
    },
    {
        "order_index": 17,
        "domain_code": "X",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Felt the need to check on certain things over and over again, like whether a door was locked or whether the stove was turned off?",
    },
    {
        "order_index": 18,
        "domain_code": "X",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Worried a lot about things you touched being dirty or having germs or being poisoned?",
    },
    {
        "order_index": 19,
        "domain_code": "X",
        "question_type": "likert_5",
        "is_risk_flag": False,
        "question_text": "Felt you had to do things in a certain way, like counting or saying special things, to keep something bad from happening?",
    },
    {
        "order_index": 20,
        "domain_code": "XI",
        "question_type": "yes_no",
        "is_risk_flag": False,
        "question_text": "Had an alcoholic beverage (beer, wine, liquor, etc.)?",
    },
    {
        "order_index": 21,
        "domain_code": "XI",
        "question_type": "yes_no",
        "is_risk_flag": False,
        "question_text": "Smoked a cigarette, a cigar, or pipe, or used snuff or chewing tobacco?",
    },
    {
        "order_index": 22,
        "domain_code": "XI",
        "question_type": "yes_no",
        "is_risk_flag": False,
        "question_text": "Used drugs like marijuana, cocaine or crack, club drugs (like Ecstasy), hallucinogens (like LSD), heroin, inhalants or solvents (like glue), or methamphetamine (like speed)?",
    },
    {
        "order_index": 23,
        "domain_code": "XI",
        "question_type": "yes_no",
        "is_risk_flag": False,
        "question_text": "Used any medicine without a doctor's prescription to get high or change the way you feel (e.g., painkillers [like Vicodin], stimulants [like Ritalin or Adderall], sedatives or tranquilizers [like sleeping pills or Valium], or steroids)?",
    },
    {
        "order_index": 24,
        "domain_code": "XII",
        "question_type": "yes_no",
        "is_risk_flag": True,
        "question_text": "In the last 2 weeks, have you thought about killing yourself or committing suicide?",
    },
    {
        "order_index": 25,
        "domain_code": "XII",
        "question_type": "yes_no",
        "is_risk_flag": True,
        "question_text": "Have you EVER tried to kill yourself?",
    },
]


async def seed_dsm5() -> None:
    async with AsyncSessionLocal() as session:
        # Check if already seeded
        existing = await session.execute(
            select(Assessment).where(Assessment.version == ASSESSMENT_VERSION)
        )
        if existing.scalar_one_or_none() is not None:
            print("DSM-5-TR assessment already seeded. Skipping.")
            return

        # Create assessment
        assessment_id = uuid.uuid4()
        assessment = Assessment(
            id=assessment_id,
            title=ASSESSMENT_TITLE,
            description="Self-rated cross-cutting symptom measure for children age 11–17. "
                        "Assesses 12 psychiatric domains per DSM-5-TR guidelines.",
            version=ASSESSMENT_VERSION,
            source_pdf_filename="APA-DSM5TR-Level1MeasureChildAge11To17.pdf",
            is_active=True,
            age_range_min=AGE_RANGE_MIN,
            age_range_max=AGE_RANGE_MAX,
        )
        session.add(assessment)

        # Create a single section (this measure is one continuous section)
        section_id = uuid.uuid4()
        section = AssessmentSection(
            id=section_id,
            assessment_id=assessment_id,
            title="Level 1 Cross-Cutting Symptoms",
            description="During the past TWO (2) WEEKS, how much (or how often) have you…",
            order_index=0,
        )
        session.add(section)

        # Create domains
        domain_map = {}  # domain_code -> domain_id
        for d in DOMAINS:
            domain_id = uuid.uuid4()
            domain_map[d["domain_code"]] = domain_id
            domain = AssessmentDomain(
                id=domain_id,
                assessment_id=assessment_id,
                **d,
            )
            session.add(domain)

        # Create questions and answer options
        for q in QUESTIONS:
            question_id = uuid.uuid4()
            question = AssessmentQuestion(
                id=question_id,
                section_id=section_id,
                question_text=q["question_text"],
                question_type=q["question_type"],
                order_index=q["order_index"],
                domain_id=domain_map[q["domain_code"]],
                is_required=True,
                is_risk_flag=q["is_risk_flag"],
            )
            session.add(question)

            # Add answer options
            options = LIKERT_OPTIONS if q["question_type"] == "likert_5" else YES_NO_OPTIONS
            for opt in options:
                answer_option = AnswerOption(
                    id=uuid.uuid4(),
                    question_id=question_id,
                    **opt,
                )
                session.add(answer_option)

        await session.commit()
        print(f"Seeded DSM-5-TR assessment: {ASSESSMENT_TITLE}")
        print(f"  - {len(DOMAINS)} domains")
        print(f"  - {len(QUESTIONS)} questions")
        print(f"  - Domain XII (Suicidal Ideation) marked as safety-critical with P0 alert")


if __name__ == "__main__":
    asyncio.run(seed_dsm5())
