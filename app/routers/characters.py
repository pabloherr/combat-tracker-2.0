"""API: Personajes de jugador (CRUD + importar/descargar PDF)."""

import json

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from ..auth import current_user
from ..cosmere_import import ImportError_, parse_statblock
from ..database import db
from ..models import CharacterIn, PetImportIn
from ..pdf_import import parse_character_pdf

router = APIRouter(prefix="/api/characters", tags=["characters"])


def _serialize(r) -> dict:
    d = dict(r)
    d["statuses"] = json.loads(d.get("statuses") or "[]")
    d["sheet"] = json.loads(d.get("sheet") or "{}")
    return d


def _owned(conn, cid: int, user: dict):
    r = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
    if not r or r["owner_id"] != user["id"]:
        raise HTTPException(404, "Personaje no encontrado")
    return r


@router.get("")
def list_characters(user=Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM characters WHERE owner_id=? ORDER BY name", (user["id"],)
        ).fetchall()
        return [_serialize(r) for r in rows]


@router.post("")
def create_character(c: CharacterIn, user=Depends(current_user)):
    name = c.name.strip() or "Personaje"
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO characters (owner_id, name, vida_max, focus_max, inv_max, vida, focus, inv, statuses, sheet, has_pdf) "
            "VALUES (?,?,?,?,?,?,?,?,'[]',?,0)",
            (user["id"], name, c.vida_max, c.focus_max, c.inv_max,
             c.vida_max, c.focus_max, c.inv_max, json.dumps(c.sheet)),
        )
        return {"id": cur.lastrowid, "name": name}


@router.put("/{cid}")
def update_character(cid: int, c: CharacterIn, user=Depends(current_user)):
    with db() as conn:
        row = _owned(conn, cid, user)
        # Valor actual: usa el que mandó el jugador (si vino), si no el guardado;
        # siempre topeado al máximo nuevo y sin bajar de 0.
        def _cur(sent, stored, mx):
            base = stored if sent is None else sent
            return max(0, min(mx, base if base is not None else mx))
        vida = _cur(c.vida, row["vida"], c.vida_max)
        focus = _cur(c.focus, row["focus"], c.focus_max)
        inv = _cur(c.inv, row["inv"], c.inv_max)
        conn.execute(
            "UPDATE characters SET name=?, vida_max=?, focus_max=?, inv_max=?, "
            "vida=?, focus=?, inv=?, sheet=? WHERE id=?",
            (c.name.strip() or "Personaje", c.vida_max, c.focus_max, c.inv_max,
             vida, focus, inv, json.dumps(c.sheet), cid),
        )
    return {"ok": True}


@router.post("/{cid}/reimport-pdf")
async def reimport_pdf(cid: int, file: UploadFile = File(...), user=Depends(current_user)):
    """Reemplaza la ficha PDF de un personaje existente y re-extrae sus datos."""
    data = await file.read()
    try:
        p = parse_character_pdf(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        _owned(conn, cid, user)
        conn.execute(
            "UPDATE characters SET name=?, vida_max=?, focus_max=?, inv_max=?, "
            "vida=?, focus=?, inv=?, sheet=?, has_pdf=1 WHERE id=?",
            (p["name"], p["vida_max"], p["focus_max"], p["inv_max"],
             p["vida"], p["focus"], p["inv"], json.dumps(p["sheet"]), cid),
        )
        conn.execute(
            "INSERT INTO character_pdfs (character_id, pdf) VALUES (?,?) "
            "ON CONFLICT(character_id) DO UPDATE SET pdf=excluded.pdf",
            (cid, data),
        )
    return {"id": cid, "name": p["name"]}


@router.delete("/{cid}")
def delete_character(cid: int, user=Depends(current_user)):
    with db() as conn:
        _owned(conn, cid, user)
        conn.execute("DELETE FROM characters WHERE id=?", (cid,))
    return {"ok": True}


@router.post("/import-pdf")
async def import_pdf(file: UploadFile = File(...), user=Depends(current_user)):
    data = await file.read()
    try:
        p = parse_character_pdf(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO characters (owner_id, name, vida_max, focus_max, inv_max, vida, focus, inv, statuses, sheet, has_pdf) "
            "VALUES (?,?,?,?,?,?,?,?,'[]',?,1)",
            (user["id"], p["name"], p["vida_max"], p["focus_max"], p["inv_max"],
             p["vida"], p["focus"], p["inv"], json.dumps(p["sheet"])),
        )
        cid = cur.lastrowid
        conn.execute("INSERT INTO character_pdfs (character_id, pdf) VALUES (?,?)", (cid, data))
    return {"id": cid, "name": p["name"]}


# ── Mascotas (pets) de un personaje ────────────────────────

def _pet_serialize(r) -> dict:
    d = dict(r)
    d["statuses"] = json.loads(d.get("statuses") or "[]")
    d["acciones"] = json.loads(d.get("acciones") or "[]")
    d["stats"] = json.loads(d.get("stats") or "{}")
    return d


@router.get("/{cid}/pets")
def list_pets(cid: int, user=Depends(current_user)):
    with db() as conn:
        _owned(conn, cid, user)
        rows = conn.execute("SELECT * FROM pets WHERE character_id=? ORDER BY name", (cid,)).fetchall()
        return [_pet_serialize(r) for r in rows]


@router.post("/{cid}/pets/import")
def import_pet(cid: int, payload: PetImportIn, user=Depends(current_user)):
    """Carga una mascota desde un statblock (mismo formato que los enemigos)."""
    with db() as conn:
        _owned(conn, cid, user)
    try:
        p = parse_statblock(payload.code)
    except ImportError_ as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        cur = conn.execute(
            "INSERT INTO pets (owner_id, character_id, name, vida_max, focus_max, inv_max, vida, focus, inv, statuses, acciones, stats) "
            "VALUES (?,?,?,?,?,?,?,?,?,'[]',?,?)",
            (user["id"], cid, p["name"], p["vida_max"], p["focus_max"], p["inv_max"],
             p["vida_max"], p["focus_max"], p["inv_max"],
             json.dumps(p["acciones"]), json.dumps(p["stats"])),
        )
        return {"id": cur.lastrowid, "name": p["name"]}


@router.delete("/{cid}/pets/{pid}")
def delete_pet(cid: int, pid: int, user=Depends(current_user)):
    with db() as conn:
        _owned(conn, cid, user)
        conn.execute("DELETE FROM pets WHERE id=? AND character_id=?", (pid, cid))
    return {"ok": True}


@router.get("/{cid}/pdf")
def download_pdf(cid: int, user=Depends(current_user)):
    """Descarga el PDF original. Accede el dueño o el DM de una campaña donde está el personaje."""
    with db() as conn:
        r = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
        if not r:
            raise HTTPException(404, "Personaje no encontrado")
        allowed = r["owner_id"] == user["id"]
        if not allowed:
            dm = conn.execute(
                "SELECT 1 FROM campaign_members m JOIN campaigns c ON c.id = m.campaign_id "
                "WHERE m.character_id = ? AND c.dm_id = ?",
                (cid, user["id"]),
            ).fetchone()
            allowed = dm is not None
        if not allowed:
            raise HTTPException(403, "Sin acceso a esta ficha")
        row = conn.execute("SELECT pdf FROM character_pdfs WHERE character_id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Este personaje no tiene PDF")
        pdf = bytes(row["pdf"])
    return Response(
        content=pdf, media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="ficha_{cid}.pdf"'},
    )
