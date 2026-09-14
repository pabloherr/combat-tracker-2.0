"""
API: Mapas de campaña, puntos de interés y tiempos de viaje.

El DM sube una lámina, le dice cuánto mide de lado a lado (o la calibra
trazando una regla sobre algo de largo conocido) y a partir de ahí el mapa
sabe traducir pixeles a distancias del mundo. Encima de eso, cualquiera de la
mesa clava puntos con nombre y descripción, y mide recorridos: la API devuelve
la distancia y cuánto se tarda con cada medio de transporte de la campaña.

Los interruptores son los mismos tres que los del calendario, en los ajustes:
`modulo_mapa` lo prende, `mapa_visible` decide si los jugadores lo ven y
`mapa_editable` si pueden marcar puntos. La unidad (`mapa_unidad`) vale para
toda la campaña: los mapas y las velocidades hablan el mismo idioma.

Las cuentas (escala, distancias, tiempos, tamaño de la imagen) viven en
`app/maps.py`, que no toca la base ni FastAPI.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from .. import maps as M
from ..access import require_access, require_dm
from ..auth import current_user
from ..config import get_config
from ..database import db
from ..models import MapCalibrate, MapIn, MapPointIn, MeasureIn, TravelModeIn

router = APIRouter(prefix="/api/campaigns/{cid}", tags=["maps"])

# Una lámina de mapa es grande, pero no infinita: el blob viaja en cada
# consulta de la imagen y vive dentro del sqlite.
MAX_IMAGE = 12 * 1024 * 1024


# ── Acceso ─────────────────────────────────────────────────

def _access(conn, cid: int, user, need_edit: bool = False):
    """(cfg, is_dm) si quien pide puede ver (o anotar en) los mapas."""
    _, is_dm = require_access(conn, cid, user)
    cfg = get_config(conn, cid)
    if not cfg["modulo_mapa"]:
        raise HTTPException(404, "El mapa no está activo en esta campaña")
    if not is_dm and not cfg["mapa_visible"]:
        raise HTTPException(403, "El DM no comparte el mapa")
    if need_edit and not is_dm and not cfg["mapa_editable"]:
        raise HTTPException(403, "El DM no deja marcar puntos en el mapa")
    return cfg, is_dm


def _dm_access(conn, cid: int, user):
    """Lo que solo hace el DM: subir mapas, calibrarlos y tocar transportes."""
    require_dm(conn, cid, user)
    cfg = get_config(conn, cid)
    if not cfg["modulo_mapa"]:
        raise HTTPException(404, "El mapa no está activo en esta campaña")
    return cfg


# ── Serialización ──────────────────────────────────────────

def _map_row(conn, cid: int, mid: int, is_dm: bool):
    r = conn.execute("SELECT * FROM maps WHERE id=? AND campaign_id=?",
                     (mid, cid)).fetchone()
    if not r or (r["secreto"] and not is_dm):
        raise HTTPException(404, "Ese mapa no existe")
    return r


def _map_json(r, unidad: str, n_puntos: int | None = None) -> dict:
    """El mapa tal como lo consume el frontend: además de lo guardado, la
    escala ya resuelta (para dibujar la regla sin repetir la cuenta)."""
    d = {
        "id": r["id"], "name": r["name"], "descripcion": r["descripcion"] or "",
        "img_w": r["img_w"], "img_h": r["img_h"],
        "ancho_real": round(r["ancho_real"], 4),
        "alto_real": round(M.alto_real(r["img_w"], r["img_h"], r["ancho_real"]), 4),
        "escala": M.escala(r["img_w"], r["ancho_real"]),
        "escalado": r["ancho_real"] > 0,
        "unidad": unidad, "secreto": bool(r["secreto"]),
        "created_at": r["created_at"],
    }
    if n_puntos is not None:
        d["n_puntos"] = n_puntos
    return d


def _point_json(r) -> dict:
    return {"id": r["id"], "map_id": r["map_id"], "user_id": r["user_id"],
            "name": r["name"], "descripcion": r["descripcion"] or "",
            "x": r["x"], "y": r["y"], "icono": r["icono"] or "",
            "color": r["color"] or "", "secreto": bool(r["secreto"])}


def _points(conn, mid: int, is_dm: bool) -> list:
    """Los puntos del mapa. Los secretos del DM ni se mandan: si no viajan, no
    hay nada que espiar mirando la red."""
    sql = "SELECT * FROM map_points WHERE map_id=?"
    if not is_dm:
        sql += " AND secreto=0"
    return [_point_json(r) for r in conn.execute(sql + " ORDER BY name", (mid,))]


def _mode_json(r) -> dict:
    return {"id": r["id"], "name": r["name"], "icono": r["icono"] or "",
            "velocidad": r["velocidad"], "horas_dia": r["horas_dia"],
            "notas": r["notas"] or ""}


def _modes(conn, cid: int) -> list:
    return [_mode_json(r) for r in conn.execute(
        "SELECT * FROM travel_modes WHERE campaign_id=? ORDER BY orden, id", (cid,))]


def _seed_modes(conn, cid: int):
    """Transportes sugeridos, para que el módulo no arranque vacío."""
    for i, m in enumerate(M.DEFAULT_MODES):
        conn.execute(
            "INSERT INTO travel_modes (campaign_id, name, icono, velocidad, horas_dia, "
            "notas, orden) VALUES (?,?,?,?,?,?,?)",
            (cid, m["name"], m["icono"], m["velocidad"], m["horas_dia"],
             m["notas"], i))


# ── Mapas ──────────────────────────────────────────────────

@router.get("/maps")
def list_maps(cid: int, user=Depends(current_user)):
    """Los mapas de la campaña + el estado del módulo.

    Si está apagado (o el DM se lo guarda para él) vuelve `enabled`/`visible`
    en false y la lista vacía: el frontend no dibuja la pestaña."""
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
        cfg = get_config(conn, cid)
        base = {"enabled": bool(cfg["modulo_mapa"]),
                "visible": True if is_dm else bool(cfg["mapa_visible"]),
                "editable": True if is_dm else bool(cfg["mapa_editable"]),
                "unidad": cfg["mapa_unidad"], "is_dm": is_dm,
                "user_id": user["id"], "maps": [], "modes": []}
        if not base["enabled"] or not base["visible"]:
            return base
        sql = "SELECT * FROM maps WHERE campaign_id=?"
        if not is_dm:
            sql += " AND secreto=0"
        rows = conn.execute(sql + " ORDER BY id", (cid,)).fetchall()
        # Cuántos puntos tiene cada mapa (los secretos solo cuentan para el DM).
        cuenta = {}
        pcount = ("SELECT p.map_id, COUNT(*) n FROM map_points p "
                  "JOIN maps m ON m.id=p.map_id WHERE m.campaign_id=?"
                  + ("" if is_dm else " AND p.secreto=0") + " GROUP BY p.map_id")
        for r in conn.execute(pcount, (cid,)):
            cuenta[r["map_id"]] = r["n"]
        base["maps"] = [_map_json(r, cfg["mapa_unidad"], cuenta.get(r["id"], 0))
                        for r in rows]
        base["modes"] = _modes(conn, cid)
        return base


@router.get("/maps/{mid}")
def get_map(cid: int, mid: int, user=Depends(current_user)):
    """Un mapa con sus puntos y los transportes de la campaña."""
    with db() as conn:
        cfg, is_dm = _access(conn, cid, user)
        r = _map_row(conn, cid, mid, is_dm)
        return {**_map_json(r, cfg["mapa_unidad"]),
                "editable": True if is_dm else bool(cfg["mapa_editable"]),
                "is_dm": is_dm, "user_id": user["id"],
                "points": _points(conn, mid, is_dm),
                "modes": _modes(conn, cid)}


async def _read_image(file: UploadFile):
    """Lee la imagen subida y saca sus medidas del encabezado."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "La imagen está vacía")
    if len(data) > MAX_IMAGE:
        raise HTTPException(400, "La imagen pasa los %d MB" % (MAX_IMAGE // (1024 * 1024)))
    got = M.image_size(data)
    if not got:
        raise HTTPException(400, "No pude leer la imagen. Usá PNG, JPG, GIF o WEBP.")
    w, h, mime = got
    return data, w, h, mime


@router.post("/maps")
async def create_map(cid: int, file: UploadFile = File(...), name: str = Form(""),
                     descripcion: str = Form(""), ancho_real: float = Form(0),
                     secreto: bool = Form(False), user=Depends(current_user)):
    """El DM sube una lámina. `ancho_real` es cuánto mide de lado a lado; se
    puede dejar en 0 y calibrarla después con la regla."""
    data, w, h, mime = await _read_image(file)
    nombre = (name or "").strip() or (file.filename or "Mapa").rsplit(".", 1)[0]
    ancho = max(0.0, min(M.MAX_ANCHO, float(ancho_real or 0)))
    with db() as conn:
        cfg = _dm_access(conn, cid, user)
        primero = conn.execute("SELECT 1 FROM maps WHERE campaign_id=?", (cid,)).fetchone()
        cur = conn.execute(
            "INSERT INTO maps (campaign_id, name, descripcion, img_w, img_h, "
            "ancho_real, mime, secreto) VALUES (?,?,?,?,?,?,?,?)",
            (cid, nombre[:120], (descripcion or "")[:2000], w, h, ancho, mime,
             1 if secreto else 0))
        mid = cur.lastrowid
        conn.execute("INSERT INTO map_images (map_id, image) VALUES (?,?)", (mid, data))
        # Al estrenar el módulo la campaña arranca con transportes sugeridos:
        # es el primer mapa y todavía no hay ninguno cargado.
        if not primero and not conn.execute(
                "SELECT 1 FROM travel_modes WHERE campaign_id=?", (cid,)).fetchone():
            _seed_modes(conn, cid)
        r = conn.execute("SELECT * FROM maps WHERE id=?", (mid,)).fetchone()
        return {"ok": True, "map": _map_json(r, cfg["mapa_unidad"], 0)}


@router.put("/maps/{mid}")
def edit_map(cid: int, mid: int, m: MapIn, user=Depends(current_user)):
    """Renombrar, redescribir, cambiar la escala o esconder un mapa."""
    with db() as conn:
        cfg = _dm_access(conn, cid, user)
        r = _map_row(conn, cid, mid, True)
        nombre = (m.name or "").strip() or r["name"]
        desc = m.descripcion if m.descripcion is not None else r["descripcion"]
        ancho = (max(0.0, min(M.MAX_ANCHO, float(m.ancho_real)))
                 if m.ancho_real is not None else r["ancho_real"])
        secreto = r["secreto"] if m.secreto is None else (1 if m.secreto else 0)
        conn.execute(
            "UPDATE maps SET name=?, descripcion=?, ancho_real=?, secreto=? WHERE id=?",
            (nombre[:120], (desc or "")[:2000], ancho, secreto, mid))
        r = conn.execute("SELECT * FROM maps WHERE id=?", (mid,)).fetchone()
        return {"ok": True, "map": _map_json(r, cfg["mapa_unidad"])}


@router.post("/maps/{mid}/image")
async def replace_image(cid: int, mid: int, file: UploadFile = File(...),
                        user=Depends(current_user)):
    """Cambiar la lámina sin perder los puntos: como las coordenadas son
    relativas, siguen donde estaban aunque la imagen nueva mida otra cosa."""
    data, w, h, mime = await _read_image(file)
    with db() as conn:
        cfg = _dm_access(conn, cid, user)
        _map_row(conn, cid, mid, True)
        # La escala se conserva: el mapa sigue midiendo lo mismo en el mundo,
        # solo cambió la resolución de la lámina.
        conn.execute("UPDATE maps SET img_w=?, img_h=?, mime=? WHERE id=?",
                     (w, h, mime, mid))
        conn.execute(
            "INSERT INTO map_images (map_id, image) VALUES (?,?) "
            "ON CONFLICT(map_id) DO UPDATE SET image=excluded.image", (mid, data))
        r = conn.execute("SELECT * FROM maps WHERE id=?", (mid,)).fetchone()
        return {"ok": True, "map": _map_json(r, cfg["mapa_unidad"])}


@router.post("/maps/{mid}/calibrate")
def calibrate_map(cid: int, mid: int, c: MapCalibrate, user=Depends(current_user)):
    """Calibrar con la regla: dos puntos y cuánto mide entre ellos.

    Sirve cuando el mapa trae una escala gráfica, o cuando se sabe la distancia
    entre dos ciudades pero no cuánto mide la lámina entera."""
    if c.distancia <= 0:
        raise HTTPException(400, "La distancia de la regla tiene que ser mayor que 0")
    with db() as conn:
        cfg = _dm_access(conn, cid, user)
        r = _map_row(conn, cid, mid, True)
        ancho = M.ancho_por_regla(r["img_w"], r["img_h"],
                                  {"x": c.x1, "y": c.y1}, {"x": c.x2, "y": c.y2},
                                  c.distancia)
        if ancho <= 0:
            raise HTTPException(400, "La regla es demasiado corta: separá más los dos puntos")
        conn.execute("UPDATE maps SET ancho_real=? WHERE id=?", (ancho, mid))
        r = conn.execute("SELECT * FROM maps WHERE id=?", (mid,)).fetchone()
        return {"ok": True, "map": _map_json(r, cfg["mapa_unidad"])}


@router.delete("/maps/{mid}")
def delete_map(cid: int, mid: int, user=Depends(current_user)):
    with db() as conn:
        _dm_access(conn, cid, user)
        _map_row(conn, cid, mid, True)
        conn.execute("DELETE FROM maps WHERE id=?", (mid,))   # puntos e imagen en cascada
    return {"ok": True}


@router.get("/maps/{mid}/image")
def map_image(cid: int, mid: int, user=Depends(current_user)):
    """Sirve la lámina. Pasa por los mismos permisos que el resto del módulo."""
    with db() as conn:
        _, is_dm = _access(conn, cid, user)
        r = _map_row(conn, cid, mid, is_dm)
        row = conn.execute("SELECT image FROM map_images WHERE map_id=?", (mid,)).fetchone()
        if not row:
            raise HTTPException(404, "Ese mapa no tiene imagen")
        blob = bytes(row["image"])
        mime = r["mime"] or "image/png"
    return Response(content=blob, media_type=mime, headers={"Cache-Control": "no-cache"})


# ── Puntos de interés ──────────────────────────────────────

@router.post("/maps/{mid}/points")
def add_point(cid: int, mid: int, p: MapPointIn, user=Depends(current_user)):
    """Clava un punto. El DM siempre puede; los jugadores, si el DM los dejó."""
    with db() as conn:
        _, is_dm = _access(conn, cid, user, need_edit=True)
        _map_row(conn, cid, mid, is_dm)
        nombre = (p.name or "").strip()
        if not nombre:
            raise HTTPException(400, "Ponele un nombre al punto")
        cur = conn.execute(
            "INSERT INTO map_points (map_id, user_id, name, descripcion, x, y, "
            "icono, color, secreto) VALUES (?,?,?,?,?,?,?,?,?)",
            (mid, user["id"], nombre[:120], (p.descripcion or "")[:4000],
             M.clamp01(p.x), M.clamp01(p.y), (p.icono or "")[:8],
             (p.color or "")[:16], 1 if (p.secreto and is_dm) else 0))
        r = conn.execute("SELECT * FROM map_points WHERE id=?", (cur.lastrowid,)).fetchone()
        return {"ok": True, "point": _point_json(r),
                "points": _points(conn, mid, is_dm)}


def _own_point(conn, cid: int, mid: int, pid: int, user, is_dm: bool):
    """El punto, si existe en ese mapa y quien pide lo puede tocar: el suyo
    cada jugador, cualquiera el DM."""
    r = conn.execute(
        "SELECT p.* FROM map_points p JOIN maps m ON m.id=p.map_id "
        "WHERE p.id=? AND p.map_id=? AND m.campaign_id=?", (pid, mid, cid)).fetchone()
    if not r or (r["secreto"] and not is_dm):
        raise HTTPException(404, "Ese punto no existe")
    if not is_dm and r["user_id"] != user["id"]:
        raise HTTPException(403, "Ese punto no es tuyo")
    return r


@router.put("/maps/{mid}/points/{pid}")
def edit_point(cid: int, mid: int, pid: int, p: MapPointIn, user=Depends(current_user)):
    with db() as conn:
        _, is_dm = _access(conn, cid, user, need_edit=True)
        r = _own_point(conn, cid, mid, pid, user, is_dm)
        nombre = (p.name or "").strip() or r["name"]
        x = M.clamp01(p.x) if p.x is not None else r["x"]
        y = M.clamp01(p.y) if p.y is not None else r["y"]
        desc = p.descripcion if p.descripcion is not None else r["descripcion"]
        conn.execute(
            "UPDATE map_points SET name=?, descripcion=?, x=?, y=?, icono=?, "
            "color=?, secreto=? WHERE id=?",
            (nombre[:120], (desc or "")[:4000], x, y, (p.icono or "")[:8],
             (p.color or "")[:16], 1 if (p.secreto and is_dm) else 0, pid))
        return {"ok": True, "points": _points(conn, mid, is_dm)}


@router.delete("/maps/{mid}/points/{pid}")
def delete_point(cid: int, mid: int, pid: int, user=Depends(current_user)):
    with db() as conn:
        _, is_dm = _access(conn, cid, user, need_edit=True)
        _own_point(conn, cid, mid, pid, user, is_dm)
        conn.execute("DELETE FROM map_points WHERE id=?", (pid,))
        return {"ok": True, "points": _points(conn, mid, is_dm)}


# ── Medios de transporte ───────────────────────────────────

@router.get("/travel-modes")
def list_modes(cid: int, user=Depends(current_user)):
    with db() as conn:
        cfg, _ = _access(conn, cid, user)
        return {"unidad": cfg["mapa_unidad"], "modes": _modes(conn, cid)}


@router.post("/travel-modes")
def add_mode(cid: int, t: TravelModeIn, user=Depends(current_user)):
    """El DM suma un transporte y le pone la velocidad que quiera."""
    with db() as conn:
        cfg = _dm_access(conn, cid, user)
        nombre = (t.name or "").strip()
        if not nombre:
            raise HTTPException(400, "Ponele un nombre al transporte")
        orden = conn.execute(
            "SELECT COALESCE(MAX(orden), -1) + 1 n FROM travel_modes WHERE campaign_id=?",
            (cid,)).fetchone()["n"]
        conn.execute(
            "INSERT INTO travel_modes (campaign_id, name, icono, velocidad, "
            "horas_dia, notas, orden) VALUES (?,?,?,?,?,?,?)",
            (cid, nombre[:60], (t.icono or "")[:8],
             max(0.0, min(M.MAX_VEL, float(t.velocidad or 0))),
             max(0.0, min(M.MAX_HORAS_DIA,
                          float(t.horas_dia if t.horas_dia is not None else 8))),
             (t.notas or "")[:500], orden))
        return {"ok": True, "unidad": cfg["mapa_unidad"], "modes": _modes(conn, cid)}


@router.put("/travel-modes/{tid}")
def edit_mode(cid: int, tid: int, t: TravelModeIn, user=Depends(current_user)):
    with db() as conn:
        cfg = _dm_access(conn, cid, user)
        r = conn.execute("SELECT * FROM travel_modes WHERE id=? AND campaign_id=?",
                         (tid, cid)).fetchone()
        if not r:
            raise HTTPException(404, "Ese transporte no existe")
        conn.execute(
            "UPDATE travel_modes SET name=?, icono=?, velocidad=?, horas_dia=?, notas=? "
            "WHERE id=?",
            ((t.name or "").strip()[:60] or r["name"], (t.icono or "")[:8],
             max(0.0, min(M.MAX_VEL,
                          float(t.velocidad if t.velocidad is not None else r["velocidad"]))),
             max(0.0, min(M.MAX_HORAS_DIA,
                          float(t.horas_dia if t.horas_dia is not None else r["horas_dia"]))),
             (t.notas or "")[:500], tid))
        return {"ok": True, "unidad": cfg["mapa_unidad"], "modes": _modes(conn, cid)}


@router.delete("/travel-modes/{tid}")
def delete_mode(cid: int, tid: int, user=Depends(current_user)):
    with db() as conn:
        cfg = _dm_access(conn, cid, user)
        conn.execute("DELETE FROM travel_modes WHERE id=? AND campaign_id=?", (tid, cid))
        return {"ok": True, "unidad": cfg["mapa_unidad"], "modes": _modes(conn, cid)}


@router.post("/travel-modes/defaults")
def restore_modes(cid: int, user=Depends(current_user)):
    """Vuelve a cargar los transportes sugeridos (sin pisar los que ya están:
    se saltea los que tengan el mismo nombre)."""
    with db() as conn:
        cfg = _dm_access(conn, cid, user)
        ya = {r["name"].lower() for r in conn.execute(
            "SELECT name FROM travel_modes WHERE campaign_id=?", (cid,))}
        orden = conn.execute(
            "SELECT COALESCE(MAX(orden), -1) + 1 n FROM travel_modes WHERE campaign_id=?",
            (cid,)).fetchone()["n"]
        for m in M.DEFAULT_MODES:
            if m["name"].lower() in ya:
                continue
            conn.execute(
                "INSERT INTO travel_modes (campaign_id, name, icono, velocidad, "
                "horas_dia, notas, orden) VALUES (?,?,?,?,?,?,?)",
                (cid, m["name"], m["icono"], m["velocidad"], m["horas_dia"],
                 m["notas"], orden))
            orden += 1
        return {"ok": True, "unidad": cfg["mapa_unidad"], "modes": _modes(conn, cid)}


# ── Medir ──────────────────────────────────────────────────

@router.post("/maps/{mid}/measure")
def measure(cid: int, mid: int, q: MeasureIn, user=Depends(current_user)):
    """Mide un recorrido y dice cuánto se tarda con cada transporte.

    Las paradas pueden venir como ids de puntos del mapa (`point_ids`), como
    coordenadas sueltas (`puntos`) o mezcladas: primero los puntos guardados,
    en el orden en que los mandaron, y después las coordenadas."""
    with db() as conn:
        cfg, is_dm = _access(conn, cid, user)
        r = _map_row(conn, cid, mid, is_dm)
        if not r["ancho_real"]:
            raise HTTPException(400, "Este mapa todavía no tiene escala: decile "
                                     "cuánto mide de ancho o calibralo con la regla")
        paradas = []
        if q.point_ids:
            marcas = ",".join("?" for _ in q.point_ids)
            sql = "SELECT * FROM map_points WHERE map_id=? AND id IN (" + marcas + ")"
            if not is_dm:
                sql += " AND secreto=0"
            hallados = {row["id"]: row for row in conn.execute(sql, (mid, *q.point_ids))}
            for pid in q.point_ids:
                row = hallados.get(pid)
                if not row:
                    raise HTTPException(404, "Ese punto no está en este mapa")
                paradas.append({"x": row["x"], "y": row["y"], "name": row["name"]})
        paradas += [{"x": p.x, "y": p.y, "name": ""} for p in q.puntos]
        if len(paradas) < 2:
            raise HTTPException(400, "Hacen falta al menos dos paradas para medir")
        res = M.ruta(r["img_w"], r["img_h"], r["ancho_real"], paradas)
        # Cada tramo se etiqueta con las dos paradas que une, para listarlo.
        for i, tramo in enumerate(res["tramos"]):
            tramo["desde"] = paradas[i]["name"]
            tramo["hasta"] = paradas[i + 1]["name"]
        return {"ok": True, "unidad": cfg["mapa_unidad"],
                "distancia": res["distancia"], "tramos": res["tramos"],
                "paradas": len(paradas),
                "viajes": M.viajes(res["distancia"], _modes(conn, cid))}
