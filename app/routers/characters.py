"""API: Personajes de jugador (CRUD + importar/descargar PDF)."""

import json
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from ..auth import current_user
from ..config import CONFIG_DEFAULTS, get_config, size_bases
from ..database import db
from ..dnd_pdf import parse_dnd_pdf
from ..models import (CharacterIn, CounterIn, CounterValue, DaysChange, InjuryIn,
                      InventoryIn, InventoryMove, InventoryQty, InventoryStash,
                      InventoryTransfer, LiveStat, LiveStatus, MarcosChange, MarcosSet,
                      PetFromEnemy, PetName, SizeIn, SlotsConfigIn, SlotSpend,
                      TakeIn)
from ..pdf_import import extract_pdf_image, parse_character_pdf
from ..state import combats
from ..ws import push_state

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


# ── Sincronización con el combate en curso ────────────────

def _campaign_of(conn, cid: int):
    r = conn.execute("SELECT campaign_id FROM characters WHERE id=?", (cid,)).fetchone()
    return r["campaign_id"] if r else None


async def _sync_combat(campaign_id, fields: dict, *, char_id=None, pet_id=None):
    """Refleja en el combate en curso un cambio hecho desde la ficha.

    Durante un combate cada participante lleva su propia copia de vida/focus/
    investidura/estados. Si el jugador toca su ficha fuera del combate hay que
    actualizar también esa copia: si no, el combate (y la pantalla del DM) se
    quedan con los valores viejos y el próximo cambio hecho en combate los pisa.
    """
    if not campaign_id:
        return
    combat = combats.get(campaign_id)
    if not combat.get("active"):
        return
    touched = False
    for p in combat.get("participants", []):
        is_char = (char_id is not None and p.get("kind") == "player"
                   and p.get("char_id") == char_id)
        is_pet = (pet_id is not None and p.get("kind") == "pet"
                  and p.get("pet_id") == pet_id)
        if not (is_char or is_pet):
            continue
        p.update(fields)
        if "vida" in fields:
            p["defeated"] = p["vida"] == 0
        touched = True
    if touched:
        await push_state(campaign_id)   # guarda y difunde por WebSocket


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
async def update_character(cid: int, c: CharacterIn, user=Depends(current_user)):
    name = c.name.strip() or "Personaje"
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
            (name, c.vida_max, c.focus_max, c.inv_max,
             vida, focus, inv, json.dumps(c.sheet), cid),
        )
        campaign_id = row["campaign_id"]
    await _sync_combat(campaign_id, {
        "name": name,
        "vida": vida, "vida_max": c.vida_max,
        "focus": focus, "focus_max": c.focus_max,
        "inv": inv, "inv_max": c.inv_max,
    }, char_id=cid)
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
        _seed_from_pdf(conn, cid, p, system)
        _link_membership(conn, campaign_id, user, cid)
        _store_extracted_image(conn, cid, data)
    return {"id": cid, "name": p["name"]}


def _seed_from_pdf(conn, char_id: int, p: dict, system: str):
    """Semilla inicial desde la ficha: inventario (armas + equipo) y marcos.

    Solo corre al **crear** el personaje. Al reimportar el PDF para subir de
    nivel no se toca nada de esto, porque para entonces el jugador ya compró,
    gastó y reacomodó sus cosas en la app."""
    if system != "cosmere":
        return
    sheet = p.get("sheet") or {}
    camp = conn.execute("SELECT campaign_id FROM characters WHERE id=?", (char_id,)).fetchone()
    for linea in (sheet.get("weapons") or []) + (sheet.get("equipment") or []):
        nombre = str(linea).strip()
        if nombre:
            conn.execute(
                "INSERT INTO inventory (character_id, campaign_id, name, slots, cantidad) "
                "VALUES (?,?,?,1,1)",
                (char_id, camp["campaign_id"] if camp else None, nombre[:120]),
            )
    marcos = max(0, int(p.get("spheres") or 0))
    if marcos:
        # Entran cargados; el jugador ajusta el reparto con el recuadro de marcos.
        conn.execute("UPDATE characters SET marcos=?, marcos_light=? WHERE id=?",
                     (marcos, marcos, char_id))


# ── Mascotas (pets) de un personaje ────────────────────────

def _pet_serialize(r) -> dict:
    d = dict(r)
    d["statuses"] = json.loads(d.get("statuses") or "[]")
    d["acciones"] = json.loads(d.get("acciones") or "[]")
    d["stats"] = json.loads(d.get("stats") or "{}")
    d["compartida"] = bool(d.get("compartida"))
    return d


@router.get("/{cid}/pets")
def list_pets(cid: int, user=Depends(current_user)):
    with db() as conn:
        _owned(conn, cid, user)
        rows = conn.execute("SELECT * FROM pets WHERE character_id=? ORDER BY name", (cid,)).fetchall()
        return [_pet_serialize(r) for r in rows]


@router.post("/{cid}/pets/from-enemy")
def add_pet_from_enemy(cid: int, payload: PetFromEnemy, user=Depends(current_user)):
    """El jugador agrega como mascota un enemigo que el DM habilitó en su campaña.

    Se copia una foto (snapshot) de la ficha: si el DM edita el enemigo después,
    la mascota ya agregada no cambia."""
    with db() as conn:
        ch = _owned(conn, cid, user)
        # El enemigo tiene que estar habilitado como mascota en la campaña del PJ.
        e = conn.execute(
            "SELECT e.* FROM campaign_pet_options po JOIN enemies e ON e.id = po.enemy_id "
            "WHERE po.campaign_id=? AND po.enemy_id=?",
            (ch["campaign_id"], payload.enemy_id),
        ).fetchone()
        if not e:
            raise HTTPException(404, "Esa mascota no está disponible en esta campaña")
        cur = conn.execute(
            "INSERT INTO pets (owner_id, character_id, name, vida_max, focus_max, inv_max, vida, focus, inv, statuses, acciones, stats) "
            "VALUES (?,?,?,?,?,?,?,?,?,'[]',?,?)",
            (user["id"], cid, e["name"], e["vida_max"], e["focus_max"], e["inv_max"],
             e["vida_max"], e["focus_max"], e["inv_max"],
             e["acciones"] or "[]", e["stats"] or "{}"),
        )
        return {"id": cur.lastrowid, "name": e["name"]}


@router.delete("/{cid}/pets/{pid}")
def delete_pet(cid: int, pid: int, user=Depends(current_user)):
    with db() as conn:
        _owned(conn, cid, user)
        conn.execute("DELETE FROM pets WHERE id=? AND character_id=?", (pid, cid))
    return {"ok": True}


@router.put("/{cid}/pets/{pid}")
async def rename_pet(cid: int, pid: int, p: PetName, user=Depends(current_user)):
    """Le cambia el nombre a la mascota: viene con el del bestiario, pero el
    axehound de la mesa se llama como ellos quieran.

    Lo cambia quien la maneja: el dueño y el DM, o cualquiera si es de todos."""
    name = p.name.strip()[:80]
    if not name:
        raise HTTPException(400, "Poné un nombre a la mascota")
    with db() as conn:
        r = _owned_pet(conn, cid, pid, user)
        conn.execute("UPDATE pets SET name=? WHERE id=?", (name, r["id"]))
        campaign_id = _campaign_of(conn, r["character_id"])
    await _sync_combat(campaign_id, {"name": name}, pet_id=r["id"])
    return {"ok": True, "name": name}


@router.post("/{cid}/pets/{pid}/shared")
async def toggle_pet_shared(cid: int, pid: int, user=Depends(current_user)):
    """Marca la mascota como **de todos**: pasa a ser del grupo y cualquiera de
    la mesa le maneja la vida, los estados y el inventario.

    La prende y la apaga quien la trajo (o el DM): es su bicho."""
    with db() as conn:
        ch = _owned_or_dm(conn, cid, user)
        r = conn.execute("SELECT * FROM pets WHERE id=? AND character_id=?",
                         (pid, cid)).fetchone()
        if not r:
            raise HTTPException(404, "Mascota no encontrada")
        val = 0 if r["compartida"] else 1
        conn.execute("UPDATE pets SET compartida=? WHERE id=?", (val, pid))
        campaign_id = ch["campaign_id"]
    # en combate cambia quién la puede tocar y quién le ve los números
    await _sync_combat(campaign_id, {"shared": bool(val)}, pet_id=pid)
    return {"ok": True, "compartida": bool(val)}


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
    """Mascota que este usuario puede manejar.

    La propia siempre; una marcada como **de todos** la maneja cualquiera de la
    mesa (y el DM), aunque la haya traído otro jugador."""
    r = conn.execute("SELECT * FROM pets WHERE id=? AND character_id=?", (pid, cid)).fetchone()
    if not r:
        # el pedido puede venir con el personaje de quien la maneja, no con el
        # de quien la trajo: si es de todos, se busca igual
        r = conn.execute(
            "SELECT p.* FROM pets p JOIN characters ch ON ch.id=p.character_id "
            "WHERE p.id=? AND p.compartida=1 AND ch.campaign_id="
            "  (SELECT campaign_id FROM characters WHERE id=?)", (pid, cid)).fetchone()
    if not r:
        raise HTTPException(404, "Mascota no encontrada")
    if not r["compartida"]:
        _owned_or_dm(conn, r["character_id"], user)
    else:
        _en_la_campana(conn, r["character_id"], user)
    return r


def _en_la_campana(conn, char_id: int, user: dict):
    """El usuario es el DM de esa campaña o un jugador aceptado en ella."""
    ok = conn.execute(
        "SELECT 1 FROM characters ch JOIN campaigns c ON c.id=ch.campaign_id "
        "WHERE ch.id=? AND (c.dm_id=? OR EXISTS("
        "  SELECT 1 FROM campaign_members m WHERE m.campaign_id=c.id AND m.user_id=?"
        "  AND m.status='accepted'))",
        (char_id, user["id"], user["id"])).fetchone()
    if not ok:
        raise HTTPException(404, "Mascota no encontrada")


def _pet_al_alcance(conn, ch, pid: int):
    """Mascota a la que este personaje le puede meter mano: una suya, o una de
    todos de su misma campaña."""
    r = conn.execute("SELECT * FROM pets WHERE id=? AND character_id=?", (pid, ch["id"])).fetchone()
    if not r and ch["campaign_id"]:
        r = conn.execute(
            "SELECT p.* FROM pets p JOIN characters c2 ON c2.id=p.character_id "
            "WHERE p.id=? AND p.compartida=1 AND c2.campaign_id=?",
            (pid, ch["campaign_id"])).fetchone()
    if not r:
        raise HTTPException(404, "Mascota no encontrada")
    return r


@router.post("/{cid}/stat")
async def character_stat(cid: int, s: LiveStat, user=Depends(current_user)):
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
        campaign_id = r["campaign_id"]
    await _sync_combat(campaign_id, {s.stat: val}, char_id=cid)
    return {"ok": True, "value": val, "marcos_light": light}


@router.post("/{cid}/status")
async def character_status(cid: int, s: LiveStatus, user=Depends(current_user)):
    with db() as conn:
        r = _owned(conn, cid, user)
        st = _toggle_status(json.loads(r["statuses"] or "[]"), s.status)
        conn.execute("UPDATE characters SET statuses=? WHERE id=?", (json.dumps(st), cid))
        campaign_id = r["campaign_id"]
    await _sync_combat(campaign_id, {"statuses": st}, char_id=cid)
    return {"ok": True, "statuses": st}


@router.post("/{cid}/status/remove_one")
async def character_status_remove(cid: int, s: LiveStatus, user=Depends(current_user)):
    with db() as conn:
        r = _owned(conn, cid, user)
        st = json.loads(r["statuses"] or "[]")
        if s.status in st:
            st.remove(s.status)
        conn.execute("UPDATE characters SET statuses=? WHERE id=?", (json.dumps(st), cid))
        campaign_id = r["campaign_id"]
    await _sync_combat(campaign_id, {"statuses": st}, char_id=cid)
    return {"ok": True, "statuses": st}


@router.post("/{cid}/pets/{pid}/stat")
async def pet_stat(cid: int, pid: int, s: LiveStat, user=Depends(current_user)):
    if s.stat not in _STATS:
        raise HTTPException(400, "Stat inválido")
    with db() as conn:
        r = _owned_pet(conn, cid, pid, user)
        val = _clamp_stat(r, s.stat, s.delta)
        conn.execute(f"UPDATE pets SET {s.stat}=? WHERE id=?", (val, pid))
        campaign_id = _campaign_of(conn, cid)
    await _sync_combat(campaign_id, {s.stat: val}, pet_id=pid)
    return {"ok": True, "value": val}


@router.post("/{cid}/pets/{pid}/status")
async def pet_status(cid: int, pid: int, s: LiveStatus, user=Depends(current_user)):
    with db() as conn:
        r = _owned_pet(conn, cid, pid, user)
        st = _toggle_status(json.loads(r["statuses"] or "[]"), s.status)
        conn.execute("UPDATE pets SET statuses=? WHERE id=?", (json.dumps(st), pid))
        campaign_id = _campaign_of(conn, cid)
    await _sync_combat(campaign_id, {"statuses": st}, pet_id=pid)
    return {"ok": True, "statuses": st}


@router.post("/{cid}/pets/{pid}/status/remove_one")
async def pet_status_remove(cid: int, pid: int, s: LiveStatus, user=Depends(current_user)):
    with db() as conn:
        r = _owned_pet(conn, cid, pid, user)
        st = json.loads(r["statuses"] or "[]")
        if s.status in st:
            st.remove(s.status)
        conn.execute("UPDATE pets SET statuses=? WHERE id=?", (json.dumps(st), pid))
        campaign_id = _campaign_of(conn, cid)
    await _sync_combat(campaign_id, {"statuses": st}, pet_id=pid)
    return {"ok": True, "statuses": st}


# ── Heridas (injuries) del personaje ──────────────────────

@router.post("/{cid}/injuries")
async def add_injury(cid: int, inj: InjuryIn, user=Depends(current_user)):
    name = inj.name.strip()
    if not name:
        raise HTTPException(400, "Poné el tipo de herida")
    with db() as conn:
        r = _owned(conn, cid, user)
        lst = json.loads(r["injuries"] or "[]")
        lst.append({"id": uuid.uuid4().hex[:8], "name": name,
                    "days": max(0, inj.days), "permanent": bool(inj.permanent)})
        conn.execute("UPDATE characters SET injuries=? WHERE id=?", (json.dumps(lst), cid))
        campaign_id = r["campaign_id"]
    await _sync_combat(campaign_id, {"injuries": lst}, char_id=cid)
    return {"ok": True, "injuries": lst}


@router.post("/{cid}/injuries/{iid}/days")
async def injury_days(cid: int, iid: str, ch: DaysChange, user=Depends(current_user)):
    with db() as conn:
        r = _owned(conn, cid, user)
        lst = json.loads(r["injuries"] or "[]")
        for it in lst:
            if it["id"] == iid and not it.get("permanent"):
                it["days"] = max(0, it.get("days", 0) + ch.delta)
        conn.execute("UPDATE characters SET injuries=? WHERE id=?", (json.dumps(lst), cid))
        campaign_id = r["campaign_id"]
    await _sync_combat(campaign_id, {"injuries": lst}, char_id=cid)
    return {"ok": True, "injuries": lst}


@router.delete("/{cid}/injuries/{iid}")
async def delete_injury(cid: int, iid: str, user=Depends(current_user)):
    with db() as conn:
        r = _owned(conn, cid, user)
        lst = [it for it in json.loads(r["injuries"] or "[]") if it["id"] != iid]
        conn.execute("UPDATE characters SET injuries=? WHERE id=?", (json.dumps(lst), cid))
        campaign_id = r["campaign_id"]
    await _sync_combat(campaign_id, {"injuries": lst}, char_id=cid)
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


# ── Inventario y capacidad de carga (regla opcional de Cosmere) ──
#
# Capacidad = base por tamaño + Fuerza (+ bonus de objetos como la mochila).
# Cada objeto ocupa 1 slot salvo excepciones: el dinero y los insignificantes 0,
# Cumbersome N ocupa 1+N, y los grandes más de 1 (queda a criterio del DM, que
# carga el número de slots al crear el objeto).

# Las bases por tamaño las fija el DM en los ajustes de la campaña (ver
# `app/config.py`); estos son los valores del manual, que valen de default.
_SIZE_BASE = size_bases(CONFIG_DEFAULTS)
# Los statblocks vienen en inglés; se mapean al tamaño del sistema.
_SIZE_ALIASES = {"tiny": "Pequeño", "small": "Pequeño", "medium": "Mediano",
                 "large": "Grande", "huge": "Enorme", "gargantuan": "Gargantuesco",
                 "pequeño": "Pequeño", "mediano": "Mediano", "grande": "Grande",
                 "enorme": "Enorme", "gargantuesco": "Gargantuesco"}


def _norm_size(value) -> str:
    return _SIZE_ALIASES.get(str(value or "").strip().lower(), "Mediano")


# Zonas donde puede estar un objeto. Solo lo que está encima ('') pesa: los
# guardados representan lo que el personaje tiene pero no carga (en la posada,
# en el carro, en la casa del grupo) y no tienen tope.
STASHES = ("", "personal", "grupo")


def _norm_stash(value) -> str:
    v = str(value or "").strip().lower()
    return v if v in ("personal", "grupo") else ""


def _inv_rows(conn, *, character_id=None, pet_id=None, campaign_id=None, stash=""):
    """Objetos de una zona. `campaign_id` trae el guardado del grupo, que no es
    de ningún personaje en particular."""
    if campaign_id is not None:
        return conn.execute(
            "SELECT * FROM inventory WHERE campaign_id=? AND character_id IS NULL "
            "AND pet_id IS NULL AND COALESCE(stash,'')='grupo' ORDER BY name",
            (campaign_id,)).fetchall()
    if character_id is not None:
        return conn.execute(
            "SELECT * FROM inventory WHERE character_id=? AND pet_id IS NULL "
            "AND COALESCE(stash,'')=? ORDER BY name", (character_id, stash)).fetchall()
    return conn.execute(
        "SELECT * FROM inventory WHERE pet_id=? AND COALESCE(stash,'')=? ORDER BY name",
        (pet_id, stash)).fetchall()


def _inv_serialize(r) -> dict:
    d = dict(r)
    d["categorias"] = json.loads(d.get("categorias") or "[]")
    d["stats"] = json.loads(d.get("stats") or "{}")
    d["contenedor"] = bool(d.get("contenedor"))
    d["equipado"] = bool(d.get("equipado"))
    d["stash"] = _norm_stash(d.get("stash"))
    return d


def _slots_de(r) -> int:
    """Slots que ocupa una entrada. Un paquete con usos (5 raciones) ocupa lo
    mismo hasta que se agota, no un slot por unidad."""
    return (r["slots"] or 0) * (r["cantidad"] or 1)


def carrying_capacity(size: str, fuerza: int, rows, bases=None) -> dict:
    """Capacidad, uso y sobrecarga. No bloquea: solo informa.

    Solo cuenta lo que la criatura lleva encima: lo **equipado** y que no esté
    dentro de un contenedor (eso va contra la capacidad del contenedor).

    `bases` es la tabla de base por tamaño de la campaña; sin ella, la del manual."""
    bases = bases or _SIZE_BASE
    base = bases.get(_norm_size(size), bases.get("Mediano", 6))
    encima = [r for r in rows if not r["parent_id"] and r["equipado"]]
    bonus = sum((r["capacity_bonus"] or 0) * (r["cantidad"] or 1) for r in encima)
    usado = sum(_slots_de(r) for r in encima)
    capacidad = base + int(fuerza or 0) + bonus
    return {"capacidad": capacidad, "usado": usado, "base": base,
            "fuerza": int(fuerza or 0), "bonus": bonus,
            "sobrecargado": usado > capacidad}


def _nest(rows) -> list:
    """Arma el árbol: los contenedores traen adentro lo que guardan, con su
    propio uso de espacio."""
    ser = {r["id"]: _inv_serialize(r) for r in rows}
    for d in ser.values():
        d["children"] = []
    top = []
    for r in rows:
        d = ser[r["id"]]
        padre = ser.get(r["parent_id"]) if r["parent_id"] else None
        (padre["children"] if padre else top).append(d)
    for d in ser.values():
        if d["contenedor"]:
            d["cont_usado"] = sum((c["slots"] or 0) * (c["cantidad"] or 1)
                                  for c in d["children"])
            d["cont_lleno"] = d["cont_usado"] > (d["contenedor_capacidad"] or 0)
    return top


def _bases_de(conn, campaign_id) -> dict:
    """Bases de carga por tamaño que configuró el DM de esa campaña."""
    if not campaign_id:
        return _SIZE_BASE
    return size_bases(get_config(conn, campaign_id))


def _char_capacity(conn, ch, rows, bases=None) -> dict:
    sheet = json.loads(ch["sheet"] or "{}")
    fuerza = (sheet.get("attributes") or {}).get("STR") or 0
    size = ch["size"] if "size" in ch.keys() else "Mediano"
    return carrying_capacity(size, fuerza, rows,
                             bases or _bases_de(conn, ch["campaign_id"]))


def _pet_capacity(conn, pet, rows, bases=None) -> dict:
    stats = json.loads(pet["stats"] or "{}")
    fuerza = (stats.get("physical") or {}).get("str") or 0
    return carrying_capacity(stats.get("size"), fuerza, rows, bases)


def _require_modulo(conn, ch, modulo="modulo_inventario"):
    """El DM puede apagar el inventario y el catálogo por separado."""
    if not ch["campaign_id"]:
        return
    if not get_config(conn, ch["campaign_id"])[modulo]:
        que = "el catálogo" if modulo == "modulo_catalogo" else "el inventario"
        raise HTTPException(400, f"El DM apagó {que} en esta campaña")


def _can_create_items(conn, ch, user) -> bool:
    """Permiso que el DM le da a un jugador para crear objetos propios."""
    m = conn.execute(
        "SELECT can_create_items FROM campaign_members WHERE campaign_id=? AND user_id=?",
        (ch["campaign_id"], user["id"]),
    ).fetchone()
    return bool(m and m["can_create_items"])


def _item_copy(it) -> dict:
    """Copia de un objeto del catálogo. Lo entregado no se altera si después el
    DM edita el catálogo (mismo criterio que las mascotas del bestiario)."""
    return {"name": it["name"], "kind": it["kind"] or "equipo", "desc": it["descripcion"],
            "cats": it["categorias"], "peso": it["peso"] or "", "slots": it["slots"],
            "cap_bonus": it["capacity_bonus"] or 0, "usos_max": it["usos_max"] or 0,
            "cont_cap": it["contenedor_capacidad"] or 0, "stats": it["stats"] or "{}",
            "item_id": it["id"]}


def _inv_insert(conn, d, *, character_id=None, pet_id=None, campaign_id=None,
                stash="", parent_id=None, cantidad=1, notas=""):
    cur = conn.execute(
        "INSERT INTO inventory (character_id, pet_id, campaign_id, stash, item_id, name, "
        "kind, descripcion, categorias, peso, slots, capacity_bonus, usos, usos_max, "
        "contenedor, contenedor_capacidad, stats, cantidad, notas, parent_id, equipado) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)",
        (character_id, pet_id, campaign_id, stash, d["item_id"], d["name"], d["kind"],
         d["desc"], d["cats"], d["peso"], d["slots"], d["cap_bonus"], d["usos_max"],
         d["usos_max"], 1 if d["cont_cap"] else 0, d["cont_cap"], d["stats"],
         max(1, cantidad), notas, parent_id),
    )
    return cur.lastrowid


def _assert_una_mochila(conn, dest: dict, *, exclude_id=None):
    """Solo se lleva **un contenedor encima**: una mochila, o una carreta si es
    una mascota. Tener dos sería espacio infinito gratis.

    En los guardados no aplica: ahí podés tener las que quieras."""
    if dest["stash"]:
        return
    sql = ("SELECT name FROM inventory WHERE COALESCE(stash,'')='' AND parent_id IS NULL "
           "AND contenedor=1 AND ")
    if dest["pet_id"]:
        sql += "pet_id=?"
        params = [dest["pet_id"]]
    else:
        sql += "character_id=? AND pet_id IS NULL"
        params = [dest["character_id"]]
    if exclude_id:
        sql += " AND id<>?"
        params.append(exclude_id)
    r = conn.execute(sql, params).fetchone()
    if r:
        quien = "La mascota ya lleva" if dest["pet_id"] else "Ya llevás"
        raise HTTPException(
            400, f"{quien} un contenedor encima ({r['name']}). Guardalo o pasalo "
                 "antes de llevar otro.")


def _dest_fields(ch, *, pet_id=None, stash="") -> dict:
    """A quién queda pegada una entrada según la zona. Los guardados son del
    personaje o del grupo: una mascota solo lleva cosas encima."""
    stash = _norm_stash(stash)
    if stash == "grupo":
        return {"character_id": None, "pet_id": None, "stash": "grupo"}
    if stash == "personal":
        return {"character_id": ch["id"], "pet_id": None, "stash": "personal"}
    if pet_id:
        return {"character_id": None, "pet_id": pet_id, "stash": ""}
    return {"character_id": ch["id"], "pet_id": None, "stash": ""}


def _place(conn, entry, *, character_id=None, pet_id=None, campaign_id=None,
           stash="", parent_id=None):
    """Deja una entrada en una zona. Lo que hay dentro de un contenedor viaja
    con él: la mochila se guarda llena."""
    conn.execute(
        "UPDATE inventory SET character_id=?, pet_id=?, campaign_id=?, stash=?, parent_id=? "
        "WHERE id=?",
        (character_id, pet_id, campaign_id, stash, parent_id, entry["id"]))
    conn.execute(
        "UPDATE inventory SET character_id=?, pet_id=?, campaign_id=?, stash=? "
        "WHERE parent_id=?",
        (character_id, pet_id, campaign_id, stash, entry["id"]))


def _add_inventory(conn, payload, *, character_id=None, pet_id=None, campaign_id=None,
                   owner_id=None, is_dm=False, can_create=False):
    """Alta de un objeto. Del catálogo (item_id) solo puede el DM: el jugador que
    quiere algo del catálogo lo agarra y lo paga (ver `take_from_catalog`)."""
    if payload.item_id:
        if not is_dm:
            raise HTTPException(403, "Solo el DM puede entregar objetos del catálogo")
        it = conn.execute("SELECT * FROM items WHERE id=?", (payload.item_id,)).fetchone()
        if not it:
            raise HTTPException(404, "Objeto no encontrado en el catálogo")
        d = _item_copy(it)
    else:
        if not (is_dm or can_create):
            raise HTTPException(403, "El DM no te habilitó a crear objetos")
        name = (payload.name or "").strip()
        if not name:
            raise HTTPException(400, "Poné un nombre al objeto")
        d = {"name": name, "kind": payload.kind or "equipo", "desc": payload.descripcion,
             "cats": json.dumps([c.strip().lower() for c in payload.categorias if c.strip()]),
             "peso": payload.peso, "slots": max(0, payload.slots),
             "cap_bonus": max(0, payload.capacity_bonus),
             "usos_max": max(0, payload.usos_max),
             "cont_cap": max(0, payload.contenedor_capacidad),
             "stats": json.dumps(payload.stats or {}), "item_id": None}
        if payload.save_to_catalog and is_dm and owner_id:
            d["item_id"] = conn.execute(
                "INSERT INTO items (owner_id, name, kind, descripcion, categorias, precio, "
                "peso, slots, capacity_bonus, usos_max, contenedor, contenedor_capacidad, "
                "stats) VALUES (?,?,?,?,?,0,?,?,?,?,?,?,?)",
                (owner_id, d["name"], d["kind"], d["desc"], d["cats"], d["peso"],
                 d["slots"], d["cap_bonus"], d["usos_max"],
                 1 if d["cont_cap"] else 0, d["cont_cap"], d["stats"]),
            ).lastrowid
    if d["cont_cap"] and not payload.parent_id:
        _assert_una_mochila(conn, {"character_id": character_id, "pet_id": pet_id,
                                   "stash": ""})
    return _inv_insert(conn, d, character_id=character_id, pet_id=pet_id,
                       campaign_id=campaign_id, parent_id=payload.parent_id,
                       cantidad=payload.cantidad, notas=payload.notas)


def _pay_marcos(conn, ch, total: int, cargados=None, opacos=None) -> dict:
    """Cobra `total` marcos. El jugador elige con cuáles paga; si no manda
    reparto, se van primero las opacas (guardarse la luz es lo sensato)."""
    marcos = ch["marcos"] or 0
    light = min(ch["marcos_light"] or 0, marcos)
    dun = marcos - light
    if total <= 0:
        return {"cargados": 0, "opacos": 0, "marcos": marcos, "marcos_light": light}
    if cargados is None and opacos is None:
        opacos = min(dun, total)
        cargados = total - opacos
    cargados, opacos = max(0, int(cargados or 0)), max(0, int(opacos or 0))
    if cargados + opacos != total:
        raise HTTPException(
            400, f"El reparto suma {cargados + opacos} y el precio es {total}")
    if cargados > light:
        raise HTTPException(400, f"No tenés {cargados} marcos cargados (tenés {light})")
    if opacos > dun:
        raise HTTPException(400, f"No tenés {opacos} marcos opacos (tenés {dun})")
    conn.execute("UPDATE characters SET marcos=?, marcos_light=? WHERE id=?",
                 (marcos - total, light - cargados, ch["id"]))
    return {"cargados": cargados, "opacos": opacos,
            "marcos": marcos - total, "marcos_light": light - cargados}


def character_inventory(conn, ch) -> dict:
    """Todo lo que rodea a un personaje: lo que lleva encima (con sus
    contenedores), lo de sus mascotas, su guardado y el del grupo."""
    cid = ch["id"]
    bases = _bases_de(conn, ch["campaign_id"])
    rows = _inv_rows(conn, character_id=cid)
    out = {"character": {"id": cid, "name": ch["name"],
                         "size": ch["size"] if "size" in ch.keys() else "Mediano",
                         "items": _nest(rows),
                         "capacity": _char_capacity(conn, ch, rows, bases),
                         "guardado": _nest(_inv_rows(conn, character_id=cid,
                                                     stash="personal"))},
           "pets": []}

    def _pet_block(pet, dueno=""):
        prows = _inv_rows(conn, pet_id=pet["id"])
        return {"id": pet["id"], "name": pet["name"], "items": _nest(prows),
                "compartida": bool(pet["compartida"]), "dueno": dueno,
                "capacity": _pet_capacity(conn, pet, prows, bases)}

    for pet in conn.execute("SELECT * FROM pets WHERE character_id=? ORDER BY name", (cid,)):
        out["pets"].append(_pet_block(pet))
    # Las mascotas de todos que trajo otro jugador: se manejan igual que las propias.
    out["compartidas"] = [
        _pet_block(p, p["dueno"])
        for p in conn.execute(
            "SELECT p.*, c2.name AS dueno FROM pets p JOIN characters c2 ON c2.id=p.character_id "
            "WHERE p.compartida=1 AND c2.campaign_id=? AND c2.id<>? ORDER BY p.name",
            (ch["campaign_id"], cid))
    ] if ch["campaign_id"] else []
    out["grupo"] = _nest(_inv_rows(conn, campaign_id=ch["campaign_id"])) \
        if ch["campaign_id"] else []
    # Compañeros de campaña, para pasarles cosas.
    out["aliados"] = [
        {"id": r["id"], "name": r["name"]}
        for r in conn.execute(
            "SELECT id, name FROM characters WHERE campaign_id=? AND id<>? ORDER BY name",
            (ch["campaign_id"], cid))
    ] if ch["campaign_id"] else []
    return out


@router.get("/{cid}/inventory")
def get_inventory(cid: int, user=Depends(current_user)):
    """Inventario del personaje y de cada mascota, con su capacidad de carga."""
    with db() as conn:
        ch = _owned_or_dm(conn, cid, user)
        _require_modulo(conn, ch)
        return character_inventory(conn, ch)


@router.post("/{cid}/inventory")
def add_inventory(cid: int, payload: InventoryIn, user=Depends(current_user)):
    with db() as conn:
        ch = _owned_or_dm(conn, cid, user)
        _require_modulo(conn, ch)
        is_dm = ch["dm_id"] == user["id"]
        _add_inventory(conn, payload, character_id=cid, campaign_id=ch["campaign_id"],
                       owner_id=ch["dm_id"], is_dm=is_dm,
                       can_create=_can_create_items(conn, ch, user))
    return {"ok": True}


@router.post("/{cid}/pets/{pid}/inventory")
def add_pet_inventory(cid: int, pid: int, payload: InventoryIn, user=Depends(current_user)):
    with db() as conn:
        ch = _owned_or_dm(conn, cid, user)
        _require_modulo(conn, ch)
        _pet_al_alcance(conn, ch, pid)
        is_dm = ch["dm_id"] == user["id"]
        _add_inventory(conn, payload, pet_id=pid, campaign_id=ch["campaign_id"],
                       owner_id=ch["dm_id"], is_dm=is_dm,
                       can_create=_can_create_items(conn, ch, user))
    return {"ok": True}


@router.post("/{cid}/inventory/take")
def take_from_catalog(cid: int, t: TakeIn, user=Depends(current_user)):
    """El jugador agarra un objeto del catálogo y lo paga.

    El precio es editable: puede ser un descuento del DM o un hallazgo (0, y no
    se le va ninguna esfera). El reparto entre esferas cargadas y opacas también
    lo elige él."""
    with db() as conn:
        ch = _owned_or_dm(conn, cid, user)
        # agarrar toca las dos puntas: sale del catálogo y entra al inventario
        _require_modulo(conn, ch, "modulo_catalogo")
        _require_modulo(conn, ch)
        is_dm = ch["dm_id"] == user["id"]
        it = conn.execute("SELECT * FROM items WHERE id=? AND owner_id=?",
                          (t.item_id, ch["dm_id"])).fetchone()
        # Lo oculto no existe para el jugador: ni siquiera se entera de que está.
        if not it or (it["secreto"] and not is_dm):
            raise HTTPException(404, "Objeto no encontrado en el catálogo")
        pet_id = _pet_al_alcance(conn, ch, t.pet_id)["id"] if t.pet_id else None
        cant = max(1, t.cantidad)
        precio = it["precio"] if t.precio is None else max(0, t.precio)
        total = precio * cant
        dest = _dest_fields(ch, pet_id=pet_id, stash=t.stash)
        if it["contenedor"] and not t.parent_id:
            _assert_una_mochila(conn, dest)
        pago = _pay_marcos(conn, ch, total, t.pago_cargados, t.pago_opacos)
        _inv_insert(conn, _item_copy(it), campaign_id=ch["campaign_id"],
                    parent_id=t.parent_id if not dest["stash"] else None,
                    cantidad=cant, **dest)
    return {"ok": True, "total": total, "pago": pago}


@router.post("/{cid}/inventory/{eid}/qty")
def inventory_qty(cid: int, eid: int, ch: InventoryQty, user=Depends(current_user)):
    """Ajusta la cantidad; al llegar a 0 se saca del inventario."""
    with db() as conn:
        _owned_or_dm(conn, cid, user)
        r = _inv_entry(conn, cid, eid)
        val = (r["cantidad"] or 1) + ch.delta
        if val <= 0:
            conn.execute("DELETE FROM inventory WHERE id=?", (eid,))
        else:
            conn.execute("UPDATE inventory SET cantidad=? WHERE id=?", (val, eid))
    return {"ok": True}


def _inv_entry(conn, cid: int, eid: int):
    """Una entrada que este personaje puede tocar: propia, de una mascota suya
    (o de una mascota de todos), o del guardado del grupo."""
    r = conn.execute(
        "SELECT inv.* FROM inventory inv LEFT JOIN pets p ON p.id=inv.pet_id "
        "WHERE inv.id=? AND (inv.character_id=? OR p.character_id=? OR "
        "  (p.compartida=1 AND p.character_id IN "
        "   (SELECT id FROM characters WHERE campaign_id="
        "    (SELECT campaign_id FROM characters WHERE id=?))) OR "
        "  (inv.character_id IS NULL AND inv.pet_id IS NULL AND inv.campaign_id="
        "   (SELECT campaign_id FROM characters WHERE id=?)))",
        (eid, cid, cid, cid, cid)).fetchone()
    if not r:
        raise HTTPException(404, "Objeto no encontrado en el inventario")
    return r


@router.post("/{cid}/inventory/{eid}/equip")
def toggle_equipado(cid: int, eid: int, user=Depends(current_user)):
    """Equipar/desequipar. Lo desequipado (y lo que lleve adentro, si es un
    contenedor) deja de contar para la capacidad: quedó en el suelo o en la carreta."""
    with db() as conn:
        _owned_or_dm(conn, cid, user)
        r = _inv_entry(conn, cid, eid)
        val = 0 if r["equipado"] else 1
        conn.execute("UPDATE inventory SET equipado=? WHERE id=?", (val, eid))
    return {"ok": True, "equipado": bool(val)}


@router.post("/{cid}/inventory/{eid}/move")
def move_inventory(cid: int, eid: int, m: InventoryMove, user=Depends(current_user)):
    """Mete o saca un objeto de un contenedor (mochila, carreta del chull…)."""
    with db() as conn:
        ch = _owned_or_dm(conn, cid, user)
        r = _inv_entry(conn, cid, eid)
        if m.parent_id:
            if m.parent_id == eid:
                raise HTTPException(400, "Un contenedor no puede guardarse a sí mismo")
            parent = _inv_entry(conn, cid, m.parent_id)
            if not parent["contenedor"]:
                raise HTTPException(400, "Ese objeto no es un contenedor")
            if r["contenedor"]:
                raise HTTPException(400, "No se puede meter un contenedor dentro de otro")
            # El objeto pasa a estar donde está el contenedor: así se carga la
            # carreta del chull con cosas que llevaba el personaje.
            _place(conn, r, character_id=parent["character_id"], pet_id=parent["pet_id"],
                   campaign_id=parent["campaign_id"] or ch["campaign_id"],
                   stash=_norm_stash(parent["stash"]), parent_id=parent["id"])
        else:
            # sacarlo lo devuelve a las manos del personaje
            _place(conn, r, character_id=cid, campaign_id=ch["campaign_id"])
    return {"ok": True}


@router.post("/{cid}/inventory/{eid}/stash")
def stash_inventory(cid: int, eid: int, s: InventoryStash, user=Depends(current_user)):
    """Mueve un objeto entre lo que se lleva encima, el guardado propio y el del
    grupo. Los guardados no tienen tope: es lo que se tiene pero no se carga."""
    with db() as conn:
        ch = _owned_or_dm(conn, cid, user)
        r = _inv_entry(conn, cid, eid)
        stash = _norm_stash(s.stash)
        if stash == "grupo" and not ch["campaign_id"]:
            raise HTTPException(400, "El personaje no está en ninguna campaña")
        dest = _dest_fields(ch, pet_id=r["pet_id"], stash=stash)
        if r["contenedor"]:
            _assert_una_mochila(conn, dest, exclude_id=r["id"])
        _place(conn, r, campaign_id=ch["campaign_id"], **dest)
    return {"ok": True, "stash": stash}


@router.post("/{cid}/inventory/{eid}/transfer")
def transfer_inventory(cid: int, eid: int, t: InventoryTransfer, user=Depends(current_user)):
    """Le pasa un objeto a otro personaje de la campaña (o a una mascota suya).
    Lo que va dentro de un contenedor se pasa con él."""
    with db() as conn:
        ch = _owned_or_dm(conn, cid, user)
        r = _inv_entry(conn, cid, eid)
        if not ch["campaign_id"]:
            raise HTTPException(400, "El personaje no está en ninguna campaña")
        if t.pet_id:
            dest = conn.execute(
                "SELECT p.id, ch.id AS char_id, ch.campaign_id, ch.name FROM pets p "
                "JOIN characters ch ON ch.id=p.character_id WHERE p.id=?", (t.pet_id,)).fetchone()
            if not dest or dest["campaign_id"] != ch["campaign_id"]:
                raise HTTPException(404, "Esa mascota no está en tu campaña")
            if r["contenedor"]:
                _assert_una_mochila(conn, {"character_id": None, "pet_id": dest["id"],
                                           "stash": ""}, exclude_id=r["id"])
            _place(conn, r, pet_id=dest["id"], campaign_id=ch["campaign_id"])
            return {"ok": True, "a": dest["name"]}
        dest = conn.execute("SELECT * FROM characters WHERE id=?", (t.character_id or 0,)).fetchone()
        if not dest or dest["campaign_id"] != ch["campaign_id"]:
            raise HTTPException(404, "Ese personaje no está en tu campaña")
        campos = _dest_fields(dest, stash=t.stash)
        if r["contenedor"]:
            _assert_una_mochila(conn, campos, exclude_id=r["id"])
        _place(conn, r, campaign_id=ch["campaign_id"], **campos)
    return {"ok": True, "a": dest["name"]}


@router.post("/{cid}/inventory/{eid}/use")
def use_inventory(cid: int, eid: int, ch: InventoryQty, user=Depends(current_user)):
    """Gasta (o repone) una dosis/carga. Al agotarse, el objeto se va."""
    with db() as conn:
        _owned_or_dm(conn, cid, user)
        r = _inv_entry(conn, cid, eid)
        if not (r["usos_max"] or 0):
            raise HTTPException(400, "Ese objeto no tiene dosis ni cargas")
        val = max(0, min(r["usos_max"], (r["usos"] or 0) + ch.delta))
        if val <= 0:
            # se acabaron: si había más de una unidad, arranca otra
            resto = (r["cantidad"] or 1) - 1
            if resto > 0:
                conn.execute("UPDATE inventory SET cantidad=?, usos=? WHERE id=?",
                             (resto, r["usos_max"], eid))
            else:
                conn.execute("DELETE FROM inventory WHERE id=?", (eid,))
            return {"ok": True, "usos": 0, "agotado": True}
        conn.execute("UPDATE inventory SET usos=? WHERE id=?", (val, eid))
    return {"ok": True, "usos": val, "agotado": False}


@router.delete("/{cid}/inventory/{eid}")
def delete_inventory(cid: int, eid: int, user=Depends(current_user)):
    with db() as conn:
        _owned_or_dm(conn, cid, user)
        r = _inv_entry(conn, cid, eid)
        conn.execute("DELETE FROM inventory WHERE id=?", (r["id"],))
    return {"ok": True}


@router.put("/{cid}/size")
def set_size(cid: int, s: SizeIn, user=Depends(current_user)):
    size = _norm_size(s.size)
    with db() as conn:
        _owned_or_dm(conn, cid, user)
        conn.execute("UPDATE characters SET size=? WHERE id=?", (size, cid))
    return {"ok": True, "size": size}


@router.post("/{cid}/marcos/charge_inv")
async def character_charge_inv(cid: int, user=Depends(current_user)):
    """Cargar investidura: llena el medidor 1:1 apagando marcos cargados."""
    with db() as conn:
        r = _owned(conn, cid, user)
        light = r["marcos_light"] or 0
        inv = r["inv"] if r["inv"] is not None else r["inv_max"]
        amount = max(0, min(r["inv_max"] - inv, light))
        new_inv = inv + amount
        new_light = light - amount
        conn.execute("UPDATE characters SET inv=?, marcos_light=? WHERE id=?", (new_inv, new_light, cid))
        campaign_id = r["campaign_id"]
    await _sync_combat(campaign_id, {"inv": new_inv}, char_id=cid)
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
