"""
Telemetría: qué pasa en el servidor, para el panel de administración.

Dos niveles:

  * **Eventos** (`telemetry_events`): cosas con nombre y poca frecuencia —
    login, registro, recuperación de contraseña, borrado de cuenta, acciones
    del admin. Se escriben en el momento.

  * **Peticiones** (`telemetry_requests`): una fila por request HTTP. Como
    hay muchas (polling + WS + navegación), no se escribe de a una: se juntan
    en memoria y se vuelcan cada pocos segundos en un hilo aparte. Escribir en
    SQLite dentro del ciclo de la petición agregaría contención al WAL, que ya
    comparten los WebSockets.

Las rutas se guardan normalizadas (`/api/campaigns/7/enemies` →
`/api/campaigns/{id}/enemies`) para que agrupar tenga sentido, y todo se purga
solo pasados `TELEMETRY_DAYS` días.
"""

import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import settings
from .database import db

# Momento en que arrancó el proceso (para el "uptime" del panel).
STARTED = time.time()

_FLUSH_EVERY = 5.0      # segundos entre volcados
_FLUSH_MAX = 200        # o antes, si se junta esto
_buffer: list[tuple] = []
_buf_lock = threading.Lock()
_worker: threading.Thread | None = None
_flushes = 0

_ID_SEG = re.compile(r"^\d+$")


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def normalize_path(path: str) -> str:
    """`/api/campaigns/7/enemies/12` → `/api/campaigns/{id}/enemies/{id}`."""
    partes = [("{id}" if _ID_SEG.match(p) else p) for p in path.split("/")]
    return "/".join(partes) or "/"


def client_ip(request) -> str:
    """IP del cliente. Si hay un proxy delante, la primera de X-Forwarded-For."""
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:45]
    return (request.client.host if request.client else "")[:45]


# ── Eventos ────────────────────────────────────────────────

def log_event(kind: str, user_id: int | None = None, username: str = "",
              detail: str = "", ip: str = "", ok: bool = True):
    """Registra un evento. Nunca lanza: la telemetría no rompe una petición."""
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO telemetry_events (ts, kind, user_id, username, detail, ip, ok) "
                "VALUES (?,?,?,?,?,?,?)",
                (now_utc(), kind, user_id, username or "", detail[:300], ip or "", 1 if ok else 0),
            )
    except Exception:
        pass


# ── Peticiones ─────────────────────────────────────────────

def record_request(method: str, path: str, status: int, ms: float, user_id: int | None):
    with _buf_lock:
        _buffer.append((now_utc(), method, normalize_path(path), int(status), round(ms, 1), user_id))
        lleno = len(_buffer) >= _FLUSH_MAX
    _ensure_worker()
    if lleno:
        flush()


def flush():
    """Vuelca lo juntado. Se llama sola desde el hilo y al apagar el server."""
    global _flushes
    with _buf_lock:
        if not _buffer:
            return
        filas, _buffer[:] = list(_buffer), []
    try:
        with db() as conn:
            conn.executemany(
                "INSERT INTO telemetry_requests (ts, method, path, status, ms, user_id) "
                "VALUES (?,?,?,?,?,?)", filas)
    except Exception:
        return
    _flushes += 1
    # Purga de vez en cuando: no hace falta en cada volcado.
    if _flushes % 120 == 1:
        purge_old()


def purge_old(days: int | None = None) -> int:
    """Borra telemetría más vieja que `days`. Devuelve cuántas filas cayeron."""
    limite = _since(days if days is not None else settings.telemetry_days())
    try:
        with db() as conn:
            a = conn.execute("DELETE FROM telemetry_requests WHERE ts < ?", (limite,)).rowcount
            b = conn.execute("DELETE FROM telemetry_events WHERE ts < ?", (limite,)).rowcount
            c = conn.execute("DELETE FROM mail_log WHERE ts < ?", (limite,)).rowcount
            # Los tokens vencidos hace rato tampoco tienen para qué quedarse.
            conn.execute("DELETE FROM auth_tokens WHERE expires_at < datetime('now','-2 days')")
        return (a or 0) + (b or 0) + (c or 0)
    except Exception:
        return 0


def _loop():
    while True:
        time.sleep(_FLUSH_EVERY)
        flush()


def _ensure_worker():
    global _worker
    if _worker is None or not _worker.is_alive():
        _worker = threading.Thread(target=_loop, daemon=True, name="telemetry")
        _worker.start()


# ── Consultas para el panel ────────────────────────────────

def uptime_seconds() -> int:
    return int(time.time() - STARTED)


def counts(conn) -> dict:
    """Cuántas cosas hay en la base, por tabla."""
    tablas = ("users", "campaigns", "characters", "pets", "enemies", "encounters",
              "items", "inventory", "sessions", "calendar_notes")
    out = {}
    for t in tablas:
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
        except Exception:
            out[t] = 0
    return out


def traffic(conn, hours: int = 24) -> dict:
    """Resumen de tráfico de las últimas `hours` horas."""
    desde = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "SELECT COUNT(*) n, AVG(ms) avg_ms, MAX(ms) max_ms, "
        "       SUM(CASE WHEN status >= 500 THEN 1 ELSE 0 END) e5, "
        "       SUM(CASE WHEN status >= 400 AND status < 500 THEN 1 ELSE 0 END) e4 "
        "FROM telemetry_requests WHERE ts >= ?", (desde,)).fetchone()
    p95 = conn.execute(
        "SELECT ms FROM telemetry_requests WHERE ts >= ? ORDER BY ms "
        "LIMIT 1 OFFSET (SELECT COUNT(*)*95/100 FROM telemetry_requests WHERE ts >= ?)",
        (desde, desde)).fetchone()
    return {
        "requests": row["n"] or 0,
        "avg_ms": round(row["avg_ms"] or 0, 1),
        "max_ms": round(row["max_ms"] or 0, 1),
        "p95_ms": round(p95["ms"], 1) if p95 else 0,
        "errores_5xx": row["e5"] or 0,
        "errores_4xx": row["e4"] or 0,
    }


def top_paths(conn, hours: int = 24, limit: int = 12) -> list[dict]:
    desde = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT path, COUNT(*) n, AVG(ms) avg_ms, MAX(ms) max_ms "
        "FROM telemetry_requests WHERE ts >= ? GROUP BY path ORDER BY n DESC LIMIT ?",
        (desde, limit)).fetchall()
    return [{"path": r["path"], "n": r["n"], "avg_ms": round(r["avg_ms"] or 0, 1),
             "max_ms": round(r["max_ms"] or 0, 1)} for r in rows]


def slowest_paths(conn, hours: int = 24, limit: int = 8) -> list[dict]:
    desde = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    rows = conn.execute(
        "SELECT path, COUNT(*) n, AVG(ms) avg_ms FROM telemetry_requests "
        "WHERE ts >= ? GROUP BY path HAVING n >= 3 ORDER BY avg_ms DESC LIMIT ?",
        (desde, limit)).fetchall()
    return [{"path": r["path"], "n": r["n"], "avg_ms": round(r["avg_ms"] or 0, 1)} for r in rows]


def daily_series(conn, days: int = 14) -> list[dict]:
    """Una fila por día: peticiones, errores, usuarios distintos, altas y logins."""
    desde = _since(days)
    req = {r["d"]: r for r in conn.execute(
        "SELECT substr(ts,1,10) d, COUNT(*) n, "
        "       SUM(CASE WHEN status >= 400 THEN 1 ELSE 0 END) err, "
        "       COUNT(DISTINCT user_id) users "
        "FROM telemetry_requests WHERE ts >= ? GROUP BY d", (desde,))}
    ev = {}
    for r in conn.execute(
            "SELECT substr(ts,1,10) d, kind, COUNT(*) n FROM telemetry_events "
            "WHERE ts >= ? GROUP BY d, kind", (desde,)):
        ev.setdefault(r["d"], {})[r["kind"]] = r["n"]

    hoy = datetime.now(timezone.utc).date()
    out = []
    for i in range(days - 1, -1, -1):
        d = (hoy - timedelta(days=i)).isoformat()
        r = req.get(d)
        e = ev.get(d, {})
        out.append({
            "dia": d,
            "requests": r["n"] if r else 0,
            "errores": (r["err"] or 0) if r else 0,
            "usuarios": (r["users"] or 0) if r else 0,
            "logins": e.get("login", 0),
            "altas": e.get("register", 0),
            "resets": e.get("reset_done", 0),
        })
    return out


def hourly_series(conn, hours: int = 24) -> list[dict]:
    desde = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    got = {r["h"]: r["n"] for r in conn.execute(
        "SELECT substr(ts,1,13) h, COUNT(*) n FROM telemetry_requests "
        "WHERE ts >= ? GROUP BY h", (desde,))}
    ahora = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    out = []
    for i in range(hours - 1, -1, -1):
        h = (ahora - timedelta(hours=i)).strftime("%Y-%m-%d %H")
        out.append({"hora": h, "requests": got.get(h, 0)})
    return out


def recent_events(conn, limit: int = 60, kind: str = "") -> list[dict]:
    if kind:
        rows = conn.execute(
            "SELECT * FROM telemetry_events WHERE kind = ? ORDER BY id DESC LIMIT ?",
            (kind, limit)).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM telemetry_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def event_kinds(conn, days: int = 30) -> list[dict]:
    rows = conn.execute(
        "SELECT kind, COUNT(*) n FROM telemetry_events WHERE ts >= ? "
        "GROUP BY kind ORDER BY n DESC", (_since(days),)).fetchall()
    return [{"kind": r["kind"], "n": r["n"]} for r in rows]
