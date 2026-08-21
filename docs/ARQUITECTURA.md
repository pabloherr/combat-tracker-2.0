# Arquitectura y base de datos

Documento de referencia del **Cosmere / D&D Combat Tracker**: cómo está armado el
proyecto y cómo es su base de datos.

> Para instalar y usar la app, ver el [README](../README.md). Este doc es para
> entender el código.

---

## 1. Visión general

App web para llevar el combate de una mesa de rol (Cosmere RPG y D&D 5e) en tiempo real,
pensada para correr en una LAN casera.

- **Backend**: Python + [FastAPI](https://fastapi.tiangolo.com/), servido con `uvicorn`.
- **Base de datos**: SQLite con la librería estándar `sqlite3` (sin ORM). Un solo
  archivo, `cosmere.db`.
- **Tiempo real**: WebSockets con una sala por campaña (para el combate en vivo).
- **Frontend**: HTML + CSS + JavaScript vanilla (sin framework ni build). Cuatro páginas
  servidas como archivos estáticos; la UI se arma con concatenación de strings y habla
  con la API por `fetch` + el WebSocket.
- **Auth**: sesión por cookie (`sid`), contraseñas con PBKDF2. Seguridad de mesa casera,
  no de alta exigencia (pensada para LAN sin HTTPS).

Dos sistemas de juego conviven: cada **campaña** tiene un `system` (`cosmere` | `dnd`)
que decide qué parser de PDF/statblock se usa y qué mecánicas se muestran.

---

## 2. Arquitectura del backend

Punto de entrada: [`main.py`](../main.py). Crea la app FastAPI, llama a `init_db()` (crea
el esquema y corre migraciones) y monta los routers y los estáticos.

### Capas

```
HTTP / WebSocket
      │
      ▼
routers/*.py        ← endpoints por dominio (validan, orquestan)
      │
      ├── access.py      ← ¿es DM? ¿es miembro? (autorización por campaña)
      ├── auth.py        ← sesiones por cookie, hashing, usuario actual
      ├── admin.py       ← ¿es administrador? (email de la app o marca en la cuenta)
      ├── recovery.py    ← tokens/códigos de recuperación y confirmación
      ├── mailer.py      ← correo saliente (SMTP en hilo, o carpeta outbox/)
      ├── telemetry.py   ← eventos + buffer de peticiones (lo lee el panel)
      ├── models.py      ← modelos Pydantic (forma de los request bodies)
      ├── *_import.py / *_pdf.py  ← parsers de fichas y statblocks
      ├── state.py       ← estado del combate (cache en memoria + persistencia)
      └── database.py    ← conexión SQLite (db()) + esquema (init_db())
                              │
                              ▼
                         cosmere.db
```

- **`app/database.py`**: `db()` es un `contextmanager` que abre una conexión, aplica los
  PRAGMAs (`foreign_keys`, `busy_timeout`, `synchronous`), hace `commit` al salir y
  cierra. `init_db()` crea las tablas (`CREATE TABLE IF NOT EXISTS`) y corre las
  migraciones idempotentes. La base usa **WAL** para tolerar lecturas concurrentes
  mientras se escribe.
- **`app/auth.py`**: hashing PBKDF2, alta/baja de sesiones, y las dependencias FastAPI
  `current_user` / `optional_user`. La sesión guarda el **rol** (`dm` | `player`) elegido
  al entrar; queda fijo hasta cerrar sesión.
- **`app/access.py`**: `require_dm(conn, cid, user)` y `require_access(conn, cid, user)`
  (DM o miembro aceptado). Todos los routers de campaña los reusan para autorizar.
- **`app/models.py`**: modelos Pydantic de los cuerpos de request (validación de entrada).
- **`app/config.py`**: los ajustes de una campaña (`campaigns.config`, un JSON): qué
  módulos están encendidos, cuánto ven los jugadores de cada stat ajeno y la base de
  capacidad de carga de cada tamaño (`CARGA_BASE` / `size_bases`). Vive aparte del
  router de campañas porque lo consultan varios: el combate, los objetos y las tormentas.
  Todo tiene default, así que una campaña que nunca tocó los ajustes anda igual que antes.
- **Parsers**:
  - `app/pdf_import.py` — lee la ficha PDF de Cosmere (AcroForm `char_*`) y extrae el
    retrato.
  - `app/dnd_pdf.py` — lee la ficha PDF de D&D 5e.
  - `app/cosmere_import.py` / `app/dnd_import.py` — parsean statblocks (YAML) del
    bestiario y los exportan de vuelta a YAML.
- **`app/state.py`**: `combats` es un `CampaignCombats` con una **cache en memoria** del
  combate por campaña, respaldada en la tabla `combats`. `player_view(combat, cfg, user_id)`
  arma lo que ve **un** jugador: sin enemigos ocultos, sin nada si el combate está en
  preparación, y con los stats de los demás recortados según los ajustes (`mask_stats`).
  Lo propio nunca se recorta.
- **`app/ws.py`**: `Hub` mantiene las salas (`cid -> [(ws, is_dm, user_id)]`).
  `push_state(cid)` guarda el combate y lo difunde: al DM el estado completo, y a cada
  jugador su propia vista (se arma una por usuario y se reusa si tiene varias pestañas).

### Routers (`app/routers/`)

| Router | Prefijo | Qué maneja |
|---|---|---|
| `auth` | `/api/auth` | registro, login (usuario o email), logout, recuperación por correo (`/forgot`, `/reset`), confirmación de cambio (`/confirm-change`), cuenta |
| `admin` | `/api/admin` | panel: telemetría, cuentas, campañas, correo y mantenimiento (exige cuenta admin) |
| `campaigns` | `/api/campaigns` | campañas, miembros, invitaciones, config, tormenta, calendario, marcos del DM, opciones de mascota |
| `characters` | `/api/characters` | personajes, PDF, imagen, mascotas, stats en vivo, heridas, marcos, recursos D&D |
| `enemies` | `/api/campaigns/{cid}/enemies` | bestiario (por DM + sistema): import, bulk, export |
| `items` | `/api/campaigns/{cid}/items` | catálogo de objetos (por DM), `/catalog` para jugadores y `/inventories` (vista del DM) |
| `encounters` | `/api/campaigns/{cid}/encounters` | encuentros y overrides por-encuentro |
| `combat` | `/api/campaigns/{cid}/combat` | combate en vivo (stats, turnos, vida máx, ocultar) |
| `frontend` | `/` | sirve las páginas HTML y hace el gating por rol |
| `ws` | `/ws/{cid}` | WebSocket del combate en tiempo real |

### Flujo de una acción de combate

1. El DM (o un jugador sobre lo suyo) llama a un endpoint de `combat` (p. ej. bajar vida).
2. El router valida acceso (`require_access`) y que pueda tocar a ese participante
   (`_guard_participant`).
3. Muta el combate en la **cache en memoria** (`combats`).
4. Llama a `push_state(cid)`: persiste en la tabla `combats` y **difunde por WebSocket**
   la vista que corresponde a cada cliente conectado.
5. Fuera de combate, el roster se refresca por **polling** (cada 5 s) contra
   `/campaigns/{cid}/roster`.

---

## 3. Estructura de archivos

```
combat-tracker-2.0/
  main.py                  ← crea la app y monta routers + estáticos
  requirements.txt         ← dependencias de producción
  requirements-dev.txt     ← + pytest/httpx para los tests
  pytest.ini
  app/
    database.py            ← conexión SQLite + esquema + migraciones
    auth.py                ← sesiones por cookie, hashing, rol
    admin.py               ← identificación del administrador
    recovery.py            ← tokens y códigos de recuperación
    mailer.py              ← correo saliente y plantillas
    telemetry.py           ← eventos y métricas
    settings.py            ← config del server (.env: correo, admins, URL)
    access.py              ← autorización por campaña (DM / miembro)
    models.py              ← modelos Pydantic
    pdf_import.py          ← ficha PDF Cosmere + retrato
    dnd_pdf.py             ← ficha PDF D&D 5e
    cosmere_import.py      ← statblocks Cosmere (import/export)
    dnd_import.py          ← statblocks D&D (import/export)
    state.py               ← estado del combate (cache + persistencia)
    roshar.py              ← calendario rosharano (índice de día ↔ fecha y nombres)
    ws.py                  ← WebSockets por campaña
    routers/               ← auth, admin, campaigns, characters, enemies,
                             encounters, combat, frontend
  .env / .env.example      ← config local (correo, admins). El .env no va al repo
  outbox/                  ← correos en disco cuando no hay SMTP (no va al repo)
  static/
    login.html  admin.html  home.html  dm.html  player.html
    cosmere_sheet.pdf  5e_sheet.pdf   ← fichas rellenables para descargar
  tests/                   ← suite pytest (corre contra DB temporal)
  cosmere.db               ← la base (se crea sola; no va al repo)
```

---

## 4. Base de datos

SQLite, sin ORM: el esquema vive en `init_db()` de [`app/database.py`](../app/database.py).
Los datos "de forma libre" (fichas, estados, listas) se guardan como **JSON dentro de una
columna TEXT** en vez de tablas relacionales, porque son documentos que se leen y
escriben enteros.

### Diagrama de relaciones

```mermaid
erDiagram
    users ||--o{ sessions : "abre"
    users ||--o{ auth_tokens : "pide recuperar contraseña"
    users ||--o{ campaigns : "dirige (dm_id)"
    users ||--o{ characters : "posee (owner_id)"
    users ||--o{ enemies : "posee bestiario (owner_id)"
    users ||--o{ campaign_members : "participa"

    campaigns ||--o{ campaign_members : "tiene"
    campaigns ||--o{ characters : "aloja (campaign_id)"
    campaigns ||--o{ encounters : "tiene"
    campaigns ||--|| combats : "estado de combate"
    campaigns ||--|| storm_tracker : "ciclo de tormenta"
    campaigns ||--|| campaign_calendar : "fecha rosharana"
    campaigns ||--o{ calendar_notes : "notas del calendario"
    campaigns ||--o{ campaign_pet_options : "mascotas habilitadas"

    characters ||--o| character_pdfs : "ficha PDF"
    characters ||--o| character_images : "retrato"
    characters ||--o{ pets : "mascotas"
    characters ||--o| campaign_members : "enlazado por"

    encounters ||--o{ encounter_enemies : "compone"
    enemies ||--o{ encounter_enemies : "aparece en"
    enemies ||--o{ campaign_pet_options : "ofrecido como mascota"

    users ||--o{ items : "posee catálogo (owner_id)"
    items ||--o{ inventory : "copiado a"
    characters ||--o{ inventory : "lleva o guarda"
    pets ||--o{ inventory : "lleva"
    campaigns ||--o{ inventory : "guardado del grupo"
    inventory ||--o{ inventory : "contenedor (parent_id)"
```

`telemetry_events`, `telemetry_requests` y `mail_log` quedan fuera del diagrama a
propósito: guardan `user_id` sin clave foránea (y el `username` en texto plano) para que
el registro sobreviva al borrado de la cuenta. Son planos y se purgan por antigüedad.

### Convenciones

- **Claves foráneas con `ON DELETE CASCADE`**: borrar un usuario, campaña o personaje
  arrastra todo lo suyo. `PRAGMA foreign_keys=ON` se activa en cada conexión, así que las
  cascadas funcionan.
- **Migraciones idempotentes**: cada cambio de esquema se agrega con `PRAGMA table_info`
  + `ALTER TABLE ADD COLUMN` (o `CREATE TABLE IF NOT EXISTS`), de modo que `init_db()`
  actualiza bases viejas sin perder datos y se puede correr siempre.
- **Columnas JSON (TEXT)**: `statuses`, `sheet`, `dnd_resources`, `injuries` (personajes);
  `acciones`, `stats` (enemigos/mascotas); `config` (campaña); `overrides` (encounter);
  `categorias`, `stats` (objetos e inventario); `data` (combate). Se serializan con
  `json.dumps`/`json.loads`.

### Tablas

#### `users` — cuentas
| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `username` | TEXT | único |
| `email` | TEXT | para recuperar contraseña; único al crearlo o cambiarlo |
| `pass_hash`, `salt` | TEXT | PBKDF2 |
| `last_login` | TEXT | última entrada |
| `login_count` | INTEGER | cuántas veces entró |
| `blocked` | INTEGER | 1 = cuenta bloqueada desde el panel (echa sus sesiones) |
| `is_admin` | INTEGER | permiso de panel dado a mano (además de `ADMIN_EMAILS`) |
| `created_at` | TEXT | |

#### `sessions` — sesiones activas
| Columna | Tipo | Notas |
|---|---|---|
| `token` | TEXT PK | valor de la cookie `sid` |
| `user_id` | INTEGER FK→users | cascade |
| `role` | TEXT | `dm` \| `player`, fijado al entrar |
| `ip`, `user_agent` | TEXT | de dónde salió la sesión |
| `last_seen` | TEXT | se refresca como mucho cada 2 min (no en cada request) |
| `created_at` | TEXT | |

#### `auth_tokens` — recuperación y confirmación de contraseña
| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `user_id` | INTEGER FK→users | cascade |
| `kind` | TEXT | `reset` (olvidé la contraseña) \| `change` (cambio desde la cuenta) |
| `token` | TEXT | único; viaja en el enlace del correo |
| `code` | TEXT | 6 dígitos, para tipear a mano |
| `payload` | TEXT (JSON) | en `change`, el `pass_hash`/`salt` nuevo que se aplica al confirmar |
| `ip` | TEXT | quién lo pidió (para el tope por IP) |
| `attempts` | INTEGER | intentos de código fallidos |
| `created_at`, `expires_at`, `used_at` | TEXT | vive 30 min y se usa una sola vez |

#### `mail_log` — correo saliente
| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | TEXT | |
| `to_addr`, `subject`, `kind` | TEXT | `kind`: `reset`, `change`, `password-changed`… |
| `ok` | INTEGER | salió o no |
| `error` | TEXT | el error del SMTP si falló |
| `mode` | TEXT | `smtp` \| `outbox` |

#### `telemetry_events` — eventos con nombre
| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | TEXT | UTC |
| `kind` | TEXT | `login`, `login_fail`, `register`, `forgot`, `reset_done`, `admin_*`… |
| `user_id`, `username` | | quién (el nombre se guarda plano: sobrevive al borrado) |
| `detail`, `ip` | TEXT | |
| `ok` | INTEGER | 0 = intento fallido |

#### `telemetry_requests` — una fila por petición HTTP
| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `ts` | TEXT | UTC |
| `method`, `path` | TEXT | la ruta va normalizada: `/api/campaigns/{id}/enemies` |
| `status` | INTEGER | |
| `ms` | REAL | cuánto tardó |
| `user_id` | INTEGER | sesión que la hizo, si había |

#### `campaigns` — campañas (mesa de un DM)
| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `name` | TEXT | |
| `dm_id` | INTEGER FK→users | dueño/DM |
| `system` | TEXT | `cosmere` \| `dnd` |
| `config` | TEXT (JSON) | ajustes de la campaña: módulos encendidos, qué ven los jugadores, rango de tormenta y curva de descarga (ver `app/config.py`) |
| `day_count` | INTEGER | días **absolutos** transcurridos (`storm_tracker.day` se reinicia con cada tormenta) |
| `created_at` | TEXT | |

#### `campaign_members` — quién está en qué campaña
| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `campaign_id` | INTEGER FK→campaigns | cascade |
| `user_id` | INTEGER FK→users | cascade |
| `character_id` | INTEGER FK→characters | el PJ con que entró (SET NULL al borrarlo) |
| `status` | TEXT | `invited` \| `accepted` |
| | | `UNIQUE(campaign_id, user_id)` |

#### `characters` — personajes de jugador
Un personaje pertenece a **una** campaña (uno por campaña por jugador, regla de la app).

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `owner_id` | INTEGER FK→users | dueño (jugador) |
| `campaign_id` | INTEGER FK→campaigns | campaña a la que pertenece |
| `name` | TEXT | |
| `vida_max` / `focus_max` / `inv_max` | INTEGER | máximos |
| `vida` / `focus` / `inv` | INTEGER | valores actuales (persisten fuera de combate) |
| `marcos` / `marcos_light` | INTEGER | esferas totales y cuántas están cargadas (con luz) |
| `statuses` | TEXT (JSON) | lista de estados |
| `injuries` | TEXT (JSON) | heridas `[{id, name, days, permanent}]` |
| `sheet` | TEXT (JSON) | ficha completa extraída del PDF |
| `dnd_resources` | TEXT (JSON) | `{slots, counters}` (solo D&D) |
| `has_pdf` / `has_image` | INTEGER | flags 0/1 |
| `created_at` | TEXT | |

#### `character_pdfs` / `character_images` — adjuntos del personaje
`character_id` PK/FK (1:1 con el personaje, cascade). `character_pdfs.pdf` es un BLOB con
el PDF original; `character_images` guarda el retrato (`image` BLOB + `mime`).

#### `pets` — mascotas de un personaje
Copia (snapshot) de un enemigo del bestiario que el jugador eligió como mascota. Entra al
combate como aliado que controla el jugador.

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `owner_id` | INTEGER FK→users | |
| `character_id` | INTEGER FK→characters | cascade |
| `name`, `vida_max`/`focus_max`/`inv_max`, `vida`/`focus`/`inv` | | como un personaje |
| `statuses`, `acciones`, `stats` | TEXT (JSON) | |
| `compartida` | INTEGER | 1 = **de todos**: la maneja cualquiera de la campaña |
| `created_at` | TEXT | |

Una mascota `compartida` sigue colgando del personaje que la trajo (ahí se borra y ahí se
prende o apaga el interruptor), pero para todo lo demás es del grupo: cualquier miembro
aceptado le toca stats, estados e inventario, y en combate ni el roster ni `player_view`
le enmascaran los números. El helper `_owned_pet` la encuentra aunque el pedido venga con
el personaje de **otro** jugador, así el frontend no necesita saber de quién es.

#### `enemies` — bestiario
El bestiario es **por DM y por sistema** (no por campaña): se comparte entre todas las
campañas del mismo DM del mismo `system`.

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `owner_id` | INTEGER | DM dueño (se consulta por `owner_id` + `system`) |
| `system` | TEXT | `cosmere` \| `dnd` |
| `name`, `tipo`, `clase` | TEXT | `clase`: `minion` \| `rival` \| `boss` |
| `vida_max`/`focus_max`/`inv_max` | INTEGER | |
| `acciones`, `stats` | TEXT (JSON) | ficha completa |
| `notas`, `faction_color` | TEXT | |
| `campaign_id` | INTEGER | **legacy**: quedó de cuando el bestiario era por campaña; hoy no se usa en altas nuevas |

#### `encounters` — encuentros armados
`id`, `campaign_id` (FK, cascade), `name`, `descripcion`.

#### `encounter_enemies` — enemigos dentro de un encuentro
| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `encounter_id` | INTEGER FK→encounters | cascade |
| `enemy_id` | INTEGER FK→enemies | cascade |
| `cantidad` | INTEGER | cuántas copias |
| `overrides` | TEXT (JSON) | ajustes válidos **solo en este encuentro** (nombre, clase, vida/focus/inv máx, color); el bestiario no se toca |

#### `campaign_pet_options` — mascotas habilitadas por campaña
Qué enemigos del bestiario puede elegir un jugador como mascota, **por campaña**.
`id`, `campaign_id` (FK), `enemy_id` (FK), `UNIQUE(campaign_id, enemy_id)`.

#### `combats` — estado del combate (1 por campaña)
`campaign_id` PK/FK. `data` es un TEXT con el JSON del combate entero (participantes,
ronda, fase, turnos, estados). Es el respaldo de la cache en memoria de `state.py`.

#### `items` — catálogo de objetos (por DM, solo Cosmere)
Igual que el bestiario: pertenece al **DM** y se comparte entre sus campañas.

| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `owner_id` | INTEGER FK→users | el DM dueño del catálogo |
| `name`, `descripcion`, `notas` | TEXT | |
| `categorias` | TEXT (JSON) | `["armas", "lujo"]` |
| `precio` | INTEGER | precio base, en marcos |
| `slots` | INTEGER | 1 por defecto; 0 = no ocupa; `Cumbersome N` = 1+N |
| `capacity_bonus` | INTEGER | mochila = 2 |
| `secreto` | INTEGER | 1 = oculto: no aparece en el catálogo del jugador ni se puede agarrar |

#### `inventory` — dónde está cada objeto
Guarda una **copia** del objeto del catálogo: editar el catálogo no altera lo ya
entregado. La misma tabla cubre las cuatro zonas, y **quién** es el dueño sale de la
combinación de columnas:

| Zona | `character_id` | `pet_id` | `campaign_id` | `stash` |
|---|---|---|---|---|
| Encima del personaje | sí | NULL | sí | `''` |
| Encima de una mascota | NULL | sí | sí | `''` |
| Guardado del personaje | sí | NULL | sí | `'personal'` |
| Guardado del grupo | NULL | NULL | sí | `'grupo'` |

| Columna | Tipo | Notas |
|---|---|---|
| `item_id` | INTEGER FK→items | origen en el catálogo (nullable: puede no venir de ahí) |
| `name`, `descripcion`, `categorias`, `kind`, `stats`, `peso` | | copia del objeto |
| `slots`, `capacity_bonus`, `cantidad` | INTEGER | |
| `usos`, `usos_max` | INTEGER | dosis/cargas de esta unidad |
| `contenedor`, `contenedor_capacidad` | INTEGER | mochila, carreta… |
| `parent_id` | INTEGER FK→inventory | dentro de qué contenedor está (cascade) |
| `equipado` | INTEGER | 0 = lo dejó: no cuenta para la capacidad |
| `stash` | TEXT | `''` \| `personal` \| `grupo` |
| `campaign_id` | INTEGER FK→campaigns | cascade; imprescindible para el guardado del grupo |

Solo lo que está en `stash=''`, sin `parent_id` y `equipado` pesa contra la capacidad de
carga. Los guardados no tienen tope. Al mover o pasar una entrada, **sus hijos viajan con
ella**: la mochila se guarda llena.

`carrying_capacity(size, fuerza, rows, bases)` calcula la carga; `bases` sale de la config
de la campaña (`size_bases`), así que el DM puede correr la escala sin tocar código. Se
lee **una sola vez** por armado de inventario y se pasa a cada personaje y mascota.

#### `storm_tracker` — ciclo de altas tormentas (1 por campaña)
| Columna | Tipo | Notas |
|---|---|---|
| `campaign_id` | INTEGER PK/FK | |
| `day` | INTEGER | día del ciclo actual |
| `target` | INTEGER | día en que cae la tormenta |
| `moment` | TEXT | momento del día (aleatorio) |

#### `campaign_calendar` — fecha rosharana (1 por campaña)
| Columna | Tipo | Notas |
|---|---|---|
| `campaign_id` | INTEGER PK/FK | |
| `day_index` | INTEGER | día **absoluto**: `año*500 + mes*50 + semana*5 + día` |

Se guarda un solo número y la fecha se descompone al mostrarla
([`app/roshar.py`](../app/roshar.py)): pasar días es una suma y no hay que arrastrar el
acarreo de semanas, meses y años. Arranca en `1173.1.1.1`.

#### `calendar_notes` — notas y pines del calendario
| Columna | Tipo | Notas |
|---|---|---|
| `id` | INTEGER PK | |
| `campaign_id` | INTEGER FK→campaigns | cascade |
| `day_index` | INTEGER | día al que está clavada (índice absoluto) |
| `user_id` | INTEGER FK→users | quién la escribió (SET NULL) |
| `texto` | TEXT | |
| `color` | TEXT | color del pin |
| `secreto` | INTEGER | 1 = solo la ve el DM |

---

## 5. Decisiones de diseño a tener en cuenta

- **Bestiario por DM + sistema, no por campaña**: un DM carga sus enemigos una vez y los
  reusa en todas sus campañas del mismo sistema. Por eso `enemies.campaign_id` quedó como
  columna muerta.
- **Snapshot de mascotas y de overrides**: elegir una mascota copia la ficha del enemigo;
  editar el enemigo después no cambia la mascota ya agregada. Los `overrides` de un
  encuentro tampoco tocan el bestiario. Esto evita sorpresas a mitad de campaña.
- **Combate: cache en memoria + persistencia**: el combate vive en `state.combats`
  (rápido, mutable) y se persiste en la tabla `combats` en cada `push_state`. Si se
  reinicia el server a mitad de combate, se restaura. Asume **un solo worker** de uvicorn
  (la cache es un dict en proceso).
- **Vista por rol y por persona**: el DM ve todo; cada jugador ve su `player_view` (sin
  enemigos ocultos, nada mientras el combate está en preparación, y los stats de los demás
  recortados según los ajustes). Se aplica en el WebSocket, en `GET /combat` y en el
  roster. **Se filtra en el servidor, no en el HTML**: en modo abstracto solo viaja un
  porcentaje redondeado de a 5, así que el número exacto no se puede deducir mirando la
  red. Cuesta armar una vista por usuario en cada broadcast, pero las salas son de pocas
  personas y se cachea por `user_id` dentro del envío.
- **El tiempo pasa en un solo lugar**: `_pass_days(conn, cid, n)` (router de campañas) es
  el único que mueve el reloj. Corre el ciclo de tormentas y la descarga de marcos **día
  por día** (el resultado depende del camino, no solo del total) y le suma `n` al
  calendario. Lo usan el descanso largo y los botones de pasar días, así que no hay dos
  formas distintas de que avance el tiempo.
- **Calendario como número, no como fecha**: `campaign_calendar.day_index` es un entero
  absoluto; `app/roshar.py` traduce a año/mes/semana/día y arma los nombres por
  composición. El frontend repite esa aritmética (cuatro líneas) para dibujar la grilla
  sin pedirle 50 fechas al servidor.
- **La contraseña no cambia sin pasar por el correo**: tanto "me la olvidé" como el
  cambio desde *Mi cuenta* generan un `auth_tokens` con enlace + código de 6 dígitos, y
  el cambio se aplica recién al usarlo. El par enlace/código existe porque el server vive
  en una LAN: el mail se abre en el celular, donde `BASE_URL` puede no resolver, y ahí el
  código es la salida. Aplicar el cambio **cierra todas las sesiones** de esa cuenta.
- **Sin SMTP no se rompe nada**: si falta `MAIL_PASSWORD`, `mailer` escribe el mensaje en
  `outbox/` como `.eml` en vez de mandarlo. La recuperación sigue siendo usable en una
  mesa sin internet, y los tests leen los mensajes de `mailer.SENT` sin tocar el disco.
- **Telemetría por lotes**: los eventos (pocos) se escriben en el momento; las peticiones
  (muchas) se juntan en memoria y un hilo las vuelca cada 5 s. Escribir una fila por
  request dentro del ciclo de la petición le agregaría contención al WAL, que ya comparten
  los WebSockets y el polling. Se purga sola por antigüedad (`TELEMETRY_DAYS`).
- **Admin por email, no por rol en la base**: administrador es quien entra con un email de
  `ADMIN_EMAILS` (o tiene `users.is_admin`). Así la cuenta del correo de la app siempre
  puede entrar al panel aunque la base se haya perdido, y el permiso no se puede "editar"
  desde adentro de la app para las cuentas de configuración.
- **Modo fijo por sesión**: el rol `dm`/`player` se elige al entrar y no se cambia sin
  volver a loguear; el router `frontend` redirige si intentás entrar al panel del otro
  rol.
- **JSON en TEXT**: fichas, estados y listas se guardan como documentos JSON. Cómodo para
  leer/escribir entero, pero **no se puede consultar por SQL** el contenido (se filtra en
  Python).
- **Sin tiendas: el catálogo es la tienda**. No hay stock, ni precios por local, ni
  aprobación del DM. El jugador agarra del catálogo, **fija el precio** (descuento o
  hallazgo) y **elige con qué esferas paga**. Es a confianza, a propósito: la mesa se
  arregla en la mesa, y lo que el DM no quiere que exista lo marca como oculto
  (`items.secreto`). La versión anterior tenía generador de tiendas, asentamientos y
  pedidos de compra; se sacó entera (queda en el historial de git).
- **Una sola tabla para las cuatro zonas del inventario**: encima, guardado propio y
  guardado del grupo son la misma tabla con distinta combinación de
  `character_id`/`pet_id`/`campaign_id`/`stash`. Alternativa descartada: una tabla por
  zona, que duplicaría toda la lógica de contenedores, dosis y capacidad.

---

## 6. Tests

Suite con **pytest + TestClient** en `tests/`. Corre siempre contra una **base temporal**
(nunca toca `cosmere.db`): `conftest.py` repunta `app.database.DB_PATH` a un archivo
temporal por test y limpia la cache de combate en memoria (y `mailer.SENT`). Cubre auth,
recuperación de contraseña por correo, panel de admin, campañas, personajes (incluido
import de PDF Cosmere y D&D), bestiario, encuentros, mascotas y combate.

```bash
pip install -r requirements-dev.txt
pytest
```
