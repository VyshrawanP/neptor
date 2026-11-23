import os
import tempfile
import pytest
from fastapi.testclient import TestClient

from app.database import init_db
import app.config as config
import app.main as main_module


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    """Creates an isolated temporary SQLite database for each test run."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        test_db_path = tmp.name

    monkeypatch.setattr(config, "DATABASE_PATH", test_db_path)
    monkeypatch.setattr("app.config.DATABASE_PATH", test_db_path)

    init_db(test_db_path)

    yield test_db_path

    if os.path.exists(test_db_path):
        try:
            os.remove(test_db_path)
        except Exception:
            pass


@pytest.fixture
def client():
    """Provides a FastAPI TestClient."""
    from app.main import app
    with TestClient(app) as test_client:
        yield test_client
