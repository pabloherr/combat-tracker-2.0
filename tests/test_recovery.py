"""Recuperación de contraseña por correo y confirmación de cambios.

El mailer corre en modo `outbox` (sin credenciales SMTP) y sin escribir
archivos: los tests leen el mensaje desde `mailer.SENT`.
"""

import re

import pytest
from app import mailer, recovery
from app.database import db
from helpers import register


def ultimo_mail(kind=None):
    """Último mensaje generado (opcionalmente, del tipo pedido)."""
    for m in reversed(mailer.SENT):
        if kind is None or m["kind"] == kind:
            return m
    raise AssertionError(f"no se mandó ningún correo de tipo {kind}")


def codigo_de(mail):
    m = re.search(r"Código(?: de confirmación)?: (\d{6})", mail["text"])
    assert m, mail["text"]
    return m.group(1)


def token_de(mail):
    m = re.search(r"token=([\w\-]+)", mail["text"])
    assert m, mail["text"]
    return m.group(1)


# ── Olvidé mi contraseña ───────────────────────────────────

def test_forgot_manda_mail_con_codigo_y_enlace(client):
    register(client, "kal", "dm", email="kal@x.com")
    r = client.post("/api/auth/forgot", json={"identifier": "kal"})
    assert r.status_code == 200 and r.json()["ok"]
    mail = ultimo_mail("reset")
    assert mail["to"] == "kal@x.com"
    assert len(codigo_de(mail)) == 6
    assert "/recuperar?token=" in mail["text"]


def test_forgot_acepta_email_y_no_delata_cuentas(client):
    register(client, "shallan", "dm", email="shallan@x.com")
    # por email, anda
    assert client.post("/api/auth/forgot", json={"identifier": "SHALLAN@x.com"}).status_code == 200
    assert ultimo_mail("reset")["to"] == "shallan@x.com"
    # con una cuenta que no existe contesta lo mismo y no manda nada
    antes = len(mailer.SENT)
    r = client.post("/api/auth/forgot", json={"identifier": "nadie"})
    assert r.status_code == 200 and r.json()["ok"]
    assert len(mailer.SENT) == antes


def test_reset_con_token_del_enlace(make_client):
    c = make_client()
    register(c, "adolin", "dm", email="adolin@x.com")
    c.post("/api/auth/forgot", json={"identifier": "adolin"})
    token = token_de(ultimo_mail("reset"))

    # el enlace dice de quién es y cuánto le queda
    info = c.get(f"/api/auth/reset/{token}")
    assert info.status_code == 200 and info.json()["username"] == "adolin"

    assert c.post("/api/auth/reset", json={"token": token, "password": "tormenta1"}).status_code == 200
    # sale el aviso de que la contraseña cambió
    assert ultimo_mail("password-changed")["to"] == "adolin@x.com"

    d = make_client()
    assert d.post("/api/auth/login", json={"username": "adolin", "password": "secreta"}).status_code == 400
    assert d.post("/api/auth/login", json={"username": "adolin", "password": "tormenta1"}).status_code == 200


def test_reset_con_codigo(make_client):
    c = make_client()
    register(c, "renarin", "dm", email="renarin@x.com")
    c.post("/api/auth/forgot", json={"identifier": "renarin"})
    code = codigo_de(ultimo_mail("reset"))

    # código equivocado, no pasa
    assert c.post("/api/auth/reset", json={
        "identifier": "renarin", "code": "000000", "password": "tormenta1"}).status_code == 400
    # el bueno, sí
    assert c.post("/api/auth/reset", json={
        "identifier": "renarin", "code": code, "password": "tormenta1"}).status_code == 200
    d = make_client()
    assert d.post("/api/auth/login",
                  json={"username": "renarin", "password": "tormenta1"}).status_code == 200


def test_token_se_usa_una_sola_vez(client):
    register(client, "jasnah", "dm", email="jasnah@x.com")
    client.post("/api/auth/forgot", json={"identifier": "jasnah"})
    token = token_de(ultimo_mail("reset"))
    assert client.post("/api/auth/reset", json={"token": token, "password": "tormenta1"}).status_code == 200
    # el mismo enlace, de nuevo, ya no sirve
    assert client.post("/api/auth/reset", json={"token": token, "password": "otra12345"}).status_code == 400
    assert client.get(f"/api/auth/reset/{token}").status_code == 400


def test_token_vencido_no_sirve(client):
    register(client, "navani", "dm", email="navani@x.com")
    client.post("/api/auth/forgot", json={"identifier": "navani"})
    token = token_de(ultimo_mail("reset"))
    with db() as conn:
        conn.execute("UPDATE auth_tokens SET expires_at = datetime('now','-1 minute')")
    assert client.post("/api/auth/reset", json={"token": token, "password": "tormenta1"}).status_code == 400


def test_reset_cierra_las_sesiones_abiertas(make_client):
    c = make_client()
    register(c, "dalinar", "dm", email="dalinar@x.com")
    assert c.get("/api/auth/me").status_code == 200
    c.post("/api/auth/forgot", json={"identifier": "dalinar"})
    token = token_de(ultimo_mail("reset"))
    c.post("/api/auth/reset", json={"token": token, "password": "tormenta1"})
    assert c.get("/api/auth/me").status_code == 401


def test_reset_no_acepta_la_misma_contrasena(client):
    register(client, "szeth", "dm", email="szeth@x.com")
    client.post("/api/auth/forgot", json={"identifier": "szeth"})
    token = token_de(ultimo_mail("reset"))
    r = client.post("/api/auth/reset", json={"token": token, "password": "secreta"})
    assert r.status_code == 400


def test_tope_de_pedidos_por_hora(client):
    register(client, "lift", "dm", email="lift@x.com")
    for _ in range(recovery.MAX_PER_HOUR):
        assert client.post("/api/auth/forgot", json={"identifier": "lift"}).status_code == 200
    assert client.post("/api/auth/forgot", json={"identifier": "lift"}).status_code == 429


# ── Cambio desde "Mi cuenta" ───────────────────────────────

def test_cambio_de_contrasena_espera_confirmacion(make_client):
    c = make_client()
    register(c, "wit", "dm", email="wit@x.com")
    r = c.post("/api/auth/account", json={
        "username": "wit", "email": "wit@x.com",
        "current_password": "secreta", "new_password": "cuentos1"})
    assert r.status_code == 200 and r.json()["password_pendiente"] is True

    # todavía no cambió nada: la vieja sigue sirviendo
    d = make_client()
    assert d.post("/api/auth/login", json={"username": "wit", "password": "cuentos1"}).status_code == 400
    assert d.post("/api/auth/login", json={"username": "wit", "password": "secreta"}).status_code == 200

    code = codigo_de(ultimo_mail("change"))
    assert c.post("/api/auth/confirm-change", json={"code": code}).status_code == 200

    e = make_client()
    assert e.post("/api/auth/login", json={"username": "wit", "password": "cuentos1"}).status_code == 200
    assert e.post("/api/auth/logout").status_code == 200


def test_confirmar_cambio_por_enlace(make_client):
    c = make_client()
    register(c, "hoid", "dm", email="hoid@x.com")
    c.post("/api/auth/account", json={
        "username": "hoid", "email": "hoid@x.com",
        "current_password": "secreta", "new_password": "cuentos1"})
    token = token_de(ultimo_mail("change"))
    # el enlace vale sin sesión: se abre desde el correo, en otro dispositivo
    sin_sesion = make_client()
    assert sin_sesion.get(f"/api/auth/change/{token}").status_code == 200
    assert sin_sesion.post("/api/auth/confirm-change", json={"token": token}).status_code == 200
    assert sin_sesion.post("/api/auth/login",
                           json={"username": "hoid", "password": "cuentos1"}).status_code == 200


def test_cambio_pide_la_contrasena_actual(client):
    register(client, "moash", "dm", email="moash@x.com")
    r = client.post("/api/auth/account", json={
        "username": "moash", "email": "moash@x.com",
        "current_password": "equivocada", "new_password": "cuentos1"})
    assert r.status_code == 400


def test_cambiar_el_email_avisa_a_las_dos_casillas(client):
    register(client, "teft", "dm", email="teft@x.com")
    r = client.post("/api/auth/account", json={"username": "teft", "email": "teft2@x.com"})
    assert r.status_code == 200
    destinos = {m["to"] for m in mailer.SENT if m["kind"] == "email-changed"}
    assert destinos == {"teft@x.com", "teft2@x.com"}


@pytest.mark.parametrize("mala", ["", "corta", "123456"])
def test_contrasenas_que_no_pasan(client, mala):
    register(client, "kaladin", "dm", email="kaladin@x.com")
    client.post("/api/auth/forgot", json={"identifier": "kaladin"})
    token = token_de(ultimo_mail("reset"))
    assert client.post("/api/auth/reset",
                       json={"token": token, "password": mala}).status_code == 400


def test_el_registro_de_correos_no_guarda_el_codigo(client):
    """El asunto lleva el código (se ve en la notificación), pero el `mail_log`
    lo mira el admin desde el panel: ahí va tapado."""
    register(client, "vin", "dm", email="vin@x.com")
    client.post("/api/auth/forgot", json={"identifier": "vin"})
    code = codigo_de(ultimo_mail("reset"))
    with db() as conn:
        asunto = conn.execute("SELECT subject FROM mail_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert code not in asunto and "••••••" in asunto
