import json
import subprocess
from pathlib import Path

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError

from backend.auth import current_principal
from backend.config import Settings
from backend.pdf_processing import PdfBusyError, process_pdf, slots
from backend.pdf_questions import PdfQuestionError
from test_security import production_settings
from test_workflow import auth, client, database  # noqa: F401


@pytest.mark.parametrize("body,code", [
    ({"id": "user", "email": "person@example.com"}, 403),
    ({"id": "user", "email": "person@example.com", "is_anonymous": True}, 401),
    ({"id": ["user"], "email": "person@example.com"}, 401),
    ({"id": "user", "email": {}, "email_confirmed_at": "2026-01-01"}, 403),
])
def test_unverified_and_malformed_auth_responses_are_rejected(monkeypatch, body, code):
    monkeypatch.setattr("backend.auth.get_settings", lambda: Settings(_env_file=None, supabase_url="https://project.supabase.co", supabase_publishable_key="sb_publishable_test"))
    class Response:
        status_code = 200
        def json(self):
            return body
    monkeypatch.setattr("backend.auth.auth_client.get", lambda *args, **kwargs: Response())
    with pytest.raises(HTTPException) as result:
        current_principal(HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token"))
    assert result.value.status_code == code


@pytest.mark.parametrize("code", [429, 500, 503])
def test_auth_provider_outage_is_not_reported_as_wrong_credentials(monkeypatch, code):
    monkeypatch.setattr("backend.auth.get_settings", lambda: Settings(_env_file=None, supabase_url="https://project.supabase.co", supabase_publishable_key="sb_publishable_test"))
    class Response:
        status_code = code
    monkeypatch.setattr("backend.auth.auth_client.get", lambda *args, **kwargs: Response())
    with pytest.raises(HTTPException) as result:
        current_principal(HTTPAuthorizationCredentials(scheme="Bearer", credentials="test-token"))
    assert result.value.status_code == 503


def test_privileged_public_keys_and_bad_production_origins_are_rejected():
    for key in ["sb_secret_synthetic", jwt.encode({"role": "service_role"}, "synthetic-signing-secret-32-characters", algorithm="HS256")]:
        with pytest.raises(ValidationError):
            Settings(_env_file=None, supabase_publishable_key=key)
    for origin in ["http://example.com", "https://*.example.com", "https://example.com/path", "https://user:password@example.com", "https://example.com?redirect=other"]:
        with pytest.raises(ValidationError):
            production_settings(cors_origins=origin)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, app_env="prodution")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openrouter_base_url="http://untrusted.example/api")


def test_validation_responses_do_not_echo_sensitive_values(client):
    response = client.post("/api/v1/invitations/accept", headers=auth("test-user", "test@example.com"), json={"token": "private-short-token"})
    assert response.status_code == 422
    assert "private-short-token" not in response.text
    assert "input" not in response.json()["detail"][0]


def test_cloud_environment_defaults_to_production_checks(monkeypatch):
    monkeypatch.delenv("APP_ENV", raising=False)
    monkeypatch.setenv("VERCEL", "1")
    with pytest.raises(ValidationError, match="DATABASE_URL must use PostgreSQL"):
        Settings(_env_file=None, database_url="sqlite:///test.db")
    monkeypatch.setenv("VERCEL_ENV", "production")
    with pytest.raises(ValidationError, match="require APP_ENV=production"):
        Settings(_env_file=None, app_env="development")


def test_pdf_timeout_is_bounded_and_capacity_is_released(monkeypatch):
    def timeout(*args, **kwargs):
        assert kwargs["timeout"] == 12
        assert kwargs.get("shell", False) is False
        raise subprocess.TimeoutExpired("parser", 12)
    monkeypatch.setattr("backend.pdf_processing.subprocess.run", timeout)
    with pytest.raises(PdfQuestionError, match="time limit"):
        process_pdf(b"%PDF-test")
    assert slots.acquire(blocking=False)
    assert slots.acquire(blocking=False)
    try:
        with pytest.raises(PdfBusyError):
            process_pdf(b"%PDF-test")
    finally:
        slots.release()
        slots.release()


def test_real_pdf_worker_rejects_malformed_pdf():
    with pytest.raises(PdfQuestionError, match="document limits"):
        process_pdf(b"%PDF-invalid")


def test_real_pdf_worker_extracts_questions():
    from io import BytesIO
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 50 750 Td (1. What is database atomicity?) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    assert process_pdf(output.getvalue()) == ["What is database atomicity?"]


def test_frontend_deployment_has_csp_and_private_html_cache_policy():
    root = Path(__file__).parents[1]
    config = json.loads((root / "frontend/vercel.json").read_text())
    headers = {header["key"]: header["value"] for header in config["headers"][0]["headers"]}
    assert "script-src 'self'" in headers["Content-Security-Policy"]
    assert "object-src 'none'" in headers["Content-Security-Policy"]
    assert headers["Cache-Control"] == "no-store"


def test_rate_limit_precedes_body_buffering(client, monkeypatch):
    from backend import main
    async def over_limit(*args):
        return 999
    monkeypatch.setattr(main.settings, "app_env", "development")
    monkeypatch.setattr(main.redis, "eval", over_limit)
    response = client.post("/api/v1/companies", content=b"x" * 1_048_577)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "60"


def test_internal_errors_and_logs_do_not_expose_exception_values(client, monkeypatch, caplog):
    # Simulate an internal route failure without touching real data or services.
    def failure(*args):
        raise RuntimeError("private-credential-and-personal-answer")
    monkeypatch.setattr("backend.api.result_query", failure)
    from test_workflow import bootstrap
    headers, _, _, _ = bootstrap(client, "owner", "owner@example.com", "Company")
    response = client.get("/api/v1/results", headers=headers)
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    assert response.headers["cache-control"] == "no-store"
    assert "private-credential-and-personal-answer" not in response.text
    assert "private-credential-and-personal-answer" not in caplog.text
