"""
Estado del combate por campaña: en memoria (cache) y persistido en SQLite.

Cada campaña tiene su propio combate. Se carga perezosamente la primera vez
que se accede y se guarda en la tabla `combats`.
"""

import json

from .config import CONFIG_DEFAULTS
from .database import db

EMPTY_COMBAT = {"active": False, "round": 1, "phase": "fast_players", "participants": []}

_STATS = ("vida", "focus", "inv")


def _pct(valor, maximo) -> int:
    if not maximo:
        return 0
    # se redondea a múltiplos de 5: la barra se ve igual y no se puede deducir
    # el número exacto contando píxeles
    return max(0, min(100, round(valor / maximo * 100 / 5) * 5))


def _nivel(valor, maximo) -> int:
    """Tramo en el que está el stat, de 5 (intacto) a 0 (vacío). Es lo único que
    se manda en modo 'color': ni siquiera un porcentaje."""
    if not maximo:
        return 0
    pct = (valor or 0) / maximo * 100
    for corte, n in ((100, 5), (75, 4), (50, 3), (25, 2)):
        if pct >= corte:
            return n
    return 1 if pct > 0 else 0


def mask_stats(p: dict, cfg: dict, grupo: str) -> dict:
    """Recorta un participante según lo que el DM deje ver de ese grupo.

    Lo que no se ve no se manda: 'color' viaja como un tramo (0-5), 'abstracto'
    como porcentaje redondeado, y en 'no' no viaja nada."""
    d = dict(p)
    for stat in _STATS:
        modo = cfg.get(f"ver_{stat}_{grupo}", "color")
        if modo == "exacto":
            continue
        maximo = d.get(f"{stat}_max") or 0
        actual = d.get(stat)
        d[stat] = None
        d[f"{stat}_max"] = None
        if maximo and modo == "abstracto":
            d[f"{stat}_pct"] = _pct(actual or 0, maximo)
        elif maximo and modo == "color":
            d[f"{stat}_nivel"] = _nivel(actual, maximo)
    if not cfg.get(f"ver_estados_{grupo}", True):
        d["statuses"] = []
        d["injuries"] = []
    return d


def player_view(combat: dict, cfg: dict = None, user_id: int = None) -> dict:
    """Vista del combate para **un** jugador.

    - Mientras está en preparación (`staged`) o no hay combate activo, los
      jugadores no ven nada (pantalla de espera).
    - Se quitan los enemigos marcados como ocultos.
    - De los demás se manda solo lo que el DM habilitó (ver `app/config.py`).
      Lo propio siempre va completo: es tu personaje.
    """
    if not combat.get("active") or combat.get("staged"):
        return {
            "active": False,
            "round": combat.get("round", 1),
            "phase": combat.get("phase", "fast_players"),
            "encounter_name": "",
            "participants": [],
        }
    cfg = cfg or CONFIG_DEFAULTS
    parts = []
    for p in combat.get("participants", []):
        if p.get("kind") == "enemy":
            if p.get("hidden"):
                continue
            parts.append(mask_stats(p, cfg, "enemigos"))
        elif user_id is not None and p.get("user_id") == user_id:
            parts.append(p)                      # tu personaje y tus mascotas, enteros
        elif p.get("kind") == "pet" and p.get("shared"):
            parts.append(p)                      # la mascota de todos, también
        else:
            parts.append(mask_stats(p, cfg, "aliados"))
    return {**combat, "participants": parts}


class CampaignCombats:
    def __init__(self):
        self._cache: dict[int, dict] = {}

    def get(self, cid: int) -> dict:
        if cid not in self._cache:
            with db() as conn:
                row = conn.execute("SELECT data FROM combats WHERE campaign_id=?", (cid,)).fetchone()
            self._cache[cid] = json.loads(row["data"]) if row else dict(EMPTY_COMBAT)
        return self._cache[cid]

    def set(self, cid: int, data: dict):
        self._cache[cid] = data

    def reset(self, cid: int):
        self._cache[cid] = dict(EMPTY_COMBAT)

    def save(self, cid: int):
        data = self.get(cid)
        with db() as conn:
            conn.execute(
                "INSERT INTO combats (campaign_id, data) VALUES (?,?) "
                "ON CONFLICT(campaign_id) DO UPDATE SET data=excluded.data",
                (cid, json.dumps(data)),
            )


combats = CampaignCombats()
