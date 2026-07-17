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
separado. El modo **queda fijo para esa sesión**: para cambiarlo, cerrá sesión y volvé
a entrar eligiendo el otro (una misma cuenta puede ser DM de sus campañas y jugador en
campañas de otros, pero de a un modo por vez).

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
    pdf_import.py       ← extracción de fichas de personaje y su retrato desde PDF
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
4. **Encuentros:** combiná enemigos del bestiario. Se pueden **editar después de
   creados** (botón *editar*), y dentro de un encuentro podés **ajustar a un enemigo**
   sin tocar el bestiario (ver abajo).
5. **Combate:** elegí un encuentro e iniciá. Entran automáticamente los personajes de
   los jugadores aceptados + los enemigos del encuentro.

**Como jugador:**
1. En el panel principal ves una **galería visual de tus personajes** (retrato, nombre,
   clases, nivel y campaña). Cada personaje **pertenece a una campaña** (uno por campaña):
   no hay personajes sueltos.
2. **Aceptá invitaciones** creando el personaje ahí mismo: **a mano** o **subiendo la
   ficha PDF**. Podés **descargar la ficha vacía** (rellenable) desde ese mismo diálogo,
   completarla con un lector de PDF y subirla: se extraen automáticamente **vida, focus
   e investidura** (y el resto de la ficha: atributos, defensas, habilidades, talentos,
   armas, equipo) y se intenta sacar un **retrato** del PDF. Eso enlaza al personaje con
   la campaña y acepta la invitación en un paso. (En campañas de D&D 5e, la ficha vacía y
   el parser son los de D&D.)
3. Al **entrar a un personaje** llegás a su ficha detallada (pestaña **Mi personaje**),
   con botones para **editarlo**, gestionar sus **🐾 mascotas**, subir/cambiar su
   **retrato** y **actualizar el PDF**.
4. **Mascotas:** elegís una de las que el **DM habilitó** para tu campaña (una lista que
   arma con enemigos de su bestiario). Entran al combate como aliados que **vos
   controlás**, con seguimiento propio (vida, focus, estados, turno).
5. Dentro de una campaña tenés tres pestañas: **Mi personaje** (tu ficha y gestión fuera
   de combate), **Grupo** (el resto del grupo) y **Combate** (tu turno en vivo). Podés
   **salir** de la campaña cuando quieras (esto borra tu personaje de esa campaña).

Para el detalle de marcos, tormentas, retratos y heridas, ver
[Novedades](#novedades-personajes-marcos-tormentas-y-heridas).

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

### Mascotas para los jugadores

En el **Bestiario**, cada enemigo tiene un botón 🐾 para **habilitarlo como mascota** en
la campaña actual. Los enemigos habilitados quedan marcados (🐾 mascota) y los jugadores
pueden **elegir uno como mascota** desde su ficha (botón 🐾 Mascotas). La lista es **por
campaña**: el mismo bestiario puede ofrecer mascotas distintas en cada campaña. Al elegir
una, se guarda una **copia** de la ficha, así que si después editás el enemigo la mascota
ya agregada no cambia.

### Buscar enemigos

Tanto en el **Bestiario** como en el **creador de encuentros** hay un buscador por
palabra además de los filtros de tier/rol/tamaño/tipo. Busca en el nombre, el tier y el
tipo de criatura; si ponés varias palabras, tienen que aparecer todas.

### Exportar el bestiario

**Bestiario → ⭱ Exportar bestiario** descarga todas tus fichas en un solo archivo
`bestiario.yaml` (statblocks separados por `---`). Sirve de backup y para pasárselo a
otro DM: se vuelve a cargar tal cual con **⭳⭳ Importar en bulk**. El archivo es
legible y editable a mano, y conserva también el color y las notas de cada ficha.

La **clase** (Minion / Rival / Boss) se deduce del `tier` (o de un campo `class:`
explícito) y define un color inicial que podés cambiar. Cada enemigo también se
puede editar a mano desde el bestiario para ajustar clase y color.

### Ajustar un enemigo dentro de un encuentro

Los encuentros se **editan después de creados**: cambiás nombre, descripción, qué
enemigos entran y cuántos.

Además, cada enemigo del encuentro tiene un recuadro **"Solo en este encuentro"** donde
ajustás **nombre, clase, vida/focus/investidura máximas y color**. Ese ajuste:

- **solo vale para ese encuentro** — el bestiario y los demás encuentros no se tocan;
- se marca en dorado, y con **"volver al bestiario"** lo deshacés;
- si dejás un campo vacío o igual al del bestiario, el enemigo vuelve a **heredar** ese
  valor (si después editás la ficha del bestiario, el cambio le llega);
- cambiar la **clase** recalcula la amenaza del encuentro.

Así armás un "Archer veterano" con más vida y otro color reusando la misma ficha base.

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
- **Vida máxima en vivo:** en la tarjeta de un enemigo se puede tocar el número de la
  derecha ("/ 12") para **cambiarle la vida máxima** a mitad de combate. Si la vida
  actual supera el nuevo máximo, se recorta. Solo afecta a **ese combate**: ni el
  bestiario ni el encuentro cambian
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

## Novedades: personajes, marcos, tormentas y heridas

### Personajes ligados a una campaña

- Cada personaje **pertenece a una campaña**, y hay **uno por campaña** por jugador. No
  se pueden tener personajes fuera de una campaña.
- El personaje se crea **al aceptar una invitación** (creándolo a mano o subiendo el PDF).
  Eso enlaza el PJ a la campaña y marca la membresía como aceptada en un solo paso.
- **Salir** de una campaña (o que el DM te **eche**) **elimina** tu personaje de esa
  campaña (con sus mascotas, PDF e imagen, por cascada).
- El panel principal del jugador es una **galería visual**: retrato, nombre, clases,
  nivel y campaña de cada personaje.

### Retrato del personaje

- Al subir el PDF, el sistema intenta **extraer un retrato** de la ficha
  (`extract_pdf_image`, best-effort: toma la imagen raster más grande; requiere que el
  PDF traiga una imagen embebida). Si no encuentra ninguna, no pasa nada.
- Desde **Mi personaje** el jugador puede **subir/cambiar** el retrato a mano (gana sobre
  el del PDF) o **borrarlo**. Reimportar el PDF **no pisa** un retrato subido a mano.
- El retrato lo ven el dueño, el DM y los miembros de la campaña.

### Marcos (esferas) e investidura

Los **marcos** son la moneda del juego y a la vez almacenan **luz/investidura**. Cada
personaje tiene un total de marcos que se reparte en:

- **Cargados** (con luz) — los que pueden alimentar investidura.
- **Opacos** (sin luz) — total menos cargados.

En **Mi personaje** y en **Combate** hay un recuadro chico (arriba a la derecha del
bloque de vida): **total** arriba, **cargados** abajo a la izquierda, **opacos** abajo a
la derecha. Haciendo **click en un número** lo editás directamente (Enter para guardar,
Escape para cancelar); el sondeo cada 5 s **no** te pisa lo que estás escribiendo.

- **Cargar investidura** consume marcos cargados **1:1**: cada punto de investidura que
  subís apaga un marco cargado (hasta llenar el medidor o quedarte sin luz).
- El **descanso largo ya no recarga la investidura**: el jugador decide cuándo cargarla
  desde sus marcos.
- **El DM** puede, por jugador, **agregar/sacar marcos** (al sacar se van primero los
  opacos; los que agrega entran opacos) y **cargar/apagar** marcos (mover luz), desde su
  vista de jugadores.

### Ciclo de altas tormentas y descarga de marcos

El **paso del tiempo** avanza con **Descanso largo** o con **Adelantar un día** (viaje).
Cada día que pasa:

- Cuando **cae la tormenta**, se **recargan todos los marcos** (todos pasan a cargados).
  Tras la tormenta arranca un ciclo nuevo con un objetivo al azar dentro del rango.
- Sin tormenta, a partir del **día de inicio de descarga** cada marco cargado se puede
  **apagar** con probabilidad creciente (pocos al principio, todos para el **día de
  apagado total**). La cantidad que se apaga cada día es **aleatoria** (Bernoulli por
  marco).
- La **forma** de la caída la controla la **curva de descarga** (exponente): `1` = pareja
  (lineal); más alto = arranca más lento y acelera al final. Por defecto **2.0**, así que
  los primeros días descargan suave.

Probabilidad por marco un día dado (sin tormenta):
`base = (día − (inicio−1)) / (apagado − (inicio−1))`, `p = base ^ curva`.
Con los valores por defecto (inicio 5, apagado 15, curva 2) casi no se pierde luz al
principio y todo llega a 0 en el día 15 si no hubo tormenta.

### Panel de ajustes del DM (⚙ Ajustes)

En la vista del DM, el botón **⚙ Ajustes** abre un panel para **tunear los parámetros**
de la campaña sin tocar código (se guardan en la campaña):

- **Tormentas:** días mínimo/máximo entre tormentas.
- **Descarga de marcos:** día de inicio, día de apagado total y **curva de descarga**.
- **Estado actual de la tormenta:** día del ciclo, día objetivo y momento del día.

El panel incluye un **preview en vivo**: una mini gráfica de barras del **% de marcos con
luz por día**, que se recalcula al instante al mover el día de inicio/apagado o la curva
(es el valor esperado; la descarga real es al azar). Sirve para ver el ritmo y ajustarlo
antes de guardar.

Los valores por defecto: tormenta cada **8–12** días, descarga del día **5** al **15**,
curva **2.0**. Una campaña sin ajustes guardados toma estos valores.

### Vista de jugadores del DM

La lista de jugadores del DM es **visual**: por cada jugador ve su personaje con retrato,
nivel/clase, **barras** de vida/focus/investidura, **estados**, **heridas**, su recuadro
de **marcos** (con controles para cargar/descargar y agregar/sacar) y acceso a la ficha y
al PDF.

### Heridas (injuries)

- Cada personaje puede acumular **heridas** con un **tipo**, **días** restantes y si es
  **permanente**. El **descanso largo** baja en 1 los días de las no permanentes; al bajar
  de 0 se curan.
- Mientras la asignás, tomarte tu tiempo **ya no reinicia** el tipo ni los días (el sondeo
  no pisa la selección en curso).
- Si una herida **genera un estado**, ese estado queda **marcado y bloqueado**: no se
  puede quitar a mano hasta que la herida se cure.

## Datos y persistencia

Todo se guarda en `cosmere.db` (SQLite) y **sobrevive a reiniciar/apagar el servidor**:
cuentas, campañas, membresías, personajes (con su PDF), bestiario y encuentros por
campaña.

- **Combate activo (por campaña):** si reiniciás a mitad de combate, se restaura tal
  cual (ronda, turnos, vida y estados de todos).
- **Estado de los personajes:** cada personaje conserva su **vida, focus, investidura y
  estados actuales** de forma permanente — sobreviven a "Terminar combate" y a empezar
  un combate nuevo (no vuelven a vida full solos). También persisten sus **marcos**
  (cargados/opacos), **heridas** y **retrato**.
- **Ciclo de tormentas y ajustes por campaña:** el día del ciclo, el objetivo y los
  parámetros del panel ⚙ Ajustes se guardan por campaña y sobreviven al reinicio.
- Los **enemigos** arrancan cada combate con su vida completa (son plantillas del
  encuentro); su vida solo se mantiene mientras ese combate siga activo.

Para hacer backup, copiá el archivo `cosmere.db`.
