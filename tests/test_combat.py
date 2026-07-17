"""Combate: armado de participantes, stats, vida máxima, marcos, ocultar, guard."""

from helpers import (SAMPLE_STATBLOCK, create_character, import_enemy, invite,
                     make_user, party, create_campaign, get_enemies)


def _combat(make_client, inv_max=0):
    """DM + jugador + enemigo, encuentro iniciado. Devuelve (dm, pl, cid, chid, combat)."""
    dm, pl, cid, chid = party(make_client, inv_max=inv_max)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    encid = dm.post(f"/api/campaigns/{cid}/encounters",
                    json={"name": "E", "enemies": [{"enemy_id": eid, "cantidad": 1}]}).json()["id"]
    dm.post(f"/api/campaigns/{cid}/combat/start/{encid}")
    combat = dm.get(f"/api/campaigns/{cid}/combat").json()
    return dm, pl, cid, chid, combat


def _find(parts, kind):
    return next(p for p in parts if p["kind"] == kind)


def test_participants_built(make_client):
    dm, pl, cid, chid, combat = _combat(make_client)
    kinds = sorted(p["kind"] for p in combat["participants"])
    assert kinds == ["enemy", "player"]
    assert combat["active"] is True


def test_stat_and_vida_max(make_client):
    dm, pl, cid, chid, combat = _combat(make_client)
    enemy = _find(combat["participants"], "enemy")
    uid = enemy["uid"]
    # bajar vida
    dm.post(f"/api/campaigns/{cid}/combat/stat", json={"uid": uid, "stat": "vida", "delta": -5})
    # bajar la vida máxima recorta la actual
    r = dm.post(f"/api/campaigns/{cid}/combat/vida_max", json={"uid": uid, "value": 6}).json()
    assert r["vida_max"] == 6 and r["vida"] == 6


def test_marcos_discharge_on_charge_in_combat(make_client):
    # Preparar marcos y vaciar la investidura ANTES de iniciar el combate, para que
    # el participante entre con inv=0 (el combate snapshotea el estado al iniciar).
    dm, pl, cid, chid = party(make_client, inv_max=10)
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 6, "opacos": 0})
    pl.post(f"/api/characters/{chid}/stat", json={"stat": "inv", "delta": -10})  # inv a 0
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    encid = dm.post(f"/api/campaigns/{cid}/encounters",
                    json={"name": "E", "enemies": [{"enemy_id": eid, "cantidad": 1}]}).json()["id"]
    dm.post(f"/api/campaigns/{cid}/combat/start/{encid}")
    me = _find(pl.get(f"/api/campaigns/{cid}/combat").json()["participants"], "player")
    # cargar +4 de investidura en combate apaga 4 marcos cargados (6 -> 2)
    pl.post(f"/api/campaigns/{cid}/combat/stat", json={"uid": me["uid"], "stat": "inv", "delta": 4})
    ch = next(m["character"] for m in pl.get(f"/api/campaigns/{cid}/roster").json()["members"])
    assert ch["inv"] == 4 and ch["marcos_light"] == 2


def test_player_view_hides_hidden_enemies(make_client):
    dm, pl, cid, chid, combat = _combat(make_client)
    enemy = _find(combat["participants"], "enemy")
    # el jugador ve al enemigo
    assert any(p["kind"] == "enemy" for p in pl.get(f"/api/campaigns/{cid}/combat").json()["participants"])
    dm.post(f"/api/campaigns/{cid}/combat/hidden/{enemy['uid']}")
    # tras ocultarlo, el jugador ya no lo ve
    assert not any(p["kind"] == "enemy" for p in pl.get(f"/api/campaigns/{cid}/combat").json()["participants"])
    # el DM sí lo sigue viendo
    assert any(p["kind"] == "enemy" for p in dm.get(f"/api/campaigns/{cid}/combat").json()["participants"])


def test_guard_player_cannot_touch_enemy(make_client):
    dm, pl, cid, chid, combat = _combat(make_client)
    enemy = _find(combat["participants"], "enemy")
    r = pl.post(f"/api/campaigns/{cid}/combat/stat",
                json={"uid": enemy["uid"], "stat": "vida", "delta": -5})
    assert r.status_code == 403
