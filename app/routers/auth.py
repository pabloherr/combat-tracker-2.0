"""API: registro, login, logout, recuperar contraseña y editar cuenta."""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..auth import (COOKIE_NAME, create_session, current_user, delete_session,
                    hash_password, optional_user, public_user, verify_password)
from ..database import db
from ..models import AccountUpdate, LoginIn, RegisterIn, ResetIn

router = APIRouter(prefix="/api/auth", tags=["auth"])

_MAX_AGE = 60 * 60 * 24 * 30  # 30 días


def _set_cookie(response: Response, token: str):
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=_MAX_AGE)


def _valid_email(email: str) -> bool:
    email = email.strip()
    return "@" in email and "." in email.split("@")[-1]


@router.post("/register")
def register(r: RegisterIn, response: Response):
    username = r.username.strip()
    email = r.email.strip()
    if not username or not r.password:
        raise HTTPException(400, "Usuario y contraseña son obligatorios")
    if not _valid_email(email):
        raise HTTPException(400, "Poné un email válido (sirve para recuperar la contraseña)")
    if len(r.password) < 4:
        raise HTTPException(400, "La contraseña debe tener al menos 4 caracteres")
    ph, salt = hash_password(r.password)
    with db() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (username, email, pass_hash, salt) VALUES (?,?,?,?)",
                (username, email, ph, salt),
            )
            uid = cur.lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Ese nombre de usuario ya existe")
    _set_cookie(response, create_session(uid))
    return {"id": uid, "username": username, "email": email}


@router.post("/login")
def login(r: LoginIn, response: Response):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (r.username.strip(),)).fetchone()
    if not row or not verify_password(r.password, row["salt"], row["pass_hash"]):
        raise HTTPException(400, "Usuario o contraseña incorrectos")
    _set_cookie(response, create_session(row["id"]))
    return public_user(dict(row))


@router.post("/reset")
def reset_password(r: ResetIn):
    """Recuperar contraseña: probando el email asociado a la cuenta."""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (r.username.strip(),)).fetchone()
        if not row or (row["email"] or "").strip().lower() != r.email.strip().lower():
            raise HTTPException(400, "Usuario o email incorrectos")
        if len(r.password) < 4:
            raise HTTPException(400, "La nueva contraseña debe tener al menos 4 caracteres")
        ph, salt = hash_password(r.password)
        conn.execute("UPDATE users SET pass_hash=?, salt=? WHERE id=?", (ph, salt, row["id"]))
        # invalida sesiones abiertas de esa cuenta
        conn.execute("DELETE FROM sessions WHERE user_id=?", (row["id"],))
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response):
    delete_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    u = optional_user(request)
    if not u:
        raise HTTPException(401, "No autenticado")
    return public_user(u)


@router.post("/account")
def update_account(a: AccountUpdate, user=Depends(current_user)):
    """Edita usuario/email; opcionalmente cambia la contraseña (pide la actual)."""
    username = a.username.strip()
    email = a.email.strip()
    if not username:
        raise HTTPException(400, "El usuario no puede quedar vacío")
    if not _valid_email(email):
        raise HTTPException(400, "Poné un email válido")
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        if a.new_password:
            if not verify_password(a.current_password, row["salt"], row["pass_hash"]):
                raise HTTPException(400, "La contraseña actual es incorrecta")
            if len(a.new_password) < 4:
                raise HTTPException(400, "La nueva contraseña debe tener al menos 4 caracteres")
            ph, salt = hash_password(a.new_password)
            conn.execute("UPDATE users SET pass_hash=?, salt=? WHERE id=?", (ph, salt, user["id"]))
        try:
            conn.execute("UPDATE users SET username=?, email=? WHERE id=?", (username, email, user["id"]))
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Ese nombre de usuario ya está en uso")
    return {"id": user["id"], "username": username, "email": email}
