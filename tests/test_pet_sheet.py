"""Editar la ficha de la mascota: el jugador la hace suya sin tocar el bestiario."""

from helpers import (SAMPLE_STATBLOCK, create_character, get_enemies,
                     import_enemy, invite, make_user, party)


def _mascota(make_client):
    """DM + jugador con un Axehound de mascota. Devuelve (dm, pl, cid, chid, eid, pid)."""
    dm, pl, cid, chid = party(make_client)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    dm.post(f"/api/campaigns/{cid}/pet-options/{eid}")
    pid = pl.post(f"/api/characters/{chid}/pets/from-enemy",
                  json={"enemy_id": eid}).json()["id"]
    return dm, pl, cid, chid, eid, pid


def _pet(cli, chid, pid):
    return next(p for p in cli.get(f"/api/characters/{chid}/pets").json() if p["id"] == pid)


FICHA = {
    "name": "Rocío",
    "vida_max": 30, "focus_max": 4, "inv_max": 2,
    "acciones": [{"nombre": "Mordisco", "coste": "", "descripcion": "Muerde fuerte."}],
    "stats": {
        "tier": "Tier 2 Rival - Large Animal",
        "physical": {"str": 5, "def": 15, "spd": 6},
        "cognitive": {"int": 1, "def": 10, "wil": 2},
        "spiritual": {"awa": 3, "def": 11, "pre": 1},
        "movement": "40 ft.", "senses": "20 ft. (smell)", "deflect": "2 (hide)",
        "immunities": "Prone", "resistances": "Cold", "weaknesses": "Fire",
        "languages": "—",
        "skills": {"physical": "Athletics +6", "cognitive": "", "spiritual": "", "surge": ""},
        "traits": [{"name": "Olfato agudo", "desc": "Rastrea a 1 km."}],
        "actions": [{"name": "Mordisco", "desc": "Muerde fuerte."}],
        "opportunities": [{"name": "Derribo", "desc": "Tira al suelo al que huye."}],
    },
}


# ── Lo básico ──────────────────────────────────────────────

def test_edit_full_sheet(make_client):
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    r = pl.put(f"/api/characters/{chid}/pets/{pid}/sheet", json=FICHA)
    assert r.status_code == 200, r.text
    p = _pet(pl, chid, pid)
    assert p["name"] == "Rocío"
    assert (p["vida_max"], p["focus_max"], p["inv_max"]) == (30, 4, 2)
    assert p["stats"]["physical"] == {"str": 5, "def": 15, "spd": 6}
    assert p["stats"]["traits"][0]["name"] == "Olfato agudo"
    assert p["acciones"][0]["nombre"] == "Mordisco"


def test_name_is_trimmed_and_required(make_client):
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    ok = pl.put(f"/api/characters/{chid}/pets/{pid}/sheet", json={**FICHA, "name": "  Rocío  "})
    assert ok.status_code == 200 and ok.json()["name"] == "Rocío"
    assert pl.put(f"/api/characters/{chid}/pets/{pid}/sheet",
                  json={**FICHA, "name": "   "}).status_code == 400
    assert _pet(pl, chid, pid)["name"] == "Rocío"


def test_vida_max_has_to_be_at_least_one(make_client):
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    assert pl.put(f"/api/characters/{chid}/pets/{pid}/sheet",
                  json={**FICHA, "vida_max": 0}).status_code == 400
    assert _pet(pl, chid, pid)["vida_max"] == 18      # la del bestiario, intacta


# ── Los valores actuales ───────────────────────────────────

def test_lowering_the_max_clamps_the_current(make_client):
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    pl.post(f"/api/characters/{chid}/pets/{pid}/stat", json={"stat": "vida", "delta": -3})
    assert _pet(pl, chid, pid)["vida"] == 15
    # sin mandar 'vida', se conserva la actual recortada al máximo nuevo
    pl.put(f"/api/characters/{chid}/pets/{pid}/sheet", json={**FICHA, "vida_max": 10})
    p = _pet(pl, chid, pid)
    assert (p["vida_max"], p["vida"]) == (10, 10)


def test_current_values_can_be_set_by_hand(make_client):
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    pl.put(f"/api/characters/{chid}/pets/{pid}/sheet",
           json={**FICHA, "vida": 7, "focus": 99, "inv": -5})
    p = _pet(pl, chid, pid)
    # 99 se recorta al máximo (4) y -5 al piso (0)
    assert (p["vida"], p["focus"], p["inv"]) == (7, 4, 0)


# ── Es una copia: no salpica ───────────────────────────────

def test_editing_does_not_touch_the_bestiary(make_client):
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    pl.put(f"/api/characters/{chid}/pets/{pid}/sheet", json=FICHA)
    e = get_enemies(dm, cid)[0]
    assert e["name"] == "Axehound" and e["vida_max"] == 18


def test_editing_does_not_touch_another_players_pet(make_client):
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    otro = make_user(make_client, "pl2", "player")
    invite(dm, cid, "pl2")
    chid2 = create_character(otro, cid, "Shallan")
    pid2 = otro.post(f"/api/characters/{chid2}/pets/from-enemy",
                     json={"enemy_id": eid}).json()["id"]
    pl.put(f"/api/characters/{chid}/pets/{pid}/sheet", json=FICHA)
    assert _pet(otro, chid2, pid2)["name"] == "Axehound"


# ── Quién puede ────────────────────────────────────────────

def test_the_dm_can_edit_it(make_client):
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    assert dm.put(f"/api/characters/{chid}/pets/{pid}/sheet",
                  json={**FICHA, "name": "Chull de carga"}).status_code == 200
    assert _pet(pl, chid, pid)["name"] == "Chull de carga"


def test_a_stranger_cannot_edit_it(make_client):
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    ajeno = make_user(make_client, "pl3", "player")
    assert ajeno.put(f"/api/characters/{chid}/pets/{pid}/sheet",
                     json=FICHA).status_code == 404


def test_a_shared_pet_is_still_edited_only_by_its_owner(make_client):
    """De todos = cualquiera la maneja. La ficha sigue siendo del que la trajo."""
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    otro = make_user(make_client, "pl2", "player")
    invite(dm, cid, "pl2")
    chid2 = create_character(otro, cid, "Shallan")
    pl.post(f"/api/characters/{chid}/pets/{pid}/shared")
    # la maneja (le baja la vida)…
    assert otro.post(f"/api/characters/{chid2}/pets/{pid}/stat",
                     json={"stat": "vida", "delta": -2}).status_code == 200
    # …pero no le reescribe la ficha
    assert otro.put(f"/api/characters/{chid2}/pets/{pid}/sheet",
                    json=FICHA).status_code == 404
    assert _pet(pl, chid, pid)["name"] == "Axehound"


# ── En combate ─────────────────────────────────────────────

def test_edit_syncs_into_an_active_combat(make_client):
    dm, pl, cid, chid, eid, pid = _mascota(make_client)
    encid = dm.post(f"/api/campaigns/{cid}/encounters",
                    json={"name": "E", "enemies": [{"enemy_id": eid, "cantidad": 1}]}).json()["id"]
    dm.post(f"/api/campaigns/{cid}/combat/start/{encid}")
    pl.put(f"/api/characters/{chid}/pets/{pid}/sheet", json={**FICHA, "vida": 12})
    parts = dm.get(f"/api/campaigns/{cid}/combat").json()["participants"]
    pet = next(p for p in parts if p["kind"] == "pet")
    assert pet["name"] == "Rocío"
    assert (pet["vida"], pet["vida_max"]) == (12, 30)
    assert pet["stats"]["movement"] == "40 ft."
