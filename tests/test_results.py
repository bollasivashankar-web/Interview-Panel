from datetime import timedelta

from backend.database import SessionLocal
from backend.models import Attempt, Membership, Role
from backend.services import now
from test_workflow import auth, bootstrap, client, database, invite_and_accept  # noqa: F401
from backend.schemas import AnswerEvaluation, AnswerEvaluationBatch
from backend.providers import ProviderError


def create_attempt(client):
    headers, company, job, interview = bootstrap(client, "owner", "owner@example.com", "Results Company")
    _, assignment, candidate = invite_and_accept(client, headers, job, interview)
    attempt = client.post(f"/api/v1/sessions/{assignment['id']}/attempts", headers=candidate).json()
    return headers, company, candidate, attempt


def test_results_show_progress_saved_answers_and_completion(client):
    headers, _, candidate, attempt = create_attempt(client)
    result = client.get("/api/v1/results", headers=headers).json()[0]
    assert result["status"] == "active"
    assert result["answered_count"] == 0
    assert result["candidate_email"] == "candidate@example.com"
    assert result["question_count"] == 1
    assert "configuration_snapshot" not in result
    question = attempt["configuration_snapshot"]["questions"][0]
    before = client.get(f"/api/v1/results/{attempt['id']}", headers=headers).json()
    assert before["answers"][0]["transcript"] is None
    answer = "Use atomic transactions to preserve consistent data."
    assert client.post(f"/api/v1/sessions/{attempt['id']}/answers", headers=candidate,
                       json={"question_id": question["id"], "transcript": answer}).status_code == 201
    assert client.post(f"/api/v1/sessions/{attempt['id']}/complete", headers=candidate).status_code == 200
    detail = client.get(f"/api/v1/results/{attempt['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["status"] == "completed"
    assert detail.json()["answered_count"] == 1
    assert detail.json()["completed_at"] is not None
    assert detail.json()["answers"][0]["question"] == question["text"]
    assert detail.json()["answers"][0]["transcript"] == answer
    assert detail.json()["answers"][0]["submitted_at"] is not None


def test_results_are_private_to_company_managers(client):
    headers, company, candidate, attempt = create_attempt(client)
    other, _, _, _ = bootstrap(client, "other", "other@example.com", "Other Company")
    assert client.get("/api/v1/results", headers=other).json() == []
    assert client.get(f"/api/v1/results/{attempt['id']}", headers=other).status_code == 404
    assert client.post(f"/api/v1/results/{attempt['id']}/evaluate", headers=other).status_code == 404
    assert client.get("/api/v1/results").status_code == 401
    assert client.get("/api/v1/results", headers=candidate).status_code == 403
    assert client.get(f"/api/v1/results/{attempt['id']}", headers=candidate).status_code == 403
    assert client.post(f"/api/v1/results/{attempt['id']}/evaluate", headers=candidate).status_code == 403
    with SessionLocal() as db:
        db.add(Membership(auth_user_id="reader", company_id=company["id"], role=Role.interviewer))
        db.add(Membership(auth_user_id="hr", company_id=company["id"], role=Role.hr_manager))
        db.commit()
    assert client.get("/api/v1/results", headers=auth("reader", "reader@example.com")).status_code == 403
    assert client.get(f"/api/v1/results/{attempt['id']}", headers=auth("reader", "reader@example.com")).status_code == 403
    assert client.get("/api/v1/results", headers=auth("hr", "hr@example.com")).status_code == 200
    assert client.get("/api/v1/results?limit=101", headers=headers).status_code == 422
    assert client.get("/api/v1/results?offset=1", headers=headers).json() == []


def test_results_report_expired_attempts_without_candidate_action(client):
    headers, _, _, attempt = create_attempt(client)
    with SessionLocal() as db:
        db.get(Attempt, attempt["id"]).deadline_at = now() - timedelta(minutes=1)
        db.commit()
    assert client.get("/api/v1/results", headers=headers).json()[0]["status"] == "expired"
    assert client.get(f"/api/v1/results/{attempt['id']}", headers=headers).json()["status"] == "expired"


def finish_attempt(client, candidate, attempt):
    question = attempt["configuration_snapshot"]["questions"][0]
    assert client.post(f"/api/v1/sessions/{attempt['id']}/answers", headers=candidate,
                       json={"question_id": question["id"], "transcript": "Use atomic transactions to avoid inconsistent updates."}).status_code == 201
    assert client.post(f"/api/v1/sessions/{attempt['id']}/complete", headers=candidate).status_code == 200
    return question


def test_evaluation_saves_scores_feedback_and_reuses_saved_result(client, monkeypatch):
    headers, _, candidate, attempt = create_attempt(client)
    question = finish_attempt(client, candidate, attempt)
    calls = []
    def evaluate(self, inputs):
        calls.append(inputs)
        return AnswerEvaluationBatch(evaluations=[AnswerEvaluation(question_id=question["id"], score=8.5, feedback="Correct atomicity explanation; isolation details are missing.")])
    monkeypatch.setattr("backend.api.OpenRouterEvaluationProvider.evaluate", evaluate)
    endpoint = f"/api/v1/results/{attempt['id']}/evaluate"
    result = client.post(endpoint, headers=headers)
    assert result.status_code == 200, result.text
    assert result.json()["answers"][0]["score"] == 8.5
    assert result.json()["average_score"] == 8.5
    assert result.json()["evaluated_at"]
    assert calls[0][0]["rubric"] == {"atomicity": 5}
    assert "candidate_email" not in calls[0][0]
    assert client.post(endpoint, headers=headers).json() == result.json()
    assert len(calls) == 1
    assert client.get("/api/v1/results", headers=headers).json()[0]["average_score"] == 8.5


def test_evaluation_rejects_active_attempts_and_scores_missing_answers_zero(client, monkeypatch):
    headers, _, _, attempt = create_attempt(client)
    def unexpected(*args):
        raise AssertionError("AI must not be called for an active or unanswered attempt")
    monkeypatch.setattr("backend.api.OpenRouterEvaluationProvider.evaluate", unexpected)
    endpoint = f"/api/v1/results/{attempt['id']}/evaluate"
    assert client.post(endpoint, headers=headers).status_code == 409
    with SessionLocal() as db:
        db.get(Attempt, attempt["id"]).deadline_at = now() - timedelta(minutes=1)
        db.commit()
    result = client.post(endpoint, headers=headers)
    assert result.status_code == 200
    assert result.json()["average_score"] == 0
    assert result.json()["answers"][0]["score"] == 0


def test_failed_evaluation_does_not_save_fabricated_scores(client, monkeypatch):
    headers, _, candidate, attempt = create_attempt(client)
    finish_attempt(client, candidate, attempt)
    def unavailable(*args):
        raise ProviderError("AI evaluation provider unavailable; retry later")
    monkeypatch.setattr("backend.api.OpenRouterEvaluationProvider.evaluate", unavailable)
    assert client.post(f"/api/v1/results/{attempt['id']}/evaluate", headers=headers).status_code == 502
    detail = client.get(f"/api/v1/results/{attempt['id']}", headers=headers).json()
    assert detail["average_score"] is None
    assert detail["answers"][0]["score"] is None
