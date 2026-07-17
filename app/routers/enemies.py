"""API: Enemigos (bestiario), por DM. Compartido entre sus campañas.

Las rutas siguen colgando de una campaña (para validar que quien llama sea el
DM de esa campaña), pero cada enemigo pertenece al DM (owner_id), así el mismo
bestiario aparece en todas las campañas de ese DM.
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..access import require_dm
from ..auth import current_user
from ..cosmere_import import (ImportError_, export_statblocks, parse_statblock,
                              parse_statblocks_bulk)
from ..database import db
from ..dnd_import import (export_dnd_statblocks, parse_dnd_statblock,
                          parse_dnd_statblocks_bulk)
from ..models import EnemyImportIn, EnemyIn

router = APIRouter(prefix="/api/campaigns/{cid}/enemies", tags=["enemies"])


def _sys(c) -> str:
    """Sistema de la campaña: los bestiarios de cada sistema no se mezclan."""
    return (c["system"] if "system" in c.keys() else None) or "cosmere"


def _insert_enemy(conn, owner_id: int, e: EnemyIn, system: str = "cosmere") -> int:
    cur = conn.execute(
        "INSERT INTO enemies (owner_id, name, tipo, clase, vida_max, focus_max, inv_max, acciones, notas, faction_color, stats, system) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (owner_id, e.name, e.tipo, e.clase, e.vida_max, e.focus_max, e.inv_max,
         json.dumps([a.model_dump() for a in e.acciones]), e.notas, e.faction_color,
         json.dumps(e.stats), system),
    )
    return cur.lastrowid


@router.get("")
def list_enemies(cid: int, user=Depends(current_user)):
    with db() as conn:
        c = require_dm(conn, cid, user)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM enemies WHERE owner_id=? AND COALESCE(system,'cosmere')=? ORDER BY name",
            (user["id"], _sys(c)))]
        for r in rows:
            r["acciones"] = json.loads(r["acciones"])
            r["stats"] = json.loads(r["stats"] or "{}")
        return rows


@router.get("/export")
def export_enemies(cid: int, user=Depends(current_user)):
    """Descarga todo el bestiario del DM como un YAML de statblocks.

    Sirve de backup y para pasárselo a otro DM: el archivo se vuelve a cargar
    con "Importar en bulk" tal cual."""
    with db() as conn:
        c = require_dm(conn, cid, user)
        system = _sys(c)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM enemies WHERE owner_id=? AND COALESCE(system,'cosmere')=? ORDER BY name",
            (user["id"], system))]
    for r in rows:
        r["acciones"] = json.loads(r["acciones"] or "[]")
        r["stats"] = json.loads(r["stats"] or "{}")
    if system == "dnd":
        text = export_dnd_statblocks(rows)
        fname = "bestiario_dnd.yaml"
    else:
        text = export_statblocks(rows)
        fname = "bestiario.yaml"
    return Response(
        content=text, media_type="text/yaml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@router.post("")
def create_enemy(cid: int, e: EnemyIn, user=Depends(current_user)):
    with db() as conn:
        c = require_dm(conn, cid, user)
        return {"id": _insert_enemy(conn, user["id"], e, _sys(c))}


@router.post("/import")
def import_enemy(cid: int, payload: EnemyImportIn, user=Depends(current_user)):
    with db() as conn:
        c = require_dm(conn, cid, user)
        system = _sys(c)
    try:
        parsed = (parse_dnd_statblock if system == "dnd" else parse_statblock)(payload.code)
    except ImportError_ as e:
        raise HTTPException(400, str(e))
    enemy = EnemyIn(**parsed)
    with db() as conn:
        require_dm(conn, cid, user)
        eid = _insert_enemy(conn, user["id"], enemy, system)
    return {"id": eid, "name": enemy.name}


@router.post("/import-bulk")
def import_bulk(cid: int, payload: EnemyImportIn, user=Depends(current_user)):
    """Importa muchas fichas de una vez (separadas por '---' o por 'layout:')."""
    with db() as conn:
        c = require_dm(conn, cid, user)
        system = _sys(c)
    try:
        parsed, errors = (parse_dnd_statblocks_bulk if system == "dnd"
                          else parse_statblocks_bulk)(payload.code)
    except ImportError_ as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        require_dm(conn, cid, user)
        for p in parsed:
            _insert_enemy(conn, user["id"], EnemyIn(**p), system)
    return {"added": len(parsed), "errors": errors, "names": [p["name"] for p in parsed]}


@router.put("/{eid}")
def update_enemy(cid: int, eid: int, e: EnemyIn, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        conn.execute(
            "UPDATE enemies SET name=?, tipo=?, clase=?, vida_max=?, focus_max=?, inv_max=?, acciones=?, notas=?, faction_color=?, stats=? "
            "WHERE id=? AND owner_id=?",
            (e.name, e.tipo, e.clase, e.vida_max, e.focus_max, e.inv_max,
             json.dumps([a.model_dump() for a in e.acciones]), e.notas, e.faction_color,
             json.dumps(e.stats), eid, user["id"]),
        )
    return {"ok": True}


@router.delete("/{eid}")
def delete_enemy(cid: int, eid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        conn.execute("DELETE FROM enemies WHERE id=? AND owner_id=?", (eid, user["id"]))
    return {"ok": True}
