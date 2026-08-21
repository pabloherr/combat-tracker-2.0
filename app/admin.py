"""
Quién es administrador y cómo se lo exige.

Admin = el email de la cuenta está en `ADMIN_EMAILS` (por defecto, la casilla
de correo de la app) **o** la cuenta tiene la marca `users.is_admin`, que se
otorga desde el panel. Lo primero hace que la cuenta del correo entre siempre,
aunque la base se haya perdido; lo segundo permite sumar a alguien más sin
tocar la configuración.
"""

from fastapi import Depends, HTTPException

from . import settings
from .auth import current_user


def is_admin(u: dict | None) -> bool:
    if not u:
        return False
    if (u.get("email") or "").strip().lower() in settings.admin_emails():
        return True
    return bool(u.get("is_admin"))


def current_admin(user=Depends(current_user)) -> dict:
    if not is_admin(user):
        raise HTTPException(403, "Esta sección es solo del administrador")
    return user


def admin_ids(conn) -> set[int]:
    """Ids de todas las cuentas que hoy son admin (por email o por marca)."""
    emails = settings.admin_emails()
    rows = conn.execute("SELECT id, email, is_admin FROM users").fetchall()
    return {r["id"] for r in rows
            if r["is_admin"] or (r["email"] or "").strip().lower() in emails}


def by_email(conn) -> set[int]:
    """Ids admin por configuración: a esos no se les puede sacar el permiso."""
    emails = settings.admin_emails()
    rows = conn.execute("SELECT id, email FROM users").fetchall()
    return {r["id"] for r in rows if (r["email"] or "").strip().lower() in emails}
