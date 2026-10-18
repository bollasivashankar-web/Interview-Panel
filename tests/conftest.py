import os
import tempfile
from pathlib import Path


os.environ["APP_ENV"] = "test"
_test_directory = tempfile.TemporaryDirectory(prefix="interview-security-tests-")
os.environ["DATABASE_URL"] = "sqlite:///" + (Path(_test_directory.name) / "test.db").as_posix()
os.environ["SUPABASE_JWT_SECRET"] = "test-secret-with-sufficient-length"
os.environ["SUPABASE_JWT_AUDIENCE"] = "authenticated"
os.environ["EMAIL_DELIVERY_MODE"] = "console"


def pytest_sessionfinish(session, exitstatus):
    from backend.database import engine
    engine.dispose()
    _test_directory.cleanup()
