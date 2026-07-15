"""Rutas que sirven las páginas HTML."""

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse

from ..auth import optional_user
from ..database import STATIC

router = APIRouter(tags=["frontend"])

# El navegador siempre revalida el HTML para no quedarse con una versión vieja.
_NO_CACHE = {"Cache-Control": "no-cache"}


def _page(name: str) -> FileResponse:
    return FileResponse(STATIC / name, headers=_NO_CACHE)


def _role_home(u: dict) -> str:
    return "/jugar" if u.get("role") == "player" else "/dm"


@router.get("/")
def home(request: Request):
    u = optional_user(request)
    if not u:
        return RedirectResponse("/login")
    return RedirectResponse(_role_home(u))


@router.get("/dm")
def dm_home(request: Request):
    # El modo queda fijo al iniciar sesión: una sesión de jugador no entra acá.
    u = optional_user(request)
    if not u:
        return RedirectResponse("/login")
    if u.get("role") == "player":
        return RedirectResponse("/jugar")
    return _page("home.html")


@router.get("/jugar")
def player_home(request: Request):
    u = optional_user(request)
    if not u:
        return RedirectResponse("/login")
    if u.get("role") != "player":
        return RedirectResponse("/dm")
    return _page("home.html")


@router.get("/login")
def login_page():
    return _page("login.html")


@router.get("/campaign/{cid}")
def dm_page(cid: int, request: Request):
    u = optional_user(request)
    if not u:
        return RedirectResponse("/login")
    if u.get("role") == "player":
        return RedirectResponse("/jugar")
    return _page("dm.html")


@router.get("/play/{cid}")
def play_page(cid: int, request: Request):
    u = optional_user(request)
    if not u:
        return RedirectResponse("/login")
    if u.get("role") != "player":
        return RedirectResponse("/dm")
    return _page("player.html")
