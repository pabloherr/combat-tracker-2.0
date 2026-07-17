"""API: Campañas, membresías e invitaciones."""

import json
import random
import re

from fastapi import APIRouter, Depends, HTTPException

from ..access import campaign_or_404, require_access, require_dm
from ..auth import current_user
from ..database import db
from ..models import CampaignIn, ConfigIn, InviteIn, LongRestIn, MarcosChange

router = APIRouter(prefix="/api", tags=["campaigns"])

# ── Altas tormentas: cada 10±2 días (8-12), en un momento al azar ──
STORM_MOMENTS = ["al amanecer", "por la mañana", "al mediodía",
                 "por la tarde", "al anochecer", "de madrugada"]

# Parámetros por defecto, ajustables por el DM (se guardan en campaigns.config).
CONFIG_DEFAULTS = {
    "storm_min": 8,          # mínimo de días entre tormentas
    "storm_max": 12,         # máximo de días entre tormentas
    "discharge_start": 5,    # día desde el que los marcos empiezan a apagarse
    "discharge_full": 15,    # día en que ya no queda luz
    "discharge_curve": 2.0,  # exponente: 1 = pareja; más alto = arranca más lento
}
_CONFIG_INT_KEYS = {"storm_min", "storm_max", "discharge_start", "discharge_full"}


def _get_config(conn, cid: int) -> dict:
    row = conn.execute("SELECT config FROM campaigns WHERE id=?", (cid,)).fetchone()
    cfg = dict(CONFIG_DEFAULTS)
    if row and row["config"]:
        try:
            saved = json.loads(row["config"])
            for k, v in saved.items():
                if k in CONFIG_DEFAULTS:
                    cfg[k] = int(v) if k in _CONFIG_INT_KEYS else float(v)
        except (ValueError, TypeError):
            pass
    # saneo básico para no romper la lógica
    cfg["storm_min"] = max(1, cfg["storm_min"])
    cfg["storm_max"] = max(cfg["storm_min"], cfg["storm_max"])
    cfg["discharge_start"] = max(1, cfg["discharge_start"])
    cfg["discharge_full"] = max(cfg["discharge_start"] + 1, cfg["discharge_full"])
    cfg["discharge_curve"] = max(0.1, min(8.0, cfg["discharge_curve"]))
    return cfg


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
            "SELECT m.user_id, m.status, m.character_id, u.username, ch.name AS character_name, "
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
        c, _ = require_access(conn, cid, user)
        rows = conn.execute(
            "SELECT m.user_id, u.username, ch.* FROM campaign_members m "
            "JOIN users u ON u.id=m.user_id "
            "JOIN characters ch ON ch.id=m.character_id "
            "WHERE m.campaign_id=? AND m.status='accepted' AND m.character_id IS NOT NULL "
            "ORDER BY u.username",
            (cid,),
        ).fetchall()
        members = []
        for r in rows:
            pets = [
                {"id": p["id"], "name": p["name"],
                 "vida": p["vida"], "vida_max": p["vida_max"],
                 "focus": p["focus"], "focus_max": p["focus_max"],
                 "inv": p["inv"], "inv_max": p["inv_max"],
                 "statuses": json.loads(p["statuses"] or "[]"),
                 "stats": json.loads(p["stats"] or "{}"),
                 "acciones": json.loads(p["acciones"] or "[]")}
                for p in conn.execute("SELECT * FROM pets WHERE character_id=? ORDER BY name", (r["id"],))
            ]
            members.append({
                "user_id": r["user_id"], "username": r["username"],
                "character": {
                    "id": r["id"], "name": r["name"],
                    "vida": r["vida"], "vida_max": r["vida_max"],
                    "focus": r["focus"], "focus_max": r["focus_max"],
                    "inv": r["inv"], "inv_max": r["inv_max"],
                    "statuses": json.loads(r["statuses"] or "[]"),
                    "injuries": json.loads(r["injuries"] or "[]"),
                    "sheet": json.loads(r["sheet"] or "{}"),
                    "has_pdf": bool(r["has_pdf"]),
                    "has_image": bool(r["has_image"]),
                    "marcos": r["marcos"] or 0,
                    "marcos_light": r["marcos_light"] or 0,
                    "dnd": json.loads(r["dnd_resources"] or "{}"),
                },
                "pets": pets,
            })
        return {"system": c["system"] or "cosmere", "members": members}


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
        storm = _advance_storm(conn, cid)   # el día pasa para todos, con o sin descanso
        _marcos_tick(conn, cid, storm["stormed"], _get_storm(conn, cid)["day"])
    return {"ok": True, "characters": done, "storm": storm}


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
    """El DM ve el día y momento exactos; los jugadores solo la barra."""
    base = {"day": row["day"], "min": cfg["storm_min"], "max": cfg["storm_max"]}
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
def advance_storm(cid: int, user=Depends(current_user)):
    """El DM adelanta un día suelto (viaje, etc.) sin descanso largo."""
    with db() as conn:
        require_dm(conn, cid, user)
        storm = _advance_storm(conn, cid)
        row = _get_storm(conn, cid)
        _marcos_tick(conn, cid, storm["stormed"], row["day"])
        state = _storm_view(row, True, _get_config(conn, cid))
    return {"ok": True, "storm": storm, "state": state}


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


# ── Parámetros ajustables por el DM ────────────────────────

@router.get("/campaigns/{cid}/config")
def get_config(cid: int, user=Depends(current_user)):
    """Devuelve los parámetros ajustables + el estado actual de la tormenta."""
    with db() as conn:
        require_dm(conn, cid, user)
        cfg = _get_config(conn, cid)
        row = _get_storm(conn, cid)
        cfg.update({"storm_day": row["day"], "storm_target": row["target"],
                    "storm_moment": row["moment"], "moments": STORM_MOMENTS})
        return cfg


@router.put("/campaigns/{cid}/config")
def put_config(cid: int, c: ConfigIn, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        cfg = _get_config(conn, cid)
        for k in CONFIG_DEFAULTS:
            v = getattr(c, k)
            if v is not None:
                cfg[k] = int(v) if k in _CONFIG_INT_KEYS else float(v)
        # revalidar coherencia antes de guardar
        cfg["storm_min"] = max(1, cfg["storm_min"])
        cfg["storm_max"] = max(cfg["storm_min"], cfg["storm_max"])
        cfg["discharge_start"] = max(1, cfg["discharge_start"])
        cfg["discharge_full"] = max(cfg["discharge_start"] + 1, cfg["discharge_full"])
        cfg["discharge_curve"] = max(0.1, min(8.0, cfg["discharge_curve"]))
        conn.execute("UPDATE campaigns SET config=? WHERE id=?",
                     (json.dumps({k: cfg[k] for k in CONFIG_DEFAULTS}), cid))
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
        cfg = _get_config(conn, cid)
        row = _get_storm(conn, cid)
        cfg.update({"storm_day": row["day"], "storm_target": row["target"],
                    "storm_moment": row["moment"], "moments": STORM_MOMENTS})
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
