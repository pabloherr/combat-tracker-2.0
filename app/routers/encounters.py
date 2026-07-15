"""API: Encuentros, por campaña. Solo el DM."""

import json

from fastapi import APIRouter, Depends, HTTPException

from ..access import require_dm
from ..auth import current_user
from ..database import db
from ..models import EncounterIn

router = APIRouter(prefix="/api/campaigns/{cid}/encounters", tags=["encounters"])

# Campos de un enemigo que el DM puede ajustar dentro de un encuentro. El ajuste
# vive en encounter_enemies.overrides: el bestiario nunca se modifica.
_OV_TEXT = {"name", "clase", "faction_color"}
_OV_INT = {"vida_max", "focus_max", "inv_max"}
_CLASES = {"minion", "rival", "boss"}


def _clean_overrides(raw) -> dict:
    """Deja solo los campos permitidos y con el tipo correcto."""
    if not isinstance(raw, dict):
        return {}
    out = {}
    for k, v in raw.items():
        if k in _OV_INT:
            try:
                out[k] = max(0, int(v))
            except (TypeError, ValueError):
                continue
        elif k in _OV_TEXT:
            s = str(v).strip()
            if not s:
                continue          # vacío = sin override, usa lo del bestiario
            if k == "clase" and s not in _CLASES:
                continue
            out[k] = s
    return out


def _save_enemies(conn, eid: int, owner_id: int, items: list):
    """Reemplaza la lista de enemigos del encuentro (con sus overrides)."""
    conn.execute("DELETE FROM encounter_enemies WHERE encounter_id=?", (eid,))
    for item in items:
        # solo enemigos del bestiario del DM
        owned = conn.execute(
            "SELECT 1 FROM enemies WHERE id=? AND owner_id=?", (item.get("enemy_id"), owner_id)
        ).fetchone()
        if not owned:
            continue
        try:
            cantidad = max(1, int(item.get("cantidad", 1) or 1))
        except (TypeError, ValueError):
            cantidad = 1
        conn.execute(
            "INSERT INTO encounter_enemies (encounter_id, enemy_id, cantidad, overrides) VALUES (?,?,?,?)",
            (eid, item["enemy_id"], cantidad, json.dumps(_clean_overrides(item.get("overrides")))),
        )


@router.get("")
def list_encounters(cid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        encounters = [dict(r) for r in conn.execute(
            "SELECT * FROM encounters WHERE campaign_id=? ORDER BY name", (cid,))]
        for enc in encounters:
            rows = conn.execute(
                "SELECT ee.id, ee.enemy_id, ee.cantidad, ee.overrides, "
                "e.name, e.clase, e.vida_max, e.focus_max, e.inv_max, e.faction_color "
                "FROM encounter_enemies ee JOIN enemies e ON e.id = ee.enemy_id "
                "WHERE ee.encounter_id = ?", (enc["id"],)
            ).fetchall()
            out = []
            for r in rows:
                d = dict(r)
                ov = _clean_overrides(json.loads(d.pop("overrides") or "{}"))
                d["overrides"] = ov
                d["base"] = {k: d[k] for k in
                             ("name", "clase", "vida_max", "focus_max", "inv_max", "faction_color")}
                d.update(ov)      # valores efectivos (base + ajustes del encuentro)
                out.append(d)
            enc["enemies"] = out
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
        _save_enemies(conn, eid, user["id"], enc.enemies)
        return {"id": eid}


@router.put("/{eid}")
def update_encounter(cid: int, eid: int, enc: EncounterIn, user=Depends(current_user)):
    """Edita un encuentro ya creado: nombre, descripción y su lista de enemigos."""
    with db() as conn:
        require_dm(conn, cid, user)
        row = conn.execute(
            "SELECT 1 FROM encounters WHERE id=? AND campaign_id=?", (eid, cid)
        ).fetchone()
        if not row:
            raise HTTPException(404, "Encuentro no encontrado")
        conn.execute(
            "UPDATE encounters SET name=?, descripcion=? WHERE id=? AND campaign_id=?",
            (enc.name, enc.descripcion, eid, cid),
        )
        _save_enemies(conn, eid, user["id"], enc.enemies)
    return {"ok": True, "id": eid}


@router.delete("/{eid}")
def delete_encounter(cid: int, eid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        conn.execute(
            "DELETE FROM encounters WHERE id=? AND campaign_id=?", (eid, cid)
        )  # cascada borra encounter_enemies
    return {"ok": True}
