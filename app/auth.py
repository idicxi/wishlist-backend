from __future__ import annotations

import os
import secrets
from datetime import datetime, timedelta
from typing import Annotated, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel as PydanticBaseModel
from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from sqlalchemy.orm import Session

from . import models, schemas
from .database import get_db

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")


router = APIRouter(prefix="/auth", tags=["auth"])
SECRET_KEY = os.environ.get("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("SECRET_KEY не задан в переменных окружения")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 дней

# Для защищённых эндпоинтов: обязательный Bearer токен
http_bearer = HTTPBearer(auto_error=True)
# Для опциональной авторизации (например get_gifts для владельца)
http_bearer_optional = HTTPBearer(auto_error=False)

# bcrypt_sha256 снимает лимит 72 байта у bcrypt
pwd_context = CryptContext(
    schemes=["bcrypt_sha256"],
    deprecated="auto",
)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Безопасная проверка пароля.
    Если формат хэша неизвестен (старые/повреждённые данные) —
    возвращаем False вместо того, чтобы ронять сервер 500-й ошибкой.
    """
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except UnknownHashError:
        # Хэш не распознан текущей схемой (например, старый bcrypt без sha256)
        return False


def create_access_token(
    data: dict,
    expires_delta: timedelta | None = None,
) -> str:
    to_encode = data.copy()
    now = datetime.utcnow()
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire, "iat": now})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


DbSession = Annotated[Session, Depends(get_db)]


def _decode_user_id(token: str) -> Optional[int]:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        sub = payload.get("sub")
        if sub is None:
            return None
        return int(sub)
    except (JWTError, ValueError, TypeError):
        return None


def get_current_user_id_required(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(http_bearer)],
) -> int:
    """Требует валидный JWT, возвращает user_id. Иначе 401."""
    user_id = _decode_user_id(credentials.credentials)
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный или истёкший токен",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user_id


def get_current_user_id_optional(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(http_bearer_optional)],
) -> Optional[int]:
    """Опциональная авторизация: если передан валидный JWT — возвращает user_id, иначе None."""
    if credentials is None:
        return None
    return _decode_user_id(credentials.credentials)


@router.post(
    "/register",
    response_model=schemas.TokenWithUser,
    status_code=status.HTTP_201_CREATED,
)
def register_user(payload: schemas.UserCreate, db: DbSession):
    # Проверяем, что email свободен
    existing = db.query(models.User).filter(models.User.email == payload.email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Пользователь с таким email уже существует",
        )

    hashed_password = get_password_hash(payload.password)

    user = models.User(
        email=payload.email,
        name=payload.name,
        hashed_password=hashed_password,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=access_token_expires,
    )

    return schemas.TokenWithUser(
        access_token=access_token,
        token_type="bearer",
        user=schemas.UserRead.model_validate(user),
    )


@router.post(
    "/login",
    response_model=schemas.TokenWithUser,
)
def login(payload: schemas.UserLogin, db: DbSession):
    # 1. Находим пользователя по email
    user = db.query(models.User).filter(models.User.email == payload.email).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )

    # 2. Проверяем пароль
    if not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Неверный email или пароль",
        )

    # 3. Создаём JWT
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=access_token_expires,
    )

    # 4. Отдаём токен и пользователя
    return schemas.TokenWithUser(
        access_token=access_token,
        token_type="bearer",
        user=schemas.UserRead.model_validate(user),
    )


class GoogleCallbackBody(PydanticBaseModel):
    code: str
    redirect_uri: str


@router.post(
    "/google",
    response_model=schemas.TokenWithUser,
)
def auth_google(payload: GoogleCallbackBody, db: DbSession):
    """Обмен code от Google OAuth на наш JWT. Фронт редиректит на Google, получает code, шлёт сюда."""
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth не настроен (GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET)",
        )
    with httpx.Client() as client:
        token_res = client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": payload.code,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "redirect_uri": payload.redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=10.0,
        )
    if token_res.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Не удалось обменять code на токен Google",
        )
    data = token_res.json()
    access_token_google = data.get("access_token")
    if not access_token_google:
        raise HTTPException(status_code=400, detail="Google не вернул access_token")
    with httpx.Client() as client:
        user_res = client.get(
            "https://www.googleapis.com/oauth2/v2/userinfo",
            headers={"Authorization": f"Bearer {access_token_google}"},
            timeout=10.0,
        )
    if user_res.status_code != 200:
        raise HTTPException(status_code=400, detail="Не удалось получить профиль Google")
    profile = user_res.json()
    email = (profile.get("email") or "").strip()
    name = (profile.get("name") or profile.get("email") or "User").strip() or "User"
    if not email:
        raise HTTPException(status_code=400, detail="У аккаунта Google нет email")
    user = db.query(models.User).filter(models.User.email == email).first()
    if not user:
        user = models.User(
            email=email,
            name=name,
            hashed_password=get_password_hash(secrets.token_urlsafe(32)),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(user.id)},
        expires_delta=access_token_expires,
    )
    return schemas.TokenWithUser(
        access_token=access_token,
        token_type="bearer",
        user=schemas.UserRead.model_validate(user),
    )