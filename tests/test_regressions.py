from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.database import SessionLocal
from backend.models import Assignment, Invitation
from backend.services import accept_invitation, aware, now
from test_workflow import auth, bootstrap, client, database, invite_and_accept  # noqa: F401


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/docs/oauth2-redirect"])
def test_documentation_policy_allows_its_assets_without_relaxing_api_policy(client, path):
    response = client.get(path)
    assert response.status_code == 200
    policy = response.headers["content-security-policy"]
    assert "script-src https://cdn.jsdelivr.net 'unsafe-inline'" in policy
    assert "connect-src 'self'" in policy
    assert client.get("/api/v1/jobs").headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


def test_active_attempt_resumes_after_assignment_window_closes(client):
    headers, _, job, interview = bootstrap(client, "owner", "owner@example.com", "Company")
    _, assignment, candidate = invite_and_accept(client, headers, job, interview)
    started = client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate)
    assert started.status_code == 200
    with SessionLocal() as db:
        db.get(Assignment, assignment["id"]).available_until = now() - timedelta(minutes=1)
        db.commit()
    resumed = client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["id"] == started.json()["id"]


def test_expired_assignment_cannot_start_a_new_attempt(client):
    headers, _, job, interview = bootstrap(client, "owner", "owner@example.com", "Company")
    _, assignment, candidate = invite_and_accept(client, headers, job, interview)
    with SessionLocal() as db:
        db.get(Assignment, assignment["id"]).available_until = now() - timedelta(minutes=1)
        db.commit()
    assert client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate).status_code == 410


def test_replacement_invitation_renews_existing_assignment(client):
    headers, _, job, interview = bootstrap(client, "owner", "owner@example.com", "Company")
    _, assignment, candidate = invite_and_accept(client, headers, job, interview)
    with SessionLocal() as db:
        db.get(Assignment, assignment["id"]).available_until = now() - timedelta(days=1)
        db.commit()
    expiry = now() + timedelta(days=4)
    invitation = client.post("/api/v1/invitations", headers=headers, json={
        "email": "candidate@example.com", "job_id": job["id"],
        "template_id": interview["id"], "expires_at": expiry.isoformat(),
    })
    assert invitation.status_code == 201
    accepted = client.post("/api/v1/invitations/accept", headers=candidate,
                           json={"token": invitation.json()["token"]})
    assert accepted.status_code == 200
    assert accepted.json()["id"] == assignment["id"]
    with SessionLocal() as db:
        assert aware(db.get(Assignment, assignment["id"]).available_until) == expiry
    assert client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate).status_code == 200


def test_invitation_flush_conflict_is_rolled_back(client, monkeypatch):
    headers, _, job, interview = bootstrap(client, "owner", "owner@example.com", "Company")
    response = client.post("/api/v1/invitations", headers=headers, json={
        "email": "candidate@example.com", "job_id": job["id"], "template_id": interview["id"],
    })
    assert response.status_code == 201
    with SessionLocal() as db:
        def conflict(*args, **kwargs):
            raise IntegrityError("synthetic conflict", {}, Exception("private database detail"))
        with monkeypatch.context() as patch:
            patch.setattr(db, "flush", conflict)
            with pytest.raises(HTTPException) as result:
                accept_invitation(db, response.json()["token"], "candidate", "candidate@example.com")
        assert result.value.status_code == 409
        assert "private" not in result.value.detail
        invitation = db.scalar(select(Invitation).where(Invitation.id == response.json()["id"]))
        assert invitation.accepted_at is None
        assert db.scalar(select(Assignment)) is None


def test_blank_answers_and_invalid_reattempt_expiry_are_rejected(client):
    headers, _, job, interview = bootstrap(client, "owner", "owner@example.com", "Company")
    _, assignment, candidate = invite_and_accept(client, headers, job, interview)
    attempt = client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate).json()
    question = attempt["configuration_snapshot"]["questions"][0]["id"]
    assert client.post(f"/api/v1/sessions/{attempt['id']}/answers", headers=candidate,
                       json={"question_id": question, "transcript": " \n\t "}).status_code == 422
    for expiry in [(now() - timedelta(days=1)).isoformat(), "2030-01-01T00:00:00"]:
        assert client.post(f"/api/v1/reattempts/{assignment['id']}", headers=headers,
                           json={"reason": "Technical issue during interview", "expires_at": expiry}).status_code == 422
