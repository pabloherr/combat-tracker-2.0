"""Campañas: membresías, roster, config de tormenta/marcos, ciclo de tormenta."""

from helpers import (create_campaign, create_character, invite, make_user,
                     party, register)


def test_create_list_delete(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm, "Mi campaña")
    camps = dm.get("/api/campaigns").json()
    assert len(camps) == 1 and camps[0]["name"] == "Mi campaña"
    assert dm.delete(f"/api/campaigns/{cid}").status_code == 200
    assert dm.get("/api/campaigns").json() == []


def test_invite_decline(make_client):
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm)
    invite(dm, cid, "pl")
    assert len(pl.get("/api/invitations").json()) == 1
    pl.post(f"/api/campaigns/{cid}/decline")
    assert pl.get("/api/invitations").json() == []
    # invitar a alguien que no existe -> 404
    assert dm.post(f"/api/campaigns/{cid}/invite", json={"username": "fantasma"}).status_code == 404


def test_leave_deletes_character(make_client):
    dm, pl, cid, chid = party(make_client)
    # el jugador aparece en el roster con su personaje
    roster = dm.get(f"/api/campaigns/{cid}/roster").json()
    assert len(roster["members"]) == 1
    # al salir, se borra su personaje de esa campaña
    assert pl.post(f"/api/campaigns/{cid}/leave").status_code == 200
    assert dm.get(f"/api/campaigns/{cid}/roster").json()["members"] == []
    assert pl.get("/api/characters").json() == []


def test_kick_deletes_character(make_client):
    dm, pl, cid, chid = party(make_client)
    # user_id del jugador
    members = dm.get(f"/api/campaigns/{cid}/members").json()
    uid = members[0]["user_id"]
    assert dm.delete(f"/api/campaigns/{cid}/members/{uid}").status_code == 200
    assert dm.get(f"/api/campaigns/{cid}/roster").json()["members"] == []
    assert pl.get("/api/characters").json() == []


def test_config_get_put_and_clamps(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    cfg = dm.get(f"/api/campaigns/{cid}/config").json()
    assert cfg["storm_min"] == 8 and cfg["discharge_curve"] == 2.0
    # guardar valores incoherentes -> se sanean (max>=min, full>=start+1, curva 0.1..8)
    dm.put(f"/api/campaigns/{cid}/config", json={
        "storm_min": 10, "storm_max": 5,
        "discharge_start": 9, "discharge_full": 2,
        "discharge_curve": 99})
    cfg = dm.get(f"/api/campaigns/{cid}/config").json()
    assert cfg["storm_max"] >= cfg["storm_min"]
    assert cfg["discharge_full"] >= cfg["discharge_start"] + 1
    assert cfg["discharge_curve"] == 8.0


def test_config_requires_dm(make_client):
    dm, pl, cid, chid = party(make_client)
    assert pl.get(f"/api/campaigns/{cid}/config").status_code == 403


def test_storm_advance_and_reset(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    # fijar un ciclo conocido: tormenta al día 3
    dm.put(f"/api/campaigns/{cid}/config", json={"storm_day": 0, "storm_target": 3})
    day0 = dm.get(f"/api/campaigns/{cid}/storm").json()["day"]
    assert day0 == 0
    dm.post(f"/api/campaigns/{cid}/storm/advance")
    assert dm.get(f"/api/campaigns/{cid}/storm").json()["day"] == 1
    # reset vuelve el día a 0
    dm.post(f"/api/campaigns/{cid}/storm/reset")
    assert dm.get(f"/api/campaigns/{cid}/storm").json()["day"] == 0
