"""Calendario rosharano: fecha de la campaña, avance de días y notas."""

from app import roshar
from helpers import party


def _cfg(dm, cid, **kw):
    r = dm.put(f"/api/campaigns/{cid}/config", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def _cal(cli, cid):
    r = cli.get(f"/api/campaigns/{cid}/calendar")
    assert r.status_code == 200, r.text
    return r.json()


# ── La cuenta de días ──────────────────────────────────────

def test_dates_round_trip_through_the_absolute_index():
    for y, m, w, d in ((1173, 1, 1, 1), (1173, 10, 10, 5), (0, 5, 3, 2)):
        got = roshar.from_index(roshar.to_index(y, m, w, d))
        assert (got["year"], got["month"], got["week"], got["day"]) == (y, m, w, d)


def test_names_are_built_by_composition():
    # mes Jes, semana 3 (ach), día 4 (ev) → Jesach / Jesachev
    assert roshar.week_name(1, 3) == "Jesach"
    assert roshar.day_name(1, 3, 4) == "Jesachev"
    assert roshar.day_name(5, 1, 1) == "Palaheses"
    assert roshar.weekday_name(5) == "Palahel"
    # el año da la vuelta a los 500 días
    assert roshar.to_index(1174, 1, 1, 1) - roshar.to_index(1173, 1, 1, 1) == 500


# ── El calendario de una campaña ───────────────────────────

def test_the_calendar_is_off_until_the_dm_turns_it_on(make_client):
    dm, pl, cid, _ = party(make_client)
    assert _cal(dm, cid)["enabled"] is False
    assert _cal(pl, cid)["enabled"] is False
    # apagado no se puede anotar
    assert pl.post(f"/api/campaigns/{cid}/calendar/notes",
                   json={"texto": "x"}).status_code == 404

    _cfg(dm, cid, modulo_calendario=True)
    c = _cal(dm, cid)
    assert c["enabled"] and c["visible"] and c["today"]["formal"] == "1173.1.1.1"


def test_the_dm_can_keep_the_calendar_for_himself(make_client):
    dm, pl, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_calendario=True, calendario_visible=False)
    assert _cal(dm, cid)["visible"] is True
    p = _cal(pl, cid)
    # ni la fecha viaja: no hay forma de deducirla mirando la red
    assert p["visible"] is False and p["notes"] == [] and p["today"] is None
    assert pl.post(f"/api/campaigns/{cid}/calendar/notes",
                   json={"texto": "x"}).status_code == 403


def test_the_dm_can_set_the_day(make_client):
    dm, _, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_calendario=True)
    r = dm.put(f"/api/campaigns/{cid}/calendar",
               json={"year": 1174, "month": 4, "week": 7, "day": 2})
    assert r.status_code == 200, r.text
    hoy = r.json()["today"]
    assert hoy["formal"] == "1174.4.7.2" and hoy["day_name"] == "Vevaban"
    # y también desde los ajustes
    _cfg(dm, cid, cal_month=1, cal_week=1, cal_day=1)
    assert _cal(dm, cid)["today"]["formal"] == "1174.1.1.1"


def test_a_player_cannot_move_the_day(make_client):
    dm, pl, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_calendario=True)
    assert pl.put(f"/api/campaigns/{cid}/calendar",
                  json={"year": 1200}).status_code == 403


# ── El tiempo que pasa ─────────────────────────────────────

def test_a_long_rest_moves_the_calendar_one_day(make_client):
    dm, _, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_calendario=True)
    antes = _cal(dm, cid)["today"]["index"]
    assert dm.post(f"/api/campaigns/{cid}/long_rest", json={}).status_code == 200
    assert _cal(dm, cid)["today"]["index"] == antes + 1


def test_the_dm_can_skip_several_days_at_once(make_client):
    dm, _, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_calendario=True)
    antes = _cal(dm, cid)["today"]["index"]
    r = dm.post(f"/api/campaigns/{cid}/storm/advance", json={"days": 5})
    assert r.status_code == 200, r.text
    assert r.json()["storm"]["days"] == 5
    assert _cal(dm, cid)["today"]["index"] == antes + 5
    # sin cuerpo sigue pasando un solo día (el botón de siempre)
    dm.post(f"/api/campaigns/{cid}/storm/advance")
    assert _cal(dm, cid)["today"]["index"] == antes + 6


def test_skipping_days_still_runs_the_storm_cycle(make_client):
    dm, _, cid, _ = party(make_client)
    # ciclo cortito (2-3 días) para que en 10 días caiga más de una tormenta
    _cfg(dm, cid, modulo_calendario=True, storm_min=2, storm_max=3,
         storm_day=0, storm_target=3)
    r = dm.post(f"/api/campaigns/{cid}/storm/advance", json={"days": 10}).json()
    assert r["storm"]["stormed"] is True and len(r["storm"]["storms"]) >= 2


# ── Notas y pines ──────────────────────────────────────────

def test_players_can_pin_notes_when_the_dm_lets_them(make_client):
    dm, pl, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_calendario=True)
    idx = _cal(pl, cid)["today"]["index"]
    r = pl.post(f"/api/campaigns/{cid}/calendar/notes",
                json={"day_index": idx + 3, "texto": "Feria en Kholinar", "color": "#e8c840"})
    assert r.status_code == 200, r.text
    nota = r.json()["notes"][0]
    assert nota["texto"] == "Feria en Kholinar" and nota["username"] == "pl"
    assert _cal(dm, cid)["notes"][0]["day_index"] == idx + 3

    # la suya la edita y la borra; la de otro, no
    assert pl.put(f"/api/campaigns/{cid}/calendar/notes/{nota['id']}",
                  json={"texto": "Feria"}).status_code == 200
    assert pl.delete(f"/api/campaigns/{cid}/calendar/notes/{nota['id']}").status_code == 200
    assert _cal(dm, cid)["notes"] == []


def test_the_dm_can_lock_the_notes(make_client):
    dm, pl, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_calendario=True, calendario_editable=False)
    assert _cal(pl, cid)["can_edit"] is False
    assert pl.post(f"/api/campaigns/{cid}/calendar/notes",
                   json={"texto": "x"}).status_code == 403
    assert dm.post(f"/api/campaigns/{cid}/calendar/notes",
                   json={"texto": "x"}).status_code == 200
    assert len(_cal(pl, cid)["notes"]) == 1


def test_secret_notes_are_only_for_the_dm(make_client):
    dm, pl, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_calendario=True)
    dm.post(f"/api/campaigns/{cid}/calendar/notes",
            json={"texto": "Cae la tormenta eterna", "secreto": True})
    assert len(_cal(dm, cid)["notes"]) == 1
    assert _cal(pl, cid)["notes"] == []
    # un jugador no puede marcar la suya como secreta
    r = pl.post(f"/api/campaigns/{cid}/calendar/notes",
                json={"texto": "mía", "secreto": True}).json()
    assert r["notes"][0]["secreto"] is False


def test_a_player_cannot_touch_someone_elses_note(make_client):
    dm, pl, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_calendario=True)
    nid = dm.post(f"/api/campaigns/{cid}/calendar/notes",
                  json={"texto": "del DM"}).json()["notes"][0]["id"]
    assert pl.delete(f"/api/campaigns/{cid}/calendar/notes/{nid}").status_code == 403
    assert pl.put(f"/api/campaigns/{cid}/calendar/notes/{nid}",
                  json={"texto": "jaja"}).status_code == 403
