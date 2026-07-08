"""
WebSockets con salas por campaña.

`push_state(cid)` guarda el combate de la campaña y lo difunde solo a los
clientes conectados a esa campaña. El endpoint `/ws/{cid}` autentica por
cookie de sesión y valida que el usuario sea el DM o un miembro aceptado.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .auth import COOKIE_NAME, user_for_token
from .database import db
from .state import combats

router = APIRouter()


class Hub:
    def __init__(self):
        self.rooms: dict[int, list[WebSocket]] = {}

    async def connect(self, cid: int, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(cid, []).append(ws)

    def disconnect(self, cid: int, ws: WebSocket):
        room = self.rooms.get(cid)
        if room and ws in room:
            room.remove(ws)

    async def broadcast(self, cid: int, payload: dict):
        dead = []
        for ws in self.rooms.get(cid, []):
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(cid, ws)


hub = Hub()


async def push_state(cid: int):
    """Guarda y difunde el combate de una campaña."""
    combats.save(cid)
    await hub.broadcast(cid, {"type": "combat", "data": combats.get(cid)})


def _has_access(cid: int, user_id: int) -> bool:
    with db() as conn:
        c = conn.execute("SELECT dm_id FROM campaigns WHERE id=?", (cid,)).fetchone()
        if not c:
            return False
        if c["dm_id"] == user_id:
            return True
        return conn.execute(
            "SELECT 1 FROM campaign_members WHERE campaign_id=? AND user_id=? AND status='accepted'",
            (cid, user_id),
        ).fetchone() is not None


@router.websocket("/ws/{cid}")
async def websocket_endpoint(ws: WebSocket, cid: int):
    user = user_for_token(ws.cookies.get(COOKIE_NAME))
    if not user or not _has_access(cid, user["id"]):
        await ws.close(code=1008)
        return
    await hub.connect(cid, ws)
    await ws.send_json({"type": "combat", "data": combats.get(cid)})
    try:
        while True:
            await ws.receive_text()  # keep-alive
    except WebSocketDisconnect:
        hub.disconnect(cid, ws)
