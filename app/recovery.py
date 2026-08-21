"""
Recuperación de contraseña y confirmación de cambios.

Un pedido crea una fila en `auth_tokens` con dos llaves para lo mismo:

  * `token`: largo y aleatorio, viaja en el enlace del correo.
  * `code`:  seis dígitos, para tipear a mano cuando el enlace no sirve
             (el mail se abre en el celular y el server está en la LAN).

Reglas: vence a los `TTL_MIN` minutos, se usa una sola vez, admite pocos
intentos de código y hay un tope de pedidos por hora (por cuenta y por IP)
para que nadie use la recuperación como un timbre.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone

from . import settings

TTL_MIN = 30            # minutos que vive un token
MAX_ATTEMPTS = 6        # intentos de código antes de invalidarlo
MAX_PER_HOUR = 5        # pedidos por hora, por cuenta y por IP

KIND_RESET = "reset"    # olvidé la contraseña (desde la pantalla de entrada)
KIND_CHANGE = "change"  # cambio pedido desde "Mi cuenta" (hay que confirmarlo)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def new_code() -> str:
    """Seis dígitos, sin sesgo (secrets, no random)."""
    return f"{secrets.randbelow(1_000_000):06d}"


def link_for(kind: str, token: str) -> str:
    ruta = "/recuperar" if kind == KIND_RESET else "/confirmar"
    return f"{settings.base_url()}{ruta}?token={token}"


def rate_limited(conn, user_id: int, ip: str, kind: str) -> bool:
    """True si esta cuenta (o esta IP) ya pidió demasiados en la última hora."""
    desde = _fmt(_now() - timedelta(hours=1))
    por_cuenta = conn.execute(
        "SELECT COUNT(*) c FROM auth_tokens WHERE user_id=? AND kind=? AND created_at >= ?",
        (user_id, kind, desde)).fetchone()["c"]
    if por_cuenta >= MAX_PER_HOUR:
        return True
    if not ip:
        return False
    por_ip = conn.execute(
        "SELECT COUNT(*) c FROM auth_tokens WHERE ip=? AND kind=? AND created_at >= ?",
        (ip, kind, desde)).fetchone()["c"]
    return por_ip >= MAX_PER_HOUR * 3


def create(conn, user_id: int, kind: str, payload: dict | None = None,
           ip: str = "") -> tuple[str, str]:
    """Crea el token del pedido y anula los anteriores del mismo tipo.

    Devuelve `(token, code)`. Solo se guarda esto: el correo es el único lugar
    donde el usuario los ve."""
    conn.execute("UPDATE auth_tokens SET used_at=datetime('now') "
                 "WHERE user_id=? AND kind=? AND used_at IS NULL", (user_id, kind))
    token = secrets.token_urlsafe(32)
    code = new_code()
    conn.execute(
        "INSERT INTO auth_tokens (user_id, kind, token, code, payload, ip, created_at, expires_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (user_id, kind, token, code, json.dumps(payload or {}), ip,
         _fmt(_now()), _fmt(_now() + timedelta(minutes=TTL_MIN))))
    return token, code


def _vivo(row) -> bool:
    return row["used_at"] is None and row["expires_at"] >= _fmt(_now()) \
        and row["attempts"] < MAX_ATTEMPTS


def by_token(conn, token: str, kind: str):
    """Fila del token del enlace, o None si no vale (usado, vencido, ajeno)."""
    if not token:
        return None
    row = conn.execute("SELECT * FROM auth_tokens WHERE token=? AND kind=?",
                       (token, kind)).fetchone()
    return row if row is not None and _vivo(row) else None


def by_code(conn, user_id: int, code: str, kind: str):
    """Fila del código de seis dígitos. Un código errado gasta un intento."""
    row = conn.execute(
        "SELECT * FROM auth_tokens WHERE user_id=? AND kind=? AND used_at IS NULL "
        "ORDER BY id DESC LIMIT 1", (user_id, kind)).fetchone()
    if row is None or not _vivo(row):
        return None
    if not secrets.compare_digest(row["code"], (code or "").strip()):
        conn.execute("UPDATE auth_tokens SET attempts = attempts + 1 WHERE id=?", (row["id"],))
        return None
    return row


def consume(conn, row_id: int):
    conn.execute("UPDATE auth_tokens SET used_at=datetime('now') WHERE id=?", (row_id,))


def payload_of(row) -> dict:
    try:
        data = json.loads(row["payload"] or "{}")
        return data if isinstance(data, dict) else {}
    except (ValueError, TypeError):
        return {}


def minutes_left(row) -> int:
    try:
        vence = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return 0
    return max(0, int((vence - _now()).total_seconds() // 60))
