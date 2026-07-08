# Cosmere Combat Tracker

Tracker de combate para Cosmere RPG con backend en Python (FastAPI + SQLite) y sincronización en tiempo real por WebSockets.

## Instalación (una sola vez)

Necesitás Python 3.10 o superior instalado.

```bash
cd cosmere-app
pip install -r requirements.txt
```

## Ejecutar (cada sesión)

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Todos (DM y jugadores) entran a la **misma dirección** y se registran / inician sesión:

- **Vos, en la compu:** `http://localhost:8000/`
- **Los jugadores, desde el celular** (misma wifi): `http://TU_IP_LOCAL:8000/`

Cada uno crea su cuenta (**usuario + email + contraseña**). En la pantalla de entrada
elegís si entrás **como DM** o **como Jugador** — cada modo tiene su propio panel
separado, y podés cambiar de modo con el switch de arriba (una misma cuenta puede ser
DM de sus campañas y jugador en campañas de otros).

- **¿Olvidaste la contraseña?** Desde la pantalla de entrada podés restablecerla dando
  tu usuario y el email de la cuenta.
- **Editar tu cuenta:** el botón **Cuenta** (arriba a la derecha) te deja cambiar
  usuario, email y contraseña.

### ¿Cómo saber tu IP local?

- **Windows:** abrí cmd y ejecutá `ipconfig` → buscá "Dirección IPv4" (ej: `192.168.1.50`)
- **Mac/Linux:** `ip addr` o `ifconfig` → buscá la IP que empieza con `192.168.` o `10.`

Entonces los jugadores entran a algo como: `http://192.168.1.50:8000/`

> Si el firewall de Windows pregunta, permitile el acceso a Python en redes privadas.
> Es una app pensada para LAN (sin HTTPS): el login es simple, no de alta seguridad.

## Estructura

```
cosmere-app/
  main.py               ← punto de entrada (crea la app y monta los routers)
  app/
    database.py         ← conexión SQLite + esquema
    auth.py             ← cuentas y sesiones (login por cookie)
    access.py           ← chequeos de acceso a campañas (DM / miembro)
    models.py           ← modelos Pydantic
    pdf_import.py       ← extracción de fichas de personaje desde PDF
    cosmere_import.py   ← parser de statblocks (importar enemigos)
    state.py            ← estado del combate por campaña (memoria + persistencia)
    ws.py               ← WebSockets con salas por campaña
    routers/            ← auth, campaigns, characters, enemies, encounters, combat, frontend
  cosmere.db            ← base de datos (se crea sola)
  requirements.txt
  static/
    login.html          ← entrar / crear cuenta
    home.html           ← panel principal (tus campañas y tus personajes)
    dm.html             ← vista del DM de una campaña
    player.html         ← vista de juego del jugador
```

## Flujo de uso

Al entrar y loguearte llegás al **panel principal** (`/`), con dos zonas:

**Como DM:**
1. **Creá una campaña.** Abrila para entrar a tu panel de esa campaña.
2. **Jugadores:** invitá gente por su nombre de usuario (o echala). Cuando aceptan,
   podés **ver la ficha** del personaje que trajeron.
3. **Bestiario:** cargá enemigos o **importalos desde código** (ver abajo). Cada
   campaña tiene su propio bestiario.
4. **Encuentros:** combiná enemigos del bestiario.
5. **Combate:** elegí un encuentro e iniciá. Entran automáticamente los personajes de
   los jugadores aceptados + los enemigos del encuentro.

**Como jugador:**
1. **Creá personajes** a mano, o **subí la ficha PDF** (formato oficial de Cosmere RPG):
   se extraen automáticamente **vida, focus e investidura** (y el resto de la ficha:
   atributos, defensas, habilidades, talentos, armas, equipo).
2. **Mascotas:** con el botón **🐾 Mascotas** de un personaje cargás sus mascotas
   pegando el statblock (mismo formato de código que los enemigos). Entran al combate
   como aliados que **vos controlás**, y les llevás el seguimiento (vida, focus,
   estados, turno) igual que a tu personaje.
3. **Aceptá invitaciones** a campañas, eligiendo con qué personaje entrás (sus mascotas
   van con él). Podés **salir** de una campaña cuando quieras.
4. Entrá a **Jugar** para gestionar en vivo tu turno, vida, focus, investidura y estados
   (los tuyos y los de tus mascotas).

### Importar enemigos desde código

En **Bestiario → Importar desde código** podés pegar un statblock en formato
Cosmere RPG (YAML, el mismo que usa el plugin *Statblocks* de Obsidian) y el
sistema arma la ficha completa: atributos (STR/DEF/SPD, etc.), las tres
defensas, vida/focus/investidura, movimiento, sentidos, habilidades, idiomas,
rasgos y acciones. No requiere internet ni ninguna API.

Ejemplo:

```yaml
layout: Cosmere RPG
name: "Archer"
tier: "Tier 1 Minion – Medium Humanoid"
str: 2
pdef: 13
spd: 1
health: "12 (9-15)"
int: 2
cdef: 13
wil: 1
focus: 3
awa: 2
sdef: 13
pre: 1
investiture: 0
hp: 12
movement: "25 ft."
senses: "10 ft. (sight)"
skills_p: "Agility +3, Heavy Weaponry +4, Light Weaponry +3"
traits:
  - name: "∞ Minion"
    desc: "The archer's attacks can't critically hit."
actions:
  - name: "▶ Strike: Knife"
    desc: "Attack +3, reach 5 ft. **Hit:** 5 (1d4 + 3) keen damage."
```

Las acciones con `(Costs X)` en el nombre toman ese coste automáticamente, y las
negritas `**...**` de las descripciones se muestran resaltadas.

La **clase** (Minion / Rival / Boss) se deduce del `tier` (o de un campo `class:`
explícito) y define un color inicial que podés cambiar. Cada enemigo también se
puede editar a mano desde el bestiario para ajustar clase y color.

### Durante el combate

- Cada participante tiene un toggle **Rápido / Lento** (sistema de turnos del Cosmere RPG)
- El orden de fases: Jugadores rápidos → Enemigos rápidos → Jugadores lentos → Enemigos lentos
- **Clases de enemigo:** Minion, Rival y Boss. Los **Boss actúan dos veces por ronda**
  (turno rápido *y* lento), con dos marcas de "actuado" independientes
- **Turnos aleatorios:** al iniciar el combate y en cada **Siguiente ronda**, cada
  enemigo (menos los Boss) recibe turno rápido o lento al azar. Igual podés
  cambiarlo a mano con el toggle
- **Colores:** cada enemigo lleva su color para diferenciarlos de un vistazo
  (borde y punto en la tarjeta), visible también para los jugadores. En combate,
  cada tarjeta de enemigo tiene un **selector de color** (arriba a la derecha) para
  pintar esa copia concreta de cualquier color — así distinguís "Archer 1" de
  "Archer 2" aunque sean del mismo tipo. El cambio es solo para ese combate.
- **Visibilidad:** el botón 👁 / 🚫 de cada enemigo decide si los jugadores lo ven o
  no en su pantalla (los ocultos desaparecen de la vista de jugadores)
- "Marcar como actuado" atenúa la tarjeta; "Siguiente ronda" limpia todas las marcas
- Los enemigos muestran sus acciones y su ficha desplegables (▸ Ver acciones / ▸ Ver ficha)
- **Exhausted** es apilable: click izquierdo agrega una carga, click derecho quita una
- En los controles de stats, **bajar está a la izquierda (−) y subir a la derecha (+)**

### Vista de juego del jugador

- El jugador entra a **Jugar** en una campaña donde fue aceptado. Su personaje es el
  que eligió al aceptar la invitación (no hace falta seleccionarlo cada vez).
- Sobre **su** personaje ve los números exactos y gestiona en vivo su **turno
  (rápido/lento), vida, focus, investidura y estados** (se sincroniza con el DM y el
  resto al instante). Solo puede tocar su propio personaje.
- De los aliados ve el estado de salud descriptivo (sin números), su turno y estados.
- De los **enemigos** ve solo el nombre, color, salud descriptiva y estados: **no** ve
  su tier/tipo, ni si tomaron turno rápido o lento. Los ocultos por el DM no aparecen.

## Datos y persistencia

Todo se guarda en `cosmere.db` (SQLite) y **sobrevive a reiniciar/apagar el servidor**:
cuentas, campañas, membresías, personajes (con su PDF), bestiario y encuentros por
campaña.

- **Combate activo (por campaña):** si reiniciás a mitad de combate, se restaura tal
  cual (ronda, turnos, vida y estados de todos).
- **Estado de los personajes:** cada personaje conserva su **vida, focus, investidura y
  estados actuales** de forma permanente — sobreviven a "Terminar combate" y a empezar
  un combate nuevo (no vuelven a vida full solos).
- Los **enemigos** arrancan cada combate con su vida completa (son plantillas del
  encuentro); su vida solo se mantiene mientras ese combate siga activo.

Para hacer backup, copiá el archivo `cosmere.db`.
