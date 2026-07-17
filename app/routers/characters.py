"""API: Personajes de jugador (CRUD + importar/descargar PDF)."""

import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from ..auth import current_user
from ..cosmere_import import ImportError_, parse_statblock
from ..database import db
from ..dnd_pdf import parse_dnd_pdf
from ..models import (CharacterIn, CounterIn, CounterValue, DaysChange, InjuryIn,
                      LiveStat, LiveStatus, MarcosChange, MarcosSet, PetImportIn,
                      SlotsConfigIn, SlotSpend)
from ..pdf_import import extract_pdf_image, parse_character_pdf

router = APIRouter(prefix="/api/characters", tags=["characters"])


def _serialize(r) -> dict:
    d = dict(r)
    d["statuses"] = json.loads(d.get("statuses") or "[]")
    d["sheet"] = json.loads(d.get("sheet") or "{}")
    d["injuries"] = json.loads(d.get("injuries") or "[]")
    d["dnd"] = json.loads(d.get("dnd_resources") or "{}")
    d["has_pdf"] = bool(d.get("has_pdf"))
    d["has_image"] = bool(d.get("has_image"))
    d.pop("dnd_resources", None)
    return d


def _owned(conn, cid: int, user: dict):
    r = conn.execute("SELECT * FROM characters WHERE id=?", (cid,)).fetchone()
    if not r or r["owner_id"] != user["id"]:
        raise HTTPException(404, "Personaje no encontrado")
    return r


def _owned_or_dm(conn, cid: int, user: dict):
    """Personaje propio, o de un jugador de una campaña donde el usuario es DM."""
    r = conn.execute(
        "SELECT ch.*, camp.dm_id FROM characters ch "
        "LEFT JOIN campaigns camp ON camp.id=ch.campaign_id WHERE ch.id=?",
        (cid,),
    ).fetchone()
    if not r or (r["owner_id"] != user["id"] and r["dm_id"] != user["id"]):
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


def _campaign_system(conn, campaign_id) -> str:
    """Sistema de la campaña (cosmere | dnd): decide qué parser de PDF usar."""
    if not campaign_id:
        return "cosmere"
    c = conn.execute("SELECT system FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    return (c["system"] if c else None) or "cosmere"


def _parse_sheet_pdf(system: str, data: bytes) -> dict:
    """Parsea la ficha según el sistema. Devuelve el mismo shape para ambos:
    name, vida_max, vida, focus_max, focus, inv_max, inv, sheet y slots (dnd)."""
    if system == "dnd":
        p = parse_dnd_pdf(data)
        return {"name": p["name"], "vida_max": p["vida_max"], "vida": p["vida"],
                "focus_max": 0, "focus": 0, "inv_max": 0, "inv": 0,
                "sheet": p["sheet"], "slots": p["slots"]}
    p = parse_character_pdf(data)
    p["slots"] = None
    return p


def _apply_pdf_slots(conn, char_id: int, slots):
    """Precarga los spell slots leídos del PDF en dnd_resources (los contadores
    personalizados del jugador no se tocan)."""
    if slots is None:
        return
    row = conn.execute("SELECT dnd_resources FROM characters WHERE id=?", (char_id,)).fetchone()
    d = json.loads((row["dnd_resources"] if row else None) or "{}")
    d.setdefault("counters", [])
    d["slots"] = slots
    conn.execute("UPDATE characters SET dnd_resources=? WHERE id=?", (json.dumps(d), char_id))


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
    with db() as conn:
        r = _owned(conn, cid, user)
        system = _campaign_system(conn, r["campaign_id"])
    try:
        p = _parse_sheet_pdf(system, data)
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
        _apply_pdf_slots(conn, cid, p["slots"])
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
    """Crea un personaje para una campaña a partir de la ficha PDF
    (Cosmere o la ficha rellenable de D&D 5e, según el sistema de la campaña)."""
    data = await file.read()
    with db() as conn:
        _joinable_membership(conn, campaign_id, user)
        system = _campaign_system(conn, campaign_id)
    try:
        p = _parse_sheet_pdf(system, data)
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
        _apply_pdf_slots(conn, cid, p["slots"])
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
        light = r["marcos_light"] or 0
        if s.stat == "inv":
            # Cargar investidura apaga marcos cargados 1:1 (tantos como luz disponible).
            cur = r["inv"] if r["inv"] is not None else r["inv_max"]
            gained = val - cur
            if gained > 0 and light > 0:
                light = max(0, light - gained)
                conn.execute("UPDATE characters SET marcos_light=? WHERE id=?", (light, cid))
    return {"ok": True, "value": val, "marcos_light": light}


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


# ── Recursos D&D: spell slots y contadores de clase ────────
# Editables por el dueño del personaje o por el DM de su campaña.

_RECOVERIES = {"long", "short", "none"}


def _dnd(row) -> dict:
    d = json.loads(row["dnd_resources"] or "{}")
    d.setdefault("slots", {})
    d.setdefault("counters", [])
    return d


def _save_dnd(conn, cid: int, d: dict):
    conn.execute("UPDATE characters SET dnd_resources=? WHERE id=?", (json.dumps(d), cid))


@router.put("/{cid}/slots")
def configure_slots(cid: int, cfg: SlotsConfigIn, user=Depends(current_user)):
    """Fija el máximo de spell slots por nivel (1-9). 0 o ausente quita el nivel.
    Un nivel nuevo arranca con todos los slots disponibles."""
    with db() as conn:
        r = _owned_or_dm(conn, cid, user)
        d = _dnd(r)
        slots = {}
        for lvl in range(1, 10):
            key = str(lvl)
            mx = max(0, min(12, int(cfg.levels.get(key, 0))))
            if mx <= 0:
                continue
            prev = d["slots"].get(key)
            cur = mx if prev is None else min(prev.get("cur", mx), mx)
            slots[key] = {"max": mx, "cur": cur}
        d["slots"] = slots
        _save_dnd(conn, cid, d)
    return {"ok": True, "dnd": d}


@router.post("/{cid}/slots/spend")
def spend_slot(cid: int, s: SlotSpend, user=Depends(current_user)):
    with db() as conn:
        r = _owned_or_dm(conn, cid, user)
        d = _dnd(r)
        slot = d["slots"].get(str(s.level))
        if not slot:
            raise HTTPException(404, "No tenés slots de ese nivel")
        slot["cur"] = max(0, min(slot.get("max", 0), slot.get("cur", 0) + s.delta))
        _save_dnd(conn, cid, d)
    return {"ok": True, "dnd": d}


@router.post("/{cid}/counters")
def add_counter(cid: int, c: CounterIn, user=Depends(current_user)):
    name = c.name.strip()
    if not name:
        raise HTTPException(400, "Poné un nombre al contador")
    if c.recovery not in _RECOVERIES:
        raise HTTPException(400, "Recuperación inválida")
    mx = max(1, c.max)
    with db() as conn:
        r = _owned_or_dm(conn, cid, user)
        d = _dnd(r)
        d["counters"].append({"id": uuid.uuid4().hex[:8], "name": name,
                              "max": mx, "cur": mx, "recovery": c.recovery})
        _save_dnd(conn, cid, d)
    return {"ok": True, "dnd": d}


@router.put("/{cid}/counters/{kid}")
def update_counter(cid: int, kid: str, c: CounterIn, user=Depends(current_user)):
    name = c.name.strip()
    if not name:
        raise HTTPException(400, "Poné un nombre al contador")
    if c.recovery not in _RECOVERIES:
        raise HTTPException(400, "Recuperación inválida")
    with db() as conn:
        r = _owned_or_dm(conn, cid, user)
        d = _dnd(r)
        for k in d["counters"]:
            if k["id"] == kid:
                k["name"] = name
                k["max"] = max(1, c.max)
                k["cur"] = min(k.get("cur", 0), k["max"])
                k["recovery"] = c.recovery
                break
        else:
            raise HTTPException(404, "Contador no encontrado")
        _save_dnd(conn, cid, d)
    return {"ok": True, "dnd": d}


@router.post("/{cid}/counters/{kid}/value")
def counter_value(cid: int, kid: str, v: CounterValue, user=Depends(current_user)):
    with db() as conn:
        r = _owned_or_dm(conn, cid, user)
        d = _dnd(r)
        for k in d["counters"]:
            if k["id"] == kid:
                k["cur"] = max(0, min(k.get("max", 0), k.get("cur", 0) + v.delta))
                break
        else:
            raise HTTPException(404, "Contador no encontrado")
        _save_dnd(conn, cid, d)
    return {"ok": True, "dnd": d}


@router.delete("/{cid}/counters/{kid}")
def delete_counter(cid: int, kid: str, user=Depends(current_user)):
    with db() as conn:
        r = _owned_or_dm(conn, cid, user)
        d = _dnd(r)
        d["counters"] = [k for k in d["counters"] if k["id"] != kid]
        _save_dnd(conn, cid, d)
    return {"ok": True, "dnd": d}


# ── Marcos (esferas) del personaje ────────────────────────

def _adjust_marcos_total(conn, char_id: int, marcos: int, light: int, delta: int):
    """Ajusta el total de marcos. Al reducir se van primero los opacos; los que se
    agregan entran opacos. Devuelve (nuevo_total, nueva_luz)."""
    new_total = max(0, marcos + delta)
    new_light = min(light, new_total)
    conn.execute("UPDATE characters SET marcos=?, marcos_light=? WHERE id=?",
                 (new_total, new_light, char_id))
    return new_total, new_light


@router.post("/{cid}/marcos")
def character_marcos(cid: int, ch: MarcosChange, user=Depends(current_user)):
    with db() as conn:
        r = _owned(conn, cid, user)
        total, light = _adjust_marcos_total(conn, cid, r["marcos"] or 0, r["marcos_light"] or 0, ch.delta)
    return {"ok": True, "marcos": total, "marcos_light": light}


@router.post("/{cid}/marcos/set")
def character_marcos_set(cid: int, s: MarcosSet, user=Depends(current_user)):
    """El jugador fija directamente cuántos marcos tiene cargados y opacos."""
    cargados = max(0, s.cargados)
    opacos = max(0, s.opacos)
    with db() as conn:
        _owned(conn, cid, user)
        conn.execute("UPDATE characters SET marcos=?, marcos_light=? WHERE id=?",
                     (cargados + opacos, cargados, cid))
    return {"ok": True, "marcos": cargados + opacos, "marcos_light": cargados}


@router.post("/{cid}/marcos/charge_inv")
def character_charge_inv(cid: int, user=Depends(current_user)):
    """Cargar investidura: llena el medidor 1:1 apagando marcos cargados."""
    with db() as conn:
        r = _owned(conn, cid, user)
        light = r["marcos_light"] or 0
        inv = r["inv"] if r["inv"] is not None else r["inv_max"]
        amount = max(0, min(r["inv_max"] - inv, light))
        new_inv = inv + amount
        new_light = light - amount
        conn.execute("UPDATE characters SET inv=?, marcos_light=? WHERE id=?", (new_inv, new_light, cid))
    return {"ok": True, "inv": new_inv, "marcos_light": new_light, "charged": amount}


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
