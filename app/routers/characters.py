"""API: Personajes de jugador (CRUD + importar/descargar PDF)."""

import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from ..auth import current_user
from ..cosmere_import import ImportError_, parse_statblock
from ..database import db
from ..models import (CharacterIn, DaysChange, InjuryIn, LiveStat, LiveStatus,
                      PetImportIn)
from ..pdf_import import extract_pdf_image, parse_character_pdf

router = APIRouter(prefix="/api/characters", tags=["characters"])


def _serialize(r) -> dict:
    d = dict(r)
    d["statuses"] = json.loads(d.get("statuses") or "[]")
    d["sheet"] = json.loads(d.get("sheet") or "{}")
    d["injuries"] = json.loads(d.get("injuries") or "[]")
    d["has_pdf"] = bool(d.get("has_pdf"))
    d["has_image"] = bool(d.get("has_image"))
    return d


def _owned(conn, cid: int, user: dict):
    r = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
    if not r or r["owner_id"] != user["id"]:
        raise HTTPException(404, "Personaje no encontrado")
    return r


def _joinable_membership(conn, campaign_id, user: dict):
    """Membresía del jugador en la campaña que todavía no tiene personaje.

    Los personajes pertenecen a una campaña: solo se crean para una campaña
    donde el jugador está invitado/es miembro y aún no trajo un PJ."""
    if not campaign_id:
        raise HTTPException(400, "Falta la campaña del personaje")
    m = conn.execute(
        "SELECT * FROM campaign_members WHERE campaign_id=? AND user_id=?",
        (campaign_id, user["id"]),
    ).fetchone()
    if not m:
        raise HTTPException(404, "No estás invitado a esa campaña")
    if m["character_id"]:
        raise HTTPException(400, "Ya tenés un personaje en esta campaña")
    return m


def _link_membership(conn, campaign_id, user: dict, char_id: int):
    conn.execute(
        "UPDATE campaign_members SET status='accepted', character_id=? "
        "WHERE campaign_id=? AND user_id=?",
        (char_id, campaign_id, user["id"]),
    )


def _store_extracted_image(conn, char_id: int, pdf_bytes: bytes):
    """Best-effort: si el PDF trae un retrato, lo guarda como imagen del PJ."""
    img = extract_pdf_image(pdf_bytes)
    if not img:
        return
    blob, mime = img
    conn.execute(
        "INSERT INTO character_images (character_id, image, mime) VALUES (?,?,?) "
        "ON CONFLICT(character_id) DO UPDATE SET image=excluded.image, mime=excluded.mime",
        (char_id, blob, mime),
    )
    conn.execute("UPDATE characters SET has_image=1 WHERE id=?", (char_id,))


@router.get("")
def list_characters(user=Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT ch.*, camp.name AS campaign_name FROM characters ch "
            "LEFT JOIN campaigns camp ON camp.id=ch.campaign_id "
            "WHERE ch.owner_id=? ORDER BY ch.name",
            (user["id"],),
        ).fetchall()
        return [_serialize(r) for r in rows]


@router.post("")
def create_character(c: CharacterIn, user=Depends(current_user)):
    name = c.name.strip() or "Personaje"
    with db() as conn:
        _joinable_membership(conn, c.campaign_id, user)
        cur = conn.execute(
            "INSERT INTO characters (owner_id, campaign_id, name, vida_max, focus_max, inv_max, vida, focus, inv, statuses, sheet, has_pdf) "
            "VALUES (?,?,?,?,?,?,?,?,?,'[]',?,0)",
            (user["id"], c.campaign_id, name, c.vida_max, c.focus_max, c.inv_max,
             c.vida_max, c.focus_max, c.inv_max, json.dumps(c.sheet)),
        )
        new_id = cur.lastrowid
        _link_membership(conn, c.campaign_id, user, new_id)
        return {"id": new_id, "name": name}


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
        # Solo auto-extrae el retrato si el PJ todavía no tiene imagen (no pisa
        # una que el jugador haya subido a mano).
        has_img = conn.execute("SELECT 1 FROM character_images WHERE character_id=?", (cid,)).fetchone()
        if not has_img:
            _store_extracted_image(conn, cid, data)
    return {"id": cid, "name": p["name"]}


@router.delete("/{cid}")
def delete_character(cid: int, user=Depends(current_user)):
    with db() as conn:
        _owned(conn, cid, user)
        conn.execute("DELETE FROM characters WHERE id=?", (cid,))
    return {"ok": True}


@router.post("/import-pdf")
async def import_pdf(campaign_id: int, file: UploadFile = File(...), user=Depends(current_user)):
    """Crea un personaje para una campaña a partir de la ficha PDF."""
    data = await file.read()
    try:
        p = parse_character_pdf(data)
    except ValueError as e:
        raise HTTPException(400, str(e))
    with db() as conn:
        _joinable_membership(conn, campaign_id, user)
        cur = conn.execute(
            "INSERT INTO characters (owner_id, campaign_id, name, vida_max, focus_max, inv_max, vida, focus, inv, statuses, sheet, has_pdf) "
            "VALUES (?,?,?,?,?,?,?,?,?,'[]',?,1)",
            (user["id"], campaign_id, p["name"], p["vida_max"], p["focus_max"], p["inv_max"],
             p["vida"], p["focus"], p["inv"], json.dumps(p["sheet"])),
        )
        cid = cur.lastrowid
        conn.execute("INSERT INTO character_pdfs (character_id, pdf) VALUES (?,?)", (cid, data))
        _link_membership(conn, campaign_id, user, cid)
        _store_extracted_image(conn, cid, data)
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


# ── Gestión en vivo (vida/focus/investidura/estados), dentro o fuera de combate ──

_STATS = {"vida", "focus", "inv"}


def _clamp_stat(row, stat: str, delta: int) -> int:
    mx = row[f"{stat}_max"]
    cur = row[stat] if row[stat] is not None else mx
    return max(0, min(mx, cur + delta))


def _toggle_status(st: list, status: str) -> list:
    # Exhausted es apilable; el resto es on/off.
    if status == "Exhausted" or status not in st:
        st.append(status)
    else:
        st.remove(status)
    return st


def _owned_pet(conn, cid: int, pid: int, user: dict):
    _owned(conn, cid, user)
    r = conn.execute("SELECT * FROM pets WHERE id=? AND character_id=?", (pid, cid)).fetchone()
    if not r:
        raise HTTPException(404, "Mascota no encontrada")
    return r


@router.post("/{cid}/stat")
def character_stat(cid: int, s: LiveStat, user=Depends(current_user)):
    if s.stat not in _STATS:
        raise HTTPException(400, "Stat inválido")
    with db() as conn:
        r = _owned(conn, cid, user)
        val = _clamp_stat(r, s.stat, s.delta)
        conn.execute(f"UPDATE characters SET {s.stat}=? WHERE id=?", (val, cid))
    return {"ok": True, "value": val}


@router.post("/{cid}/status")
def character_status(cid: int, s: LiveStatus, user=Depends(current_user)):
    with db() as conn:
        r = _owned(conn, cid, user)
        st = _toggle_status(json.loads(r["statuses"] or "[]"), s.status)
        conn.execute("UPDATE characters SET statuses=? WHERE id=?", (json.dumps(st), cid))
    return {"ok": True, "statuses": st}


@router.post("/{cid}/status/remove_one")
def character_status_remove(cid: int, s: LiveStatus, user=Depends(current_user)):
    with db() as conn:
        r = _owned(conn, cid, user)
        st = json.loads(r["statuses"] or "[]")
        if s.status in st:
            st.remove(s.status)
        conn.execute("UPDATE characters SET statuses=? WHERE id=?", (json.dumps(st), cid))
    return {"ok": True, "statuses": st}


@router.post("/{cid}/pets/{pid}/stat")
def pet_stat(cid: int, pid: int, s: LiveStat, user=Depends(current_user)):
    if s.stat not in _STATS:
        raise HTTPException(400, "Stat inválido")
    with db() as conn:
        r = _owned_pet(conn, cid, pid, user)
        val = _clamp_stat(r, s.stat, s.delta)
        conn.execute(f"UPDATE pets SET {s.stat}=? WHERE id=?", (val, pid))
    return {"ok": True, "value": val}


@router.post("/{cid}/pets/{pid}/status")
def pet_status(cid: int, pid: int, s: LiveStatus, user=Depends(current_user)):
    with db() as conn:
        r = _owned_pet(conn, cid, pid, user)
        st = _toggle_status(json.loads(r["statuses"] or "[]"), s.status)
        conn.execute("UPDATE pets SET statuses=? WHERE id=?", (json.dumps(st), pid))
    return {"ok": True, "statuses": st}


@router.post("/{cid}/pets/{pid}/status/remove_one")
def pet_status_remove(cid: int, pid: int, s: LiveStatus, user=Depends(current_user)):
    with db() as conn:
        r = _owned_pet(conn, cid, pid, user)
        st = json.loads(r["statuses"] or "[]")
        if s.status in st:
            st.remove(s.status)
        conn.execute("UPDATE pets SET statuses=? WHERE id=?", (json.dumps(st), pid))
    return {"ok": True, "statuses": st}


# ── Heridas (injuries) del personaje ──────────────────────

@router.post("/{cid}/injuries")
def add_injury(cid: int, inj: InjuryIn, user=Depends(current_user)):
    name = inj.name.strip()
    if not name:
        raise HTTPException(400, "Poné el tipo de herida")
    with db() as conn:
        r = _owned(conn, cid, user)
        lst = json.loads(r["injuries"] or "[]")
        lst.append({"id": uuid.uuid4().hex[:8], "name": name,
                    "days": max(0, inj.days), "permanent": bool(inj.permanent)})
        conn.execute("UPDATE characters SET injuries=? WHERE id=?", (json.dumps(lst), cid))
    return {"ok": True, "injuries": lst}


@router.post("/{cid}/injuries/{iid}/days")
def injury_days(cid: int, iid: str, ch: DaysChange, user=Depends(current_user)):
    with db() as conn:
        r = _owned(conn, cid, user)
        lst = json.loads(r["injuries"] or "[]")
        for it in lst:
            if it["id"] == iid and not it.get("permanent"):
                it["days"] = max(0, it.get("days", 0) + ch.delta)
        conn.execute("UPDATE characters SET injuries=? WHERE id=?", (json.dumps(lst), cid))
    return {"ok": True, "injuries": lst}


@router.delete("/{cid}/injuries/{iid}")
def delete_injury(cid: int, iid: str, user=Depends(current_user)):
    with db() as conn:
        r = _owned(conn, cid, user)
        lst = [it for it in json.loads(r["injuries"] or "[]") if it["id"] != iid]
        conn.execute("UPDATE characters SET injuries=? WHERE id=?", (json.dumps(lst), cid))
    return {"ok": True, "injuries": lst}


# ── Imagen (retrato) del personaje ────────────────────────

@router.post("/{cid}/image")
async def upload_image(cid: int, file: UploadFile = File(...), user=Depends(current_user)):
    """El dueño sube/reemplaza el retrato del personaje (gana sobre el del PDF)."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "La imagen está vacía")
    mime = file.content_type or "image/png"
    if not mime.startswith("image/"):
        raise HTTPException(400, "El archivo no es una imagen")
    with db() as conn:
        _owned(conn, cid, user)
        conn.execute(
            "INSERT INTO character_images (character_id, image, mime) VALUES (?,?,?) "
            "ON CONFLICT(character_id) DO UPDATE SET image=excluded.image, mime=excluded.mime",
            (cid, data, mime),
        )
        conn.execute("UPDATE characters SET has_image=1 WHERE id=?", (cid,))
    return {"ok": True}


@router.get("/{cid}/image")
def get_image(cid: int, user=Depends(current_user)):
    """Sirve el retrato. Lo ve el dueño, el DM o cualquier miembro de su campaña."""
    with db() as conn:
        r = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
        if not r:
            raise HTTPException(404, "Personaje no encontrado")
        allowed = r["owner_id"] == user["id"]
        if not allowed and r["campaign_id"]:
            acc = conn.execute(
                "SELECT 1 FROM campaigns c WHERE c.id=? AND (c.dm_id=? OR EXISTS("
                "  SELECT 1 FROM campaign_members m WHERE m.campaign_id=c.id AND m.user_id=?))",
                (r["campaign_id"], user["id"], user["id"]),
            ).fetchone()
            allowed = acc is not None
        if not allowed:
            raise HTTPException(403, "Sin acceso a esta imagen")
        row = conn.execute("SELECT image, mime FROM character_images WHERE character_id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "Este personaje no tiene imagen")
        blob = bytes(row["image"])
        mime = row["mime"] or "image/png"
    return Response(content=blob, media_type=mime, headers={"Cache-Control": "no-cache"})


@router.delete("/{cid}/image")
def delete_image(cid: int, user=Depends(current_user)):
    with db() as conn:
        _owned(conn, cid, user)
        conn.execute("DELETE FROM character_images WHERE character_id=?", (cid,))
        conn.execute("UPDATE characters SET has_image=0 WHERE id=?", (cid,))
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
