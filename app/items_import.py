"""
Parseo y exportación de objetos en formato YAML.

Mismo espíritu que `cosmere_import.py` (statblocks de enemigos): el DM pega una
ficha o un archivo entero y se cargan al catálogo; el catálogo se puede exportar
y volver a importar sin perder nada.

Cada objeto es de un **tipo** (`kind`) y guarda en `stats` los datos propios de
ese tipo, porque un arma y una posada no se listan igual:

    kind: arma          -> weapon_class, damage, range, traits, expert_traits, skill
    kind: armadura      -> deflect, traits, expert_traits
    kind: equipo        -> nada extra (precio + descripción); se subdivide con
                           `categories` (medicina, herramientas, comida…)
    kind: alojamiento   -> precio por persona por noche
    kind: vehiculo      -> vehicle_type, speed, rental
    kind: fabrial       -> charges

Ejemplo:

    kind: arma
    weapon_class: light
    name: "Javelin"
    damage: "1d6 keen"
    range: "Melee"
    traits: "Thrown [30/120]"
    expert_traits: "Indirect"
    weight: "2 lb."
    price: 20
"""

import re

import yaml

from .cosmere_import import ImportError_, _strip_code_fence

# Tipos principales. Cada uno se lista y se muestra distinto.
KINDS = ["arma", "armadura", "equipo", "alojamiento", "vehiculo", "fabrial"]
KIND_LABELS = {"arma": "Armas", "armadura": "Armaduras", "equipo": "Equipo",
               "alojamiento": "Alojamiento", "vehiculo": "Vehículos",
               "fabrial": "Fabriales"}
WEAPON_CLASSES = ["light", "heavy", "special"]

# Subcategorías sugeridas para el equipo (el DM puede escribir las que quiera).
CATEGORIAS = ["generales", "comida", "herramientas", "medicina", "alquimia",
              "materiales", "ropa", "lujo", "libros", "animales", "iluminacion",
              "contenedores", "venenos"]


def _to_int(value, default: int = 0) -> int:
    """Tolera '20 mk', '1–300 mk' (toma el primero) o 'Reward only' (default)."""
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        m = re.search(r"\d+(?:[.,]\d+)?", value.replace(",", ""))
        if m:
            return int(round(float(m.group())))
    return default


def _s(v) -> str:
    return "" if v is None else str(v).strip()


def _norm_list(raw) -> list[str]:
    """Acepta lista o texto separado por comas."""
    if isinstance(raw, str):
        raw = re.split(r",(?![^\[\(]*[\]\)])", raw)   # no corta dentro de [30/120]
    if not isinstance(raw, list):
        return []
    out = []
    for c in raw:
        s = str(c).strip()
        if s and s not in ("—", "-", "–") and s not in out:
            out.append(s)
    return out


def _norm_categories(raw) -> list[str]:
    return [c.lower() for c in _norm_list(raw)]


def _build_from_data(data: dict) -> dict:
    name = _s(data.get("name"))
    if not name:
        raise ImportError_("Falta el campo 'name'.")

    kind = _s(data.get("kind") or data.get("tipo")).lower() or "equipo"
    if kind not in KINDS:
        raise ImportError_(f"Tipo '{kind}' desconocido (usá: {', '.join(KINDS)}).")

    # Slots: 1 por defecto; `cumbersome N` ocupa 1+N (regla de carga).
    slots = data.get("slots")
    if slots is None and data.get("cumbersome") is not None:
        slots = 1 + max(0, _to_int(data.get("cumbersome")))

    stats = {}
    if kind == "arma":
        wc = _s(data.get("weapon_class") or data.get("clase")).lower() or "light"
        if wc not in WEAPON_CLASSES:
            wc = "special"
        stats = {"weapon_class": wc,
                 "damage": _s(data.get("damage")),
                 "range": _s(data.get("range")),
                 "traits": _norm_list(data.get("traits")),
                 "expert_traits": _norm_list(data.get("expert_traits")),
                 "skill": _s(data.get("skill"))}
    elif kind == "armadura":
        stats = {"deflect": _to_int(data.get("deflect")),
                 "traits": _norm_list(data.get("traits")),
                 "expert_traits": _norm_list(data.get("expert_traits"))}
    elif kind == "alojamiento":
        stats = {"per_night": _to_int(data.get("price", data.get("precio")))}
    elif kind == "vehiculo":
        stats = {"vehicle_type": _s(data.get("vehicle_type") or data.get("tipo_vehiculo")),
                 "speed": _s(data.get("speed")),
                 "rental": _to_int(data.get("rental"))}
    elif kind == "fabrial":
        stats = {"charges": _to_int(data.get("charges"))}

    # Dosis/cargas por unidad: 5 raciones ocupan 1 slot hasta agotarse.
    usos = _to_int(data.get("uses", data.get("usos", data.get("doses"))))
    if not usos and kind == "fabrial":
        usos = stats.get("charges", 0)

    cont_cap = _to_int(data.get("container", data.get("contenedor")))

    return {
        "name": name,
        "kind": kind,
        "descripcion": _s(data.get("description") or data.get("descripcion")),
        "categorias": _norm_categories(data.get("categories", data.get("categorias"))),
        "precio": max(0, _to_int(data.get("price", data.get("precio")))),
        "peso": _s(data.get("weight") or data.get("peso")),
        "slots": max(0, _to_int(slots, 1)),
        "capacity_bonus": max(0, _to_int(data.get("capacity_bonus"))),
        "usos_max": max(0, usos),
        "contenedor": 1 if cont_cap > 0 else 0,
        "contenedor_capacidad": max(0, cont_cap),
        "secreto": bool(data.get("secret", data.get("secreto"))),
        "notas": _s(data.get("notes") or data.get("notas")),
        "stats": stats,
    }


def parse_item(code: str) -> dict:
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


def parse_items_bulk(code: str):
    """Varios objetos de una vez. Devuelve (objetos, errores)."""
    text = _strip_code_fence(code)
    if not text.strip():
        raise ImportError_("El texto está vacío.")
    if re.search(r"^---\s*$", text, re.M):
        blocks = re.split(r"^---\s*$", text, flags=re.M)
    else:
        blocks = re.split(r"(?m)^(?=name\s*:)", text)

    items, errors, idx = [], [], 0
    for block in blocks:
        if not block.strip() or re.match(r"^\s*(#.*\n?)+$", block):
            continue
        idx += 1
        try:
            data = yaml.safe_load(block)
            if not isinstance(data, dict):
                raise ImportError_("no tiene formato clave: valor")
            items.append(_build_from_data(data))
        except (yaml.YAMLError, ImportError_) as e:
            errors.append(f"Bloque {idx}: {e}")
    return items, errors


def item_to_yaml(it: dict) -> dict:
    s = it.get("stats") or {}
    d = {"kind": it.get("kind", "equipo"), "name": it.get("name", "")}
    if it["kind"] == "arma":
        d["weapon_class"] = s.get("weapon_class", "light")
        for k in ("skill", "damage", "range"):
            if s.get(k):
                d[k] = s[k]
        for k in ("traits", "expert_traits"):
            if s.get(k):
                d[k] = list(s[k])
    elif it["kind"] == "armadura":
        d["deflect"] = s.get("deflect", 0)
        for k in ("traits", "expert_traits"):
            if s.get(k):
                d[k] = list(s[k])
    elif it["kind"] == "vehiculo":
        for k, key in (("vehicle_type", "vehicle_type"), ("speed", "speed")):
            if s.get(key):
                d[k] = s[key]
        if s.get("rental"):
            d["rental"] = s["rental"]
    elif it["kind"] == "fabrial" and s.get("charges"):
        d["charges"] = s["charges"]
    if it.get("categorias"):
        d["categories"] = list(it["categorias"])
    d["price"] = it.get("precio", 0)
    if it.get("peso"):
        d["weight"] = it["peso"]
    d["slots"] = it.get("slots", 1)
    if it.get("capacity_bonus"):
        d["capacity_bonus"] = it["capacity_bonus"]
    if it.get("usos_max"):
        d["uses"] = it["usos_max"]
    if it.get("contenedor_capacidad"):
        d["container"] = it["contenedor_capacidad"]
    if it.get("secreto"):
        d["secret"] = True
    if it.get("descripcion"):
        d["description"] = it["descripcion"]
    if it.get("notas"):
        d["notes"] = it["notas"]
    return d


def export_items(items: list[dict]) -> str:
    """Catálogo completo como YAML multi-documento, re-importable con bulk."""
    return yaml.safe_dump_all([item_to_yaml(i) for i in items], sort_keys=False,
                              allow_unicode=True, explicit_start=True, width=10_000)
