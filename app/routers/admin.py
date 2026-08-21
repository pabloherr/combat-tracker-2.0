"""
API del panel de administración (`/admin`).

Todo lo de acá exige una cuenta admin (ver `app/admin.py`): por defecto, la que
tiene el email de la app. Son tres cosas: mirar telemetría, administrar cuentas
y campañas, y controlar el correo saliente.

Nada de esto toca el estado de combate: son lecturas agregadas y operaciones de
mantenimiento sobre las mismas tablas que ya usa el resto de la app.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request

from .. import admin as adm
from .. import mailer, recovery, settings, telemetry
from ..database import DB_PATH, db
from ..models import AdminUserUpdate, MailTestIn
from ..version import VERSION

router = APIRouter(prefix="/api/admin", tags=["admin"],
                   dependencies=[Depends(adm.current_admin)])

# Una sesión cuenta como "activa" si dio señales en este rato.
ACTIVA_MINUTOS = 15


def _db_size() -> int:
    total = 0
    for suf in ("", "-wal", "-shm"):
        p = DB_PATH.parent / (DB_PATH.name + suf)
        if p.exists():
            total += p.stat().st_size
    return total


@router.get("/overview")
def overview(request: Request):
    """Foto del servidor: cuántas cosas hay, cuánto tráfico hubo y cómo está el correo."""
    telemetry.flush()   # que lo del último rato entre en los números
    with db() as conn:
        activas = conn.execute(
            "SELECT COUNT(*) c FROM sessions WHERE last_seen >= datetime('now', ?)",
            (f"-{ACTIVA_MINUTOS} minutes",)).fetchone()["c"]
        online = conn.execute(
            "SELECT COUNT(DISTINCT user_id) c FROM sessions WHERE last_seen >= datetime('now', ?)",
            (f"-{ACTIVA_MINUTOS} minutes",)).fetchone()["c"]
        nuevos = conn.execute(
            "SELECT COUNT(*) c FROM users WHERE created_at >= datetime('now','-7 days')"
        ).fetchone()["c"]
        mail_fallidos = conn.execute(
            "SELECT COUNT(*) c FROM mail_log WHERE ok=0 AND ts >= datetime('now','-7 days')"
        ).fetchone()["c"]
        pendientes = conn.execute(
            "SELECT COUNT(*) c FROM auth_tokens WHERE used_at IS NULL "
            "AND expires_at >= datetime('now')").fetchone()["c"]
        datos = {
            "version": VERSION,
            "uptime": telemetry.uptime_seconds(),
            "db_bytes": _db_size(),
            "db_path": str(DB_PATH),
            "counts": telemetry.counts(conn),
            "sesiones_activas": activas,
            "usuarios_online": online,
            "usuarios_nuevos_7d": nuevos,
            "tokens_pendientes": pendientes,
            "mail_fallidos_7d": mail_fallidos,
            "trafico_24h": telemetry.traffic(conn, 24),
            "trafico_7d": telemetry.traffic(conn, 24 * 7),
            "top_paths": telemetry.top_paths(conn, 24),
            "lentos": telemetry.slowest_paths(conn, 24),
            "eventos": telemetry.recent_events(conn, 25),
            "eventos_tipos": telemetry.event_kinds(conn, 30),
            "horas": telemetry.hourly_series(conn, 24),
            "dias": telemetry.daily_series(conn, 14),
            "mail": mailer.status(),
            "admins": sorted(settings.admin_emails()),
            "retencion_dias": settings.telemetry_days(),
        }
    return datos


@router.get("/series")
def series(days: int = 14):
    days = max(2, min(90, days))
    with db() as conn:
        return {"dias": telemetry.daily_series(conn, days),
                "horas": telemetry.hourly_series(conn, 24)}


# ── Cuentas ────────────────────────────────────────────────

@router.get("/users")
def list_users(q: str = "", limit: int = 200):
    """Cuentas con lo que hizo cada una: campañas, personajes, última entrada."""
    limit = max(1, min(500, limit))
    like = f"%{q.strip()}%"
    with db() as conn:
        rows = conn.execute(
            "SELECT u.id, u.username, u.email, u.created_at, u.last_login, "
            "       COALESCE(u.login_count,0) login_count, COALESCE(u.blocked,0) blocked, "
            "       COALESCE(u.is_admin,0) is_admin, "
            "       (SELECT COUNT(*) FROM campaigns c WHERE c.dm_id=u.id) campanias, "
            "       (SELECT COUNT(*) FROM characters ch WHERE ch.owner_id=u.id) personajes, "
            "       (SELECT COUNT(*) FROM enemies e WHERE e.owner_id=u.id) enemigos, "
            "       (SELECT COUNT(*) FROM items i WHERE i.owner_id=u.id) objetos, "
            "       (SELECT COUNT(*) FROM sessions s WHERE s.user_id=u.id) sesiones, "
            "       (SELECT MAX(s.last_seen) FROM sessions s WHERE s.user_id=u.id) visto "
            "FROM users u "
            "WHERE (? = '' OR u.username LIKE ? OR u.email LIKE ?) "
            "ORDER BY u.id", (q.strip(), like, like)).fetchall()
        conf = adm.by_email(conn)
        out = []
        for r in rows:
            d = dict(r)
            d["admin"] = bool(d["is_admin"]) or r["id"] in conf
            d["admin_fijo"] = r["id"] in conf     # viene de la configuración: no se saca
            out.append(d)
    return out


@router.get("/users/{uid}")
def user_detail(uid: int):
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "No existe esa cuenta")
        sesiones = [dict(s) for s in conn.execute(
            "SELECT role, ip, user_agent, created_at, last_seen FROM sessions "
            "WHERE user_id=? ORDER BY last_seen DESC", (uid,)).fetchall()]
        campanias = [dict(c) for c in conn.execute(
            "SELECT id, name, system, created_at, "
            "  (SELECT COUNT(*) FROM campaign_members m WHERE m.campaign_id=c.id) miembros "
            "FROM campaigns c WHERE dm_id=? ORDER BY id", (uid,)).fetchall()]
        personajes = [dict(c) for c in conn.execute(
            "SELECT id, name, campaign_id, created_at FROM characters "
            "WHERE owner_id=? ORDER BY id", (uid,)).fetchall()]
        eventos = [dict(e) for e in conn.execute(
            "SELECT * FROM telemetry_events WHERE user_id=? ORDER BY id DESC LIMIT 40",
            (uid,)).fetchall()]
        correos = [dict(m) for m in conn.execute(
            "SELECT * FROM mail_log WHERE to_addr=? ORDER BY id DESC LIMIT 20",
            ((row["email"] or ""),)).fetchall()]
        conf = adm.by_email(conn)
    u = dict(row)
    for k in ("pass_hash", "salt"):
        u.pop(k, None)
    u["admin"] = bool(u.get("is_admin")) or row["id"] in conf
    u["admin_fijo"] = row["id"] in conf
    return {"usuario": u, "sesiones": sesiones, "campanias": campanias,
            "personajes": personajes, "eventos": eventos, "correos": correos}


@router.post("/users/{uid}")
def update_user(uid: int, cambios: AdminUserUpdate, request: Request,
                admin=Depends(adm.current_admin)):
    """Cambia nombre, email, bloqueo o permiso de admin de una cuenta."""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "No existe esa cuenta")
        conf = adm.by_email(conn)
        hechos = []

        if cambios.username is not None and cambios.username.strip() != row["username"]:
            nuevo = cambios.username.strip()
            if not nuevo:
                raise HTTPException(400, "El usuario no puede quedar vacío")
            try:
                conn.execute("UPDATE users SET username=? WHERE id=?", (nuevo, uid))
            except sqlite3.IntegrityError:
                raise HTTPException(400, "Ese nombre de usuario ya está en uso")
            hechos.append(f"usuario → {nuevo}")

        if cambios.email is not None and cambios.email.strip().lower() != (row["email"] or "").lower():
            nuevo = cambios.email.strip()
            if nuevo and ("@" not in nuevo or "." not in nuevo.split("@")[-1]):
                raise HTTPException(400, "Poné un email válido")
            ocupado = conn.execute(
                "SELECT id FROM users WHERE lower(email)=lower(?) AND email <> '' AND id <> ?",
                (nuevo, uid)).fetchone()
            if ocupado:
                raise HTTPException(400, "Ese email ya está usado por otra cuenta")
            conn.execute("UPDATE users SET email=? WHERE id=?", (nuevo, uid))
            hechos.append("email")

        if cambios.blocked is not None and bool(cambios.blocked) != bool(row["blocked"]):
            if cambios.blocked and (uid == admin["id"] or uid in conf):
                raise HTTPException(400, "No se puede bloquear una cuenta de administración")
            conn.execute("UPDATE users SET blocked=? WHERE id=?", (1 if cambios.blocked else 0, uid))
            if cambios.blocked:
                conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
            hechos.append("bloqueada" if cambios.blocked else "desbloqueada")

        if cambios.is_admin is not None and bool(cambios.is_admin) != bool(row["is_admin"]):
            if not cambios.is_admin and uid in conf:
                raise HTTPException(400, "Esa cuenta es administradora por configuración "
                                         "(ADMIN_EMAILS): sacale el permiso desde el .env")
            conn.execute("UPDATE users SET is_admin=? WHERE id=?",
                         (1 if cambios.is_admin else 0, uid))
            hechos.append("admin" if cambios.is_admin else "sin admin")

    telemetry.log_event("admin_user_update", admin["id"], admin["username"],
                        f"#{uid}: {', '.join(hechos) or 'sin cambios'}",
                        telemetry.client_ip(request))
    return {"ok": True, "cambios": hechos}


@router.post("/users/{uid}/reset")
def admin_reset(uid: int, request: Request, admin=Depends(adm.current_admin)):
    """Dispara una recuperación de contraseña para esa cuenta y le manda el correo."""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "No existe esa cuenta")
        email = (row["email"] or "").strip()
        if not email:
            raise HTTPException(400, "Esa cuenta no tiene email: no hay a dónde mandarlo")
        token, code = recovery.create(conn, uid, recovery.KIND_RESET,
                                      ip=telemetry.client_ip(request))
        username = row["username"]
    link = recovery.link_for(recovery.KIND_RESET, token)
    subject, text, html = mailer.admin_reset_notice(username, code, link, recovery.TTL_MIN)
    mailer.send(email, subject, text, html, kind="admin-reset")
    telemetry.log_event("admin_reset", admin["id"], admin["username"],
                        f"para {username}", telemetry.client_ip(request))
    return {"ok": True, "enviado_a": email, "vence_en": recovery.TTL_MIN}


@router.post("/users/{uid}/logout")
def admin_logout(uid: int, request: Request, admin=Depends(adm.current_admin)):
    """Cierra todas las sesiones abiertas de esa cuenta."""
    with db() as conn:
        n = conn.execute("DELETE FROM sessions WHERE user_id=?", (uid,)).rowcount
    telemetry.log_event("admin_logout", admin["id"], admin["username"],
                        f"#{uid}: {n} sesiones", telemetry.client_ip(request))
    return {"ok": True, "cerradas": n or 0}


@router.delete("/users/{uid}")
def admin_delete_user(uid: int, request: Request, admin=Depends(adm.current_admin)):
    """Borra una cuenta y todo lo suyo. No se puede borrar la propia ni otra admin."""
    with db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not row:
            raise HTTPException(404, "No existe esa cuenta")
        if uid == admin["id"]:
            raise HTTPException(400, "No podés borrar tu propia cuenta desde el panel")
        if uid in adm.admin_ids(conn):
            raise HTTPException(400, "Sacale primero el permiso de administración")
        conn.execute("DELETE FROM enemies WHERE owner_id=?", (uid,))
        conn.execute("DELETE FROM items WHERE owner_id=?", (uid,))
        conn.execute("DELETE FROM users WHERE id=?", (uid,))
    telemetry.log_event("admin_user_delete", admin["id"], admin["username"],
                        f"borró {row['username']}", telemetry.client_ip(request))
    return {"ok": True}


# ── Campañas ───────────────────────────────────────────────

@router.get("/campaigns")
def list_campaigns():
    with db() as conn:
        rows = conn.execute(
            "SELECT c.id, c.name, c.system, c.created_at, c.day_count, "
            "       u.username dm, u.id dm_id, "
            "       (SELECT COUNT(*) FROM campaign_members m WHERE m.campaign_id=c.id) miembros, "
            "       (SELECT COUNT(*) FROM characters ch WHERE ch.campaign_id=c.id) personajes, "
            "       (SELECT COUNT(*) FROM encounters e WHERE e.campaign_id=c.id) encuentros "
            "FROM campaigns c LEFT JOIN users u ON u.id=c.dm_id ORDER BY c.id").fetchall()
    return [dict(r) for r in rows]


@router.delete("/campaigns/{cid}")
def admin_delete_campaign(cid: int, request: Request, admin=Depends(adm.current_admin)):
    with db() as conn:
        row = conn.execute("SELECT name FROM campaigns WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "No existe esa campaña")
        conn.execute("DELETE FROM campaigns WHERE id=?", (cid,))
    telemetry.log_event("admin_campaign_delete", admin["id"], admin["username"],
                        f"borró {row['name']}", telemetry.client_ip(request))
    return {"ok": True}


# ── Eventos y correo ───────────────────────────────────────

@router.get("/events")
def events(limit: int = 100, kind: str = ""):
    limit = max(1, min(500, limit))
    with db() as conn:
        return telemetry.recent_events(conn, limit, kind.strip())


@router.get("/requests")
def requests_log(limit: int = 100, status: int = 0):
    """Últimas peticiones registradas (para mirar errores de cerca)."""
    telemetry.flush()
    limit = max(1, min(500, limit))
    with db() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM telemetry_requests WHERE status >= ? ORDER BY id DESC LIMIT ?",
                (status, limit)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM telemetry_requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


@router.get("/mail")
def mail_state(limit: int = 50):
    limit = max(1, min(200, limit))
    with db() as conn:
        log = [dict(r) for r in conn.execute(
            "SELECT * FROM mail_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()]
    # El cuerpo de los mensajes no sale por la API: lleva los códigos de
    # recuperación, y el panel no los necesita para nada.
    return {"config": mailer.status(), "log": log}


@router.post("/mail/test")
def mail_test(t: MailTestIn, request: Request, admin=Depends(adm.current_admin)):
    """Manda un correo de prueba y cuenta qué pasó (sin hilo: acá se espera)."""
    destino = (t.to or "").strip() or (admin.get("email") or "").strip() or settings.mail_user()
    subject, text, html = mailer.test_email(destino)
    ok, detalle = mailer.send_now(destino, subject, text, html, kind="test")
    telemetry.log_event("admin_mail_test", admin["id"], admin["username"],
                        f"{destino}: {detalle}", telemetry.client_ip(request), ok=ok)
    return {"ok": ok, "detalle": detalle, "destino": destino}


# ── Mantenimiento ──────────────────────────────────────────

@router.post("/maintenance/purge")
def purge(request: Request, days: int = 0, admin=Depends(adm.current_admin)):
    """Borra telemetría vieja. `days=0` usa la retención configurada."""
    n = telemetry.purge_old(days or None)
    telemetry.log_event("admin_purge", admin["id"], admin["username"], f"{n} filas",
                        telemetry.client_ip(request))
    return {"ok": True, "borradas": n}


@router.post("/maintenance/reload-config")
def reload_config(request: Request, admin=Depends(adm.current_admin)):
    """Relee el `.env` sin reiniciar el server (útil tras cargar el SMTP)."""
    settings.reload_env()
    telemetry.log_event("admin_reload_config", admin["id"], admin["username"], "",
                        telemetry.client_ip(request))
    return {"ok": True, "mail": mailer.status(), "admins": sorted(settings.admin_emails())}
