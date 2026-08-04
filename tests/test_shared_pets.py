"""Mascotas de todos: las maneja cualquiera de la mesa, no solo quien las trajo."""

from helpers import (SAMPLE_STATBLOCK, create_character, get_enemies,
                     import_enemy, invite, make_user, party)


def _mesa(make_client):
    """DM + dos jugadores; el primero trae un Axehound. Devuelve
    (dm, pl, otro, cid, chid, chid2, pid)."""
    dm, pl, cid, chid = party(make_client)
    otro = make_user(make_client, "pl2", "player")
    invite(dm, cid, "pl2")
    chid2 = create_character(otro, cid, "Shallan")
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    dm.post(f"/api/campaigns/{cid}/pet-options/{eid}")
    pid = pl.post(f"/api/characters/{chid}/pets/from-enemy",
                  json={"enemy_id": eid}).json()["id"]
    return dm, pl, otro, cid, chid, chid2, pid


def _uid_de(dm, cid, username):
    for m in dm.get(f"/api/campaigns/{cid}/members").json():
        if m["username"] == username:
            return m["user_id"]
    raise AssertionError(f"falta {username}")


def _compartir(cli, chid, pid):
    r = cli.post(f"/api/characters/{chid}/pets/{pid}/shared")
    assert r.status_code == 200, r.text
    return r.json()["compartida"]


def _vida(cli, cid, nombre="Axehound"):
    for m in cli.get(f"/api/campaigns/{cid}/roster").json()["members"]:
        for p in m["pets"]:
            if p["name"] == nombre:
                return p
    return None


# ── El interruptor ─────────────────────────────────────────

def test_a_pet_starts_private(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    assert pl.get(f"/api/characters/{chid}/pets").json()[0]["compartida"] is False
    # el otro jugador no la puede tocar
    assert otro.post(f"/api/characters/{chid}/pets/{pid}/stat",
                     json={"stat": "vida", "delta": -3}).status_code == 404


def test_sharing_lets_anyone_manage_it(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    assert _compartir(pl, chid, pid) is True
    r = otro.post(f"/api/characters/{chid}/pets/{pid}/stat",
                  json={"stat": "vida", "delta": -5})
    assert r.status_code == 200 and r.json()["value"] == 13
    # y también los estados
    assert otro.post(f"/api/characters/{chid}/pets/{pid}/status",
                     json={"status": "Stunned"}).status_code == 200
    assert _vida(otro, cid)["statuses"] == ["Stunned"]


def test_it_can_be_managed_through_your_own_character(make_client):
    """El jugador le pega a la URL de su propio personaje: la mascota de todos
    se encuentra igual, sin tener que saber de quién es."""
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    _compartir(pl, chid, pid)
    r = otro.post(f"/api/characters/{chid2}/pets/{pid}/stat",
                  json={"stat": "vida", "delta": -2})
    assert r.status_code == 200 and r.json()["value"] == 16


def test_unsharing_takes_it_back(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    _compartir(pl, chid, pid)
    assert _compartir(pl, chid, pid) is False
    assert otro.post(f"/api/characters/{chid}/pets/{pid}/stat",
                     json={"stat": "vida", "delta": -1}).status_code == 404


def test_only_the_owner_or_the_dm_flips_the_switch(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    assert otro.post(f"/api/characters/{chid}/pets/{pid}/shared").status_code == 404
    assert dm.post(f"/api/characters/{chid}/pets/{pid}/shared").status_code == 200


def test_someone_outside_the_campaign_is_still_out(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    _compartir(pl, chid, pid)
    ajeno = make_user(make_client, "nadie", "player")
    assert ajeno.post(f"/api/characters/{chid}/pets/{pid}/stat",
                      json={"stat": "vida", "delta": -1}).status_code == 404


# ── Lo que se ve ───────────────────────────────────────────

def test_a_shared_pet_is_never_masked(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    dm.put(f"/api/campaigns/{cid}/config", json={"ver_vida_aliados": "no"})
    # privada: el otro solo ve lo que el DM habilitó
    p = _vida(otro, cid)
    assert p["vida"] is None
    _compartir(pl, chid, pid)
    p = _vida(otro, cid)
    assert p["vida"] == 18 and p["vida_max"] == 18 and p["compartida"] is True


# ── Inventario ─────────────────────────────────────────────

def test_anyone_can_load_a_shared_pet(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    _compartir(pl, chid, pid)
    dm.post(f"/api/campaigns/{cid}/members/{_uid_de(dm, cid, 'pl2')}/can-create-items")
    r = otro.post(f"/api/characters/{chid2}/pets/{pid}/inventory",
                  json={"name": "Alforjas", "slots": 1})
    assert r.status_code == 200, r.text
    inv = otro.get(f"/api/characters/{chid2}/inventory").json()
    assert [p["name"] for p in inv["compartidas"]] == ["Axehound"]
    assert inv["compartidas"][0]["items"][0]["name"] == "Alforjas"
    # y la puede vaciar: el objeto es del grupo, no de quien lo cargó
    eid = inv["compartidas"][0]["items"][0]["id"]
    assert otro.delete(f"/api/characters/{chid2}/inventory/{eid}").status_code == 200


def test_a_private_pet_is_not_in_anyone_elses_inventory(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    inv = otro.get(f"/api/characters/{chid2}/inventory").json()
    assert inv["compartidas"] == []
    assert otro.post(f"/api/characters/{chid2}/pets/{pid}/inventory",
                     json={"name": "Alforjas"}).status_code == 404


def test_the_owner_sees_it_among_their_own(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    _compartir(pl, chid, pid)
    inv = pl.get(f"/api/characters/{chid}/inventory").json()
    assert [p["name"] for p in inv["pets"]] == ["Axehound"]
    assert inv["pets"][0]["compartida"] is True
    assert inv["compartidas"] == []      # no se duplica


# ── Combate ────────────────────────────────────────────────

def _combate(dm, cid):
    enc = dm.post(f"/api/campaigns/{cid}/encounters",
                  json={"name": "E", "enemies": []}).json()["id"]
    r = dm.post(f"/api/campaigns/{cid}/combat/start/{enc}")
    assert r.status_code == 200, r.text


def _parte(cli, cid, nombre):
    for p in cli.get(f"/api/campaigns/{cid}/combat").json()["participants"]:
        if p["name"] == nombre:
            return p
    return None


def test_in_combat_anyone_can_hit_a_shared_pet(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    _compartir(pl, chid, pid)
    _combate(dm, cid)
    uid = _parte(dm, cid, "Axehound")["uid"]
    r = otro.post(f"/api/campaigns/{cid}/combat/stat",
                  json={"uid": uid, "stat": "vida", "delta": -4})
    assert r.status_code == 200, r.text
    assert _parte(otro, cid, "Axehound")["vida"] == 14      # sin enmascarar


def test_in_combat_a_private_pet_stays_private(make_client):
    dm, pl, otro, cid, chid, chid2, pid = _mesa(make_client)
    dm.put(f"/api/campaigns/{cid}/config", json={"ver_vida_aliados": "no"})
    _combate(dm, cid)
    uid = _parte(dm, cid, "Axehound")["uid"]
    assert otro.post(f"/api/campaigns/{cid}/combat/stat",
                     json={"uid": uid, "stat": "vida", "delta": -4}).status_code == 403
    assert _parte(otro, cid, "Axehound")["vida"] is None
