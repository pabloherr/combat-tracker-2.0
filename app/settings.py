"""
Ajustes del servidor: correo saliente, cuentas de administrador y URL pública.

No son ajustes de campaña (eso vive en `app/config.py`), sino cosas del
despliegue. Se leen del entorno o de un archivo `.env` en la raíz del proyecto,
que NO va al repo (tiene la contraseña de aplicación del correo).

Ejemplo de `.env` (ver `.env.example`):

    MAIL_USER=gmcito234@gmail.com
    MAIL_PASSWORD=abcd efgh ijkl mnop      # contraseña de aplicación de Google
    BASE_URL=http://192.168.0.10:8000

Si no hay `MAIL_PASSWORD`, el correo no se cae: cada mensaje se escribe en
`outbox/` como archivo `.eml` y el enlace de recuperación queda igual en el
registro. Así la app funciona en una LAN sin internet.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"
OUTBOX = ROOT / "outbox"

# Cuenta de correo de la app (y, por lo tanto, la del panel de administración).
DEFAULT_ADMIN_EMAIL = "gmcito234@gmail.com"

_env_cache: dict[str, str] | None = None


def _load_env_file() -> dict[str, str]:
    """Lee el `.env` (KEY=VALOR por línea, `#` comenta). Vacío si no existe."""
    data: dict[str, str] = {}
    if not ENV_FILE.exists():
        return data
    # utf-8-sig: si el archivo se creó desde PowerShell viene con BOM, y sin esto
    # la primera clave quedaría ilegible (`﻿MAIL_USER`).
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        # Se admiten comillas alrededor del valor (útil si tiene espacios).
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        data[k.strip().upper()] = v
    return data


def env(key: str, default: str = "") -> str:
    """Valor de configuración: primero el entorno real, después el `.env`."""
    global _env_cache
    real = os.environ.get(key)
    if real is not None:
        return real
    if _env_cache is None:
        _env_cache = _load_env_file()
    return _env_cache.get(key.upper(), default)


def reload_env():
    """Vuelve a leer el `.env` (lo usa el panel tras cambiar la configuración)."""
    global _env_cache
    _env_cache = None


# ── Correo saliente ────────────────────────────────────────

def mail_host() -> str:
    return env("MAIL_HOST", "smtp.gmail.com")


def mail_port() -> int:
    try:
        return int(env("MAIL_PORT", "587"))
    except ValueError:
        return 587


def mail_user() -> str:
    return env("MAIL_USER", DEFAULT_ADMIN_EMAIL).strip()


def mail_password() -> str:
    # Google exige "contraseña de aplicación": la normal no entra por SMTP.
    return env("MAIL_PASSWORD", "").replace(" ", "")


def mail_from_name() -> str:
    return env("MAIL_FROM_NAME", "Cosmere Combat Tracker")


def mail_enabled() -> bool:
    """Hay credenciales para mandar de verdad. Si no, modo `outbox`."""
    return bool(mail_user() and mail_password())


# ── URL pública ────────────────────────────────────────────

def base_url() -> str:
    """Base para armar los enlaces de los correos. En LAN conviene poner la IP
    del server (`http://192.168.0.10:8000`) o el enlace no le sirve a nadie."""
    return env("BASE_URL", "http://localhost:8000").rstrip("/")


# ── Administración ─────────────────────────────────────────

def admin_emails() -> set[str]:
    """Emails con acceso al panel. Por defecto, la cuenta de correo de la app."""
    raw = env("ADMIN_EMAILS", DEFAULT_ADMIN_EMAIL)
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


# ── Retención de telemetría ────────────────────────────────

def telemetry_days() -> int:
    """Días de historial que se guardan (lo viejo se purga solo)."""
    try:
        return max(1, int(env("TELEMETRY_DAYS", "45")))
    except ValueError:
        return 45
