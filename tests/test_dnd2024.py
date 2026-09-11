"""La ficha rellenable de D&D 5e (2024): se mapea por posición, porque trae los
campos con nombres generados."""

import io

import pypdf
from helpers import create_campaign, invite, make_user
from pypdf.generic import BooleanObject, NameObject, TextStringObject

from app.dnd2024_pdf import _columnas, _pick, _widgets, parse_dnd2024_pdf


def _rellenar(pdf: bytes, textos: dict, marcar=()) -> bytes:
    """Escribe por posición, igual que lee el parser."""
    r = pypdf.PdfReader(io.BytesIO(pdf))
    ws = _widgets(r)
    plan, cruces = {}, set()

    def poner(caja, valor):
        plan[caja.name] = valor

    izq, der = _columnas(_pick(ws, w=17.2, h=10.8, tol=1.5))
    mi, md = _columnas(_pick(ws, w=28.2, h=20.0, tol=1.5))
    si, sd = _columnas(_pick(ws, w=21.0, h=17.8, tol=1.5))
    sitio = {
        "nombre": _pick(ws, y=744.2, w=225.0)[0],
        "clase": _pick(ws, y=722.8, x=148.8, w=96.0)[0],
        "especie": _pick(ws, y=701.0, x=24.8, w=119.2)[0],
        "nivel": _pick(ws, y=727.9, x=257.2)[0],
        "ca": _pick(ws, y=716.2, x=317.5)[0],
        "pg": _pick(ws, x=378.8, h=41.0)[0],
        "pg_max": _pick(ws, y=702.8, x=437.5)[0],
        "arma": _pick(ws, x=228.2, w=104.0, h=13.5, tol=2.0)[0],
        "arma_dmg": _pick(ws, x=384.1, w=73.5, h=13.5, tol=2.0)[0],
    }
    for k, v in textos.items():
        if k in sitio:
            poner(sitio[k], v)
    for caja, v in zip(si, textos.get("_izq", [])):
        poner(caja, v)
    for caja, v in zip(sd, textos.get("_der", [])):
        poner(caja, v)
    for i in marcar:
        fila = (izq + der)[i]
        for b in ws:
            if b.btn and abs(b.y - fila.y) < 3 and 0 < fila.x - b.x < 20:
                cruces.add(b.name)

    w = pypdf.PdfWriter(clone_from=io.BytesIO(pdf))
    for page in w.pages:
        for a in page.get("/Annots") or []:
            a = a.get_object()
            tm = str(a.get("/TM") or a.get("/T"))
            if tm in plan and a.get("/FT") == "/Tx":
                a[NameObject("/V")] = TextStringObject(plan[tm])
                if "/AP" in a:
                    del a["/AP"]
            if tm in cruces and a.get("/FT") == "/Btn":
                st = a.get("/AP", {}).get("/N", {})
                on = [k for k in st.keys() if k != "/Off"]
                if on:
                    a[NameObject("/V")] = NameObject(on[0])
                    a[NameObject("/AS")] = NameObject(on[0])
    w._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_the_blank_2024_sheet_is_recognised(dnd2024_pdf):
    p = parse_dnd2024_pdf(dnd2024_pdf)
    assert p["name"] == "Personaje"
    assert p["sheet"]["dnd"] is True and p["sheet"]["sheet_2024"] is True
    # 18 habilidades y 6 salvaciones, aunque estén vacías
    assert p["sheet"]["skills"] == [] and p["sheet"]["saves"] == {}


def test_it_reads_what_the_sheet_has(dnd2024_pdf):
    pdf = _rellenar(dnd2024_pdf, {
        "nombre": "Alden Rook", "clase": "Pícaro", "especie": "Mediano",
        "nivel": "5", "ca": "16", "pg": "22", "pg_max": "38",
        "arma": "Estoque", "arma_dmg": "1d8+4",
        # puntuaciones: izquierda STR/DEX/CON, derecha INT/WIS/CHA
        "_izq": ["10", "18", "14"], "_der": ["12", "13", "8"],
    }, marcar=[2, 5])       # salvación de DEX y Stealth
    p = parse_dnd2024_pdf(pdf)
    s = p["sheet"]

    assert p["name"] == "Alden Rook"
    assert (p["vida"], p["vida_max"]) == (22, 38)
    assert s["paths"] == "Pícaro" and s["race"] == "Mediano" and s["level"] == "5"
    assert s["ac"] == "16"
    assert s["abilities"] == {"STR": 10, "DEX": 18, "CON": 14,
                              "INT": 12, "WIS": 13, "CHA": 8}
    assert s["save_prof"] == {"DEX": True}
    assert s["skill_prof"] == {"Stealth": 1}
    assert s["attacks"][0]["name"] == "Estoque"
    assert s["attacks"][0]["damage"] == "1d8+4"


def test_the_two_dnd_sheets_are_accepted_and_cosmere_is_not(make_client, dnd_pdf,
                                                            dnd2024_pdf, cosmere_pdf):
    dm = make_user(make_client, "dm", "dm")
    pl = make_user(make_client, "pl", "player")
    cid = create_campaign(dm, "DnD", "dnd")
    invite(dm, cid, "pl")

    r = pl.post(f"/api/characters/import-pdf?campaign_id={cid}",
                files={"file": ("f.pdf", dnd2024_pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    chid = r.json()["id"]
    ch = pl.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]
    assert ch["sheet"]["sheet_2024"] is True

    # y al actualizar valen las dos: la misma ficha se puede pisar con la clásica
    assert pl.post(f"/api/characters/{chid}/reimport-pdf",
                   files={"file": ("f.pdf", dnd_pdf, "application/pdf")}).status_code == 200
    ch = pl.get(f"/api/campaigns/{cid}/roster").json()["members"][0]["character"]
    assert not ch["sheet"].get("sheet_2024")
    assert pl.post(f"/api/characters/{chid}/reimport-pdf",
                   files={"file": ("f.pdf", dnd2024_pdf, "application/pdf")}).status_code == 200

    # una ficha de Cosmere no cuela
    r = pl.post(f"/api/characters/{chid}/reimport-pdf",
                files={"file": ("f.pdf", cosmere_pdf, "application/pdf")})
    assert r.status_code == 400 and "D&D" in r.json()["detail"]


def test_a_solo_dnd_character_can_come_from_the_2024_sheet(make_client, dnd2024_pdf):
    pl = make_user(make_client, "pl", "player")
    r = pl.post("/api/characters/import-pdf?system=dnd",
                files={"file": ("f.pdf", dnd2024_pdf, "application/pdf")})
    assert r.status_code == 200, r.text
    c = {x["id"]: x for x in pl.get("/api/characters").json()}[r.json()["id"]]
    assert c["campaign_id"] is None and c["system"] == "dnd"
    assert c["sheet"]["sheet_2024"] is True
