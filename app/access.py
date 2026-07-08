"""Chequeos de acceso a campañas (DM / miembro), reutilizados por varios routers."""

from fastapi import HTTPException


def campaign_or_404(conn, cid: int):
    c = conn.execute("SELECT * FROM campaigns WHERE id=?", (cid,)).fetchone()
    if not c:
        raise HTTPException(404, "Campaña no encontrada")
    return c


def require_dm(conn, cid: int, user: dict):
    """Exige que el usuario sea el DM (dueño) de la campaña."""
    c = campaign_or_404(conn, cid)
    if c["dm_id"] != user["id"]:
        raise HTTPException(403, "Solo el DM de la campaña puede hacer esto")
    return c


def require_access(conn, cid: int, user: dict):
    """Permite al DM o a un miembro aceptado. Devuelve (campaña, is_dm)."""
    c = campaign_or_404(conn, cid)
    if c["dm_id"] == user["id"]:
        return c, True
    m = conn.execute(
        "SELECT 1 FROM campaign_members WHERE campaign_id=? AND user_id=? AND status='accepted'",
        (cid, user["id"]),
    ).fetchone()
    if not m:
        raise HTTPException(403, "No sos parte de esta campaña")
    return c, False
