"""WebSocket del combate: estado inicial, heartbeat (ping/pong) y acceso."""

import pytest
from starlette.websockets import WebSocketDisconnect

from helpers import create_campaign, make_user, party


def test_sends_combat_on_connect(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    with dm.websocket_connect(f"/ws/{cid}") as ws:
        msg = ws.receive_json()
        assert msg["type"] == "combat" and "participants" in msg["data"]


def test_ping_gets_pong(make_client):
    """El cliente manda pings periódicos para que el proxy no corte la conexión
    por inactividad y para detectar sockets muertos; el server responde pong."""
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    with dm.websocket_connect(f"/ws/{cid}") as ws:
        ws.receive_json()                      # estado inicial del combate
        ws.send_text('{"type":"ping"}')
        assert ws.receive_json() == {"type": "pong"}
        # sigue vivo y responde más de una vez
        ws.send_text('{"type":"ping"}')
        assert ws.receive_json() == {"type": "pong"}


def test_player_member_can_connect(make_client):
    dm, pl, cid, chid = party(make_client)
    with pl.websocket_connect(f"/ws/{cid}") as ws:
        assert ws.receive_json()["type"] == "combat"


def test_rejects_without_session(make_client, client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    # `client` no tiene sesión: el server cierra la conexión
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(f"/ws/{cid}") as ws:
            ws.receive_json()


def test_rejects_non_member(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    intruso = make_user(make_client, "intruso", "player")
    with pytest.raises(WebSocketDisconnect):
        with intruso.websocket_connect(f"/ws/{cid}") as ws:
            ws.receive_json()
