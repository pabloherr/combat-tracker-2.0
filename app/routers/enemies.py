"""API: Enemigos (bestiario), por DM. Compartido entre sus campañas.

Las rutas siguen colgando de una campaña (para validar que quien llama sea el
DM de esa campaña), pero cada enemigo pertenece al DM (owner_id), así el mismo
bestiario aparece en todas las campañas de ese DM.
"""

import json

from fastapi import APIRouter, Depends, HTTPException

from ..access import require_dm
from ..auth import current_user
from ..cosmere_import import ImportError_, parse_statblock
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
