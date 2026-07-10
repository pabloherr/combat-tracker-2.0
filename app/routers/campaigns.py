"""API: Campañas, membresías e invitaciones."""

import json
import random
import re

from fastapi import APIRouter, Depends, HTTPException

from ..access import campaign_or_404, require_access, require_dm
from ..auth import current_user
from ..database import db
from ..models import CampaignIn, InviteIn, LongRestIn, MarcosChange

router = APIRouter(prefix="/api", tags=["campaigns"])

# ── Altas tormentas: cada 10±2 días (8-12), en un momento al azar ──
STORM_MIN, STORM_MAX = 8, 12
STORM_MOMENTS = ["al amanecer", "por la mañana", "al mediodía",
                 "por la tarde", "al anochecer", "de madrugada"]


def _get_storm(conn, cid: int):
    row = conn.execute("SELECT * FROM storm_tracker WHERE campaign_id=?", (cid,)).fetchone()
    if not row:
        conn.execute(
            "INSERT INTO storm_tracker (campaign_id, day, target, moment) VALUES (?,0,?,?)",
            (cid, random.randint(STORM_MIN, STORM_MAX), random.choice(STORM_MOMENTS)),
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
        conn.execute(
            "UPDATE storm_tracker SET day=?, target=?, moment=? WHERE campaign_id=?",
            (day, random.randint(STORM_MIN, STORM_MAX), random.choice(STORM_MOMENTS), cid),
        )
    else:
        conn.execute("UPDATE storm_tracker SET day=? WHERE campaign_id=?", (day, cid))
    return {"stormed": stormed, "storm_day": storm_day, "storm_moment": storm_moment}


# ── Marcos: recarga en tormenta y descarga con el paso de los días ──
STORM_DISCHARGE_START = 5    # día desde el que los marcos empiezan a apagarse
STORM_DISCHARGE_FULL = 15    # día en que ya no queda ninguno con luz


def _marcos_tick(conn, cid: int, stormed: bool, day: int):
    """Aplica al día que avanza: la tormenta recarga todos los marcos; a partir del
    día 5 sin tormenta, cada marco cargado se apaga con probabilidad creciente
    (pocos al principio, todos para el día 15)."""
    rows = conn.execute(
        "SELECT id, marcos, marcos_light FROM characters WHERE campaign_id=?", (cid,)
    ).fetchall()
    if stormed:
        for r in rows:
            if (r["marcos"] or 0) != (r["marcos_light"] or 0):
                conn.execute("UPDATE characters SET marcos_light=marcos WHERE id=?", (r["id"],))
        return
    if day < STORM_DISCHARGE_START:
        return
    span = STORM_DISCHARGE_FULL - (STORM_DISCHARGE_START - 1)   # 11
    p = min(1.0, max(0.0, (day - (STORM_DISCHARGE_START - 1)) / span))
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
    with db() as conn:
        cur = conn.execute("INSERT INTO campaigns (name, dm_id) VALUES (?,?)", (name, user["id"]))
        return {"id": cur.lastrowid, "name": name}


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
        return {"id": c["id"], "name": c["name"], "is_dm": c["dm_id"] == user["id"]}


# ── Miembros (DM) ──────────────────────────────────────────

@router.get("/campaigns/{cid}/members")
def list_members(cid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
        rows = conn.execute(
            "SELECT m.user_id, m.status, m.character_id, u.username, ch.name AS character_name, "
            "ch.has_pdf, ch.marcos, ch.marcos_light "
            "FROM campaign_members m JOIN users u ON u.id=m.user_id "
            "LEFT JOIN characters ch ON ch.id=m.character_id "
            "WHERE m.campaign_id=? ORDER BY u.username",
            (cid,),
        ).fetchall()
        return [dict(r) for r in rows]


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
        require_access(conn, cid, user)
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
                },
                "pets": pets,
            })
        return {"members": members}


@router.post("/campaigns/{cid}/long_rest")
def long_rest(cid: int, payload: LongRestIn, user=Depends(current_user)):
    """Descanso largo (DM): cura a full a los personajes aceptados (y sus mascotas)
    y baja en 1 los días de sus heridas; las permanentes no cambian. El DM puede
    excluir jugadores con `exclude` (lista de user_id)."""
    excl = set(payload.exclude or [])
    with db() as conn:
        require_dm(conn, cid, user)
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
        storm = _advance_storm(conn, cid)   # el día pasa para todos, con o sin descanso
        _marcos_tick(conn, cid, storm["stormed"], _get_storm(conn, cid)["day"])
    return {"ok": True, "characters": done, "storm": storm}


def _storm_view(row, is_dm: bool) -> dict:
    """El DM ve el día y momento exactos; los jugadores solo la barra."""
    base = {"day": row["day"], "min": STORM_MIN, "max": STORM_MAX}
    if is_dm:
        base["target"] = row["target"]
        base["moment"] = row["moment"]
    return base


@router.get("/campaigns/{cid}/storm")
def get_storm(cid: int, user=Depends(current_user)):
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
        row = _get_storm(conn, cid)
        return _storm_view(row, is_dm)


@router.post("/campaigns/{cid}/storm/advance")
def advance_storm(cid: int, user=Depends(current_user)):
    """El DM adelanta un día suelto (viaje, etc.) sin descanso largo."""
    with db() as conn:
        require_dm(conn, cid, user)
        storm = _advance_storm(conn, cid)
        row = _get_storm(conn, cid)
        _marcos_tick(conn, cid, storm["stormed"], row["day"])
    return {"ok": True, "storm": storm, "state": _storm_view(row, True)}


@router.post("/campaigns/{cid}/storm/reset")
def reset_storm(cid: int, user=Depends(current_user)):
    """El DM reinicia el ciclo (nuevo día y momento al azar)."""
    with db() as conn:
        require_dm(conn, cid, user)
        _get_storm(conn, cid)   # asegura que exista la fila
        conn.execute(
            "UPDATE storm_tracker SET day=0, target=?, moment=? WHERE campaign_id=?",
            (random.randint(STORM_MIN, STORM_MAX), random.choice(STORM_MOMENTS), cid),
        )
        row = _get_storm(conn, cid)
    return {"ok": True, "state": _storm_view(row, True)}


# ── Jugador: invitaciones y membresías ─────────────────────

@router.get("/invitations")
def my_invitations(user=Depends(current_user)):
    with db() as conn:
        rows = conn.execute(
            "SELECT c.id AS campaign_id, c.name, u.username AS dm "
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
            "SELECT c.id, c.name, u.username AS dm, m.character_id, ch.name AS character_name "
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
