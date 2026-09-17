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


# ── Expertise de equipo: una lista en la ficha ─────────────
# Vive en la ficha y no en cada objeto, así que se puede marcar un arma que el
# personaje todavía no tiene. Ver `EQUIPO_KEY` en app/routers/characters.py.

def _exp(pl, chid):
    r = pl.get(f"/api/characters/{chid}/expertise")
    assert r.status_code == 200, r.text
    return r.json()


def _exp_set(pl, chid, lista):
    r = pl.put(f"/api/characters/{chid}/expertise", json={"lista": lista})
    assert r.status_code == 200, r.text
    return r.json()["lista"]


def _exp_toggle(pl, chid, name, on):
    r = pl.post(f"/api/characters/{chid}/expertise", json={"name": name, "on": on})
    assert r.status_code == 200, r.text
    return r.json()["lista"]


def test_the_sheet_list_decides_the_expertise(make_client):
    dm, pl, cid, chid = _armado(make_client)
    assert _items(pl, chid)["Daga"]["expertise"] is False

    _exp_set(pl, chid, ["Daga"])
    it = _items(pl, chid)
    assert it["Daga"]["expertise"] is True and it["Daga"]["rasgos"] == ["Offhand"]
    assert it["Espada"]["expertise"] is False

    # y sacarla de la lista la apaga
    _exp_set(pl, chid, [])
    assert _items(pl, chid)["Daga"]["expertise"] is False


def test_expertise_survives_not_owning_the_weapon(make_client):
    """Lo que motiva todo esto: marcar el arma antes de tenerla."""
    dm, pl, cid, chid = party(make_client)
    _exp_set(pl, chid, ["Espadón"])
    assert _exp(pl, chid)["lista"] == ["Espadón"]

    # más tarde le llega el arma, y ya viene con la expertise puesta
    _dar(dm, chid, name="Espadón", kind="arma",
         stats={"weapon_class": "heavy", "expert_traits": ["Offhand"]})
    esp = _items(pl, chid)["Espadón"]
    assert esp["expertise"] is True and esp["rasgos"] == ["Offhand"]


def test_the_list_still_matches_a_name_inside_a_longer_one(make_client):
    dm, pl, cid, chid = party(make_client)
    _dar(dm, chid, name="Espada larga", kind="arma", stats={"weapon_class": "heavy"})
    _dar(dm, chid, name="Cota de malla", kind="armadura", stats={"deflect": 2})
    _exp_set(pl, chid, ["Espada", "Cota de Malla"])
    it = _items(pl, chid)
    assert it["Espada larga"]["expertise"] is True
    assert it["Cota de malla"]["expertise"] is True


def test_turning_it_off_from_the_item_removes_what_was_granting_it(make_client):
    """El cartel del inventario apaga la expertise de esa arma: si la estaba
    dando una entrada más corta ("Espada" para la "Espada larga"), esa es la
    que tiene que salir, o el botón no haría nada."""
    dm, pl, cid, chid = party(make_client)
    _dar(dm, chid, name="Espada larga", kind="arma", stats={"weapon_class": "heavy"})
    _exp_set(pl, chid, ["Espada", "Daga"])
    assert _items(pl, chid)["Espada larga"]["expertise"] is True

    lista = _exp_toggle(pl, chid, "Espada larga", False)
    assert lista == ["Daga"]                       # se fue "Espada", quedó el resto
    assert _items(pl, chid)["Espada larga"]["expertise"] is False

    # y prenderla la suma de nuevo, sin duplicar si ya estaba cubierta
    _exp_toggle(pl, chid, "Espada larga", True)
    assert _items(pl, chid)["Espada larga"]["expertise"] is True
    assert _exp_toggle(pl, chid, "Espada larga", True) == ["Daga", "Espada larga"]


def test_an_old_sheet_keeps_deducing_until_it_gets_a_list(make_client):
    """Una ficha que nunca pasó por el editor nuevo no tiene lista: sigue
    deduciendo del texto de especialidades y de la marca por objeto, y al
    materializarse conserva exactamente lo que ya daba por experto."""
    dm, pl, cid, chid = _armado(make_client)
    _ficha(pl, cid, chid, expertise="Daga, Idiomas")
    assert _exp(pl, chid)["explicita"] is False
    assert _items(pl, chid)["Daga"]["expertise"] is True

    # lo que estaba deducido aparece ya marcado entre las opciones
    o = _exp(pl, chid)
    assert o["lista"] == ["Daga"]
    assert [x["name"] for x in o["opciones"] if x["marcada"]] == ["Daga"]

    # al tocar cualquier cosa, la lista queda escrita y manda ella
    _exp_toggle(pl, chid, "Espada", True)
    o = _exp(pl, chid)
    assert o["explicita"] is True and sorted(o["lista"]) == ["Daga", "Espada"]
    it = _items(pl, chid)
    assert it["Daga"]["expertise"] is True and it["Espada"]["expertise"] is True


def test_the_options_come_from_the_catalog_and_the_inventory(make_client):
    dm, pl, cid, chid = party(make_client)
    for it in (
        {"name": "Estoque", "kind": "arma", "stats": {"damage": "1d6 keen"}},
        {"name": "Coraza", "kind": "armadura", "stats": {"deflect": 3}},
        {"name": "Estoque secreto", "kind": "arma", "secreto": True},
        {"name": "Cuerda", "kind": "equipo"},          # no es arma ni armadura
    ):
        assert dm.post(f"/api/campaigns/{cid}/items", json=it).status_code == 200
    _dar(dm, chid, name="Maza", kind="arma", stats={"damage": "1d8"})

    o = _exp(pl, chid)
    porNombre = {x["name"]: x for x in o["opciones"]}
    assert porNombre["Estoque"]["origen"] == "catalogo"
    assert porNombre["Coraza"]["kind"] == "armadura"
    assert porNombre["Maza"]["origen"] == "inventario"
    assert "Cuerda" not in porNombre                   # solo armas y armaduras
    assert "Estoque secreto" not in porNombre          # lo que el DM esconde, no

    # una marcada a mano que no está en ningún lado igual aparece en la lista
    _exp_set(pl, chid, ["Hoja esquirlada"])
    o = _exp(pl, chid)
    assert [x["name"] for x in o["opciones"] if x["marcada"]] == ["Hoja esquirlada"]


def test_the_list_is_deduplicated_and_kept_tidy(make_client):
    dm, pl, cid, chid = party(make_client)
    assert _exp_set(pl, chid, ["  Daga ", "daga", "", "Cota"]) == ["Cota", "Daga"]


def test_nobody_else_touches_your_expertise(make_client):
    from helpers import make_user
    dm, pl, cid, chid = party(make_client)
    fuera = make_user(make_client, "colado", "player")
    assert fuera.get(f"/api/characters/{chid}/expertise").status_code == 404
    assert fuera.put(f"/api/characters/{chid}/expertise",
                     json={"lista": ["Daga"]}).status_code == 404
    # el DM de la campaña sí (edita la ficha de los suyos)
    assert dm.put(f"/api/characters/{chid}/expertise",
                  json={"lista": ["Daga"]}).status_code == 200


def test_the_old_per_item_mark_refuses_once_the_sheet_has_its_list(make_client):
    """Marcar por objeto no haría nada sobre una ficha con lista: se rechaza en
    vez de guardar algo que después nadie mira."""
    dm, pl, cid, chid = _armado(make_client)
    eid = _items(pl, chid)["Daga"]["id"]
    # sin lista todavía, la marca vieja sigue valiendo
    assert pl.post(f"/api/characters/{chid}/inventory/{eid}/experto",
                   json={"experto": "si"}).status_code == 200
    assert _items(pl, chid)["Daga"]["expertise"] is True

    _exp_set(pl, chid, [])                      # la ficha estrena su lista
    assert _items(pl, chid)["Daga"]["expertise"] is False
    r = pl.post(f"/api/characters/{chid}/inventory/{eid}/experto", json={"experto": "si"})
    assert r.status_code == 409 and "ficha" in r.json()["detail"]
