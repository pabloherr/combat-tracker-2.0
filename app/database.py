"""
Acceso a la base de datos SQLite.

Expone el contextmanager `db()` para obtener una conexión con commit
automático y claves foráneas activas, la función `init_db()` que crea el
esquema, y las rutas `DB_PATH` / `STATIC`.
"""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "cosmere.db"
STATIC = ROOT / "static"


@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with db() as conn:
        conn.executescript("""
        -- ── Cuentas y sesiones ─────────────────────────────
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT DEFAULT '',
            pass_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── Campañas y membresías ──────────────────────────
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            dm_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── Personajes de jugador ──────────────────────────
        CREATE TABLE IF NOT EXISTS characters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            vida_max INTEGER NOT NULL DEFAULT 20,
            focus_max INTEGER NOT NULL DEFAULT 10,
            inv_max INTEGER NOT NULL DEFAULT 0,
            vida INTEGER,
            focus INTEGER,
            inv INTEGER,
            statuses TEXT DEFAULT '[]',
            sheet TEXT DEFAULT '{}',        -- JSON: ficha completa extraída del PDF
            has_pdf INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS character_pdfs (
            character_id INTEGER PRIMARY KEY REFERENCES characters(id) ON DELETE CASCADE,
            pdf BLOB NOT NULL
        );

        -- Mascotas de un personaje (ficha estilo enemigo, cargada por el jugador)
        CREATE TABLE IF NOT EXISTS pets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            character_id INTEGER NOT NULL REFERENCES characters(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            vida_max INTEGER NOT NULL DEFAULT 10,
            focus_max INTEGER NOT NULL DEFAULT 0,
            inv_max INTEGER NOT NULL DEFAULT 0,
            vida INTEGER,
            focus INTEGER,
            inv INTEGER,
            statuses TEXT DEFAULT '[]',
            acciones TEXT DEFAULT '[]',
            stats TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS campaign_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            character_id INTEGER REFERENCES characters(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'invited',   -- invited | accepted
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(campaign_id, user_id)
        );

        -- ── Bestiario y encuentros (por campaña) ───────────
        CREATE TABLE IF NOT EXISTS enemies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            tipo TEXT DEFAULT '',
            clase TEXT DEFAULT 'rival',    -- minion | rival | boss
            vida_max INTEGER NOT NULL DEFAULT 20,
            focus_max INTEGER NOT NULL DEFAULT 10,
            inv_max INTEGER NOT NULL DEFAULT 0,
            acciones TEXT DEFAULT '[]',   -- JSON: [{nombre, coste, descripcion}]
            notas TEXT DEFAULT '',
            faction_color TEXT DEFAULT '',
            stats TEXT DEFAULT '{}'
        );

        CREATE TABLE IF NOT EXISTS encounters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER REFERENCES campaigns(id) ON DELETE CASCADE,
            name TEXT NOT NULL,
            descripcion TEXT DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS encounter_enemies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            encounter_id INTEGER NOT NULL REFERENCES encounters(id) ON DELETE CASCADE,
            enemy_id INTEGER NOT NULL REFERENCES enemies(id) ON DELETE CASCADE,
            cantidad INTEGER NOT NULL DEFAULT 1
        );

        -- ── Estado de combate por campaña ──────────────────
        CREATE TABLE IF NOT EXISTS combats (
            campaign_id INTEGER PRIMARY KEY REFERENCES campaigns(id) ON DELETE CASCADE,
            data TEXT NOT NULL
        );
        """)

        # Migración: email en usuarios (para recuperar contraseña).
        ucols = {r["name"] for r in conn.execute("PRAGMA table_info(users)")}
        if "email" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN email TEXT DEFAULT ''")

        # Migración: agrega campaign_id a bestiario/encuentros de bases existentes.
        ecols = {r["name"] for r in conn.execute("PRAGMA table_info(enemies)")}
        if "campaign_id" not in ecols:
            conn.execute("ALTER TABLE enemies ADD COLUMN campaign_id INTEGER")
        enccols = {r["name"] for r in conn.execute("PRAGMA table_info(encounters)")}
        if "campaign_id" not in enccols:
            conn.execute("ALTER TABLE encounters ADD COLUMN campaign_id INTEGER")

        # Migración: el bestiario pasa a ser por DM (owner_id), no por campaña.
        # Se comparte entre todas las campañas de un mismo DM.
        ecols = {r["name"] for r in conn.execute("PRAGMA table_info(enemies)")}
        if "owner_id" not in ecols:
            conn.execute("ALTER TABLE enemies ADD COLUMN owner_id INTEGER")
            # Backfill: dueño = el DM de la campaña a la que pertenecía cada enemigo.
            conn.execute(
                "UPDATE enemies SET owner_id = ("
                "  SELECT c.dm_id FROM campaigns c WHERE c.id = enemies.campaign_id"
                ") WHERE owner_id IS NULL AND campaign_id IS NOT NULL"
            )
