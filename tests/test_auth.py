"""Тесты регистрации, входа и защищённых эндпоинтов."""


def test_register_success(client):
    """Регистрация возвращает 201, токен и данные пользователя."""
    r = client.post(
        "/auth/register",
        json={"email": "test@example.com", "name": "Test User", "password": "secure123"},
    )
    assert r.status_code == 201
    data = r.json()
    assert "access_token" in data
    assert data.get("token_type") == "bearer"
    assert "user" in data
    assert data["user"]["email"] == "test@example.com"
    assert data["user"]["name"] == "Test User"
    assert "id" in data["user"]
    assert "registered_at" in data["user"]


def test_register_duplicate_email(client):
    """Повторная регистрация с тем же email — 400."""
    payload = {"email": "dup@example.com", "name": "First", "password": "pass123"}
    client.post("/auth/register", json=payload)
    r = client.post("/auth/register", json={**payload, "name": "Second"})
    assert r.status_code == 400
    assert "уже существует" in r.json().get("detail", "")


def test_login_success(client):
    """После регистрации логин возвращает токен и пользователя."""
    client.post(
        "/auth/register",
        json={"email": "login@example.com", "name": "Login User", "password": "mypass"},
    )
    r = client.post(
        "/auth/login",
        json={"email": "login@example.com", "password": "mypass"},
    )
    assert r.status_code == 200
    data = r.json()
    assert "access_token" in data
    assert data["user"]["email"] == "login@example.com"


def test_login_wrong_password(client):
    """Неверный пароль — 401."""
    client.post(
        "/auth/register",
        json={"email": "wrong@example.com", "name": "User", "password": "correct"},
    )
    r = client.post(
        "/auth/login",
        json={"email": "wrong@example.com", "password": "wrong"},
    )
    assert r.status_code == 401


def test_protected_without_token(client):
    """Защищённый эндпоинт без токена возвращает 401."""
    r = client.post(
        "/wishlists/",
        json={"title": "List", "description": ""},
    )
    assert r.status_code == 403  # FastAPI HTTPBearer auto_error=True -> 403 when no header
