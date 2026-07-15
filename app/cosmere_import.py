"""
Parseo de statblocks de Cosmere RPG en formato YAML.

Acepta el bloque de texto que usan las fichas (estilo plugin *Statblocks*
de Obsidian, con `layout: Cosmere RPG`) y lo convierte en la estructura
de enemigo que usa la app: los campos base para el combate + un objeto
`stats` con la ficha completa para visualizarla.

Ejemplo de entrada:

    layout: Cosmere RPG
    name: "Archer"
    tier: "Tier 1 Minion – Medium Humanoid"
    str: 2
    pdef: 13
    ...
    actions:
      - name: "▶ Strike: Knife"
        desc: "Attack +3..."
"""

import re

import yaml


class ImportError_(ValueError):
    """Error de parseo legible para el usuario."""


def _strip_code_fence(text: str) -> str:
    """Quita un cerco de código Markdown (``` o ```statblock) si lo hay."""
    lines = text.strip().splitlines()
    if lines and lines[0].lstrip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines)


def _to_int(value, default: int = 0) -> int:
    """Convierte a int tolerando strings como '12 (9-15)' → 12."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"-?\d+", value)
        if m:
            return int(m.group())
    return default


def _norm_entries(raw) -> list[dict]:
    """Normaliza listas de rasgos/acciones a [{'name', 'desc'}]."""
    out = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            desc = str(item.get("desc", item.get("description", ""))).strip()
            if name or desc:
                out.append({"name": name, "desc": desc})
        elif isinstance(item, str) and item.strip():
            out.append({"name": item.strip(), "desc": ""})
    return out


# Coste entre paréntesis: "(Costs 1 Focus)", "(Cuesta 2 Focus)", etc.
_COST_RE = re.compile(r"\((?:costs?|cuesta)\s+([^)]+)\)", re.IGNORECASE)

# Color por defecto según la clase (el DM lo puede cambiar después).
_CLASS_COLORS = {"minion": "#8a8a8a", "rival": "#c87830", "boss": "#e05050"}


def _detect_clase(data: dict, tier: str) -> str:
    """Deduce la clase (minion/rival/boss) de un campo explícito o del tier."""
    explicit = str(data.get("class", data.get("clase", ""))).strip().lower()
    if explicit in _CLASS_COLORS:
        return explicit
    low = tier.lower()
    if "boss" in low:
        return "boss"
    if "minion" in low:
        return "minion"
    if "rival" in low:
        return "rival"
    return "rival"


_SIZES = ["Tiny", "Small", "Medium", "Large", "Huge", "Gargantuan"]


def _parse_tier_meta(tier: str) -> dict:
    """Del texto del tier ('Tier 1 Minion – Medium Humanoid') saca datos estructurados.

    Devuelve {tier_num, size, creature_type}; cada uno None si no se pudo deducir.
    """
    tier_num = None
    m = re.search(r"tier\s*(\d+)", tier, re.IGNORECASE)
    if m:
        tier_num = int(m.group(1))
    size = None
    for s in _SIZES:
        if re.search(r"\b" + s + r"\b", tier, re.IGNORECASE):
            size = s
            break
    creature_type = None
    # Tras el guion suele venir "Tamaño TipoDeCriatura" (ej: "Medium Humanoid").
    parts = re.split(r"[–—-]", tier)
    if len(parts) > 1:
        tail_words = parts[-1].split()
        rem = [w for w in tail_words if w.lower() not in (s.lower() for s in _SIZES)]
        if rem:
            creature_type = " ".join(rem).strip()
    return {"tier_num": tier_num, "size": size, "creature_type": creature_type}


def _action_to_accion(action: dict) -> dict:
    """Convierte una acción de la ficha al formato de acción de combate."""
    name = action["name"]
    coste = ""
    m = _COST_RE.search(name)
    if m:
        coste = m.group(1).strip()
        name = _COST_RE.sub("", name).strip()
    return {"nombre": name, "coste": coste, "descripcion": action["desc"]}


def parse_statblock(code: str) -> dict:
    """
    Parsea un bloque YAML y devuelve un dict con los campos del enemigo.

    Lanza ImportError_ con un mensaje claro si el texto no es válido.
    """
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


def parse_statblocks_bulk(code: str):
    """
    Parsea varias fichas de una sola vez.

    Los bloques se separan con líneas `---` (documentos YAML), o si no las hay,
    antes de cada `layout:` o `name:` al inicio de línea.

    Devuelve (lista_de_enemigos, lista_de_errores). Cada error es un texto
    legible con el número de bloque y el motivo, para reportárselo al usuario.
    """
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
    """Construye el enemigo a partir del dict YAML ya cargado."""
    name = str(data.get("name", "")).strip()
    if not name:
        raise ImportError_("Falta el campo 'name'.")

    tier = str(data.get("tier", "")).strip()
    health = str(data.get("health", "")).strip()
    clase = _detect_clase(data, tier)

    traits = _norm_entries(data.get("traits"))
    actions = _norm_entries(data.get("actions"))
    opportunities = _norm_entries(data.get("opportunities"))

    meta = _parse_tier_meta(tier)

    stats = {
        "tier": tier,
        "tier_num": meta["tier_num"],
        "size": meta["size"],
        "creature_type": meta["creature_type"],
        "health": health,
        "physical": {
            "str": _to_int(data.get("str")),
            "def": _to_int(data.get("pdef")),
            "spd": _to_int(data.get("spd")),
        },
        "cognitive": {
            "int": _to_int(data.get("int")),
            "def": _to_int(data.get("cdef")),
            "wil": _to_int(data.get("wil")),
        },
        "spiritual": {
            "awa": _to_int(data.get("awa")),
            "def": _to_int(data.get("sdef")),
            "pre": _to_int(data.get("pre")),
        },
        "focus": _to_int(data.get("focus")),
        "investiture": _to_int(data.get("investiture")),
        "deflect": str(data.get("deflect", "")).strip(),
        "movement": str(data.get("movement", "")).strip(),
        "senses": str(data.get("senses", "")).strip(),
        "immunities": str(data.get("immunities", "")).strip(),
        "resistances": str(data.get("resistances", "")).strip(),
        "weaknesses": str(data.get("weaknesses", "")).strip(),
        "languages": str(data.get("languages", "")).strip(),
        "skills": {
            "physical": str(data.get("skills_p", "")).strip(),
            "cognitive": str(data.get("skills_c", "")).strip(),
            "spiritual": str(data.get("skills_s", "")).strip(),
            "surge": str(data.get("skills_surge", "")).strip(),
        },
        "traits": traits,
        "actions": actions,
        "opportunities": opportunities,
    }

    # Vida para el tracker: 'hp' explícito, si no el número inicial de 'health'.
    vida_max = _to_int(data.get("hp"), _to_int(health, 20))

    # `color` y `notes` no son parte del formato original, pero los emite el
    # exportador para que el bestiario vaya y vuelva sin perder nada.
    color = str(data.get("color", "")).strip()
    notas = str(data.get("notes", data.get("notas", ""))).strip()

    return {
        "name": name,
        "tipo": tier,
        "clase": clase,
        "vida_max": vida_max,
        "focus_max": stats["focus"],
        "inv_max": stats["investiture"],
        "acciones": [_action_to_accion(a) for a in actions],
        "notas": notas,
        "faction_color": color or _CLASS_COLORS.get(clase, ""),
        "stats": stats,
    }


# ── Exportar: enemigo guardado → statblock YAML ────────────

def enemy_to_statblock(e: dict) -> dict:
    """Reversa de `_build_from_data`: arma el dict YAML de una ficha guardada."""
    s = e.get("stats") or {}
    ph, co, sp = s.get("physical") or {}, s.get("cognitive") or {}, s.get("spiritual") or {}
    sk = s.get("skills") or {}

    d = {"layout": "Cosmere RPG", "name": e.get("name", "")}
    tier = s.get("tier") or e.get("tipo") or ""
    if tier:
        d["tier"] = tier
    d["class"] = e.get("clase") or "rival"

    d["str"], d["pdef"], d["spd"] = ph.get("str", 0), ph.get("def", 0), ph.get("spd", 0)
    if s.get("health"):
        d["health"] = s["health"]
    d["int"], d["cdef"], d["wil"] = co.get("int", 0), co.get("def", 0), co.get("wil", 0)
    d["focus"] = e.get("focus_max", 0)
    d["awa"], d["sdef"], d["pre"] = sp.get("awa", 0), sp.get("def", 0), sp.get("pre", 0)
    d["investiture"] = e.get("inv_max", 0)
    d["hp"] = e.get("vida_max", 0)

    for k in ("deflect", "movement", "senses", "immunities", "resistances",
              "weaknesses", "languages"):
        if s.get(k):
            d[k] = s[k]
    for src, dst in (("physical", "skills_p"), ("cognitive", "skills_c"),
                     ("spiritual", "skills_s"), ("surge", "skills_surge")):
        if sk.get(src):
            d[dst] = sk[src]

    if e.get("faction_color"):
        d["color"] = e["faction_color"]
    if e.get("notas"):
        d["notes"] = e["notas"]
    for k in ("traits", "actions", "opportunities"):
        entries = s.get(k) or []
        if entries:
            d[k] = [{"name": x.get("name", ""), "desc": x.get("desc", "")} for x in entries]
    return d


def export_statblocks(enemies: list[dict]) -> str:
    """Bestiario completo como YAML multi-documento, re-importable con import-bulk."""
    docs = [enemy_to_statblock(e) for e in enemies]
    return yaml.safe_dump_all(docs, sort_keys=False, allow_unicode=True,
                              explicit_start=True, width=10_000)
