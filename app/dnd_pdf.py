"""
Extracción de una ficha de personaje de D&D 5e desde el PDF rellenable oficial
(5E_CharacterSheet_Fillable). Es un AcroForm: leemos los campos con pypdf y
devolvemos nombre + vida para el tracker, la ficha completa (`sheet`) para
visualizarla, y los spell slots por nivel para precargar los contadores.
"""

import io

import pypdf


def _s(v) -> str:
    return "" if v in (None, "") else str(v).strip()


def _i(v, default: int = 0) -> int:
    try:
        return int(float(str(v).strip().replace(",", ".")))
    except (TypeError, ValueError):
        return default


# Campos de habilidades (nombre del campo en el PDF → etiqueta).
_SKILLS = [
    ("Acrobatics", "Acrobatics"), ("Animal", "Animal Handling"), ("Arcana", "Arcana"),
    ("Athletics", "Athletics"), ("Deception", "Deception"), ("History", "History"),
    ("Insight", "Insight"), ("Intimidation", "Intimidation"),
    ("Investigation", "Investigation"), ("Medicine", "Medicine"), ("Nature", "Nature"),
    ("Perception", "Perception"), ("Performance", "Performance"),
    ("Persuasion", "Persuasion"), ("Religion", "Religion"),
    ("SleightofHand", "Sleight of Hand"), ("Stealth", "Stealth"),
    ("Survival", "Survival"),
]

_ABILITIES = [("STR", "STR"), ("DEX", "DEX"), ("CON", "CON"),
              ("INT", "INT"), ("WIS", "WIS"), ("CHA", "CHA")]
_MODS = {"STR": "STRmod", "DEX": "DEXmod", "CON": "CONmod",
         "INT": "INTmod", "WIS": "WISmod", "CHA": "CHamod"}
_SAVES = {"STR": "ST Strength", "DEX": "ST Dexterity", "CON": "ST Constitution",
          "INT": "ST Intelligence", "WIS": "ST Wisdom", "CHA": "ST Charisma"}


def _spell_levels(reader) -> dict:
    """Agrupa los campos 'Spells NNNN' de la página 3 por nivel, por posición.

    Cada bloque de nivel arranca en su campo 'SlotsTotal N' (19→nivel 1 ...
    27→nivel 9); los conjuros por encima del primer bloque de la columna 1
    son los trucos (nivel 0)."""
    try:
        page = reader.pages[2]
        annots = page.get("/Annots") or []
    except Exception:
        return {}
    spells, headers = [], []
    for a in annots:
        o = a.get_object()
        nm, rect = o.get("/T"), o.get("/Rect")
        if not nm or not rect:
            continue
        nm = str(nm)
        x, y = float(rect[0]), float(rect[3])
        col = 0 if x < 150 else (1 if x < 350 else 2)
        if nm.startswith("Spells "):
            spells.append((nm, col, y))
        elif nm.startswith("SlotsTotal "):
            headers.append((_i(nm.split()[-1]) - 18, col, y))
    out = {}
    for nm, col, y in spells:
        # nivel = bloque cuyo encabezado queda justo arriba (misma columna)
        above = [h for h in headers if h[1] == col and h[2] >= y - 2]
        lvl = min(above, key=lambda h: h[2])[0] if above else 0
        out.setdefault(lvl, []).append(nm)
    return out


def parse_dnd_pdf(data: bytes) -> dict:
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        fields = reader.get_fields() or {}
    except Exception as e:
        raise ValueError(f"No se pudo leer el PDF: {e}")

    # Los nombres de campo del PDF oficial traen espacios colgantes ('Race ',
    # 'DEXmod ', 'Wpn3 AtkBonus  '): indexamos por nombre sin espacios extra.
    vals = {}
    for k, f in fields.items():
        v = f.get("/V")
        if v not in (None, ""):
            vals[" ".join(str(k).split())] = str(v).strip()

    def g(name):
        return vals.get(name, "")

    if not g("CharacterName") and not g("HPMax"):
        raise ValueError("El PDF no parece la ficha rellenable de D&D 5e (no encontré los campos).")

    name = g("CharacterName") or "Personaje"
    hp_max = max(1, _i(g("HPMax"), 10))
    hp_cur = _i(g("HPCurrent"), hp_max)
    hp_cur = max(0, min(hp_max, hp_cur if g("HPCurrent") else hp_max))

    class_level = g("ClassLevel")
    # nivel: último número del texto de clase ("Mago 5" → 5)
    level = ""
    for tok in reversed(class_level.replace("/", " ").split()):
        if tok.isdigit():
            level = tok
            break

    abilities = {k: _i(g(f), None) for k, f in _ABILITIES}
    mods = {k: g(f) for k, f in _MODS.items() if g(f)}
    saves = {k: g(f) for k, f in _SAVES.items() if g(f)}
    skills = [{"name": lbl, "value": g(f)} for f, lbl in _SKILLS if g(f)]

    attacks = []
    for pre in (("Wpn Name", "Wpn1 AtkBonus", "Wpn1 Damage"),
                ("Wpn Name 2", "Wpn2 AtkBonus", "Wpn2 Damage"),
                ("Wpn Name 3", "Wpn3 AtkBonus", "Wpn3 Damage")):
        if g(pre[0]):
            attacks.append({"name": g(pre[0]), "bonus": g(pre[1]), "damage": g(pre[2])})

    # Conjuros por nivel + slots (SlotsTotal 19..27 = niveles 1..9)
    groups = _spell_levels(reader)
    spells = {}
    for lvl, names in groups.items():
        lst = [vals[n] for n in (" ".join(x.split()) for x in names) if vals.get(n)]
        if lst:
            spells[str(lvl)] = sorted(lst, key=str.lower)
    slots = {}
    for n in range(1, 10):
        total = _i(g(f"SlotsTotal {n + 18}"), 0)
        if total > 0:
            remaining = g(f"SlotsRemaining {n + 18}")
            cur = _i(remaining, total) if remaining else total
            slots[str(n)] = {"max": min(12, total), "cur": max(0, min(min(12, total), cur))}

    sheet = {
        "dnd": True,
        "paths": class_level,          # mismo campo que usa la UI para "clase"
        "level": level,
        "race": g("Race"),
        "background": g("Background"),
        "alignment": g("Alignment"),
        "abilities": abilities,
        "mods": mods,
        "saves": saves,
        "skills": skills,
        "ac": g("AC"),
        "initiative": g("Initiative"),
        "speed": g("Speed"),
        "prof": g("ProfBonus"),
        "passive": g("Passive"),
        "hd": g("HDTotal") or g("HD"),
        "attacks": attacks,
        "attacks_text": g("AttacksSpellcasting"),
        "prof_lang": g("ProficienciesLang"),
        "equipment": g("Equipment"),
        "features": g("Features and Traits"),
        "features2": g("Feat+Traits"),
        "spellcasting": {
            "class": g("Spellcasting Class 2"),
            "ability": g("SpellcastingAbility 2"),
            "dc": g("SpellSaveDC 2"),
            "atk": g("SpellAtkBonus 2"),
        },
        "spells": spells,
    }
    return {
        "name": name,
        "vida_max": hp_max,
        "vida": hp_cur,
        "sheet": sheet,
        "slots": slots,
    }
