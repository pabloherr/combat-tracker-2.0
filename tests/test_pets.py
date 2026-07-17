"""Mascotas desde el bestiario: lista por campaña, copia (snapshot), validación."""

from helpers import (SAMPLE_STATBLOCK, create_campaign, create_character,
                     get_enemies, import_enemy, invite, make_user)


def _setup(make_client):
    """DM con bestiario + jugador con personaje en la campaña A. Devuelve
    (dm, pl, cid_A, chid, eid)."""
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm, "A")
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    invite(dm, cid, "pl")
    chid = create_character(pl, cid, "Kal")
    return dm, pl, cid, chid, eid


def test_from_enemy_requires_enabled(make_client):
    dm, pl, cid, chid, eid = _setup(make_client)
    # sin habilitar, no está en la lista ni se puede agregar
    assert pl.get(f"/api/campaigns/{cid}/pet-options").json() == []
    assert pl.post(f"/api/characters/{chid}/pets/from-enemy",
                   json={"enemy_id": eid}).status_code == 404


def test_from_enemy_snapshot(make_client):
    dm, pl, cid, chid, eid = _setup(make_client)
    dm.post(f"/api/campaigns/{cid}/pet-options/{eid}")
    assert pl.post(f"/api/characters/{chid}/pets/from-enemy",
                   json={"enemy_id": eid}).status_code == 200
    pet = pl.get(f"/api/characters/{chid}/pets").json()[0]
    assert pet["name"] == "Axehound" and pet["vida_max"] == 18 and pet["acciones"]
    # editar el enemigo del bestiario NO cambia la mascota ya agregada (snapshot)
    e = get_enemies(dm, cid)[0]
    e.update({"name": "Axehound Gigante", "vida_max": 99})
    dm.put(f"/api/campaigns/{cid}/enemies/{eid}", json=e)
    pet = pl.get(f"/api/characters/{chid}/pets").json()[0]
    assert pet["name"] == "Axehound" and pet["vida_max"] == 18


def test_validation_is_per_campaign(make_client):
    dm, pl, cid, chid, eid = _setup(make_client)
    # habilitar en otra campaña del mismo DM no sirve para un PJ de la campaña A
    cidB = create_campaign(dm, "B")
    dm.post(f"/api/campaigns/{cidB}/pet-options/{eid}")
    assert pl.post(f"/api/characters/{chid}/pets/from-enemy",
                   json={"enemy_id": eid}).status_code == 404


def test_delete_pet(make_client):
    dm, pl, cid, chid, eid = _setup(make_client)
    dm.post(f"/api/campaigns/{cid}/pet-options/{eid}")
    pid = pl.post(f"/api/characters/{chid}/pets/from-enemy",
                  json={"enemy_id": eid}).json()["id"]
    assert pl.delete(f"/api/characters/{chid}/pets/{pid}").status_code == 200
    assert pl.get(f"/api/characters/{chid}/pets").json() == []
