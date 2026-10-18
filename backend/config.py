from functools import lru_cache
from pathlib import Path
import os
from typing import Literal
from urllib.parse import urlsplit
import jwt

from email.utils import parseaddr

from pydantic import EmailStr, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def default_app_env() -> str:
    return "production" if os.getenv("VERCEL") or os.getenv("RENDER") else "development"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ENV_FILE, extra="ignore", hide_input_in_errors=True)

    app_env: Literal["development", "test", "production"] = Field(default_factory=default_app_env)
    database_url: str = "sqlite:///./interview_platform.db"
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"
    cors_origins: str = "http://localhost:5173"
    email_delivery_mode: Literal["console", "resend"] = "console"
    resend_api_key: str = ""
    email_test_recipient: EmailStr | None = None
    email_from: str = "InterviewOS <onboarding@resend.dev>"
    public_app_url: str = "http://localhost:5173"
    invitation_ttl_hours: int = Field(default=72, ge=1, le=8760)
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_app_name: str = "InterviewOS"
    redis_url: str = "redis://localhost:6379/0"
    max_request_bytes: int = Field(default=1_048_576, ge=1024, le=10_485_760)
    max_pdf_request_bytes: int = Field(default=5_500_000, ge=1024, le=6_291_456)
    rate_limit_per_minute: int = Field(default=120, ge=1, le=10000)

    @field_validator("supabase_publishable_key")
    @classmethod
    def reject_privileged_key(cls, value: str) -> str:
        if value.startswith("sb_secret_"):
            raise ValueError("Use a public Supabase publishable key, never a secret key")
        if value.count(".") == 2:
            try:
                claims = jwt.decode(value, options={"verify_signature": False})
            except jwt.InvalidTokenError:
                raise ValueError("Invalid public Supabase key") from None
            if claims.get("role") != "anon":
                raise ValueError("Only an anonymous Supabase legacy key may be used")
        return value

    @field_validator("openrouter_base_url")
    @classmethod
    def valid_ai_endpoint(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
            raise ValueError("OPENROUTER_BASE_URL must be an HTTPS endpoint without credentials, query or fragment")
        return value

    @model_validator(mode="after")
    def validate_production(self):
        if os.getenv("VERCEL_ENV") == "production" and self.app_env != "production":
            raise ValueError("Vercel production deployments require APP_ENV=production")
        if self.app_env.casefold() != "production":
            return self
        errors = []
        if not self.database_url.startswith(("postgresql://", "postgresql+psycopg://")):
            errors.append("DATABASE_URL must use PostgreSQL")
        if not self.supabase_url.startswith("https://") or not self.supabase_publishable_key:
            errors.append("Supabase Auth must be configured")
        if (
            self.email_delivery_mode != "resend"
            or not self.resend_api_key
            or "paste_your_key_here" in self.resend_api_key
            or ("resend.dev" in self.email_from and not (self.email_test_recipient and parseaddr(self.email_from)[1].casefold() == "onboarding@resend.dev"))
            or "your-verified-domain.com" in self.email_from
        ):
            errors.append("Resend email delivery must be configured")
        def https_origin(value: str) -> bool:
            parts = urlsplit(value)
            return bool(parts.scheme == "https" and parts.hostname and not parts.username and not parts.password
                        and parts.path in {"", "/"} and not parts.query and not parts.fragment and "*" not in value)
        if not https_origin(self.public_app_url):
            errors.append("PUBLIC_APP_URL must use HTTPS")
        origins = [origin.strip() for origin in self.cors_origins.split(",")]
        if not origins or any(not https_origin(origin) or urlsplit(origin).hostname in {"localhost", "127.0.0.1", "::1"} for origin in origins):
            errors.append("CORS_ORIGINS must list production origins")
        if not self.openrouter_api_key or "paste_your_key_here" in self.openrouter_api_key:
            errors.append("OpenRouter must be configured")
        if "localhost" in self.redis_url or "127.0.0.1" in self.redis_url:
            errors.append("REDIS_URL must use a production Redis service")
        if errors:
            raise ValueError("Invalid production configuration: " + "; ".join(errors))
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()

