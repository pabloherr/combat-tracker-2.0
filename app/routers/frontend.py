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


@router.get("/")
def home(request: Request):
    if not optional_user(request):
        return RedirectResponse("/login")
    return RedirectResponse("/dm")


@router.get("/dm")
def dm_home(request: Request):
    if not optional_user(request):
        return RedirectResponse("/login")
    return _page("home.html")


@router.get("/jugar")
def player_home(request: Request):
    if not optional_user(request):
        return RedirectResponse("/login")
    return _page("home.html")


@router.get("/login")
def login_page():
    return _page("login.html")


@router.get("/campaign/{cid}")
def dm_page(cid: int):
    return _page("dm.html")


@router.get("/play/{cid}")
def play_page(cid: int):
    return _page("player.html")
