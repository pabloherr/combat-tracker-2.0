"""Agarrar del catálogo, pagar con las esferas que uno quiera, los dos guardados
(el propio y el del grupo) y pasarse objetos entre jugadores."""

from helpers import (SAMPLE_STATBLOCK, create_character, get_enemies,
                     import_enemy, invite, make_user, party)


def _inv(cli, chid):
    return cli.get(f"/api/characters/{chid}/inventory").json()


def _marcos(cli, cid):
    """Marcos del personaje del jugador, leídos del roster."""
    m = cli.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]
    return m["marcos"], m["marcos_light"]


def _catalogo(dm, cid, **kw):
    body = {"name": "Cuerda", "precio": 10, "slots": 1}
    body.update(kw)
    dm.post(f"/api/campaigns/{cid}/items", json=body)
    return dm.get(f"/api/campaigns/{cid}/items").json()[-1]["id"]


def _mesa(make_client):
    """DM + dos jugadores con personaje en la misma campaña."""
    dm, pl, cid, chid = party(make_client)
    otro = make_user(make_client, "pl2", "player")
    invite(dm, cid, "pl2")
    chid2 = create_character(otro, cid, "Shallan")
    return dm, pl, chid, otro, chid2, cid


# ── Agarrar del catálogo y pagarlo ─────────────────────────

def test_take_pays_with_the_chosen_spheres(make_client):
    dm, pl, cid, chid = party(make_client)
    iid = _catalogo(dm, cid, precio=10)
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 8, "opacos": 7})

    r = pl.post(f"/api/characters/{chid}/inventory/take",
                json={"item_id": iid, "pago_cargados": 4, "pago_opacos": 6})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 10 and r.json()["pago"]["cargados"] == 4
    assert _marcos(pl, cid) == (5, 4)          # 15-10 en total, 8-4 con luz
    assert [i["name"] for i in _inv(pl, chid)["character"]["items"]] == ["Cuerda"]


def test_take_defaults_to_spending_dun_spheres_first(make_client):
    dm, pl, cid, chid = party(make_client)
    iid = _catalogo(dm, cid, precio=6)
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 5, "opacos": 4})
    pago = pl.post(f"/api/characters/{chid}/inventory/take",
                   json={"item_id": iid}).json()["pago"]
    assert pago == {"cargados": 2, "opacos": 4, "marcos": 3, "marcos_light": 3}


def test_take_rejects_a_split_that_does_not_add_up(make_client):
    dm, pl, cid, chid = party(make_client)
    iid = _catalogo(dm, cid, precio=10)
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 20, "opacos": 0})
    r = pl.post(f"/api/characters/{chid}/inventory/take",
                json={"item_id": iid, "pago_cargados": 3, "pago_opacos": 3})
    assert r.status_code == 400 and "10" in r.json()["detail"]
    assert _marcos(pl, cid) == (20, 20)        # no se le tocó nada


def test_take_rejects_spheres_it_does_not_have(make_client):
    dm, pl, cid, chid = party(make_client)
    iid = _catalogo(dm, cid, precio=5)
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 1, "opacos": 4})
    r = pl.post(f"/api/characters/{chid}/inventory/take",
                json={"item_id": iid, "pago_cargados": 5, "pago_opacos": 0})
    assert r.status_code == 400
    assert _inv(pl, chid)["character"]["items"] == []


def test_found_item_costs_nothing(make_client):
    """Precio 0: un hallazgo o un regalo del DM. No se le va ninguna esfera."""
    dm, pl, cid, chid = party(make_client)
    iid = _catalogo(dm, cid, precio=500)
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 2, "opacos": 0})
    r = pl.post(f"/api/characters/{chid}/inventory/take", json={"item_id": iid, "precio": 0})
    assert r.status_code == 200 and r.json()["total"] == 0
    assert _marcos(pl, cid) == (2, 2)
    assert len(_inv(pl, chid)["character"]["items"]) == 1


def test_discounted_price_is_what_gets_charged(make_client):
    dm, pl, cid, chid = party(make_client)
    iid = _catalogo(dm, cid, precio=100)
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 0, "opacos": 60})
    r = pl.post(f"/api/characters/{chid}/inventory/take",
                json={"item_id": iid, "precio": 25, "cantidad": 2})
    assert r.status_code == 200 and r.json()["total"] == 50
    assert _marcos(pl, cid) == (10, 0)


def test_hidden_items_cannot_be_taken(make_client):
    dm, pl, cid, chid = party(make_client)
    iid = _catalogo(dm, cid, precio=1)
    dm.post(f"/api/campaigns/{cid}/items/{iid}/secret")
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 50, "opacos": 0})
    assert pl.post(f"/api/characters/{chid}/inventory/take",
                   json={"item_id": iid}).status_code == 404
    # el DM sí puede dárselo
    assert dm.post(f"/api/characters/{chid}/inventory/take",
                   json={"item_id": iid, "precio": 0}).status_code == 200


def test_take_straight_into_the_backpack(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={
        "name": "Mochila", "slots": 1, "contenedor_capacidad": 4})
    mochila = _inv(pl, chid)["character"]["items"][0]
    iid = _catalogo(dm, cid, precio=0)
    pl.post(f"/api/characters/{chid}/inventory/take",
            json={"item_id": iid, "parent_id": mochila["id"]})
    inv = _inv(pl, chid)
    assert [c["name"] for c in inv["character"]["items"][0]["children"]] == ["Cuerda"]
    assert inv["character"]["capacity"]["usado"] == 1     # solo la mochila


# ── Guardados: el propio y el del grupo ────────────────────

def test_stash_does_not_count_as_carried_weight(make_client):
    dm, pl, cid, chid = party(make_client)      # capacidad 6
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Yunque", "slots": 5})
    eid = _inv(pl, chid)["character"]["items"][0]["id"]
    assert _inv(pl, chid)["character"]["capacity"]["usado"] == 5

    pl.post(f"/api/characters/{chid}/inventory/{eid}/stash", json={"stash": "personal"})
    inv = _inv(pl, chid)
    assert inv["character"]["items"] == []                # ya no lo lleva
    assert inv["character"]["capacity"]["usado"] == 0
    assert [i["name"] for i in inv["character"]["guardado"]] == ["Yunque"]

    pl.post(f"/api/characters/{chid}/inventory/{eid}/stash", json={"stash": ""})
    inv = _inv(pl, chid)
    assert [i["name"] for i in inv["character"]["items"]] == ["Yunque"]
    assert inv["character"]["capacity"]["usado"] == 5


def test_group_stash_is_shared_between_players(make_client):
    dm, pl, chid, otro, chid2, cid = _mesa(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Tienda de campaña", "slots": 3})
    eid = _inv(pl, chid)["character"]["items"][0]["id"]
    pl.post(f"/api/characters/{chid}/inventory/{eid}/stash", json={"stash": "grupo"})

    # el otro jugador lo ve en el guardado compartido...
    assert [i["name"] for i in _inv(otro, chid2)["grupo"]] == ["Tienda de campaña"]
    # ...y puede llevárselo
    otro.post(f"/api/characters/{chid2}/inventory/{eid}/stash", json={"stash": ""})
    assert [i["name"] for i in _inv(otro, chid2)["character"]["items"]] == ["Tienda de campaña"]
    assert _inv(pl, chid)["grupo"] == []
    assert _inv(pl, chid)["character"]["items"] == []


def test_stashing_a_container_takes_its_contents_along(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={
        "name": "Mochila", "slots": 1, "contenedor_capacidad": 4})
    mochila = _inv(pl, chid)["character"]["items"][0]
    for n in ("Cuerda", "Vendas"):
        dm.post(f"/api/characters/{chid}/inventory",
                json={"name": n, "slots": 1, "parent_id": mochila["id"]})

    pl.post(f"/api/characters/{chid}/inventory/{mochila['id']}/stash",
            json={"stash": "grupo"})
    inv = _inv(pl, chid)
    assert inv["character"]["items"] == []
    assert len(inv["grupo"]) == 1                        # la mochila, con todo adentro
    assert sorted(c["name"] for c in inv["grupo"][0]["children"]) == ["Cuerda", "Vendas"]


def test_take_into_each_stash(make_client):
    dm, pl, cid, chid = party(make_client)
    iid = _catalogo(dm, cid, precio=0)
    pl.post(f"/api/characters/{chid}/inventory/take", json={"item_id": iid, "stash": "personal"})
    pl.post(f"/api/characters/{chid}/inventory/take", json={"item_id": iid, "stash": "grupo"})
    inv = _inv(pl, chid)
    assert inv["character"]["items"] == []
    assert len(inv["character"]["guardado"]) == 1 and len(inv["grupo"]) == 1


# ── Pasarse objetos ────────────────────────────────────────

def test_pass_an_item_to_another_player(make_client):
    dm, pl, chid, otro, chid2, cid = _mesa(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Lanza", "slots": 1})
    eid = _inv(pl, chid)["character"]["items"][0]["id"]
    assert [a["name"] for a in _inv(pl, chid)["aliados"]] == ["Shallan"]

    r = pl.post(f"/api/characters/{chid}/inventory/{eid}/transfer",
                json={"character_id": chid2})
    assert r.status_code == 200 and r.json()["a"] == "Shallan"
    assert _inv(pl, chid)["character"]["items"] == []
    assert [i["name"] for i in _inv(otro, chid2)["character"]["items"]] == ["Lanza"]


def test_passing_a_container_moves_what_is_inside(make_client):
    dm, pl, chid, otro, chid2, cid = _mesa(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={
        "name": "Mochila", "slots": 1, "contenedor_capacidad": 4})
    mochila = _inv(pl, chid)["character"]["items"][0]
    dm.post(f"/api/characters/{chid}/inventory",
            json={"name": "Cuerda", "slots": 1, "parent_id": mochila["id"]})

    pl.post(f"/api/characters/{chid}/inventory/{mochila['id']}/transfer",
            json={"character_id": chid2})
    items = _inv(otro, chid2)["character"]["items"]
    assert len(items) == 1 and [c["name"] for c in items[0]["children"]] == ["Cuerda"]
    assert _inv(pl, chid)["character"]["items"] == []


def test_pass_to_your_own_pet(make_client):
    dm, pl, cid, chid = party(make_client)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    dm.post(f"/api/campaigns/{cid}/pet-options/{eid}")
    pid = pl.post(f"/api/characters/{chid}/pets/from-enemy", json={"enemy_id": eid}).json()["id"]
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Barril", "slots": 4})
    item = _inv(pl, chid)["character"]["items"][0]

    pl.post(f"/api/characters/{chid}/inventory/{item['id']}/transfer", json={"pet_id": pid})
    inv = _inv(pl, chid)
    assert inv["character"]["items"] == [] and inv["character"]["capacity"]["usado"] == 0
    assert [i["name"] for i in inv["pets"][0]["items"]] == ["Barril"]


def test_cannot_pass_to_someone_outside_the_campaign(make_client):
    dm, pl, cid, chid = party(make_client)
    dm2 = make_user(make_client, "dm2", "dm")
    ajeno = make_user(make_client, "pl3", "player")
    cid2 = dm2.post("/api/campaigns", json={"name": "Otra", "system": "cosmere"}).json()["id"]
    invite(dm2, cid2, "pl3")
    otro_chid = create_character(ajeno, cid2, "Ajeno")
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Lanza", "slots": 1})
    eid = _inv(pl, chid)["character"]["items"][0]["id"]

    r = pl.post(f"/api/characters/{chid}/inventory/{eid}/transfer",
                json={"character_id": otro_chid})
    assert r.status_code == 404
    assert [i["name"] for i in _inv(pl, chid)["character"]["items"]] == ["Lanza"]


# ── Una sola mochila encima ────────────────────────────────

def _mochila(cli, chid, **kw):
    body = {"name": "Mochila", "slots": 1, "contenedor_capacidad": 4}
    body.update(kw)
    return cli.post(f"/api/characters/{chid}/inventory", json=body)


def test_only_one_container_carried_at_a_time(make_client):
    """Dos mochilas encima serían espacio infinito gratis."""
    dm, pl, cid, chid = party(make_client)
    assert _mochila(dm, chid).status_code == 200
    r = _mochila(dm, chid, name="Otra mochila")
    assert r.status_code == 400 and "Mochila" in r.json()["detail"]
    assert len(_inv(pl, chid)["character"]["items"]) == 1


def test_a_second_container_can_go_to_a_stash(make_client):
    """Tener otra guardada sí: lo que no se puede es llevar dos encima."""
    dm, pl, cid, chid = party(make_client)
    _mochila(dm, chid)
    iid = _catalogo(dm, cid, name="Mochila de repuesto", precio=0,
                    contenedor_capacidad=4)
    r = pl.post(f"/api/characters/{chid}/inventory/take",
                json={"item_id": iid, "stash": "personal"})
    assert r.status_code == 200
    # y sacarla del guardado choca con la que ya lleva
    eid = _inv(pl, chid)["character"]["guardado"][0]["id"]
    r = pl.post(f"/api/characters/{chid}/inventory/{eid}/stash", json={"stash": ""})
    assert r.status_code == 400


def test_swapping_backpacks_works(make_client):
    """Guardar la que llevaba libera el lugar para la otra."""
    dm, pl, cid, chid = party(make_client)
    _mochila(dm, chid)
    vieja = _inv(pl, chid)["character"]["items"][0]["id"]
    iid = _catalogo(dm, cid, name="Mochila nueva", precio=0, contenedor_capacidad=6)
    pl.post(f"/api/characters/{chid}/inventory/take", json={"item_id": iid, "stash": "personal"})
    nueva = _inv(pl, chid)["character"]["guardado"][0]["id"]

    pl.post(f"/api/characters/{chid}/inventory/{vieja}/stash", json={"stash": "personal"})
    assert pl.post(f"/api/characters/{chid}/inventory/{nueva}/stash",
                   json={"stash": ""}).status_code == 200
    inv = _inv(pl, chid)
    assert [i["name"] for i in inv["character"]["items"]] == ["Mochila nueva"]


def test_the_pet_has_its_own_container_slot(make_client):
    """La carreta del chull no compite con la mochila del personaje."""
    dm, pl, cid, chid = party(make_client)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    dm.post(f"/api/campaigns/{cid}/pet-options/{eid}")
    pid = pl.post(f"/api/characters/{chid}/pets/from-enemy", json={"enemy_id": eid}).json()["id"]
    _mochila(dm, chid)
    assert dm.post(f"/api/characters/{chid}/pets/{pid}/inventory",
                   json={"name": "Carreta", "slots": 1,
                         "contenedor_capacidad": 20}).status_code == 200
    # pero una segunda carreta no
    assert dm.post(f"/api/characters/{chid}/pets/{pid}/inventory",
                   json={"name": "Otra carreta", "slots": 1,
                         "contenedor_capacidad": 20}).status_code == 400


def test_cannot_pass_a_backpack_to_someone_who_already_carries_one(make_client):
    dm, pl, chid, otro, chid2, cid = _mesa(make_client)
    _mochila(dm, chid)
    _mochila(dm, chid2, name="Mochila de Shallan")
    eid = _inv(pl, chid)["character"]["items"][0]["id"]
    r = pl.post(f"/api/characters/{chid}/inventory/{eid}/transfer",
                json={"character_id": chid2})
    assert r.status_code == 400
    # pero sí a su guardado
    assert pl.post(f"/api/characters/{chid}/inventory/{eid}/transfer",
                   json={"character_id": chid2, "stash": "personal"}).status_code == 200


# ── Dosis y cargas: gastar y reponer ───────────────────────

def test_uses_go_down_and_back_up(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory",
            json={"name": "Raciones (5 dias)", "slots": 1, "usos_max": 5})
    eid = _inv(pl, chid)["character"]["items"][0]["id"]
    url = f"/api/characters/{chid}/inventory/{eid}/use"

    assert pl.post(url, json={"delta": -2}).json()["usos"] == 3   # comió dos días
    assert pl.post(url, json={"delta": 1}).json()["usos"] == 4    # repuso una
    assert pl.post(url, json={"delta": 99}).json()["usos"] == 5   # no pasa del máximo


# ── El DM ve y edita todos los inventarios ─────────────────

def test_dm_sees_and_edits_every_inventory(make_client):
    dm, pl, chid, otro, chid2, cid = _mesa(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Lanza", "slots": 1})
    dm.post(f"/api/characters/{chid2}/inventory", json={"name": "Pincel", "slots": 1})
    eid = _inv(pl, chid)["character"]["items"][0]["id"]
    pl.post(f"/api/characters/{chid}/inventory/{eid}/stash", json={"stash": "grupo"})

    data = dm.get(f"/api/campaigns/{cid}/inventories").json()
    porNombre = {c["character"]["name"]: c for c in data["characters"]}
    assert sorted(porNombre) == ["Kal", "Shallan"]
    assert porNombre["Kal"]["character"]["items"] == []
    assert [i["name"] for i in porNombre["Shallan"]["character"]["items"]] == ["Pincel"]
    assert [i["name"] for i in data["grupo"]] == ["Lanza"]

    # y puede sacarle cosas a cualquiera
    otro_eid = porNombre["Shallan"]["character"]["items"][0]["id"]
    assert dm.delete(f"/api/characters/{chid2}/inventory/{otro_eid}").status_code == 200
    assert _inv(otro, chid2)["character"]["items"] == []


def test_players_cannot_list_every_inventory(make_client):
    dm, pl, cid, chid = party(make_client)
    assert pl.get(f"/api/campaigns/{cid}/inventories").status_code == 403
