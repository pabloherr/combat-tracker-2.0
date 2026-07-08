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
    Parsea el bloque YAML y devuelve un dict con los campos del enemigo.

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

    name = str(data.get("name", "")).strip()
    if not name:
        raise ImportError_("Falta el campo 'name'.")

    tier = str(data.get("tier", "")).strip()
    health = str(data.get("health", "")).strip()
    clase = _detect_clase(data, tier)

    traits = _norm_entries(data.get("traits"))
    actions = _norm_entries(data.get("actions"))
    opportunities = _norm_entries(data.get("opportunities"))

    stats = {
        "tier": tier,
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

    return {
        "name": name,
        "tipo": tier,
        "clase": clase,
        "vida_max": vida_max,
        "focus_max": stats["focus"],
        "inv_max": stats["investiture"],
        "acciones": [_action_to_accion(a) for a in actions],
        "notas": "",
        "faction_color": _CLASS_COLORS.get(clase, ""),
        "stats": stats,
    }
