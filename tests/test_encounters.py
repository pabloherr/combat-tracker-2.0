"""Encuentros: crear, editar (PUT) y overrides por-encuentro (no tocan el bestiario)."""

from helpers import (SAMPLE_STATBLOCK, create_campaign, get_enemies, import_enemy,
                     make_user)


def _dm_with_enemy(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    return dm, cid, eid


def test_create_and_overrides_dont_touch_bestiary(make_client):
    dm, cid, eid = _dm_with_enemy(make_client)
    enc = dm.post(f"/api/campaigns/{cid}/encounters", json={"name": "Emboscada", "enemies": [
        {"enemy_id": eid, "cantidad": 2,
         "overrides": {"name": "Axehound Alfa", "vida_max": 99, "clase": "boss"}},
    ]}).json()
    got = dm.get(f"/api/campaigns/{cid}/encounters").json()[0]["enemies"][0]
    # valores efectivos = base + override
    assert got["name"] == "Axehound Alfa" and got["vida_max"] == 99 and got["clase"] == "boss"
    # base intacta
    assert got["base"]["name"] == "Axehound" and got["base"]["vida_max"] == 18
    # el bestiario no cambió
    be = get_enemies(dm, cid)[0]
    assert be["name"] == "Axehound" and be["vida_max"] == 18 and be["clase"] == "rival"


def test_put_edits_name_qty_and_clears_override(make_client):
    dm, cid, eid = _dm_with_enemy(make_client)
    encid = dm.post(f"/api/campaigns/{cid}/encounters", json={"name": "E", "enemies": [
        {"enemy_id": eid, "cantidad": 1, "overrides": {"vida_max": 50}}]}).json()["id"]
    # PUT cambia nombre/desc/cantidad y quita el override (overrides vacío -> hereda)
    dm.put(f"/api/campaigns/{cid}/encounters/{encid}", json={
        "name": "E2", "descripcion": "de noche",
        "enemies": [{"enemy_id": eid, "cantidad": 3, "overrides": {}}]})
    e = dm.get(f"/api/campaigns/{cid}/encounters").json()[0]
    assert e["name"] == "E2" and e["descripcion"] == "de noche"
    en = e["enemies"][0]
    assert en["cantidad"] == 3
    assert en["overrides"] == {} and en["vida_max"] == 18  # vuelve a heredar del bestiario


def test_invalid_overrides_discarded(make_client):
    dm, cid, eid = _dm_with_enemy(make_client)
    encid = dm.post(f"/api/campaigns/{cid}/encounters", json={"name": "E", "enemies": [
        {"enemy_id": eid, "cantidad": 1,
         "overrides": {"clase": "hacker", "vida_max": "abc", "owner_id": 999}}]}).json()["id"]
    en = dm.get(f"/api/campaigns/{cid}/encounters").json()[0]["enemies"][0]
    assert en["overrides"] == {}


def test_overrides_applied_in_combat(make_client):
    dm, cid, eid = _dm_with_enemy(make_client)
    encid = dm.post(f"/api/campaigns/{cid}/encounters", json={"name": "E", "enemies": [
        {"enemy_id": eid, "cantidad": 2,
         "overrides": {"name": "Axehound Alfa", "vida_max": 99, "clase": "boss"}}]}).json()["id"]
    dm.post(f"/api/campaigns/{cid}/combat/start/{encid}")
    cb = dm.get(f"/api/campaigns/{cid}/combat").json()
    ens = [p for p in cb["participants"] if p["kind"] == "enemy"]
    assert len(ens) == 2
    assert ens[0]["name"] == "Axehound Alfa 1" and ens[0]["vida_max"] == 99 and ens[0]["clase"] == "boss"
    # el bestiario sigue intacto
    assert get_enemies(dm, cid)[0]["vida_max"] == 18


def test_delete_encounter(make_client):
    dm, cid, eid = _dm_with_enemy(make_client)
    encid = dm.post(f"/api/campaigns/{cid}/encounters",
                    json={"name": "E", "enemies": [{"enemy_id": eid, "cantidad": 1}]}).json()["id"]
    assert dm.delete(f"/api/campaigns/{cid}/encounters/{encid}").status_code == 200
    assert dm.get(f"/api/campaigns/{cid}/encounters").json() == []
