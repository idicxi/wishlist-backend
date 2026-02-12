from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from jose import jwt
from passlib.context import CryptContext
from passlib.exc import UnknownHashError
from sqlalchemy.orm import Session

from . import models, schemas
from .database import get_db


router = APIRouter(prefix="/auth", tags=["auth"])


SECRET_KEY = "CHANGE_ME_SUPER_SECRET"  # TODO: вынеси в переменные окружения / .env
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 дней

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