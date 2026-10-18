import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, enum.Enum):
    admin = "admin"
    hr_manager = "hr_manager"
    interviewer = "interviewer"


class AttemptStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    suspended = "suspended"
    expired = "expired"


class IdMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Company(IdMixin, Base):
    __tablename__ = "companies"
    name: Mapped[str] = mapped_column(String(160))


class Membership(IdMixin, Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("auth_user_id", "company_id"),
        Index("uq_memberships_auth_user_id", "auth_user_id", unique=True),
    )
    auth_user_id: Mapped[str] = mapped_column(String(128), index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    role: Mapped[Role] = mapped_column(Enum(Role))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Job(IdMixin, Base):
    __tablename__ = "jobs"
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str] = mapped_column(Text)
    required_skills: Mapped[list] = mapped_column(JSON, default=list)


class Candidate(IdMixin, Base):
    __tablename__ = "candidates"
    __table_args__ = (UniqueConstraint("company_id", "email"),)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    email: Mapped[str] = mapped_column(String(320))
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    auth_user_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)


class InterviewTemplate(IdMixin, Base):
    __tablename__ = "interview_templates"
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(160))
    duration_minutes: Mapped[int] = mapped_column(Integer)
    attempt_limit: Mapped[int] = mapped_column(Integer, default=1)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    questions: Mapped[list["Question"]] = relationship(cascade="all, delete-orphan")


class Question(IdMixin, Base):
    __tablename__ = "questions"
    __table_args__ = (Index("uq_questions_template_position", "template_id", "position", unique=True),)
    template_id: Mapped[str] = mapped_column(ForeignKey("interview_templates.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    time_limit_seconds: Mapped[int] = mapped_column(Integer)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    rubric: Mapped[dict] = mapped_column(JSON, default=dict)


class Invitation(IdMixin, Base):
    __tablename__ = "invitations"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"))
    template_id: Mapped[str] = mapped_column(ForeignKey("interview_templates.id", ondelete="CASCADE"))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Assignment(IdMixin, Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("candidate_id", "template_id"),)
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    template_id: Mapped[str] = mapped_column(ForeignKey("interview_templates.id", ondelete="CASCADE"))
    available_until: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Attempt(IdMixin, Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("assignment_id", "attempt_number"),)
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), index=True)
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[AttemptStatus] = mapped_column(Enum(AttemptStatus), default=AttemptStatus.active)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    configuration_snapshot: Mapped[dict] = mapped_column(JSON)
    evaluation: Mapped[dict | None] = mapped_column(JSON(none_as_null=True), nullable=True)


class Answer(IdMixin, Base):
    __tablename__ = "answers"
    __table_args__ = (UniqueConstraint("attempt_id", "question_id"),)
    attempt_id: Mapped[str] = mapped_column(ForeignKey("attempts.id", ondelete="CASCADE"), index=True)
    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"))
    transcript: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ReattemptPermission(IdMixin, Base):
    __tablename__ = "reattempt_permissions"
    assignment_id: Mapped[str] = mapped_column(ForeignKey("assignments.id", ondelete="CASCADE"), index=True)
    granted_by: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(IdMixin, Base):
    __tablename__ = "audit_logs"
    company_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(100))
    entity_type: Mapped[str] = mapped_column(String(80))
    entity_id: Mapped[str] = mapped_column(String(36))
    details: Mapped[dict] = mapped_column(JSON, default=dict)


class Notification(IdMixin, Base):
    __tablename__ = "notifications"
    company_id: Mapped[str] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("candidates.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(20), default="email")
    recipient: Mapped[str] = mapped_column(String(320))
    template: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(160), nullable=True)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
