# Устанавливаем тестовое окружение до импорта app (database и auth читают env при загрузке)
import os
os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL", "sqlite:///:memory:")
os.environ["SECRET_KEY"] = os.environ.get("SECRET_KEY", "test-secret-key")

import pytest
from fastapi.testclient import TestClient
from app.main import app


@pytest.fixture
def client():
    return TestClient(app)
