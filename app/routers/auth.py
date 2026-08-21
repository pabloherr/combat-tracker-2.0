"""API: registro, login, logout, recuperación de contraseña y edición de cuenta.

Recuperar la contraseña ya no es "poné el email de la cuenta y listo": ahora
hay que probar que se puede leer ese correo. El flujo es:

    /forgot          → manda un mail con enlace + código de seis dígitos
    /reset/{token}   → el enlace: dice de quién es y si sigue vivo
    /reset           → contraseña nueva (con token o con usuario + código)

Cambiar la contraseña desde "Mi cuenta" también pasa por el correo: se pide la
actual, se manda un mail de confirmación y el cambio se aplica recién cuando
se confirma (`/confirm-change`). En los dos casos, al terminar sale un aviso de
"tu contraseña cambió" y se cierran todas las sesiones abiertas.
"""

import sqlite3
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from .. import mailer, recovery, telemetry
from ..admin import is_admin
from ..auth import (COOKIE_NAME, create_session, current_user, delete_session,
                    hash_password, optional_user, password_problem, public_user,
                    user_by_identifier, verify_password)
from ..database import db
from ..models import (AccountUpdate, ConfirmChangeIn, DeleteAccount, ForgotIn,
                      LoginIn, RegisterIn, ResetIn)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_MAX_AGE = 60 * 60 * 24 * 30  # 30 días

# Respuesta única del pedido de recuperación: sirva o no el usuario tipeado, se
# contesta lo mismo, así nadie usa la pantalla para averiguar qué cuentas hay.
_FORGOT_MSG = ("Si esa cuenta existe y tiene email, te mandamos un mensaje con "
               "un enlace y un código para elegir contraseña nueva.")


def _set_cookie(response: Response, token: str):
    response.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax", max_age=_MAX_AGE)


def _valid_email(email: str) -> bool:
    email = email.strip()
    return "@" in email and "." in email.split("@")[-1] and " " not in email


def _ua(request: Request) -> str:
    return request.headers.get("user-agent", "")[:200]


def _ahora() -> str:
    return datetime.now().strftime("%d/%m/%Y %H:%M")


def _email_ocupado(conn, email: str, salvo_id: int | None = None) -> bool:
    """El email tiene que ser único: es la llave para recuperar la cuenta."""
    row = conn.execute(
        "SELECT id FROM users WHERE lower(email)=lower(?) AND email <> '' AND id <> ?",
        (email, salvo_id or -1)).fetchone()
    return row is not None


def _cerrar_sesiones(conn, uid: int):
    conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))


def _aplicar_password(conn, uid: int, ph: str, salt: str):
    conn.execute("UPDATE users SET pass_hash=?, salt=? WHERE id=?", (ph, salt, uid))
    _cerrar_sesiones(conn, uid)


def _avisar_cambio(row, motivo: str):
    """Aviso posterior: la contraseña ya cambió."""
    email = (row["email"] or "").strip()
    if not email:
        return
    subject, text, html = mailer.changed_notice(row["username"], _ahora(), motivo)
    mailer.send(email, subject, text, html, kind="password-changed")


# ── Registro y entrada ─────────────────────────────────────

@router.post("/register")
def register(r: RegisterIn, request: Request, response: Response):
    username = r.username.strip()
    email = r.email.strip()
    ip = telemetry.client_ip(request)
    if not username or not r.password:
        raise HTTPException(400, "Usuario y contraseña son obligatorios")
    if not _valid_email(email):
        raise HTTPException(400, "Poné un email válido (sirve para recuperar la contraseña)")
    problema = password_problem(r.password)
    if problema:
        raise HTTPException(400, problema)
    ph, salt = hash_password(r.password)
    with db() as conn:
        if _email_ocupado(conn, email):
            raise HTTPException(400, "Ese email ya está usado por otra cuenta")
        try:
            cur = conn.execute(
                "INSERT INTO users (username, email, pass_hash, salt, last_login, login_count) "
                "VALUES (?,?,?,?,datetime('now'),1)",
                (username, email, ph, salt),
            )
            uid = cur.lastrowid
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Ese nombre de usuario ya existe")
    telemetry.log_event("register", uid, username, f"rol {r.role}", ip)
    _set_cookie(response, create_session(uid, r.role, ip, _ua(request)))
    return {"id": uid, "username": username, "email": email, "role": r.role,
            "admin": is_admin({"email": email, "is_admin": 0})}


@router.post("/login")
def login(r: LoginIn, request: Request, response: Response):
    """Entra con nombre de usuario **o** con el email de la cuenta."""
    ident = r.username.strip()
    ip = telemetry.client_ip(request)
    with db() as conn:
        row = user_by_identifier(conn, ident)
        if not row or not verify_password(r.password, row["salt"], row["pass_hash"]):
            telemetry.log_event("login_fail", None, ident, "usuario o contraseña", ip, ok=False)
            raise HTTPException(400, "Usuario o contraseña incorrectos")
        if row["blocked"]:
            telemetry.log_event("login_fail", row["id"], row["username"], "cuenta bloqueada",
                                ip, ok=False)
            raise HTTPException(403, "Esta cuenta está bloqueada. Hablá con el administrador.")
        conn.execute("UPDATE users SET last_login=datetime('now'), "
                     "login_count=COALESCE(login_count,0)+1 WHERE id=?", (row["id"],))
    telemetry.log_event("login", row["id"], row["username"], f"rol {r.role}", ip)
    _set_cookie(response, create_session(row["id"], r.role, ip, _ua(request)))
    u = dict(row)
    u["role"] = r.role
    out = public_user(u)
    out["admin"] = is_admin(u)
    return out


@router.post("/logout")
def logout(request: Request, response: Response):
    u = optional_user(request)
    if u:
        telemetry.log_event("logout", u["id"], u["username"], "", telemetry.client_ip(request))
    delete_session(request.cookies.get(COOKIE_NAME))
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    u = optional_user(request)
    if not u:
        raise HTTPException(401, "No autenticado")
    out = public_user(u)
    out["admin"] = is_admin(u)
    return out


# ── Olvidé mi contraseña ───────────────────────────────────

@router.post("/forgot")
def forgot(r: ForgotIn, request: Request):
    """Manda el correo de recuperación. Contesta siempre lo mismo."""
    ident = (r.identifier or "").strip()
    ip = telemetry.client_ip(request)
    if not ident:
        raise HTTPException(400, "Escribí tu usuario o tu email")

    with db() as conn:
        row = user_by_identifier(conn, ident)
        if not row:
            telemetry.log_event("forgot_miss", None, ident, "cuenta inexistente", ip, ok=False)
            return {"ok": True, "mensaje": _FORGOT_MSG}
        email = (row["email"] or "").strip()
        if not email:
            telemetry.log_event("forgot_miss", row["id"], row["username"],
                                "la cuenta no tiene email", ip, ok=False)
            return {"ok": True, "mensaje": _FORGOT_MSG}
        if recovery.rate_limited(conn, row["id"], ip, recovery.KIND_RESET):
            telemetry.log_event("forgot_limit", row["id"], row["username"],
                                "demasiados pedidos", ip, ok=False)
            raise HTTPException(429, "Pediste varios restablecimientos seguidos. "
                                     "Esperá un rato y revisá tu correo (mirá también el spam).")
        token, code = recovery.create(conn, row["id"], recovery.KIND_RESET, ip=ip)
        username = row["username"]

    link = recovery.link_for(recovery.KIND_RESET, token)
    subject, text, html = mailer.reset_email(username, code, link, recovery.TTL_MIN)
    mailer.send(email, subject, text, html, kind="reset")
    telemetry.log_event("forgot", None, username, f"mail a {_ofuscar(email)}", ip)
    return {"ok": True, "mensaje": _FORGOT_MSG, "pista": _ofuscar(email)}


def _ofuscar(email: str) -> str:
    """`gmcito234@gmail.com` → `gm•••••34@gmail.com`: alcanza para reconocer la
    casilla propia sin publicar la ajena."""
    if "@" not in email:
        return ""
    user, dom = email.split("@", 1)
    if len(user) <= 4:
        return f"{user[:1]}•••@{dom}"
    return f"{user[:2]}{'•' * 5}{user[-2:]}@{dom}"


@router.get("/reset/{token}")
def reset_info(token: str):
    """¿El enlace del correo sigue sirviendo? Lo consulta la pantalla."""
    with db() as conn:
        row = recovery.by_token(conn, token, recovery.KIND_RESET)
        if not row:
            raise HTTPException(400, "Ese enlace ya se usó o venció. Pedí uno nuevo.")
        user = conn.execute("SELECT username FROM users WHERE id=?", (row["user_id"],)).fetchone()
    return {"ok": True, "username": user["username"] if user else "",
            "minutos": recovery.minutes_left(row)}


@router.post("/reset")
def reset_password(r: ResetIn, request: Request):
    """Contraseña nueva, con el token del enlace o con usuario + código."""
    ip = telemetry.client_ip(request)
    problema = password_problem(r.password)
    if problema:
        raise HTTPException(400, problema)

    with db() as conn:
        if r.token:
            tok = recovery.by_token(conn, r.token, recovery.KIND_RESET)
            if not tok:
                raise HTTPException(400, "Ese enlace ya se usó o venció. Pedí uno nuevo.")
        else:
            row = user_by_identifier(conn, r.identifier)
            if not row:
                raise HTTPException(400, "Usuario o código incorrectos")
            tok = recovery.by_code(conn, row["id"], r.code, recovery.KIND_RESET)
            if not tok:
                raise HTTPException(400, "Usuario o código incorrectos")
        user = conn.execute("SELECT * FROM users WHERE id=?", (tok["user_id"],)).fetchone()
        if not user:
            raise HTTPException(400, "La cuenta ya no existe")
        if verify_password(r.password, user["salt"], user["pass_hash"]):
            raise HTTPException(400, "Esa es la contraseña que ya tenías; elegí otra")
        ph, salt = hash_password(r.password)
        recovery.consume(conn, tok["id"])
        _aplicar_password(conn, user["id"], ph, salt)

    telemetry.log_event("reset_done", user["id"], user["username"], "por correo", ip)
    _avisar_cambio(user, "con el enlace de recuperación")
    return {"ok": True, "username": user["username"]}


# ── Cambio de contraseña desde "Mi cuenta" ─────────────────

@router.post("/account")
def update_account(a: AccountUpdate, request: Request, user=Depends(current_user)):
    """Edita usuario/email. El cambio de contraseña no se aplica acá: se pide la
    actual, se manda un correo de confirmación y queda pendiente."""
    username = a.username.strip()
    email = a.email.strip()
    ip = telemetry.client_ip(request)
    if not username:
        raise HTTPException(400, "El usuario no puede quedar vacío")
    if not _valid_email(email):
        raise HTTPException(400, "Poné un email válido")

    pendiente = False
    aviso = ""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        email_viejo = (row["email"] or "").strip()
        # La unicidad se exige solo al cambiarlo: hay cuentas viejas que
        # comparten casilla y no tienen por qué romperse al editar el nombre.
        if email.lower() != email_viejo.lower() and _email_ocupado(conn, email, user["id"]):
            raise HTTPException(400, "Ese email ya está usado por otra cuenta")

        if a.new_password:
            if not verify_password(a.current_password, row["salt"], row["pass_hash"]):
                raise HTTPException(400, "La contraseña actual es incorrecta")
            problema = password_problem(a.new_password)
            if problema:
                raise HTTPException(400, problema)
            if verify_password(a.new_password, row["salt"], row["pass_hash"]):
                raise HTTPException(400, "Esa es la contraseña que ya tenías; elegí otra")
            destino = email or email_viejo
            if not destino:
                raise HTTPException(400, "Necesitás un email en la cuenta para confirmar el cambio")
            if recovery.rate_limited(conn, user["id"], ip, recovery.KIND_CHANGE):
                raise HTTPException(429, "Pediste varios cambios seguidos. Esperá un rato.")
            ph, salt = hash_password(a.new_password)
            token, code = recovery.create(conn, user["id"], recovery.KIND_CHANGE,
                                          {"pass_hash": ph, "salt": salt}, ip)
            pendiente = True

        try:
            conn.execute("UPDATE users SET username=?, email=? WHERE id=?",
                         (username, email, user["id"]))
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Ese nombre de usuario ya está en uso")

    if pendiente:
        link = recovery.link_for(recovery.KIND_CHANGE, token)
        subject, text, html = mailer.change_email(username, code, link, recovery.TTL_MIN)
        mailer.send(destino, subject, text, html, kind="change")
        telemetry.log_event("change_request", user["id"], username, "espera confirmación", ip)
        aviso = (f"Te mandamos un correo a {_ofuscar(destino)} para confirmar el cambio. "
                 f"Hasta que lo confirmes, sigue valiendo tu contraseña actual.")

    if email_viejo and email.lower() != email_viejo.lower():
        subject, text, html = mailer.account_email_changed(username, email_viejo, email)
        mailer.send(email_viejo, subject, text, html, kind="email-changed")
        mailer.send(email, subject, text, html, kind="email-changed")
        telemetry.log_event("email_changed", user["id"], username,
                            f"{_ofuscar(email_viejo)} → {_ofuscar(email)}", ip)

    return {"id": user["id"], "username": username, "email": email,
            "password_pendiente": pendiente, "aviso": aviso}


@router.get("/change/{token}")
def change_info(token: str):
    """¿El enlace de confirmación del cambio sigue sirviendo?"""
    with db() as conn:
        row = recovery.by_token(conn, token, recovery.KIND_CHANGE)
        if not row:
            raise HTTPException(400, "Ese enlace ya se usó o venció. Pedí el cambio de nuevo.")
        user = conn.execute("SELECT username FROM users WHERE id=?", (row["user_id"],)).fetchone()
    return {"ok": True, "username": user["username"] if user else "",
            "minutos": recovery.minutes_left(row)}


@router.post("/confirm-change")
def confirm_change(c: ConfirmChangeIn, request: Request):
    """Aplica el cambio pendiente. Con el enlace del correo o, si ya estás
    dentro de la app, con el código de seis dígitos."""
    ip = telemetry.client_ip(request)
    with db() as conn:
        if c.token:
            tok = recovery.by_token(conn, c.token, recovery.KIND_CHANGE)
        else:
            actual = optional_user(request)
            if not actual:
                raise HTTPException(401, "Entrá a tu cuenta o usá el enlace del correo")
            tok = recovery.by_code(conn, actual["id"], c.code, recovery.KIND_CHANGE)
        if not tok:
            raise HTTPException(400, "Código o enlace incorrecto (o ya venció)")
        datos = recovery.payload_of(tok)
        if not datos.get("pass_hash") or not datos.get("salt"):
            raise HTTPException(400, "Ese pedido no tiene un cambio para aplicar")
        user = conn.execute("SELECT * FROM users WHERE id=?", (tok["user_id"],)).fetchone()
        if not user:
            raise HTTPException(400, "La cuenta ya no existe")
        recovery.consume(conn, tok["id"])
        _aplicar_password(conn, user["id"], datos["pass_hash"], datos["salt"])

    telemetry.log_event("change_done", user["id"], user["username"], "confirmado por correo", ip)
    _avisar_cambio(user, "desde tu cuenta")
    return {"ok": True, "username": user["username"]}


# ── Baja de la cuenta ──────────────────────────────────────

@router.delete("/account")
def delete_account(a: DeleteAccount, request: Request, response: Response,
                   user=Depends(current_user)):
    """Borra la cuenta del usuario y todo lo suyo (campañas, personajes, bestiario)."""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
        if not verify_password(a.password, row["salt"], row["pass_hash"]):
            raise HTTPException(400, "La contraseña es incorrecta")
        # El bestiario cuelga del owner por una columna sin FK; lo borro a mano.
        conn.execute("DELETE FROM enemies WHERE owner_id=?", (user["id"],))
        # El resto (campañas, personajes, mascotas, membresías, sesiones) cae por cascada.
        conn.execute("DELETE FROM users WHERE id=?", (user["id"],))
    telemetry.log_event("account_deleted", None, user["username"], "baja voluntaria",
                        telemetry.client_ip(request))
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}
