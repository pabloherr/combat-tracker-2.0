"""Modelos Pydantic para las peticiones de la API."""

from pydantic import BaseModel


# ── Cuentas ────────────────────────────────────────────────

class RegisterIn(BaseModel):
    username: str
    email: str = ""
    password: str


class LoginIn(BaseModel):
    username: str
    password: str


class ResetIn(BaseModel):
    username: str
    email: str
    password: str


class AccountUpdate(BaseModel):
    username: str
    email: str
    current_password: str = ""
    new_password: str = ""


class DeleteAccount(BaseModel):
    password: str = ""       # contraseña actual, para confirmar el borrado


# ── Campañas ───────────────────────────────────────────────

class CampaignIn(BaseModel):
    name: str


class InviteIn(BaseModel):
    username: str


class AcceptIn(BaseModel):
    character_id: int


# ── Personajes ─────────────────────────────────────────────

class CharacterIn(BaseModel):
    name: str
    vida_max: int = 20
    focus_max: int = 10
    inv_max: int = 0
    sheet: dict = {}
    # Valores actuales (opcionales): al editar a mano el jugador puede ajustarlos.
    # En creación se ignoran (arrancan en el máximo).
    vida: int | None = None
    focus: int | None = None
    inv: int | None = None


class PetImportIn(BaseModel):
    code: str                  # statblock YAML (mismo formato que los enemigos)


class LiveStat(BaseModel):
    stat: str                  # vida | focus | inv
    delta: int


class LiveStatus(BaseModel):
    status: str


class AccionIn(BaseModel):
    nombre: str
    coste: str = ""          # ej: "1 acción", "2 focus"
    descripcion: str = ""


class EnemyIn(BaseModel):
    name: str
    tipo: str = ""
    clase: str = "rival"       # minion | rival | boss
    vida_max: int = 20
    focus_max: int = 10
    inv_max: int = 0
    acciones: list[AccionIn] = []
    notas: str = ""
    faction_color: str = ""
    stats: dict = {}           # ficha completa (atributos, defensas, rasgos, etc.)


class EnemyImportIn(BaseModel):
    code: str                  # bloque YAML del statblock a parsear


class EncounterIn(BaseModel):
    name: str
    descripcion: str = ""
    enemies: list[dict] = []   # [{enemy_id, cantidad}]


class AddEnemyIn(BaseModel):
    enemy_id: int             # enemigo del bestiario a sumar al combate en curso
    cantidad: int = 1


class StatChange(BaseModel):
    uid: str
    stat: str                  # vida | focus | inv
    delta: int


class StatusToggle(BaseModel):
    uid: str
    status: str


class TurnChange(BaseModel):
    uid: str
    turn: str                  # fast | slow


class ColorChange(BaseModel):
    uid: str
    color: str                 # color de esta instancia en combate (hex)
