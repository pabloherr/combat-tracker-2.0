"""
Autenticación: hashing de contraseñas (stdlib), sesiones por cookie y
dependencias de FastAPI para obtener el usuario actual.

Nota: pensado para una app de LAN sin HTTPS. El hashing (pbkdf2) y la
cookie httponly son razonables para una mesa casera; no es alta seguridad.
"""

import hashlib
import secrets

from fastapi import HTTPException, Request

from .database import db

COOKIE_NAME = "sid"
_ITERATIONS = 100_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    if salt is None:
        salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), _ITERATIONS).hex()
    return h, salt


def verify_password(password: str, salt: str, pass_hash: str) -> bool:
    calc, _ = hash_password(password, salt)
    return secrets.compare_digest(calc, pass_hash)


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    with db() as conn:
        conn.execute("INSERT INTO sessions (token, user_id) VALUES (?,?)", (token, user_id))
    return token


def delete_session(token: str | None):
    if not token:
        return
    with db() as conn:
        conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def user_for_token(token: str | None) -> dict | None:
    """Devuelve el usuario (dict) para un token de sesión, o None."""
    if not token:
        return None
    with db() as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?",
            (token,),
        ).fetchone()
        return dict(row) if row else None


def public_user(u: dict) -> dict:
    """Datos del usuario seguros para enviar al cliente (sin hash/salt)."""
    return {"id": u["id"], "username": u["username"], "email": u.get("email", "")}


# ── Dependencias ───────────────────────────────────────────

def optional_user(request: Request) -> dict | None:
    return user_for_token(request.cookies.get(COOKIE_NAME))


def current_user(request: Request) -> dict:
    u = optional_user(request)
    if not u:
        raise HTTPException(401, "No autenticado")
    return u
