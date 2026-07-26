"""Tiendas y asentamientos: generación por tamaño, precios, visibilidad, restock."""

from helpers import create_campaign, make_user, party

# Catálogo variado: barato, medio y carísimo, en varias categorías.
CATALOGO = """
name: "Cuerda"
categories: [generales]
price: 4
---
name: "Racion"
categories: [comida]
price: 2
---
name: "Martillo"
categories: [herramientas]
price: 10
---
name: "Espada larga"
categories: [armas]
price: 25
---
name: "Cota de malla"
categories: [armaduras]
price: 150
slots: 4
---
name: "Placa completa"
categories: [armaduras]
price: 400
slots: 6
---
name: "Hoja Esquirlada"
categories: [armas, lujo]
price: 100000
slots: 0
"""


def _dm_con_catalogo(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm, "C", "cosmere")
    dm.post(f"/api/campaigns/{cid}/items/import-bulk", json={"code": CATALOGO})
    return dm, cid


def _shop(dm, cid, **kw):
    body = {"name": "Tienda", "preset": "general", "size": "mediana",
            "price_policy": "normal"}
    body.update(kw)
    r = dm.post(f"/api/campaigns/{cid}/shops", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_small_shop_respects_price_cap(make_client):
    dm, cid = _dm_con_catalogo(make_client)
    # una herrería chica: tope 50 -> nada de cota (150) ni placa (400)
    _shop(dm, cid, name="Herrería del pueblo", preset="herreria", size="chica")
    stock = dm.get(f"/api/campaigns/{cid}/shops").json()[0]["items"]
    assert stock, "la herrería chica debería tener algo"
    assert all(i["precio"] <= 50 for i in stock), [i["name"] for i in stock]
    assert "Placa completa" not in [i["name"] for i in stock]
    # y solo vende cosas de su rubro
    assert all(set(i["categorias"]) & {"armas", "armaduras", "herramientas", "materiales"}
               for i in stock)


def test_large_shop_can_stock_expensive(make_client):
    dm, cid = _dm_con_catalogo(make_client)
    _shop(dm, cid, name="Gran herrería", preset="herreria", size="grande")
    names = [i["name"] for i in dm.get(f"/api/campaigns/{cid}/shops").json()[0]["items"]]
    assert "Placa completa" in names      # sin tope de precio


def test_price_policies(make_client):
    dm, cid = _dm_con_catalogo(make_client)
    _shop(dm, cid, name="Cara", preset="general", size="grande", price_policy="caro")
    shop = dm.get(f"/api/campaigns/{cid}/shops").json()[0]
    assert all(i["price_tier"] == "caro" for i in shop["items"])
    # el precio congelado es 1.5x el de catálogo
    cat = {i["name"]: i["precio"] for i in dm.get(f"/api/campaigns/{cid}/items").json()}
    for i in shop["items"]:
        assert i["precio"] == max(1, round(cat[i["name"]] * 1.5))


def test_variado_mixes_tiers(make_client):
    dm, cid = _dm_con_catalogo(make_client)
    _shop(dm, cid, name="Variada", preset="general", size="grande", price_policy="variado")
    tiers = {i["price_tier"] for i in dm.get(f"/api/campaigns/{cid}/shops").json()[0]["items"]}
    assert tiers <= {"barato", "normal", "caro"} and len(tiers) >= 1


def test_secret_items_never_generated(make_client):
    dm, cid = _dm_con_catalogo(make_client)
    ids = {i["name"]: i["id"] for i in dm.get(f"/api/campaigns/{cid}/items").json()}
    dm.post(f"/api/campaigns/{cid}/items/{ids['Hoja Esquirlada']}/secret")
    _shop(dm, cid, name="Todo", preset="general", size="grande")
    _shop(dm, cid, name="Armas", preset="herreria", size="grande")
    for shop in dm.get(f"/api/campaigns/{cid}/shops").json():
        assert "Hoja Esquirlada" not in [i["name"] for i in shop["items"]]


def test_hidden_stock_not_visible_to_player(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/campaigns/{cid}/items/import-bulk", json={"code": CATALOGO})
    _shop(dm, cid, name="General", preset="general", size="grande")
    dm_stock = dm.get(f"/api/campaigns/{cid}/shops").json()[0]["items"]
    ocultos = [i for i in dm_stock if not i["visible"]]
    assert ocultos, "parte del stock debería arrancar en la trastienda"
    pl_stock = pl.get(f"/api/campaigns/{cid}/shops").json()[0]["items"]
    assert len(pl_stock) == len(dm_stock) - len(ocultos)
    assert all(i["visible"] for i in pl_stock)
    # el DM lo muestra y el jugador lo empieza a ver
    siid = ocultos[0]["id"]
    sid = dm_stock[0]["shop_id"]
    assert dm.post(f"/api/campaigns/{cid}/shops/{sid}/items/{siid}/visible").json()["visible"] is True
    assert len(pl.get(f"/api/campaigns/{cid}/shops").json()[0]["items"]) == len(pl_stock) + 1


def test_dm_adds_and_removes_stock(make_client):
    dm, cid = _dm_con_catalogo(make_client)
    sid = _shop(dm, cid, name="General", preset="general", size="chica")["id"]
    ids = {i["name"]: i["id"] for i in dm.get(f"/api/campaigns/{cid}/items").json()}
    # agrega a mano incluso algo fuera del rubro y del tope
    dm.post(f"/api/campaigns/{cid}/shops/{sid}/items",
            json={"item_id": ids["Placa completa"], "cantidad": 1, "precio": 350})
    stock = dm.get(f"/api/campaigns/{cid}/shops").json()[0]["items"]
    placa = next(i for i in stock if i["name"] == "Placa completa")
    assert placa["precio"] == 350
    dm.delete(f"/api/campaigns/{cid}/shops/{sid}/items/{placa['id']}")
    assert "Placa completa" not in [
        i["name"] for i in dm.get(f"/api/campaigns/{cid}/shops").json()[0]["items"]]


def test_restock_uses_days_passed(make_client):
    dm, cid = _dm_con_catalogo(make_client)
    sid = _shop(dm, cid, name="General", preset="general", size="grande")["id"]
    assert dm.get(f"/api/campaigns/{cid}/shops").json()[0]["dias_sin_restock"] == 0
    for _ in range(4):
        dm.post(f"/api/campaigns/{cid}/storm/advance")
    assert dm.get(f"/api/campaigns/{cid}/shops").json()[0]["dias_sin_restock"] == 4
    r = dm.post(f"/api/campaigns/{cid}/shops/{sid}/restock").json()
    assert r["dias"] == 4 and r["salieron"] >= 1
    assert dm.get(f"/api/campaigns/{cid}/shops").json()[0]["dias_sin_restock"] == 0


def test_day_count_survives_storm_reset(make_client):
    """El contador de días del restock no se reinicia con la tormenta."""
    dm, cid = _dm_con_catalogo(make_client)
    dm.put(f"/api/campaigns/{cid}/config", json={"storm_day": 0, "storm_target": 2})
    sid = _shop(dm, cid, name="General", preset="general", size="mediana")["id"]
    for _ in range(3):                      # cae la tormenta en el medio
        dm.post(f"/api/campaigns/{cid}/storm/advance")
    assert dm.get(f"/api/campaigns/{cid}/storm").json()["day"] < 3   # el ciclo se reinició
    assert dm.get(f"/api/campaigns/{cid}/shops").json()[0]["dias_sin_restock"] == 3


# ── Asentamientos ──────────────────────────────────────────

def test_settlement_generates_shops_by_size(make_client):
    dm, cid = _dm_con_catalogo(make_client)
    r = dm.post(f"/api/campaigns/{cid}/settlements",
                json={"name": "Villa Chica", "size": "aldea"}).json()
    assert r["tiendas"] == 2
    st = dm.get(f"/api/campaigns/{cid}/settlements").json()[0]
    assert st["size_label"] == "Aldea" and len(st["shops"]) == 2
    assert {s["preset"] for s in st["shops"]} == {"general", "granja"}
    assert all(s["size"] == "chica" for s in st["shops"])

    dm.post(f"/api/campaigns/{cid}/settlements", json={"name": "Kholinar", "size": "ciudad"})
    ciudad = [s for s in dm.get(f"/api/campaigns/{cid}/settlements").json()
              if s["name"] == "Kholinar"][0]
    assert len(ciudad["shops"]) == 8 and all(s["size"] == "grande" for s in ciudad["shops"])


def test_add_shop_to_existing_settlement(make_client):
    dm, cid = _dm_con_catalogo(make_client)
    stid = dm.post(f"/api/campaigns/{cid}/settlements",
                   json={"name": "Villa", "size": "aldea"}).json()["id"]
    # sumarle una herrería a la aldea
    r = dm.post(f"/api/campaigns/{cid}/settlements/{stid}/shops",
                json={"name": "", "preset": "herreria", "size": "chica",
                      "price_policy": "normal"}).json()
    assert r["name"] == "Herrería de Villa"
    st = dm.get(f"/api/campaigns/{cid}/settlements").json()[0]
    assert len(st["shops"]) == 3 and "herreria" in {s["preset"] for s in st["shops"]}


def test_settlement_catalog_and_restock(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/campaigns/{cid}/items/import-bulk", json={"code": CATALOGO})
    stid = dm.post(f"/api/campaigns/{cid}/settlements",
                   json={"name": "Pueblo", "size": "pueblo"}).json()["id"]
    cat = dm.get(f"/api/campaigns/{cid}/settlements/{stid}/catalog").json()
    assert len(cat["shops"]) == 4 and cat["total"] > 0
    # el jugador ve el catálogo del pueblo pero solo lo expuesto
    pcat = pl.get(f"/api/campaigns/{cid}/settlements/{stid}/catalog").json()
    assert pcat["total"] <= cat["total"]
    assert all(i["visible"] for s in pcat["shops"] for i in s["items"])
    r = dm.post(f"/api/campaigns/{cid}/settlements/{stid}/restock").json()
    assert r["tiendas"] == 4


# ── Pedidos: el jugador pide, el DM confirma ───────────────

def _tienda_con_jugador(make_client, marcos_cargados=100, opacos=0):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/campaigns/{cid}/items/import-bulk", json={"code": CATALOGO})
    sid = _shop(dm, cid, name="General", preset="general", size="grande")["id"]
    pl.post(f"/api/characters/{chid}/marcos/set",
            json={"cargados": marcos_cargados, "opacos": opacos})
    visible = [i for i in pl.get(f"/api/campaigns/{cid}/shops").json()[0]["items"]]
    return dm, pl, cid, chid, sid, visible


def _marcos(pl, cid):
    ch = next(m["character"] for m in pl.get(f"/api/campaigns/{cid}/roster").json()["members"])
    return ch["marcos"], ch["marcos_light"]


def test_purchase_flow_approve(make_client):
    dm, pl, cid, chid, sid, visible = _tienda_con_jugador(make_client)
    art = visible[0]
    r = pl.post(f"/api/campaigns/{cid}/shops/{sid}/request",
                json={"shop_item_id": art["id"], "cantidad": 1}).json()
    total = r["total"]
    # queda pendiente: todavía no se cobró ni se entregó
    assert _marcos(pl, cid) == (100, 100)
    assert pl.get(f"/api/characters/{chid}/inventory").json()["character"]["items"] == []
    pend = dm.get(f"/api/campaigns/{cid}/requests").json()
    assert len(pend) == 1 and pend[0]["status"] == "pending" and pend[0]["total"] == total

    assert dm.post(f"/api/campaigns/{cid}/requests/{pend[0]['id']}/approve").status_code == 200
    # cobrado, entregado y descontado del stock
    assert _marcos(pl, cid) == (100 - total, 100 - total)
    inv = pl.get(f"/api/characters/{chid}/inventory").json()["character"]["items"]
    assert [i["name"] for i in inv] == [art["name"]]
    assert dm.get(f"/api/campaigns/{cid}/requests").json()[0]["status"] == "approved"


def test_purchase_spends_dun_spheres_first(make_client):
    """Se pagan primero los opacos: uno se queda con la luz que pueda."""
    dm, pl, cid, chid, sid, visible = _tienda_con_jugador(make_client, marcos_cargados=5, opacos=20)
    art = min(visible, key=lambda i: i["precio"])
    total = art["precio"]
    rid = pl.post(f"/api/campaigns/{cid}/shops/{sid}/request",
                  json={"shop_item_id": art["id"]}).json()["id"]
    dm.post(f"/api/campaigns/{cid}/requests/{rid}/approve")
    marcos, light = _marcos(pl, cid)
    assert marcos == 25 - total and light == 5      # la luz quedó intacta


def test_purchase_rejected_if_not_enough_marcos(make_client):
    dm, pl, cid, chid, sid, visible = _tienda_con_jugador(make_client, marcos_cargados=1)
    art = max(visible, key=lambda i: i["precio"])
    rid = pl.post(f"/api/campaigns/{cid}/shops/{sid}/request",
                  json={"shop_item_id": art["id"]}).json()["id"]
    r = dm.post(f"/api/campaigns/{cid}/requests/{rid}/approve")
    assert r.status_code == 400 and "alcanzan" in r.json()["detail"]
    assert _marcos(pl, cid) == (1, 1)               # no se cobró nada
    assert pl.get(f"/api/characters/{chid}/inventory").json()["character"]["items"] == []


def test_player_cannot_request_hidden_item(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/campaigns/{cid}/items/import-bulk", json={"code": CATALOGO})
    sid = _shop(dm, cid, name="General", preset="general", size="grande")["id"]
    oculto = [i for i in dm.get(f"/api/campaigns/{cid}/shops").json()[0]["items"]
              if not i["visible"]][0]
    r = pl.post(f"/api/campaigns/{cid}/shops/{sid}/request", json={"shop_item_id": oculto["id"]})
    assert r.status_code == 404


def test_reject_request(make_client):
    dm, pl, cid, chid, sid, visible = _tienda_con_jugador(make_client)
    rid = pl.post(f"/api/campaigns/{cid}/shops/{sid}/request",
                  json={"shop_item_id": visible[0]["id"]}).json()["id"]
    dm.post(f"/api/campaigns/{cid}/requests/{rid}/reject")
    assert dm.get(f"/api/campaigns/{cid}/requests").json()[0]["status"] == "rejected"
    assert _marcos(pl, cid) == (100, 100)
    # y no se puede aprobar después de rechazado
    assert dm.post(f"/api/campaigns/{cid}/requests/{rid}/approve").status_code == 400


def test_player_only_sees_own_requests(make_client):
    dm, pl, cid, chid, sid, visible = _tienda_con_jugador(make_client)
    pl.post(f"/api/campaigns/{cid}/shops/{sid}/request", json={"shop_item_id": visible[0]["id"]})
    assert len(pl.get(f"/api/campaigns/{cid}/requests").json()) == 1
    assert pl.post(f"/api/campaigns/{cid}/requests/1/approve").status_code == 403


def test_shops_rejected_in_dnd(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm, "DnD", "dnd")
    assert dm.get(f"/api/campaigns/{cid}/shops").status_code == 400
    assert dm.post(f"/api/campaigns/{cid}/settlements",
                   json={"name": "X", "size": "aldea"}).status_code == 400
