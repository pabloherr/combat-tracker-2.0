"""
Extracción de la ficha rellenable de D&D 5e (edición 2024).

A diferencia de la ficha clásica, esta trae los campos con nombres generados
(`form_0_0`, `form_1_0`…), así que no se puede mapear por nombre. Lo que sí es
estable es **dónde está cada casilla**: el mapeo se hace por geometría, con la
disposición de la hoja oficial (dos columnas de características, las
habilidades en orden alfabético dentro de cada una, y la fila de armas).

Devuelve el mismo shape que `dnd_pdf.parse_dnd_pdf`, para que el resto de la
app no tenga que saber de qué ficha vino.
"""

import io

import pypdf

# Tamaño de la hoja oficial. Si llega escalada, se normaliza a este marco.
PAGE_W, PAGE_H = 603.0, 774.0

# Habilidades en el orden en que caen en la hoja, por columna. Cada bloque
# arranca con su salvación y sigue con sus habilidades en orden alfabético.
COL_IZQ = [("save", "STR"), ("skill", "Athletics"),
           ("save", "DEX"), ("skill", "Acrobatics"),
           ("skill", "Sleight of Hand"), ("skill", "Stealth"),
           ("save", "CON")]
COL_DER = [("save", "INT"), ("skill", "Arcana"), ("skill", "History"),
           ("skill", "Investigation"), ("skill", "Nature"), ("skill", "Religion"),
           ("save", "WIS"), ("skill", "Animal Handling"), ("skill", "Insight"),
           ("skill", "Medicine"), ("skill", "Perception"), ("skill", "Survival"),
           ("save", "CHA"), ("skill", "Deception"), ("skill", "Intimidation"),
           ("skill", "Performance"), ("skill", "Persuasion")]

# Características: la columna izquierda lleva STR/DEX/CON y la derecha INT/WIS/CHA,
# cada una de arriba hacia abajo.
AB_IZQ = ["STR", "DEX", "CON"]
AB_DER = ["INT", "WIS", "CHA"]


def _s(v) -> str:
    return "" if v in (None, "") else str(v).strip()


def _i(v, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


class _W:
    """Una casilla del formulario, ya normalizada al marco de la hoja."""

    __slots__ = ("name", "page", "x", "y", "w", "h", "btn", "value", "on")

    def __init__(self, name, page, x, y, w, h, btn, value, on):
        self.name, self.page = name, page
        self.x, self.y, self.w, self.h = x, y, w, h
        self.btn, self.value, self.on = btn, value, on

    @property
    def cx(self):
        return self.x + self.w / 2


def _widgets(reader) -> list:
    out = []
    for pi, page in enumerate(reader.pages):
        mb = page.mediabox
        sx = PAGE_W / float(mb.width) if float(mb.width) else 1.0
        sy = PAGE_H / float(mb.height) if float(mb.height) else 1.0
        for a in page.get("/Annots") or []:
            try:
                a = a.get_object()
            except Exception:
                continue
            if a.get("/Subtype") != "/Widget":
                continue
            parent = a.get("/Parent")
            parent = parent.get_object() if parent else None
            ft = a.get("/FT") or (parent.get("/FT") if parent else None)
            name = a.get("/TM") or a.get("/T") or (parent.get("/T") if parent else None)
            rect = a.get("/Rect")
            if not rect:
                continue
            r = [float(v) for v in rect]
            v = a.get("/V")
            if v is None and parent is not None:
                v = parent.get("/V")
            on = str(v) not in ("", "None", "/Off")
            out.append(_W(str(name), pi,
                          min(r[0], r[2]) * sx, min(r[1], r[3]) * sy,
                          abs(r[2] - r[0]) * sx, abs(r[3] - r[1]) * sy,
                          str(ft) == "/Btn", _s(v), on))
    return out


def _pick(ws, *, page=0, btn=False, x=None, y=None, w=None, h=None, tol=4.0):
    """Las casillas que caen donde se espera, de arriba hacia abajo."""
    out = []
    for k in ws:
        if k.page != page or k.btn != btn:
            continue
        if x is not None and abs(k.x - x) > tol:
            continue
        if y is not None and abs(k.y - y) > tol:
            continue
        if w is not None and abs(k.w - w) > tol:
            continue
        if h is not None and abs(k.h - h) > tol:
            continue
        out.append(k)
    out.sort(key=lambda k: (-k.y, k.x))
    return out


def _one(ws, **kw) -> str:
    got = _pick(ws, **kw)
    return got[0].value if got else ""


def es_ficha_2024(ws) -> bool:
    """La reconoce por su forma: el nombre arriba de todo, las 24 casillas de
    salvación y habilidad, y las seis de característica."""
    if not _pick(ws, y=744.2, w=225.0):
        return False
    if len(_pick(ws, w=17.2, h=10.8, tol=1.5)) < 24:
        return False
    return len(_pick(ws, w=28.2, h=20.0, tol=1.5)) >= 6


def _columnas(cajas):
    """Parte en columna izquierda y derecha por la x, cada una de arriba abajo."""
    izq = sorted([k for k in cajas if k.x < 100], key=lambda k: -k.y)
    der = sorted([k for k in cajas if k.x >= 100], key=lambda k: -k.y)
    return izq, der


def _competencia(ws, caja):
    """El tilde de competencia de esa fila: el checkbox a su izquierda."""
    for b in ws:
        if b.page != caja.page or not b.btn:
            continue
        if abs(b.y - caja.y) < 3 and 0 < caja.x - b.x < 20:
            return b.on
    return False


def parse_dnd2024_pdf(data: bytes) -> dict:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"No se pudo leer el PDF: {e}")
    ws = _widgets(reader)
    if not es_ficha_2024(ws):
        raise ValueError("El PDF no parece la ficha de D&D 5e (2024).")

    # ── Cabecera ──────────────────────────────────────────
    name = _one(ws, y=744.2, w=225.0) or "Personaje"
    background = _one(ws, y=722.8, x=25.2, w=119.2)
    clase = _one(ws, y=722.8, x=148.8, w=96.0)
    species = _one(ws, y=701.0, x=24.8, w=119.2)
    subclass = _one(ws, y=701.0, x=148.2, w=99.5)
    level = _one(ws, y=727.9, x=257.2)
    xp = _one(ws, y=706.5, x=257.0)

    ac = _one(ws, y=716.2, x=317.5)
    hp_cur_s = _one(ws, x=378.8, h=41.0)
    hp_max_s = _one(ws, y=702.8, x=437.5)
    hp_temp = _one(ws, y=724.5, x=437.8)
    hd_max = _one(ws, y=702.8, x=490.8)
    hd_spent = _one(ws, y=724.4, x=490.8)
    prof = _one(ws, y=607.8, x=46.5)

    # fila de arriba: iniciativa, tamaño, velocidad y percepción pasiva
    fila = _pick(ws, y=624.7)
    fila.sort(key=lambda k: k.x)
    top = [k.value for k in fila] + ["", "", "", ""]
    initiative, size, speed, passive = top[0], top[1], top[2], top[3]

    # ── Características ───────────────────────────────────
    mods_b = _pick(ws, w=28.2, h=20.0, tol=1.5)
    scores_b = _pick(ws, w=21.0, h=17.8, tol=1.5)
    abilities, mods = {}, {}
    mi, md = _columnas(mods_b)
    si, sd = _columnas(scores_b)
    for lista, claves in ((mi, AB_IZQ), (md, AB_DER)):
        for k, clave in zip(lista, claves):
            if k.value:
                mods[clave] = k.value
    for lista, claves in ((si, AB_IZQ), (sd, AB_DER)):
        for k, clave in zip(lista, claves):
            abilities[clave] = _i(k.value, None) if k.value else None

    # ── Salvaciones y habilidades ─────────────────────────
    filas = _pick(ws, w=17.2, h=10.8, tol=1.5)
    izq, der = _columnas(filas)
    saves, skills, save_prof, skill_prof = {}, [], {}, {}
    for lista, orden in ((izq, COL_IZQ), (der, COL_DER)):
        for k, (tipo, clave) in zip(lista, orden):
            marcado = _competencia(ws, k)
            if tipo == "save":
                if k.value:
                    saves[clave] = k.value
                if marcado:
                    save_prof[clave] = True
            else:
                if k.value:
                    skills.append({"name": clave, "value": k.value})
                if marcado:
                    skill_prof[clave] = 1

    # ── Armas ─────────────────────────────────────────────
    def _col(x, w):
        return {round(k.y): k.value for k in _pick(ws, x=x, w=w, h=13.5, tol=2.0)}

    nombres = _pick(ws, x=228.2, w=104.0, h=13.5, tol=2.0)
    atk, dmg, notas = _col(336.6, 43.0), _col(384.1, 73.5), _col(462.1, 123.2)

    def _cerca(d, y):
        for yy, v in d.items():
            if abs(yy - y) < 3:
                return v
        return ""

    attacks = []
    for k in nombres:
        if not k.value:
            continue
        attacks.append({"name": k.value, "bonus": _cerca(atk, k.y),
                        "damage": _cerca(dmg, k.y), "notes": _cerca(notas, k.y)})

    # ── Bloques largos ────────────────────────────────────
    rasgos = [k.value for k in _pick(ws, y=219.0, h=205.2, tol=3.0) if k.value]
    abajo = [k.value for k in _pick(ws, y=14.5, h=169.0, tol=4.0) if k.value]
    entrenamiento = [k.value for k in _pick(ws, x=16.2, w=191.2) if k.value]
    armaduras = [b for b in _pick(ws, btn=True, y=121.6, tol=2.0)]
    etiquetas = ["Ligera", "Media", "Pesada", "Escudos"]
    armor_training = [lbl for b, lbl in zip(sorted(armaduras, key=lambda k: k.x), etiquetas)
                      if b.on]

    # ── Página 2: conjuros, equipo y monedas ──────────────
    sc = _pick(ws, page=1, w=24.8, h=19.2, tol=2.0)
    sc_vals = [k.value for k in sc] + ["", "", ""]
    sc_ability = _one(ws, page=1, y=740.8, w=108.8, tol=3.0)
    slots_b = _pick(ws, page=1, w=17.2, h=9.5, tol=1.5)
    slots = {}
    # los nueve niveles van en tres columnas de tres: 1-2-3, 4-5-6, 7-8-9
    orden_slots = sorted(slots_b, key=lambda k: (round(k.x / 50), -k.y))
    for n, k in enumerate(orden_slots, start=1):
        total = _i(k.value, 0)
        if total > 0:
            total = min(12, total)
            slots[str(n)] = {"max": total, "cur": total}

    spells = {}
    nombres_c = _pick(ws, page=1, w=108.0, h=12.8, tol=1.5)
    niveles_c = {round(k.y): k.value for k in _pick(ws, page=1, w=21.8, h=12.8, tol=1.5)}
    for k in nombres_c:
        if not k.value:
            continue
        lvl = _s(_cerca(niveles_c, k.y)) or "0"
        lvl = "".join(ch for ch in lvl if ch.isdigit()) or "0"
        spells.setdefault(lvl, []).append(k.value)
    for lvl in spells:
        spells[lvl] = sorted(spells[lvl], key=str.lower)

    attunement = chr(10).join(k.value for k in _pick(ws, page=1, w=155.8, h=13.0, tol=2.0) if k.value)
    grandes = _pick(ws, page=1, w=177.8, tol=3.0)
    appearance = backstory = languages = equipment = ""
    for k in grandes:
        if abs(k.h - 173.8) < 3:
            appearance = k.value
        elif abs(k.h - 135.0) < 3:
            backstory = k.value
        elif abs(k.h - 71.0) < 3:
            equipment = k.value
        elif abs(k.h - 34.0) < 3:
            languages = k.value
    monedas = _pick(ws, page=1, w=26.8, h=14.0, tol=1.5)
    monedas.sort(key=lambda k: k.x)
    coins = {}
    for lbl, k in zip(["cp", "sp", "ep", "gp", "pp"], monedas):
        if k.value:
            coins[lbl] = _i(k.value, 0)

    hp_max = max(1, _i(hp_max_s, 10))
    hp_cur = _i(hp_cur_s, hp_max) if hp_cur_s else hp_max
    hp_cur = max(0, min(hp_max, hp_cur))

    sheet = {
        "dnd": True,
        "sheet_2024": True,
        "paths": clase,
        "level": level,
        "race": species,
        "subclass": subclass,
        "background": background,
        "xp": xp,
        "size": size,
        "abilities": abilities,
        "mods": mods,
        "saves": saves,
        "save_prof": save_prof,
        "skills": skills,
        "skill_prof": skill_prof,
        "ac": ac,
        "initiative": initiative,
        "speed": speed,
        "prof": prof,
        "passive": passive,
        "hd": hd_max,
        "hd_spent": hd_spent,
        "hp_temp": hp_temp,
        "attacks": attacks,
        "features": "\n\n".join(rasgos),
        "features2": "\n\n".join(abajo),
        "prof_lang": "\n".join(entrenamiento),
        "armor_training": armor_training,
        "equipment": equipment,
        "languages": languages,
        "appearance": appearance,
        "backstory": backstory,
        "attunement": attunement,
        "coins": coins,
        "spellcasting": {
            "ability": sc_ability,
            "mod": sc_vals[0],
            "dc": sc_vals[1],
            "atk": sc_vals[2],
        },
        "spells": spells,
    }
    return {"name": name, "vida_max": hp_max, "vida": hp_cur,
            "sheet": sheet, "slots": slots}
