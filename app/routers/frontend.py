"""Rutas que sirven las páginas HTML."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ..auth import optional_user
from ..database import STATIC
from ..version import VERSION

router = APIRouter(tags=["frontend"])

# El navegador siempre revalida el HTML para no quedarse con una versión vieja.
_NO_CACHE = {"Cache-Control": "no-cache"}

# Cartel con la versión, abajo a la derecha. Se inyecta al servir la página en
# vez de escribirlo en cada HTML: así aparece en todas (incluidas las que se
# agreguen después) y para publicar una versión nueva solo se toca version.py.
_VERSION_TAG = (
    '<div class="app-version" style="position:fixed;right:8px;bottom:6px;z-index:60;'
    "font-family:var(--font-h);font-size:10px;letter-spacing:.08em;"
    'color:var(--text3);opacity:.75;pointer-events:none;user-select:none">'
    f"v{VERSION}</div>"
)


def _page(name: str) -> HTMLResponse:
    html = (STATIC / name).read_text(encoding="utf-8")
    # `1` = solo la primera aparición; si el HTML no tuviera </body>, queda igual.
    return HTMLResponse(html.replace("</body>", _VERSION_TAG + "</body>", 1),
                        headers=_NO_CACHE)


@router.get("/api/version")
def api_version():
    """Para saber qué versión está corriendo el servidor sin abrir una página."""
    return {"version": VERSION}


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
