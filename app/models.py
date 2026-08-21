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


class ForgotIn(BaseModel):
    """Pedido de recuperación: alcanza con el usuario o el email."""
    identifier: str = ""


class ResetIn(BaseModel):
    """Contraseña nueva. Se identifica con el token del enlace del correo o,
    si el enlace no sirve, con usuario/email + el código de seis dígitos."""
    token: str = ""
    identifier: str = ""
    code: str = ""
    password: str


class ConfirmChangeIn(BaseModel):
    """Confirmación del cambio de contraseña pedido desde "Mi cuenta"."""
    token: str = ""
    code: str = ""


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


class DaysIn(BaseModel):
    days: int = 1                # cuántos días pasan de una (1 = el botón de siempre)


class CalendarSet(BaseModel):
    """Fijar el día en que están los jugadores (DM).

    Se puede mandar la fecha desarmada o el índice absoluto; si vienen las dos,
    manda el índice."""
    year: int | None = None
    month: int | None = None
    week: int | None = None
    day: int | None = None
    day_index: int | None = None


class CalendarNoteIn(BaseModel):
    day_index: int | None = None   # día al que se pega la nota (None = hoy)
    texto: str = ""
    color: str = ""                # color del pin (hex); vacío = el de por defecto
    secreto: bool = False          # solo del DM: los jugadores no la ven


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
    # Módulos que se pueden apagar en la campaña
    modulo_catalogo: bool | None = None     # el catálogo de objetos
    modulo_inventario: bool | None = None   # inventario, guardados y carga
    modulo_tormentas: bool | None = None    # tracker de altas tormentas
    modulo_calendario: bool | None = None   # calendario rosharano
    # Calendario: quién lo ve, quién lo anota y cuántos días salta el botón
    calendario_visible: bool | None = None
    calendario_editable: bool | None = None
    salto_dias: int | None = None
    # Estado actual del calendario (opcional, como el de la tormenta)
    cal_year: int | None = None
    cal_month: int | None = None
    cal_week: int | None = None
    cal_day: int | None = None
    # Capacidad de carga: base por tamaño (después se le suma la Fuerza)
    carga_pequeno: int | None = None
    carga_mediano: int | None = None
    carga_grande: int | None = None
    carga_enorme: int | None = None
    carga_gargantuesco: int | None = None
    # Qué ven los jugadores de los demás en combate: no | abstracto | exacto
    ver_vida_enemigos: str | None = None
    ver_focus_enemigos: str | None = None
    ver_inv_enemigos: str | None = None
    ver_estados_enemigos: bool | None = None
    ver_vida_aliados: str | None = None
    ver_focus_aliados: str | None = None
    ver_inv_aliados: str | None = None
    ver_estados_aliados: bool | None = None


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


class PetName(BaseModel):
    name: str                  # la mascota es tuya: ponele el nombre que quieras


class PetSheet(BaseModel):
    """Ficha completa de la mascota, editada por quien la trajo.

    Entra como copia del bestiario, pero el axehound de la mesa evoluciona: el
    jugador le cambia los números, los rasgos y las acciones sin tocar al
    enemigo original ni a las mascotas de los demás."""
    name: str
    vida_max: int = 10
    focus_max: int = 0
    inv_max: int = 0
    acciones: list = []
    stats: dict = {}
    # Valores actuales (opcionales). Si no vienen, se recortan al nuevo máximo.
    vida: int | None = None
    focus: int | None = None
    inv: int | None = None


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


class InventoryStash(BaseModel):
    """Mover un objeto entre zonas: encima, el guardado propio o el del grupo."""
    stash: str = ""                 # "" (encima) | personal | grupo


class InventoryTransfer(BaseModel):
    """Pasarle un objeto a otro personaje (o a una mascota) de la campaña."""
    character_id: int | None = None
    pet_id: int | None = None
    stash: str = ""                 # zona en la que cae, en el destino


class TakeIn(BaseModel):
    """El jugador agarra un objeto del catálogo y lo paga como quiera.

    `precio` es editable: el DM puede hacerle un descuento, o el objeto puede
    ser un hallazgo (precio 0). El reparto de esferas también lo elige él; si no
    manda ninguno, se cobra con opacas primero."""
    item_id: int
    cantidad: int = 1
    precio: int | None = None       # por unidad; None = el del catálogo
    pago_cargados: int | None = None
    pago_opacos: int | None = None
    pet_id: int | None = None       # destino: una mascota en vez del personaje
    parent_id: int | None = None    # destino: dentro de un contenedor
    stash: str = ""                 # destino: encima, guardado propio o del grupo


class SizeIn(BaseModel):
    size: str                    # Pequeño | Mediano | Grande | Enorme | Gargantuesco


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


# ── Panel de administración ────────────────────────────────

class AdminUserUpdate(BaseModel):
    """Edición de una cuenta desde el panel. Solo se aplica lo que venga."""
    username: str | None = None
    email: str | None = None
    blocked: bool | None = None
    is_admin: bool | None = None


class MailTestIn(BaseModel):
    to: str = ""            # vacío = a la casilla de la app
