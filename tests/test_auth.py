"""Cuentas, sesiones y lock de rol (dm | player)."""

from helpers import register


def test_register_validations(client):
    # email inválido
    assert client.post("/api/auth/register", json={
        "username": "a", "email": "noesmail", "password": "secreta"}).status_code == 400
    # contraseña corta
    assert client.post("/api/auth/register", json={
        "username": "a", "email": "a@x.com", "password": "xx"}).status_code == 400
    # ok
    assert client.post("/api/auth/register", json={
        "username": "a", "email": "a@x.com", "password": "secreta"}).status_code == 200
    # usuario duplicado
    assert client.post("/api/auth/register", json={
        "username": "a", "email": "a2@x.com", "password": "secreta"}).status_code == 400
    # el email también es único: es la llave para recuperar la cuenta
    assert client.post("/api/auth/register", json={
        "username": "otro", "email": "A@x.com", "password": "secreta"}).status_code == 400


def test_login_ok_and_bad(make_client):
    c = make_client()
    register(c, "bob", "dm")
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={"username": "bob", "password": "bad"}).status_code == 400
    r = c.post("/api/auth/login", json={"username": "bob", "password": "secreta"})
    assert r.status_code == 200 and r.json()["role"] == "dm"


def test_login_con_email(make_client):
    """Entrar también anda poniendo el email en vez del usuario."""
    c = make_client()
    register(c, "eva", "dm", email="eva@x.com")
    c.post("/api/auth/logout")
    r = c.post("/api/auth/login", json={"username": "EVA@x.com", "password": "secreta"})
    assert r.status_code == 200 and r.json()["username"] == "eva"


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401
    register(client, "z", "player")
    me = client.get("/api/auth/me").json()
    assert me["username"] == "z" and me["role"] == "player" and me["admin"] is False


def test_role_lock_pages(make_client):
    dm = make_client(); register(dm, "dm", "dm")
    pl = make_client(); register(pl, "pl", "player")
    # una sesión de jugador no entra al panel de DM y viceversa
    r = pl.get("/dm", follow_redirects=False)
    assert r.status_code in (302, 307) and r.headers["location"] == "/jugar"
    r = dm.get("/jugar", follow_redirects=False)
    assert r.headers["location"] == "/dm"
    # y cada uno entra al suyo
    assert dm.get("/dm", follow_redirects=False).status_code == 200
    assert pl.get("/jugar", follow_redirects=False).status_code == 200
    # la raíz manda a cada quien a su panel
    assert dm.get("/", follow_redirects=False).headers["location"] == "/dm"
    assert pl.get("/", follow_redirects=False).headers["location"] == "/jugar"


def test_update_account(client):
    register(client, "dave", "dm")
    r = client.post("/api/auth/account", json={
        "username": "dave2", "email": "dave2@x.com",
        "current_password": "secreta", "new_password": ""})
    assert r.status_code == 200 and r.json()["username"] == "dave2"
    client.post("/api/auth/logout")
    # sin tocar la contraseña, sigue valiendo la de siempre
    assert client.post("/api/auth/login",
                       json={"username": "dave2", "password": "secreta"}).status_code == 200


def test_cuenta_bloqueada_no_entra(client, make_client):
    """Bloquear desde el panel echa a la cuenta y no la deja volver."""
    from app.database import db

    register(client, "malo", "dm")
    with db() as conn:
        conn.execute("UPDATE users SET blocked=1 WHERE username='malo'")
    # la sesión abierta deja de valer
    assert client.get("/api/auth/me").status_code == 401
    otro = make_client()
    assert otro.post("/api/auth/login",
                     json={"username": "malo", "password": "secreta"}).status_code == 403
