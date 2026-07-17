"""Personajes: alta, import PDF, imagen, marcos, heridas, stats, recursos D&D."""

from helpers import create_campaign, create_character, invite, make_user, party


def test_one_per_campaign_and_membership(make_client):
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm)
    # sin invitación no puede crear personaje
    r = pl.post("/api/characters", json={"name": "X", "campaign_id": cid})
    assert r.status_code == 404
    invite(dm, cid, "pl")
    create_character(pl, cid, "Kal")
    # un segundo personaje en la misma campaña es rechazado
    r = pl.post("/api/characters", json={"name": "Otro", "campaign_id": cid})
    assert r.status_code == 400


def test_import_cosmere_pdf(make_client, cosmere_pdf):
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm, system="cosmere")
    invite(dm, cid, "pl")
    r = pl.post(f"/api/characters/import-pdf?campaign_id={cid}",
                files={"file": ("cosmere.pdf", cosmere_pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    ch = pl.get("/api/characters").json()[0]
    assert ch["has_pdf"] and ch["campaign_id"] == cid


def test_import_dnd_pdf(make_client, dnd_pdf):
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm, system="dnd")
    invite(dm, cid, "pl")
    r = pl.post(f"/api/characters/import-pdf?campaign_id={cid}",
                files={"file": ("dnd.pdf", dnd_pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    assert pl.get("/api/characters").json()[0]["has_pdf"]


def test_import_wrong_pdf_rejected(make_client, dnd_pdf):
    # subir una ficha de D&D a una campaña de Cosmere debe fallar limpio (400)
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm, system="cosmere")
    invite(dm, cid, "pl")
    r = pl.post(f"/api/characters/import-pdf?campaign_id={cid}",
                files={"file": ("dnd.pdf", dnd_pdf, "application/pdf")})
    assert r.status_code == 400


def test_image_upload_serve_delete(make_client):
    dm, pl, cid, chid = party(make_client)
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    r = pl.post(f"/api/characters/{chid}/image",
                files={"file": ("p.png", png, "image/png")})
    assert r.status_code == 200
    got = pl.get(f"/api/characters/{chid}/image")
    assert got.status_code == 200 and got.content == png
    # el DM de la campaña también la ve
    assert dm.get(f"/api/characters/{chid}/image").status_code == 200
    assert pl.delete(f"/api/characters/{chid}/image").status_code == 200
    assert pl.get(f"/api/characters/{chid}/image").status_code == 404


def test_marcos_set_and_charge_couples_light(make_client):
    dm, pl, cid, chid = party(make_client, inv_max=10)
    pl.post(f"/api/characters/{chid}/marcos/set", json={"cargados": 8, "opacos": 2})
    pl.post(f"/api/characters/{chid}/stat", json={"stat": "inv", "delta": -10})  # vaciar inv
    # cargar +3 de investidura apaga 3 marcos cargados
    pl.post(f"/api/characters/{chid}/stat", json={"stat": "inv", "delta": 3})
    ch = next(m["character"] for m in pl.get(f"/api/campaigns/{cid}/roster").json()["members"])
    assert ch["inv"] == 3 and ch["marcos_light"] == 5 and ch["marcos"] == 10
    # bajar investidura NO devuelve luz
    pl.post(f"/api/characters/{chid}/stat", json={"stat": "inv", "delta": -2})
    ch = next(m["character"] for m in pl.get(f"/api/campaigns/{cid}/roster").json()["members"])
    assert ch["marcos_light"] == 5


def test_injuries(make_client):
    dm, pl, cid, chid = party(make_client)
    r = pl.post(f"/api/characters/{chid}/injuries",
                json={"name": "Brazo roto", "days": 3, "permanent": False}).json()
    iid = r["injuries"][0]["id"]
    r = pl.post(f"/api/characters/{chid}/injuries/{iid}/days", json={"delta": -1}).json()
    assert r["injuries"][0]["days"] == 2
    r = pl.delete(f"/api/characters/{chid}/injuries/{iid}").json()
    assert r["injuries"] == []


def test_stat_clamp(make_client):
    dm, pl, cid, chid = party(make_client)  # vida_max 20
    pl.post(f"/api/characters/{chid}/stat", json={"stat": "vida", "delta": -999})
    ch = next(m["character"] for m in pl.get(f"/api/campaigns/{cid}/roster").json()["members"])
    assert ch["vida"] == 0
    pl.post(f"/api/characters/{chid}/stat", json={"stat": "vida", "delta": 999})
    ch = next(m["character"] for m in pl.get(f"/api/campaigns/{cid}/roster").json()["members"])
    assert ch["vida"] == 20


def test_dnd_slots(make_client):
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm, system="dnd")
    invite(dm, cid, "pl")
    chid = create_character(pl, cid, "Mago", vida_max=12, focus_max=0, inv_max=0)
    r = pl.put(f"/api/characters/{chid}/slots", json={"levels": {"1": 3, "2": 2}}).json()
    assert r["dnd"]["slots"]["1"] == {"max": 3, "cur": 3}
    r = pl.post(f"/api/characters/{chid}/slots/spend", json={"level": 1, "delta": -1}).json()
    assert r["dnd"]["slots"]["1"]["cur"] == 2
