"""API: Encuentros, por campaña. Solo el DM."""

from fastapi import APIRouter, Depends

from ..access import require_dm
from ..auth import current_user
from ..database import db
from ..models import EncounterIn

router = APIRouter(prefix="/api/campaigns/{cid}/encounters", tags=["encounters"])


@router.get("")
def list_encounters(cid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        encounters = [dict(r) for r in conn.execute(
            "SELECT * FROM encounters WHERE campaign_id=? ORDER BY name", (cid,))]
        for enc in encounters:
            enc["enemies"] = [
                dict(r) for r in conn.execute(
                    "SELECT ee.id, ee.enemy_id, ee.cantidad, e.name, e.vida_max, e.focus_max, e.inv_max "
                    "FROM encounter_enemies ee JOIN enemies e ON e.id = ee.enemy_id "
                    "WHERE ee.encounter_id = ?", (enc["id"],)
                )
            ]
        return encounters


@router.post("")
def create_encounter(cid: int, enc: EncounterIn, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        cur = conn.execute(
            "INSERT INTO encounters (campaign_id, name, descripcion) VALUES (?,?,?)",
            (cid, enc.name, enc.descripcion),
        )
        eid = cur.lastrowid
        for item in enc.enemies:
            # solo enemigos del bestiario del DM
            owned = conn.execute(
                "SELECT 1 FROM enemies WHERE id=? AND owner_id=?", (item["enemy_id"], user["id"])
            ).fetchone()
            if owned:
                conn.execute(
                    "INSERT INTO encounter_enemies (encounter_id, enemy_id, cantidad) VALUES (?,?,?)",
                    (eid, item["enemy_id"], item.get("cantidad", 1)),
                )
        return {"id": eid}


@router.delete("/{eid}")
def delete_encounter(cid: int, eid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        conn.execute(
            "DELETE FROM encounters WHERE id=? AND campaign_id=?", (eid, cid)
        )  # cascada borra encounter_enemies
    return {"ok": True}
