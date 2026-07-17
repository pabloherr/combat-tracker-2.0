"""
Extracción de una ficha de personaje de Cosmere RPG desde el PDF rellenable.

El PDF oficial es un formulario AcroForm con campos `char_*`. Leemos esos
campos con pypdf y devolvemos nombre + medidores (vida/focus/investidura)
para el tracker, más una ficha completa (`sheet`) para poder visualizarla.
"""

import io

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

    # Habilidades: por categoría, solo las que tienen valor.
    skills = {}
    for cat, items in _SKILLS.items():
        rows = []
        for field, label in items:
            val = g(f"char_{field}")
            if val not in (None, ""):
                rows.append({"name": label, "value": _i(val)})
        # habilidad custom de la categoría
        cat_key = {"physical": "phys", "cognitive": "cog", "spiritual": "spirit"}[cat]
        cname = _s(g(f"char_{cat_key}_custom_name"))
        cval = g(f"char_{cat_key}_custom")
        if cname and cval not in (None, ""):
            rows.append({"name": cname, "value": _i(cval)})
        skills[cat] = rows

    # Talentos: char_talent_name_N / char_talent_desc_N
    talents = []
    for n in range(1, 16):
        tn = _s(g(f"char_talent_name_{n}"))
        td = _s(g(f"char_talent_desc_{n}"))
        if tn or td:
            talents.append({"name": tn, "desc": td})

    vida_max = _i(g("char_health_max"), 20)
    focus_max = _i(g("char_focus_max"), 0)
    inv_max = _i(g("char_invest_max"), 0)

    sheet = {
        "level": _s(g("char_level")),
        "ancestry": _s(g("char_ancestry")),
        "paths": _s(g("char_paths")),
        "attributes": {
            "STR": _i(g("char_strength")), "SPD": _i(g("char_speed")),
            "INT": _i(g("char_intellect")), "WIL": _i(g("char_willpower")),
            "AWA": _i(g("char_awareness")), "PRE": _i(g("char_presence")),
        },
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
