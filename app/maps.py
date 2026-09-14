"""
Mapas de campaña: escala, distancias y tiempo de viaje.

La imagen del mapa se guarda tal cual la subió el DM, y los puntos se guardan
en **coordenadas relativas** (`x`, `y` de 0 a 1 sobre el ancho y el alto de la
imagen): si mañana sube la misma lámina en mejor resolución, los puntos siguen
donde estaban.

La escala sale de una sola cifra, `ancho_real`: cuánto mide el mapa de lado a
lado en el mundo. De ahí se deduce todo lo demás::

    escala = ancho_real / ancho_en_pixeles      (unidades por pixel)

Como los pixeles son cuadrados, la misma escala vale para el alto, así que el
alto real no hace falta guardarlo: se deduce. Calibrar con una regla (trazar
una línea sobre algo de largo conocido) tampoco guarda nada nuevo: despeja el
`ancho_real` que hace que esa línea mida lo que el DM dijo.

Este módulo es solo cuentas: no toca la base ni FastAPI, así que se puede
probar suelto.
"""

import math

# Unidad de distancia de la campaña. Se elige una y vale para todo: los mapas,
# la velocidad de los transportes y lo que se mide.
UNITS = {"km": "km", "mi": "mi"}
DEFAULT_UNIT = "km"

# Transportes sugeridos al estrenar el módulo. Velocidad en km/h de marcha real
# (no el sprint) y horas de camino por jornada. El DM los cambia o los borra.
DEFAULT_MODES = [
    {"name": "A pie", "icono": "🥾", "velocidad": 4.0, "horas_dia": 8,
     "notas": "Marcha sostenida por terreno normal."},
    {"name": "A caballo", "icono": "🐴", "velocidad": 8.0, "horas_dia": 8,
     "notas": "Al paso, alternando trote. Al galope se cansa en una hora."},
    {"name": "Carreta de chull", "icono": "🐚", "velocidad": 3.0, "horas_dia": 10,
     "notas": "Lento pero incansable: carga y aguanta jornadas largas."},
    {"name": "Barco", "icono": "⛵", "velocidad": 12.0, "horas_dia": 24,
     "notas": "Navega de noche, con turnos de tripulación."},
]

# Topes, para que un dedazo no deje el mapa inservible ni la cuenta en infinito.
MAX_ANCHO = 10_000_000.0     # un mapa no mide más que esto de lado a lado
MIN_ANCHO = 0.001
MAX_VEL = 100_000.0
MAX_HORAS_DIA = 24.0


def unit(value) -> str:
    """Normaliza la unidad; lo que no se entiende cae en km."""
    v = str(value or "").strip().lower()
    return v if v in UNITS else DEFAULT_UNIT


def clamp01(v) -> float:
    """Coordenada relativa: siempre dentro de la imagen."""
    try:
        return max(0.0, min(1.0, float(v)))
    except (TypeError, ValueError):
        return 0.0


def escala(img_w: int, ancho_real: float) -> float:
    """Unidades de mundo por pixel de imagen. 0 si el mapa no está escalado."""
    if not img_w or ancho_real <= 0:
        return 0.0
    return ancho_real / float(img_w)


def alto_real(img_w: int, img_h: int, ancho_real: float) -> float:
    """El alto se deduce: los pixeles son cuadrados, la escala es la misma."""
    return escala(img_w, ancho_real) * (img_h or 0)


def distancia(img_w: int, img_h: int, ancho_real: float, a: dict, b: dict) -> float:
    """Distancia en línea recta entre dos puntos relativos, en unidades.

    Las coordenadas son fracciones, así que primero vuelven a pixeles (que es
    donde la geometría es isótropa) y recién ahí se multiplican por la escala.
    """
    esc = escala(img_w, ancho_real)
    if not esc:
        return 0.0
    dx = (clamp01(b.get("x")) - clamp01(a.get("x"))) * img_w
    dy = (clamp01(b.get("y")) - clamp01(a.get("y"))) * (img_h or 0)
    return math.hypot(dx, dy) * esc


def ancho_por_regla(img_w: int, img_h: int, a: dict, b: dict, medida: float) -> float:
    """Calibrar: qué `ancho_real` hace que el tramo `a`-`b` mida `medida`.

    Es el despeje de `distancia()`: si la línea ocupa `p` pixeles y el DM dice
    que son `medida` unidades, entonces cada pixel vale `medida / p` y el mapa
    entero mide `ancho_en_pixeles` por eso.
    """
    if not img_w or medida <= 0:
        return 0.0
    dx = (clamp01(b.get("x")) - clamp01(a.get("x"))) * img_w
    dy = (clamp01(b.get("y")) - clamp01(a.get("y"))) * (img_h or 0)
    px = math.hypot(dx, dy)
    if px < 1e-9:
        return 0.0
    return max(MIN_ANCHO, min(MAX_ANCHO, (medida / px) * img_w))


def ruta(img_w: int, img_h: int, ancho_real: float, puntos: list) -> dict:
    """Mide una ruta de varias paradas: cada tramo y el total.

    Con menos de dos puntos no hay nada que medir y el total es 0 (no es un
    error: el frontend llama a esto mientras el DM todavía está clickeando).
    """
    tramos = []
    total = 0.0
    for a, b in zip(puntos, puntos[1:]):
        d = distancia(img_w, img_h, ancho_real, a, b)
        total += d
        tramos.append({"distancia": round(d, 2)})
    return {"distancia": round(total, 2), "tramos": tramos}


def viaje(dist: float, velocidad: float, horas_dia: float) -> dict:
    """Tiempo de viaje para una distancia y un transporte.

    Devuelve las horas de marcha (no de reloj) y en cuántas jornadas entran.
    `dias` es fraccionario para poder ordenar y comparar; `dias_enteros` es el
    número que se dice en la mesa ("son cuatro días de camino"), redondeado
    para arriba porque medio día de marcha igual te obliga a acampar.
    """
    velocidad = max(0.0, float(velocidad or 0))
    horas_dia = max(0.0, min(MAX_HORAS_DIA, float(horas_dia or 0)))
    if velocidad <= 0 or dist <= 0:
        return {"horas": 0.0, "dias": 0.0, "dias_enteros": 0}
    horas = dist / velocidad
    if horas_dia <= 0:
        return {"horas": round(horas, 2), "dias": 0.0, "dias_enteros": 0}
    dias = horas / horas_dia
    return {"horas": round(horas, 2), "dias": round(dias, 2),
            "dias_enteros": math.ceil(dias - 1e-9)}


def viajes(dist: float, modos: list) -> list:
    """El mismo trayecto, con cada transporte que tenga la campaña."""
    out = []
    for m in modos:
        t = viaje(dist, m.get("velocidad", 0), m.get("horas_dia", 0))
        out.append({"id": m.get("id"), "name": m.get("name", ""),
                    "icono": m.get("icono", ""), "velocidad": m.get("velocidad", 0),
                    "horas_dia": m.get("horas_dia", 0), **t})
    return out


# ── Tamaño de la imagen, leyendo el encabezado ─────────────
# La escala se apoya en el ancho en pixeles, así que hay que saberlo del lado
# del servidor (el navegador lo sabe, pero no se le cree para una cuenta que
# después usan todos). No hace falta Pillow: el alto y el ancho viven en los
# primeros bytes de cada formato.

SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
               0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _png_size(d: bytes):
    if len(d) >= 24 and d[:8] == b"\x89PNG\r\n\x1a\n" and d[12:16] == b"IHDR":
        return (int.from_bytes(d[16:20], "big"), int.from_bytes(d[20:24], "big"),
                "image/png")
    return None


def _gif_size(d: bytes):
    if len(d) >= 10 and d[:6] in (b"GIF87a", b"GIF89a"):
        return (int.from_bytes(d[6:8], "little"),
                int.from_bytes(d[8:10], "little"), "image/gif")
    return None


def _jpeg_size(d: bytes):
    """Recorre los segmentos hasta el SOF, que es el que trae las medidas."""
    if len(d) < 4 or d[:2] != b"\xff\xd8":
        return None
    i = 2
    while i + 9 < len(d):
        if d[i] != 0xFF:            # relleno entre segmentos
            i += 1
            continue
        marker = d[i + 1]
        if marker == 0xFF:
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        if marker == 0xD9 or marker == 0xDA:   # fin / arranca la imagen
            return None
        seglen = int.from_bytes(d[i + 2:i + 4], "big")
        if seglen < 2:
            return None
        if marker in SOF_MARKERS:
            return (int.from_bytes(d[i + 7:i + 9], "big"),
                    int.from_bytes(d[i + 5:i + 7], "big"), "image/jpeg")
        i += 2 + seglen
    return None


def _webp_size(d: bytes):
    if len(d) < 30 or d[:4] != b"RIFF" or d[8:12] != b"WEBP":
        return None
    fourcc = d[12:16]
    if fourcc == b"VP8X":           # extendido: el lienzo va en el encabezado
        return (int.from_bytes(d[24:27], "little") + 1,
                int.from_bytes(d[27:30], "little") + 1, "image/webp")
    if fourcc == b"VP8L":           # sin pérdida: 14 bits para cada medida
        bits = int.from_bytes(d[21:25], "little")
        return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1, "image/webp")
    if fourcc == b"VP8 " and d[23:26] == b"\x9d\x01\x2a":
        return (int.from_bytes(d[26:28], "little") & 0x3FFF,
                int.from_bytes(d[28:30], "little") & 0x3FFF, "image/webp")
    return None


def image_size(data: bytes):
    """(ancho, alto, mime) de la imagen, o None si no se reconoce el formato."""
    for probe in (_png_size, _jpeg_size, _gif_size, _webp_size):
        try:
            got = probe(data)
        except Exception:
            got = None
        if got and got[0] > 0 and got[1] > 0:
            return got
    return None
