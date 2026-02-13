from __future__ import annotations

import random
import string
from datetime import datetime

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models, schemas
from .auth import router as auth_router
from .database import Base, engine, get_db
from .websocket_manager import manager

app = FastAPI(title="Социальный вишлист")


origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://wishlist-frontend.vercel.app",
    "https://wishlist-frontend-xi.vercel.app",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Роуты аутентификации
app.include_router(auth_router)
Base.metadata.create_all(bind=engine)
def _slugify_title(title: str) -> str:
    base = (
        title.strip()
        .lower()
        .replace(" ", "-")
    )
    allowed = set(string.ascii_lowercase + string.digits + "-")
    cleaned = "".join(ch for ch in base if ch in allowed)
    return cleaned.strip("-")

def generate_unique_slug(title: str, db: Session) -> str:
    base_slug = _slugify_title(title)
    if not base_slug:
        base_slug = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

    slug = base_slug
    attempt = 1

    while db.query(models.Wishlist).filter(models.Wishlist.slug == slug).first():
        suffix = "".join(random.choices(string.ascii_lowercase + string.digits, k=4))
        slug = f"{base_slug}-{suffix}"
        attempt += 1
        if attempt > 10:
            slug = "".join(random.choices(string.ascii_lowercase + string.digits, k=12))
            break

    return slug

# ОБНОВЛЕНИЕ ПОДАРКА
@app.put("/gifts/{gift_id}")
def update_gift(
    gift_id: int,
    title: str | None = None,
    price: float | None = None,
    url: str | None = None,
    image_url: str | None = None,
    db: Session = Depends(get_db)
):
    try:
        gift = db.query(models.Gift).filter(models.Gift.id == gift_id).first()
        if not gift:
            return {"error": "Подарок не найден"}
        
        if title is not None:
            gift.title = title
        if price is not None:
            gift.price = price
        if url is not None:
            gift.url = url
        if image_url is not None:
            gift.image_url = image_url
        
        db.commit()
        db.refresh(gift)
        
        return {
            "id": gift.id,
            "title": gift.title,
            "price": gift.price,
            "url": gift.url,
            "image_url": gift.image_url,
            "wishlist_id": gift.wishlist_id,
            "is_reserved": gift.is_reserved
        }
    except Exception as e:
        print(f"Error updating gift: {e}")
        return {"error": str(e)}

# УДАЛЕНИЕ ПОДАРКА
@app.delete("/gifts/{gift_id}")
def delete_gift(gift_id: int, db: Session = Depends(get_db)):
    try:
        gift = db.query(models.Gift).filter(models.Gift.id == gift_id).first()
        if not gift:
            return {"error": "Подарок не найден"}
        
        db.delete(gift)
        db.commit()
        return {"status": "deleted"}
    except Exception as e:
        print(f"Error deleting gift: {e}")
        return {"error": str(e)}

@app.get("/")
def root():
    return {"message": "Социальный вишлист API работает! 🎉"}

@app.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    """Сводка для главной: всего собрано, цель, последние скинувшиеся."""
    total_collected = (
        db.query(func.coalesce(func.sum(models.Contribution.amount), 0)).scalar() or 0
    )
    total_goal = (
        db.query(func.coalesce(func.sum(models.Gift.price), 0)).scalar() or 0
    )
    recent = (
        db.query(models.Contribution)
        .order_by(models.Contribution.created_at.desc())
        .limit(10)
        .all()
    )
    contributors = []
    seen_ids = set()
    for c in recent:
        if c.user_id in seen_ids:
            continue
        seen_ids.add(c.user_id)
        user = db.query(models.User).filter(models.User.id == c.user_id).first()
        name = user.name if user else f"User {c.user_id}"
        contributors.append({"name": name})
        if len(contributors) >= 5:
            break
    return {
        "total_collected": float(total_collected),
        "total_goal": float(total_goal),
        "recent_contributors": contributors,
    }

@app.post("/wishlists/")
def create_wishlist(payload: schemas.WishlistCreateWithOwner, db: Session = Depends(get_db)):
    slug = generate_unique_slug(payload.title, db)
    wishlist = models.Wishlist(
        title=payload.title,
        description=payload.description,
        event_date=payload.event_date,
        slug=slug,
        owner_id=payload.owner_id,
    )
    db.add(wishlist)
    db.commit()
    db.refresh(wishlist)
    return wishlist

@app.get("/wishlists/")
def list_wishlists(user_id: int, db: Session = Depends(get_db)):
    wishlists = (
        db.query(models.Wishlist)
        .filter(models.Wishlist.owner_id == user_id)
        .all()
    )
    return wishlists

@app.get("/wishlist/{slug}")
def get_wishlist(slug: str, db: Session = Depends(get_db)):
    wishlist = db.query(models.Wishlist).filter(models.Wishlist.slug == slug).first()
    return wishlist

@app.post("/gifts/")
async def create_gift(
    title: str,
    price: float,
    wishlist_id: int,
    url: str | None = None,
    image_url: str | None = None,
    db: Session = Depends(get_db),
):
    try:
        gift = models.Gift(
            title=title,
            price=price,
            url=url,
            image_url=image_url,
            wishlist_id=wishlist_id,
            is_reserved=False,
        )
        db.add(gift)
        db.commit()
        db.refresh(gift)

        payload = {
            "id": gift.id,
            "title": gift.title,
            "price": float(gift.price) if gift.price is not None else 0,
            "url": gift.url,
            "image_url": gift.image_url,
            "is_reserved": gift.is_reserved,
            "collected": 0,
            "progress": 0,
        }

        await manager.broadcast_to_wishlist(
            str(wishlist_id),
            {
                "type": "gift_added",
                "gift_id": gift.id,
                "gift": payload,
            },
        )

        return {
            "id": gift.id,
            "title": gift.title,
            "price": gift.price,
            "url": gift.url,
            "image_url": gift.image_url,
            "wishlist_id": gift.wishlist_id,
            "is_reserved": gift.is_reserved,
            "collected": 0,
            "progress": 0,
        }
    except Exception as e:
        print(f"Error creating gift: {e}")
        return {"error": str(e)}

@app.post("/gifts/{gift_id}/reserve")
async def reserve_gift(
    gift_id: int, 
    user_id: int,
    db: Session = Depends(get_db)
):
    try:
        print(f"🔥 RESERVE HIT: gift_id={gift_id}, user_id={user_id}")
        
        gift = db.query(models.Gift).filter(models.Gift.id == gift_id).first()
        if not gift:
            return {"error": "Подарок не найден"}

        if gift.is_reserved:
            return {"error": "Подарок уже забронирован"}

        contributions_count = db.query(models.Contribution).filter(
            models.Contribution.gift_id == gift_id
        ).count()
        
        if contributions_count > 0:
            return {"error": "Нельзя забронировать подарок, на который уже скинулись"}

        reservation = models.Reservation(gift_id=gift_id, user_id=user_id)
        gift.is_reserved = True

        db.add(reservation)
        db.commit()
        db.refresh(gift)

        user = db.query(models.User).filter(models.User.id == user_id).first()
        user_name = user.name if user else f"User {user_id}"
        
        print(f"✅ USER NAME: {user_name}")

        await manager.broadcast_to_wishlist(
            str(gift.wishlist_id),
            {
                "type": "item_reserved",
                "gift_id": gift_id,
                "user_id": user_id,
                "user_name": user_name,
                "wishlist_id": gift.wishlist_id
            },
        )

        return {"message": "Подарок забронирован!"}
        
    except Exception as e:
        print(f"Error reserving gift: {e}")
        return {"error": str(e)}

@app.post("/gifts/{gift_id}/contribute")
async def contribute(
    gift_id: int,
    amount: float,
    user_id: int,
    db: Session = Depends(get_db),
):
    gift = db.query(models.Gift).filter(models.Gift.id == gift_id).first()
    if not gift:
        return {"error": "Подарок не найден"}

    if gift.is_reserved:
        return {"error": "Подарок уже полностью выкуплен"}

    contribution = models.Contribution(gift_id=gift_id, user_id=user_id, amount=amount)
    db.add(contribution)
    db.commit()
    db.refresh(contribution)
    db.refresh(gift)

    total = (
        db.query(models.Contribution)
        .filter(models.Contribution.gift_id == gift_id)
        .with_entities(func.sum(models.Contribution.amount))
        .scalar()
        or 0
    )

    if gift.price is not None and total >= float(gift.price):
        gift.is_reserved = True
        db.commit()

    await manager.broadcast_to_wishlist(
        str(gift.wishlist_id),
        {
            "type": "contribution_added",
            "gift_id": gift_id,
            "amount": float(amount),
            "total": float(total),
            "user_id": user_id,
            "user_name": contribution.user.name,
        },
    )
    await manager.broadcast_to_landing({"type": "stats_updated"})

    return {
        "message": "Вклад добавлен!",
        "collected": total,
        "goal": gift.price,
        "is_reserved": gift.is_reserved,
    }

@app.get("/wishlists/{wishlist_id}/gifts")
def get_gifts(wishlist_id: int, db: Session = Depends(get_db)):
    gifts = (
        db.query(models.Gift)
        .filter(models.Gift.wishlist_id == wishlist_id)
        .all()
    )

    result = []
    for gift in gifts:
        total = (
            db.query(models.Contribution)
            .filter(models.Contribution.gift_id == gift.id)
            .with_entities(func.sum(models.Contribution.amount))
            .scalar()
            or 0
        )

        total_value = float(total)
        price_value = float(gift.price) if gift.price is not None else 0.0
        progress = int((total_value / price_value) * 100) if price_value > 0 else 0

        reservation = gift.reservation
        reserved_by = None
        if reservation:
            user = db.query(models.User).filter(models.User.id == reservation.user_id).first()
            reserved_by = {
                "id": user.id,
                "name": user.name
            } if user else None

        contributors = []
        for c in gift.contributions:
            user = db.query(models.User).filter(models.User.id == c.user_id).first()
            contributors.append({
                "id": c.id,
                "user_id": c.user_id,
                "user_name": user.name if user else f"User {c.user_id}",
                "amount": float(c.amount),
                "created_at": c.created_at.isoformat() if c.created_at else None
            })

        contributors.sort(key=lambda x: x["created_at"], reverse=True)

        result.append(
            {
                "id": gift.id,
                "title": gift.title,
                "price": price_value,
                "url": gift.url,
                "image_url": gift.image_url,
                "is_reserved": gift.is_reserved,
                "collected": total_value,
                "progress": progress,
                "reserved_by": reserved_by,
                "contributors": contributors,
                "has_contributions": len(contributors) > 0,
            }
        )

    return result

@app.put("/wishlists/{wishlist_id}")
def update_wishlist(
    wishlist_id: int, 
    payload: schemas.WishlistCreateWithOwner,
    db: Session = Depends(get_db)
):
    wishlist = db.query(models.Wishlist).filter(models.Wishlist.id == wishlist_id).first()
    if not wishlist:
        return {"error": "Вишлист не найден"}

    wishlist.title = payload.title
    wishlist.description = payload.description
    wishlist.event_date = payload.event_date
    wishlist.owner_id = payload.owner_id

    db.commit()
    db.refresh(wishlist)
    return wishlist

@app.delete("/wishlists/{wishlist_id}")
def delete_wishlist(wishlist_id: int, db: Session = Depends(get_db)):
    wishlist = db.query(models.Wishlist).filter(models.Wishlist.id == wishlist_id).first()
    if not wishlist:
        return {"error": "Вишлист не найден"}

    db.delete(wishlist)
    db.commit()
    return {"status": "deleted"}

@app.get("/ws/test")
def websocket_test():
    return {
        "status": "ok",
        "websocket_manager": str(type(manager)),
        "endpoint": "/ws/wishlists/{wishlist_id}",
        "message": "WebSocket поддержка активна"
    }

@app.get("/users/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    try:
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            return {"error": "Пользователь не найден"}
        
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "created_at": user.registered_at.isoformat() if user.registered_at else None
        }
    except Exception as e:
        print(f"Error getting user: {e}")
        return {"error": str(e)}

@app.websocket("/ws/landing")
async def websocket_landing(websocket: WebSocket):
    try:
        await manager.connect_landing(websocket)
        await websocket.send_json({"type": "connected", "message": "Landing stats"})
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
    except Exception as e:
        print(f"WebSocket landing error: {e}")
    finally:
        manager.disconnect_landing(websocket)

@app.websocket("/ws/wishlists/{wishlist_id}")
async def websocket_endpoint(websocket: WebSocket, wishlist_id: int):
    try:
        await manager.connect(websocket, str(wishlist_id))
        
        await websocket.send_json({
            "type": "connected",
            "wishlist_id": wishlist_id,
            "message": "WebSocket подключен успешно"
        })
        
        while True:
            try:
                await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except Exception as e:
                print(f"WebSocket error for wishlist {wishlist_id}: {e}")
                break
    except Exception as e:
        print(f"WebSocket connection error: {e}")
    finally:
        manager.disconnect(websocket, str(wishlist_id))
