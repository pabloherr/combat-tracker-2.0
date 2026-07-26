"""Modelos Pydantic para las peticiones de la API."""

from pydantic import BaseModel


# ── Cuentas ────────────────────────────────────────────────

class RegisterIn(BaseModel):
    username: str
    email: str = ""
    password: str
    role: str = "dm"           # modo de la sesión: dm | player (no se cambia sin re-loguear)


class LoginIn(BaseModel):
    username: str
    password: str
    role: str = "dm"


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
    system: str = "cosmere"    # cosmere | dnd (se fija al crear la campaña)


class InviteIn(BaseModel):
    username: str


class LongRestIn(BaseModel):
    exclude: list[int] = []      # user_ids que NO reciben el descanso


class ConfigIn(BaseModel):
    # Parámetros ajustables por el DM. Todos opcionales: se aplica lo que venga.
    storm_min: int | None = None        # mínimo de días entre tormentas
    storm_max: int | None = None        # máximo de días entre tormentas
    discharge_start: int | None = None  # día desde el que los marcos empiezan a apagarse
    discharge_full: int | None = None   # día en que ya no queda luz
    discharge_curve: float | None = None  # exponente de la curva (1 = pareja)
    storm_day: int | None = None        # estado actual: día del ciclo
    storm_target: int | None = None     # estado actual: día en que cae la tormenta
    storm_moment: str | None = None     # estado actual: momento del día


# ── Personajes ─────────────────────────────────────────────

class CharacterIn(BaseModel):
    name: str
    campaign_id: int | None = None   # requerido al crear: el PJ pertenece a una campaña
    vida_max: int = 20
    focus_max: int = 10
    inv_max: int = 0
    sheet: dict = {}
    # Valores actuales (opcionales): al editar a mano el jugador puede ajustarlos.
    # En creación se ignoran (arrancan en el máximo).
    vida: int | None = None
    focus: int | None = None
    inv: int | None = None


class PetFromEnemy(BaseModel):
    enemy_id: int              # enemigo del bestiario habilitado como mascota por el DM


class LiveStat(BaseModel):
    stat: str                  # vida | focus | inv
    delta: int


class LiveStatus(BaseModel):
    status: str


class InjuryIn(BaseModel):
    name: str
    days: int = 0            # días restantes (0 = se cura en el próximo descanso largo)
    permanent: bool = False


class DaysChange(BaseModel):
    delta: int


class MarcosChange(BaseModel):
    delta: int              # +/- marcos (total) o carga/descarga de luz, según el endpoint


class MarcosSet(BaseModel):
    cargados: int = 0       # marcos con luz
    opacos: int = 0         # marcos sin luz (total = cargados + opacos)


# ── Recursos D&D (spell slots y contadores) ────────────────

class SlotsConfigIn(BaseModel):
    levels: dict[str, int] = {}   # "1".."9" -> máximo de slots (0/ausente = quitar nivel)


class SlotSpend(BaseModel):
    level: int                 # 1..9
    delta: int                 # -1 gastar / +1 recuperar


class CounterIn(BaseModel):
    name: str
    max: int = 1
    recovery: str = "long"     # long | short | none (cuándo se recupera)


class CounterValue(BaseModel):
    delta: int


# ── Objetos, comercio e inventario ─────────────────────────

class ItemIn(BaseModel):
    name: str
    kind: str = "equipo"         # arma | armadura | equipo | alojamiento | vehiculo | fabrial
    descripcion: str = ""
    categorias: list[str] = []   # subcategorías: medicina, herramientas, comida…
    precio: int = 0
    peso: str = ""               # peso del manual (informativo)
    slots: int = 1
    capacity_bonus: int = 0      # suma capacidad al que lo lleva
    usos_max: int = 0            # dosis/cargas por unidad (5 raciones = 1 slot)
    contenedor_capacidad: int = 0  # >0 => es contenedor (mochila, carreta…)
    stats: dict = {}             # datos propios del tipo (daño, deflect, cargas…)
    notas: str = ""
    secreto: bool = False        # no aparece en el catálogo de los jugadores


class ItemImportIn(BaseModel):
    code: str                    # YAML de uno o varios objetos


class InventoryIn(BaseModel):
    """Alta de un objeto en el inventario.

    El DM puede dar uno del catálogo (`item_id`) o crearlo al vuelo; el jugador
    con permiso solo puede crearlo al vuelo."""
    item_id: int | None = None
    name: str = ""
    kind: str = "equipo"
    descripcion: str = ""
    categorias: list[str] = []
    peso: str = ""
    slots: int = 1
    capacity_bonus: int = 0
    usos_max: int = 0
    contenedor_capacidad: int = 0
    stats: dict = {}
    cantidad: int = 1
    notas: str = ""
    parent_id: int | None = None    # dentro de qué contenedor entra
    save_to_catalog: bool = False   # además, guardarlo en el catálogo del DM


class InventoryQty(BaseModel):
    delta: int


class InventoryMove(BaseModel):
    parent_id: int | None = None    # null = sacarlo del contenedor


class SizeIn(BaseModel):
    size: str                    # Pequeño | Mediano | Grande | Enorme | Gargantuesco


class ShopIn(BaseModel):
    name: str
    preset: str = "general"
    size: str = "mediana"           # pequeña | mediana | grande
    price_policy: str = "normal"    # barato | normal | caro | variado
    descripcion: str = ""
    settlement_id: int | None = None


class SettlementIn(BaseModel):
    name: str
    size: str = "pueblo"            # aldea | pueblo | ciudad
    descripcion: str = ""


class ShopItemIn(BaseModel):
    item_id: int
    cantidad: int = 1
    precio: int | None = None       # si no viene, se calcula con la política
    visible: bool = True


class PriceIn(BaseModel):
    precio: int


class PurchaseIn(BaseModel):
    shop_item_id: int
    cantidad: int = 1


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
    # [{enemy_id, cantidad, overrides}] — overrides ajusta al enemigo solo en este
    # encuentro (name, clase, vida_max, focus_max, inv_max, faction_color).
    enemies: list[dict] = []


class AddEnemyIn(BaseModel):
    enemy_id: int             # enemigo del bestiario a sumar al combate en curso
    cantidad: int = 1


class StatChange(BaseModel):
    uid: str
    stat: str                  # vida | focus | inv
    delta: int


class VidaMaxIn(BaseModel):
    uid: str
    value: int                 # nueva vida máxima en este combate (la actual se recorta)


class StatusToggle(BaseModel):
    uid: str
    status: str


class TurnChange(BaseModel):
    uid: str
    turn: str                  # fast | slow


class ColorChange(BaseModel):
    uid: str
    color: str                 # color de esta instancia en combate (hex)


class InitiativeIn(BaseModel):
    uid: str
    value: int                 # iniciativa del participante (D&D)
