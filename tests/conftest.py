"""Fixtures de pytest.

Regla de oro: los tests NUNCA tocan la base real `cosmere.db`. Cada test corre
contra una base sqlite temporal y aislada.

Para eso hay que apuntar `app.database.DB_PATH` a un archivo temporal ANTES de
importar `main` (que corre `init_db()` en el import). Eso se hace a nivel de
módulo acá, porque pytest importa este conftest antes que cualquier test.
"""

import tempfile
from pathlib import Path

import pytest

import app.database as database

# Base descartable para el init_db() que corre al importar main (nunca la real).
database.DB_PATH = Path(tempfile.mkdtemp()) / "boot.db"

import main  # noqa: E402  (debe ir después de fijar DB_PATH)
from app.state import combats  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def _db(tmp_path, monkeypatch):
    """Base limpia y aislada por test. Repunta DB_PATH, crea el esquema y limpia
    la cache de combate en memoria para que no se filtre estado entre tests."""
    monkeypatch.setattr(database, "DB_PATH", tmp_path / "test.db")
    database.init_db()
    combats._cache.clear()
    yield
    combats._cache.clear()


@pytest.fixture
def make_client(_db):
    """Factory de TestClient (cada uno con su cookie jar) sobre la misma DB del test."""
    return lambda: TestClient(main.app)


@pytest.fixture
def client(make_client):
    """Un TestClient suelto (sin loguear)."""
    return make_client()


@pytest.fixture
def cosmere_pdf():
    return (database.STATIC / "cosmere_sheet.pdf").read_bytes()


@pytest.fixture
def dnd_pdf():
    return (database.STATIC / "5e_sheet.pdf").read_bytes()
