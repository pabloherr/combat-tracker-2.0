"""Cuentas, sesiones y lock de rol (dm | player)."""

from helpers import register


def test_register_validations(client):
    # email inválido
    assert client.post("/api/auth/register", json={
        "username": "a", "email": "noesmail", "password": "xxxx"}).status_code == 400
    # contraseña corta
    assert client.post("/api/auth/register", json={
        "username": "a", "email": "a@x.com", "password": "xx"}).status_code == 400
    # ok
    assert client.post("/api/auth/register", json={
        "username": "a", "email": "a@x.com", "password": "xxxx"}).status_code == 200
    # usuario duplicado
    assert client.post("/api/auth/register", json={
        "username": "a", "email": "a2@x.com", "password": "xxxx"}).status_code == 400


def test_login_ok_and_bad(make_client):
    c = make_client()
    register(c, "bob", "dm")
    c.post("/api/auth/logout")
    assert c.post("/api/auth/login", json={"username": "bob", "password": "bad"}).status_code == 400
    r = c.post("/api/auth/login", json={"username": "bob", "password": "xxxx"})
    assert r.status_code == 200 and r.json()["role"] == "dm"


def test_me_requires_auth(client):
    assert client.get("/api/auth/me").status_code == 401
    register(client, "z", "player")
    me = client.get("/api/auth/me").json()
    assert me["username"] == "z" and me["role"] == "player"


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


def test_reset_password(make_client):
    c = make_client()
    register(c, "carol", "dm", email="carol@x.com")
    # email incorrecto -> rechaza
    assert c.post("/api/auth/reset", json={
        "username": "carol", "email": "otro@x.com", "password": "newpass"}).status_code == 400
    # email correcto -> cambia y cierra sesiones
    assert c.post("/api/auth/reset", json={
        "username": "carol", "email": "carol@x.com", "password": "newpass"}).status_code == 200
    # login con la nueva anda; con la vieja no
    d = make_client()
    assert d.post("/api/auth/login", json={"username": "carol", "password": "xxxx"}).status_code == 400
    assert d.post("/api/auth/login", json={"username": "carol", "password": "newpass"}).status_code == 200


def test_update_account(client):
    register(client, "dave", "dm")
    r = client.post("/api/auth/account", json={
        "username": "dave2", "email": "dave2@x.com",
        "current_password": "xxxx", "new_password": "yyyy"})
    assert r.status_code == 200 and r.json()["username"] == "dave2"
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "dave2", "password": "yyyy"}).status_code == 200
