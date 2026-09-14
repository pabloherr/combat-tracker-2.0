"""
Extracción de una ficha de personaje de Cosmere RPG desde el PDF rellenable.

El PDF oficial es un formulario AcroForm con campos `char_*`. Leemos esos
campos con pypdf y devolvemos nombre + medidores (vida/focus/investidura)
para el tracker, más una ficha completa (`sheet`) para poder visualizarla.
"""

import io
import re
import unicodedata

import pypdf


def _s(v) -> str:
    return "" if v in (None, "") else str(v).strip()


def _i(v, default: int = 0) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


# Habilidades del sistema (campo → etiqueta legible), por categoría.
_SKILLS = {
    "physical": [("athletics", "Athletics"), ("agility", "Agility"),
                 ("heavy_weapon", "Heavy Weaponry"), ("light_weapon", "Light Weaponry"),
                 ("stealth", "Stealth"), ("thievery", "Thievery")],
    "cognitive": [("crafting", "Crafting"), ("deduction", "Deduction"),
                  ("discipline", "Discipline"), ("intimidation", "Intimidation"),
                  ("lore", "Lore"), ("medicine", "Medicine")],
    "spiritual": [("deception", "Deception"), ("insight", "Insight"),
                  ("leadership", "Leadership"), ("perception", "Perception"),
                  ("persuasion", "Persuasion"), ("survival", "Survival")],
}


# ── Potencias radiantes (Surges) ──
# Cada una se usa como una habilidad más, con su atributo fijo, y por eso vive
# en la columna de ese atributo. La ficha oficial no tiene casillas para ellas:
# el jugador las escribe en la fila libre de la columna (o, si no le entran, en
# los talentos), así que se buscan en los dos lugares.
SURGES = {
    "Abrasion": "SPD", "Adhesion": "PRE", "Cohesion": "WIL", "Division": "INT",
    "Gravitation": "AWA", "Illumination": "PRE", "Progression": "AWA",
    "Tension": "STR", "Transformation": "WIL", "Transportation": "INT",
}

# Como se escriben en la mesa: en inglés (el manual) o en castellano.
_SURGE_ALIAS = {
    "abrasion": "Abrasion", "adhesion": "Adhesion", "cohesion": "Cohesion",
    "division": "Division", "gravitation": "Gravitation", "gravitacion": "Gravitation",
    "illumination": "Illumination", "iluminacion": "Illumination",
    "progression": "Progression", "progresion": "Progression", "tension": "Tension",
    "transformation": "Transformation", "transformacion": "Transformation",
    "transportation": "Transportation", "transportacion": "Transportation",
}

CAT_DE_ATTR = {"STR": "physical", "SPD": "physical",
               "INT": "cognitive", "WIL": "cognitive",
               "AWA": "spiritual", "PRE": "spiritual"}

_ATTR_ALIAS = {
    "str": "STR", "strength": "STR", "fuerza": "STR",
    "spd": "SPD", "speed": "SPD", "velocidad": "SPD",
    "int": "INT", "intellect": "INT", "intelecto": "INT",
    "wil": "WIL", "will": "WIL", "willpower": "WIL", "voluntad": "WIL",
    "awa": "AWA", "awareness": "AWA", "conciencia": "AWA",
    "pre": "PRE", "presence": "PRE", "presencia": "PRE",
}


def _norm(txt: str) -> str:
    """Minúsculas, sin tildes y sin puntuación: para comparar nombres escritos a mano."""
    t = unicodedata.normalize("NFKD", str(txt or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-zA-Z0-9\s]", " ", t)).strip().lower()


def surge_en(txt: str, exacto: bool = False) -> str | None:
    """La potencia que nombra este texto, o None.

    `exacto` pide que el texto sea solo el nombre (para no confundir un talento
    como "Division of Spoils" con la potencia Division). Si no, alcanza con que
    la nombre: "Surge of Gravitation", "Potencia de Gravitación"."""
    t = _norm(txt)
    if not t:
        return None
    if t in _SURGE_ALIAS:
        return _SURGE_ALIAS[t]
    if exacto:
        return None
    for palabra in t.split(" "):
        if palabra in _SURGE_ALIAS:
            return _SURGE_ALIAS[palabra]
    return None


def _attr_code(txt: str) -> str:
    return _ATTR_ALIAS.get(_norm(txt).replace(" ", ""), "")


# Atributo de cada habilidad (el modificador es atributo + rangos).
_SK_ATTR = {
    "Athletics": "STR", "Heavy Weaponry": "STR",
    "Agility": "SPD", "Light Weaponry": "SPD", "Stealth": "SPD", "Thievery": "SPD",
    "Crafting": "INT", "Deduction": "INT", "Lore": "INT", "Medicine": "INT",
    "Discipline": "WIL", "Intimidation": "WIL",
    "Deception": "PRE", "Leadership": "PRE", "Persuasion": "PRE",
    "Insight": "AWA", "Perception": "AWA", "Survival": "AWA",
}


def parse_character_pdf(data: bytes) -> dict:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        fields = reader.get_fields() or {}
    except Exception as e:
        raise ValueError(f"No se pudo leer el PDF: {e}")

    def g(name):
        f = fields.get(name)
        return f.get("/V") if f is not None else None

    # Se identifica por la presencia de los campos (no por su valor): una ficha
    # en blanco o con esos campos vacíos igual es una ficha de Cosmere válida.
    if "char_health_max" not in fields and "char_name" not in fields:
        raise ValueError("El PDF no parece una ficha de Cosmere RPG (no encontré los campos).")

    def collect(prefix):
        out, i = [], 0
        while f"{prefix}.{i}" in fields:
            v = _s(g(f"{prefix}.{i}"))
            if v:
                out.append(v)
            i += 1
        return out

    attrs = {
        "STR": _i(g("char_strength")), "SPD": _i(g("char_speed")),
        "INT": _i(g("char_intellect")), "WIL": _i(g("char_willpower")),
        "AWA": _i(g("char_awareness")), "PRE": _i(g("char_presence")),
    }

    def rango(field):
        """Los rangos son los cinco cuadraditos de la fila: cuenta los marcados."""
        return sum(1 for i in range(1, 6)
                   if _s(g(f"char_{field}_rank_{i}")) not in ("", "/Off", "Off"))

    def fila(label, field, attr):
        """Una habilidad de la ficha, o None si el jugador no anotó nada.

        Con el modificador escrito se respeta tal cual (puede llevar sumas que
        la app no conoce). Si solo marcó los rangos, el modificador se calcula:
        atributo + rangos."""
        val = g(f"char_{field}")
        if val not in (None, ""):
            return {"name": label, "value": _i(val)}
        r = rango(field)
        if not r:
            return None
        return {"name": label, "rank": r, "value": attrs.get(attr, 0) + r}

    # Habilidades: por categoría, solo las que el jugador anotó.
    skills = {cat: [] for cat in _SKILLS}
    for cat, items in _SKILLS.items():
        for field, label in items:
            f = fila(label, field, _SK_ATTR.get(label, ""))
            if f:
                skills[cat].append(f)

    # La fila libre de cada columna: ahí van las potencias radiantes y cualquier
    # habilidad que la mesa se haya inventado. Una potencia se guarda en la
    # columna de su atributo, aunque esté escrita en otra.
    for cat in _SKILLS:
        cat_key = {"physical": "phys", "cognitive": "cog", "spiritual": "spirit"}[cat]
        cname = _s(g(f"char_{cat_key}_custom_name"))
        if not cname:
            continue
        f = fila(cname, f"{cat_key}_custom", _attr_code(g(f"char_{cat_key}_custom_attr")))
        if not f:
            # nombre escrito y nada más: igual vale, con rango 0
            f = {"name": cname, "rank": 0, "value": 0}
        surge = surge_en(cname)
        if surge:
            at = SURGES[surge]
            f.update({"name": surge, "surge": True, "attr": at})
            if "rank" in f:
                f["value"] = attrs.get(at, 0) + f["rank"]
            skills[CAT_DE_ATTR[at]].append(f)
        else:
            at = _attr_code(g(f"char_{cat_key}_custom_attr"))
            if at:
                f["attr"] = at
            skills[cat].append(f)

    # Talentos: char_talent_name_N / char_talent_desc_N
    talents = []
    for n in range(1, 16):
        tn = _s(g(f"char_talent_name_{n}"))
        td = _s(g(f"char_talent_desc_{n}"))
        if tn or td:
            talents.append({"name": tn, "desc": td})

    # Un radiante suele tener dos potencias de la misma columna y en la ficha
    # hay una sola fila libre por columna: la segunda termina anotada entre los
    # talentos. Se busca ahí también, pidiendo que el talento se llame igual que
    # la potencia (para no confundirla con un talento que solo la menciona).
    puestas = {x["name"] for rows in skills.values() for x in rows}
    for t in talents:
        surge = surge_en(t["name"], exacto=True)
        if surge and surge not in puestas:
            at = SURGES[surge]
            skills[CAT_DE_ATTR[at]].append({"name": surge, "surge": True, "attr": at,
                                            "rank": 0, "value": attrs.get(at, 0)})
            puestas.add(surge)

    vida_max = _i(g("char_health_max"), 20)
    focus_max = _i(g("char_focus_max"), 0)
    inv_max = _i(g("char_invest_max"), 0)

    sheet = {
        "level": _s(g("char_level")),
        "ancestry": _s(g("char_ancestry")),
        "paths": _s(g("char_paths")),
        "attributes": attrs,
        "defenses": {
            "physical": _i(g("char_phys_def")),
            "cognitive": _i(g("char_cog_def")),
            "spiritual": _i(g("char_spirit_def")),
        },
        "deflect": _s(g("char_deflect")),
        "movement": _s(g("char_movement")),
        "senses": _s(g("char_senses")),
        "recovery": _s(g("char_recovery")),
        "expertise": _s(g("char_expertise")),
        "skills": skills,
        "talents": talents,
        "weapons": collect("char_weapons"),
        "equipment": collect("char_equipment"),
        "connections": collect("char_connections"),
    }

    return {
        "name": _s(g("char_name")) or "Personaje",
        # Esferas anotadas en la ficha: solo se usan al crear el personaje, como
        # semilla de sus marcos (después el jugador los gestiona en la app).
        "spheres": _i(g("char_spheres"), 0),
        "vida_max": vida_max,
        "vida": _i(g("char_health_current"), vida_max),
        "focus_max": focus_max,
        "focus": _i(g("char_focus_current"), focus_max),
        "inv_max": inv_max,
        "inv": _i(g("char_invest_current"), inv_max),
        "sheet": sheet,
    }


_IMG_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp", "tiff": "image/tiff",
}


def extract_pdf_image(data: bytes) -> tuple[bytes, str] | None:
    """Intenta sacar el retrato del PDF: la imagen raster más grande.

    Best-effort y sin dependencia dura de Pillow: si algo falla (Pillow no
    instalado, formato que pypdf no puede decodificar, PDF sin imágenes, etc.)
    devuelve None y el jugador sube la imagen a mano. NUNCA propaga excepciones:
    la subida del PDF no debe romperse por no poder extraer un retrato.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
    except Exception:
        return None
    best = None          # (len, bytes, mime)
    for page in reader.pages:
        # La iteración de page.images decodifica cada imagen y puede lanzar
        # (p.ej. ImportError si falta Pillow); se envuelve todo el recorrido.
        try:
            for img in page.images:
                raw = img.data
                if not raw or len(raw) < 6000:   # descarta íconos/logos chicos
                    continue
                ext = (getattr(img, "name", "") or "").rsplit(".", 1)[-1].lower()
                mime = _IMG_MIME.get(ext, "image/png")
                if best is None or len(raw) > best[0]:
                    best = (len(raw), raw, mime)
        except Exception:
            continue
    if best is None:
        return None
    return best[1], best[2]
