from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from backend.models import Assignment, Attempt, AttemptStatus, Membership, ReattemptPermission, Role
from backend.database import SessionLocal
from backend.services import now, start_attempt
from backend.api import tenant_template
from test_workflow import auth, bootstrap, client, database  # noqa: F401


@pytest.mark.parametrize("path", ["/jobs", "/interviews", "/sessions", "/companies/me"])
def test_protected_reads_reject_anonymous(client, path):
    response = client.get("/api/v1" + path)
    assert response.status_code == 401
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"


def test_role_tampering_and_interviewer_writes_are_rejected(client):
    headers, company, job, interview = bootstrap(client, "owner", "owner@example.com", "Tenant")
    response = client.post("/api/v1/jobs", headers=headers, json={
        "title": "Injected role", "description": "A valid job description", "role": "admin",
        "company_id": "another-company",
    })
    assert response.status_code == 422
    with SessionLocal() as db:
        db.add(Membership(auth_user_id="reader", company_id=company["id"], role=Role.interviewer))
        db.commit()
    response = client.post("/api/v1/interviews/" + interview["id"] + "/publish",
                           headers=auth("reader", "reader@example.com"))
    assert response.status_code == 403


def test_cross_tenant_question_mutation_is_hidden(client):
    headers, _, _, interview = bootstrap(client, "owner", "owner@example.com", "Tenant")
    questions = client.get(f"/api/v1/interviews/{interview['id']}/questions", headers=headers).json()
    other, _, _, _ = bootstrap(client, "other", "other@example.com", "Other")
    response = client.patch(f"/api/v1/questions/{questions[0]['id']}", headers=other,
                            json={"text": "An unauthorized replacement question?", "time_limit_seconds": 180})
    assert response.status_code == 404


def test_oversized_body_and_untrusted_cors(client):
    response = client.post("/api/v1/companies", content=b"x" * 1_048_577,
                           headers={"Content-Type": "application/json", "Origin": "https://attacker.example"})
    assert response.status_code == 413
    assert "access-control-allow-origin" not in response.headers
    response = client.options("/api/v1/jobs", headers={"Origin": "https://attacker.example",
                                                       "Access-Control-Request-Method": "POST"})
    assert response.status_code == 400


def test_production_rate_limit_failure_is_closed(client, monkeypatch):
    from backend import main
    from redis.exceptions import RedisError
    async def unavailable(*args, **kwargs):
        raise RedisError("Synthetic store outage")
    monkeypatch.setattr(main.settings, "app_env", "production")
    monkeypatch.setattr(main.redis, "eval", unavailable)
    response = client.get("/api/v1/jobs")
    assert response.status_code == 503
    assert response.json() == {"detail": "Request protection unavailable"}


def test_rate_limit_is_enforced_before_protected_routes(client, monkeypatch):
    from backend import main
    async def over_limit(*args, **kwargs):
        return 121
    monkeypatch.setattr(main.settings, "app_env", "development")
    monkeypatch.setattr(main.redis, "eval", over_limit)
    response = client.get("/api/v1/jobs")
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_sql_and_html_payload_remain_data(client):
    headers, _, _, _ = bootstrap(client, "owner", "owner@example.com", "Tenant")
    payload = "<script>alert(1)</script>'; DROP TABLE jobs; --"
    response = client.post("/api/v1/jobs", headers=headers,
                           json={"title": payload, "description": "Synthetic injection regression"})
    assert response.status_code == 201
    assert response.json()["title"] == payload
    assert len(client.get("/api/v1/jobs", headers=headers).json()) == 2


def test_valid_reattempt_is_not_shadowed_by_expired_permission(client):
    headers, company, _, interview = bootstrap(client, "owner", "owner@example.com", "Tenant")
    from backend.models import Candidate
    with SessionLocal() as db:
        candidate = Candidate(company_id=company["id"], email="candidate@example.com", auth_user_id="candidate")
        db.add(candidate)
        db.flush()
        assignment = Assignment(company_id=company["id"], candidate_id=candidate.id,
                                template_id=interview["id"], available_until=now() + timedelta(days=1))
        db.add(assignment)
        db.flush()
        template = tenant_template(db, interview["id"], company["id"])
        first = start_attempt(db, assignment, template, "candidate")
        first.status = AttemptStatus.completed
        expired = ReattemptPermission(assignment_id=assignment.id, granted_by="owner", reason="Expired grant",
                                      expires_at=now() - timedelta(hours=1), created_at=now() - timedelta(days=1))
        valid = ReattemptPermission(assignment_id=assignment.id, granted_by="owner", reason="Valid grant")
        db.add_all([expired, valid])
        db.commit()
        second = start_attempt(db, assignment, template, "candidate")
        assert second.attempt_number == 2
        assert valid.used_at is not None
        assert expired.used_at is None


def test_attempt_flush_conflict_returns_safe_conflict(client, monkeypatch):
    _, company, _, interview = bootstrap(client, "owner", "owner@example.com", "Tenant")
    from backend.models import Candidate
    with SessionLocal() as db:
        candidate = Candidate(company_id=company["id"], email="candidate@example.com")
        db.add(candidate)
        db.flush()
        assignment = Assignment(company_id=company["id"], candidate_id=candidate.id,
                                template_id=interview["id"], available_until=now() + timedelta(days=1))
        db.add(assignment)
        db.commit()
        template = tenant_template(db, interview["id"], company["id"])
        def conflict(*args, **kwargs):
            raise IntegrityError("synthetic conflict", {}, Exception("private database detail"))
        monkeypatch.setattr(db, "flush", conflict)
        with pytest.raises(HTTPException) as result:
            start_attempt(db, assignment, template, "candidate")
        assert result.value.status_code == 409
        assert "private" not in result.value.detail


def test_frontend_permissions_allow_devices_only_for_self():
    config = (Path(__file__).parents[1] / "frontend/nginx.conf").read_text()
    assert "camera=(self), microphone=(self), geolocation=()" in config
    assert "frame-ancestors 'none'" in config


@pytest.mark.parametrize("action", ["approve", "deselect"])
def test_published_question_approval_cannot_be_changed(client, action):
    headers, _, _, interview = bootstrap(client, "owner", "owner@example.com", "Tenant")
    questions = client.get(f"/api/v1/interviews/{interview['id']}/questions", headers=headers).json()
    response = client.post(f"/api/v1/questions/{questions[0]['id']}/{action}", headers=headers)
    assert response.status_code == 409
    assert client.get(f"/api/v1/interviews/{interview['id']}/questions", headers=headers).json()[0]["approved"] is True


def test_docker_build_context_excludes_secrets():
    root = Path(__file__).parents[1]
    for relative in [".dockerignore", "frontend/.dockerignore"]:
        rules = (root / relative).read_text().splitlines()
        assert ".env" in rules
        assert ".env.*" in rules
        assert "*.key" in rules or "**/*.key" in rules


@pytest.mark.parametrize("data", [b"<html>not a pdf</html>", b"%PDF-invalid"])
def test_invalid_pdf_is_rejected(data):
    from backend.pdf_questions import PdfQuestionError, extract_pdf_questions
    with pytest.raises(PdfQuestionError):
        extract_pdf_questions(data)
