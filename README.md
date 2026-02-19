# Wishlist API (бэкенд)

API для приложения «Социальный вишлист»: вишлисты, подарки, бронирование, скидывание, JWT-авторизация, WebSocket для реалтайма.

## Стек

- Python 3.10+
- FastAPI, SQLAlchemy, SQLite (или PostgreSQL через `DATABASE_URL`)
- JWT (python-jose), bcrypt (passlib), Google OAuth (httpx)

## Установка и запуск

```bash
python -m venv venv
# Windows: venv\Scripts\activate
# Linux/macOS: source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Отредактируйте .env: задайте SECRET_KEY и при необходимости GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

API: <http://localhost:8000>  
Документация: <http://localhost:8000/docs>

## Переменные окружения

| Переменная | Обязательно | Описание |
|------------|-------------|----------|
| `SECRET_KEY` | да | Секрет для подписи JWT (в проде — длинная случайная строка). |
| `GOOGLE_CLIENT_ID` | нет | Для входа через Google (OAuth). |
| `GOOGLE_CLIENT_SECRET` | нет | Секрет приложения Google. |
| `DATABASE_URL` | нет | По умолчанию `sqlite:///./wishlist.db`. |

## Основные эндпоинты

- **Auth:** `POST /auth/register`, `POST /auth/login`, `POST /auth/google` (OAuth)
- **Вишлисты:** `GET/POST /wishlists/`, `GET /wishlist/{slug}`, `GET /wishlists/{id}/gifts`, `PUT/DELETE /wishlists/{id}`
- **Подарки:** `POST /gifts/`, `PUT/DELETE /gifts/{id}`, `POST /gifts/{id}/reserve`, `POST /gifts/{id}/contribute`
- **Прочее:** `GET /stats` (для главной), `GET /api/parse-url?url=...` (автозаполнение по ссылке), `GET /users/{id}`
- **WebSocket:** `WS /ws/wishlists/{wishlist_id}`, `WS /ws/landing`

Мутации требуют заголовок `Authorization: Bearer <JWT>`.

## Тесты

```bash
pip install -r requirements.txt
pytest
```

См. папку `tests/`.
