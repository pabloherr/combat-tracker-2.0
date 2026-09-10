"""Condiciones y heridas: catálogo por campaña, y las que impone una herida."""

from helpers import party


def _cat(cli, cid):
    r = cli.get(f"/api/campaigns/{cid}/conditions")
    assert r.status_code == 200, r.text
    return r.json()


def _nombres(cat):
    return [c["name"] for c in cat["condiciones"]]


def _cfg(dm, cid, **kw):
    r = dm.put(f"/api/campaigns/{cid}/config", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


# ── Catálogo ───────────────────────────────────────────────

def test_the_catalog_arrives_whole_with_its_rules_text(make_client):
    dm, pl, cid, chid = party(make_client)
    cat = _cat(pl, cid)

    # las 15 del manual, con su descripción
    assert "Exhausted" in _nombres(cat)
    assert "Unconscious" in _nombres(cat)
    exh = [c for c in cat["condiciones"] if c["name"] == "Exhausted"][0]
    assert exh["param"] == "penal"       # pide su número entre corchetes
    assert exh["apila"] is True
    assert exh["efecto"] == {"tests": True}
    assert "descanso largo" in exh["desc"]
    # una que no toca la ficha trae solo su descripción
    det = [c for c in cat["condiciones"] if c["name"] == "Determined"][0]
    assert det["efecto"] == {}
    assert det["desc"]


def test_the_dm_turns_off_a_condition_and_it_leaves_the_campaign(make_client):
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, cond_off=["Prone", "Stunned"])

    nombres = _nombres(_cat(pl, cid))
    assert "Prone" not in nombres and "Stunned" not in nombres
    assert "Slowed" in nombres
    # el catálogo entero sigue viajando: si no, el DM no podría volver a prenderla
    assert "Prone" in [c["name"] for c in _cat(dm, cid)["catalogo"]]


def test_the_dm_adds_a_condition_of_their_own_with_an_effect(make_client):
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, cond_extra=[{"name": "Mareado", "tono": "neg",
                               "desc": "Todo da vueltas.", "ef": "mov_mitad"}])

    prop = [c for c in _cat(pl, cid)["condiciones"] if c["name"] == "Mareado"]
    assert len(prop) == 1
    assert prop[0]["efecto"] == {"mov": "mitad"}
    assert prop[0]["desc"] == "Todo da vueltas."


def test_a_condition_of_their_own_without_a_name_is_ignored(make_client):
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, cond_extra=[{"name": "  ", "desc": "x"}, {"nada": 1}])
    assert "Mareado" not in _nombres(_cat(pl, cid))
    assert len(_cat(pl, cid)["condiciones"]) == 15


def test_players_read_the_catalog_but_outsiders_do_not(make_client):
    dm, pl, cid, chid = party(make_client)
    from helpers import make_user
    ajeno = make_user(make_client, "ajeno", "player")
    assert ajeno.get(f"/api/campaigns/{cid}/conditions").status_code == 403


# ── Heridas ────────────────────────────────────────────────

def test_the_injury_list_includes_diminished_for_every_attribute(make_client):
    dm, pl, cid, chid = party(make_client)
    heridas = {h["name"]: h["cond"] for h in _cat(pl, cid)["heridas"]}

    assert heridas["Diminished [Speed −1]"] == "Diminished [Speed −1]"
    assert heridas["Exhausted [−2]"] == "Exhausted [−2]"
    assert heridas["Can only use one hand"] == ""   # no cambia ningún número


def test_an_injury_keeps_the_condition_it_had_when_it_was_taken(make_client):
    dm, pl, cid, chid = party(make_client)
    r = pl.post(f"/api/characters/{chid}/injuries",
                json={"name": "Tobillo torcido", "days": 3,
                      "cond": "Diminished [Speed −2]"})
    assert r.status_code == 200, r.text
    inj = r.json()["injuries"][0]
    assert inj["cond"] == "Diminished [Speed −2]"

    # el DM cambia su lista y la herida ya sacada no se entera
    _cfg(dm, cid, her_off=["Diminished [Speed −1]"])
    guardada = pl.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]
    assert guardada["injuries"][0]["cond"] == "Diminished [Speed −2]"


def test_the_dm_adds_an_injury_of_their_own(make_client):
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, her_extra=[{"name": "Costilla rota", "cond": "Exhausted [−3]"}])

    heridas = {h["name"]: h["cond"] for h in _cat(pl, cid)["heridas"]}
    assert heridas["Costilla rota"] == "Exhausted [−3]"


# ── Condiciones con corchete: se acumulan ──────────────────

def test_a_stacking_condition_adds_one_instance_per_call(make_client):
    dm, pl, cid, chid = party(make_client)
    for st in ("Exhausted [−1]", "Exhausted [−2]", "Enhanced [Speed +2]"):
        r = pl.post(f"/api/characters/{chid}/status", json={"status": st, "add": True})
        assert r.status_code == 200, r.text
    # la misma dos veces suma dos instancias, no la apaga
    pl.post(f"/api/characters/{chid}/status", json={"status": "Exhausted [−1]", "add": True})

    st = pl.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]["statuses"]
    assert st.count("Exhausted [−1]") == 2
    assert "Enhanced [Speed +2]" in st


def test_without_add_a_condition_is_a_switch(make_client):
    dm, pl, cid, chid = party(make_client)
    pl.post(f"/api/characters/{chid}/status", json={"status": "Slowed"})
    pl.post(f"/api/characters/{chid}/status", json={"status": "Slowed"})

    st = pl.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]["statuses"]
    assert "Slowed" not in st


def test_one_instance_can_be_removed_leaving_the_others(make_client):
    dm, pl, cid, chid = party(make_client)
    pl.post(f"/api/characters/{chid}/status", json={"status": "Exhausted [−1]", "add": True})
    pl.post(f"/api/characters/{chid}/status", json={"status": "Exhausted [−1]", "add": True})
    pl.post(f"/api/characters/{chid}/status/remove_one", json={"status": "Exhausted [−1]"})

    st = pl.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]["statuses"]
    assert st.count("Exhausted [−1]") == 1
