"""
Autenticación: hashing de contraseñas (stdlib), sesiones por cookie y
dependencias de FastAPI para obtener el usuario actual.

Nota: pensado para una app de LAN sin HTTPS. El hashing (pbkdf2) y la
cookie httponly son razonables para una mesa casera; no es alta seguridad.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from .database import db

COOKIE_NAME = "sid"
_ITERATIONS = 100_000

# Cada cuánto se refresca `sessions.last_seen`. No en cada petición: sería una
# escritura por request y el panel no necesita esa precisión.
_SEEN_EVERY_SECONDS = 120


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS).hex()
    return h, salt


def verify_password(password: str, salt: str, pass_hash: str) -> bool:
    calc, _ = hash_password(password, salt)
    return secrets.compare_digest(calc, pass_hash)


def password_problem(password: str) -> str:
    """Devuelve el motivo por el que una contraseña no sirve, o '' si está bien.

    Un solo lugar para la regla: la usan el registro, el cambio desde la cuenta
    y la recuperación."""
    if not password:
        return "Poné una contraseña"
    if len(password) < 6:
        return "La contraseña debe tener al menos 6 caracteres"
    if password.strip().lower() in ("123456", "password", "contrasena", "contraseña", "qwerty"):
        return "Esa contraseña es de las primeras que prueba cualquiera; elegí otra"
    return ""


ROLES = ("dm", "player")


def create_session(user_id: int, role: str = "dm", ip: str = "", user_agent: str = "") -> str:
    """Crea la sesión. El modo (dm | player) se elige al entrar y queda fijo:
    para cambiarlo hay que cerrar sesión y volver a entrar."""
    token = secrets.token_urlsafe(32)
    role = role if role in ROLES else "dm"
    with db() as conn:
        conn.execute(
            "INSERT INTO sessions (token, user_id, role, ip, user_agent, last_seen) "
            "VALUES (?,?,?,?,?,datetime('now'))",
            (token, user_id, role, ip[:45], user_agent[:200]))
    return token


def delete_session(token: str | None):
    if not token:
        return
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def user_for_token(token: str | None) -> dict | None:
    """Devuelve el usuario (dict) para un token de sesión, o None.

    Incluye `role`: el modo con el que se inició esta sesión."""
    if not token:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT u.*, s.role, s.last_seen AS session_seen FROM sessions s "
            "JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        ).fetchone()
        if not row:
            return None
        u = dict(row)
        # Cuenta bloqueada desde el panel: la sesión deja de valer.
        if u.get("blocked"):
            conn.execute("DELETE FROM sessions WHERE token=?", (token,))
            return None
        u["role"] = u.get("role") if u.get("role") in ROLES else "dm"
        seen = u.get("session_seen")
        if not seen or seen < _seen_cutoff():
            conn.execute("UPDATE sessions SET last_seen=datetime('now') WHERE token=?", (token,))
        return u


def _seen_cutoff() -> str:
    """Marca de tiempo (UTC, como la guarda SQLite) a partir de la cual no hace
    falta volver a tocar `last_seen`."""
    corte = datetime.now(timezone.utc) - timedelta(seconds=_SEEN_EVERY_SECONDS)
    return corte.strftime("%Y-%m-%d %H:%M:%S")


def session_user_id(token: str | None) -> int | None:
    """Solo el id de la sesión, sin traer el usuario ni tocar `last_seen`.

    Lo usa la telemetría en cada petición: es una búsqueda por clave primaria y
    no agrega escrituras al camino de una request cualquiera."""
    if not token:
        return None
    try:
        with db() as conn:
            row = conn.execute("SELECT user_id FROM sessions WHERE token=?", (token,)).fetchone()
        return row["user_id"] if row else None
    except Exception:
        return None


def user_by_identifier(conn, identifier: str):
    """Busca por nombre de usuario o por email (sin distinguir mayúsculas)."""
    ident = (identifier or "").strip()
    if not ident:
        return None
    row = conn.execute("SELECT * FROM users WHERE username = ?", (ident,)).fetchone()
    if row:
        return row
    return conn.execute("SELECT * FROM users WHERE lower(email) = lower(?) AND email <> ''",
                        (ident,)).fetchone()


def public_user(u: dict) -> dict:
    """Datos del usuario seguros para enviar al cliente (sin hash/salt)."""
    return {"id": u["id"], "username": u["username"], "email": u.get("email", ""),
            "role": u.get("role", "dm")}


# ── Dependencias ───────────────────────────────────────────

def optional_user(request: Request) -> dict | None:
    return user_for_token(request.cookies.get(COOKIE_NAME))


def current_user(request: Request) -> dict:
    u = optional_user(request)
    if not u:
        raise HTTPException(401, "No autenticado")
    return u
