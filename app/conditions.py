"""
Condiciones y heridas del Cosmere RPG.

Acá vive el catálogo canónico: el nombre de cada condición, si es buena o mala,
qué le pide entre corchetes, si se puede tener varias veces y —lo importante—
qué le hace a la ficha. La ficha del jugador lee `efecto` para aplicar los
números; las que no tocan la ficha traen solo su descripción.

El DM apaga las que no quiera en su campaña y agrega las suyas (ver
`app/config.py`: `cond_off` / `cond_extra` y `her_off` / `her_extra`). El
catálogo no se copia por campaña: así el texto de las reglas queda en un solo
lugar.

Formato de `efecto` (todo opcional; `{}` = no toca la ficha):

    {"attr": 1}               el corchete trae atributo y número, y suma
    {"attr": -1}              idem, pero resta
    {"tests": True}           el corchete trae la penalización a los tests
    {"mov": "mitad"}          el movimiento se parte al medio
    {"mov": 0}                el movimiento queda en 0
    {"desv": "todos"}         desventaja en todos los tests
    {"desv": ["Perception"]}  desventaja en esas habilidades

Y `param` dice qué pedirle a quien la aplica:

    "attr"   un atributo y un número (Enhanced [Speed +2])
    "penal"  un número negativo (Exhausted [−2])
    "texto"  texto libre (Afflicted [1d4 vital], Depleted [Gold])
    None     nada
"""

# Cómo se escribe un atributo entre corchetes → clave de la ficha. Se acepta el
# nombre entero, la abreviatura y el nombre en castellano.
ATTR_KEYS = {
    "strength": "STR", "str": "STR", "fuerza": "STR", "fue": "STR",
    "speed": "SPD", "spd": "SPD", "velocidad": "SPD", "vel": "SPD",
    "intellect": "INT", "int": "INT", "intelecto": "INT",
    "willpower": "WIL", "wil": "WIL", "voluntad": "WIL", "vol": "WIL",
    "awareness": "AWA", "awa": "AWA", "conciencia": "AWA", "con": "AWA",
    "presence": "PRE", "pre": "PRE", "presencia": "PRE",
}

# Etiqueta linda para cada atributo (la que se ofrece al elegir uno).
ATTR_LABELS = [("STR", "Strength"), ("SPD", "Speed"), ("INT", "Intellect"),
               ("WIL", "Willpower"), ("AWA", "Awareness"), ("PRE", "Presence")]

CONDICIONES = [
    {
        "name": "Afflicted", "tono": "neg", "param": "texto", "ph": "1d4 vital",
        "apila": True, "efecto": {},
        "desc": "Recibís daño con el tiempo. En combate, al final de cada uno de tus "
                "turnos recibís la cantidad y el tipo de daño que indique el efecto que "
                "te la aplicó (va entre corchetes): con Afflicted [1d4 vital] recibís "
                "1d4 al final de cada turno tuyo. Fuera de combate ese daño cae cada 10 "
                "segundos, y también cada vez que alguien intenta quitarte la condición. "
                "A diferencia de casi todas, podés estar Afflicted por varios efectos a "
                "la vez: cada uno se resuelve por separado.",
    },
    {
        "name": "Depleted", "tono": "neg", "param": "texto", "ph": "Gold",
        "apila": True, "efecto": {},
        "desc": "Te quedaste sin acceso a la Investidura de ese poder, así que no podés "
                "usarlo: ni gastar Investidura en sus efectos, ni contar como Investido "
                "para lo que dependa de él, ni aprovechar sus efectos nacientes. El poder "
                "va entre corchetes (Depleted [Gold]). Podés estar Depleted de varios "
                "poderes a la vez. Se quita con la acción de Beber un vial.",
    },
    {
        "name": "Determined", "tono": "pos", "param": None, "apila": False, "efecto": {},
        "desc": "Cuando falles un test, podés sumarle una Oportunidad al resultado. "
                "Después de hacerlo, se quita la condición.",
    },
    {
        "name": "Diminished", "tono": "neg", "param": "attr", "apila": True,
        "efecto": {"attr": -1},
        "desc": "Uno de tus atributos baja temporalmente en lo que diga el corchete. "
                "La baja NO cambia tus defensas, ni tu vida, focus o Investidura máximos: "
                "solo los tests de las habilidades de ese atributo, los talentos que lo "
                "usen y —si es Velocidad— tu movimiento. Se acumula, y podés tener varios "
                "atributos Diminished a la vez.",
    },
    {
        "name": "Disoriented", "tono": "neg", "param": None, "apila": False,
        "efecto": {"desv": ["Perception"]},
        "desc": "Tenés los sentidos alterados. No podés usar reacciones, tus sentidos "
                "cuentan siempre como obstruidos, y los tests de Perception (y cualquiera "
                "parecido que use los sentidos) tienen desventaja.",
    },
    {
        "name": "Enhanced", "tono": "pos", "param": "attr", "apila": True,
        "efecto": {"attr": 1},
        "desc": "Uno de tus atributos sube temporalmente en lo que diga el corchete. "
                "La suba NO cambia tus defensas, ni tu vida, focus o Investidura máximos: "
                "solo los tests de las habilidades de ese atributo, los talentos que lo "
                "usen y —si es Velocidad— tu movimiento. Se acumula, y podés tener varios "
                "atributos Enhanced a la vez.",
    },
    {
        "name": "Exhausted", "tono": "neg", "param": "penal", "apila": True,
        "efecto": {"tests": True},
        "desc": "Estás agotado y todo te sale peor. Después de calcular el resultado de un "
                "test y antes de resolverlo, aplicale la penalización del corchete. Cada "
                "descanso largo la baja en 1, y la condición se va cuando llega a 0. Se "
                "acumula: Exhausted [−2] y después Exhausted [−1] te dejan en −3. El "
                "resultado final de un test nunca baja de 0.",
    },
    {
        "name": "Focused", "tono": "pos", "param": None, "apila": False, "efecto": {},
        "desc": "Estás concentrado en lo que hacés: cuando uses una habilidad que cueste "
                "focus, su costo baja en 1.",
    },
    {
        "name": "Immobilized", "tono": "neg", "param": None, "apila": False,
        "efecto": {"mov": 0},
        "desc": "Tu movimiento queda en 0: no podés moverte ni ser movido por otros efectos.",
    },
    {
        "name": "Prone", "tono": "neg", "param": None, "apila": False,
        "efecto": {"mov": "mitad"},
        "desc": "Estás tirado en el piso. Contás como Slowed y los ataques cuerpo a cuerpo "
                "contra vos tienen ventaja. Podés usar la acción de Cubrirte sin cobertura. "
                "Levantarte cuesta una acción y quita la condición; después tu movimiento "
                "baja 5 pies hasta el inicio de tu próximo turno. Si te quedás Prone "
                "trepando o volando, caés y recibís el daño de siempre.",
    },
    {
        "name": "Restrained", "tono": "neg", "param": None, "apila": False,
        "efecto": {"mov": 0, "desv": "todos"},
        "desc": "Tu movimiento queda en 0 y tenés desventaja en todos los tests menos los "
                "que hagas para zafarte. Si el efecto que te apresó no dice una DC para "
                "escapar, el DM decide si se puede salir antes y cómo.",
    },
    {
        "name": "Slowed", "tono": "neg", "param": None, "apila": False,
        "efecto": {"mov": "mitad"},
        "desc": "Tu movimiento se parte al medio. Si te frenan en mitad de un movimiento, "
                "se parte al medio lo que te quedaba (redondeando para arriba).",
    },
    {
        "name": "Stunned", "tono": "neg", "param": None, "apila": False, "efecto": {},
        "desc": "En combate perdés tus reacciones y, en tu turno, tenés dos acciones menos "
                "y no ganás reacción. Fuera de combate estás sobrepasado: te movés y "
                "reaccionás más lento, a criterio del DM.",
    },
    {
        "name": "Surprised", "tono": "neg", "param": None, "apila": False, "efecto": {},
        "desc": "Perdés tus reacciones y no ganás reacción ni al empezar el combate ni en "
                "tu turno, no podés tomar turno rápido, y tenés una acción menos. Se quita "
                "después de tu próximo turno.",
    },
    {
        "name": "Unconscious", "tono": "neg", "param": None, "apila": False,
        "efecto": {"mov": 0},
        "desc": "Tu movimiento queda en 0, no podés moverte ni comunicarte, y no te enterás "
                "de nada de lo que pasa alrededor. Al quedar inconsciente caés Prone y "
                "soltás lo que llevabas. No podés interactuar con el entorno ni usar "
                "acciones o reacciones. En combate siempre vas lento, pero no podés hacer "
                "nada en tu turno. Los enemigos suelen ignorarte salvo que tengan una buena "
                "razón. Si sos PJ podés elegir despertarte al final de cualquiera de tus "
                "turnos, o cuando algo te cure a 1 de vida o más: al hacerlo los demás se "
                "dan cuenta, se quita la condición y —si estabas en 0— recuperás 1 de vida. "
                "Ojo: con tan poca vida es fácil llevarse otra herida, y peor.",
    },
]

# Heridas que puede sacar un personaje. Cada una puede imponer una condición
# mientras dure (el jugador no la puede quitar a mano: se va al curar la herida).
HERIDAS = [
    {"name": "Exhausted [−1]", "cond": "Exhausted [−1]"},
    {"name": "Exhausted [−2]", "cond": "Exhausted [−2]"},
    {"name": "Slowed", "cond": "Slowed"},
    {"name": "Disoriented", "cond": "Disoriented"},
    {"name": "Surprised", "cond": "Surprised"},
    {"name": "Diminished [Strength −1]", "cond": "Diminished [Strength −1]"},
    {"name": "Diminished [Speed −1]", "cond": "Diminished [Speed −1]"},
    {"name": "Diminished [Intellect −1]", "cond": "Diminished [Intellect −1]"},
    {"name": "Diminished [Willpower −1]", "cond": "Diminished [Willpower −1]"},
    {"name": "Diminished [Awareness −1]", "cond": "Diminished [Awareness −1]"},
    {"name": "Diminished [Presence −1]", "cond": "Diminished [Presence −1]"},
    {"name": "Can only use one hand", "cond": ""},
]

# Efectos que el DM puede elegir para una condición propia. La clave viaja en
# `cond_extra[].ef` y acá se traduce al `efecto` de arriba.
EFECTOS_MENU = [
    {"k": "", "n": "Ninguno (solo descripción)", "efecto": {}},
    {"k": "mov_mitad", "n": "Movimiento a la mitad", "efecto": {"mov": "mitad"}},
    {"k": "mov_cero", "n": "Movimiento en 0", "efecto": {"mov": 0}},
    {"k": "desv_todos", "n": "Desventaja en todos los tests", "efecto": {"desv": "todos"}},
    {"k": "tests", "n": "Penalización a los tests (va entre corchetes)",
     "efecto": {"tests": True}, "param": "penal"},
    {"k": "attr_mas", "n": "Sube un atributo (va entre corchetes)",
     "efecto": {"attr": 1}, "param": "attr"},
    {"k": "attr_menos", "n": "Baja un atributo (va entre corchetes)",
     "efecto": {"attr": -1}, "param": "attr"},
]
_EFECTOS = {e["k"]: e for e in EFECTOS_MENU}

_TONOS = ("pos", "neg", "neutro")


def _extra_cond(raw: dict) -> dict | None:
    """Normaliza una condición que cargó el DM. Devuelve None si no sirve."""
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()[:60]
    if not name:
        return None
    ef = _EFECTOS.get(str(raw.get("ef") or ""), _EFECTOS[""])
    tono = str(raw.get("tono") or "neg").strip().lower()
    return {
        "name": name,
        "tono": tono if tono in _TONOS else "neg",
        "desc": str(raw.get("desc") or "").strip()[:1200],
        "param": ef.get("param"),
        "ph": str(raw.get("ph") or "").strip()[:40] or None,
        "apila": bool(raw.get("apila")) or ef.get("param") is not None,
        "efecto": dict(ef["efecto"]),
        "ef": ef["k"],
        "propia": True,
    }


def _extra_herida(raw: dict) -> dict | None:
    if not isinstance(raw, dict):
        return None
    name = str(raw.get("name") or "").strip()[:80]
    if not name:
        return None
    return {"name": name, "cond": str(raw.get("cond") or "").strip()[:80], "propia": True}


def resolve(cfg: dict) -> dict:
    """Las condiciones y heridas que valen en esta campaña.

    Sale el catálogo sin las que el DM apagó, más las que agregó. Lo consumen
    la ficha del jugador (para aplicar los efectos) y el panel del DM.
    """
    off = set(cfg.get("cond_off") or [])
    conds = [dict(c) for c in CONDICIONES if c["name"] not in off]
    for raw in (cfg.get("cond_extra") or []):
        c = _extra_cond(raw)
        if c and c["name"] not in off:
            conds.append(c)

    hoff = set(cfg.get("her_off") or [])
    hers = [dict(h) for h in HERIDAS if h["name"] not in hoff]
    for raw in (cfg.get("her_extra") or []):
        h = _extra_herida(raw)
        if h and h["name"] not in hoff:
            hers.append(h)

    return {
        "condiciones": conds,
        "heridas": hers,
        "atributos": ATTR_KEYS,
        "attr_labels": [{"k": k, "n": n} for k, n in ATTR_LABELS],
        # Para el panel del DM: el catálogo entero (también las que apagó, que
        # si no no las podría volver a prender) y los efectos que puede elegir.
        "catalogo": CONDICIONES,
        "catalogo_heridas": HERIDAS,
        "efectos": [{"k": e["k"], "n": e["n"]} for e in EFECTOS_MENU],
    }
