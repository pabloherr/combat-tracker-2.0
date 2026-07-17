"""
Parseo de statblocks de D&D 5e en formato YAML (espejo de cosmere_import).

Acepta un bloque `layout: D&D 5e` con los campos del manual de monstruos y
lo convierte en la estructura de enemigo que usa la app: campos base para el
combate + `stats` (con `dnd: true`) para renderizar la ficha completa.

Ejemplo de entrada:

    layout: D&D 5e
    name: "Lich"
    type: "Medium Undead, Any Evil Alignment"
    ac: "17 (natural armor)"
    hp: 135
    speed: "30 ft."
    cr: "21 (33,000 XP)"
    str: 11
    dex: 16
    ...
    actions:
      - name: "Paralyzing Touch"
        desc: "Melee Spell Attack: +12 to hit..."
"""

import re

import yaml

from .cosmere_import import ImportError_, _norm_entries, _strip_code_fence, _to_int

_ABILITIES = ["str", "dex", "con", "int", "wis", "cha"]


def parse_dnd_statblock(code: str) -> dict:
    """Parsea un statblock YAML de D&D. Lanza ImportError_ si no es válido."""
    text = _strip_code_fence(code)
    if not text.strip():
        raise ImportError_("El código está vacío.")
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ImportError_(f"No se pudo leer el YAML: {e}")
    if not isinstance(data, dict):
        raise ImportError_("El código no tiene el formato esperado (clave: valor).")
    return _build_from_data(data)


def parse_dnd_statblocks_bulk(code: str):
    """Varias fichas de una vez: bloques separados por `---`, `layout:` o `name:`.

    Devuelve (lista_de_enemigos, lista_de_errores)."""
    text = _strip_code_fence(code)
    if not text.strip():
        raise ImportError_("El texto está vacío.")

    if re.search(r"^---\s*$", text, re.M):
        raw_blocks = re.split(r"^---\s*$", text, flags=re.M)
    elif re.search(r"(?m)^layout\s*:", text):
        raw_blocks = re.split(r"(?m)^(?=layout\s*:)", text)
    else:
        raw_blocks = re.split(r"(?m)^(?=name\s*:)", text)

    enemies, errors = [], []
    idx = 0
    for block in raw_blocks:
        if not block.strip():
            continue
        idx += 1
        try:
            data = yaml.safe_load(block)
            if not isinstance(data, dict):
                raise ImportError_("no tiene formato clave: valor")
            enemies.append(_build_from_data(data))
        except (yaml.YAMLError, ImportError_) as e:
            errors.append(f"Bloque {idx}: {e}")
    return enemies, errors


def _build_from_data(data: dict) -> dict:
    name = str(data.get("name", "")).strip()
    if not name:
        raise ImportError_("Falta el campo 'name'.")

    tipo = str(data.get("type", data.get("tipo", ""))).strip()

    abilities = {}
    for k in _ABILITIES:
        if data.get(k) is not None:
            abilities[k.upper()] = _to_int(data.get(k), 10)

    traits = _norm_entries(data.get("traits"))
    actions = _norm_entries(data.get("actions"))
    legendary = _norm_entries(data.get("legendary", data.get("legendary_actions")))

    stats = {
        "dnd": True,
        "ac": str(data.get("ac", "")).strip(),
        "speed": str(data.get("speed", "")).strip(),
        "cr": str(data.get("cr", data.get("challenge", ""))).strip(),
        "abilities": abilities,
        "saves": str(data.get("saves", data.get("saving_throws", ""))).strip(),
        "skills_text": str(data.get("skills", "")).strip(),
        "vulnerabilities": str(data.get("vulnerabilities", "")).strip(),
        "resistances": str(data.get("resistances", "")).strip(),
        "immunities": str(data.get("immunities", "")).strip(),
        "cond_immunities": str(data.get("condition_immunities", "")).strip(),
        "senses": str(data.get("senses", "")).strip(),
        "languages": str(data.get("languages", "")).strip(),
        "traits": traits,
        "actions": actions,
        "legendary": legendary,
    }

    # Acciones en formato combate ("Ver acciones" de las tarjetas).
    acciones = [{"nombre": a["name"], "coste": "", "descripcion": a["desc"]} for a in actions]
    acciones += [{"nombre": a["name"], "coste": "Legendaria", "descripcion": a["desc"]}
                 for a in legendary]

    return {
        "name": name,
        "tipo": tipo,
        "clase": "rival",          # en D&D no hay minion/rival/boss
        "vida_max": max(1, _to_int(data.get("hp"), 10)),
        "focus_max": 0,
        "inv_max": 0,
        "acciones": acciones,
        "notas": str(data.get("notes", data.get("notas", ""))).strip(),
        "faction_color": str(data.get("color", "")).strip(),
        "stats": stats,
    }


# ── Exportar: enemigo guardado → statblock YAML ────────────

def enemy_to_dnd_statblock(e: dict) -> dict:
    """Reversa de `_build_from_data`: arma el dict YAML de una ficha guardada."""
    s = e.get("stats") or {}
    ab = s.get("abilities") or {}

    d = {"layout": "D&D 5e", "name": e.get("name", "")}
    if e.get("tipo"):
        d["type"] = e["tipo"]
    if s.get("ac"):
        d["ac"] = s["ac"]
    d["hp"] = e.get("vida_max", 0)
    if s.get("speed"):
        d["speed"] = s["speed"]
    if s.get("cr"):
        d["cr"] = s["cr"]
    for k in _ABILITIES:
        if ab.get(k.upper()) is not None:
            d[k] = ab[k.upper()]
    pairs = (("saves", "saves"), ("skills_text", "skills"),
             ("vulnerabilities", "vulnerabilities"), ("resistances", "resistances"),
             ("immunities", "immunities"), ("cond_immunities", "condition_immunities"),
             ("senses", "senses"), ("languages", "languages"))
    for src, dst in pairs:
        if s.get(src):
            d[dst] = s[src]
    if e.get("faction_color"):
        d["color"] = e["faction_color"]
    if e.get("notas"):
        d["notes"] = e["notas"]
    for src, dst in (("traits", "traits"), ("actions", "actions"), ("legendary", "legendary")):
        entries = s.get(src) or []
        if entries:
            d[dst] = [{"name": x.get("name", ""), "desc": x.get("desc", "")} for x in entries]
    return d


def export_dnd_statblocks(enemies: list[dict]) -> str:
    """Bestiario D&D completo como YAML multi-documento, re-importable en bulk."""
    docs = [enemy_to_dnd_statblock(e) for e in enemies]
    return yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True,
                              explicit_start=True, width=10_000)
