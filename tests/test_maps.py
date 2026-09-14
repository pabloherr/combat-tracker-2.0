"""Mapas de campaña: escala, puntos de interés y tiempo de viaje."""

import struct
import zlib

import pytest
from app import maps
from helpers import party


# ── Imágenes de mentira, armadas a mano ────────────────────
# El módulo lee el ancho y el alto del encabezado, así que alcanza con los
# primeros bytes de cada formato: no hace falta una imagen de verdad.

def png(w: int, h: int) -> bytes:
    cuerpo = b"IHDR" + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + cuerpo
            + struct.pack(">I", zlib.crc32(cuerpo)))


def jpeg(w: int, h: int) -> bytes:
    sof = b"\xff\xc0" + struct.pack(">H", 17) + b"\x08" + struct.pack(">HH", h, w)
    return b"\xff\xd8" + sof + b"\x03" + b"\x00" * 9


def gif(w: int, h: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", w, h) + b"\x00" * 10


# ── Helpers de escenario ───────────────────────────────────

def _cfg(dm, cid, **kw):
    r = dm.put(f"/api/campaigns/{cid}/config", json=kw)
    assert r.status_code == 200, r.text
    return r.json()


def _maps(cli, cid):
    r = cli.get(f"/api/campaigns/{cid}/maps")
    assert r.status_code == 200, r.text
    return r.json()


def _upload(dm, cid, ancho=1000, w=1000, h=500, name="Roshar", secreto=False,
            data=None):
    r = dm.post(f"/api/campaigns/{cid}/maps",
                files={"file": ("mapa.png", data or png(w, h), "image/png")},
                data={"name": name, "ancho_real": str(ancho),
                      "secreto": "true" if secreto else "false"})
    assert r.status_code == 200, r.text
    return r.json()["map"]


def _point(cli, cid, mid, name, x, y, **kw):
    r = cli.post(f"/api/campaigns/{cid}/maps/{mid}/points",
                 json={"name": name, "x": x, "y": y, **kw})
    assert r.status_code == 200, r.text
    return r.json()["point"]


@pytest.fixture
def mapa(make_client):
    """DM + jugador + campaña con el módulo prendido y un mapa de 1000 km de
    ancho sobre una lámina de 1000x500 px: 1 px = 1 km, cuentas redondas."""
    dm, pl, cid, chid = party(make_client)
    _cfg(dm, cid, modulo_mapa=True)
    m = _upload(dm, cid, ancho=1000, w=1000, h=500)
    return dm, pl, cid, m


# ── Las cuentas, sueltas ───────────────────────────────────

def test_the_scale_comes_from_the_width_alone():
    # 1000 km repartidos en 2000 px = medio km por pixel; el alto se deduce.
    assert maps.escala(2000, 1000) == 0.5
    assert maps.alto_real(2000, 800, 1000) == 400
    # sin ancho no hay escala (mapa recién subido, todavía sin calibrar)
    assert maps.escala(2000, 0) == 0


def test_distance_uses_pixels_so_it_is_the_same_in_both_axes():
    # 1000x500 px con 1000 km de ancho: 1 px = 1 km.
    d = maps.distancia(1000, 500, 1000, {"x": 0, "y": 0}, {"x": 0.3, "y": 0})
    assert d == pytest.approx(300)
    # 0.6 del alto son 300 px, que también son 300 km: la escala no se estira
    d = maps.distancia(1000, 500, 1000, {"x": 0, "y": 0}, {"x": 0, "y": 0.6})
    assert d == pytest.approx(300)
    # 300-400-500: la diagonal sale de la misma cuenta
    d = maps.distancia(1000, 500, 1000, {"x": 0, "y": 0}, {"x": 0.4, "y": 0.6})
    assert d == pytest.approx(500)


def test_the_ruler_is_the_same_formula_solved_backwards():
    # media lámina de ancho declarada como 500 km ⇒ el mapa entero mide 1000
    ancho = maps.ancho_por_regla(1000, 500, {"x": 0.25, "y": 0}, {"x": 0.75, "y": 0}, 500)
    assert ancho == pytest.approx(1000)
    # y con ese ancho la regla vuelve a medir lo que dijo el DM
    assert maps.distancia(1000, 500, ancho,
                          {"x": 0.25, "y": 0}, {"x": 0.75, "y": 0}) == pytest.approx(500)
    # dos puntos pegados no calibran nada
    assert maps.ancho_por_regla(1000, 500, {"x": 0.5, "y": 0.5}, {"x": 0.5, "y": 0.5}, 10) == 0


def test_travel_splits_the_hours_into_days_of_march():
    # 100 km a 4 km/h = 25 h de marcha; a 8 h por día, cuatro jornadas
    assert maps.viaje(100, 4, 8) == {"horas": 25.0, "dias": 3.12, "dias_enteros": 4}
    # una jornada justa no se redondea para arriba
    assert maps.viaje(32, 4, 8)["dias_enteros"] == 1
    # un transporte sin velocidad no llega a ningún lado (en vez de dividir por 0)
    assert maps.viaje(100, 0, 8) == {"horas": 0.0, "dias": 0.0, "dias_enteros": 0}


def test_image_size_is_read_from_the_header():
    assert maps.image_size(png(640, 400)) == (640, 400, "image/png")
    assert maps.image_size(jpeg(800, 480)) == (800, 480, "image/jpeg")
    assert maps.image_size(gif(300, 200)) == (300, 200, "image/gif")
    assert maps.image_size(b"esto no es una imagen") is None


# ── El módulo arranca apagado ──────────────────────────────

def test_the_map_is_off_until_the_dm_turns_it_on(make_client):
    dm, pl, cid, _ = party(make_client)
    assert _maps(dm, cid)["enabled"] is False
    assert _maps(pl, cid)["enabled"] is False
    # apagado no se sube nada
    r = dm.post(f"/api/campaigns/{cid}/maps",
                files={"file": ("m.png", png(100, 100), "image/png")},
                data={"ancho_real": "10"})
    assert r.status_code == 404

    _cfg(dm, cid, modulo_mapa=True)
    assert _maps(dm, cid)["enabled"] is True


def test_the_dm_can_keep_the_map_for_himself(mapa):
    dm, pl, cid, m = mapa
    _cfg(dm, cid, mapa_visible=False)
    assert _maps(dm, cid)["visible"] is True
    p = _maps(pl, cid)
    # ni la lista viaja: no hay forma de saber qué mapas hay mirando la red
    assert p["visible"] is False and p["maps"] == []
    assert pl.get(f"/api/campaigns/{cid}/maps/{m['id']}").status_code == 403
    assert pl.get(f"/api/campaigns/{cid}/maps/{m['id']}/image").status_code == 403


def test_a_secret_map_is_only_the_dms(mapa):
    dm, pl, cid, _ = mapa
    oculto = _upload(dm, cid, name="Guarida", secreto=True)
    assert len(_maps(dm, cid)["maps"]) == 2
    vistos = _maps(pl, cid)["maps"]
    assert [x["name"] for x in vistos] == ["Roshar"]
    assert pl.get(f"/api/campaigns/{cid}/maps/{oculto['id']}").status_code == 404


# ── Subir y escalar ────────────────────────────────────────

def test_uploading_a_map_reads_its_size_and_derives_the_rest(mapa):
    _, _, _, m = mapa
    assert (m["img_w"], m["img_h"]) == (1000, 500)
    assert m["ancho_real"] == 1000 and m["alto_real"] == 500
    assert m["escala"] == 1 and m["escalado"] is True


def test_a_map_can_be_uploaded_unscaled_and_calibrated_later(make_client):
    dm, pl, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_mapa=True)
    m = _upload(dm, cid, ancho=0, w=1000, h=500)
    assert m["escalado"] is False
    # sin escala no se puede medir: el error lo dice
    r = dm.post(f"/api/campaigns/{cid}/maps/{m['id']}/measure",
                json={"puntos": [{"x": 0, "y": 0}, {"x": 1, "y": 0}]})
    assert r.status_code == 400 and "escala" in r.json()["detail"]

    r = dm.post(f"/api/campaigns/{cid}/maps/{m['id']}/calibrate",
                json={"x1": 0.25, "y1": 0.5, "x2": 0.75, "y2": 0.5, "distancia": 500})
    assert r.status_code == 200, r.text
    assert r.json()["map"]["ancho_real"] == pytest.approx(1000)


def test_a_file_that_is_not_an_image_is_rejected(make_client):
    dm, _, cid, _ = party(make_client)
    _cfg(dm, cid, modulo_mapa=True)
    r = dm.post(f"/api/campaigns/{cid}/maps",
                files={"file": ("m.png", b"no soy una imagen", "image/png")},
                data={"ancho_real": "10"})
    assert r.status_code == 400


def test_replacing_the_image_keeps_the_scale_and_the_points(mapa):
    dm, _, cid, m = mapa
    mid = m["id"]
    _point(dm, cid, mid, "Kholinar", 0.5, 0.5)
    # la misma lámina al doble de resolución
    r = dm.post(f"/api/campaigns/{cid}/maps/{mid}/image",
                files={"file": ("m.png", png(2000, 1000), "image/png")})
    assert r.status_code == 200, r.text
    nuevo = r.json()["map"]
    assert (nuevo["img_w"], nuevo["img_h"]) == (2000, 1000)
    # el mapa sigue midiendo lo mismo en el mundo; cambia la escala por pixel
    assert nuevo["ancho_real"] == 1000 and nuevo["escala"] == 0.5
    pts = dm.get(f"/api/campaigns/{cid}/maps/{mid}").json()["points"]
    assert (pts[0]["x"], pts[0]["y"]) == (0.5, 0.5)


def test_the_image_is_served_to_the_table_and_deleting_the_map_cleans_up(mapa):
    dm, pl, cid, m = mapa
    r = pl.get(f"/api/campaigns/{cid}/maps/{m['id']}/image")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/")

    _point(dm, cid, m["id"], "Urithiru", 0.1, 0.1)
    assert dm.delete(f"/api/campaigns/{cid}/maps/{m['id']}").status_code == 200
    assert _maps(dm, cid)["maps"] == []
    # el punto se fue con el mapa (cascada), no quedó colgado
    assert dm.get(f"/api/campaigns/{cid}/maps/{m['id']}").status_code == 404


# ── Puntos de interés ──────────────────────────────────────

def test_players_mark_points_when_the_dm_lets_them(mapa):
    dm, pl, cid, m = mapa
    mid = m["id"]
    p = _point(pl, cid, mid, "La posada", 0.2, 0.3, descripcion="Cerveza cara")
    assert p["name"] == "La posada" and p["descripcion"] == "Cerveza cara"
    assert _maps(pl, cid)["maps"][0]["n_puntos"] == 1

    _cfg(dm, cid, mapa_editable=False)
    r = pl.post(f"/api/campaigns/{cid}/maps/{mid}/points",
                json={"name": "Otro", "x": 0.4, "y": 0.4})
    assert r.status_code == 403
    # el DM sigue pudiendo, y el jugador sigue viendo el mapa
    _point(dm, cid, mid, "Torre", 0.6, 0.6)
    assert len(dm.get(f"/api/campaigns/{cid}/maps/{mid}").json()["points"]) == 2


def test_a_player_only_edits_his_own_points(mapa):
    dm, pl, cid, m = mapa
    mid = m["id"]
    mio = _point(pl, cid, mid, "Mío", 0.2, 0.2)
    ajeno = _point(dm, cid, mid, "Del DM", 0.8, 0.8)

    r = pl.put(f"/api/campaigns/{cid}/maps/{mid}/points/{mio['id']}",
               json={"name": "Mío v2", "x": 0.25, "y": 0.25})
    assert r.status_code == 200, r.text
    assert pl.put(f"/api/campaigns/{cid}/maps/{mid}/points/{ajeno['id']}",
                  json={"name": "Robado"}).status_code == 403
    assert pl.delete(f"/api/campaigns/{cid}/maps/{mid}/points/{ajeno['id']}").status_code == 403
    # el DM sí puede con cualquiera
    assert dm.delete(f"/api/campaigns/{cid}/maps/{mid}/points/{mio['id']}").status_code == 200


def test_secret_points_never_travel_to_the_players(mapa):
    dm, pl, cid, m = mapa
    mid = m["id"]
    _point(dm, cid, mid, "A la vista", 0.1, 0.1)
    escondido = _point(dm, cid, mid, "Emboscada", 0.9, 0.9, secreto=True)
    assert escondido["secreto"] is True

    vistos = pl.get(f"/api/campaigns/{cid}/maps/{mid}").json()["points"]
    assert [p["name"] for p in vistos] == ["A la vista"]
    # tampoco se puede medir contra un punto que no se ve
    r = pl.post(f"/api/campaigns/{cid}/maps/{mid}/measure",
                json={"point_ids": [escondido["id"]], "puntos": [{"x": 0, "y": 0}]})
    assert r.status_code == 404


def test_a_player_cannot_make_his_point_secret(mapa):
    dm, pl, cid, m = mapa
    p = _point(pl, cid, m["id"], "Nada secreto", 0.5, 0.5, secreto=True)
    assert p["secreto"] is False


def test_coordinates_stay_inside_the_image(mapa):
    dm, _, cid, m = mapa
    p = _point(dm, cid, m["id"], "Fuera", 5, -3)
    assert (p["x"], p["y"]) == (1.0, 0.0)


# ── Transportes ────────────────────────────────────────────

def test_the_first_map_seeds_the_suggested_transports(mapa):
    dm, _, cid, _ = mapa
    modos = _maps(dm, cid)["modes"]
    assert [m["name"] for m in modos] == [m["name"] for m in maps.DEFAULT_MODES]


def test_the_dm_adds_edits_and_removes_transports(mapa):
    dm, pl, cid, _ = mapa
    r = dm.post(f"/api/campaigns/{cid}/travel-modes",
                json={"name": "Chull de carga", "icono": "🐚",
                      "velocidad": 3, "horas_dia": 10})
    assert r.status_code == 200, r.text
    chull = [m for m in r.json()["modes"] if m["name"] == "Chull de carga"][0]

    r = dm.put(f"/api/campaigns/{cid}/travel-modes/{chull['id']}",
               json={"name": "Chull de carga", "velocidad": 2.5})
    assert r.status_code == 200
    editado = [m for m in r.json()["modes"] if m["id"] == chull["id"]][0]
    # lo que no se manda no se pisa: sigue con sus 10 h de jornada
    assert editado["velocidad"] == 2.5 and editado["horas_dia"] == 10

    # los jugadores miran, no tocan
    assert pl.post(f"/api/campaigns/{cid}/travel-modes",
                   json={"name": "Trampa", "velocidad": 999}).status_code == 403
    assert pl.get(f"/api/campaigns/{cid}/travel-modes").status_code == 200

    r = dm.delete(f"/api/campaigns/{cid}/travel-modes/{chull['id']}")
    assert chull["id"] not in [m["id"] for m in r.json()["modes"]]


def test_restoring_the_defaults_does_not_duplicate_what_is_there(mapa):
    dm, _, cid, _ = mapa
    for m in _maps(dm, cid)["modes"][1:]:
        dm.delete(f"/api/campaigns/{cid}/travel-modes/{m['id']}")
    r = dm.post(f"/api/campaigns/{cid}/travel-modes/defaults")
    nombres = [m["name"] for m in r.json()["modes"]]
    assert sorted(nombres) == sorted(m["name"] for m in maps.DEFAULT_MODES)


# ── Medir ──────────────────────────────────────────────────

def test_measuring_two_points_gives_distance_and_a_time_per_transport(mapa):
    dm, pl, cid, m = mapa
    mid = m["id"]
    a = _point(dm, cid, mid, "Kholinar", 0.1, 0.5)
    b = _point(dm, cid, mid, "Urithiru", 0.5, 0.5)   # 400 px = 400 km

    r = pl.post(f"/api/campaigns/{cid}/maps/{mid}/measure",
                json={"point_ids": [a["id"], b["id"]]})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["distancia"] == pytest.approx(400) and out["unidad"] == "km"
    a_pie = [v for v in out["viajes"] if v["name"] == "A pie"][0]
    assert a_pie["horas"] == 100.0 and a_pie["dias_enteros"] == 13


def test_a_route_adds_up_its_legs(mapa):
    dm, _, cid, m = mapa
    mid = m["id"]
    r = dm.post(f"/api/campaigns/{cid}/maps/{mid}/measure",
                json={"puntos": [{"x": 0, "y": 0}, {"x": 0.3, "y": 0},
                                 {"x": 0.3, "y": 0.4}]})
    out = r.json()
    # 300 px + 200 px (0.4 del alto de 500)
    assert [t["distancia"] for t in out["tramos"]] == [300, 200]
    assert out["distancia"] == 500 and out["paradas"] == 3


def test_measuring_needs_at_least_two_stops(mapa):
    dm, _, cid, m = mapa
    r = dm.post(f"/api/campaigns/{cid}/maps/{m['id']}/measure",
                json={"puntos": [{"x": 0.1, "y": 0.1}]})
    assert r.status_code == 400


def test_the_unit_is_the_campaigns_and_travels_with_every_answer(mapa):
    dm, pl, cid, m = mapa
    _cfg(dm, cid, mapa_unidad="mi")
    assert _maps(pl, cid)["unidad"] == "mi"
    r = pl.post(f"/api/campaigns/{cid}/maps/{m['id']}/measure",
                json={"puntos": [{"x": 0, "y": 0}, {"x": 1, "y": 0}]})
    assert r.json()["unidad"] == "mi"
    # una unidad inventada cae en km, no rompe la campaña
    _cfg(dm, cid, mapa_unidad="parasangas")
    assert _maps(dm, cid)["unidad"] == "km"


# ── Nadie de afuera ────────────────────────────────────────

def test_someone_outside_the_campaign_sees_nothing(mapa, make_client):
    from helpers import make_user
    dm, _, cid, m = mapa
    fuera = make_user(make_client, "colado", "player")
    assert fuera.get(f"/api/campaigns/{cid}/maps").status_code == 403
    assert fuera.get(f"/api/campaigns/{cid}/maps/{m['id']}/image").status_code == 403
    assert fuera.post(f"/api/campaigns/{cid}/maps/{m['id']}/points",
                      json={"name": "x", "x": 0, "y": 0}).status_code == 403
