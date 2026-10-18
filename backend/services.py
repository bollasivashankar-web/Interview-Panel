import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models import (
    Assignment,
    Attempt,
    AttemptStatus,
    AuditLog,
    Candidate,
    InterviewTemplate,
    Invitation,
    ReattemptPermission,
)


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def audit(db: Session, company_id: str | None, actor_id: str, action: str, entity) -> None:
    db.add(
        AuditLog(
            company_id=company_id,
            actor_id=actor_id,
            action=action,
            entity_type=type(entity).__name__,
            entity_id=entity.id,
        )
    )


def create_invitation_token() -> tuple[str, str]:
    token = secrets.token_urlsafe(32)
    return token, token_hash(token)


def accept_invitation(db: Session, raw_token: str, user_id: str, email: str | None) -> Assignment:
    invitation = db.scalar(
        select(Invitation).where(Invitation.token_hash == token_hash(raw_token)).with_for_update()
    )
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    candidate = db.scalar(select(Candidate).where(Candidate.id == invitation.candidate_id).with_for_update())
    if invitation.revoked_at or invitation.accepted_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "Invitation is no longer usable")
    if aware(invitation.expires_at) <= now():
        raise HTTPException(status.HTTP_410_GONE, "Invitation expired")
    if not email or candidate.email.casefold() != email.casefold():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Verified email does not match invitation")
    if candidate.auth_user_id and candidate.auth_user_id != user_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "Candidate already linked")

    invitation.accepted_at = now()
    candidate.auth_user_id = user_id
    assignment = db.scalar(
        select(Assignment).where(
            Assignment.candidate_id == candidate.id,
            Assignment.template_id == invitation.template_id,
        )
    )
    if assignment is None:
        assignment = Assignment(
            company_id=invitation.company_id,
            candidate_id=candidate.id,
            template_id=invitation.template_id,
            available_until=invitation.expires_at,
        )
        db.add(assignment)
    elif aware(assignment.available_until) < aware(invitation.expires_at):
        assignment.available_until = invitation.expires_at
    try:
        db.flush()
        audit(db, invitation.company_id, user_id, "invitation.accept", invitation)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Invitation was accepted concurrently") from exc
    return assignment


def start_attempt(db: Session, assignment: Assignment, template: InterviewTemplate, actor_id: str) -> Attempt:
    # Serialize attempt numbering and permission consumption per assignment.
    assignment = db.scalar(select(Assignment).where(Assignment.id == assignment.id).with_for_update())
    active = db.scalar(
        select(Attempt).where(
            Attempt.assignment_id == assignment.id,
            Attempt.status == AttemptStatus.active,
        )
    )
    if active:
        if aware(active.deadline_at) > now():
            return active
        active.status = AttemptStatus.expired

    if aware(assignment.available_until) <= now():
        raise HTTPException(status.HTTP_410_GONE, "Assignment expired")

    count = db.scalar(select(func.count(Attempt.id)).where(Attempt.assignment_id == assignment.id)) or 0
    permission = db.scalar(
        select(ReattemptPermission)
        .where(
            ReattemptPermission.assignment_id == assignment.id,
            ReattemptPermission.used_at.is_(None),
            ReattemptPermission.revoked_at.is_(None),
            or_(ReattemptPermission.expires_at.is_(None), ReattemptPermission.expires_at > now()),
        )
        .order_by(ReattemptPermission.created_at)
    )
    if count >= template.attempt_limit:
        if permission is None or (permission.expires_at and aware(permission.expires_at) <= now()):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "No authorized attempts remain")
        permission.used_at = now()

    question_snapshot = [
        {"id": q.id, "text": q.text, "position": q.position, "time_limit_seconds": q.time_limit_seconds}
        for q in sorted(template.questions, key=lambda item: item.position)
        if q.approved
    ]
    if not template.published or not question_snapshot:
        raise HTTPException(status.HTTP_409_CONFLICT, "Interview is not ready")
    started = now()
    attempt = Attempt(
        assignment_id=assignment.id,
        attempt_number=count + 1,
        deadline_at=started + timedelta(minutes=template.duration_minutes),
        configuration_snapshot={
            "template_id": template.id,
            "duration_minutes": template.duration_minutes,
            "questions": question_snapshot,
        },
    )
    db.add(attempt)
    try:
        db.flush()
        audit(db, assignment.company_id, actor_id, "attempt.start", attempt)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Attempt was started concurrently") from exc
    return attempt
