import os
from datetime import datetime, timedelta, timezone

os.environ["APP_ENV"] = "test"
os.environ["SUPABASE_JWT_SECRET"] = "test-secret-with-sufficient-length"
os.environ["SUPABASE_JWT_AUDIENCE"] = "authenticated"
os.environ["EMAIL_DELIVERY_MODE"] = "console"

import jwt
import pytest
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.database import Base, SessionLocal, engine
from backend.auth import Principal, bearer, current_principal
from backend.config import get_settings
from backend.main import app
from backend.models import Invitation
from backend.pdf_questions import questions_from_text
from backend.schemas import GeneratedCriterion, GeneratedQuestion, GeneratedQuestionBatch


def token(user_id: str, email: str) -> str:
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "aud": "authenticated",
            "exp": datetime.now(timezone.utc) + timedelta(hours=1),
        },
        "test-secret-with-sufficient-length",
        algorithm="HS256",
    )


def auth(user_id: str, email: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token(user_id, email)}"}


def fake_principal(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)) -> Principal:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required")
    payload = jwt.decode(
        credentials.credentials,
        "test-secret-with-sufficient-length",
        algorithms=["HS256"],
        audience="authenticated",
    )
    return Principal(user_id=payload["sub"], email=payload.get("email"))


@pytest.fixture(autouse=True)
def database():
    assert engine.url.get_backend_name() == "sqlite", "Tests must never run against a persistent database"
    app.dependency_overrides[current_principal] = fake_principal
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def bootstrap(client: TestClient, user: str, email: str, name: str):
    headers = auth(user, email)
    company = client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert company.status_code == 201, company.text
    job = client.post(
        "/api/v1/jobs",
        json={"title": "Backend Engineer", "description": "Build reliable Python services.", "required_skills": ["Python", "SQL"]},
        headers=headers,
    )
    assert job.status_code == 201, job.text
    interview = client.post(
        "/api/v1/interviews",
        json={"job_id": job.json()["id"], "name": "Core screen", "duration_minutes": 30, "attempt_limit": 1},
        headers=headers,
    )
    assert interview.status_code == 201, interview.text
    question = client.post(
        f"/api/v1/interviews/{interview.json()['id']}/questions",
        json={"text": "How do database transactions prevent races?", "position": 1, "time_limit_seconds": 180, "rubric": {"atomicity": 5}},
        headers=headers,
    )
    assert question.status_code == 201, question.text
    published = client.post(f"/api/v1/interviews/{interview.json()['id']}/publish", headers=headers)
    assert published.status_code == 200, published.text
    return headers, company.json(), job.json(), interview.json()


def invite_and_accept(client: TestClient, company_headers, job, interview, candidate_user="candidate-1", candidate_email="candidate@example.com"):
    invitation = client.post(
        "/api/v1/invitations",
        json={"email": candidate_email, "name": "Candidate", "job_id": job["id"], "template_id": interview["id"]},
        headers=company_headers,
    )
    assert invitation.status_code == 201, invitation.text
    accepted = client.post(
        "/api/v1/invitations/accept",
        json={"token": invitation.json()["token"]},
        headers=auth(candidate_user, candidate_email),
    )
    assert accepted.status_code == 200, accepted.text
    return invitation.json(), accepted.json(), auth(candidate_user, candidate_email)


def test_company_isolation_hides_other_tenant_jobs(client):
    headers_a, _, _, _ = bootstrap(client, "admin-a", "a@example.com", "Alpha")
    headers_b, _, _, _ = bootstrap(client, "admin-b", "b@example.com", "Beta")
    jobs_a = client.get("/api/v1/jobs", headers=headers_a)
    jobs_b = client.get("/api/v1/jobs", headers=headers_b)
    assert [job["title"] for job in jobs_a.json()] == ["Backend Engineer"]
    assert [job["title"] for job in jobs_b.json()] == ["Backend Engineer"]
    assert jobs_a.json()[0]["id"] != jobs_b.json()[0]["id"]


def test_invitation_is_email_bound_and_single_use(client):
    headers, _, job, interview = bootstrap(client, "admin", "admin@example.com", "Acme")
    requested_expiry = (datetime.now(timezone.utc) + timedelta(days=4)).replace(microsecond=0)
    invitation_response = client.post(
        "/api/v1/invitations",
        json={"email": "right@example.com", "job_id": job["id"], "template_id": interview["id"], "expires_at": requested_expiry.isoformat()},
        headers=headers,
    )
    assert invitation_response.status_code == 201, invitation_response.text
    invitation = invitation_response.json()
    assert datetime.fromisoformat(invitation["expires_at"]) == requested_expiry
    updated_expiry = (requested_expiry + timedelta(days=2)).isoformat()
    updated = client.patch(
        f"/api/v1/invitations/{invitation['id']}",
        json={"expires_at": updated_expiry},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert datetime.fromisoformat(updated.json()["expires_at"]) == datetime.fromisoformat(updated_expiry)
    other_headers, _, _, _ = bootstrap(client, "other-admin", "other@example.com", "Other")
    assert client.patch(
        f"/api/v1/invitations/{invitation['id']}",
        json={"expires_at": updated_expiry},
        headers=other_headers,
    ).status_code == 404
    wrong = client.post(
        "/api/v1/invitations/accept",
        json={"token": invitation["token"]},
        headers=auth("candidate-wrong", "wrong@example.com"),
    )
    assert wrong.status_code == 403
    first = client.post(
        "/api/v1/invitations/accept",
        json={"token": invitation["token"]},
        headers=auth("candidate-right", "right@example.com"),
    )
    assert first.status_code == 200
    assert client.patch(
        f"/api/v1/invitations/{invitation['id']}",
        json={"expires_at": updated_expiry},
        headers=headers,
    ).status_code == 409
    reused = client.post(
        "/api/v1/invitations/accept",
        json={"token": invitation["token"]},
        headers=auth("candidate-right", "right@example.com"),
    )
    assert reused.status_code == 409


def test_expired_invitation_fails(client):
    headers, _, job, interview = bootstrap(client, "admin", "admin@example.com", "Acme")
    invitation = client.post(
        "/api/v1/invitations",
        json={"email": "candidate@example.com", "job_id": job["id"], "template_id": interview["id"]},
        headers=headers,
    ).json()
    with SessionLocal() as db:
        record = db.scalar(select(Invitation).where(Invitation.id == invitation["id"]))
        record.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    response = client.post(
        "/api/v1/invitations/accept",
        json={"token": invitation["token"]},
        headers=auth("candidate", "candidate@example.com"),
    )
    assert response.status_code == 410


def test_attempt_answer_and_company_controlled_reattempt(client):
    company_headers, _, job, interview = bootstrap(client, "admin", "admin@example.com", "Acme")
    _, assignment, candidate_headers = invite_and_accept(client, company_headers, job, interview)
    started = client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate_headers)
    assert started.status_code == 200
    attempt = started.json()
    assert attempt["answered_question_ids"] == []
    resumed = client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate_headers)
    assert resumed.json()["id"] == attempt["id"]
    assert client.post(f"/api/v1/sessions/{attempt['id']}/complete", headers=candidate_headers).status_code == 409
    question_id = attempt["configuration_snapshot"]["questions"][0]["id"]
    answer = client.post(
        f"/api/v1/sessions/{attempt['id']}/answers",
        json={"question_id": question_id, "transcript": "Atomic commit and row locks prevent conflicting partial writes."},
        headers=candidate_headers,
    )
    assert answer.status_code == 201
    resumed = client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate_headers)
    assert resumed.json()["answered_question_ids"] == [question_id]
    duplicate = client.post(
        f"/api/v1/sessions/{attempt['id']}/answers",
        json={"question_id": question_id, "transcript": "Duplicate"},
        headers=candidate_headers,
    )
    assert duplicate.status_code == 409
    assert client.post(f"/api/v1/sessions/{attempt['id']}/complete", headers=candidate_headers).status_code == 200
    denied = client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate_headers)
    assert denied.status_code == 403
    candidate_grant = client.post(
        f"/api/v1/reattempts/{assignment['id']}",
        json={"reason": "Candidate cannot grant this."},
        headers=candidate_headers,
    )
    assert candidate_grant.status_code == 403
    grant = client.post(
        f"/api/v1/reattempts/{assignment['id']}",
        json={"reason": "Verified audio upload failed during the first attempt."},
        headers=company_headers,
    )
    assert grant.status_code == 201
    second = client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate_headers)
    assert second.status_code == 200
    assert second.json()["attempt_number"] == 2


def test_candidate_cannot_access_another_assignment(client):
    company_headers, _, job, interview = bootstrap(client, "admin", "admin@example.com", "Acme")
    _, assignment, _ = invite_and_accept(client, company_headers, job, interview, "candidate-a", "a@candidate.com")
    response = client.post(
        f"/api/v1/sessions/{assignment['id']}/attempts",
        headers=auth("candidate-b", "b@candidate.com"),
    )
    assert response.status_code == 404


def test_supabase_auth_server_validates_production_tokens(monkeypatch):
    settings = get_settings()
    original = (settings.app_env, settings.supabase_url, settings.supabase_publishable_key)
    settings.app_env = "development"
    settings.supabase_url = "https://example.supabase.co"
    settings.supabase_publishable_key = "sb_publishable_test"

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "supabase-user", "email": "user@example.com", "email_confirmed_at": "2026-01-01T00:00:00Z"}

    monkeypatch.setattr("backend.auth.auth_client.get", lambda *args, **kwargs: Response())
    try:
        principal = current_principal(HTTPAuthorizationCredentials(scheme="Bearer", credentials="access-token"))
        assert principal.user_id == "supabase-user"
        assert principal.email == "user@example.com"
    finally:
        settings.app_env, settings.supabase_url, settings.supabase_publishable_key = original


def test_supabase_auth_server_rejects_invalid_tokens(monkeypatch):
    settings = get_settings()
    original = (settings.app_env, settings.supabase_url, settings.supabase_publishable_key)
    settings.app_env = "development"
    settings.supabase_url = "https://example.supabase.co"
    settings.supabase_publishable_key = "sb_publishable_test"

    class Response:
        status_code = 401

    monkeypatch.setattr("backend.auth.auth_client.get", lambda *args, **kwargs: Response())
    try:
        with pytest.raises(HTTPException) as error:
            current_principal(HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid"))
        assert error.value.status_code == 401
    finally:
        settings.app_env, settings.supabase_url, settings.supabase_publishable_key = original


def test_generated_questions_require_company_approval(client, monkeypatch):
    headers, _, job, _ = bootstrap(client, "admin", "admin@example.com", "Acme")
    interview = client.post(
        "/api/v1/interviews",
        json={"job_id": job["id"], "name": "AI screen", "duration_minutes": 30, "attempt_limit": 1},
        headers=headers,
    )
    assert interview.status_code == 201

    def generated(*args, **kwargs):
        return GeneratedQuestionBatch(
            questions=[
                GeneratedQuestion(
                    text="How do you prevent concurrent duplicate interview attempts?",
                    difficulty="hard",
                    competency="Concurrency control",
                    time_limit_seconds=180,
                    criteria=[
                        GeneratedCriterion(
                            name="Transactions",
                            description="Uses a transaction and a uniqueness constraint.",
                            max_points=10,
                        )
                    ],
                )
            ]
        )

    monkeypatch.setattr("backend.api.OpenRouterQuestionProvider.generate", generated)
    response = client.post(
        f"/api/v1/interviews/{interview.json()['id']}/questions/generate",
        json={"count": 1, "difficulty": "hard"},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    question = response.json()[0]
    assert question["approved"] is False
    approved = client.post(f"/api/v1/questions/{question['id']}/approve", headers=headers)
    assert approved.status_code == 200
    assert approved.json()["approved"] is True
    deselected = client.post(f"/api/v1/questions/{question['id']}/deselect", headers=headers)
    assert deselected.status_code == 200
    assert deselected.json()["approved"] is False
    approved = client.post(f"/api/v1/questions/{question['id']}/approve", headers=headers)
    assert approved.status_code == 200
    bulk_deselected = client.post(
        f"/api/v1/interviews/{interview.json()['id']}/questions/approval",
        json={"approved": False},
        headers=headers,
    )
    assert bulk_deselected.status_code == 200
    assert bulk_deselected.json() == {"approved": False, "count": 1}
    bulk_approved = client.post(
        f"/api/v1/interviews/{interview.json()['id']}/questions/approval",
        json={"approved": True},
        headers=headers,
    )
    assert bulk_approved.status_code == 200
    assert client.post(f"/api/v1/interviews/{interview.json()['id']}/publish", headers=headers).status_code == 200
    assert client.post(f"/api/v1/questions/{question['id']}/deselect", headers=headers).status_code == 409
    assert client.post(
        f"/api/v1/interviews/{interview.json()['id']}/questions/approval",
        json={"approved": False},
        headers=headers,
    ).status_code == 409
    templates = client.get(f"/api/v1/interviews?job_id={job['id']}", headers=headers)
    assert interview.json()["id"] in {item["id"] for item in templates.json()}
    questions = client.get(f"/api/v1/interviews/{interview.json()['id']}/questions", headers=headers)
    assert questions.status_code == 200
    assert questions.json()[0]["approved"] is True


def test_published_interview_revision_preserves_assignments_and_is_editable(client, monkeypatch):
    headers, _, job, interview = bootstrap(client, "admin", "admin@example.com", "Acme")
    _, assignment, _ = invite_and_accept(client, headers, job, interview)

    revision_response = client.post(f"/api/v1/interviews/{interview['id']}/revise", headers=headers)
    assert revision_response.status_code == 201, revision_response.text
    revision = revision_response.json()
    assert revision["id"] != interview["id"]
    assert revision["published"] is False
    assert assignment["template_id"] == interview["id"]

    original_questions = client.get(f"/api/v1/interviews/{interview['id']}/questions", headers=headers).json()
    revision_questions = client.get(f"/api/v1/interviews/{revision['id']}/questions", headers=headers).json()
    assert revision_questions[0]["id"] != original_questions[0]["id"]
    assert revision_questions[0]["text"] == original_questions[0]["text"]
    assert client.patch(
        f"/api/v1/questions/{original_questions[0]['id']}",
        json={"text": "This published question must stay unchanged.", "time_limit_seconds": 180},
        headers=headers,
    ).status_code == 409

    updated = client.patch(
        f"/api/v1/questions/{revision_questions[0]['id']}",
        json={"text": "How would you use transactions and row locks to prevent races?", "time_limit_seconds": 240},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["approved"] is False
    assert updated.json()["time_limit_seconds"] == 240
    assert client.get(f"/api/v1/interviews/{interview['id']}/questions", headers=headers).json()[0]["text"] == original_questions[0]["text"]

    monkeypatch.setattr(
        "backend.api.OpenRouterQuestionProvider.generate",
        lambda *args, **kwargs: GeneratedQuestionBatch(
            questions=[
                GeneratedQuestion(
                    text="How would you make a background job safe to retry?",
                    difficulty="medium",
                    competency="Reliability",
                    time_limit_seconds=180,
                    criteria=[
                        GeneratedCriterion(
                            name="Idempotency",
                            description="Explains idempotency keys and durable state.",
                            max_points=10,
                        )
                    ],
                )
            ]
        ),
    )
    generated = client.post(
        f"/api/v1/interviews/{revision['id']}/questions/generate",
        json={"count": 1, "difficulty": "medium"},
        headers=headers,
    )
    assert generated.status_code == 201, generated.text
    assert generated.json()[0]["text"] == "How would you make a background job safe to retry?"
    assert client.post(f"/api/v1/interviews/{revision['id']}/revise", headers=headers).status_code == 409


def test_pdf_question_text_parser_handles_numbered_bulleted_and_wrapped_questions():
    extracted = questions_from_text(
        """Interview questions

        1. Explain how database transactions prevent
        concurrent writes from corrupting data?
        • What is an idempotency key?
        This heading is not a question
        How would you monitor a production API?
        """
    )
    assert extracted == [
        "Explain how database transactions prevent concurrent writes from corrupting data?",
        "What is an idempotency key?",
        "How would you monitor a production API?",
    ]


def test_pdf_questions_can_be_previewed_and_selected_for_import(client, monkeypatch):
    headers, _, job, _ = bootstrap(client, "admin", "admin@example.com", "Acme")
    interview = client.post(
        "/api/v1/interviews",
        json={"job_id": job["id"], "name": "PDF screen", "duration_minutes": 30, "attempt_limit": 1},
        headers=headers,
    ).json()
    extracted = ["What is transaction isolation?", "How do you make an API idempotent?"]
    monkeypatch.setattr("backend.api.process_pdf", lambda _: extracted)

    preview = client.post(
        f"/api/v1/interviews/{interview['id']}/questions/extract-pdf",
        files={"file": ("questions.pdf", b"%PDF-test", "application/pdf")},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["questions"] == extracted

    imported = client.post(
        f"/api/v1/interviews/{interview['id']}/questions/import",
        json={"questions": [extracted[1]], "time_limit_seconds": 240},
        headers=headers,
    )
    assert imported.status_code == 201, imported.text
    assert imported.json()[0]["text"] == extracted[1]
    assert imported.json()[0]["approved"] is True
    assert imported.json()[0]["rubric"] == {"source": "pdf"}

    duplicate = client.post(
        f"/api/v1/interviews/{interview['id']}/questions/import",
        json={"questions": [extracted[1]]},
        headers=headers,
    )
    assert duplicate.status_code == 409


def test_requests_reject_unknown_fields_and_return_security_headers(client):
    response = client.post(
        "/api/v1/companies",
        json={"name": "Acme", "unexpected": True},
        headers=auth("admin", "admin@example.com"),
    )
    assert response.status_code == 422
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


def test_duplicate_question_position_is_rejected(client):
    headers, _, job, _ = bootstrap(client, "admin", "admin@example.com", "Acme")
    interview = client.post(
        "/api/v1/interviews",
        json={"job_id": job["id"], "name": "Duplicate check", "duration_minutes": 30},
        headers=headers,
    ).json()
    payload = {"text": "A question with a protected position", "position": 1, "time_limit_seconds": 180}
    assert client.post(
        f"/api/v1/interviews/{interview['id']}/questions", json=payload, headers=headers
    ).status_code == 201
    response = client.post(
        f"/api/v1/interviews/{interview['id']}/questions", json=payload, headers=headers
    )
    assert response.status_code == 409

