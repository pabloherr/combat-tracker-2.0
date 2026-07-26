"""API: Tiendas y asentamientos de una campaña (solo Cosmere).

El DM arma tiendas a partir de su catálogo de objetos. El tamaño de la tienda
decide cuántos objetos tiene y hasta qué precio llega (una herrería chica no
tiene una placa completa), y la política de precios si son caros, baratos o
mezclados. Parte del stock arranca oculto: el DM decide qué muestra.
"""

import json
import random

from fastapi import APIRouter, Depends, HTTPException

from ..access import require_access, require_dm
from ..auth import current_user
from ..database import db
from ..models import PriceIn, PurchaseIn, SettlementIn, ShopIn, ShopItemIn
from .items import _require_cosmere

router = APIRouter(prefix="/api/campaigns/{cid}", tags=["shops"])

# Cada preset sugiere qué categorías de objetos vende.
SHOP_PRESETS = {
    # Ojo: "equipo" es el cajón genérico, así que ningún preset filtra por ese
    # tipo — lo que define a una tienda de equipo es la subcategoría.
    "general":     {"label": "Tienda general", "kinds": [],
                    "categorias": ["generales", "comida", "herramientas", "ropa", "iluminacion", "contenedores"]},
    # "armas"/"armaduras" como categoría cubre catálogos viejos, cargados antes
    # de que los objetos tuvieran tipo propio.
    "herreria":    {"label": "Herrería",       "kinds": ["arma", "armadura"],
                    "categorias": ["herramientas", "materiales", "armas", "armaduras"]},
    "fabrial":     {"label": "Taller fabrial", "kinds": ["fabrial"],
                    "categorias": ["materiales", "lujo"]},
    "carpinteria": {"label": "Carpintería",    "kinds": [],
                    "categorias": ["herramientas", "materiales", "generales"]},
    "granja":      {"label": "Granja",         "kinds": [],
                    "categorias": ["comida", "generales", "animales"]},
    "medica":      {"label": "Médica / herbalista", "kinds": [],
                    "categorias": ["medicina", "alquimia"]},
    "sastre":      {"label": "Sastrería",      "kinds": [],
                    "categorias": ["ropa", "materiales", "lujo"]},
    "taberna":     {"label": "Taberna",        "kinds": ["alojamiento"],
                    "categorias": ["comida"]},
    "joyeria":     {"label": "Joyería",        "kinds": [],
                    "categorias": ["lujo", "materiales"]},
    "alquimista":  {"label": "Alquimista",     "kinds": [],
                    "categorias": ["alquimia", "medicina", "venenos"]},
    "libreria":    {"label": "Librería",       "kinds": [],
                    "categorias": ["libros", "generales", "lujo"]},
    "establo":     {"label": "Establo / carretas", "kinds": ["vehiculo"],
                    "categorias": ["animales"]},
    "armeria":     {"label": "Armería",        "kinds": ["arma"], "categorias": ["armas"]},
}

# El tamaño define cuántos objetos hay y el techo de precio del stock.
SHOP_SIZES = {
    "chica":   {"label": "Pequeña", "count": (4, 8),   "max_price": 50},
    "mediana": {"label": "Mediana", "count": (8, 16),  "max_price": 200},
    "grande":  {"label": "Grande",  "count": (16, 28), "max_price": None},
}

# Política de precios de la tienda (o por objeto, si es "variado").
PRICE_TIERS = {"barato": 0.75, "normal": 1.0, "caro": 1.5}
PRICE_POLICIES = list(PRICE_TIERS) + ["variado"]

# Qué tiendas trae cada asentamiento al crearse, y de qué tamaño.
SETTLEMENT_SIZES = {
    "aldea":  {"label": "Aldea",  "presets": ["general", "granja"], "shop_size": "chica"},
    "pueblo": {"label": "Pueblo", "presets": ["general", "herreria", "medica", "taberna"],
               "shop_size": "mediana"},
    "ciudad": {"label": "Ciudad", "presets": ["general", "herreria", "medica", "taberna",
                                              "fabrial", "joyeria", "alquimista", "sastre"],
               "shop_size": "grande"},
}

_RESTOCK_PER_DAY = 0.15     # fracción del stock que rota por día transcurrido
_HIDDEN_SHARE = 0.25        # parte del stock que arranca en la trastienda


def _hidden_idx(n: int) -> set:
    """Índices que arrancan ocultos: no todo lo que tiene el vendedor está a la
    vista. El DM decide después qué muestra."""
    k = round(n * _HIDDEN_SHARE)
    return set(random.sample(range(n), k)) if n and k else set()


def _day_count(conn, cid: int) -> int:
    r = conn.execute("SELECT day_count FROM campaigns WHERE id=?", (cid,)).fetchone()
    return (r["day_count"] if r else 0) or 0


def _price_for(base: int, policy: str) -> tuple[int, str]:
    tier = random.choice(list(PRICE_TIERS)) if policy == "variado" else policy
    if tier not in PRICE_TIERS:
        tier = "normal"
    return max(1, round(base * PRICE_TIERS[tier])), tier


def _pick_items(conn, dm_id: int, preset: str, max_price, n: int, exclude=()):
    """Objetos del catálogo que puede tener esta tienda: los de su rubro (por
    tipo y/o subcategoría) y dentro del tope de precio del tamaño.

    Los secretos no salen por sorteo (son de trama): el DM los agrega a mano."""
    cfg = SHOP_PRESETS.get(preset) or {}
    kinds, cats = set(cfg.get("kinds") or []), set(cfg.get("categorias") or [])
    sql = "SELECT * FROM items WHERE owner_id=? AND COALESCE(secreto,0)=0"
    params = [dm_id]
    if max_price is not None:
        sql += " AND precio<=?"
        params.append(max_price)
    rows = []
    for r in conn.execute(sql, params):
        if r["id"] in exclude:
            continue
        kind = r["kind"] or "equipo"
        if kinds and kind in kinds:
            rows.append(r)
        elif cats & set(json.loads(r["categorias"] or "[]")):
            rows.append(r)
    random.shuffle(rows)
    return rows[:max(0, n)]


def _add_stock(conn, shop_id: int, item, policy: str, visible=True, cantidad=1, precio=None):
    p, tier = _price_for(item["precio"], policy)
    if precio is not None:
        p = max(0, int(precio))
    conn.execute(
        "INSERT INTO shop_items (shop_id, item_id, cantidad, precio, price_tier, visible) "
        "VALUES (?,?,?,?,?,?)",
        (shop_id, item["id"], max(1, cantidad), p, tier, 1 if visible else 0),
    )


def _generate_stock(conn, shop_id: int, dm_id: int, preset: str, size: str, policy: str):
    cfg = SHOP_SIZES.get(size) or SHOP_SIZES["mediana"]
    n = random.randint(*cfg["count"])
    picked = _pick_items(conn, dm_id, preset, cfg["max_price"], n)
    ocultos = _hidden_idx(len(picked))
    for i, it in enumerate(picked):
        _add_stock(conn, shop_id, it, policy, visible=(i not in ocultos),
                   cantidad=random.randint(1, 3))
    return len(picked)


def _shop_row(conn, cid: int, sid: int):
    s = conn.execute("SELECT * FROM shops WHERE id=? AND campaign_id=?", (sid, cid)).fetchone()
    if not s:
        raise HTTPException(404, "Tienda no encontrada")
    return s


def _stock(conn, shop_id: int, is_dm: bool):
    sql = ("SELECT si.*, i.name, i.descripcion, i.categorias, i.slots, i.capacity_bonus "
           "FROM shop_items si JOIN items i ON i.id=si.item_id WHERE si.shop_id=?")
    if not is_dm:
        # El jugador no ve la trastienda ni lo que se agotó.
        sql += " AND si.visible=1 AND si.cantidad>0"
    out = []
    for r in conn.execute(sql + " ORDER BY i.name", (shop_id,)):
        d = dict(r)
        d["categorias"] = json.loads(d.get("categorias") or "[]")
        d["visible"] = bool(d["visible"])
        out.append(d)
    return out


def _serialize_shop(conn, s, is_dm: bool, day: int) -> dict:
    d = dict(s)
    d["items"] = _stock(conn, s["id"], is_dm)
    d["preset_label"] = (SHOP_PRESETS.get(s["preset"]) or {}).get("label", s["preset"])
    d["size_label"] = (SHOP_SIZES.get(s["size"]) or {}).get("label", s["size"])
    if is_dm:
        d["dias_sin_restock"] = max(0, day - (s["last_restock_day"] or 0))
    return d


# ── Presets (para el formulario del DM) ────────────────────

@router.get("/shop-presets")
def shop_presets(cid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        return {
            "presets": [{"key": k, **v} for k, v in SHOP_PRESETS.items()],
            "sizes": [{"key": k, "label": v["label"], "max_price": v["max_price"]}
                      for k, v in SHOP_SIZES.items()],
            "policies": PRICE_POLICIES,
            "settlement_sizes": [{"key": k, "label": v["label"],
                                  "tiendas": len(v["presets"])}
                                 for k, v in SETTLEMENT_SIZES.items()],
        }


# ── Tiendas ────────────────────────────────────────────────

@router.get("/shops")
def list_shops(cid: int, user=Depends(current_user)):
    """El DM ve todo; los jugadores solo el stock expuesto."""
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
        _require_cosmere(conn, cid)
        day = _day_count(conn, cid)
        rows = conn.execute("SELECT * FROM shops WHERE campaign_id=? ORDER BY name", (cid,)).fetchall()
        return [_serialize_shop(conn, s, is_dm, day) for s in rows]


@router.post("/shops")
def create_shop(cid: int, s: ShopIn, user=Depends(current_user)):
    name = s.name.strip()
    if not name:
        raise HTTPException(400, "Poné un nombre a la tienda")
    preset = s.preset if s.preset in SHOP_PRESETS else "general"
    size = s.size if s.size in SHOP_SIZES else "mediana"
    policy = s.price_policy if s.price_policy in PRICE_POLICIES else "normal"
    with db() as conn:
        c = require_dm(conn, cid, user)
        _require_cosmere(conn, cid)
        day = _day_count(conn, cid)
        cur = conn.execute(
            "INSERT INTO shops (campaign_id, settlement_id, name, preset, size, "
            "price_policy, descripcion, last_restock_day) VALUES (?,?,?,?,?,?,?,?)",
            (cid, s.settlement_id, name, preset, size, policy, s.descripcion, day),
        )
        sid = cur.lastrowid
        added = _generate_stock(conn, sid, c["dm_id"], preset, size, policy)
    return {"id": sid, "name": name, "items": added}


@router.put("/shops/{sid}")
def update_shop(cid: int, sid: int, s: ShopIn, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        _shop_row(conn, cid, sid)
        conn.execute(
            "UPDATE shops SET name=?, preset=?, size=?, price_policy=?, descripcion=?, "
            "settlement_id=? WHERE id=?",
            (s.name.strip() or "Tienda", s.preset, s.size, s.price_policy,
             s.descripcion, s.settlement_id, sid),
        )
    return {"ok": True}


@router.delete("/shops/{sid}")
def delete_shop(cid: int, sid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        conn.execute("DELETE FROM shops WHERE id=? AND campaign_id=?", (sid, cid))
    return {"ok": True}


# ── Stock de una tienda ────────────────────────────────────

@router.post("/shops/{sid}/items")
def add_shop_item(cid: int, sid: int, payload: ShopItemIn, user=Depends(current_user)):
    """El DM suma un objeto del catálogo a una tienda ya creada."""
    with db() as conn:
        c = require_dm(conn, cid, user)
        _shop_row(conn, cid, sid)
        it = conn.execute("SELECT * FROM items WHERE id=? AND owner_id=?",
                          (payload.item_id, c["dm_id"])).fetchone()
        if not it:
            raise HTTPException(404, "Objeto no encontrado en tu catálogo")
        shop = _shop_row(conn, cid, sid)
        _add_stock(conn, sid, it, shop["price_policy"], visible=payload.visible,
                   cantidad=payload.cantidad, precio=payload.precio)
    return {"ok": True}


@router.delete("/shops/{sid}/items/{siid}")
def remove_shop_item(cid: int, sid: int, siid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        _shop_row(conn, cid, sid)
        conn.execute("DELETE FROM shop_items WHERE id=? AND shop_id=?", (siid, sid))
    return {"ok": True}


@router.post("/shops/{sid}/items/{siid}/visible")
def toggle_shop_item(cid: int, sid: int, siid: int, user=Depends(current_user)):
    """Mostrar/ocultar un objeto a los jugadores (la trastienda del vendedor)."""
    with db() as conn:
        require_dm(conn, cid, user)
        _shop_row(conn, cid, sid)
        r = conn.execute("SELECT visible FROM shop_items WHERE id=? AND shop_id=?",
                         (siid, sid)).fetchone()
        if not r:
            raise HTTPException(404, "Ese objeto no está en la tienda")
        val = 0 if r["visible"] else 1
        conn.execute("UPDATE shop_items SET visible=? WHERE id=?", (val, siid))
    return {"ok": True, "visible": bool(val)}


@router.post("/shops/{sid}/items/{siid}/price")
def set_shop_item_price(cid: int, sid: int, siid: int, p: PriceIn, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        _shop_row(conn, cid, sid)
        conn.execute("UPDATE shop_items SET precio=? WHERE id=? AND shop_id=?",
                     (max(0, p.precio), siid, sid))
    return {"ok": True}


def _restock_shop(conn, cid: int, shop, dm_id: int, day: int) -> dict:
    """Rota el stock según los días pasados desde el último restock."""
    dias = max(0, day - (shop["last_restock_day"] or 0))
    frac = min(1.0, max(0.1, dias * _RESTOCK_PER_DAY))
    # lo agotado se limpia siempre
    conn.execute("DELETE FROM shop_items WHERE shop_id=? AND cantidad<=0", (shop["id"],))
    actuales = conn.execute("SELECT * FROM shop_items WHERE shop_id=?", (shop["id"],)).fetchall()
    salen = round(len(actuales) * frac)
    if salen and actuales:
        for r in random.sample(list(actuales), min(salen, len(actuales))):
            conn.execute("DELETE FROM shop_items WHERE id=?", (r["id"],))
    # repone lo que quedó y trae caras nuevas
    quedan = conn.execute("SELECT item_id FROM shop_items WHERE shop_id=?", (shop["id"],)).fetchall()
    for r in quedan:
        conn.execute("UPDATE shop_items SET cantidad=? WHERE shop_id=? AND item_id=?",
                     (random.randint(1, 3), shop["id"], r["item_id"]))
    cfg = SHOP_SIZES.get(shop["size"]) or SHOP_SIZES["mediana"]
    objetivo = random.randint(*cfg["count"])
    faltan = max(0, objetivo - len(quedan))
    nuevos = _pick_items(conn, dm_id, shop["preset"], cfg["max_price"], faltan,
                         exclude={r["item_id"] for r in quedan})
    ocultos = _hidden_idx(len(nuevos))
    for i, it in enumerate(nuevos):
        _add_stock(conn, shop["id"], it, shop["price_policy"], visible=(i not in ocultos),
                   cantidad=random.randint(1, 3))
    conn.execute("UPDATE shops SET last_restock_day=? WHERE id=?", (day, shop["id"]))
    return {"dias": dias, "salieron": salen, "entraron": len(nuevos)}


@router.post("/shops/{sid}/restock")
def restock_shop(cid: int, sid: int, user=Depends(current_user)):
    with db() as conn:
        c = require_dm(conn, cid, user)
        shop = _shop_row(conn, cid, sid)
        res = _restock_shop(conn, cid, shop, c["dm_id"], _day_count(conn, cid))
    return {"ok": True, **res}


# ── Pedidos: el jugador pide, el DM confirma ───────────────

def _my_character(conn, cid: int, user):
    r = conn.execute(
        "SELECT ch.* FROM campaign_members m JOIN characters ch ON ch.id=m.character_id "
        "WHERE m.campaign_id=? AND m.user_id=?", (cid, user["id"])).fetchone()
    if not r:
        raise HTTPException(404, "No tenés personaje en esta campaña")
    return r


def _pay_marcos(conn, ch, total: int):
    """Cobra en marcos: primero los opacos (uno se queda con la luz), después
    los cargados. Devuelve False si no le alcanza."""
    marcos = ch["marcos"] or 0
    light = min(ch["marcos_light"] or 0, marcos)
    if total > marcos:
        return False
    opacos = marcos - light
    de_opacos = min(opacos, total)
    de_cargados = total - de_opacos
    conn.execute("UPDATE characters SET marcos=?, marcos_light=? WHERE id=?",
                 (marcos - total, light - de_cargados, ch["id"]))
    return True


@router.post("/shops/{sid}/request")
def request_purchase(cid: int, sid: int, p: PurchaseIn, user=Depends(current_user)):
    """El jugador pide un objeto; queda pendiente hasta que el DM lo confirme."""
    with db() as conn:
        require_access(conn, cid, user)
        _require_cosmere(conn, cid)
        _shop_row(conn, cid, sid)
        ch = _my_character(conn, cid, user)
        si = conn.execute("SELECT * FROM shop_items WHERE id=? AND shop_id=?",
                          (p.shop_item_id, sid)).fetchone()
        if not si or not si["visible"]:
            raise HTTPException(404, "Ese objeto no está a la venta")
        cant = max(1, p.cantidad)
        if cant > (si["cantidad"] or 0):
            raise HTTPException(400, "No hay tantas unidades")
        rid = conn.execute(
            "INSERT INTO purchase_requests (shop_id, shop_item_id, character_id, cantidad) "
            "VALUES (?,?,?,?)", (sid, si["id"], ch["id"], cant)).lastrowid
    return {"ok": True, "id": rid, "total": si["precio"] * cant}


@router.get("/requests")
def list_requests(cid: int, user=Depends(current_user)):
    """DM: todos los pedidos. Jugador: los suyos (para ver en qué quedaron)."""
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
        sql = ("SELECT r.*, i.name AS item_name, si.precio, si.visible, s.name AS shop_name, "
               "ch.name AS character_name, ch.owner_id, ch.marcos "
               "FROM purchase_requests r "
               "JOIN shop_items si ON si.id=r.shop_item_id "
               "JOIN items i ON i.id=si.item_id "
               "JOIN shops s ON s.id=r.shop_id "
               "JOIN characters ch ON ch.id=r.character_id "
               "WHERE s.campaign_id=?")
        params = [cid]
        if not is_dm:
            sql += " AND ch.owner_id=?"
            params.append(user["id"])
        out = []
        for r in conn.execute(sql + " ORDER BY r.created_at DESC", params):
            d = dict(r)
            d["total"] = (d["precio"] or 0) * (d["cantidad"] or 1)
            out.append(d)
        return out


@router.post("/requests/{rid}/approve")
def approve_request(cid: int, rid: int, user=Depends(current_user)):
    """El DM confirma la venta: cobra los marcos, baja el stock y entrega."""
    with db() as conn:
        require_dm(conn, cid, user)
        r = conn.execute(
            "SELECT r.*, s.campaign_id FROM purchase_requests r "
            "JOIN shops s ON s.id=r.shop_id WHERE r.id=? AND s.campaign_id=?",
            (rid, cid)).fetchone()
        if not r:
            raise HTTPException(404, "Pedido no encontrado")
        if r["status"] != "pending":
            raise HTTPException(400, "Ese pedido ya fue resuelto")
        si = conn.execute("SELECT * FROM shop_items WHERE id=?", (r["shop_item_id"],)).fetchone()
        if not si:
            raise HTTPException(404, "El objeto ya no está en la tienda")
        cant = max(1, r["cantidad"] or 1)
        if cant > (si["cantidad"] or 0):
            raise HTTPException(400, "No queda stock suficiente")
        ch = conn.execute("SELECT * FROM characters WHERE id=?", (r["character_id"],)).fetchone()
        total = (si["precio"] or 0) * cant
        if not _pay_marcos(conn, ch, total):
            raise HTTPException(400, f"No le alcanzan los marcos (necesita {total})")
        it = conn.execute("SELECT * FROM items WHERE id=?", (si["item_id"],)).fetchone()
        conn.execute(
            "INSERT INTO inventory (character_id, item_id, name, kind, descripcion, "
            "categorias, peso, slots, capacity_bonus, usos, usos_max, contenedor, "
            "contenedor_capacidad, stats, cantidad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ch["id"], it["id"], it["name"], it["kind"] or "equipo", it["descripcion"],
             it["categorias"], it["peso"] or "", it["slots"], it["capacity_bonus"] or 0,
             it["usos_max"] or 0, it["usos_max"] or 0, 1 if it["contenedor"] else 0,
             it["contenedor_capacidad"] or 0, it["stats"] or "{}", cant),
        )
        # Queda en 0 ("agotado") en vez de borrarse: así el pedido aprobado no se
        # va con él por la cascada y el DM ve qué se vendió hasta el restock.
        resto = max(0, (si["cantidad"] or 0) - cant)
        conn.execute("UPDATE shop_items SET cantidad=? WHERE id=?", (resto, si["id"]))
        conn.execute("UPDATE purchase_requests SET status='approved' WHERE id=?", (rid,))
    return {"ok": True, "total": total}


@router.post("/requests/{rid}/reject")
def reject_request(cid: int, rid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        conn.execute(
            "UPDATE purchase_requests SET status='rejected' WHERE id=? AND status='pending' "
            "AND shop_id IN (SELECT id FROM shops WHERE campaign_id=?)", (rid, cid))
    return {"ok": True}


# ── Asentamientos (aldea | pueblo | ciudad) ────────────────

def _settlement_row(conn, cid: int, stid: int):
    s = conn.execute("SELECT * FROM settlements WHERE id=? AND campaign_id=?",
                     (stid, cid)).fetchone()
    if not s:
        raise HTTPException(404, "Asentamiento no encontrado")
    return s


@router.get("/settlements")
def list_settlements(cid: int, user=Depends(current_user)):
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
        _require_cosmere(conn, cid)
        day = _day_count(conn, cid)
        out = []
        for s in conn.execute("SELECT * FROM settlements WHERE campaign_id=? ORDER BY name", (cid,)):
            d = dict(s)
            d["size_label"] = (SETTLEMENT_SIZES.get(s["size"]) or {}).get("label", s["size"])
            shops = conn.execute("SELECT * FROM shops WHERE settlement_id=? ORDER BY name",
                                 (s["id"],)).fetchall()
            d["shops"] = [_serialize_shop(conn, sh, is_dm, day) for sh in shops]
            out.append(d)
        return out


@router.post("/settlements")
def create_settlement(cid: int, s: SettlementIn, user=Depends(current_user)):
    """Crea el asentamiento y genera sus tiendas según el tamaño."""
    name = s.name.strip()
    if not name:
        raise HTTPException(400, "Poné un nombre al asentamiento")
    size = s.size if s.size in SETTLEMENT_SIZES else "pueblo"
    cfg = SETTLEMENT_SIZES[size]
    with db() as conn:
        c = require_dm(conn, cid, user)
        _require_cosmere(conn, cid)
        day = _day_count(conn, cid)
        stid = conn.execute(
            "INSERT INTO settlements (campaign_id, name, size, descripcion) VALUES (?,?,?,?)",
            (cid, name, size, s.descripcion),
        ).lastrowid
        for preset in cfg["presets"]:
            label = SHOP_PRESETS[preset]["label"]
            policy = random.choice(["barato", "normal", "normal", "caro", "variado"])
            sid = conn.execute(
                "INSERT INTO shops (campaign_id, settlement_id, name, preset, size, "
                "price_policy, last_restock_day) VALUES (?,?,?,?,?,?,?)",
                (cid, stid, f"{label} de {name}", preset, cfg["shop_size"], policy, day),
            ).lastrowid
            _generate_stock(conn, sid, c["dm_id"], preset, cfg["shop_size"], policy)
    return {"id": stid, "name": name, "tiendas": len(cfg["presets"])}


@router.post("/settlements/{stid}/shops")
def add_settlement_shop(cid: int, stid: int, s: ShopIn, user=Depends(current_user)):
    """Suma una tienda nueva a un asentamiento ya creado (p. ej. una herrería
    en una aldea que no tenía)."""
    with db() as conn:
        c = require_dm(conn, cid, user)
        st = _settlement_row(conn, cid, stid)
        preset = s.preset if s.preset in SHOP_PRESETS else "general"
        size = s.size if s.size in SHOP_SIZES else \
            (SETTLEMENT_SIZES.get(st["size"]) or {}).get("shop_size", "mediana")
        policy = s.price_policy if s.price_policy in PRICE_POLICIES else "normal"
        name = s.name.strip() or f'{SHOP_PRESETS[preset]["label"]} de {st["name"]}'
        day = _day_count(conn, cid)
        sid = conn.execute(
            "INSERT INTO shops (campaign_id, settlement_id, name, preset, size, "
            "price_policy, descripcion, last_restock_day) VALUES (?,?,?,?,?,?,?,?)",
            (cid, stid, name, preset, size, policy, s.descripcion, day),
        ).lastrowid
        added = _generate_stock(conn, sid, c["dm_id"], preset, size, policy)
    return {"id": sid, "name": name, "items": added}


@router.put("/settlements/{stid}")
def update_settlement(cid: int, stid: int, s: SettlementIn, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        _settlement_row(conn, cid, stid)
        conn.execute("UPDATE settlements SET name=?, size=?, descripcion=? WHERE id=?",
                     (s.name.strip() or "Asentamiento", s.size, s.descripcion, stid))
    return {"ok": True}


@router.delete("/settlements/{stid}")
def delete_settlement(cid: int, stid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        conn.execute("DELETE FROM settlements WHERE id=? AND campaign_id=?", (stid, cid))
    return {"ok": True}


@router.post("/settlements/{stid}/restock")
def restock_settlement(cid: int, stid: int, user=Depends(current_user)):
    """Restockea de una todas las tiendas del pueblo."""
    with db() as conn:
        c = require_dm(conn, cid, user)
        _settlement_row(conn, cid, stid)
        day = _day_count(conn, cid)
        shops = conn.execute("SELECT * FROM shops WHERE settlement_id=?", (stid,)).fetchall()
        for shop in shops:
            _restock_shop(conn, cid, shop, c["dm_id"], day)
    return {"ok": True, "tiendas": len(shops)}


@router.get("/settlements/{stid}/catalog")
def settlement_catalog(cid: int, stid: int, user=Depends(current_user)):
    """Catálogo general del pueblo: todo lo que se consigue ahí, por tienda."""
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
        st = _settlement_row(conn, cid, stid)
        out = {"id": st["id"], "name": st["name"], "size": st["size"], "shops": []}
        for shop in conn.execute("SELECT * FROM shops WHERE settlement_id=? ORDER BY name", (stid,)):
            items = _stock(conn, shop["id"], is_dm)
            out["shops"].append({"id": shop["id"], "name": shop["name"],
                                 "preset": shop["preset"], "items": items})
        out["total"] = sum(len(s["items"]) for s in out["shops"])
        return out
