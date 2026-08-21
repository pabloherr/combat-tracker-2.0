"""
Cosmere Combat Tracker — Backend
================================
FastAPI + SQLite + WebSockets, con cuentas y campañas.

Punto de entrada: crea la app, inicializa la base de datos y monta los
routers. La lógica vive en el paquete `app/`:

    app/database.py         → conexión SQLite + esquema
    app/auth.py             → cuentas y sesiones
    app/recovery.py         → tokens de recuperación de contraseña
    app/mailer.py           → correo saliente (SMTP o carpeta outbox/)
    app/telemetry.py        → eventos y métricas para el panel de admin
    app/settings.py         → configuración del server (correo, admins, URL)
    app/access.py           → chequeos de acceso a campañas
    app/models.py           → modelos Pydantic
    app/pdf_import.py       → extracción de fichas PDF
    app/state.py            → estado del combate por campaña
    app/ws.py               → WebSockets por campaña
    app/routers/            → endpoints por dominio

Ejecutar:
    uvicorn main:app --host 0.0.0.0 --port 8000
"""

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles

from app import telemetry, ws
from app.auth import COOKIE_NAME, session_user_id
from app.database import STATIC, init_db
from app.routers import (admin, auth, campaigns, characters, combat, encounters,
                         enemies, frontend, items)

init_db()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Al apagar, lo que quedó juntado en memoria se guarda igual.
    telemetry.flush()


app = FastAPI(title="Cosmere Combat Tracker", lifespan=lifespan)


@app.middleware("http")
async def telemetria(request: Request, call_next):
    """Anota cada petición para el panel: ruta, estado y cuánto tardó.

    No escribe en la base acá: `record_request` junta en memoria y un hilo
    vuelca cada pocos segundos. Los archivos estáticos no cuentan (son ruido y
    los sirve Starlette sin pasar por la app)."""
    t0 = time.perf_counter()
    respuesta = await call_next(request)
    ruta = request.url.path
    if not ruta.startswith("/static"):
        uid = session_user_id(request.cookies.get(COOKIE_NAME))
        telemetry.record_request(request.method, ruta, respuesta.status_code,
                                 (time.perf_counter() - t0) * 1000, uid)
    return respuesta


app.include_router(ws.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(campaigns.router)
app.include_router(characters.router)
app.include_router(enemies.router)
app.include_router(encounters.router)
app.include_router(items.router)
app.include_router(combat.router)
app.include_router(frontend.router)

app.mount("/static", StaticFiles(directory=STATIC), name="static")
