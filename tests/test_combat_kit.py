"""Equipo de combate: arma principal y secundaria, armadura puesta, y qué
objetos con cargas se ven en la pestaña de combate."""

from helpers import party


def _inv(pl, chid):
    return pl.get(f"/api/characters/{chid}/inventory").json()


def _items(pl, chid):
    return {i["name"]: i for i in _inv(pl, chid)["character"]["items"]}


def _dar(dm, chid, **kw):
    r = dm.post(f"/api/characters/{chid}/inventory", json=kw)
    assert r.status_code == 200, r.text


def _rol(pl, chid, eid, rol):
    return pl.post(f"/api/characters/{chid}/inventory/{eid}/rol", json={"rol": rol})


def _armado(make_client):
    dm, pl, cid, chid = party(make_client)
    _dar(dm, chid, name="Espada", kind="arma",
         stats={"weapon_class": "heavy", "damage": "1d8"})
    _dar(dm, chid, name="Daga", kind="arma",
         stats={"weapon_class": "light", "damage": "1d4", "expert_traits": ["Offhand"]})
    _dar(dm, chid, name="Cota", kind="armadura", stats={"deflect": 2})
    _dar(dm, chid, name="Placa", kind="armadura", stats={"deflect": 4})
    _dar(dm, chid, name="Antiséptico", usos_max=3)
    return dm, pl, cid, chid


# ── Roles ──────────────────────────────────────────────────

def test_only_one_weapon_per_hand_and_one_armor_worn(make_client):
    dm, pl, cid, chid = _armado(make_client)
    it = _items(pl, chid)
    assert _rol(pl, chid, it["Espada"]["id"], "principal").status_code == 200
    assert _rol(pl, chid, it["Daga"]["id"], "secundaria").status_code == 200
    assert _rol(pl, chid, it["Cota"]["id"], "puesta").status_code == 200
    it = _items(pl, chid)
    assert (it["Espada"]["rol"], it["Daga"]["rol"], it["Cota"]["rol"]) == \
        ("principal", "secundaria", "puesta")

    # la daga pasa a principal: la espada se queda sin rol
    _rol(pl, chid, it["Daga"]["id"], "principal")
    it = _items(pl, chid)
    assert it["Daga"]["rol"] == "principal" and it["Espada"]["rol"] == ""

    # ponerse la placa saca la cota
    _rol(pl, chid, it["Placa"]["id"], "puesta")
    it = _items(pl, chid)
    assert it["Placa"]["rol"] == "puesta" and it["Cota"]["rol"] == ""


def test_the_role_matches_the_kind_of_object(make_client):
    dm, pl, cid, chid = _armado(make_client)
    it = _items(pl, chid)
    assert _rol(pl, chid, it["Cota"]["id"], "principal").status_code == 400
    assert _rol(pl, chid, it["Espada"]["id"], "puesta").status_code == 400
    assert _rol(pl, chid, it["Antiséptico"]["id"], "principal").status_code == 400
    assert _rol(pl, chid, it["Espada"]["id"], "zurda").status_code == 400
    # quitarse el rol siempre vale
    _rol(pl, chid, it["Espada"]["id"], "principal")
    assert _rol(pl, chid, it["Espada"]["id"], "").status_code == 200
    assert _items(pl, chid)["Espada"]["rol"] == ""


def test_wielding_equips_and_dropping_unwields(make_client):
    dm, pl, cid, chid = _armado(make_client)
    eid = _items(pl, chid)["Espada"]["id"]
    pl.post(f"/api/characters/{chid}/inventory/{eid}/equip")      # la deja en el suelo
    assert _items(pl, chid)["Espada"]["equipado"] is False
    _rol(pl, chid, eid, "principal")                               # la agarra
    esp = _items(pl, chid)["Espada"]
    assert esp["equipado"] is True and esp["rol"] == "principal"
    pl.post(f"/api/characters/{chid}/inventory/{eid}/equip")      # la vuelve a dejar
    esp = _items(pl, chid)["Espada"]
    assert esp["equipado"] is False and esp["rol"] == ""


def test_storing_or_handing_over_a_weapon_takes_it_out_of_the_hand(make_client):
    dm, pl, cid, chid = _armado(make_client)
    _dar(dm, chid, name="Mochila", contenedor_capacidad=5)
    it = _items(pl, chid)
    _rol(pl, chid, it["Espada"]["id"], "principal")
    _rol(pl, chid, it["Daga"]["id"], "secundaria")

    # a la mochila
    pl.post(f"/api/characters/{chid}/inventory/{it['Espada']['id']}/move",
            json={"parent_id": it["Mochila"]["id"]})
    moch = _items(pl, chid)["Mochila"]
    assert moch["children"][0]["rol"] == ""
    # y no se puede empuñar desde adentro
    assert _rol(pl, chid, it["Espada"]["id"], "principal").status_code == 400

    # al guardado
    pl.post(f"/api/characters/{chid}/inventory/{it['Daga']['id']}/stash", json={"stash": "personal"})
    guard = {i["name"]: i for i in _inv(pl, chid)["character"]["guardado"]}
    assert guard["Daga"]["rol"] == ""


# ── Objetos con cargas en la pestaña de combate ────────────

def test_charged_items_show_in_combat_until_the_player_hides_them(make_client):
    dm, pl, cid, chid = _armado(make_client)
    eid = _items(pl, chid)["Antiséptico"]["id"]
    assert _items(pl, chid)["Antiséptico"]["en_combate"] is True     # por defecto se ve

    r = pl.post(f"/api/characters/{chid}/inventory/{eid}/combate").json()
    assert r["en_combate"] is False
    assert _items(pl, chid)["Antiséptico"]["en_combate"] is False

    r = pl.post(f"/api/characters/{chid}/inventory/{eid}/combate").json()
    assert r["en_combate"] is True
