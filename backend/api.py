from datetime import timedelta

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from starlette.concurrency import run_in_threadpool

from backend.auth import CompanyContext, Principal, company_context, current_principal, require_roles
from backend.config import get_settings
from backend.database import get_db
from backend.models import (
    Answer,
    Assignment,
    Attempt,
    AttemptStatus,
    Candidate,
    Company,
    InterviewTemplate,
    Invitation,
    Job,
    Membership,
    Notification,
    Question,
    ReattemptPermission,
    Role,
)
from backend.pdf_questions import MAX_PDF_BYTES, PdfQuestionError
from backend.pdf_processing import PdfBusyError, process_pdf
from backend.schemas import (
    InterviewResultRead,
    InterviewResultDetail,
    ResultAnswerRead,
    AnswerCreate,
    AssignmentRead,
    AttemptRead,
    CandidateAttemptRead,
    CompanyCreate,
    CompanyRead,
    InvitationAccept,
    InvitationCreate,
    InvitationExpiryRead,
    InvitationExpiryUpdate,
    InvitationRead,
    JobCreate,
    JobRead,
    QuestionBulkApprovalRequest,
    QuestionCreate,
    QuestionGenerationRequest,
    QuestionImportRequest,
    QuestionRead,
    QuestionUpdate,
    PdfQuestionExtraction,
    ReattemptCreate,
    TemplateCreate,
    TemplateRead,
)
from backend.providers import OpenRouterEvaluationProvider, OpenRouterQuestionProvider, ProviderError, get_email_provider
from backend.services import accept_invitation, audit, aware, create_invitation_token, now, start_attempt


router = APIRouter(prefix="/api/v1")
manager = require_roles(Role.admin, Role.hr_manager)


def result_query(company_id: str):
    return (
        select(Attempt, Candidate, InterviewTemplate, Job)
        .join(Assignment, Attempt.assignment_id == Assignment.id)
        .join(Candidate, Assignment.candidate_id == Candidate.id)
        .join(InterviewTemplate, Assignment.template_id == InterviewTemplate.id)
        .join(Job, InterviewTemplate.job_id == Job.id)
        .where(Assignment.company_id == company_id)
    )


def result_summary(row, answered_count: int) -> InterviewResultRead:
    attempt, candidate, template, job = row
    effective_status = attempt.status.value
    if attempt.status == AttemptStatus.active and aware(attempt.deadline_at) <= now():
        effective_status = AttemptStatus.expired.value
    return InterviewResultRead(
        id=attempt.id, candidate_name=candidate.name, candidate_email=candidate.email,
        job_title=job.title, interview_name=template.name, attempt_number=attempt.attempt_number,
        status=effective_status, started_at=aware(attempt.started_at),
        completed_at=aware(attempt.completed_at) if attempt.completed_at else None,
        answered_count=answered_count,
        question_count=len(attempt.configuration_snapshot.get("questions", [])),
        average_score=attempt.evaluation.get("average_score") if attempt.evaluation else None,
        evaluated_at=attempt.evaluation.get("evaluated_at") if attempt.evaluation else None,
    )


@router.get("/results", response_model=list[InterviewResultRead])
def list_results(
    context: CompanyContext = Depends(manager), db: Session = Depends(get_db),
    offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
):
    rows = db.execute(result_query(context.company_id).order_by(Attempt.started_at.desc(), Attempt.id).offset(offset).limit(limit)).all()
    attempt_ids = [row[0].id for row in rows]
    counts = dict(db.execute(select(Answer.attempt_id, func.count(Answer.id)).where(Answer.attempt_id.in_(attempt_ids)).group_by(Answer.attempt_id)).all()) if attempt_ids else {}
    return [result_summary(row, counts.get(row[0].id, 0)) for row in rows]


@router.get("/results/{attempt_id}", response_model=InterviewResultDetail)
def get_result(attempt_id: str, context: CompanyContext = Depends(manager), db: Session = Depends(get_db)):
    row = db.execute(result_query(context.company_id).where(Attempt.id == attempt_id)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview result not found")
    answers = {answer.question_id: answer for answer in db.scalars(select(Answer).where(Answer.attempt_id == attempt_id))}
    questions = row[0].configuration_snapshot.get("questions", [])
    scores = {item["question_id"]: item for item in (row[0].evaluation or {}).get("evaluations", [])}
    return InterviewResultDetail(
        **result_summary(row, len(answers)).model_dump(),
        answers=[ResultAnswerRead(
            question_id=question["id"], question=question["text"],
            transcript=answers[question["id"]].transcript if question["id"] in answers else None,
            submitted_at=aware(answers[question["id"]].submitted_at) if question["id"] in answers else None,
            score=scores.get(question["id"], {}).get("score"),
            feedback=scores.get(question["id"], {}).get("feedback"),
        ) for question in questions],
    )


@router.post("/results/{attempt_id}/evaluate", response_model=InterviewResultDetail)
def evaluate_result(attempt_id: str, context: CompanyContext = Depends(manager), db: Session = Depends(get_db)):
    row = db.execute(result_query(context.company_id).where(Attempt.id == attempt_id)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview result not found")
    attempt = row[0]
    if attempt.evaluation is not None:
        return get_result(attempt_id, context, db)
    if attempt.status != AttemptStatus.completed and not (
        attempt.status in {AttemptStatus.active, AttemptStatus.expired} and aware(attempt.deadline_at) <= now()
    ):
        raise HTTPException(status.HTTP_409_CONFLICT, "Evaluate after the interview is completed or expired")
    questions = attempt.configuration_snapshot.get("questions", [])
    if not questions:
        raise HTTPException(status.HTTP_409_CONFLICT, "This attempt has no questions to evaluate")
    answers = {answer.question_id: answer.transcript for answer in db.scalars(select(Answer).where(Answer.attempt_id == attempt_id))}
    rubrics = {question.id: question.rubric for question in db.scalars(select(Question).where(Question.template_id == row[2].id))}
    inputs = [{"question_id": q["id"], "question": q["text"], "rubric": rubrics.get(q["id"], {}), "transcript": answers[q["id"]]}
              for q in questions if answers.get(q["id"], "").strip()]
    model = get_settings().openrouter_model
    # Release the database connection before waiting on the external AI provider.
    db.commit()
    try:
        batch = OpenRouterEvaluationProvider().evaluate(inputs) if inputs else None
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    scored = {item.question_id: item.model_dump() for item in batch.evaluations} if batch else {}
    evaluations = [scored.get(q["id"], {"question_id": q["id"], "score": 0.0, "feedback": "No answer was submitted for this question."}) for q in questions]
    evaluation = {"evaluations": evaluations,
                  "average_score": round(sum(item["score"] for item in evaluations) / len(questions), 2),
                  "evaluated_at": now().isoformat(), "model": model, "version": 1}
    # Preserve the first saved evaluation if two requests finish concurrently.
    saved = db.execute(update(Attempt).where(Attempt.id == attempt_id, Attempt.evaluation.is_(None)).values(evaluation=evaluation))
    if saved.rowcount:
        audit(db, context.company_id, context.principal.user_id, "attempt.evaluate", db.get(Attempt, attempt_id))
    db.commit()
    db.expire_all()
    return get_result(attempt_id, context, db)


@router.post("/companies", response_model=CompanyRead, status_code=201)
def create_company(
    body: CompanyCreate,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
):
    if db.scalar(select(Membership).where(Membership.auth_user_id == principal.user_id)):
        raise HTTPException(status.HTTP_409_CONFLICT, "User already belongs to a company")
    company = Company(name=body.name.strip())
    db.add(company)
    db.flush()
    db.add(Membership(auth_user_id=principal.user_id, company_id=company.id, role=Role.admin))
    audit(db, company.id, principal.user_id, "company.create", company)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "User already belongs to a company") from exc
    return company


@router.get("/companies/me", response_model=CompanyRead)
def my_company(context: CompanyContext = Depends(company_context), db: Session = Depends(get_db)):
    return db.get(Company, context.company_id)


@router.post("/jobs", response_model=JobRead, status_code=201)
def create_job(body: JobCreate, context: CompanyContext = Depends(manager), db: Session = Depends(get_db)):
    job = Job(company_id=context.company_id, **body.model_dump())
    db.add(job)
    db.flush()
    audit(db, context.company_id, context.principal.user_id, "job.create", job)
    db.commit()
    return job


@router.get("/jobs", response_model=list[JobRead])
def list_jobs(context: CompanyContext = Depends(company_context), db: Session = Depends(get_db)):
    return db.scalars(select(Job).where(Job.company_id == context.company_id).order_by(Job.created_at)).all()


@router.post("/interviews", response_model=TemplateRead, status_code=201)
def create_template(body: TemplateCreate, context: CompanyContext = Depends(manager), db: Session = Depends(get_db)):
    job = db.scalar(select(Job).where(Job.id == body.job_id, Job.company_id == context.company_id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    template = InterviewTemplate(company_id=context.company_id, **body.model_dump())
    db.add(template)
    db.flush()
    audit(db, context.company_id, context.principal.user_id, "interview.create", template)
    db.commit()
    return template


@router.get("/interviews", response_model=list[TemplateRead])
def list_templates(
    job_id: str | None = None,
    context: CompanyContext = Depends(company_context),
    db: Session = Depends(get_db),
):
    statement = select(InterviewTemplate).where(InterviewTemplate.company_id == context.company_id)
    if job_id:
        statement = statement.where(InterviewTemplate.job_id == job_id)
    return db.scalars(statement.order_by(InterviewTemplate.created_at)).all()


@router.post("/interviews/{template_id}/revise", response_model=TemplateRead, status_code=201)
def revise_template(template_id: str, context: CompanyContext = Depends(manager), db: Session = Depends(get_db)):
    source = tenant_template(db, template_id, context.company_id, lock=True)
    if not source.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only published interviews need a revision")
    revision = InterviewTemplate(
        company_id=source.company_id,
        job_id=source.job_id,
        name=f"{source.name[:149]} (revision)",
        duration_minutes=source.duration_minutes,
        attempt_limit=source.attempt_limit,
        published=False,
    )
    db.add(revision)
    db.flush()
    db.add_all([
        Question(
            template_id=revision.id,
            text=question.text,
            position=question.position,
            time_limit_seconds=question.time_limit_seconds,
            approved=question.approved,
            rubric=dict(question.rubric),
        )
        for question in source.questions
    ])
    audit(db, context.company_id, context.principal.user_id, "interview.revise", revision)
    db.commit()
    return revision


def tenant_template(db: Session, template_id: str, company_id: str, *, lock: bool = False) -> InterviewTemplate:
    statement = (
        select(InterviewTemplate)
        .options(selectinload(InterviewTemplate.questions))
        .where(InterviewTemplate.id == template_id, InterviewTemplate.company_id == company_id)
    )
    if lock:
        statement = statement.with_for_update().execution_options(populate_existing=True)
    template = db.scalar(statement)
    if template is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    return template


@router.get("/interviews/{template_id}/questions", response_model=list[QuestionRead])
def list_questions(
    template_id: str,
    context: CompanyContext = Depends(company_context),
    db: Session = Depends(get_db),
):
    template = tenant_template(db, template_id, context.company_id)
    return sorted(template.questions, key=lambda question: question.position)


@router.post("/interviews/{template_id}/questions", status_code=201)
def add_question(template_id: str, body: QuestionCreate, context: CompanyContext = Depends(manager), db: Session = Depends(get_db)):
    template = tenant_template(db, template_id, context.company_id, lock=True)
    if template.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published interviews are immutable")
    question = Question(template_id=template.id, approved=True, **body.model_dump())
    db.add(question)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Question position already exists") from exc
    audit(db, context.company_id, context.principal.user_id, "question.create", question)
    db.commit()
    return {"id": question.id, "approved": question.approved}


@router.post("/interviews/{template_id}/questions/extract-pdf", response_model=PdfQuestionExtraction)
async def extract_questions_from_pdf(
    template_id: str,
    file: UploadFile = File(...),
    context: CompanyContext = Depends(manager),
    db: Session = Depends(get_db),
):
    template = tenant_template(db, template_id, context.company_id)
    if template.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published interviews are immutable")
    if file.content_type not in {"application/pdf", "application/octet-stream"}:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Upload a PDF file")
    data = await file.read(MAX_PDF_BYTES + 1)
    await file.close()
    try:
        return PdfQuestionExtraction(questions=await run_in_threadpool(process_pdf, data))
    except PdfBusyError as exc:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, str(exc), headers={"Retry-After": "5"}) from exc
    except PdfQuestionError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc


@router.post("/interviews/{template_id}/questions/import", response_model=list[QuestionRead], status_code=201)
def import_questions(
    template_id: str,
    body: QuestionImportRequest,
    context: CompanyContext = Depends(manager),
    db: Session = Depends(get_db),
):
    template = tenant_template(db, template_id, context.company_id, lock=True)
    if template.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published interviews are immutable")
    existing = {question.text.strip().casefold() for question in template.questions}
    new_questions = [text for text in body.questions if text.casefold() not in existing]
    if not new_questions:
        raise HTTPException(status.HTTP_409_CONFLICT, "All selected questions already exist in this interview")
    next_position = (db.scalar(select(func.max(Question.position)).where(Question.template_id == template.id)) or 0) + 1
    if next_position + len(new_questions) - 1 > 200:
        raise HTTPException(status.HTTP_409_CONFLICT, "An interview can contain at most 200 questions")
    created = []
    for offset, text in enumerate(new_questions):
        question = Question(
            template_id=template.id,
            text=text,
            position=next_position + offset,
            time_limit_seconds=body.time_limit_seconds,
            approved=True,
            rubric={"source": "pdf"},
        )
        db.add(question)
        db.flush()
        audit(db, context.company_id, context.principal.user_id, "question.import", question)
        created.append(question)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Questions were added concurrently; import again") from exc
    return created


@router.post("/interviews/{template_id}/questions/generate", status_code=201)
def generate_questions(
    template_id: str,
    body: QuestionGenerationRequest,
    context: CompanyContext = Depends(manager),
    db: Session = Depends(get_db),
):
    template = tenant_template(db, template_id, context.company_id)
    if template.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published interviews are immutable")
    job = db.scalar(select(Job).where(Job.id == template.job_id, Job.company_id == context.company_id))
    try:
        batch = OpenRouterQuestionProvider().generate(
            job_title=job.title,
            job_description=job.description,
            required_skills=job.required_skills,
            existing_questions=[question.text for question in template.questions],
            count=body.count,
            difficulty=body.difficulty,
        )
    except ProviderError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    template = tenant_template(db, template_id, context.company_id, lock=True)
    if template.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published interviews are immutable")
    next_position = (db.scalar(select(func.max(Question.position)).where(Question.template_id == template.id)) or 0) + 1
    if next_position + len(batch.questions) - 1 > 200:
        raise HTTPException(status.HTTP_409_CONFLICT, "An interview can contain at most 200 questions")
    existing_text = {question.text.strip().casefold() for question in template.questions}
    if any(question.text.strip().casefold() in existing_text for question in batch.questions):
        raise HTTPException(status.HTTP_409_CONFLICT, "Questions were added concurrently; generate again")
    created = []
    for offset, generated in enumerate(batch.questions):
        rubric = {
            "difficulty": generated.difficulty,
            "competency": generated.competency,
            "criteria": [criterion.model_dump() for criterion in generated.criteria],
            "maximum_marks": sum(criterion.max_points for criterion in generated.criteria),
            "provider": "openrouter",
            "model": get_settings().openrouter_model,
        }
        question = Question(
            template_id=template.id,
            text=generated.text,
            position=next_position + offset,
            time_limit_seconds=generated.time_limit_seconds,
            approved=False,
            rubric=rubric,
        )
        db.add(question)
        db.flush()
        audit(db, context.company_id, context.principal.user_id, "question.generate", question)
        created.append({"id": question.id, "text": question.text, "approved": False, "rubric": rubric})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Questions were added concurrently; generate again") from exc
    return created


@router.post("/questions/{question_id}/approve")
def approve_question(
    question_id: str,
    context: CompanyContext = Depends(manager),
    db: Session = Depends(get_db),
):
    question = db.scalar(
        select(Question)
        .join(InterviewTemplate)
        .where(Question.id == question_id, InterviewTemplate.company_id == context.company_id)
    )
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    template = tenant_template(db, question.template_id, context.company_id, lock=True)
    if template.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published interviews are immutable")
    db.refresh(question)
    question.approved = True
    audit(db, context.company_id, context.principal.user_id, "question.approve", question)
    db.commit()
    return {"id": question.id, "approved": True}


@router.patch("/questions/{question_id}", response_model=QuestionRead)
def update_question(
    question_id: str,
    body: QuestionUpdate,
    context: CompanyContext = Depends(manager),
    db: Session = Depends(get_db),
):
    question = db.scalar(
        select(Question)
        .join(InterviewTemplate)
        .where(Question.id == question_id, InterviewTemplate.company_id == context.company_id)
    )
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    template = tenant_template(db, question.template_id, context.company_id, lock=True)
    if template.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published interviews are immutable")
    db.refresh(question)
    question.text = " ".join(body.text.split())
    question.time_limit_seconds = body.time_limit_seconds
    question.approved = False
    audit(db, context.company_id, context.principal.user_id, "question.update", question)
    db.commit()
    return question


@router.post("/questions/{question_id}/deselect")
def deselect_question(
    question_id: str,
    context: CompanyContext = Depends(manager),
    db: Session = Depends(get_db),
):
    question = db.scalar(
        select(Question)
        .join(InterviewTemplate)
        .where(Question.id == question_id, InterviewTemplate.company_id == context.company_id)
    )
    if question is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    template = tenant_template(db, question.template_id, context.company_id, lock=True)
    if template.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published interviews are immutable")
    db.refresh(question)
    question.approved = False
    audit(db, context.company_id, context.principal.user_id, "question.deselect", question)
    db.commit()
    return {"id": question.id, "approved": False}


@router.post("/interviews/{template_id}/questions/approval")
def set_all_question_approvals(
    template_id: str,
    body: QuestionBulkApprovalRequest,
    context: CompanyContext = Depends(manager),
    db: Session = Depends(get_db),
):
    template = tenant_template(db, template_id, context.company_id, lock=True)
    if template.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published interviews are immutable")
    for question in template.questions:
        if question.approved != body.approved:
            question.approved = body.approved
            audit(db, context.company_id, context.principal.user_id, "question.approval.bulk", question)
    db.commit()
    return {"approved": body.approved, "count": len(template.questions)}


@router.post("/interviews/{template_id}/publish", response_model=TemplateRead)
def publish_template(template_id: str, context: CompanyContext = Depends(manager), db: Session = Depends(get_db)):
    template = tenant_template(db, template_id, context.company_id, lock=True)
    if not any(question.approved for question in template.questions):
        raise HTTPException(status.HTTP_409_CONFLICT, "Approve at least one question")
    template.published = True
    audit(db, context.company_id, context.principal.user_id, "interview.publish", template)
    db.commit()
    return template


@router.post("/invitations", response_model=InvitationRead, status_code=201)
def create_invitation(body: InvitationCreate, context: CompanyContext = Depends(manager), db: Session = Depends(get_db)):
    template = tenant_template(db, body.template_id, context.company_id)
    if template.job_id != body.job_id or not template.published:
        raise HTTPException(status.HTTP_409_CONFLICT, "Published interview and job must match")
    normalized_email = str(body.email).casefold()
    candidate = db.scalar(select(Candidate).where(Candidate.company_id == context.company_id, Candidate.email == normalized_email))
    if candidate is None:
        candidate = Candidate(company_id=context.company_id, email=normalized_email, name=body.name)
        db.add(candidate)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, "Candidate was created concurrently; retry invitation") from exc
    raw_token, hashed = create_invitation_token()
    invitation = Invitation(
        token_hash=hashed,
        company_id=context.company_id,
        candidate_id=candidate.id,
        job_id=body.job_id,
        template_id=body.template_id,
        expires_at=body.expires_at or now() + timedelta(hours=get_settings().invitation_ttl_hours),
    )
    db.add(invitation)
    db.flush()
    notification = Notification(
        company_id=context.company_id,
        candidate_id=candidate.id,
        recipient=normalized_email,
        template="candidate_invitation",
        status="pending",
    )
    db.add(notification)
    audit(db, context.company_id, context.principal.user_id, "invitation.create", invitation)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Invitation was created concurrently; retry") from exc
    settings = get_settings()
    console = settings.email_delivery_mode == "console"
    company = db.get(Company, context.company_id)
    job = db.get(Job, body.job_id)
    try:
        notification.provider_message_id = get_email_provider(settings).send_invitation(
            normalized_email,
            candidate.name,
            company.name,
            job.title,
            raw_token,
        )
        notification.status = "sent" if not console else "console"
        audit(db, context.company_id, context.principal.user_id, "invitation.deliver", invitation)
        db.commit()
    except ProviderError as exc:
        # shortcut: synchronous delivery is sufficient for initial volume; use a transactional outbox before scaling.
        invitation.revoked_at = now()
        notification.status = "failed"
        notification.error = str(exc)[:500]
        audit(db, context.company_id, context.principal.user_id, "invitation.delivery_failed", invitation)
        db.commit()
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    return InvitationRead(
        id=invitation.id,
        expires_at=invitation.expires_at,
        delivery_status=notification.status,
        token=raw_token if console else None,
    )


@router.patch("/invitations/{invitation_id}", response_model=InvitationExpiryRead)
def update_invitation_expiry(
    invitation_id: str,
    body: InvitationExpiryUpdate,
    context: CompanyContext = Depends(manager),
    db: Session = Depends(get_db),
):
    invitation = db.scalar(
        select(Invitation)
        .where(Invitation.id == invitation_id, Invitation.company_id == context.company_id)
        .with_for_update()
    )
    if invitation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invitation not found")
    if invitation.accepted_at or invitation.revoked_at:
        raise HTTPException(status.HTTP_409_CONFLICT, "Invitation is no longer editable")
    invitation.expires_at = body.expires_at
    audit(db, context.company_id, context.principal.user_id, "invitation.expiry.update", invitation)
    db.commit()
    return InvitationExpiryRead(id=invitation.id, expires_at=invitation.expires_at)


@router.post("/invitations/accept", response_model=AssignmentRead)
def use_invitation(body: InvitationAccept, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return accept_invitation(db, body.token, principal.user_id, principal.email)


def candidate_assignment(db: Session, assignment_id: str, user_id: str) -> tuple[Assignment, InterviewTemplate]:
    assignment = db.scalar(
        select(Assignment)
        .join(Candidate)
        .where(Assignment.id == assignment_id, Candidate.auth_user_id == user_id)
    )
    if assignment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")
    template = db.scalar(
        select(InterviewTemplate)
        .options(selectinload(InterviewTemplate.questions))
        .where(InterviewTemplate.id == assignment.template_id)
    )
    return assignment, template


@router.get("/sessions", response_model=list[AssignmentRead])
def candidate_sessions(principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    return db.scalars(select(Assignment).join(Candidate).where(Candidate.auth_user_id == principal.user_id)).all()


def candidate_attempt_read(db: Session, attempt: Attempt) -> CandidateAttemptRead:
    answered_question_ids = list(db.scalars(select(Answer.question_id).where(Answer.attempt_id == attempt.id)))
    return CandidateAttemptRead(
        **AttemptRead.model_validate(attempt).model_dump(),
        answered_question_ids=answered_question_ids,
    )


@router.post("/sessions/{assignment_id}/attempts", response_model=CandidateAttemptRead)
def begin_attempt(assignment_id: str, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    assignment, template = candidate_assignment(db, assignment_id, principal.user_id)
    attempt = start_attempt(db, assignment, template, principal.user_id)
    return candidate_attempt_read(db, attempt)


def candidate_attempt(db: Session, attempt_id: str, user_id: str) -> Attempt:
    attempt = db.scalar(
        select(Attempt)
        .join(Assignment)
        .join(Candidate)
        .where(Attempt.id == attempt_id, Candidate.auth_user_id == user_id)
        .with_for_update(of=Attempt)
    )
    if attempt is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Attempt not found")
    if attempt.status == AttemptStatus.active and aware(attempt.deadline_at) <= now():
        attempt.status = AttemptStatus.expired
        db.commit()
    return attempt


@router.post("/sessions/{attempt_id}/answers", status_code=201)
def submit_answer(attempt_id: str, body: AnswerCreate, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    attempt = candidate_attempt(db, attempt_id, principal.user_id)
    if attempt.status != AttemptStatus.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Attempt is not active")
    allowed_ids = {item["id"] for item in attempt.configuration_snapshot["questions"]}
    if body.question_id not in allowed_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not in this attempt")
    answer = Answer(attempt_id=attempt.id, **body.model_dump())
    db.add(answer)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Answer already submitted") from exc
    assignment = db.get(Assignment, attempt.assignment_id)
    audit(db, assignment.company_id, principal.user_id, "answer.submit", answer)
    db.commit()
    return {"id": answer.id, "submitted_at": answer.submitted_at}


@router.post("/sessions/{attempt_id}/complete", response_model=AttemptRead)
def complete_attempt(attempt_id: str, principal: Principal = Depends(current_principal), db: Session = Depends(get_db)):
    attempt = candidate_attempt(db, attempt_id, principal.user_id)
    if attempt.status != AttemptStatus.active:
        raise HTTPException(status.HTTP_409_CONFLICT, "Attempt is not active")
    required_ids = {item["id"] for item in attempt.configuration_snapshot["questions"]}
    answered_ids = set(db.scalars(select(Answer.question_id).where(Answer.attempt_id == attempt.id)))
    if required_ids - answered_ids:
        raise HTTPException(status.HTTP_409_CONFLICT, "Answer every question before completing the interview")
    attempt.status = AttemptStatus.completed
    attempt.completed_at = now()
    assignment = db.get(Assignment, attempt.assignment_id)
    audit(db, assignment.company_id, principal.user_id, "attempt.complete", attempt)
    db.commit()
    return attempt


@router.post("/reattempts/{assignment_id}", status_code=201)
def grant_reattempt(assignment_id: str, body: ReattemptCreate, context: CompanyContext = Depends(manager), db: Session = Depends(get_db)):
    assignment = db.scalar(select(Assignment).where(Assignment.id == assignment_id, Assignment.company_id == context.company_id))
    if assignment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Assignment not found")
    permission = ReattemptPermission(assignment_id=assignment.id, granted_by=context.principal.user_id, **body.model_dump())
    db.add(permission)
    db.flush()
    audit(db, context.company_id, context.principal.user_id, "reattempt.grant", permission)
    db.commit()
    return {"id": permission.id, "reason": permission.reason}

