"""API: Catálogo de objetos, por DM. Compartido entre sus campañas (como el bestiario).

Solo aplica a campañas de Cosmere: el sistema de objetos, tiendas e inventario
usa las reglas de carga del Cosmere RPG.
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..access import campaign_or_404, require_access, require_dm
from ..auth import current_user
from ..config import get_config
from ..cosmere_import import ImportError_
from ..database import db
from ..items_import import (CATEGORIAS, KIND_LABELS, KINDS, WEAPON_CLASSES,
                            export_items, parse_item, parse_items_bulk)
from ..models import ItemImportIn, ItemIn
from .characters import _inv_rows, _nest, character_inventory

router = APIRouter(prefix="/api/campaigns/{cid}", tags=["items"])


def _require_cosmere(conn, cid: int, modulo="modulo_catalogo"):
    """Los objetos son una regla de Cosmere, y además el DM puede apagar el
    catálogo y el inventario por separado desde los ajustes de la campaña."""
    c = campaign_or_404(conn, cid)
    system = (c["system"] if "system" in c.keys() else None) or "cosmere"
    if system != "cosmere":
        raise HTTPException(400, "Los objetos son solo para campañas de Cosmere")
    if modulo and not get_config(conn, cid)[modulo]:
        que = "el catálogo" if modulo == "modulo_catalogo" else "el inventario"
        raise HTTPException(400, f"El DM apagó {que} en esta campaña")
    return c


def _serialize(r) -> dict:
    d = dict(r)
    d["categorias"] = json.loads(d.get("categorias") or "[]")
    d["stats"] = json.loads(d.get("stats") or "{}")
    d["secreto"] = bool(d.get("secreto"))
    d["contenedor"] = bool(d.get("contenedor"))
    return d


def _insert_item(conn, owner_id: int, it: dict) -> int:
    cont_cap = max(0, int(it.get("contenedor_capacidad", 0)))
    cur = conn.execute(
        "INSERT INTO items (owner_id, name, kind, descripcion, categorias, precio, peso, "
        "slots, capacity_bonus, usos_max, contenedor, contenedor_capacidad, secreto, "
        "notas, stats) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (owner_id, it["name"], it.get("kind", "equipo"), it.get("descripcion", ""),
         json.dumps(it.get("categorias") or []), max(0, int(it.get("precio", 0))),
         it.get("peso", ""), max(0, int(it.get("slots", 1))),
         max(0, int(it.get("capacity_bonus", 0))), max(0, int(it.get("usos_max", 0))),
         1 if cont_cap else 0, cont_cap, 1 if it.get("secreto") else 0,
         it.get("notas", ""), json.dumps(it.get("stats") or {})),
    )
    return cur.lastrowid


# ── Catálogo del DM ────────────────────────────────────────

@router.get("/items")
def list_items(cid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        _require_cosmere(conn, cid)
        rows = conn.execute(
            "SELECT * FROM items WHERE owner_id=? ORDER BY name", (user["id"],)
        ).fetchall()
        return [_serialize(r) for r in rows]


@router.get("/items/categories")
def list_categories(cid: int, user=Depends(current_user)):
    """Categorías sugeridas + las que el DM ya usó en su catálogo."""
    with db() as conn:
        require_dm(conn, cid, user)
        cats = list(CATEGORIAS)
        for r in conn.execute("SELECT categorias FROM items WHERE owner_id=?", (user["id"],)):
            for c in json.loads(r["categorias"] or "[]"):
                if c not in cats:
                    cats.append(c)
        return cats


@router.get("/items/export")
def export_catalog(cid: int, user=Depends(current_user)):
    """Descarga el catálogo entero como YAML (re-importable con el bulk)."""
    with db() as conn:
        require_dm(conn, cid, user)
        _require_cosmere(conn, cid)
        rows = [_serialize(r) for r in conn.execute(
            "SELECT * FROM items WHERE owner_id=? ORDER BY name", (user["id"],))]
    text = export_items(rows)
    return Response(
        content=text, media_type="text/yaml; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="objetos.yaml"'},
    )


@router.post("/items")
def create_item(cid: int, it: ItemIn, user=Depends(current_user)):
    name = it.name.strip()
    if not name:
        raise HTTPException(400, "Poné un nombre al objeto")
    with db() as conn:
        require_dm(conn, cid, user)
        _require_cosmere(conn, cid)
        data = it.model_dump()
        data["name"] = name
        return {"id": _insert_item(conn, user["id"], data), "name": name}


@router.post("/items/import")
def import_item(cid: int, payload: ItemImportIn, user=Depends(current_user)):
    try:
        parsed = parse_item(payload.code)
    except ImportError_ as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        require_dm(conn, cid, user)
        _require_cosmere(conn, cid)
        iid = _insert_item(conn, user["id"], parsed)
    return {"id": iid, "name": parsed["name"]}


@router.post("/items/import-bulk")
def import_items_bulk(cid: int, payload: ItemImportIn, user=Depends(current_user)):
    try:
        parsed, errors = parse_items_bulk(payload.code)
    except ImportError_ as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        require_dm(conn, cid, user)
        _require_cosmere(conn, cid)
        for p in parsed:
            _insert_item(conn, user["id"], p)
    return {"added": len(parsed), "errors": errors, "names": [p["name"] for p in parsed]}


@router.put("/items/{iid}")
def update_item(cid: int, iid: int, it: ItemIn, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        cont_cap = max(0, it.contenedor_capacidad)
        conn.execute(
            "UPDATE items SET name=?, kind=?, descripcion=?, categorias=?, precio=?, "
            "peso=?, slots=?, capacity_bonus=?, usos_max=?, contenedor=?, "
            "contenedor_capacidad=?, secreto=?, notas=?, stats=? WHERE id=? AND owner_id=?",
            (it.name.strip() or "Objeto", it.kind, it.descripcion,
             json.dumps([c.strip().lower() for c in it.categorias if c.strip()]),
             max(0, it.precio), it.peso, max(0, it.slots), max(0, it.capacity_bonus),
             max(0, it.usos_max), 1 if cont_cap else 0, cont_cap,
             1 if it.secreto else 0, it.notas, json.dumps(it.stats), iid, user["id"]),
        )
    return {"ok": True}


@router.get("/item-kinds")
def item_kinds(cid: int, user=Depends(current_user)):
    """Tipos de objeto y sus etiquetas, para los formularios."""
    with db() as conn:
        require_dm(conn, cid, user)
        return {"kinds": [{"key": k, "label": KIND_LABELS[k]} for k in KINDS],
                "weapon_classes": WEAPON_CLASSES, "categorias": CATEGORIAS}


@router.post("/items/{iid}/secret")
def toggle_secret(cid: int, iid: int, user=Depends(current_user)):
    """Un objeto oculto no aparece en el catálogo que ven los jugadores."""
    with db() as conn:
        require_dm(conn, cid, user)
        r = conn.execute("SELECT secreto FROM items WHERE id=? AND owner_id=?",
                         (iid, user["id"])).fetchone()
        if not r:
            raise HTTPException(404, "Objeto no encontrado")
        val = 0 if r["secreto"] else 1
        conn.execute("UPDATE items SET secreto=? WHERE id=?", (val, iid))
    return {"ok": True, "secreto": bool(val)}


@router.delete("/items/{iid}")
def delete_item(cid: int, iid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        conn.execute("DELETE FROM items WHERE id=? AND owner_id=?", (iid, user["id"]))
    return {"ok": True}


# ── Catálogo que ven los jugadores ─────────────────────────

@router.get("/catalog")
def player_catalog(cid: int, user=Depends(current_user)):
    """Catálogo del DM de la campaña, con precios: de acá los jugadores agarran
    lo que quieran (ver `POST /api/characters/{id}/inventory/take`).

    Lo que el DM marcó como oculto no aparece."""
    with db() as conn:
        c, is_dm = require_access(conn, cid, user)
        _require_cosmere(conn, cid)
        sql = "SELECT * FROM items WHERE owner_id=?"
        if not is_dm:
            sql += " AND COALESCE(secreto,0)=0"
        rows = conn.execute(sql + " ORDER BY name", (c["dm_id"],)).fetchall()
        return [_serialize(r) for r in rows]


# ── Los inventarios de la mesa, para el DM ─────────────────

@router.get("/inventories")
def campaign_inventories(cid: int, user=Depends(current_user)):
    """Qué lleva cada uno. El DM edita cualquiera de estos inventarios con los
    endpoints de `/api/characters/{id}/inventory…`."""
    with db() as conn:
        require_dm(conn, cid, user)
        _require_cosmere(conn, cid, "modulo_inventario")
        chars = conn.execute(
            "SELECT * FROM characters WHERE campaign_id=? ORDER BY name", (cid,)).fetchall()
        out = {"characters": [], "grupo": _nest(_inv_rows(conn, campaign_id=cid))}
        for ch in chars:
            inv = character_inventory(conn, ch)
            inv.pop("grupo", None)      # el del grupo va una sola vez, arriba
            inv.pop("aliados", None)
            out["characters"].append(inv)
        return out
