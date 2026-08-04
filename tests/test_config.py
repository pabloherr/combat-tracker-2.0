"""Ajustes de la campaña: módulos que se apagan y qué ven los jugadores."""

from helpers import (SAMPLE_STATBLOCK, create_character, get_enemies,
                     import_enemy, invite, make_user, party)


def _cfg(dm, cid, **kw):
    r = dm.put(f"/api/campaigns/{cid}/config", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def _combate(dm, cid):
    """Arranca un combate con un enemigo (ya visible para los jugadores)."""
    import_enemy(dm, cid, SAMPLE_STATBLOCK)
    eid = get_enemies(dm, cid)[0]["id"]
    enc = dm.post(f"/api/campaigns/{cid}/encounters",
                  json={"name": "E", "enemies": [{"enemy_id": eid, "cantidad": 1}]}).json()["id"]
    r = dm.post(f"/api/campaigns/{cid}/combat/start/{enc}")
    assert r.status_code == 200, r.text


def _partes(cli, cid):
    return {p["name"]: p for p in cli.get(f"/api/campaigns/{cid}/combat").json()["participants"]}


# ── Módulos ────────────────────────────────────────────────

def test_the_catalog_can_be_closed_without_touching_the_inventory(make_client):
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, modulo_catalogo=False)

    assert pl.get(f"/api/campaigns/{cid}/catalog").status_code == 400
    assert dm.get(f"/api/campaigns/{cid}/items").status_code == 400
    # el inventario sigue andando: el DM le da cosas a mano
    assert pl.get(f"/api/characters/{chid}/inventory").status_code == 200
    assert dm.post(f"/api/characters/{chid}/inventory",
                   json={"name": "Cuerda"}).status_code == 200
    assert dm.get(f"/api/campaigns/{cid}/inventories").status_code == 200


def test_the_inventory_can_be_closed_without_touching_the_catalog(make_client):
    dm, pl, cid, chid = party(make_client)
    dm.post(f"/api/campaigns/{cid}/items", json={"name": "Cuerda", "precio": 3})
    iid = dm.get(f"/api/campaigns/{cid}/items").json()[0]["id"]
    _cfg(dm, cid, modulo_inventario=False)

    assert pl.get(f"/api/characters/{chid}/inventory").status_code == 400
    assert dm.get(f"/api/campaigns/{cid}/inventories").status_code == 400
    assert dm.post(f"/api/characters/{chid}/inventory",
                   json={"name": "Cuerda"}).status_code == 400
    # el catálogo queda como lista de consulta, pero no se puede agarrar nada
    assert pl.get(f"/api/campaigns/{cid}/catalog").status_code == 200
    assert pl.post(f"/api/characters/{chid}/inventory/take",
                   json={"item_id": iid, "precio": 0}).status_code == 400


def test_turning_both_off_and_back_on(make_client):
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, modulo_catalogo=False, modulo_inventario=False)
    assert pl.get(f"/api/characters/{chid}/inventory").status_code == 400
    assert pl.get(f"/api/campaigns/{cid}/catalog").status_code == 400
    _cfg(dm, cid, modulo_catalogo=True, modulo_inventario=True)
    assert pl.get(f"/api/characters/{chid}/inventory").status_code == 200
    assert pl.get(f"/api/campaigns/{cid}/catalog").status_code == 200


def test_players_are_told_which_modules_are_on(make_client):
    dm, pl, cid, chid = party(make_client)
    assert pl.get(f"/api/campaigns/{cid}/roster").json()["config"] == {
        "modulo_catalogo": True, "modulo_inventario": True, "modulo_tormentas": True}
    _cfg(dm, cid, modulo_catalogo=False)
    cfg = pl.get(f"/api/campaigns/{cid}/roster").json()["config"]
    assert cfg["modulo_catalogo"] is False and cfg["modulo_inventario"] is True


def test_the_old_single_switch_still_applies_to_both(make_client):
    """Las campañas que guardaron el interruptor viejo (`modulo_objetos`) siguen
    con los objetos apagados: vale para el catálogo y para el inventario."""
    import json

    from app.database import db

    dm, pl, cid, chid = party(make_client)
    with db() as conn:
        conn.execute("UPDATE campaigns SET config=? WHERE id=?",
                     (json.dumps({"modulo_objetos": False}), cid))
    cfg = dm.get(f"/api/campaigns/{cid}/config").json()
    assert cfg["modulo_catalogo"] is False and cfg["modulo_inventario"] is False
    assert pl.get(f"/api/characters/{chid}/inventory").status_code == 400


def test_storm_tracker_can_be_turned_off(make_client):
    dm, pl, cid, chid = party(make_client)
    assert pl.get(f"/api/campaigns/{cid}/storm").json()["enabled"] is True
    _cfg(dm, cid, modulo_tormentas=False)
    assert pl.get(f"/api/campaigns/{cid}/storm").json()["enabled"] is False


# ── Qué ven los jugadores en combate ───────────────────────

def test_color_mode_only_sends_the_band(make_client):
    """Por defecto la barra solo cambia de color: viaja el tramo (0-5), ni
    siquiera un porcentaje."""
    dm, pl, cid, chid = party(make_client)
    _combate(dm, cid)
    dm_axe = _partes(dm, cid)["Axehound"]
    pl_axe = _partes(pl, cid)["Axehound"]
    assert dm_axe["vida"] == 18 and dm_axe["vida_max"] == 18
    assert pl_axe["vida"] is None and pl_axe["vida_max"] is None
    assert pl_axe["vida_nivel"] == 5 and "vida_pct" not in pl_axe
    # focus e investidura, lo mismo
    assert pl_axe["focus"] is None and pl_axe["focus_nivel"] == 5


def test_color_bands_go_down_without_leaking_the_number(make_client):
    dm, pl, cid, chid = party(make_client)
    _combate(dm, cid)
    uid = _partes(dm, cid)["Axehound"]["uid"]

    # el Axehound tiene 18: 17 (94%), 13 (72%), 8 (44%), 3 (17%), 0
    for quita, tramo in ((1, 4), (4, 3), (5, 2), (5, 1), (3, 0)):
        dm.post(f"/api/campaigns/{cid}/combat/stat",
                json={"uid": uid, "stat": "vida", "delta": -quita})
        axe = _partes(pl, cid)["Axehound"]
        assert axe["vida_nivel"] == tramo
        assert axe["vida"] is None and "vida_pct" not in axe


def test_abstract_mode_sends_a_rounded_percentage(make_client):
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, ver_vida_enemigos="abstracto", ver_focus_enemigos="abstracto")
    _combate(dm, cid)
    pl_axe = _partes(pl, cid)["Axehound"]
    assert pl_axe["vida"] is None and pl_axe["vida_max"] is None
    assert pl_axe["vida_pct"] == 100 and "vida_nivel" not in pl_axe
    assert pl_axe["focus"] is None and pl_axe["focus_pct"] == 100


def test_exact_mode_sends_the_numbers(make_client):
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, ver_vida_enemigos="exacto")
    _combate(dm, cid)
    axe = _partes(pl, cid)["Axehound"]
    assert axe["vida"] == 18 and axe["vida_max"] == 18
    assert "vida_pct" not in axe


def test_hidden_mode_sends_nothing_at_all(make_client):
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, ver_vida_enemigos="no", ver_focus_enemigos="no", ver_inv_enemigos="no")
    _combate(dm, cid)
    axe = _partes(pl, cid)["Axehound"]
    for stat in ("vida", "focus", "inv"):
        assert axe[stat] is None and axe[f"{stat}_max"] is None
        assert f"{stat}_pct" not in axe          # ni siquiera la barra


def test_statuses_can_be_hidden(make_client):
    dm, pl, cid, chid = party(make_client)
    _combate(dm, cid)
    uid = _partes(dm, cid)["Axehound"]["uid"]
    dm.post(f"/api/campaigns/{cid}/combat/status", json={"uid": uid, "status": "Stunned"})
    assert _partes(pl, cid)["Axehound"]["statuses"] == ["Stunned"]
    _cfg(dm, cid, ver_estados_enemigos=False)
    assert _partes(pl, cid)["Axehound"]["statuses"] == []
    assert _partes(dm, cid)["Axehound"]["statuses"] == ["Stunned"]   # el DM ve todo


def test_your_own_character_is_never_masked(make_client):
    """Aunque los aliados se vean en abstracto, lo tuyo lo ves exacto."""
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, ver_vida_aliados="no")
    _combate(dm, cid)
    yo = _partes(pl, cid)["Kal"]
    assert yo["vida"] == 20 and yo["vida_max"] == 20


def test_allies_follow_their_own_setting(make_client):
    dm, pl, cid, chid = party(make_client)
    otro = make_user(make_client, "pl2", "player")
    invite(dm, cid, "pl2")
    create_character(otro, cid, "Shallan")
    _cfg(dm, cid, ver_vida_aliados="exacto")
    _combate(dm, cid)
    assert _partes(pl, cid)["Shallan"]["vida"] == 20      # exacto

    _cfg(dm, cid, ver_vida_aliados="abstracto")
    shallan = _partes(pl, cid)["Shallan"]
    assert shallan["vida"] is None and shallan["vida_pct"] == 100
    # y el enemigo sigue con su propio ajuste, no con el de los aliados
    assert _partes(pl, cid)["Axehound"]["vida"] is None


def test_the_roster_uses_the_same_rules(make_client):
    """Fuera de combate, en la pestaña Grupo, se aplica lo mismo."""
    dm, pl, cid, chid = party(make_client)
    otro = make_user(make_client, "pl2", "player")
    invite(dm, cid, "pl2")
    create_character(otro, cid, "Shallan")
    _cfg(dm, cid, ver_vida_aliados="no")

    miembros = {m["character"]["name"]: m["character"]
                for m in pl.get(f"/api/campaigns/{cid}/roster").json()["members"]}
    assert miembros["Shallan"]["vida"] is None and "vida_pct" not in miembros["Shallan"]
    assert miembros["Kal"]["vida"] == 20                 # el propio, entero
    # el DM ve a todos con sus números
    dmv = {m["character"]["name"]: m["character"]
           for m in dm.get(f"/api/campaigns/{cid}/roster").json()["members"]}
    assert dmv["Shallan"]["vida"] == 20


def test_percentages_are_rounded_so_they_dont_leak_the_number(make_client):
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, ver_vida_enemigos="abstracto")
    _combate(dm, cid)
    uid = _partes(dm, cid)["Axehound"]["uid"]
    dm.post(f"/api/campaigns/{cid}/combat/stat",
            json={"uid": uid, "stat": "vida", "delta": -1})    # 17/18 = 94.4%
    assert _partes(dm, cid)["Axehound"]["vida"] == 17
    assert _partes(pl, cid)["Axehound"]["vida_pct"] == 95      # redondeado de a 5


def test_bad_values_fall_back_to_the_default(make_client):
    dm, pl, cid, chid = party(make_client)
    cfg = _cfg(dm, cid, ver_vida_enemigos="cualquier cosa")
    assert cfg["ver_vida_enemigos"] == "color"


def test_players_cannot_change_the_settings(make_client):
    dm, pl, cid, chid = party(make_client)
    assert pl.get(f"/api/campaigns/{cid}/config").status_code == 403
    assert pl.put(f"/api/campaigns/{cid}/config",
                  json={"modulo_objetos": False}).status_code == 403
