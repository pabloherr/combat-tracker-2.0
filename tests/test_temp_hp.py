"""PG temporales (D&D): se ponen y se sacan de a un clic, se comen el daño antes
que la vida y no sobreviven a un descanso largo."""

from helpers import make_user, party


def _ch(pl, cid):
    return pl.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]


def _yo(dm, cid):
    """El participante del jugador, como lo ve el DM."""
    parts = dm.get(f"/api/campaigns/{cid}/combat").json()["participants"]
    return next(p for p in parts if p["kind"] == "player")


def _combate(dm, cid):
    """Combate con el jugador solo (sin enemigos: no hacen falta para pegarle)."""
    encid = dm.post(f"/api/campaigns/{cid}/encounters",
                    json={"name": "E", "enemies": []}).json()["id"]
    dm.post(f"/api/campaigns/{cid}/combat/start/{encid}")
    return _yo(dm, cid)


def test_they_are_set_moved_and_cleared(make_client):
    dm, pl, cid, chid = party(make_client, system="dnd")
    assert _ch(pl, cid)["sheet"].get("hp_temp") in (None, 0)

    assert pl.post(f"/api/characters/{chid}/temp-hp", json={"value": 7}).json()["hp_temp"] == 7
    assert pl.post(f"/api/characters/{chid}/temp-hp", json={"delta": 2}).json()["hp_temp"] == 9
    assert _ch(pl, cid)["sheet"]["hp_temp"] == 9
    # nunca quedan en negativo, ni restando de más ni escribiendo cualquier cosa
    assert pl.post(f"/api/characters/{chid}/temp-hp", json={"delta": -20}).json()["hp_temp"] == 0
    assert pl.post(f"/api/characters/{chid}/temp-hp", json={"value": -3}).json()["hp_temp"] == 0
    assert pl.post(f"/api/characters/{chid}/temp-hp", json={}).status_code == 400


def test_the_dm_can_touch_them_and_a_stranger_cannot(make_client):
    dm, pl, cid, chid = party(make_client, system="dnd")
    otro = make_user(make_client, "otro", "player")
    assert dm.post(f"/api/characters/{chid}/temp-hp", json={"value": 5}).json()["hp_temp"] == 5
    assert otro.post(f"/api/characters/{chid}/temp-hp", json={"value": 99}).status_code == 404
    assert _ch(pl, cid)["sheet"]["hp_temp"] == 5


def test_there_are_no_temporary_hit_points_in_cosmere(make_client):
    dm, pl, cid, chid = party(make_client)
    r = pl.post(f"/api/characters/{chid}/temp-hp", json={"value": 5})
    assert r.status_code == 400 and "D&D" in r.json()["detail"]


def test_damage_eats_them_first_and_healing_does_not_bring_them_back(make_client):
    dm, pl, cid, chid = party(make_client, system="dnd")     # vida_max 20
    pl.post(f"/api/characters/{chid}/temp-hp", json={"value": 5})

    r = pl.post(f"/api/characters/{chid}/damage", json={"amount": 3}).json()
    assert (r["absorbed"], r["hp_temp"], r["vida"]) == (3, 2, 20)
    # el golpe que se pasa gasta lo que queda y el resto sí baja la vida
    r = pl.post(f"/api/characters/{chid}/damage", json={"amount": 6}).json()
    assert (r["absorbed"], r["hp_temp"], r["vida"]) == (2, 0, 16)

    pl.post(f"/api/characters/{chid}/stat", json={"stat": "vida", "delta": 4})
    ch = _ch(pl, cid)
    assert ch["vida"] == 20 and ch["sheet"]["hp_temp"] == 0


def test_a_sheet_imported_from_a_pdf_writes_them_as_text(make_client):
    dm, pl, cid, chid = party(make_client, system="dnd")
    pl.put(f"/api/characters/{chid}", json={
        "name": "Kal", "campaign_id": cid, "vida_max": 20, "focus_max": 10,
        "inv_max": 0, "sheet": {"hp_temp": "4"}})
    r = pl.post(f"/api/characters/{chid}/damage", json={"amount": 6}).json()
    assert (r["absorbed"], r["hp_temp"], r["vida"]) == (4, 0, 18)


def test_a_long_rest_takes_them_away(make_client):
    dm, pl, cid, chid = party(make_client, system="dnd")
    pl.post(f"/api/characters/{chid}/temp-hp", json={"value": 8})
    assert dm.post(f"/api/campaigns/{cid}/long_rest", json={}).status_code == 200
    assert _ch(pl, cid)["sheet"]["hp_temp"] == 0


def test_in_combat_the_damage_also_goes_through_them(make_client):
    dm, pl, cid, chid = party(make_client, system="dnd")
    pl.post(f"/api/characters/{chid}/temp-hp", json={"value": 6})
    me = _combate(dm, cid)
    assert me["hp_temp"] == 6          # el combate arranca con los que tenía

    # el DM le pega 4: se los come la copia temporal, la vida no se mueve
    dm.post(f"/api/campaigns/{cid}/combat/stat",
            json={"uid": me["uid"], "stat": "vida", "delta": -4})
    me = _yo(dm, cid)
    assert (me["hp_temp"], me["vida"]) == (2, 20)
    assert _ch(pl, cid)["sheet"]["hp_temp"] == 2

    # y el siguiente golpe gasta lo que queda y baja la vida por el resto
    dm.post(f"/api/campaigns/{cid}/combat/stat",
            json={"uid": me["uid"], "stat": "vida", "delta": -5})
    me = _yo(dm, cid)
    assert (me["hp_temp"], me["vida"]) == (0, 17)
    assert _ch(pl, cid)["vida"] == 17

    # curar en combate no los devuelve
    dm.post(f"/api/campaigns/{cid}/combat/stat",
            json={"uid": me["uid"], "stat": "vida", "delta": 3})
    assert _yo(dm, cid)["hp_temp"] == 0


def test_the_ficha_and_the_combat_copy_stay_together(make_client):
    dm, pl, cid, chid = party(make_client, system="dnd")
    me = _combate(dm, cid)
    assert me["hp_temp"] == 0
    # se los carga desde la ficha, en pleno combate
    pl.post(f"/api/characters/{chid}/temp-hp", json={"value": 9})
    assert _yo(dm, cid)["hp_temp"] == 9
    # y el daño desde la ficha también llega al combate
    pl.post(f"/api/characters/{chid}/damage", json={"amount": 12})
    me = _yo(dm, cid)
    assert (me["hp_temp"], me["vida"]) == (0, 17)


def _yo(dm, cid):
    parts = dm.get(f"/api/campaigns/{cid}/combat").json()["participants"]
    return next(p for p in parts if p["kind"] == "player")
