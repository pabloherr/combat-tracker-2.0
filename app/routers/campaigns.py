"""API: Campañas, membresías e invitaciones."""

import json
import re

from fastapi import APIRouter, Depends, HTTPException

from ..access import campaign_or_404, require_dm
from ..auth import current_user
from ..database import db
from ..models import AcceptIn, CampaignIn, InviteIn

router = APIRouter(prefix="/api", tags=["campaigns"])


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
            "SELECT m.user_id, m.status, m.character_id, u.username, ch.name AS character_name, ch.has_pdf "
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


@router.post("/campaigns/{cid}/accept")
def accept_invite(cid: int, a: AcceptIn, user=Depends(current_user)):
    with db() as conn:
        m = conn.execute(
            "SELECT * FROM campaign_members WHERE campaign_id=? AND user_id=?",
            (cid, user["id"]),
        ).fetchone()
        if not m or m["status"] != "invited":
            raise HTTPException(404, "No tenés una invitación pendiente a esta campaña")
        ch = conn.execute(
            "SELECT id FROM characters WHERE id=? AND owner_id=?",
            (a.character_id, user["id"]),
        ).fetchone()
        if not ch:
            raise HTTPException(400, "Elegí un personaje tuyo válido")
        conn.execute(
            "UPDATE campaign_members SET status='accepted', character_id=? WHERE campaign_id=? AND user_id=?",
            (a.character_id, cid, user["id"]),
        )
    return {"ok": True}


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
        conn.execute(
            "DELETE FROM campaign_members WHERE campaign_id=? AND user_id=?",
            (cid, user["id"]),
        )
    return {"ok": True}
