"""API: Campañas, membresías e invitaciones."""

import json
import random
import re

from fastapi import APIRouter, Depends, HTTPException

from .. import roshar
from ..access import campaign_or_404, require_access, require_dm
from ..auth import current_user
from ..config import (CONFIG_DEFAULTS, VER_MODOS, coerce, get_config,
                      player_config, sane, save_config)
from ..database import db
from ..models import (CalendarNoteIn, CalendarSet, CampaignIn, ConfigIn, DaysIn,
                      InviteIn, LongRestIn, MarcosChange)
from ..state import mask_stats

router = APIRouter(prefix="/api", tags=["campaigns"])

# ── Altas tormentas: cada 10±2 días (8-12), en un momento al azar ──
STORM_MOMENTS = ["al amanecer", "por la mañana", "al mediodía",
                 "por la tarde", "al anochecer", "de madrugada"]

# Los parámetros ajustables viven en app/config.py (los consultan varios routers).
_get_config = get_config


def _actual(valor, maximo):
    """NULL en la columna significa 'está al máximo' (nunca se le tocó el stat)."""
    return maximo if valor is None else valor


def _new_target(cfg: dict) -> int:
    return random.randint(cfg["storm_min"], cfg["storm_max"])


def _get_storm(conn, cid: int):
    row = conn.execute("SELECT * FROM storm_tracker WHERE campaign_id=?", (cid,)).fetchone()
    if not row:
        cfg = _get_config(conn, cid)
        conn.execute(
            "INSERT INTO storm_tracker (campaign_id, day, target, moment) VALUES (?,0,?,?)",
            (cid, _new_target(cfg), random.choice(STORM_MOMENTS)),
        )
        row = conn.execute("SELECT * FROM storm_tracker WHERE campaign_id=?", (cid,)).fetchone()
    return row


def _advance_storm(conn, cid: int) -> dict:
    """Pasa un día. Si toca, cae la tormenta y arranca un ciclo nuevo."""
    # Días absolutos: `day` se reinicia con cada tormenta, así que el restock de
    # las tiendas necesita su propio contador que nunca vuelve atrás.
    conn.execute("UPDATE campaigns SET day_count=COALESCE(day_count,0)+1 WHERE id=?", (cid,))
    row = _get_storm(conn, cid)
    day = row["day"] + 1
    stormed = False
    storm_day, storm_moment = row["target"], row["moment"]
    if day >= row["target"]:
        stormed = True
        day = 0
        cfg = _get_config(conn, cid)
        conn.execute(
            "UPDATE storm_tracker SET day=?, target=?, moment=? WHERE campaign_id=?",
            (day, _new_target(cfg), random.choice(STORM_MOMENTS), cid),
        )
    else:
        conn.execute("UPDATE storm_tracker SET day=? WHERE campaign_id=?", (day, cid))
    return {"stormed": stormed, "storm_day": storm_day, "storm_moment": storm_moment}


# ── Marcos: recarga en tormenta y descarga con el paso de los días ──

def _marcos_tick(conn, cid: int, stormed: bool, day: int):
    """Aplica al día que avanza: la tormenta recarga todos los marcos; a partir del
    día de inicio, sin tormenta, cada marco cargado se apaga con probabilidad
    creciente (pocos al principio, todos para el día de apagado total)."""
    rows = conn.execute(
        "SELECT id, marcos, marcos_light FROM characters WHERE campaign_id=?", (cid,)
    ).fetchall()
    if stormed:
        for r in rows:
            if (r["marcos"] or 0) != (r["marcos_light"] or 0):
                conn.execute("UPDATE characters SET marcos_light=marcos WHERE id=?", (r["id"],))
        return
    cfg = _get_config(conn, cid)
    start, full = cfg["discharge_start"], cfg["discharge_full"]
    if day < start:
        return
    span = full - (start - 1)
    base = min(1.0, max(0.0, (day - (start - 1)) / span))
    p = base ** cfg["discharge_curve"]   # curva>1 => arranca más lento
    for r in rows:
        light = r["marcos_light"] or 0
        if light <= 0:
            continue
        lost = sum(1 for _ in range(light) if random.random() < p)
        if lost:
            conn.execute("UPDATE characters SET marcos_light=? WHERE id=?", (light - lost, r["id"]))


# ── Calendario rosharano ───────────────────────────────────

MAX_SALTO = 500      # tope de días por avance (un año); evita saltos accidentales


def _get_calendar(conn, cid: int):
    row = conn.execute("SELECT * FROM campaign_calendar WHERE campaign_id=?",
                       (cid,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO campaign_calendar (campaign_id, day_index) VALUES (?,?)",
            (cid, roshar.DEFAULT_INDEX),
        )
        row = conn.execute("SELECT * FROM campaign_calendar WHERE campaign_id=?",
                           (cid,)).fetchone()
    return row


def _set_calendar(conn, cid: int, idx: int) -> int:
    idx = roshar.clamp_index(idx)
    _get_calendar(conn, cid)
    conn.execute("UPDATE campaign_calendar SET day_index=? WHERE campaign_id=?",
                 (idx, cid))
    return idx


def _notes(conn, cid: int, is_dm: bool) -> list:
    """Notas y pines del calendario. Las secretas son solo del DM."""
    rows = conn.execute(
        "SELECT n.*, u.username FROM calendar_notes n "
        "LEFT JOIN users u ON u.id=n.user_id "
        "WHERE n.campaign_id=? ORDER BY n.day_index, n.id", (cid,),
    ).fetchall()
    out = []
    for r in rows:
        if r["secreto"] and not is_dm:
            continue
        out.append({"id": r["id"], "day_index": r["day_index"],
                    "texto": r["texto"], "color": r["color"] or "",
                    "secreto": bool(r["secreto"]),
                    "user_id": r["user_id"], "username": r["username"] or "—"})
    return out


def _calendar_view(conn, cid: int, cfg: dict, is_dm: bool, user_id: int) -> dict:
    """Lo que ve del calendario quien lo pide.

    `enabled` en false = el DM no usa el calendario en esta campaña. Un jugador
    tampoco lo ve si el DM lo dejó solo para él (`calendario_visible`)."""
    idx = _get_calendar(conn, cid)["day_index"]
    visible = bool(cfg["modulo_calendario"]) and (is_dm or cfg["calendario_visible"])
    # Si el DM se lo guarda para él, la fecha ni se manda: no hay nada que espiar.
    return {
        "enabled": bool(cfg["modulo_calendario"]),
        "visible": visible,
        "is_dm": is_dm,
        "can_edit": visible and (is_dm or bool(cfg["calendario_editable"])),
        "user_id": user_id,
        "salto_dias": cfg["salto_dias"],
        "today": roshar.describe(idx) if visible else None,
        "notes": _notes(conn, cid, is_dm) if visible else [],
    }


def _cal_fields(conn, cid: int) -> dict:
    """La fecha actual desarmada, para el modal de ajustes del DM."""
    d = roshar.describe(_get_calendar(conn, cid)["day_index"])
    return {"cal_year": d["year"], "cal_month": d["month"], "cal_week": d["week"],
            "cal_day": d["day"], "cal_date": d,
            "cal_months": roshar.MONTHS, "cal_weekdays": roshar.WEEKDAYS}


def _pass_days(conn, cid: int, days: int = 1) -> dict:
    """Pasan `days` días para toda la campaña: calendario, ciclo de tormentas y
    descarga de marcos. Es el único lugar donde el tiempo avanza."""
    days = max(1, min(MAX_SALTO, int(days or 1)))
    storms = []
    for _ in range(days):
        st = _advance_storm(conn, cid)
        _marcos_tick(conn, cid, st["stormed"], _get_storm(conn, cid)["day"])
        if st["stormed"]:
            storms.append(st)
    idx = _set_calendar(conn, cid, _get_calendar(conn, cid)["day_index"] + days)
    out = {"days": days, "storms": storms, "stormed": bool(storms),
           "date": roshar.describe(idx)}
    if storms:      # compatibilidad con el aviso de un solo día
        out["storm_day"] = storms[-1]["storm_day"]
        out["storm_moment"] = storms[-1]["storm_moment"]
    return out


# ── DM: campañas propias ───────────────────────────────────

@router.get("/campaigns")
def my_campaigns_as_dm(user=Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT c.*, "
            "(SELECT COUNT(*) FROM campaign_members m WHERE m.campaign_id=c.id AND m.status='accepted') AS players "
            "FROM campaigns c WHERE c.dm_id=? ORDER BY c.created_at DESC",
            (user["id"],),
        ).fetchall()
        return [dict(r) for r in rows]


@router.post("/campaigns")
def create_campaign(c: CampaignIn, user=Depends(current_user)):
    name = c.name.strip()
    if not name:
        raise HTTPException(400, "Poné un nombre a la campaña")
    if c.system not in ("cosmere", "dnd"):
        raise HTTPException(400, "Sistema desconocido")
    with db() as conn:
        cur = conn.execute("INSERT INTO campaigns (name, dm_id, system) VALUES (?,?,?)",
                           (name, user["id"], c.system))
        return {"id": cur.lastrowid, "name": name, "system": c.system}


@router.delete("/campaigns/{cid}")
def delete_campaign(cid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        conn.execute("DELETE FROM campaigns WHERE id=?", (cid,))  # cascada borra todo lo asociado
    return {"ok": True}


@router.get("/campaigns/{cid}")
def get_campaign(cid: int, user=Depends(current_user)):
    """Datos básicos de la campaña (para el DM). Incluye si el usuario es el DM."""
    with db() as conn:
        c = campaign_or_404(conn, cid)
        return {"id": c["id"], "name": c["name"], "system": c["system"] or "cosmere",
                "is_dm": c["dm_id"] == user["id"]}


# ── Miembros (DM) ──────────────────────────────────────────

@router.get("/campaigns/{cid}/members")
def list_members(cid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        rows = conn.execute(
            "SELECT m.user_id, m.status, m.character_id, m.can_create_items, "
            "u.username, ch.name AS character_name, "
            "ch.has_pdf, ch.has_image, ch.marcos, ch.marcos_light, ch.sheet, "
            "ch.vida, ch.vida_max, ch.focus, ch.focus_max, ch.inv, ch.inv_max, "
            "ch.statuses, ch.injuries, ch.dnd_resources "
            "FROM campaign_members m JOIN users u ON u.id=m.user_id "
            "LEFT JOIN characters ch ON ch.id=m.character_id "
            "WHERE m.campaign_id=? ORDER BY u.username",
            (cid,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["can_create_items"] = bool(d.get("can_create_items"))
            if d.get("character_id"):
                sheet = json.loads(d.get("sheet") or "{}")
                d["clase"] = sheet.get("paths") or ""
                d["nivel"] = sheet.get("level") or ""
                d["statuses"] = json.loads(d.get("statuses") or "[]")
                d["injuries"] = json.loads(d.get("injuries") or "[]")
                d["dnd"] = json.loads(d.get("dnd_resources") or "{}")
                d["has_pdf"] = bool(d.get("has_pdf"))
                d["has_image"] = bool(d.get("has_image"))
            d.pop("sheet", None)
            d.pop("dnd_resources", None)
            out.append(d)
        return out


@router.post("/campaigns/{cid}/invite")
def invite(cid: int, inv: InviteIn, user=Depends(current_user)):
    uname = inv.username.strip()
    with db() as conn:
        require_dm(conn, cid, user)
        target = conn.execute("SELECT id FROM users WHERE username=?", (uname,)).fetchone()
        if not target:
            raise HTTPException(404, "No existe un usuario con ese nombre")
        if target["id"] == user["id"]:
            raise HTTPException(400, "No podés invitarte a tu propia campaña")
        exists = conn.execute(
            "SELECT status FROM campaign_members WHERE campaign_id=? AND user_id=?",
            (cid, target["id"]),
        ).fetchone()
        if exists:
            raise HTTPException(400, "Ese jugador ya está invitado o es miembro")
        conn.execute(
            "INSERT INTO campaign_members (campaign_id, user_id, status) VALUES (?,?, 'invited')",
            (cid, target["id"]),
        )
    return {"ok": True}


@router.delete("/campaigns/{cid}/members/{uid}")
def kick(cid: int, uid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        # El personaje pertenece a la campaña: al echar al jugador se elimina.
        conn.execute("DELETE FROM characters WHERE campaign_id=? AND owner_id=?", (cid, uid))
        conn.execute("DELETE FROM campaign_members WHERE campaign_id=? AND user_id=?", (cid, uid))
    return {"ok": True}


@router.get("/campaigns/{cid}/members/{uid}/sheet")
def member_sheet(cid: int, uid: int, user=Depends(current_user)):
    """El DM ve la ficha del personaje que trajo un jugador."""
    with db() as conn:
        require_dm(conn, cid, user)
        m = conn.execute(
            "SELECT character_id FROM campaign_members WHERE campaign_id=? AND user_id=?",
            (cid, uid),
        ).fetchone()
        if not m or not m["character_id"]:
            raise HTTPException(404, "Ese jugador todavía no eligió personaje")
        ch = conn.execute("SELECT * FROM characters WHERE id=?", (m["character_id"],)).fetchone()
        if not ch:
            raise HTTPException(404, "Personaje no encontrado")
        d = dict(ch)
        d["statuses"] = json.loads(d.get("statuses") or "[]")
        d["sheet"] = json.loads(d.get("sheet") or "{}")
        return d


def _member_char(conn, cid: int, uid: int):
    m = conn.execute(
        "SELECT ch.* FROM campaign_members m JOIN characters ch ON ch.id=m.character_id "
        "WHERE m.campaign_id=? AND m.user_id=?",
        (cid, uid),
    ).fetchone()
    if not m:
        raise HTTPException(404, "Ese jugador todavía no tiene personaje")
    return m


@router.post("/campaigns/{cid}/members/{uid}/marcos")
def dm_member_marcos(cid: int, uid: int, ch: MarcosChange, user=Depends(current_user)):
    """El DM agrega/saca marcos (total) a un jugador. Al reducir, opacos primero."""
    with db() as conn:
        require_dm(conn, cid, user)
        r = _member_char(conn, cid, uid)
        total = max(0, (r["marcos"] or 0) + ch.delta)
        light = min(r["marcos_light"] or 0, total)
        conn.execute("UPDATE characters SET marcos=?, marcos_light=? WHERE id=?", (total, light, r["id"]))
    return {"ok": True, "marcos": total, "marcos_light": light}


@router.post("/campaigns/{cid}/members/{uid}/marcos/light")
def dm_member_marcos_light(cid: int, uid: int, ch: MarcosChange, user=Depends(current_user)):
    """El DM carga (delta>0) o apaga (delta<0) marcos de un jugador."""
    with db() as conn:
        require_dm(conn, cid, user)
        r = _member_char(conn, cid, uid)
        total = r["marcos"] or 0
        light = max(0, min(total, (r["marcos_light"] or 0) + ch.delta))
        conn.execute("UPDATE characters SET marcos_light=? WHERE id=?", (light, r["id"]))
    return {"ok": True, "marcos": total, "marcos_light": light}


# ── Mascotas disponibles: enemigos del bestiario que el DM habilita por campaña ──

@router.get("/campaigns/{cid}/pet-options")
def list_pet_options(cid: int, user=Depends(current_user)):
    """Enemigos habilitados como mascota en esta campaña. Lo ven el DM y los
    miembros (el jugador elige de acá)."""
    with db() as conn:
        require_access(conn, cid, user)
        rows = conn.execute(
            "SELECT e.id, e.name, e.clase, e.tipo, e.vida_max, e.focus_max, e.inv_max, "
            "e.faction_color, e.acciones, e.stats "
            "FROM campaign_pet_options po JOIN enemies e ON e.id = po.enemy_id "
            "WHERE po.campaign_id=? ORDER BY e.name",
            (cid,),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["acciones"] = json.loads(d.get("acciones") or "[]")
            d["stats"] = json.loads(d.get("stats") or "{}")
            out.append(d)
        return out


@router.post("/campaigns/{cid}/pet-options/{eid}")
def add_pet_option(cid: int, eid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        owned = conn.execute(
            "SELECT 1 FROM enemies WHERE id=? AND owner_id=?", (eid, user["id"])
        ).fetchone()
        if not owned:
            raise HTTPException(404, "Enemigo no encontrado en tu bestiario")
        conn.execute(
            "INSERT OR IGNORE INTO campaign_pet_options (campaign_id, enemy_id) VALUES (?,?)",
            (cid, eid),
        )
    return {"ok": True}


@router.delete("/campaigns/{cid}/pet-options/{eid}")
def remove_pet_option(cid: int, eid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        conn.execute(
            "DELETE FROM campaign_pet_options WHERE campaign_id=? AND enemy_id=?", (cid, eid)
        )
    return {"ok": True}


@router.post("/campaigns/{cid}/members/{uid}/can-create-items")
def toggle_can_create_items(cid: int, uid: int, user=Depends(current_user)):
    """Habilita/deshabilita que ese jugador cree objetos en su inventario."""
    with db() as conn:
        require_dm(conn, cid, user)
        m = conn.execute(
            "SELECT can_create_items FROM campaign_members WHERE campaign_id=? AND user_id=?",
            (cid, uid)).fetchone()
        if not m:
            raise HTTPException(404, "Ese jugador no está en la campaña")
        val = 0 if m["can_create_items"] else 1
        conn.execute(
            "UPDATE campaign_members SET can_create_items=? WHERE campaign_id=? AND user_id=?",
            (val, cid, uid))
    return {"ok": True, "can_create_items": bool(val)}


@router.get("/campaigns/{cid}/party")
def campaign_party(cid: int, user=Depends(current_user)):
    """Jugadores aceptados con el nivel de su personaje (para calcular dificultad)."""
    with db() as conn:
        require_dm(conn, cid, user)
        rows = conn.execute(
            "SELECT u.username, ch.name AS character_name, ch.sheet "
            "FROM campaign_members m JOIN users u ON u.id=m.user_id "
            "JOIN characters ch ON ch.id=m.character_id "
            "WHERE m.campaign_id=? AND m.status='accepted' AND m.character_id IS NOT NULL "
            "ORDER BY u.username",
            (cid,),
        ).fetchall()
        out = []
        for r in rows:
            sheet = json.loads(r["sheet"] or "{}")
            level = 0
            m = re.search(r"\d+", str(sheet.get("level", "")))
            if m:
                level = int(m.group())
            out.append({"username": r["username"], "character_name": r["character_name"], "level": level})
        return out


@router.get("/campaigns/{cid}/roster")
def campaign_roster(cid: int, user=Depends(current_user)):
    """Estado en vivo de los personajes de la campaña (para gestionar fuera de
    combate y ver a los demás). Accesible al DM y a cualquier miembro aceptado."""
    with db() as conn:
        c, is_dm = require_access(conn, cid, user)
        cfg = _get_config(conn, cid)
        rows = conn.execute(
            "SELECT m.user_id, m.can_create_items, u.username, ch.* FROM campaign_members m "
            "JOIN users u ON u.id=m.user_id "
            "JOIN characters ch ON ch.id=m.character_id "
            "WHERE m.campaign_id=? AND m.status='accepted' AND m.character_id IS NOT NULL "
            "ORDER BY u.username",
            (cid,),
        ).fetchall()
        members = []
        for r in rows:
            pets = [
                {"id": p["id"], "name": p["name"], "char_id": r["id"],
                 "compartida": bool(p["compartida"]),
                 "vida": _actual(p["vida"], p["vida_max"]), "vida_max": p["vida_max"],
                 "focus": _actual(p["focus"], p["focus_max"]), "focus_max": p["focus_max"],
                 "inv": _actual(p["inv"], p["inv_max"]), "inv_max": p["inv_max"],
                 "statuses": json.loads(p["statuses"] or "[]"),
                 "stats": json.loads(p["stats"] or "{}"),
                 "acciones": json.loads(p["acciones"] or "[]")}
                for p in conn.execute("SELECT * FROM pets WHERE character_id=? ORDER BY name", (r["id"],))
            ]
            # De los demás jugadores se ve lo que el DM habilitó; lo propio y lo
            # que ve el DM va entero. Las mascotas de todos también: si cualquiera
            # las maneja, cualquiera les ve los números.
            ajeno = not is_dm and r["user_id"] != user["id"]
            if ajeno:
                pets = [p if p["compartida"] else mask_stats(p, cfg, "aliados")
                        for p in pets]
            char = {
                "id": r["id"], "name": r["name"],
                # NULL en la columna = está al máximo (nunca se le tocó el stat)
                "vida": _actual(r["vida"], r["vida_max"]), "vida_max": r["vida_max"],
                "focus": _actual(r["focus"], r["focus_max"]), "focus_max": r["focus_max"],
                "inv": _actual(r["inv"], r["inv_max"]), "inv_max": r["inv_max"],
                "statuses": json.loads(r["statuses"] or "[]"),
                "injuries": json.loads(r["injuries"] or "[]"),
                "sheet": json.loads(r["sheet"] or "{}"),
                "has_pdf": bool(r["has_pdf"]),
                "has_image": bool(r["has_image"]),
                "marcos": r["marcos"] or 0,
                "marcos_light": r["marcos_light"] or 0,
                "dnd": json.loads(r["dnd_resources"] or "{}"),
            }
            if ajeno:
                char = mask_stats(char, cfg, "aliados")
            members.append({
                "user_id": r["user_id"], "username": r["username"],
                "can_create_items": bool(r["can_create_items"]),
                "character": char,
                "pets": pets,
            })
        return {"system": c["system"] or "cosmere", "members": members,
                "config": player_config(cfg)}


@router.post("/campaigns/{cid}/long_rest")
def long_rest(cid: int, payload: LongRestIn, user=Depends(current_user)):
    """Descanso largo (DM): cura a full a los personajes aceptados (y sus mascotas)
    y baja en 1 los días de sus heridas; las permanentes no cambian. El DM puede
    excluir jugadores con `exclude` (lista de user_id)."""
    excl = set(payload.exclude or [])
    with db() as conn:
        c = require_dm(conn, cid, user)
        is_dnd = (c["system"] or "cosmere") == "dnd"
        chars = conn.execute(
            "SELECT m.user_id, ch.* FROM campaign_members m JOIN characters ch ON ch.id=m.character_id "
            "WHERE m.campaign_id=? AND m.status='accepted' AND m.character_id IS NOT NULL",
            (cid,),
        ).fetchall()
        done = 0
        for ch in chars:
            if ch["user_id"] in excl:
                continue
            done += 1
            if is_dnd:
                # D&D: cura vida y limpia estados (focus/inv no se usan). Recupera
                # todos los spell slots y los contadores de descanso largo o corto.
                conn.execute(
                    "UPDATE characters SET vida=vida_max, statuses='[]' WHERE id=?",
                    (ch["id"],),
                )
                d = json.loads(ch["dnd_resources"] or "{}")
                for slot in (d.get("slots") or {}).values():
                    slot["cur"] = slot.get("max", 0)
                for k in (d.get("counters") or []):
                    if k.get("recovery") in ("long", "short"):
                        k["cur"] = k.get("max", 0)
                conn.execute("UPDATE characters SET dnd_resources=? WHERE id=?",
                             (json.dumps(d), ch["id"]))
            else:
                # El descanso ya NO recarga investidura: el jugador la carga cuando quiere
                # desde sus marcos. Sí cura vida/focus y limpia estados.
                conn.execute(
                    "UPDATE characters SET vida=vida_max, focus=focus_max, statuses='[]' WHERE id=?",
                    (ch["id"],),
                )
            conn.execute(
                "UPDATE pets SET vida=vida_max, focus=focus_max, inv=inv_max, statuses='[]' WHERE character_id=?",
                (ch["id"],),
            )
            inj = json.loads(ch["injuries"] or "[]")
            kept = []
            for it in inj:
                if it.get("permanent"):
                    kept.append(it)
                    continue
                it["days"] = it.get("days", 0) - 1
                if it["days"] >= 0:      # al bajar de 0, la herida se curó
                    kept.append(it)
            conn.execute("UPDATE characters SET injuries=? WHERE id=?", (json.dumps(kept), ch["id"]))
        if is_dnd:
            return {"ok": True, "characters": done}
        storm = _pass_days(conn, cid, 1)    # el día pasa para todos, con o sin descanso
        state = _storm_view(_get_storm(conn, cid), True, _get_config(conn, cid))
    return {"ok": True, "characters": done, "storm": storm, "state": state}


@router.post("/campaigns/{cid}/short_rest")
def short_rest(cid: int, payload: LongRestIn, user=Depends(current_user)):
    """Descanso corto (DM, solo D&D): recupera únicamente los contadores con
    recuperación 'short'. No cura vida ni devuelve spell slots."""
    excl = set(payload.exclude or [])
    with db() as conn:
        c = require_dm(conn, cid, user)
        if (c["system"] or "cosmere") != "dnd":
            raise HTTPException(400, "El descanso corto solo existe en campañas de D&D")
        chars = conn.execute(
            "SELECT m.user_id, ch.id, ch.dnd_resources FROM campaign_members m "
            "JOIN characters ch ON ch.id=m.character_id "
            "WHERE m.campaign_id=? AND m.status='accepted' AND m.character_id IS NOT NULL",
            (cid,),
        ).fetchall()
        done = 0
        for ch in chars:
            if ch["user_id"] in excl:
                continue
            done += 1
            d = json.loads(ch["dnd_resources"] or "{}")
            for k in (d.get("counters") or []):
                if k.get("recovery") == "short":
                    k["cur"] = k.get("max", 0)
            conn.execute("UPDATE characters SET dnd_resources=? WHERE id=?",
                         (json.dumps(d), ch["id"]))
    return {"ok": True, "characters": done}


def _storm_view(row, is_dm: bool, cfg: dict) -> dict:
    """El DM ve el día y momento exactos; los jugadores solo la barra.

    `enabled` en false = el DM apagó el tracker: la barra no se dibuja."""
    base = {"day": row["day"], "min": cfg["storm_min"], "max": cfg["storm_max"],
            "enabled": cfg["modulo_tormentas"]}
    if is_dm:
        base["target"] = row["target"]
        base["moment"] = row["moment"]
    return base


@router.get("/campaigns/{cid}/storm")
def get_storm(cid: int, user=Depends(current_user)):
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
        row = _get_storm(conn, cid)
        return _storm_view(row, is_dm, _get_config(conn, cid))


@router.post("/campaigns/{cid}/storm/advance")
def advance_storm(cid: int, payload: DaysIn | None = None, user=Depends(current_user)):
    """El DM adelanta días sueltos (viaje, etc.) sin descanso largo.

    Sin cuerpo pasa un día; con `{"days": n}` pasa n de una (la semana rosharana
    son 5, pero el número lo elige el DM en los ajustes)."""
    days = payload.days if payload else 1
    with db() as conn:
        require_dm(conn, cid, user)
        cfg = _get_config(conn, cid)
        storm = _pass_days(conn, cid, days)
        state = _storm_view(_get_storm(conn, cid), True, cfg)
    return {"ok": True, "storm": storm, "state": state, "date": storm["date"]}


@router.post("/campaigns/{cid}/storm/reset")
def reset_storm(cid: int, user=Depends(current_user)):
    """El DM reinicia el ciclo (nuevo día y momento al azar)."""
    with db() as conn:
        require_dm(conn, cid, user)
        _get_storm(conn, cid)   # asegura que exista la fila
        cfg = _get_config(conn, cid)
        conn.execute(
            "UPDATE storm_tracker SET day=0, target=?, moment=? WHERE campaign_id=?",
            (_new_target(cfg), random.choice(STORM_MOMENTS), cid),
        )
        row = _get_storm(conn, cid)
    return {"ok": True, "state": _storm_view(row, True, cfg)}


# ── Calendario: consulta, día actual y notas ───────────────

def _calendar_or_403(conn, cid: int, user, need_edit: bool = False):
    """Devuelve (cfg, is_dm) si quien pide puede ver (o editar) el calendario."""
    _, is_dm = require_access(conn, cid, user)
    cfg = _get_config(conn, cid)
    if not cfg["modulo_calendario"]:
        raise HTTPException(404, "El calendario no está activo en esta campaña")
    if not is_dm and not cfg["calendario_visible"]:
        raise HTTPException(403, "El DM no comparte el calendario")
    if need_edit and not is_dm and not cfg["calendario_editable"]:
        raise HTTPException(403, "El DM no deja anotar en el calendario")
    return cfg, is_dm


@router.get("/campaigns/{cid}/calendar")
def get_calendar(cid: int, user=Depends(current_user)):
    """Fecha en la que están los jugadores + notas. Si el calendario está
    apagado (o el DM se lo guarda) vuelve `enabled`/`visible` en false y nada
    más: el frontend no dibuja el botón y no hay nada que espiar."""
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
        cfg = _get_config(conn, cid)
        return _calendar_view(conn, cid, cfg, is_dm, user["id"])


@router.put("/campaigns/{cid}/calendar")
def set_calendar(cid: int, c: CalendarSet, user=Depends(current_user)):
    """El DM fija en qué día están (desde el calendario o desde los ajustes)."""
    with db() as conn:
        require_dm(conn, cid, user)
        cur = roshar.from_index(_get_calendar(conn, cid)["day_index"])
        if c.day_index is not None:
            idx = roshar.clamp_index(c.day_index)
        else:
            idx = roshar.to_index(
                c.year if c.year is not None else cur["year"],
                c.month if c.month is not None else cur["month"],
                c.week if c.week is not None else cur["week"],
                c.day if c.day is not None else cur["day"],
            )
        _set_calendar(conn, cid, idx)
        cfg = _get_config(conn, cid)
        return {"ok": True, **_calendar_view(conn, cid, cfg, True, user["id"])}


@router.post("/campaigns/{cid}/calendar/notes")
def add_calendar_note(cid: int, n: CalendarNoteIn, user=Depends(current_user)):
    """Clava una nota (o pin) en un día. El DM siempre puede; los jugadores,
    solo si el DM los dejó."""
    with db() as conn:
        cfg, is_dm = _calendar_or_403(conn, cid, user, need_edit=True)
        texto = (n.texto or "").strip()
        if not texto:
            raise HTTPException(400, "Escribí algo en la nota")
        idx = (roshar.clamp_index(n.day_index) if n.day_index is not None
               else _get_calendar(conn, cid)["day_index"])
        conn.execute(
            "INSERT INTO calendar_notes (campaign_id, day_index, user_id, texto, color, secreto) "
            "VALUES (?,?,?,?,?,?)",
            (cid, idx, user["id"], texto[:500], (n.color or "")[:16],
             1 if (n.secreto and is_dm) else 0),
        )
        return {"ok": True, **_calendar_view(conn, cid, cfg, is_dm, user["id"])}


@router.put("/campaigns/{cid}/calendar/notes/{nid}")
def edit_calendar_note(cid: int, nid: int, n: CalendarNoteIn, user=Depends(current_user)):
    """Editar una nota: la suya cada jugador, cualquiera el DM."""
    with db() as conn:
        cfg, is_dm = _calendar_or_403(conn, cid, user, need_edit=True)
        row = conn.execute("SELECT * FROM calendar_notes WHERE id=? AND campaign_id=?",
                           (nid, cid)).fetchone()
        if not row:
            raise HTTPException(404, "Esa nota no existe")
        if not is_dm and row["user_id"] != user["id"]:
            raise HTTPException(403, "Esa nota no es tuya")
        texto = (n.texto or "").strip()
        if not texto:
            raise HTTPException(400, "Escribí algo en la nota")
        idx = roshar.clamp_index(n.day_index) if n.day_index is not None else row["day_index"]
        conn.execute(
            "UPDATE calendar_notes SET texto=?, color=?, secreto=?, day_index=? WHERE id=?",
            (texto[:500], (n.color or "")[:16],
             1 if (n.secreto and is_dm) else 0, idx, nid),
        )
        return {"ok": True, **_calendar_view(conn, cid, cfg, is_dm, user["id"])}


@router.delete("/campaigns/{cid}/calendar/notes/{nid}")
def del_calendar_note(cid: int, nid: int, user=Depends(current_user)):
    with db() as conn:
        cfg, is_dm = _calendar_or_403(conn, cid, user, need_edit=True)
        row = conn.execute("SELECT * FROM calendar_notes WHERE id=? AND campaign_id=?",
                           (nid, cid)).fetchone()
        if not row:
            raise HTTPException(404, "Esa nota no existe")
        if not is_dm and row["user_id"] != user["id"]:
            raise HTTPException(403, "Esa nota no es tuya")
        conn.execute("DELETE FROM calendar_notes WHERE id=?", (nid,))
        return {"ok": True, **_calendar_view(conn, cid, cfg, is_dm, user["id"])}


# ── Parámetros ajustables por el DM ────────────────────────

@router.get("/campaigns/{cid}/config")
def get_config(cid: int, user=Depends(current_user)):
    """Devuelve los parámetros ajustables + el estado actual de la tormenta."""
    with db() as conn:
        require_dm(conn, cid, user)
        cfg = _get_config(conn, cid)
        row = _get_storm(conn, cid)
        cfg.update({"storm_day": row["day"], "storm_target": row["target"],
                    "storm_moment": row["moment"], "moments": STORM_MOMENTS,
                    "modos": list(VER_MODOS)})
        cfg.update(_cal_fields(conn, cid))
        return cfg


@router.put("/campaigns/{cid}/config")
def put_config(cid: int, c: ConfigIn, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        cfg = _get_config(conn, cid)
        for k in CONFIG_DEFAULTS:
            v = getattr(c, k, None)
            if v is not None:
                cfg[k] = coerce(k, v)
        save_config(conn, cid, sane(cfg))
        # estado actual de la tormenta (opcional)
        row = _get_storm(conn, cid)
        day = c.storm_day if c.storm_day is not None else row["day"]
        target = c.storm_target if c.storm_target is not None else row["target"]
        moment = c.storm_moment if c.storm_moment is not None else row["moment"]
        day = max(0, int(day))
        target = max(1, int(target))
        if moment not in STORM_MOMENTS:
            moment = row["moment"]
        conn.execute("UPDATE storm_tracker SET day=?, target=?, moment=? WHERE campaign_id=?",
                     (day, target, moment, cid))
        # fecha actual del calendario (opcional, igual que la tormenta)
        if any(v is not None for v in (c.cal_year, c.cal_month, c.cal_week, c.cal_day)):
            cur = roshar.from_index(_get_calendar(conn, cid)["day_index"])
            _set_calendar(conn, cid, roshar.to_index(
                c.cal_year if c.cal_year is not None else cur["year"],
                c.cal_month if c.cal_month is not None else cur["month"],
                c.cal_week if c.cal_week is not None else cur["week"],
                c.cal_day if c.cal_day is not None else cur["day"],
            ))
        cfg = _get_config(conn, cid)
        row = _get_storm(conn, cid)
        cfg.update({"storm_day": row["day"], "storm_target": row["target"],
                    "storm_moment": row["moment"], "moments": STORM_MOMENTS})
        cfg.update(_cal_fields(conn, cid))
    return {"ok": True, **cfg}


# ── Jugador: invitaciones y membresías ─────────────────────

@router.get("/invitations")
def my_invitations(user=Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT c.id AS campaign_id, c.name, c.system, u.username AS dm "
            "FROM campaign_members m JOIN campaigns c ON c.id=m.campaign_id "
            "JOIN users u ON u.id=c.dm_id "
            "WHERE m.user_id=? AND m.status='invited' ORDER BY m.created_at DESC",
            (user["id"],),
        ).fetchall()
        return [dict(r) for r in rows]


@router.get("/my/campaigns")
def my_campaigns_as_player(user=Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT c.id, c.name, c.system, u.username AS dm, m.character_id, ch.name AS character_name "
            "FROM campaign_members m JOIN campaigns c ON c.id=m.campaign_id "
            "JOIN users u ON u.id=c.dm_id "
            "LEFT JOIN characters ch ON ch.id=m.character_id "
            "WHERE m.user_id=? AND m.status='accepted' ORDER BY c.name",
            (user["id"],),
        ).fetchall()
        return [dict(r) for r in rows]


# Aceptar una invitación ya no es "elegir un PJ existente": el jugador crea el
# personaje (o sube el PDF) para esa campaña desde el router de personajes, y eso
# marca la membresía como 'accepted' y la enlaza. Ver characters.create_character /
# import_pdf.


@router.post("/campaigns/{cid}/decline")
def decline_invite(cid: int, user=Depends(current_user)):
    with db() as conn:
        conn.execute(
            "DELETE FROM campaign_members WHERE campaign_id=? AND user_id=? AND status='invited'",
            (cid, user["id"]),
        )
    return {"ok": True}


@router.post("/campaigns/{cid}/leave")
def leave_campaign(cid: int, user=Depends(current_user)):
    with db() as conn:
        # El personaje pertenece a la campaña: al salir se elimina.
        conn.execute("DELETE FROM characters WHERE campaign_id=? AND owner_id=?", (cid, user["id"]))
        conn.execute(
            "DELETE FROM campaign_members WHERE campaign_id=? AND user_id=?",
            (cid, user["id"]),
        )
    return {"ok": True}
