"""Bestiario: import, bulk, export round-trip y opciones de mascota."""

from helpers import (SAMPLE_STATBLOCK, create_campaign, get_enemies, import_enemy,
                     make_user, party)


def test_import_and_list(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    enemies = get_enemies(dm, cid)
    assert len(enemies) == 1 and enemies[0]["name"] == "Axehound" and enemies[0]["vida_max"] == 18


def test_import_bulk(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    two = SAMPLE_STATBLOCK + "\n---\n" + SAMPLE_STATBLOCK.replace("Axehound", "Chull")
    r = dm.post(f"/api/campaigns/{cid}/enemies/import-bulk", json={"code": two}).json()
    assert r["added"] == 2 and not r["errors"]
    assert {e["name"] for e in get_enemies(dm, cid)} == {"Axehound", "Chull"}


def test_export_roundtrip(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    # ajustar color y notas para verificar que van y vuelven
    e = get_enemies(dm, cid)[0]
    e.update({"faction_color": "#ff00aa", "notas": "cuidado con el mordisco"})
    dm.put(f"/api/campaigns/{cid}/enemies/{e['id']}", json=e)

    exp = dm.get(f"/api/campaigns/{cid}/enemies/export")
    assert exp.status_code == 200 and "attachment" in exp.headers.get("content-disposition", "")

    # re-importar el YAML exportado en otra campaña reproduce la ficha
    dm2 = make_user(make_client, "dm2", "dm")
    cid2 = create_campaign(dm2)
    r = dm2.post(f"/api/campaigns/{cid2}/enemies/import-bulk", json={"code": exp.text}).json()
    assert r["added"] == 1 and not r["errors"]
    back = get_enemies(dm2, cid2)[0]
    assert back["name"] == "Axehound" and back["vida_max"] == 18
    assert back["faction_color"] == "#ff00aa" and back["notas"] == "cuidado con el mordisco"


def test_pet_options_permissions(make_client):
    dm, pl, cid, chid = party(make_client)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    # jugador no puede habilitar opciones de mascota
    assert pl.post(f"/api/campaigns/{cid}/pet-options/{eid}").status_code == 403
    # DM sí; y el miembro puede ver la lista
    assert dm.post(f"/api/campaigns/{cid}/pet-options/{eid}").status_code == 200
    opts = pl.get(f"/api/campaigns/{cid}/pet-options").json()
    assert len(opts) == 1 and opts[0]["name"] == "Axehound"
    # quitar
    assert dm.delete(f"/api/campaigns/{cid}/pet-options/{eid}").status_code == 200
    assert pl.get(f"/api/campaigns/{cid}/pet-options").json() == []


def test_delete_enemy(make_client):
    dm = make_user(make_client, "dm", "dm")
    cid = create_campaign(dm)
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    assert dm.delete(f"/api/campaigns/{cid}/enemies/{eid}").status_code == 200
    assert get_enemies(dm, cid) == []
