"""
WebSockets con salas por campaña.

`push_state(cid)` guarda el combate de la campaña y lo difunde solo a los
clientes conectados a esa campaña. El endpoint `/ws/{cid}` autentica por
cookie de sesión y valida que el usuario sea el DM o un miembro aceptado.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .auth import COOKIE_NAME, user_for_token
from .config import get_config
from .database import db
from .state import combats, player_view

router = APIRouter()


class Hub:
    def __init__(self):
        # cid -> lista de (ws, is_dm, user_id): cada quien recibe su vista, que
        # depende de quién es (lo propio se ve entero) y de los ajustes del DM.
        self.rooms: dict[int, list[tuple[WebSocket, bool, int]]] = {}

    async def connect(self, cid: int, ws: WebSocket, is_dm: bool, user_id: int):
        await ws.accept()
        self.rooms.setdefault(cid, []).append((ws, is_dm, user_id))

    def disconnect(self, cid: int, ws: WebSocket):
        room = self.rooms.get(cid)
        if room:
            self.rooms[cid] = [t for t in room if t[0] is not ws]

    async def broadcast(self, cid: int, combat: dict, cfg: dict):
        dm_payload = {"type": "combat", "data": combat}
        vistas = {}      # una vista por jugador, reusada si tiene varias pestañas
        dead = []
        for ws, is_dm, uid in self.rooms.get(cid, []):
            if is_dm:
                payload = dm_payload
            else:
                if uid not in vistas:
                    vistas[uid] = {"type": "combat",
                                   "data": player_view(combat, cfg, uid)}
                payload = vistas[uid]
            try:
                await ws.send_json(payload)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(cid, ws)


hub = Hub()


async def push_state(cid: int):
    """Guarda y difunde el combate de una campaña (vista según quién mira)."""
    combats.save(cid)
    room = hub.rooms.get(cid) or []
    # solo leemos los ajustes si hay algún jugador escuchando
    cfg = None
    if any(not is_dm for _, is_dm, _ in room):
        with db() as conn:
            cfg = get_config(conn, cid)
    await hub.broadcast(cid, combats.get(cid), cfg)


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
    await hub.connect(cid, ws, is_dm, user["id"])
    combat = combats.get(cid)
    if is_dm:
        await ws.send_json({"type": "combat", "data": combat})
    else:
        with db() as conn:
            cfg = get_config(conn, cid)
        await ws.send_json({"type": "combat",
                            "data": player_view(combat, cfg, user["id"])})
    try:
        while True:
            raw = await ws.receive_text()  # keep-alive / heartbeat del cliente
            # El cliente manda pings periódicos para mantener viva la conexión a
            # través del proxy (Cloudflare) y detectar cortes; le devolvemos pong.
            if raw and '"ping"' in raw:
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        hub.disconnect(cid, ws)
