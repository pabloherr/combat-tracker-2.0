"""
WebSockets con salas por campaña.

`push_state(cid)` guarda el combate de la campaña y lo difunde solo a los
clientes conectados a esa campaña. El endpoint `/ws/{cid}` autentica por
cookie de sesión y valida que el usuario sea el DM o un miembro aceptado.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .auth import COOKIE_NAME, user_for_token
from .database import db
from .state import combats, player_view

router = APIRouter()


class Hub:
    def __init__(self):
        # cid -> lista de (ws, is_dm): así cada quien recibe su propia vista.
        self.rooms: dict[int, list[tuple[WebSocket, bool]]] = {}

    async def connect(self, cid: int, ws: WebSocket, is_dm: bool):
        await ws.accept()
        self.rooms.setdefault(cid, []).append((ws, is_dm))

    def disconnect(self, cid: int, ws: WebSocket):
        room = self.rooms.get(cid)
        if room:
            self.rooms[cid] = [(w, d) for (w, d) in room if w is not ws]

    async def broadcast(self, cid: int, combat: dict):
        dm_payload = {"type": "combat", "data": combat}
        player_payload = {"type": "combat", "data": player_view(combat)}
        dead = []
        for ws, is_dm in self.rooms.get(cid, []):
            try:
                await ws.send_json(dm_payload if is_dm else player_payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(cid, ws)


hub = Hub()


async def push_state(cid: int):
    """Guarda y difunde el combate de una campaña (vista según el rol)."""
    combats.save(cid)
    await hub.broadcast(cid, combats.get(cid))


def _is_dm(cid: int, user_id: int):
    """Devuelve True si es DM, False si es miembro aceptado, None si sin acceso."""
    with db() as conn:
        c = conn.execute("SELECT dm_id FROM campaigns WHERE id=?", (cid,)).fetchone()
        if not c:
            return None
        if c["dm_id"] == user_id:
            return True
        member = conn.execute(
            "SELECT 1 FROM campaign_members WHERE campaign_id=? AND user_id=? AND status='accepted'",
            (cid, user_id),
        ).fetchone()
        return False if member else None


@router.websocket("/ws/{cid}")
async def websocket_endpoint(ws: WebSocket, cid: int):
    user = user_for_token(ws.cookies.get(COOKIE_NAME))
    is_dm = _is_dm(cid, user["id"]) if user else None
    if is_dm is None:
        await ws.close(code=1008)
        return
    await hub.connect(cid, ws, is_dm)
    combat = combats.get(cid)
    await ws.send_json({"type": "combat", "data": combat if is_dm else player_view(combat)})
    try:
        while True:
            await ws.receive_text()  # keep-alive
    except WebSocketDisconnect:
        hub.disconnect(cid, ws)
