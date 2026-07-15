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
from ..models import EnemyImportIn, EnemyIn

router = APIRouter(prefix="/api/campaigns/{cid}/enemies", tags=["enemies"])


def _insert_enemy(conn, owner_id: int, e: EnemyIn) -> int:
    cur = conn.execute(
        "INSERT INTO enemies (owner_id, name, tipo, clase, vida_max, focus_max, inv_max, acciones, notas, faction_color, stats) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (owner_id, e.name, e.tipo, e.clase, e.vida_max, e.focus_max, e.inv_max,
         json.dumps([a.model_dump() for a in e.acciones]), e.notas, e.faction_color,
         json.dumps(e.stats)),
    )
    return cur.lastrowid


@router.get("")
def list_enemies(cid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM enemies WHERE owner_id=? ORDER BY name", (user["id"],))]
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
        require_dm(conn, cid, user)
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM enemies WHERE owner_id=? ORDER BY name", (user["id"],))]
    for r in rows:
        r["acciones"] = json.loads(r["acciones"] or "[]")
        r["stats"] = json.loads(r["stats"] or "{}")
    text = export_statblocks(rows)
    return Response(
        content=text, media_type="text/yaml; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="bestiario.yaml"'},
    )


@router.post("")
def create_enemy(cid: int, e: EnemyIn, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        return {"id": _insert_enemy(conn, user["id"], e)}


@router.post("/import")
def import_enemy(cid: int, payload: EnemyImportIn, user=Depends(current_user)):
    try:
        parsed = parse_statblock(payload.code)
    except ImportError_ as e:
        raise HTTPException(400, str(e))
    enemy = EnemyIn(**parsed)
    with db() as conn:
        require_dm(conn, cid, user)
        eid = _insert_enemy(conn, user["id"], enemy)
    return {"id": eid, "name": enemy.name}


@router.post("/import-bulk")
def import_bulk(cid: int, payload: EnemyImportIn, user=Depends(current_user)):
    """Importa muchas fichas de una vez (separadas por '---' o por 'layout:')."""
    try:
        parsed, errors = parse_statblocks_bulk(payload.code)
    except ImportError_ as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        require_dm(conn, cid, user)
        for p in parsed:
            _insert_enemy(conn, user["id"], EnemyIn(**p))
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
