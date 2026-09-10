"""Equipo de combate: arma principal y secundaria (y las de dos manos),
armadura puesta, expertise en el objeto y qué objetos con cargas se ven en la
pestaña de combate."""

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


def _ficha(pl, cid, chid, **sheet):
    r = pl.put(f"/api/characters/{chid}", json={
        "name": "Kal", "campaign_id": cid, "vida_max": 20, "focus_max": 4, "inv_max": 0,
        "sheet": sheet})
    assert r.status_code == 200, r.text


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


def test_everything_carried_counts_for_capacity(make_client):
    dm, pl, cid, chid = party(make_client)
    _dar(dm, chid, name="Placa completa", slots=6)
    assert _inv(pl, chid)["character"]["capacity"]["usado"] == 6
    # ya no hay tick de "equipado": el endpoint no existe
    eid = _items(pl, chid)["Placa completa"]["id"]
    assert pl.post(f"/api/characters/{chid}/inventory/{eid}/equip").status_code == 404


# ── Dos manos ──────────────────────────────────────────────

def test_a_two_handed_weapon_takes_both_hands(make_client):
    dm, pl, cid, chid = _armado(make_client)
    _dar(dm, chid, name="Espadón", kind="arma",
         stats={"weapon_class": "heavy", "damage": "2d6", "traits": ["Two-Handed"]})
    it = _items(pl, chid)
    # no puede ser la secundaria
    r = _rol(pl, chid, it["Espadón"]["id"], "secundaria")
    assert r.status_code == 400 and "dos manos" in r.json()["detail"]
    # como principal, desaloja la secundaria que había
    _rol(pl, chid, it["Daga"]["id"], "secundaria")
    _rol(pl, chid, it["Espadón"]["id"], "principal")
    it = _items(pl, chid)
    assert it["Espadón"]["rol"] == "principal" and it["Daga"]["rol"] == ""
    # y mientras está en la mano no entra ninguna secundaria
    r = _rol(pl, chid, it["Daga"]["id"], "secundaria")
    assert r.status_code == 400 and "Espadón" in r.json()["detail"]
    # con una principal de una mano, la secundaria vuelve a valer
    _rol(pl, chid, it["Espada"]["id"], "principal")
    assert _rol(pl, chid, it["Daga"]["id"], "secundaria").status_code == 200


def test_expertise_can_remove_the_two_handed_trait(make_client):
    dm, pl, cid, chid = _armado(make_client)
    _dar(dm, chid, name="Espadón", kind="arma",
         stats={"weapon_class": "heavy", "damage": "2d6", "traits": ["Two-Handed"],
                "expert_traits": ["Unique: pierde el rasgo Two-Handed"]})
    eid = _items(pl, chid)["Espadón"]["id"]
    assert _rol(pl, chid, eid, "secundaria").status_code == 400
    # con Espadón entre las especialidades de la ficha, pasa a ser de una mano
    _ficha(pl, cid, chid, expertise="Cultura, Espadón")
    esp = _items(pl, chid)["Espadón"]
    assert esp["expertise"] is True and esp["rasgos"] == []      # Two-Handed se fue
    assert _rol(pl, chid, eid, "secundaria").status_code == 200


# ── Expertise: deducida de la ficha o marcada a mano ───────

def test_expertise_is_deduced_from_the_sheet_and_can_be_overridden(make_client):
    dm, pl, cid, chid = _armado(make_client)
    it = _items(pl, chid)
    assert it["Daga"]["expertise"] is False and it["Daga"]["rasgos"] == []

    _ficha(pl, cid, chid, expertise="Daga, Idiomas")
    it = _items(pl, chid)
    assert it["Daga"]["expertise"] is True and it["Daga"]["rasgos"] == ["Offhand"]
    assert it["Espada"]["expertise"] is False

    # el jugador la niega a mano
    r = pl.post(f"/api/characters/{chid}/inventory/{it['Daga']['id']}/experto", json={"experto": "no"})
    assert r.status_code == 200
    assert _items(pl, chid)["Daga"]["expertise"] is False
    # y se la da a la espada aunque la ficha no la tenga
    pl.post(f"/api/characters/{chid}/inventory/{it['Espada']['id']}/experto", json={"experto": "si"})
    assert _items(pl, chid)["Espada"]["expertise"] is True
    # vuelve a automático
    pl.post(f"/api/characters/{chid}/inventory/{it['Daga']['id']}/experto", json={"experto": ""})
    assert _items(pl, chid)["Daga"]["expertise"] is True

    # solo armas y armaduras
    r = pl.post(f"/api/characters/{chid}/inventory/{it['Antiséptico']['id']}/experto", json={"experto": "si"})
    assert r.status_code == 400


def test_expertise_matches_the_name_inside_a_longer_one(make_client):
    dm, pl, cid, chid = party(make_client)
    _dar(dm, chid, name="Espada larga", kind="arma", stats={"weapon_class": "heavy"})
    _dar(dm, chid, name="Cota de malla", kind="armadura", stats={"deflect": 2})
    _ficha(pl, cid, chid, expertise="Espada, Cota de Malla")
    it = _items(pl, chid)
    assert it["Espada larga"]["expertise"] is True
    assert it["Cota de malla"]["expertise"] is True


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
