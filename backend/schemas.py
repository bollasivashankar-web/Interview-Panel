from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


def validate_invitation_expiry(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("expires_at must include a timezone")
    current = datetime.now(timezone.utc)
    if value <= current:
        raise ValueError("expires_at must be in the future")
    if value > current + timedelta(days=365):
        raise ValueError("expires_at must be within one year")
    return value


class CompanyCreate(StrictModel):
    name: str = Field(min_length=2, max_length=160)


class CompanyRead(ORMModel):
    id: str
    name: str


class JobCreate(StrictModel):
    title: str = Field(min_length=2, max_length=160)
    description: str = Field(min_length=10, max_length=20_000)
    required_skills: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("required_skills")
    @classmethod
    def unique_skills(cls, value: list[str]) -> list[str]:
        cleaned = [skill.strip() for skill in value if skill.strip()]
        if len(set(map(str.casefold, cleaned))) != len(cleaned):
            raise ValueError("required_skills must be unique")
        return cleaned


class JobRead(ORMModel):
    id: str
    title: str
    description: str
    required_skills: list[str]


class QuestionCreate(StrictModel):
    text: str = Field(min_length=5, max_length=5000)
    position: int = Field(ge=1, le=200)
    time_limit_seconds: int = Field(ge=30, le=3600)
    rubric: dict = Field(default_factory=dict)


class QuestionRead(ORMModel):
    id: str
    template_id: str
    text: str
    position: int
    time_limit_seconds: int
    approved: bool
    rubric: dict


class QuestionUpdate(StrictModel):
    text: str = Field(min_length=5, max_length=5000)
    time_limit_seconds: int = Field(ge=30, le=3600)


class QuestionGenerationRequest(StrictModel):
    count: int = Field(default=5, ge=1, le=20)
    difficulty: Literal["easy", "medium", "hard", "mixed"] = "mixed"


class QuestionBulkApprovalRequest(StrictModel):
    approved: bool


class QuestionImportRequest(StrictModel):
    questions: list[str] = Field(min_length=1, max_length=200)
    time_limit_seconds: int = Field(default=180, ge=30, le=3600)

    @field_validator("questions")
    @classmethod
    def clean_questions(cls, value: list[str]) -> list[str]:
        cleaned = [" ".join(question.split()) for question in value]
        if any(len(question) < 5 or len(question) > 5_000 for question in cleaned):
            raise ValueError("Each question must contain 5 to 5000 characters")
        if len(set(map(str.casefold, cleaned))) != len(cleaned):
            raise ValueError("questions must be unique")
        return cleaned


class PdfQuestionExtraction(StrictModel):
    questions: list[str]


class GeneratedCriterion(StrictModel):
    name: str = Field(min_length=2, max_length=100)
    description: str = Field(min_length=5, max_length=500)
    max_points: int = Field(ge=1, le=100)


class GeneratedQuestion(StrictModel):
    text: str = Field(min_length=10, max_length=5000)
    difficulty: Literal["easy", "medium", "hard"]
    competency: str = Field(min_length=2, max_length=160)
    time_limit_seconds: int = Field(ge=30, le=3600)
    criteria: list[GeneratedCriterion] = Field(min_length=1, max_length=10)


class GeneratedQuestionBatch(StrictModel):
    questions: list[GeneratedQuestion] = Field(min_length=1, max_length=20)


class TemplateCreate(StrictModel):
    job_id: str
    name: str = Field(min_length=2, max_length=160)
    duration_minutes: int = Field(ge=5, le=480)
    attempt_limit: int = Field(default=1, ge=1, le=10)


class TemplateRead(ORMModel):
    id: str
    job_id: str
    name: str
    duration_minutes: int
    attempt_limit: int
    published: bool


class InvitationCreate(StrictModel):
    email: EmailStr
    name: str | None = Field(default=None, max_length=160)
    job_id: str
    template_id: str
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def valid_expiry(cls, value: datetime | None) -> datetime | None:
        return validate_invitation_expiry(value) if value else None


class InvitationRead(BaseModel):
    id: str
    expires_at: datetime
    delivery_status: str
    token: str | None = None


class InvitationExpiryUpdate(StrictModel):
    expires_at: datetime

    @field_validator("expires_at")
    @classmethod
    def valid_expiry(cls, value: datetime) -> datetime:
        return validate_invitation_expiry(value)


class InvitationExpiryRead(StrictModel):
    id: str
    expires_at: datetime


class InvitationAccept(StrictModel):
    token: str = Field(min_length=32, max_length=512)


class AssignmentRead(ORMModel):
    id: str
    template_id: str
    available_until: datetime


class AnswerCreate(StrictModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    question_id: str
    transcript: str = Field(min_length=1, max_length=50_000)


class AttemptRead(ORMModel):
    id: str
    assignment_id: str
    attempt_number: int
    status: str
    started_at: datetime
    deadline_at: datetime
    configuration_snapshot: dict


class CandidateAttemptRead(AttemptRead):
    answered_question_ids: list[str]


class InterviewResultRead(StrictModel):
    id: str
    candidate_name: str | None
    candidate_email: str
    job_title: str
    interview_name: str
    attempt_number: int
    status: str
    started_at: datetime
    completed_at: datetime | None
    answered_count: int
    question_count: int
    average_score: float | None = None
    evaluated_at: datetime | None = None


class ResultAnswerRead(StrictModel):
    question_id: str
    question: str
    transcript: str | None
    submitted_at: datetime | None
    score: float | None = None
    feedback: str | None = None


class InterviewResultDetail(InterviewResultRead):
    answers: list[ResultAnswerRead]


class AnswerEvaluation(StrictModel):
    question_id: str
    score: float = Field(ge=0, le=10, strict=True, allow_inf_nan=False)
    feedback: str = Field(min_length=10, max_length=2000)


class AnswerEvaluationBatch(StrictModel):
    evaluations: list[AnswerEvaluation] = Field(min_length=1, max_length=200)


class ReattemptCreate(StrictModel):
    reason: str = Field(min_length=10, max_length=2000)
    expires_at: datetime | None = None

    @field_validator("expires_at")
    @classmethod
    def valid_expiry(cls, value: datetime | None) -> datetime | None:
        return validate_invitation_expiry(value) if value else None

