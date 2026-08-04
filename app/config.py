"""
Configuración por campaña: qué módulos están activos y qué ven los jugadores.

Vive en `campaigns.config` como un JSON. Todo tiene un valor por defecto, así
que una campaña que nunca tocó los ajustes funciona igual que siempre.

Está en su propio módulo (y no dentro del router de campañas) porque lo
consultan varios: el combate para filtrar lo que se manda a cada jugador, los
objetos para saber si el módulo está encendido, y el tracker de tormentas.
"""

import json

# Qué tanto ve un jugador de un stat ajeno.
#   no        → ni se muestra
#   color     → una barra llena que solo cambia de color, con su etiqueta
#   abstracto → además la barra se vacía a medida que baja
#   exacto    → los números
VER_MODOS = ("no", "color", "abstracto", "exacto")

CONFIG_DEFAULTS = {
    # ── Ciclo de altas tormentas y descarga de marcos ──
    "storm_min": 8,          # mínimo de días entre tormentas
    "storm_max": 12,         # máximo de días entre tormentas
    "discharge_start": 5,    # día desde el que los marcos empiezan a apagarse
    "discharge_full": 15,    # día en que ya no queda luz
    "discharge_curve": 2.0,  # exponente: 1 = pareja; más alto = arranca más lento

    # ── Módulos que se pueden apagar en una campaña ──
    "modulo_catalogo": True,    # el catálogo de objetos del DM y el que ven ellos
    "modulo_inventario": True,  # inventario, guardados y capacidad de carga
    "modulo_tormentas": True,   # tracker de altas tormentas

    # ── Capacidad de carga: base por tamaño (la fuerza se suma aparte) ──
    "carga_pequeno": 4,
    "carga_mediano": 6,
    "carga_grande": 10,
    "carga_enorme": 15,
    "carga_gargantuesco": 20,

    # ── Qué ven los jugadores de los demás en combate (ver VER_MODOS) ──
    # Lo propio siempre se ve exacto: es tu personaje.
    "ver_vida_enemigos": "color",
    "ver_focus_enemigos": "color",
    "ver_inv_enemigos": "color",
    "ver_estados_enemigos": True,
    "ver_vida_aliados": "color",
    "ver_focus_aliados": "color",
    "ver_inv_aliados": "color",
    "ver_estados_aliados": True,
}

# Tamaño de la criatura → clave con su base de carga. La capacidad final es
# `base + Fuerza (+ lo que sumen mochilas y demás)`.
CARGA_BASE = {"Pequeño": "carga_pequeno", "Mediano": "carga_mediano",
              "Grande": "carga_grande", "Enorme": "carga_enorme",
              "Gargantuesco": "carga_gargantuesco"}

INT_KEYS = {"storm_min", "storm_max", "discharge_start", "discharge_full"} \
    | set(CARGA_BASE.values())
FLOAT_KEYS = {"discharge_curve"}
BOOL_KEYS = {"modulo_catalogo", "modulo_inventario", "modulo_tormentas",
             "ver_estados_enemigos", "ver_estados_aliados"}
MODO_KEYS = {k for k in CONFIG_DEFAULTS if k.startswith("ver_") and k not in BOOL_KEYS}

# Claves que los jugadores necesitan saber (el resto es cosa del DM).
PLAYER_KEYS = ("modulo_catalogo", "modulo_inventario", "modulo_tormentas")


def coerce(key: str, value):
    """Lleva un valor al tipo de su clave. Si no se entiende, el default."""
    try:
        if key in BOOL_KEYS:
            if isinstance(value, str):
                return value.strip().lower() not in ("", "0", "false", "no")
            return bool(value)
        if key in MODO_KEYS:
            v = str(value).strip().lower()
            return v if v in VER_MODOS else CONFIG_DEFAULTS[key]
        if key in INT_KEYS:
            return int(value)
        return float(value)
    except (TypeError, ValueError):
        return CONFIG_DEFAULTS[key]


def sane(cfg: dict) -> dict:
    """Coherencia entre los valores que dependen unos de otros."""
    cfg["storm_min"] = max(1, cfg["storm_min"])
    cfg["storm_max"] = max(cfg["storm_min"], cfg["storm_max"])
    cfg["discharge_start"] = max(1, cfg["discharge_start"])
    cfg["discharge_full"] = max(cfg["discharge_start"] + 1, cfg["discharge_full"])
    cfg["discharge_curve"] = max(0.1, min(8.0, cfg["discharge_curve"]))
    for k in CARGA_BASE.values():
        cfg[k] = max(0, min(99, cfg[k]))
    return cfg


def size_bases(cfg: dict) -> dict:
    """Base de carga por tamaño, tal como la dejó el DM."""
    return {size: cfg[k] for size, k in CARGA_BASE.items()}


def get_config(conn, cid: int) -> dict:
    row = conn.execute("SELECT config FROM campaigns WHERE id=?", (cid,)).fetchone()
    cfg = dict(CONFIG_DEFAULTS)
    if row and row["config"]:
        try:
            saved = json.loads(row["config"])
        except (ValueError, TypeError):
            saved = {}
        if isinstance(saved, dict):
            # Antes había un solo interruptor para catálogo e inventario: si una
            # campaña lo tiene guardado, vale para los dos.
            if "modulo_objetos" in saved:
                viejo = coerce("modulo_catalogo", saved["modulo_objetos"])
                cfg["modulo_catalogo"] = cfg["modulo_inventario"] = viejo
            for k, v in saved.items():
                if k in CONFIG_DEFAULTS:
                    cfg[k] = coerce(k, v)
    return sane(cfg)


def save_config(conn, cid: int, cfg: dict):
    conn.execute("UPDATE campaigns SET config=? WHERE id=?",
                 (json.dumps({k: cfg[k] for k in CONFIG_DEFAULTS}), cid))


def player_config(cfg: dict) -> dict:
    return {k: cfg[k] for k in PLAYER_KEYS}
