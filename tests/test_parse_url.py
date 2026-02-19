"""Тесты автозаполнения по URL (parse-url)."""


def test_parse_url_empty(client):
    """Пустой URL возвращает null-поля."""
    r = client.get("/api/parse-url?url=")
    assert r.status_code == 200
    data = r.json()
    assert data.get("title") is None
    assert data.get("image") is None
    assert data.get("price") is None


def test_parse_url_whitespace(client):
    """URL из пробелов — то же."""
    r = client.get("/api/parse-url?url=   ")
    assert r.status_code == 200
    data = r.json()
    assert data.get("title") is None
    assert data.get("image") is None
