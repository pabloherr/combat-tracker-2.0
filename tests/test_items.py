"""Catálogo de objetos: alta, import/bulk, export round-trip, secretos y compendio."""

from helpers import create_campaign, make_user, party

SAMPLE = """
name: "Espada larga"
categories: [armas]
price: 25
slots: 1
description: "Arma de filo."
"""

BULK = """
name: "Mochila"
categories: [generales]
price: 5
slots: 1
capacity_bonus: 2
---
name: "Placa completa"
categories: [armaduras]
price: 400
slots: 6
---
name: "Alabarda"
categories: [armas]
price: 30
cumbersome: 2
"""


def _dm_campaign(make_client):
    dm = make_user(make_client, "dm", "dm")
    return dm, create_campaign(dm, "C", "cosmere")


def test_create_and_list(make_client):
    dm, cid = _dm_campaign(make_client)
    dm.post(f"/api/campaigns/{cid}/items", json={
        "name": "Cuerda", "categorias": ["generales"], "precio": 3, "slots": 1})
    items = dm.get(f"/api/campaigns/{cid}/items").json()
    assert len(items) == 1 and items[0]["name"] == "Cuerda"
    assert items[0]["categorias"] == ["generales"] and items[0]["precio"] == 3


def test_import_and_bulk_with_cumbersome(make_client):
    dm, cid = _dm_campaign(make_client)
    dm.post(f"/api/campaigns/{cid}/items/import", json={"code": SAMPLE})
    r = dm.post(f"/api/campaigns/{cid}/items/import-bulk", json={"code": BULK}).json()
    assert r["added"] == 3 and not r["errors"]
    items = {i["name"]: i for i in dm.get(f"/api/campaigns/{cid}/items").json()}
    assert items["Mochila"]["capacity_bonus"] == 2      # mochila: +2 de capacidad
    assert items["Placa completa"]["slots"] == 6
    assert items["Alabarda"]["slots"] == 3              # cumbersome 2 -> 1+2 slots


def test_export_roundtrip(make_client):
    dm, cid = _dm_campaign(make_client)
    dm.post(f"/api/campaigns/{cid}/items/import-bulk", json={"code": BULK})
    exp = dm.get(f"/api/campaigns/{cid}/items/export")
    assert exp.status_code == 200 and "attachment" in exp.headers.get("content-disposition", "")

    dm2 = make_user(make_client, "dm2", "dm")
    cid2 = create_campaign(dm2, "C2", "cosmere")
    r = dm2.post(f"/api/campaigns/{cid2}/items/import-bulk", json={"code": exp.text}).json()
    assert r["added"] == 3 and not r["errors"]
    back = {i["name"]: i for i in dm2.get(f"/api/campaigns/{cid2}/items").json()}
    assert back["Mochila"]["capacity_bonus"] == 2 and back["Mochila"]["precio"] == 5
    assert back["Placa completa"]["slots"] == 6


def test_player_catalog_hides_price_and_secrets(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/campaigns/{cid}/items", json={"name": "Cuerda", "precio": 3})
    dm.post(f"/api/campaigns/{cid}/items", json={"name": "Hoja Esquirlada", "precio": 99999})
    ids = {i["name"]: i["id"] for i in dm.get(f"/api/campaigns/{cid}/items").json()}
    # el DM marca la hoja como secreta
    r = dm.post(f"/api/campaigns/{cid}/items/{ids['Hoja Esquirlada']}/secret").json()
    assert r["secreto"] is True

    cat = pl.get(f"/api/campaigns/{cid}/catalog").json()
    assert [c["name"] for c in cat] == ["Cuerda"]      # el secreto no aparece
    assert "precio" not in cat[0]                       # y sin precios
    # el DM sí ve todo, con precio
    dcat = dm.get(f"/api/campaigns/{cid}/catalog").json()
    assert len(dcat) == 2 and "precio" in dcat[0]


def test_items_rejected_in_dnd_campaign(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm, "DnD", "dnd")
    assert dm.get(f"/api/campaigns/{cid}/items").status_code == 400
    assert dm.post(f"/api/campaigns/{cid}/items", json={"name": "X"}).status_code == 400


def test_player_cannot_touch_catalog(make_client):
    dm, pl, cid, chid = party(make_client)
    assert pl.get(f"/api/campaigns/{cid}/items").status_code == 403
    assert pl.post(f"/api/campaigns/{cid}/items", json={"name": "X"}).status_code == 403


# ── Tipos de objeto (cada uno con sus stats) ───────────────

POR_TIPO = """
kind: arma
weapon_class: light
name: "Javelin"
damage: "1d6 keen"
range: "Melee"
traits: "Thrown [30/120]"
expert_traits: "Indirect"
weight: "2 lb."
price: 20
---
kind: arma
weapon_class: special
name: "Shardblade"
skill: "Heavy Weaponry"
damage: "2d8 spirit"
range: "Melee"
traits: "Dangerous, Deadly, Unique"
weight: "4 lb."
price: 0
secret: true
notes: "Solo como recompensa."
---
kind: armadura
name: "Full Plate"
deflect: 4
traits: "Cumbersome [5]"
weight: "55 lb."
slots: 6
price: 1600
---
kind: alojamiento
name: "Comfortable"
price: 4
---
kind: vehiculo
name: "Chull cart"
vehicle_type: "Land"
speed: "2 mph"
rental: 5
price: 500
container: 20
---
kind: fabrial
name: "Alerter"
charges: 5
weight: "0.5-10 lb."
price: 500
---
kind: equipo
name: "Raciones (5 dias)"
categories: [comida]
price: 1
uses: 5
---
kind: equipo
name: "Backpack"
categories: [generales, contenedores]
price: 8
container: 2
"""


def test_kinds_and_type_stats(make_client):
    dm, cid = _dm_campaign(make_client)
    r = dm.post(f"/api/campaigns/{cid}/items/import-bulk", json={"code": POR_TIPO}).json()
    assert r["added"] == 8 and not r["errors"], r["errors"]
    it = {i["name"]: i for i in dm.get(f"/api/campaigns/{cid}/items").json()}

    jav = it["Javelin"]
    assert jav["kind"] == "arma" and jav["stats"]["weapon_class"] == "light"
    assert jav["stats"]["damage"] == "1d6 keen" and jav["stats"]["range"] == "Melee"
    # el rasgo con corchetes no se parte por la coma interna
    assert jav["stats"]["traits"] == ["Thrown [30/120]"]
    assert jav["stats"]["expert_traits"] == ["Indirect"] and jav["peso"] == "2 lb."

    sh = it["Shardblade"]
    assert sh["stats"]["skill"] == "Heavy Weaponry" and sh["secreto"] is True
    assert sh["stats"]["traits"] == ["Dangerous", "Deadly", "Unique"]

    assert it["Full Plate"]["kind"] == "armadura"
    assert it["Full Plate"]["stats"]["deflect"] == 4 and it["Full Plate"]["slots"] == 6
    assert it["Comfortable"]["stats"]["per_night"] == 4
    assert it["Chull cart"]["stats"] == {"vehicle_type": "Land", "speed": "2 mph", "rental": 5}
    assert it["Chull cart"]["contenedor"] is True and it["Chull cart"]["contenedor_capacidad"] == 20
    assert it["Alerter"]["stats"]["charges"] == 5 and it["Alerter"]["usos_max"] == 5
    assert it["Raciones (5 dias)"]["usos_max"] == 5 and it["Raciones (5 dias)"]["slots"] == 1
    assert it["Backpack"]["contenedor_capacidad"] == 2


def test_kind_roundtrip_export(make_client):
    dm, cid = _dm_campaign(make_client)
    dm.post(f"/api/campaigns/{cid}/items/import-bulk", json={"code": POR_TIPO})
    exp = dm.get(f"/api/campaigns/{cid}/items/export").text
    dm2 = make_user(make_client, "dm2", "dm")
    cid2 = create_campaign(dm2, "C2", "cosmere")
    r = dm2.post(f"/api/campaigns/{cid2}/items/import-bulk", json={"code": exp}).json()
    assert r["added"] == 8 and not r["errors"]
    it = {i["name"]: i for i in dm2.get(f"/api/campaigns/{cid2}/items").json()}
    assert it["Javelin"]["stats"]["traits"] == ["Thrown [30/120]"]
    assert it["Full Plate"]["stats"]["deflect"] == 4
    assert it["Chull cart"]["contenedor_capacidad"] == 20
    assert it["Raciones (5 dias)"]["usos_max"] == 5
    assert it["Shardblade"]["secreto"] is True


def test_unknown_kind_rejected(make_client):
    dm, cid = _dm_campaign(make_client)
    r = dm.post(f"/api/campaigns/{cid}/items/import",
                json={"code": 'kind: nave\nname: "X"'})
    assert r.status_code == 400 and "desconocido" in r.json()["detail"]


def test_update_and_delete(make_client):
    dm, cid = _dm_campaign(make_client)
    dm.post(f"/api/campaigns/{cid}/items", json={"name": "Cuerda", "precio": 3})
    iid = dm.get(f"/api/campaigns/{cid}/items").json()[0]["id"]
    dm.put(f"/api/campaigns/{cid}/items/{iid}", json={
        "name": "Cuerda de seda", "precio": 12, "slots": 1, "categorias": ["Generales"]})
    it = dm.get(f"/api/campaigns/{cid}/items").json()[0]
    assert it["name"] == "Cuerda de seda" and it["precio"] == 12
    assert it["categorias"] == ["generales"]        # se normaliza a minúsculas
    dm.delete(f"/api/campaigns/{cid}/items/{iid}")
    assert dm.get(f"/api/campaigns/{cid}/items").json() == []
