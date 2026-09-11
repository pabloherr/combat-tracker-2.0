"""Personajes sueltos: se crean sin campaña, se usan solos y después se
enganchan a una campaña del mismo sistema."""

from helpers import create_campaign, invite, make_user


def _solo(pl, **kw):
    body = {"name": "Vagabundo", "vida_max": 18, "focus_max": 6, "inv_max": 0, "sheet": {}}
    body.update(kw)
    r = pl.post("/api/characters", json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_a_player_creates_a_character_without_a_campaign(make_client):
    pl = make_user(make_client, "pl", "player")
    chid = _solo(pl, system="dnd")
    mine = {c["id"]: c for c in pl.get("/api/characters").json()}
    assert mine[chid]["campaign_id"] is None
    assert mine[chid]["system"] == "dnd"
    assert mine[chid]["campaign_name"] is None


def test_the_solo_roster_and_catalog_look_like_a_campaign_s(make_client):
    pl = make_user(make_client, "pl", "player")
    chid = _solo(pl)
    r = pl.get(f"/api/characters/{chid}/roster")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["solo"] is True and j["system"] == "cosmere"
    assert len(j["members"]) == 1
    m = j["members"][0]
    assert m["character"]["id"] == chid and m["character"]["vida"] == 18
    assert m["can_create_items"] is True
    assert j["config"]["modulo_inventario"] is True

    cat = pl.get(f"/api/characters/{chid}/conditions").json()
    assert "Exhausted" in [c["name"] for c in cat["condiciones"]]

    # y el dueño crea objetos sin pedirle permiso a nadie
    r = pl.post(f"/api/characters/{chid}/inventory", json={"name": "Cuerda", "slots": 1})
    assert r.status_code == 200, r.text
    inv = pl.get(f"/api/characters/{chid}/inventory").json()
    assert inv["character"]["items"][0]["name"] == "Cuerda"


def test_the_sheet_controls_work_without_a_campaign(make_client):
    pl = make_user(make_client, "pl", "player")
    chid = _solo(pl)
    assert pl.post(f"/api/characters/{chid}/stat", json={"stat": "vida", "delta": -4}).status_code == 200
    assert pl.post(f"/api/characters/{chid}/status", json={"status": "Slowed"}).status_code == 200
    ch = pl.get(f"/api/characters/{chid}/roster").json()["members"][0]["character"]
    assert ch["vida"] == 14 and ch["statuses"] == ["Slowed"]


def test_a_solo_character_joins_a_campaign_of_the_same_system(make_client):
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm, "C", system="cosmere")
    invite(dm, cid, "pl")
    chid = _solo(pl)

    r = pl.post(f"/api/characters/{chid}/link", json={"campaign_id": cid})
    assert r.status_code == 200, r.text
    mine = {c["id"]: c for c in pl.get("/api/characters").json()}
    assert mine[chid]["campaign_id"] == cid
    # ya es un personaje de la campaña: aparece en el roster del DM
    names = [m["character"]["name"] for m in dm.get(f"/api/campaigns/{cid}/roster").json()["members"]]
    assert names == ["Vagabundo"]
    # y el roster "suelto" ya no aplica
    assert pl.get(f"/api/characters/{chid}/roster").status_code == 400


def test_linking_checks_system_invitation_and_ownership(make_client):
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    otro = make_user(make_client, "otro", "player")
    cid = create_campaign(dm, "C", system="dnd")
    invite(dm, cid, "pl")
    cosmere = _solo(pl)                       # cosmere por defecto
    dnd = _solo(pl, system="dnd")

    assert pl.post(f"/api/characters/{cosmere}/link", json={"campaign_id": cid}).status_code == 400
    assert otro.post(f"/api/characters/{dnd}/link", json={"campaign_id": cid}).status_code == 404
    assert pl.post(f"/api/characters/{dnd}/link", json={"campaign_id": 999}).status_code == 404
    assert pl.post(f"/api/characters/{dnd}/link", json={"campaign_id": cid}).status_code == 200
    # una vez adentro, no se engancha dos veces
    assert pl.post(f"/api/characters/{dnd}/link", json={"campaign_id": cid}).status_code == 400


def test_a_solo_pdf_import_uses_the_requested_system(make_client, cosmere_pdf):
    pl = make_user(make_client, "pl", "player")
    r = pl.post("/api/characters/import-pdf?system=cosmere",
                files={"file": ("f.pdf", cosmere_pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    chid = r.json()["id"]
    mine = {c["id"]: c for c in pl.get("/api/characters").json()}
    assert mine[chid]["campaign_id"] is None and mine[chid]["system"] == "cosmere"
