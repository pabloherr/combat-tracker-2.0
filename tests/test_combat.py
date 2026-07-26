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


def _me(cli, cid):
    return _find(cli.get(f"/api/campaigns/{cid}/combat").json()["participants"], "player")


def test_out_of_combat_change_reaches_combat(make_client):
    """Lo que el jugador toca en 'Mi personaje' durante un combate tiene que verse
    en el combate (y en la pantalla del DM), no quedar en dos estados distintos."""
    dm, pl, cid, chid, _ = _combat(make_client, inv_max=10)
    # vida desde la ficha (endpoint de personaje, no el de combate)
    pl.post(f"/api/characters/{chid}/stat", json={"stat": "vida", "delta": -7})
    assert _me(dm, cid)["vida"] == 13          # el DM lo ve en el combate
    assert _me(pl, cid)["vida"] == 13
    # focus e investidura
    pl.post(f"/api/characters/{chid}/stat", json={"stat": "focus", "delta": -3})
    assert _me(dm, cid)["focus"] == 7
    # estados
    pl.post(f"/api/characters/{chid}/status", json={"status": "Slowed"})
    assert "Slowed" in _me(dm, cid)["statuses"]
    pl.post(f"/api/characters/{chid}/status/remove_one", json={"status": "Slowed"})
    assert "Slowed" not in _me(dm, cid)["statuses"]


def test_injuries_reach_combat(make_client):
    dm, pl, cid, chid, _ = _combat(make_client)
    assert _me(dm, cid)["injuries"] == []
    r = pl.post(f"/api/characters/{chid}/injuries",
                json={"name": "Exhausted [-2]", "days": 3}).json()
    iid = r["injuries"][0]["id"]
    inj = _me(dm, cid)["injuries"]
    assert len(inj) == 1 and inj[0]["name"] == "Exhausted [-2]" and inj[0]["days"] == 3
    # cambiar los días y curar también se reflejan
    pl.post(f"/api/characters/{chid}/injuries/{iid}/days", json={"delta": -1})
    assert _me(dm, cid)["injuries"][0]["days"] == 2
    pl.delete(f"/api/characters/{chid}/injuries/{iid}")
    assert _me(dm, cid)["injuries"] == []


def test_combat_does_not_overwrite_out_of_combat_change(make_client):
    """El bug de fondo: sin sincronizar, el combate guardaba su copia vieja encima
    de lo que el jugador había cambiado desde su ficha."""
    dm, pl, cid, chid, _ = _combat(make_client)
    pl.post(f"/api/characters/{chid}/stat", json={"stat": "vida", "delta": -10})  # 20 -> 10
    # ahora un cambio hecho DENTRO del combate parte de 10, no de 20
    uid = _me(pl, cid)["uid"]
    pl.post(f"/api/campaigns/{cid}/combat/stat", json={"uid": uid, "stat": "vida", "delta": -1})
    assert _me(dm, cid)["vida"] == 9
    ch = next(m["character"] for m in pl.get(f"/api/campaigns/{cid}/roster").json()["members"])
    assert ch["vida"] == 9      # la ficha y el combate coinciden


def test_edit_sheet_syncs_maximums(make_client):
    dm, pl, cid, chid, _ = _combat(make_client)
    pl.put(f"/api/characters/{chid}", json={
        "name": "Kal el Grande", "campaign_id": cid,
        "vida_max": 40, "focus_max": 10, "inv_max": 0, "sheet": {}})
    me = _me(dm, cid)
    assert me["vida_max"] == 40 and me["name"] == "Kal el Grande"


def test_pet_change_out_of_combat_reaches_combat(make_client):
    from helpers import SAMPLE_STATBLOCK, get_enemies, import_enemy
    dm, pl, cid, chid = party(make_client)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    dm.post(f"/api/campaigns/{cid}/pet-options/{eid}")
    pid = pl.post(f"/api/characters/{chid}/pets/from-enemy",
                  json={"enemy_id": eid}).json()["id"]
    encid = dm.post(f"/api/campaigns/{cid}/encounters",
                    json={"name": "E", "enemies": [{"enemy_id": eid, "cantidad": 1}]}).json()["id"]
    dm.post(f"/api/campaigns/{cid}/combat/start/{encid}")
    pl.post(f"/api/characters/{chid}/pets/{pid}/stat", json={"stat": "vida", "delta": -5})
    pet = _find(dm.get(f"/api/campaigns/{cid}/combat").json()["participants"], "pet")
    assert pet["vida"] == 13     # 18 - 5


def test_guard_player_cannot_touch_enemy(make_client):
    dm, pl, cid, chid, combat = _combat(make_client)
    enemy = _find(combat["participants"], "enemy")
    r = pl.post(f"/api/campaigns/{cid}/combat/stat",
                json={"uid": enemy["uid"], "stat": "vida", "delta": -5})
    assert r.status_code == 403
