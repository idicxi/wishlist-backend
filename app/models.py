from __future__ import annotations

from datetime import datetime, date
from decimal import Decimal
from typing import List, Optional

from sqlalchemy import (
    String,
    DateTime,
    Date,
    Boolean,
    Numeric,
    ForeignKey,
    UniqueConstraint,
    func,
    Column,
    Integer,
    Table,
)
from sqlalchemy.orm import relationship

from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True, nullable=False)
    hashed_password = Column(String(255), nullable=False)
    name = Column(String(255), nullable=False)
    registered_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    wishlists = relationship("Wishlist", back_populates="owner", cascade="all, delete-orphan")
    reservations = relationship("Reservation", back_populates="user", cascade="all, delete-orphan")
    contributions = relationship("Contribution", back_populates="user", cascade="all, delete-orphan")


class Wishlist(Base):
    __tablename__ = "wishlists"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(String, nullable=True)
    event_date = Column(Date, nullable=True)
    slug = Column(String(255), unique=True, index=True, nullable=False)

    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    owner = relationship("User", back_populates="wishlists")

    gifts = relationship("Gift", back_populates="wishlist", cascade="all, delete-orphan")


class Gift(Base):
    __tablename__ = "gifts"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    url = Column(String, nullable=True)
    price = Column(Numeric(10, 2), nullable=True)
    image_url = Column(String, nullable=True)
    is_reserved = Column(Boolean, nullable=False, server_default="false")

    wishlist_id = Column(Integer, ForeignKey("wishlists.id", ondelete="CASCADE"), nullable=False)
    wishlist = relationship("Wishlist", back_populates="gifts")

    reservation = relationship("Reservation", back_populates="gift", uselist=False, cascade="all, delete-orphan")
    contributions = relationship("Contribution", back_populates="gift", cascade="all, delete-orphan")


class Reservation(Base):
    __tablename__ = "reservations"
    __table_args__ = (
        UniqueConstraint("gift_id", name="uq_reservation_gift_id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    gift_id = Column(Integer, ForeignKey("gifts.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    gift = relationship("Gift", back_populates="reservation")
    user = relationship("User", back_populates="reservations")


class Contribution(Base):
    __tablename__ = "contributions"

    id = Column(Integer, primary_key=True, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    gift_id = Column(Integer, ForeignKey("gifts.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)

    gift = relationship("Gift", back_populates="contributions")
    user = relationship("User", back_populates="contributions")