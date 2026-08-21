"""Panel de administración: quién entra, telemetría y gestión de cuentas."""

import pytest
from app import settings, telemetry
from app.database import db
from helpers import create_campaign, register

ADMIN_MAIL = settings.DEFAULT_ADMIN_EMAIL


@pytest.fixture
def admin(make_client):
    """Cliente logueado con la cuenta de correo de la app (la admin)."""
    c = make_client()
    register(c, "jefe", "dm", email=ADMIN_MAIL)
    return c


def test_solo_el_admin_entra(make_client, admin):
    otro = make_client()
    register(otro, "pepe", "dm", email="pepe@x.com")
    assert otro.get("/api/admin/overview").status_code == 403
    assert otro.get("/admin", follow_redirects=False).headers["location"] == "/dm"
    assert admin.get("/api/admin/overview").status_code == 200
    assert admin.get("/admin", follow_redirects=False).status_code == 200


def test_sin_sesion_no_hay_panel(client):
    assert client.get("/api/admin/overview").status_code == 401
    assert client.get("/admin", follow_redirects=False).headers["location"] == "/login"


def test_me_marca_al_admin(admin):
    assert admin.get("/api/auth/me").json()["admin"] is True


def test_login_del_admin_avisa_para_el_panel(make_client, admin):
    admin.post("/api/auth/logout")
    r = admin.post("/api/auth/login", json={"username": "jefe", "password": "secreta"})
    assert r.status_code == 200 and r.json()["admin"] is True
    # y la raíz lo lleva derecho al panel
    assert admin.get("/", follow_redirects=False).headers["location"] == "/admin"


def test_overview_trae_conteos_y_trafico(make_client, admin):
    dm = make_client(); register(dm, "otrodm", "dm", email="otrodm@x.com")
    create_campaign(dm, "Alethkar")
    telemetry.flush()

    d = admin.get("/api/admin/overview").json()
    assert d["counts"]["users"] == 2
    assert d["counts"]["campaigns"] == 1
    assert d["trafico_24h"]["requests"] > 0
    assert len(d["dias"]) == 14 and len(d["horas"]) == 24
    assert d["mail"]["mode"] in ("smtp", "outbox")
    assert any(e["kind"] == "register" for e in d["eventos"])


def test_lista_de_usuarios_con_sus_cosas(make_client, admin):
    dm = make_client(); register(dm, "sarah", "dm", email="sarah@x.com")
    create_campaign(dm, "C1")
    users = admin.get("/api/admin/users").json()
    sarah = [u for u in users if u["username"] == "sarah"][0]
    assert sarah["campanias"] == 1 and sarah["email"] == "sarah@x.com"
    assert sarah["admin"] is False
    jefe = [u for u in users if u["username"] == "jefe"][0]
    assert jefe["admin"] is True and jefe["admin_fijo"] is True
    # y el buscador filtra
    assert [u["username"] for u in admin.get("/api/admin/users?q=sar").json()] == ["sarah"]


def test_detalle_de_usuario_no_filtra_el_hash(make_client, admin):
    dm = make_client(); register(dm, "tom", "dm", email="tom@x.com")
    uid = admin.get("/api/admin/users?q=tom").json()[0]["id"]
    d = admin.get(f"/api/admin/users/{uid}").json()
    assert d["usuario"]["username"] == "tom"
    assert "pass_hash" not in d["usuario"] and "salt" not in d["usuario"]
    assert len(d["sesiones"]) == 1


def test_admin_bloquea_y_desbloquea(make_client, admin):
    victima = make_client(); register(victima, "gil", "dm", email="gil@x.com")
    uid = admin.get("/api/admin/users?q=gil").json()[0]["id"]

    assert admin.post(f"/api/admin/users/{uid}", json={"blocked": True}).status_code == 200
    assert victima.get("/api/auth/me").status_code == 401       # lo echó
    otro = make_client()
    assert otro.post("/api/auth/login",
                     json={"username": "gil", "password": "secreta"}).status_code == 403

    assert admin.post(f"/api/admin/users/{uid}", json={"blocked": False}).status_code == 200
    assert otro.post("/api/auth/login",
                     json={"username": "gil", "password": "secreta"}).status_code == 200


def test_admin_no_se_bloquea_ni_se_borra_a_si_mismo(admin):
    uid = admin.get("/api/auth/me").json()["id"]
    assert admin.post(f"/api/admin/users/{uid}", json={"blocked": True}).status_code == 400
    assert admin.delete(f"/api/admin/users/{uid}").status_code == 400


def test_admin_da_permiso_a_otra_cuenta(make_client, admin):
    otro = make_client(); register(otro, "mano", "dm", email="mano@x.com")
    uid = admin.get("/api/admin/users?q=mano").json()[0]["id"]
    assert otro.get("/api/admin/overview").status_code == 403
    admin.post(f"/api/admin/users/{uid}", json={"is_admin": True})
    assert otro.get("/api/admin/overview").status_code == 200
    admin.post(f"/api/admin/users/{uid}", json={"is_admin": False})
    assert otro.get("/api/admin/overview").status_code == 403


def test_admin_manda_reset_de_contrasena(make_client, admin):
    from test_recovery import codigo_de, ultimo_mail

    otro = make_client(); register(otro, "olvidadizo", "dm", email="olvi@x.com")
    uid = admin.get("/api/admin/users?q=olvidadizo").json()[0]["id"]
    r = admin.post(f"/api/admin/users/{uid}/reset")
    assert r.status_code == 200 and r.json()["enviado_a"] == "olvi@x.com"

    code = codigo_de(ultimo_mail("admin-reset"))
    assert otro.post("/api/auth/reset", json={
        "identifier": "olvidadizo", "code": code, "password": "nuevaclave"}).status_code == 200


def test_admin_cierra_sesiones_ajenas(make_client, admin):
    otro = make_client(); register(otro, "colgado", "dm", email="colgado@x.com")
    uid = admin.get("/api/admin/users?q=colgado").json()[0]["id"]
    r = admin.post(f"/api/admin/users/{uid}/logout")
    assert r.status_code == 200 and r.json()["cerradas"] == 1
    assert otro.get("/api/auth/me").status_code == 401


def test_admin_borra_cuentas_y_campanias(make_client, admin):
    otro = make_client(); register(otro, "chau", "dm", email="chau@x.com")
    cid = create_campaign(otro, "Se va")
    uid = admin.get("/api/admin/users?q=chau").json()[0]["id"]

    camps = admin.get("/api/admin/campaigns").json()
    assert camps[0]["dm"] == "chau" and camps[0]["miembros"] == 0

    assert admin.delete(f"/api/admin/campaigns/{cid}").status_code == 200
    assert admin.get("/api/admin/campaigns").json() == []
    assert admin.delete(f"/api/admin/users/{uid}").status_code == 200
    assert [u["username"] for u in admin.get("/api/admin/users").json()] == ["jefe"]


def test_eventos_y_correo(admin):
    admin.post("/api/auth/forgot", json={"identifier": "jefe"})
    evs = admin.get("/api/admin/events?kind=forgot").json()
    assert evs and evs[0]["kind"] == "forgot"

    correo = admin.get("/api/admin/mail").json()
    assert correo["config"]["mode"] == "outbox"
    assert any(m["kind"] == "reset" for m in correo["log"])

    prueba = admin.post("/api/admin/mail/test", json={"to": "probando@x.com"})
    assert prueba.status_code == 200 and prueba.json()["destino"] == "probando@x.com"


def test_purga_de_telemetria_vieja(admin):
    with db() as conn:
        conn.execute("INSERT INTO telemetry_events (ts, kind) VALUES (datetime('now','-99 days'),'viejo')")
    assert admin.post("/api/admin/maintenance/purge").json()["borradas"] >= 1
    assert admin.get("/api/admin/events?kind=viejo").json() == []
