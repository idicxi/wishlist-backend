from __future__ import annotations

import random
import re
import string
from datetime import datetime
import html
import json
from urllib.parse import urljoin

import httpx
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models, schemas
from .auth import (
    get_current_user_id_optional,
    get_current_user_id_required,
    router as auth_router,
)
from .database import Base, engine, get_db
from .websocket_manager import manager

app = FastAPI(title="Социальный вишлист")


origins = [
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

@app.put("/gifts/{gift_id}")
def update_gift(
    gift_id: int,
    title: str | None = None,
    price: float | None = None,
    url: str | None = None,
    image_url: str | None = None,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id_required),
):
    try:
        gift = db.query(models.Gift).filter(models.Gift.id == gift_id).first()
        if not gift:
            return {"error": "Подарок не найден"}
        wishlist = db.query(models.Wishlist).filter(models.Wishlist.id == gift.wishlist_id).first()
        if not wishlist or wishlist.owner_id != current_user_id:
            raise HTTPException(status_code=403, detail="Только владелец вишлиста может редактировать подарок")
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
            "is_reserved": gift.is_reserved,
        }
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating gift: {e}")
        return {"error": str(e)}


@app.delete("/gifts/{gift_id}")
def delete_gift(
    gift_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id_required),
):
    try:
        gift = db.query(models.Gift).filter(models.Gift.id == gift_id).first()
        if not gift:
            return {"error": "Подарок не найден"}
        wishlist = db.query(models.Wishlist).filter(models.Wishlist.id == gift.wishlist_id).first()
        if not wishlist or wishlist.owner_id != current_user_id:
            raise HTTPException(status_code=403, detail="Только владелец вишлиста может удалить подарок")
        db.delete(gift)
        db.commit()
        return {"status": "deleted"}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting gift: {e}")
        return {"error": str(e)}

@app.get("/")
def root():
    return {"message": "Социальный вишлист API работает! 🎉"}


@app.get("/api/parse-url")
def parse_url(url: str):
    """Извлекает og:title, og:image (и по возможности цену) по URL для автозаполнения карточки подарка."""
    if not url or not url.strip():
        return {"title": None, "image": None, "price": None}
    u = url.strip()
    if not u.startswith(("http://", "https://")):
        u = "https://" + u
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        }
        with httpx.Client(follow_redirects=True, timeout=10.0) as client:
            resp = client.get(u, headers=headers)
            resp.raise_for_status()
            html = resp.text
            base_url = str(resp.url)
    except httpx.HTTPError as e:
        print(f"parse-url fetch error: {e}")
        return {"title": None, "image": None, "price": None}
    title: str | None = None
    image: str | None = None
    price: float | None = None

    # 1) Пытаемся вытащить структурированные данные (Schema.org Product / Offer / AggregateOffer)
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>([\s\S]*?)</script>',
        html,
        re.I,
    ):
        raw_json = m.group(1).strip()
        if not raw_json:
            continue
        # Некоторые сайты кладут несколько JSON-объектов подряд — игнорируем ошибки парсинга
        try:
            data = json.loads(raw_json)
        except Exception:
            continue

        def extract_from_obj(obj: dict):
            nonlocal title, image, price
            if not isinstance(obj, dict):
                return
            t = obj.get("@type") or obj.get("type")
            if isinstance(t, str):
                t = [t]
            if isinstance(t, list) and not any(
                x in ("Product", "Offer", "AggregateOffer") for x in t
            ):
                # Не продукт — пропускаем
                pass

            # Название
            if not title and isinstance(obj.get("name"), str):
                title = obj["name"].strip() or title

            # Картинка
            img = obj.get("image")
            if not image and isinstance(img, str):
                image = img
            elif not image and isinstance(img, dict):
                # Некоторые сайты кладут image как ImageObject с полем url
                url_val = img.get("url")
                if isinstance(url_val, str):
                    image = url_val
            elif not image and isinstance(img, list) and img:
                first = img[0]
                if isinstance(first, str):
                    image = first
                elif isinstance(first, dict):
                    url_val = first.get("url")
                    if isinstance(url_val, str):
                        image = url_val

            # Цена: либо прямо в объекте, либо в offers
            offers = obj.get("offers")
            candidates = []
            if isinstance(offers, dict):
                candidates.append(offers)
            elif isinstance(offers, list):
                candidates.extend([o for o in offers if isinstance(o, dict)])

            for off in candidates:
                p = off.get("price") or off.get("lowPrice")
                if isinstance(p, (int, float)):
                    price = float(p)
                    break
                if isinstance(p, str):
                    raw = (
                        p.replace(" ", "")
                        .replace("\xa0", "")
                        .replace(",", ".")
                    )
                    try:
                        price = float(raw)
                        break
                    except ValueError:
                        continue

        if isinstance(data, dict):
            extract_from_obj(data)
        elif isinstance(data, list):
            for item in data:
                extract_from_obj(item)

    # 2) og:title / twitter:title + fallback <title>
    for meta in re.finditer(
        r'<meta[^>]+(?:property|name)=["\'](?:og:title|twitter:title)["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.I,
    ):
        title = meta.group(1).strip()
        if title:
            break
    if not title:
        for meta in re.finditer(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:title|twitter:title)["\']', html, re.I):
            title = meta.group(1).strip()
            if title:
                break
    # Фоллбэк: <title>...</title>
    if not title:
        m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
        if m:
            title = m.group(1).strip()

    # Чистим title: HTML-сущности и хвосты с названием сайта/продавца
    if title:
        # Декодируем &quot; &amp; и т.п.
        title = html.unescape(title)
        # Режем по разделителям « | », « — », « - », оставляем первую часть
        for sep in [" | ", " — ", " - "]:
            if sep in title:
                title = title.split(sep)[0]
                break
        title = title.strip(" \u00a0-–—")

    # 3) og:image / twitter:image
    for meta in re.finditer(
        r'<meta[^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\'][^>]+content=["\']([^"\']+)["\']',
        html,
        re.I,
    ):
        candidate = meta.group(1).strip()
        if candidate:
            image = candidate
            break
    if not image:
        for meta in re.finditer(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:property|name)=["\'](?:og:image|twitter:image)["\']', html, re.I):
            candidate = meta.group(1).strip()
            if candidate:
                image = candidate
                break

    # Нормализуем URL картинки: протокол-относительные и относительные пути
    if image:
        if image.startswith("//"):
            # //cdn.site.com/img.jpg -> https://cdn.site.com/img.jpg
            image = "https:" + image
        elif not image.startswith(("http://", "https://")) and "base_url" in locals():
            # /img/pic.jpg или img/pic.jpg -> абсолютный URL
            image = urljoin(base_url, image)

    # 4) Попытка достать цену по текстовым паттернам, если не нашли в JSON-LD
    price_match = re.search(r'"price"\s*:\s*["\']?([0-9\s.,]+)["\']?', html)
    if not price_match:
        price_match = re.search(r'content=["\']([0-9\s.,]+)\s*(?:₽|RUB)["\']', html)
    if price_match:
        raw = price_match.group(1)
        raw = raw.replace(" ", "").replace("\xa0", "").replace(",", ".")
        try:
            price = float(raw)
        except ValueError:
            price = None
    return {"title": title, "image": image, "price": price}


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
def create_wishlist(
    payload: schemas.WishlistCreateWithOwner,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id_required),
):
    slug = generate_unique_slug(payload.title, db)
    wishlist = models.Wishlist(
        title=payload.title,
        description=payload.description,
        event_date=payload.event_date,
        slug=slug,
        owner_id=current_user_id,
    )
    db.add(wishlist)
    db.commit()
    db.refresh(wishlist)
    return wishlist


@app.get("/wishlists/")
def list_wishlists(
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id_required),
):
    wishlists = (
        db.query(models.Wishlist)
        .filter(models.Wishlist.owner_id == current_user_id)
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
    current_user_id: int = Depends(get_current_user_id_required),
):
    try:
        wishlist = db.query(models.Wishlist).filter(models.Wishlist.id == wishlist_id).first()
        if not wishlist:
            raise HTTPException(status_code=404, detail="Вишлист не найден")
        if wishlist.owner_id != current_user_id:
            raise HTTPException(status_code=403, detail="Только владелец вишлиста может добавлять подарки")
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
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error creating gift: {e}")
        return {"error": str(e)}


@app.post("/gifts/{gift_id}/reserve")
async def reserve_gift(
    gift_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id_required),
):
    try:
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

        reservation = models.Reservation(gift_id=gift_id, user_id=current_user_id)
        gift.is_reserved = True

        db.add(reservation)
        db.commit()
        db.refresh(gift)

        user = db.query(models.User).filter(models.User.id == current_user_id).first()
        user_name = user.name if user else f"User {current_user_id}"

        await manager.broadcast_to_wishlist(
            str(gift.wishlist_id),
            {
                "type": "item_reserved",
                "gift_id": gift_id,
                "user_id": current_user_id,
                "user_name": user_name,
                "wishlist_id": gift.wishlist_id,
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
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id_required),
):
    gift = db.query(models.Gift).filter(models.Gift.id == gift_id).first()
    if not gift:
        return {"error": "Подарок не найден"}

    if gift.is_reserved:
        return {"error": "Подарок уже полностью выкуплен"}

    contribution = models.Contribution(gift_id=gift_id, user_id=current_user_id, amount=amount)
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
            "user_id": current_user_id,
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
def get_gifts(
    wishlist_id: int,
    db: Session = Depends(get_db),
    current_user_id: int | None = Depends(get_current_user_id_optional),
):
    wishlist = db.query(models.Wishlist).filter(models.Wishlist.id == wishlist_id).first()
    if not wishlist:
        raise HTTPException(status_code=404, detail="Вишлист не найден")
    is_owner = current_user_id is not None and wishlist.owner_id == current_user_id

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

        reserved_by = None
        contributors = []
        if not is_owner:
            reservation = gift.reservation
            if reservation:
                user = db.query(models.User).filter(models.User.id == reservation.user_id).first()
                reserved_by = (
                    {"id": user.id, "name": user.name} if user else None
                )
            for c in gift.contributions:
                user = db.query(models.User).filter(models.User.id == c.user_id).first()
                contributors.append({
                    "id": c.id,
                    "user_id": c.user_id,
                    "user_name": user.name if user else f"User {c.user_id}",
                    "amount": float(c.amount),
                    "created_at": c.created_at.isoformat() if c.created_at else None,
                })
            contributors.sort(key=lambda x: x["created_at"], reverse=True)

        result.append({
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
            # Владелец не должен видеть ни участников, ни сам факт сбора
            "has_contributions": len(contributors) > 0 if not is_owner else False,
        })

    return result

@app.put("/wishlists/{wishlist_id}")
def update_wishlist(
    wishlist_id: int,
    payload: schemas.WishlistCreateWithOwner,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id_required),
):
    wishlist = db.query(models.Wishlist).filter(models.Wishlist.id == wishlist_id).first()
    if not wishlist:
        return {"error": "Вишлист не найден"}
    if wishlist.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Только владелец может редактировать вишлист")

    wishlist.title = payload.title
    wishlist.description = payload.description
    wishlist.event_date = payload.event_date

    db.commit()
    db.refresh(wishlist)
    return wishlist


@app.delete("/wishlists/{wishlist_id}")
def delete_wishlist(
    wishlist_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id_required),
):
    wishlist = db.query(models.Wishlist).filter(models.Wishlist.id == wishlist_id).first()
    if not wishlist:
        return {"error": "Вишлист не найден"}
    if wishlist.owner_id != current_user_id:
        raise HTTPException(status_code=403, detail="Только владелец может удалить вишлист")

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
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id_required),
):
    if user_id != current_user_id:
        raise HTTPException(status_code=403, detail="Можно запрашивать только свой профиль")
    try:
        user = db.query(models.User).filter(models.User.id == user_id).first()
        if not user:
            return {"error": "Пользователь не найден"}
        return {
            "id": user.id,
            "email": user.email,
            "name": user.name,
            "created_at": user.registered_at.isoformat() if user.registered_at else None,
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
