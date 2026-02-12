from __future__ import annotations

from typing import Dict, Set

from fastapi import WebSocket


class ConnectionManager:
    """Менеджер WebSocket соединений для вишлистов и главной страницы."""

    def __init__(self):
        # wishlist_id (str) -> Set[WebSocket]
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self.landing_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket, wishlist_id: str):
        await websocket.accept()
        if wishlist_id not in self.active_connections:
            self.active_connections[wishlist_id] = set()
        self.active_connections[wishlist_id].add(websocket)

    def disconnect(self, websocket: WebSocket, wishlist_id: str):
        if wishlist_id in self.active_connections:
            self.active_connections[wishlist_id].discard(websocket)
            if not self.active_connections[wishlist_id]:
                del self.active_connections[wishlist_id]

    async def connect_landing(self, websocket: WebSocket):
        await websocket.accept()
        self.landing_connections.add(websocket)

    def disconnect_landing(self, websocket: WebSocket):
        self.landing_connections.discard(websocket)

    async def broadcast_to_landing(self, message: dict):
        """Отправляет сообщение всем подключённым к главной странице (для реалтайм статистики)."""
        disconnected = set()
        for connection in self.landing_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                print(f"WebSocket broadcast error for landing: {e}")
                disconnected.add(connection)
        for connection in disconnected:
            self.landing_connections.discard(connection)

    async def broadcast_to_wishlist(
        self,
        wishlist_id: str,
        message: dict,
    ):
        """Отправляет сообщение всем подключённым к вишлисту клиентам."""
        if wishlist_id not in self.active_connections:
            return

        disconnected = set()
        for connection in self.active_connections[wishlist_id]:
            try:
                await connection.send_json(message)
            except Exception as e:
                print(f"WebSocket broadcast error for wishlist {wishlist_id}: {e}")
                disconnected.add(connection)

        for connection in disconnected:
            self.active_connections[wishlist_id].discard(connection)

        if not self.active_connections[wishlist_id]:
            del self.active_connections[wishlist_id]


# Глобальный менеджер соединений
manager = ConnectionManager()
