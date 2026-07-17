"""Helpers que arman escenarios pegándole a los endpoints reales (no atajos por
DB): así los propios helpers ejercitan la API y los tests quedan legibles."""


def register(client, user, role="dm", password="xxxx", email=None):
    """Registra (y deja logueado por la cookie) a un usuario con un rol."""
    r = client.post("/api/auth/register", json={
        "username": user, "email": email or f"{user}@x.com",
        "password": password, "role": role,
    })
    assert r.status_code == 200, r.text
    return r


def make_user(make_client, user, role):
    c = make_client()
    register(c, user, role)
    return c


def create_campaign(dm, name="C", system="cosmere"):
    r = dm.post("/api/campaigns", json={"name": name, "system": system})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def invite(dm, cid, username):
    r = dm.post(f"/api/campaigns/{cid}/invite", json={"username": username})
    assert r.status_code == 200, r.text


def create_character(player, cid, name="Hero", vida_max=20, focus_max=10, inv_max=0):
    """Crea el personaje del jugador para la campaña (acepta la invitación)."""
    r = player.post("/api/characters", json={
        "name": name, "campaign_id": cid,
        "vida_max": vida_max, "focus_max": focus_max, "inv_max": inv_max,
    })
    assert r.status_code == 200, r.text
    return r.json()["id"]


def import_enemy(dm, cid, code):
    r = dm.post(f"/api/campaigns/{cid}/enemies/import", json={"code": code})
    assert r.status_code == 200, r.text
    return r.json()


def get_enemies(dm, cid):
    r = dm.get(f"/api/campaigns/{cid}/enemies")
    assert r.status_code == 200, r.text
    return r.json()


def party(make_client, campaign_name="C", system="cosmere", inv_max=0):
    """Escenario típico: DM + campaña + jugador aceptado con personaje.

    Devuelve (dm, player, cid, chid)."""
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm, campaign_name, system)
    invite(dm, cid, "pl")
    chid = create_character(pl, cid, "Kal", inv_max=inv_max)
    return dm, pl, cid, chid


# Statblock mínimo de Cosmere para tests de bestiario/mascotas.
SAMPLE_STATBLOCK = """
layout: Cosmere RPG
name: "Axehound"
tier: "Tier 1 Rival - Medium Animal"
str: 3
pdef: 13
spd: 4
health: "18 (14-22)"
focus: 2
investiture: 0
hp: 18
actions:
  - name: "Bite"
    desc: "Attack +4, reach 5 ft. **Hit:** 6 (1d6 + 3) keen damage."
  - name: "Pounce"
    desc: "The axehound leaps to a target within 15 ft."
"""
