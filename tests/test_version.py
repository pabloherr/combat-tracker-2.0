"""La versión se muestra en todas las pantallas y se puede consultar por API."""

from app.version import VERSION
from helpers import make_user, party


def test_api_version(client):
    r = client.get("/api/version")
    assert r.status_code == 200 and r.json() == {"version": VERSION}


def test_version_tag_in_every_page(make_client):
    dm, pl, cid, chid = party(make_client)
    tag = f"v{VERSION}"
    # login (sin sesión), panel del DM, panel del jugador y las dos vistas de campaña
    assert tag in make_client().get("/login").text
    assert tag in dm.get("/dm").text
    assert tag in pl.get("/jugar").text
    assert tag in dm.get(f"/campaign/{cid}").text
    assert tag in pl.get(f"/play/{cid}").text


def test_page_still_serves_full_html(make_client):
    """Inyectar el cartel no debe romper la página: sigue llegando entera."""
    dm = make_user(make_client, "dm", "dm")
    html = dm.get("/dm").text
    assert html.rstrip().endswith("</html>")
    assert html.count("</body>") == 1          # no se duplicó
    assert '<div class="app-version"' in html
    assert html.index('class="app-version"') < html.index("</body>")
