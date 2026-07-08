"""
Estado del combate por campaña: en memoria (cache) y persistido en SQLite.

Cada campaña tiene su propio combate. Se carga perezosamente la primera vez
que se accede y se guarda en la tabla `combats`.
"""

import json

from .database import db

EMPTY_COMBAT = {"active": False, "round": 1, "phase": "fast_players", "participants": []}


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
