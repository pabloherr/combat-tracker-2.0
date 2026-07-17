"""API: Combate, por campaña."""

import json
import random
import uuid

from fastapi import APIRouter, Depends, HTTPException

from ..access import campaign_or_404, require_access, require_dm
from ..auth import current_user
from ..database import db
from ..models import (AddEnemyIn, ColorChange, InitiativeIn, StatChange,
                      StatusToggle, TurnChange, VidaMaxIn)
from ..state import combats, player_view
from ..ws import push_state

router = APIRouter(prefix="/api/campaigns/{cid}/combat", tags=["combat"])


def _roll_enemy_turn(clase: str) -> str:
    return "both" if clase == "boss" else random.choice(["fast", "slow"])


def _dex_mod(stats: dict) -> int:
    """Modificador de DEX de un statblock de D&D (0 si no tiene)."""
    try:
        dex = (stats or {}).get("abilities", {}).get("DEX")
        return (int(dex) - 10) // 2 if dex is not None else 0
    except (TypeError, ValueError):
        return 0


def _roll_initiative(stats: dict) -> int:
    return random.randint(1, 20) + _dex_mod(stats)


def _mk_participant(kind, name, vida, focus, inv, acciones=None, notas="", faction_color="",
                    tipo="", stats=None, clase="rival", cur_vida=None, cur_focus=None,
                    cur_inv=None, statuses=None, char_id=None, user_id=None, pet_id=None,
                    owner_name="", has_pdf=False, system="cosmere"):
    # Enemigos y mascotas toman turno al azar; los jugadores lo eligen.
    turn = _roll_enemy_turn(clase) if kind in ("enemy", "pet") else "slow"
    # D&D: los enemigos y mascotas tiran iniciativa solos (d20 + mod de DEX);
    # los jugadores anotan la suya al arrancar el combate.
    initiative = None
    if system == "dnd" and kind in ("enemy", "pet"):
        initiative = _roll_initiative(stats)
    cur_vida = vida if cur_vida is None else cur_vida
    cur_focus = focus if cur_focus is None else cur_focus
    cur_inv = inv if cur_inv is None else cur_inv
    return {
        "uid": uuid.uuid4().hex[:8],
        "kind": kind,                # 'player' | 'enemy' | 'pet'
        "char_id": char_id,          # personaje (jugadores)
        "pet_id": pet_id,            # mascota
        "user_id": user_id,          # dueño (jugadores y mascotas)
        "owner_name": owner_name,    # nombre del PJ dueño de la mascota
        "has_pdf": has_pdf,          # el personaje tiene PDF cargado
        "clase": clase if kind == "enemy" else "",
        "name": name,
        "tipo": tipo,
        "vida": cur_vida, "vida_max": vida,
        "focus": cur_focus, "focus_max": focus,
        "inv": cur_inv, "inv_max": inv,
        "statuses": statuses or [],
        "turn": turn,
        "initiative": initiative,
        "acted": False,
        "acted_slow": False,
        "hidden": False,
        "defeated": cur_vida == 0,
        "acciones": acciones or [],
        "notas": notas,
        "faction_color": faction_color,
        "stats": stats or {},
    }


def _persist_participant(p: dict):
    """Guarda el estado vivo de un personaje o mascota en su tabla."""
    if p.get("kind") == "player" and p.get("char_id"):
        with db() as conn:
            conn.execute(
                "UPDATE characters SET vida=?, focus=?, inv=?, statuses=? WHERE id=?",
                (p["vida"], p["focus"], p["inv"], json.dumps(p["statuses"]), p["char_id"]),
            )
    elif p.get("kind") == "pet" and p.get("pet_id"):
        with db() as conn:
            conn.execute(
                "UPDATE pets SET vida=?, focus=?, inv=?, statuses=? WHERE id=?",
                (p["vida"], p["focus"], p["inv"], json.dumps(p["statuses"]), p["pet_id"]),
            )


def _find(cid: int, uid: str):
    for p in combats.get(cid)["participants"]:
        if p["uid"] == uid:
            return p
    return None


def _guard_participant(is_dm: bool, p: dict, user: dict):
    """El DM puede tocar a cualquiera; un jugador, solo su personaje o sus mascotas."""
    if is_dm:
        return
    if p.get("kind") in ("player", "pet") and p.get("user_id") == user["id"]:
        return
    raise HTTPException(403, "Solo podés modificar tu propio personaje o tus mascotas")


def _build_participants(conn, cid: int, encounter_id: int, system: str = "cosmere"):
    """Arma la lista de participantes: personajes aceptados (+ mascotas) + enemigos."""
    participants = []

    # Jugadores: personaje que trajo cada miembro aceptado.
    chars = conn.execute(
        "SELECT m.user_id, ch.* FROM campaign_members m "
        "JOIN characters ch ON ch.id = m.character_id "
        "WHERE m.campaign_id=? AND m.status='accepted' AND m.character_id IS NOT NULL "
        "ORDER BY ch.name",
        (cid,),
    ).fetchall()
    for ch in chars:
        participants.append(_mk_participant(
            "player", ch["name"], ch["vida_max"], ch["focus_max"], ch["inv_max"],
            cur_vida=ch["vida"], cur_focus=ch["focus"], cur_inv=ch["inv"],
            statuses=json.loads(ch["statuses"] or "[]"),
            stats=json.loads(ch["sheet"] or "{}"),
            char_id=ch["id"], user_id=ch["user_id"], has_pdf=bool(ch["has_pdf"]),
            system=system,
        ))
        # Mascotas del personaje: entran como aliados que el jugador controla.
        for pet in conn.execute("SELECT * FROM pets WHERE character_id=? ORDER BY name", (ch["id"],)):
            participants.append(_mk_participant(
                "pet", pet["name"], pet["vida_max"], pet["focus_max"], pet["inv_max"],
                acciones=json.loads(pet["acciones"] or "[]"),
                cur_vida=pet["vida"], cur_focus=pet["focus"], cur_inv=pet["inv"],
                statuses=json.loads(pet["statuses"] or "[]"),
                stats=json.loads(pet["stats"] or "{}"),
                user_id=ch["user_id"], pet_id=pet["id"], owner_name=ch["name"],
                system=system,
            ))

    # Enemigos del encuentro. Los overrides ajustan al enemigo solo en este
    # encuentro (el bestiario queda intacto).
    rows = conn.execute(
        "SELECT ee.cantidad, ee.overrides, e.* FROM encounter_enemies ee "
        "JOIN enemies e ON e.id = ee.enemy_id WHERE ee.encounter_id=?",
        (encounter_id,),
    ).fetchall()
    for r in rows:
        acciones = json.loads(r["acciones"])
        stats = json.loads(r["stats"] or "{}")
        try:
            ov = json.loads(r["overrides"] or "{}")
        except (ValueError, TypeError):
            ov = {}
        if not isinstance(ov, dict):
            ov = {}

        def _f(key):
            return ov[key] if ov.get(key) not in (None, "") else r[key]

        base_name = _f("name")
        for i in range(1, r["cantidad"] + 1):
            nm = base_name if r["cantidad"] == 1 else f"{base_name} {i}"
            participants.append(_mk_participant(
                "enemy", nm, _f("vida_max"), _f("focus_max"), _f("inv_max"),
                acciones=acciones, notas=r["notas"],
                faction_color=_f("faction_color"), tipo=r["tipo"], stats=stats,
                clase=_f("clase"), system=system,
            ))
    return participants


def _new_combat(conn, cid: int, encounter_id: int, staged: bool):
    enc = conn.execute(
        "SELECT * FROM encounters WHERE id=? AND campaign_id=?", (encounter_id, cid)
    ).fetchone()
    if not enc:
        raise HTTPException(404, "Encuentro no encontrado")
    system = (campaign_or_404(conn, cid)["system"]) or "cosmere"
    return {
        "active": True,
        "staged": staged,   # en preparación: el DM lo ve, los jugadores no
        "round": 1,
        "phase": "fast_players",
        "system": system,   # dnd: orden por iniciativa en vez de fases
        "encounter_name": enc["name"],
        "participants": _build_participants(conn, cid, encounter_id, system),
    }


@router.post("/stage/{encounter_id}")
async def stage_combat(cid: int, encounter_id: int, user=Depends(current_user)):
    """Prepara el combate en modo 'staging': el DM puede examinarlo y editarlo
    (colores, agregar/quitar enemigos, visibilidad) antes de enviarlo a los jugadores."""
    with db() as conn:
        require_dm(conn, cid, user)
        combat = _new_combat(conn, cid, encounter_id, staged=True)
    combats.set(cid, combat)
    await push_state(cid)
    return combats.get(cid)


@router.post("/start/{encounter_id}")
async def start_combat(cid: int, encounter_id: int, user=Depends(current_user)):
    """Inicia combate directo (sin staging): visible a los jugadores de una."""
    with db() as conn:
        require_dm(conn, cid, user)
        combat = _new_combat(conn, cid, encounter_id, staged=False)
    combats.set(cid, combat)
    await push_state(cid)
    return combats.get(cid)


@router.post("/reveal")
async def reveal_combat(cid: int, user=Depends(current_user)):
    """Envía el combate preparado a los jugadores (sale del modo staging)."""
    with db() as conn:
        require_dm(conn, cid, user)
    combat = combats.get(cid)
    if not combat.get("active"):
        raise HTTPException(400, "No hay un combate preparado")
    combat["staged"] = False
    await push_state(cid)
    return {"ok": True}


@router.post("/remove/{uid}")
async def remove_participant(cid: int, uid: str, user=Depends(current_user)):
    """Saca un enemigo (o mascota) del combate. No se puede sacar a un jugador."""
    with db() as conn:
        require_dm(conn, cid, user)
    combat = combats.get(cid)
    p = _find(cid, uid)
    if not p:
        raise HTTPException(404, "Participante no encontrado")
    if p.get("kind") == "player":
        raise HTTPException(400, "No podés sacar a un jugador del combate")
    combat["participants"] = [x for x in combat["participants"] if x["uid"] != uid]
    await push_state(cid)
    return {"ok": True}


@router.post("/add_enemy")
async def add_enemy(cid: int, payload: AddEnemyIn, user=Depends(current_user)):
    """El DM suma enemigos del bestiario a un combate en curso."""
    with db() as conn:
        require_dm(conn, cid, user)
        e = conn.execute(
            "SELECT * FROM enemies WHERE id=? AND owner_id=?", (payload.enemy_id, user["id"])
        ).fetchone()
        if not e:
            raise HTTPException(404, "Enemigo no encontrado")

    combat = combats.get(cid)
    if not combat.get("active"):
        raise HTTPException(400, "No hay un combate activo")

    acciones = json.loads(e["acciones"])
    stats = json.loads(e["stats"] or "{}")
    parts = combat["participants"]
    base = e["name"]
    # Continúa la numeración a partir de las copias de este enemigo ya presentes.
    existing = sum(
        1 for p in parts
        if p.get("kind") == "enemy" and (p["name"] == base or p["name"].startswith(base + " "))
    )
    cantidad = max(1, payload.cantidad)
    for i in range(cantidad):
        idx = existing + i + 1
        nm = base if (cantidad == 1 and existing == 0) else f"{base} {idx}"
        parts.append(_mk_participant(
            "enemy", nm, e["vida_max"], e["focus_max"], e["inv_max"],
            acciones=acciones, notas=e["notas"], faction_color=e["faction_color"],
            tipo=e["tipo"], stats=stats, clase=e["clase"],
            system=combat.get("system") or "cosmere",
        ))
    await push_state(cid)
    return {"ok": True, "added": cantidad}


@router.get("")
def get_combat(cid: int, user=Depends(current_user)):
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
    combat = combats.get(cid)
    return combat if is_dm else player_view(combat)


@router.post("/stat")
async def change_stat(cid: int, c: StatChange, user=Depends(current_user)):
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
    p = _find(cid, c.uid)
    if not p:
        raise HTTPException(404, "Participante no encontrado")
    _guard_participant(is_dm, p, user)
    mx = p[f"{c.stat}_max"]
    old = p[c.stat]
    p[c.stat] = max(0, min(mx, old + c.delta))
    if c.stat == "vida":
        p["defeated"] = p["vida"] == 0
    _persist_participant(p)
    # Cargar investidura apaga marcos cargados 1:1 (solo personajes de jugador).
    if c.stat == "inv" and p.get("char_id"):
        gained = p["inv"] - old
        if gained > 0:
            with db() as conn:
                row = conn.execute("SELECT marcos_light FROM characters WHERE id=?",
                                   (p["char_id"],)).fetchone()
                light = (row["marcos_light"] if row else 0) or 0
                if light > 0:
                    conn.execute("UPDATE characters SET marcos_light=? WHERE id=?",
                                 (max(0, light - gained), p["char_id"]))
    await push_state(cid)
    return {"ok": True}


@router.post("/vida_max")
async def change_vida_max(cid: int, v: VidaMaxIn, user=Depends(current_user)):
    """El DM ajusta la vida máxima de un participante en este combate.
    La vida actual se recorta si supera el nuevo máximo. No toca el bestiario."""
    with db() as conn:
        require_dm(conn, cid, user)
    p = _find(cid, v.uid)
    if not p:
        raise HTTPException(404, "Participante no encontrado")
    mx = max(1, v.value)
    p["vida_max"] = mx
    p["vida"] = min(p["vida"], mx)
    p["defeated"] = p["vida"] == 0
    _persist_participant(p)
    await push_state(cid)
    return {"ok": True, "vida": p["vida"], "vida_max": mx}


@router.post("/status")
async def toggle_status(cid: int, t: StatusToggle, user=Depends(current_user)):
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
    p = _find(cid, t.uid)
    if not p:
        raise HTTPException(404, "Participante no encontrado")
    _guard_participant(is_dm, p, user)
    if t.status == "Exhausted":
        p["statuses"].append(t.status)
    elif t.status in p["statuses"]:
        p["statuses"].remove(t.status)
    else:
        p["statuses"].append(t.status)
    _persist_participant(p)
    await push_state(cid)
    return {"ok": True}


@router.post("/status/remove_one")
async def remove_one_status(cid: int, t: StatusToggle, user=Depends(current_user)):
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
    p = _find(cid, t.uid)
    if not p or t.status not in p["statuses"]:
        raise HTTPException(404, "No encontrado")
    _guard_participant(is_dm, p, user)
    p["statuses"].remove(t.status)
    _persist_participant(p)
    await push_state(cid)
    return {"ok": True}


@router.post("/turn")
async def set_turn(cid: int, t: TurnChange, user=Depends(current_user)):
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
    p = _find(cid, t.uid)
    if not p:
        raise HTTPException(404, "Participante no encontrado")
    # El jugador solo elige el turno de su PERSONAJE; el de las mascotas es al azar (solo DM).
    if not is_dm and not (p.get("kind") == "player" and p.get("user_id") == user["id"]):
        raise HTTPException(403, "No podés cambiar este turno")
    p["turn"] = t.turn
    await push_state(cid)
    return {"ok": True}


@router.post("/initiative")
async def set_initiative(cid: int, t: InitiativeIn, user=Depends(current_user)):
    """Anota la iniciativa de un participante (D&D). El jugador la de su
    personaje o mascotas; el DM la de cualquiera."""
    with db() as conn:
        _, is_dm = require_access(conn, cid, user)
    p = _find(cid, t.uid)
    if not p:
        raise HTTPException(404, "Participante no encontrado")
    _guard_participant(is_dm, p, user)
    p["initiative"] = max(-10, min(99, t.value))
    await push_state(cid)
    return {"ok": True, "initiative": p["initiative"]}


@router.post("/color")
async def set_color(cid: int, c: ColorChange, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
    p = _find(cid, c.uid)
    if not p:
        raise HTTPException(404, "Participante no encontrado")
    p["faction_color"] = c.color
    await push_state(cid)
    return {"ok": True}


@router.post("/acted/{uid}")
async def toggle_acted(cid: int, uid: str, slow: bool = False, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
    p = _find(cid, uid)
    if not p:
        raise HTTPException(404, "Participante no encontrado")
    key = "acted_slow" if slow else "acted"
    p[key] = not p.get(key, False)
    await push_state(cid)
    return {"ok": True}


@router.post("/hidden/{uid}")
async def toggle_hidden(cid: int, uid: str, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
    p = _find(cid, uid)
    if not p:
        raise HTTPException(404, "Participante no encontrado")
    p["hidden"] = not p.get("hidden", False)
    await push_state(cid)
    return {"ok": True}


@router.post("/next_round")
async def next_round(cid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
    combat = combats.get(cid)
    combat["round"] += 1
    is_dnd = (combat.get("system") or "cosmere") == "dnd"
    for p in combat["participants"]:
        p["acted"] = False
        p["acted_slow"] = False
        # D&D: la iniciativa se mantiene todo el combate; en Cosmere los
        # enemigos vuelven a sortear su turno cada ronda.
        if not is_dnd and p["kind"] in ("enemy", "pet"):
            p["turn"] = _roll_enemy_turn(p.get("clase") or "rival")
    await push_state(cid)
    return {"ok": True}


@router.post("/end")
async def end_combat(cid: int, user=Depends(current_user)):
    with db() as conn:
        require_dm(conn, cid, user)
    combats.reset(cid)
    await push_state(cid)
    return {"ok": True}
