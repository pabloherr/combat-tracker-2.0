"""
Cosmere Combat Tracker — Backend
================================
FastAPI + SQLite + WebSockets, con cuentas y campañas.

Punto de entrada: crea la app, inicializa la base de datos y monta los
routers. La lógica vive en el paquete `app/`:

    app/database.py         → conexión SQLite + esquema
    app/auth.py             → cuentas y sesiones
    app/access.py           → chequeos de acceso a campañas
    app/models.py           → modelos Pydantic
    app/pdf_import.py       → extracción de fichas PDF
    app/state.py            → estado del combate por campaña
    app/ws.py               → WebSockets por campaña
    app/routers/            → endpoints por dominio

Ejecutar:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app import ws
from app.database import STATIC, init_db
from app.routers import (auth, campaigns, characters, combat, encounters,
                         enemies, frontend)

init_db()

app = FastAPI(title="Cosmere Combat Tracker")

app.include_router(ws.router)
app.include_router(auth.router)
app.include_router(campaigns.router)
app.include_router(characters.router)
app.include_router(enemies.router)
app.include_router(encounters.router)
app.include_router(combat.router)
app.include_router(frontend.router)

app.mount("/static", StaticFiles(directory=STATIC), name="static")
