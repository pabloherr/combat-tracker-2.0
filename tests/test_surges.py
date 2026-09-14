"""Potencias radiantes (Surges): salen del PDF a la columna de su atributo, y el
jugador las pone o las saca a mano editando la ficha."""

import io

import pypdf
from helpers import make_user, party

from app.pdf_import import SURGES, parse_character_pdf, surge_en


def _rellenar(pdf: bytes, textos: dict, marcar=()) -> bytes:
    """Rellena la ficha: textos por nombre de campo y casillas de rango marcadas."""
    vals = dict(textos)
    vals.update({n: "/Yes" for n in marcar})
    w = pypdf.PdfWriter(clone_from=io.BytesIO(pdf))
    for page in w.pages:
        w.update_page_form_field_values(page, vals, auto_regenerate=False)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def _por_nombre(sheet, cat):
    return {x["name"]: x for x in sheet["skills"][cat]}


# ── La tabla ───────────────────────────────────────────────

def test_the_ten_surges_and_how_they_are_written():
    assert len(SURGES) == 10
    assert SURGES["Abrasion"] == "SPD" and SURGES["Tension"] == "STR"
    assert SURGES["Cohesion"] == "WIL" and SURGES["Transportation"] == "INT"
    assert SURGES["Gravitation"] == "AWA" and SURGES["Adhesion"] == "PRE"
    # se escriben en inglés o en castellano, con o sin tildes
    assert surge_en("Gravitación") == "Gravitation"
    assert surge_en("iluminacion") == "Illumination"
    assert surge_en("Surge of Tension") == "Tension"
    assert surge_en("Cultura") is None
    # en un talento se pide el nombre exacto, para no confundirla
    assert surge_en("Division of Spoils") == "Division"
    assert surge_en("Division of Spoils", exacto=True) is None


# ── Desde el PDF ───────────────────────────────────────────

def test_a_surge_written_in_the_free_row_lands_in_its_column(cosmere_pdf):
    pdf = _rellenar(cosmere_pdf, {
        "char_name": "Kaladin", "char_awareness": "3", "char_presence": "2",
        # la escribe en la fila libre de la columna espiritual, con 2 rangos
        "char_spirit_custom_name": "Gravitación",
    }, marcar=["char_spirit_custom_rank_1", "char_spirit_custom_rank_2"])
    s = parse_character_pdf(pdf)["sheet"]

    g = _por_nombre(s, "spiritual")["Gravitation"]
    assert g["surge"] is True and g["attr"] == "AWA"
    assert g["rank"] == 2 and g["value"] == 5        # AWA 3 + 2 rangos
    assert "Gravitation" not in _por_nombre(s, "physical")


def test_it_goes_by_its_attribute_even_if_written_in_another_column(cosmere_pdf):
    pdf = _rellenar(cosmere_pdf, {
        "char_strength": "4", "char_intellect": "1",
        # Tensión es de Fuerza: aunque la escriba en la columna cognitiva,
        # se guarda en la física
        "char_cog_custom_name": "Tension", "char_cog_custom": "6",
    })
    s = parse_character_pdf(pdf)["sheet"]
    assert "Tension" not in _por_nombre(s, "cognitive")
    t = _por_nombre(s, "physical")["Tension"]
    assert t["surge"] is True and t["attr"] == "STR" and t["value"] == 6


def test_the_second_surge_can_come_from_the_talents(cosmere_pdf):
    # Un Corredor del Viento tiene Adhesión y Gravitación, las dos espirituales,
    # y en la hoja hay una sola fila libre por columna.
    pdf = _rellenar(cosmere_pdf, {
        "char_awareness": "3", "char_presence": "2",
        "char_spirit_custom_name": "Adhesion", "char_spirit_custom": "4",
        "char_talent_name_1": "Gravitation",
        "char_talent_desc_1": "Cambiá la dirección de la gravedad.",
        "char_talent_name_2": "Division of Spoils",
    })
    s = parse_character_pdf(pdf)["sheet"]
    esp = _por_nombre(s, "spiritual")

    assert esp["Adhesion"]["value"] == 4
    assert esp["Gravitation"]["surge"] is True and esp["Gravitation"]["value"] == 3
    # el talento se conserva: la descripción sirve
    assert s["talents"][0]["name"] == "Gravitation"
    # y un talento que solo la menciona no cuenta como potencia
    assert "Division" not in _por_nombre(s, "cognitive")


def test_a_custom_skill_that_is_not_a_surge_stays_where_it_was(cosmere_pdf):
    pdf = _rellenar(cosmere_pdf, {
        "char_speed": "3",
        "char_phys_custom_name": "Acrobacias de circo",
        "char_phys_custom_attr": "Velocidad",
        "char_phys_custom": "5",
    })
    s = parse_character_pdf(pdf)["sheet"]
    x = _por_nombre(s, "physical")["Acrobacias de circo"]
    assert x["value"] == 5 and x["attr"] == "SPD" and "surge" not in x


def test_ranks_alone_are_enough_for_a_skill(cosmere_pdf):
    # sin modificador escrito pero con rangos marcados: el modificador se calcula
    pdf = _rellenar(cosmere_pdf, {"char_strength": "2"},
                    marcar=["char_athletics_rank_1", "char_athletics_rank_2",
                            "char_athletics_rank_3"])
    s = parse_character_pdf(pdf)["sheet"]
    at = _por_nombre(s, "physical")["Athletics"]
    assert at["rank"] == 3 and at["value"] == 5
    # y una habilidad sin nada anotado sigue sin aparecer
    assert "Thievery" not in _por_nombre(s, "physical")


def test_the_blank_sheet_has_no_surges(cosmere_pdf):
    s = parse_character_pdf(cosmere_pdf)["sheet"]
    todas = [x for cat in ("physical", "cognitive", "spiritual")
             for x in s["skills"][cat]]
    assert [x for x in todas if x.get("surge")] == []


# ── Importando de verdad, y editando a mano ────────────────

def test_the_import_keeps_the_surge_in_the_sheet(make_client, cosmere_pdf):
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = dm.post("/api/campaigns", json={"name": "C", "system": "cosmere"}).json()["id"]
    dm.post(f"/api/campaigns/{cid}/invite", json={"username": "pl"})

    pdf = _rellenar(cosmere_pdf, {
        "char_name": "Shallan", "char_intellect": "4",
        "char_cog_custom_name": "Transformation", "char_cog_custom": "6",
    })
    r = pl.post(f"/api/characters/import-pdf?campaign_id={cid}",
                files={"file": ("f.pdf", pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    ch = pl.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]
    cog = {x["name"]: x for x in ch["sheet"]["skills"]["cognitive"]}
    assert cog["Transformation"]["surge"] is True
    assert cog["Transformation"]["attr"] == "WIL"    # el atributo es el de la potencia


def test_a_player_adds_and_removes_them_by_hand(make_client):
    dm, pl, cid, chid = party(make_client)
    # como las guarda el editor: en la columna de su atributo, con su rango
    sheet = {"attributes": {"STR": 3, "SPD": 2, "INT": 1, "WIL": 2, "AWA": 2, "PRE": 1},
             "skills": {"physical": [{"name": "Abrasion", "rank": 2, "value": 4,
                                      "attr": "SPD", "surge": True}],
                        "cognitive": [], "spiritual": []}}
    r = pl.put(f"/api/characters/{chid}", json={
        "name": "Kal", "campaign_id": cid, "vida_max": 20, "focus_max": 4,
        "inv_max": 2, "sheet": sheet})
    assert r.status_code == 200, r.text
    ch = pl.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]
    assert ch["sheet"]["skills"]["physical"][0]["surge"] is True

    sheet["skills"]["physical"] = []
    pl.put(f"/api/characters/{chid}", json={
        "name": "Kal", "campaign_id": cid, "vida_max": 20, "focus_max": 4,
        "inv_max": 2, "sheet": sheet})
    ch = pl.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]
    assert ch["sheet"]["skills"]["physical"] == []
