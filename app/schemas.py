from datetime import datetime, date
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, EmailStr


# ---------- User ----------


class UserBase(BaseModel):
    email: EmailStr
    name: str


class UserCreate(UserBase):
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserRead(UserBase):
    id: int
    # В модели пользователь хранится в поле registered_at,
    # поэтому здесь используем то же имя, чтобы Pydantic мог
    # корректно прочитать атрибут из ORM-объекта.
    registered_at: datetime

    model_config = {"from_attributes": True}


# ---------- Wishlist ----------


class WishlistBase(BaseModel):
    title: str
    description: Optional[str] = None
    event_date: Optional[date] = None


class WishlistCreate(WishlistBase):
    pass


class WishlistCreateWithOwner(WishlistBase):
    owner_id: int


class WishlistRead(WishlistBase):
    id: int
    slug: str
    owner_id: int

    model_config = {"from_attributes": True}


# ---------- Gift ----------


class GiftBase(BaseModel):
    title: str
    url: Optional[str] = None
    price: Optional[Decimal] = None
    image_url: Optional[str] = None


class GiftCreate(GiftBase):
    wishlist_id: int


class GiftRead(GiftBase):
    id: int
    wishlist_id: int
    is_reserved: bool

    model_config = {"from_attributes": True}


# ---------- Reservation ----------


class ReservationBase(BaseModel):
    gift_id: int
    user_id: int


class ReservationCreate(ReservationBase):
    pass


class ReservationRead(ReservationBase):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- Contribution ----------


class ContributionBase(BaseModel):
    gift_id: int
    user_id: int
    amount: Decimal


class ContributionCreate(ContributionBase):
    pass


class ContributionRead(ContributionBase):
    id: int
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------- Token / Auth ----------


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TokenWithUser(Token):
    user: UserRead

