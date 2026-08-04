"""Inventario, capacidad de carga, entrega del DM y semilla del PDF."""

import io

import pypdf
from helpers import (SAMPLE_STATBLOCK, create_campaign, create_character,
                     get_enemies, import_enemy, invite, make_user, party)


def _members(dm, cid):
    return dm.get(f"/api/campaigns/{cid}/members").json()


def test_capacity_formula_and_backpack(make_client):
    dm, pl, cid, chid = party(make_client)
    # STR 4 en la ficha -> Mediano (6) + 4 = 10 de capacidad
    pl.put(f"/api/characters/{chid}", json={
        "name": "Kal", "campaign_id": cid, "vida_max": 20, "focus_max": 10, "inv_max": 0,
        "sheet": {"attributes": {"STR": 4}}})
    inv = pl.get(f"/api/characters/{chid}/inventory").json()
    assert inv["character"]["capacity"] == {
        "capacidad": 10, "usado": 0, "base": 6, "fuerza": 4, "bonus": 0, "sobrecargado": False}

    # el DM le da una mochila (+2 capacidad, 1 slot) y una placa completa (6 slots)
    dm.post(f"/api/characters/{chid}/inventory", json={
        "name": "Mochila", "slots": 1, "capacity_bonus": 2})
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Placa completa", "slots": 6})
    cap = pl.get(f"/api/characters/{chid}/inventory").json()["character"]["capacity"]
    assert cap["capacidad"] == 12 and cap["usado"] == 7 and cap["sobrecargado"] is False


def test_overload_warns_but_does_not_block(make_client):
    dm, pl, cid, chid = party(make_client)   # sin STR -> capacidad 6
    for i in range(8):
        r = dm.post(f"/api/characters/{chid}/inventory", json={"name": f"Piedra {i}", "slots": 1})
        assert r.status_code == 200          # nunca bloquea
    cap = pl.get(f"/api/characters/{chid}/inventory").json()["character"]["capacity"]
    assert cap["usado"] == 8 and cap["capacidad"] == 6 and cap["sobrecargado"] is True


def test_size_changes_capacity(make_client):
    dm, pl, cid, chid = party(make_client)
    pl.put(f"/api/characters/{chid}/size", json={"size": "Grande"})
    cap = pl.get(f"/api/characters/{chid}/inventory").json()["character"]["capacity"]
    assert cap["base"] == 10


def test_zero_slot_items_dont_count(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Vela", "slots": 0})
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Hoja Esquirlada", "slots": 0})
    cap = pl.get(f"/api/characters/{chid}/inventory").json()["character"]["capacity"]
    assert cap["usado"] == 0


def test_player_needs_permission_to_create(make_client):
    dm, pl, cid, chid = party(make_client)
    # sin permiso -> 403
    r = pl.post(f"/api/characters/{chid}/inventory", json={"name": "Espada casera", "slots": 1})
    assert r.status_code == 403
    uid = _members(dm, cid)[0]["user_id"]
    assert dm.post(f"/api/campaigns/{cid}/members/{uid}/can-create-items").json()["can_create_items"] is True
    # con permiso -> puede
    assert pl.post(f"/api/characters/{chid}/inventory",
                   json={"name": "Espada casera", "slots": 1}).status_code == 200
    assert _members(dm, cid)[0]["can_create_items"] is True


def test_player_cannot_get_catalog_items_for_free(make_client):
    """Del catálogo se agarra pagando (`/inventory/take`). El alta directa con
    `item_id` es del DM: sería sacar cosas gratis."""
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/campaigns/{cid}/items", json={"name": "Hoja Esquirlada", "precio": 99999})
    iid = dm.get(f"/api/campaigns/{cid}/items").json()[0]["id"]
    uid = _members(dm, cid)[0]["user_id"]
    dm.post(f"/api/campaigns/{cid}/members/{uid}/can-create-items")   # aun con permiso
    r = pl.post(f"/api/characters/{chid}/inventory", json={"item_id": iid})
    assert r.status_code == 403
    # el DM sí puede entregárselo
    assert dm.post(f"/api/characters/{chid}/inventory", json={"item_id": iid}).status_code == 200
    inv = pl.get(f"/api/characters/{chid}/inventory").json()
    assert [i["name"] for i in inv["character"]["items"]] == ["Hoja Esquirlada"]


def test_dm_creates_item_on_the_fly_and_saves_to_catalog(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={
        "name": "Amuleto raro", "descripcion": "brilla", "slots": 1, "save_to_catalog": True})
    assert [i["name"] for i in dm.get(f"/api/campaigns/{cid}/items").json()] == ["Amuleto raro"]
    inv = pl.get(f"/api/characters/{chid}/inventory").json()
    assert inv["character"]["items"][0]["name"] == "Amuleto raro"


def test_pet_inventory_and_capacity(make_client):
    dm, pl, cid, chid = party(make_client)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)           # Axehound: str 3, Medium
    eid = get_enemies(dm, cid)[0]["id"]
    dm.post(f"/api/campaigns/{cid}/pet-options/{eid}")
    pid = pl.post(f"/api/characters/{chid}/pets/from-enemy",
                  json={"enemy_id": eid}).json()["id"]
    dm.post(f"/api/characters/{chid}/pets/{pid}/inventory", json={"name": "Alforja", "slots": 1})
    pet = pl.get(f"/api/characters/{chid}/inventory").json()["pets"][0]
    assert pet["capacity"]["base"] == 6 and pet["capacity"]["fuerza"] == 3   # Medium + str 3
    assert pet["capacity"]["capacidad"] == 9 and pet["capacity"]["usado"] == 1


def test_qty_and_delete(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Flechas", "slots": 1, "cantidad": 3})
    eid = pl.get(f"/api/characters/{chid}/inventory").json()["character"]["items"][0]["id"]
    pl.post(f"/api/characters/{chid}/inventory/{eid}/qty", json={"delta": -1})
    items = pl.get(f"/api/characters/{chid}/inventory").json()["character"]["items"]
    assert items[0]["cantidad"] == 2
    pl.delete(f"/api/characters/{chid}/inventory/{eid}")
    assert pl.get(f"/api/characters/{chid}/inventory").json()["character"]["items"] == []


# ── Contenedores (mochila, carreta) y dosis/cargas ─────────

def _inv(cli, chid):
    return cli.get(f"/api/characters/{chid}/inventory").json()


def test_backpack_is_separate_storage(make_client):
    """La mochila no suma capacidad al bolsillo: guarda aparte. Lo que va adentro
    ocupa su espacio, no el del personaje."""
    dm, pl, cid, chid = party(make_client)          # capacidad base 6 (sin STR)
    dm.post(f"/api/characters/{chid}/inventory", json={
        "name": "Mochila", "slots": 1, "contenedor_capacidad": 2})
    inv = _inv(pl, chid)
    mochila = inv["character"]["items"][0]
    assert mochila["contenedor"] is True and mochila["contenedor_capacidad"] == 2
    assert inv["character"]["capacity"]["usado"] == 1     # la mochila misma

    # dos cosas adentro: no cambian la carga del personaje
    for n in ("Cuerda", "Vendas"):
        dm.post(f"/api/characters/{chid}/inventory",
                json={"name": n, "slots": 1, "parent_id": mochila["id"]})
    inv = _inv(pl, chid)
    mochila = inv["character"]["items"][0]
    assert len(mochila["children"]) == 2
    assert mochila["cont_usado"] == 2 and mochila["cont_lleno"] is False
    assert inv["character"]["capacity"]["usado"] == 1     # sigue siendo 1

    # una tercera la desborda (se avisa, no se bloquea)
    dm.post(f"/api/characters/{chid}/inventory",
            json={"name": "Pala", "slots": 1, "parent_id": mochila["id"]})
    mochila = _inv(pl, chid)["character"]["items"][0]
    assert mochila["cont_usado"] == 3 and mochila["cont_lleno"] is True


def test_unequip_frees_capacity(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Placa completa", "slots": 6})
    eid = _inv(pl, chid)["character"]["items"][0]["id"]
    assert _inv(pl, chid)["character"]["capacity"]["usado"] == 6
    r = pl.post(f"/api/characters/{chid}/inventory/{eid}/equip").json()
    assert r["equipado"] is False
    assert _inv(pl, chid)["character"]["capacity"]["usado"] == 0   # la dejó en el suelo
    pl.post(f"/api/characters/{chid}/inventory/{eid}/equip")
    assert _inv(pl, chid)["character"]["capacity"]["usado"] == 6


def test_move_in_and_out_of_container(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Mochila", "slots": 1,
                                                       "contenedor_capacidad": 2})
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Cuerda", "slots": 1})
    items = _inv(pl, chid)["character"]["items"]
    mochila = next(i for i in items if i["name"] == "Mochila")
    cuerda = next(i for i in items if i["name"] == "Cuerda")
    assert _inv(pl, chid)["character"]["capacity"]["usado"] == 2

    pl.post(f"/api/characters/{chid}/inventory/{cuerda['id']}/move",
            json={"parent_id": mochila["id"]})
    assert _inv(pl, chid)["character"]["capacity"]["usado"] == 1   # la cuerda va adentro
    pl.post(f"/api/characters/{chid}/inventory/{cuerda['id']}/move", json={"parent_id": None})
    assert _inv(pl, chid)["character"]["capacity"]["usado"] == 2   # y vuelve a la mano

    # no se puede meter un contenedor dentro de otro ni en sí mismo
    assert pl.post(f"/api/characters/{chid}/inventory/{mochila['id']}/move",
                   json={"parent_id": mochila["id"]}).status_code == 400


def test_load_the_chull_cart(make_client):
    """Cargar la carreta: lo que llevaba el personaje pasa a la mascota."""
    dm, pl, cid, chid = party(make_client)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    dm.post(f"/api/campaigns/{cid}/pet-options/{eid}")
    pid = pl.post(f"/api/characters/{chid}/pets/from-enemy", json={"enemy_id": eid}).json()["id"]
    dm.post(f"/api/characters/{chid}/pets/{pid}/inventory",
            json={"name": "Carreta", "slots": 1, "contenedor_capacidad": 20})
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Barril", "slots": 4})
    carreta = _inv(pl, chid)["pets"][0]["items"][0]
    barril = _inv(pl, chid)["character"]["items"][0]
    assert _inv(pl, chid)["character"]["capacity"]["usado"] == 4

    pl.post(f"/api/characters/{chid}/inventory/{barril['id']}/move",
            json={"parent_id": carreta["id"]})
    inv = _inv(pl, chid)
    assert inv["character"]["items"] == []                 # ya no lo lleva encima
    assert inv["character"]["capacity"]["usado"] == 0
    assert inv["pets"][0]["items"][0]["cont_usado"] == 4    # va en la carreta

    # y bajarlo lo devuelve a las manos del personaje
    pl.post(f"/api/characters/{chid}/inventory/{barril['id']}/move", json={"parent_id": None})
    inv = _inv(pl, chid)
    assert [i["name"] for i in inv["character"]["items"]] == ["Barril"]
    assert inv["pets"][0]["items"][0]["cont_usado"] == 0


def test_cart_expands_pet_storage(make_client):
    """Al chull se le engancha una carreta: es un contenedor de la mascota."""
    dm, pl, cid, chid = party(make_client)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    dm.post(f"/api/campaigns/{cid}/pet-options/{eid}")
    pid = pl.post(f"/api/characters/{chid}/pets/from-enemy", json={"enemy_id": eid}).json()["id"]
    dm.post(f"/api/characters/{chid}/pets/{pid}/inventory",
            json={"name": "Carreta", "slots": 1, "contenedor_capacidad": 20})
    carreta = _inv(pl, chid)["pets"][0]["items"][0]
    assert carreta["contenedor_capacidad"] == 20
    for i in range(5):
        dm.post(f"/api/characters/{chid}/pets/{pid}/inventory",
                json={"name": f"Barril {i}", "slots": 2, "parent_id": carreta["id"]})
    pet = _inv(pl, chid)["pets"][0]
    assert pet["items"][0]["cont_usado"] == 10 and pet["items"][0]["cont_lleno"] is False
    assert pet["capacity"]["usado"] == 1        # el chull solo carga la carreta


def test_doses_consume_without_extra_slots(make_client):
    """5 raciones ocupan 1 slot hasta que se acaban."""
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory",
            json={"name": "Raciones (5 dias)", "slots": 1, "usos_max": 5})
    inv = _inv(pl, chid)
    it = inv["character"]["items"][0]
    assert it["usos"] == 5 and it["usos_max"] == 5
    assert inv["character"]["capacity"]["usado"] == 1     # 1 slot, no 5

    for esperado in (4, 3, 2, 1):
        r = pl.post(f"/api/characters/{chid}/inventory/{it['id']}/use", json={"delta": -1}).json()
        assert r["usos"] == esperado and r["agotado"] is False
        assert _inv(pl, chid)["character"]["capacity"]["usado"] == 1   # sigue ocupando 1
    r = pl.post(f"/api/characters/{chid}/inventory/{it['id']}/use", json={"delta": -1}).json()
    assert r["agotado"] is True
    assert _inv(pl, chid)["character"]["items"] == []     # se acabaron


def test_second_unit_starts_when_one_runs_out(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={
        "name": "Antiseptico (5 dosis)", "slots": 1, "usos_max": 5, "cantidad": 2})
    it = _inv(pl, chid)["character"]["items"][0]
    for _ in range(5):
        pl.post(f"/api/characters/{chid}/inventory/{it['id']}/use", json={"delta": -1})
    it2 = _inv(pl, chid)["character"]["items"][0]
    assert it2["cantidad"] == 1 and it2["usos"] == 5      # arrancó el segundo frasco


def test_use_rejected_on_plain_item(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/characters/{chid}/inventory", json={"name": "Cuerda", "slots": 1})
    eid = _inv(pl, chid)["character"]["items"][0]["id"]
    assert pl.post(f"/api/characters/{chid}/inventory/{eid}/use",
                   json={"delta": -1}).status_code == 400


def test_taken_item_keeps_uses_and_container(make_client):
    """Lo que se agarra del catálogo llega con sus dosis y su capacidad."""
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/campaigns/{cid}/items", json={
        "name": "Mochila", "kind": "equipo", "precio": 8, "slots": 1,
        "contenedor_capacidad": 2, "usos_max": 0})
    iid = dm.get(f"/api/campaigns/{cid}/items").json()[0]["id"]
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 50, "opacos": 0})
    assert pl.post(f"/api/characters/{chid}/inventory/take",
                   json={"item_id": iid}).status_code == 200
    it = _inv(pl, chid)["character"]["items"][0]
    assert it["contenedor"] is True and it["contenedor_capacidad"] == 2


def _filled_sheet(cosmere_pdf, vals):
    r = pypdf.PdfReader(io.BytesIO(cosmere_pdf))
    w = pypdf.PdfWriter()
    w.append(r)
    for pg in w.pages:
        w.update_page_form_field_values(pg, vals)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_pdf_seeds_inventory_and_marcos_only_on_create(make_client, cosmere_pdf):
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm, "C", "cosmere")
    invite(dm, cid, "pl")
    pdf = _filled_sheet(cosmere_pdf, {
        "char_name": "Kaladin", "char_health_max": "25", "char_spheres": "17",
        "char_weapons.0": "Lanza", "char_equipment.0": "Mochila",
        "char_equipment.1": "Cuerda"})
    chid = pl.post(f"/api/characters/import-pdf?campaign_id={cid}",
                   files={"file": ("f.pdf", pdf, "application/pdf")}).json()["id"]

    inv = pl.get(f"/api/characters/{chid}/inventory").json()
    assert sorted(i["name"] for i in inv["character"]["items"]) == ["Cuerda", "Lanza", "Mochila"]
    ch = next(m["character"] for m in pl.get(f"/api/campaigns/{cid}/roster").json()["members"])
    assert ch["marcos"] == 17 and ch["marcos_light"] == 17

    # el jugador gasta y reacomoda...
    eid = inv["character"]["items"][0]["id"]
    pl.delete(f"/api/characters/{chid}/inventory/{eid}")
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 2, "opacos": 1})

    # ...y al reimportar el PDF (subir de nivel) NO se le pisa nada
    pl.post(f"/api/characters/{chid}/reimport-pdf",
            files={"file": ("f.pdf", pdf, "application/pdf")})
    inv2 = pl.get(f"/api/characters/{chid}/inventory").json()
    assert len(inv2["character"]["items"]) == 2          # sigue sin el que borró
    ch2 = next(m["character"] for m in pl.get(f"/api/campaigns/{cid}/roster").json()["members"])
    assert ch2["marcos"] == 3 and ch2["marcos_light"] == 2
