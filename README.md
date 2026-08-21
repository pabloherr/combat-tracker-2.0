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

**Ponerle nombre.** La mascota entra con el nombre del bestiario (*Axehound*), pero se le
cambia tocando el nombre o el **✎** que tiene al lado, en la ficha, en combate o en el
modal de 🐾 Mascotas. El enemigo del bestiario no se toca: solo cambia tu bicho.

**Mascotas de todos.** Quien trajo la mascota (o el DM) puede marcarla como **de todos**
con el botón *Hacerla de todos*. A partir de ahí deja de ser suya: aparece en la ficha de
**cada** jugador bajo *Mascotas del grupo*, y cualquiera de la mesa le maneja la vida, el
focus, la investidura, los estados y el **inventario**, dentro y fuera de combate. Es para
el chull de carga, la carreta del grupo o el animal que compraron entre todos. Tampoco se
le enmascaran los números: si la maneja cualquiera, la ve cualquiera. Con *Hacerla mía*
vuelve a ser privada.

**Recuadros plegables.** El recuadro de vida/focus/estados de cada mascota se pliega con
la flechita **▾/▸** del encabezado; plegado deja una línea con los valores. Anda igual en
la ficha y **en combate** (ahí la fila del turno queda siempre a la vista, porque en D&D
es donde se anota la iniciativa), y es la misma preferencia en los dos lados: se guarda en
el navegador de cada jugador y no afecta a nadie más.

## Objetos e inventario (solo Cosmere)

### Catálogo de objetos (pestaña **Objetos**)

El DM arma su catálogo igual que el bestiario: a mano, **importando desde código**
(YAML), **en bulk** (varias fichas separadas por `---`, o un archivo) y con **exportar**
para hacer backup o pasárselo a otro DM.

> **Ya viene cargado:** el archivo [`static/catalogo_cosmere.yaml`](static/catalogo_cosmere.yaml)
> tiene los **123 objetos del manual** (armas ligeras, pesadas y especiales, armaduras,
> equipo, alojamiento, vehículos y fabriales). Importalo con
> *Objetos → ⭳⭳ Importar en bulk → Cargar archivo*.

Cada objeto es de un **tipo**, y cada tipo se lista con sus propios datos:

| Tipo | Qué guarda |
|---|---|
| **Arma** | clase (ligera/pesada/especial), daño, alcance, habilidad, rasgos y rasgos de experto |
| **Armadura** | deflect, rasgos y rasgos de experto |
| **Equipo** | precio y descripción; se subdivide con categorías (medicina, herramientas, comida…) |
| **Alojamiento** | precio por persona por noche |
| **Vehículo** | tipo, velocidad, alquiler por día y precio de compra |
| **Fabrial** | cargas |

Y en común: precio en marcos, peso, **slots**, **+capacidad**, **dosis/cargas** y
**capacidad de contenedor**.

```yaml
kind: arma
weapon_class: light
name: "Jabalina"
damage: "1d6 keen"
range: "Melee"
traits: "Thrown [30/120]"
expert_traits: "Indirect"
weight: "2 lb."
price: 20
---
kind: equipo
name: "Raciones (5 días)"
categories: [comida]
price: 1
uses: 5           # 5 usos que ocupan 1 solo slot
---
kind: equipo
name: "Mochila"
categories: [generales, contenedores]
price: 8
container: 2      # guarda aparte: 2 slots propios
---
kind: armadura
name: "Placa completa"
deflect: 4
traits: "Cumbersome [5]"
slots: 6
price: 1600
```

El catálogo es **del DM** y se comparte entre sus campañas. Con el botón 🔒 lo marcás como
**oculto**: existe para vos, pero los jugadores ni se enteran de que está (así vienen
cargadas la Hoja Esquirlada, el Moldeador de almas, etc.). En la lista, lo oculto se ve
atenuado y con su etiqueta.

La pestaña **Objetos** se lee **igual que el catálogo de los jugadores** (mismas solapas
por tipo, mismas tablas, mismo filtro por categoría y precio); la única diferencia es la
última columna, que en vez de *Agarrar* trae **🔒 ocultar · editar · ✕**. Y vos ves
también los objetos ocultos, claro.

### Agarrar del catálogo (pestaña **Catálogo** del jugador)

No hay tiendas: los jugadores ven el catálogo entero **con precios** y agarran lo que
quieran. Está separado en **solapas por tipo** (armas, armaduras, equipo, alojamiento,
vehículos, fabriales), y cada tipo se lista **como en el manual**, con su propia tabla:

- **Armas**, partidas en **ligeras**, **pesadas** y **especiales**, con daño, alcance,
  rasgos y rasgos de experto.
- **Armaduras** con su deflect y sus rasgos.
- **Equipo** agrupado por categoría: la comida junta, la medicina junta, las herramientas
  juntas, los contenedores juntos. Un objeto con varias categorías aparece en cada una (la
  mochila está en *generales* y en *contenedores*).
- **Vehículos** con tipo, velocidad, alquiler por día y precio de compra.
- **Alojamiento** con el precio por persona y noche; **fabriales** con sus cargas.

Además del buscador hay **filtro por categoría** (los chips de abajo, con cuántos hay en
cada una) y **por precio** (desde / hasta), que se combinan entre sí. **Limpiar** los
saca todos.

Al tocar **Agarrar** se elige:

- **Cantidad** y **precio por unidad**. El precio viene del catálogo pero **es editable**:
  si el DM les hizo un descuento lo bajás, y si el objeto es un **hallazgo** lo ponés en
  **0** y no se gasta ninguna esfera.
- **Dónde lo ponés**: encima, dentro de la mochila (o de cualquier contenedor), encima de
  una mascota, en tu guardado o en el del grupo.
- **Con qué esferas lo pagás**: dos casilleros, **cargadas** y **opacas**, que se
  completan solos para llegar al total. Por defecto se van primero las opacas (guardarse
  la luz es lo sensato), pero podés repartirlo como quieras.

Lo que el DM marcó como oculto no aparece ni se puede agarrar.

### Dar objetos: el DM

En la pestaña **Jugadores**, con **🎁 Dar objeto** le das algo a un personaje (o a su
mascota) eligiéndolo del catálogo o **creándolo en el momento**, con la opción de
guardarlo también en el catálogo — y sin cobrarle nada. Al lado, el toggle
**✎ Crea objetos** le da permiso a ese jugador para cargarse objetos propios; sin ese
permiso solo recibe o agarra del catálogo pagando.

### Ver y editar los inventarios (pestaña **Inventarios** del DM)

El DM ve de un vistazo **qué lleva cada uno**: lo que tiene encima, lo que va en cada
contenedor, lo de sus mascotas, su guardado personal y el guardado del grupo, cada uno con
su barra de carga. Desde ahí puede **darle** cosas (🎁), **ajustar cantidades** (+ / −) y
**sacarle** lo que sea (✕).

### Inventario y capacidad de carga

Regla opcional del Cosmere RPG. El inventario vive en la pestaña **Mi personaje**, debajo
de las vidas: las tuyas y las de tus mascotas.

```
Capacidad = base por tamaño + Fuerza
Pequeño 4 · Mediano 6 · Grande 10 · Enorme 15 · Gargantuesco 20
```

Esas bases son las del manual, pero **las fija el DM** en *⚙ Ajustes → Capacidad de
carga*: una por tamaño, y valen igual para personajes y mascotas.

Cada objeto ocupa **1 slot**, salvo: el dinero y las cosas insignificantes **0**,
`Cumbersome N` ocupa **1+N**, la Placa y la Hoja Radiante **0**, y los objetos grandes más
de 1 (a criterio del DM, se carga en el objeto). El personaje y **cada mascota** tienen su
propia barra; si te pasás, se marca **Sobrecargado** en rojo pero **no te bloquea** (la
penalización la decide el DM).

**Encima y la mochila.** El inventario viene partido: **🖐 Encima** es lo que llevás en la
mano, y cada **contenedor** (mochila, saco, cofre, la **carreta** del chull) es su propio
bloque con su barra de espacio. Lo que metés adentro ocupa **ese** espacio, no el tuyo.
Con **⇩ Guardar** elegís en qué contenedor va y con **⇧ Sacar** vuelve a tus manos; podés
cargar la carreta del chull con cosas que llevabas encima. Cada mascota tiene el mismo
corte: lo que lleva encima y lo que va en la carreta. Además cada cosa se puede **equipar
o dejar** (🎒): lo que dejaste no cuenta para tu carga.

**Una sola a la vez.** No se puede llevar **más de un contenedor encima**: una mochila, o
una carreta si es una mascota. Tener dos sería espacio infinito gratis. Podés tener las
que quieras en un guardado y cambiarlas cuando quieras.

**Guardados (sin tope).** Además de lo que se carga hay dos depósitos, que representan lo
que tenés pero **no llevás encima**:

- **🏠 Mi guardado** — tuyo: lo que dejaste en la posada, en tu cuarto, en tu casa.
- **👥 Guardado del grupo** — compartido con toda la mesa: cualquiera deja y cualquiera
  saca.

Ninguno de los dos pesa ni tiene límite. Con el botón **⇄** movés cualquier cosa entre las
tres zonas; si movés un contenedor, se va con todo lo que lleva adentro.

**Pasarse cosas.** Con **🤝** le das un objeto a otro personaje de la campaña o a una
mascota tuya. Le queda encima, y si es un contenedor viaja lleno.

**Dosis y cargas.** Los objetos con usos (raciones de 5 días, antisépticos de 5 dosis,
venenos, fabriales con cargas) muestran un contador **− 3/5 +**: con los botones gastás y
reponés de a una, y tocando el número escribís el que quede (media botella, tres
raciones). Ocupan **un solo slot** hasta que se agotan, y ahí desaparecen (si tenías más
de una unidad, arranca la siguiente). El DM tiene el mismo contador en **Inventarios**.

Al **crear** el personaje desde el PDF se importan su equipo y sus armas como objetos, y
sus esferas como marcos. Al **actualizar** el PDF (subir de nivel) el inventario y los
marcos **no se tocan**.

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
- **Defensas a la vista:** al lado de cada barra va la defensa que se le opone —
  🛡 física con la **vida**, cognitiva con el **focus** y espiritual con la
  **investidura**— así no hay que abrir la ficha para resolver un ataque. Sale del
  statblock (enemigos y mascotas) o de la ficha PDF (jugadores); si no está cargada,
  no se muestra nada
- "Marcar como actuado" atenúa la tarjeta; "Siguiente ronda" limpia todas las marcas
- Los enemigos muestran sus acciones y su ficha desplegables (▸ Ver acciones / ▸ Ver ficha).
  Lo que dejes abierto **queda abierto**: pegarle a alguien redibuja las tarjetas pero no
  te cierra lo que estabas leyendo
- **Exhausted** es apilable: click izquierdo agrega una carga, click derecho quita una
- En los controles de stats, **bajar está a la izquierda (−) y subir a la derecha (+)**

### Vista de juego del jugador

- El jugador entra a **Jugar** en una campaña donde fue aceptado. Su personaje es el
  que eligió al aceptar la invitación (no hace falta seleccionarlo cada vez).
- Sobre **su** personaje ve los números exactos y gestiona en vivo su **turno
  (rápido/lento), vida, focus, investidura y estados** (se sincroniza con el DM y el
  resto al instante). Solo puede tocar su propio personaje.
- De los demás (aliados y enemigos) ve **vida, focus e investidura**, cada uno con su
  barra. Cuánto ve de cada stat lo decide el DM en **⚙ Ajustes → Qué ven los jugadores**:
  nada, solo el color, una barra que se vacía, o los números exactos. Por defecto, solo el
  color: la barra queda llena y cambia de color con su etiqueta.
- De los **enemigos** además **no** ve su tier/tipo ni si tomaron turno rápido o lento.
  Los ocultos por el DM no aparecen.
- Su propio personaje y sus mascotas nunca se recortan: esos siempre van con los números.

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

### Calendario rosharano

El DM puede llevar la cuenta de **en qué día están** los jugadores, con el calendario de
Roshar: **10 meses de 10 semanas de 5 días** (500 días por año). Los nombres se arman por
composición, como en el manual: el mes **Jes** tiene la semana **Jesach** (3ª), y su 4º
día es **Jesachev**. Las columnas de la grilla son *Jesel, Nanel, Chachel, Vevel,
Palahel*, y la fecha formal se escribe **año.mes.semana.día** (`1173.1.3.4`).

Viene **apagado**: se prende en *⚙ Ajustes → Módulos → Calendario rosharano*. Cuando está
encendido aparecen, en la barra del paso del tiempo, la **fecha de hoy** y el botón
**📅 Calendario**.

- **El día avanza solo** con el **Descanso largo** y con los botones de pasar días: nunca
  se mueve por su cuenta.
- **📅 Calendario** abre la grilla del mes, con **hoy** marcado, los días pasados en gris
  y los **pines** de cada día. Se navega por mes (`‹ ›`) y por año (`« »`), y **Hoy**
  vuelve al día en curso.
- Al **tocar un día** se ven sus notas y se puede **anotar** (con color de pin). El **DM**
  además puede **marcar ese día como hoy**, para saltar a donde quiera sin pasar días
  uno por uno.
- Cada uno **edita y borra lo suyo**; el DM puede tocar cualquier nota y marcar las suyas
  como **solo yo** (los jugadores no las ven ni les llegan).

En *⚙ Ajustes → Calendario* el DM decide **si los jugadores lo ven**, **si pueden anotar**
y en **qué día están** (mes, semana, día y año). Si lo deja para él solo, a los jugadores
no les aparece el botón y **la fecha ni siquiera les viaja**.

### Pasar varios días de una

Al lado de **+1 día** hay un botón de **avance rápido** que pasa varios días de un saque:
por defecto **5** (una semana rosharana), y el número lo elige el DM en
*⚙ Ajustes → Calendario → Días del botón de avance rápido* (1 a 500). El botón muestra
siempre cuántos días salta (**+5 días**).

Saltar días es exactamente igual que apretar *+1 día* muchas veces: se mueve el
calendario, corre el ciclo de tormentas **día por día** (si en el medio cae una o varias
altas tormentas, se avisa cuántas) y se aplica la descarga de marcos de cada día.

### Panel de ajustes del DM (⚙ Ajustes)

En la vista del DM, el botón **⚙ Ajustes** (arriba a la derecha, al lado de *← Inicio*)
abre el panel de configuración de la campaña, con cinco solapas. Podés moverte entre
ellas sin perder lo que tocaste; se guarda todo junto con **Guardar**.

**Módulos** — qué se usa en esta campaña. Lo que apagues desaparece para todos, y el
backend lo rechaza (no es solo esconder botones):

| Módulo | Qué se lleva si lo apagás |
|---|---|
| **Catálogo de objetos** | tu pestaña *Objetos* y la pestaña *Catálogo* de ellos |
| **Inventario** | lo que lleva cada uno, los guardados, la capacidad de carga y tu pestaña *Inventarios* |
| **Tracker de altas tormentas** | la barra del ciclo y la descarga de marcos por día |
| **Calendario rosharano** | la fecha en la barra, el botón *📅 Calendario* y sus notas (viene apagado) |

El catálogo y el inventario son **independientes**, así que las cuatro combinaciones
sirven:

- **Los dos** — el flujo completo: miran el catálogo, agarran y lo llevan.
- **Solo inventario** — sin catálogo compartido: llevan sus cosas y vos se las das a mano.
- **Solo catálogo** — queda como lista de consulta (qué existe y cuánto vale), sin
  botón *Agarrar* y sin inventarios que llevar.
- **Ninguno** — la campaña no usa objetos.

**Capacidad de carga** — la base de cada tamaño (Pequeño a Gargantuesco). Lo que carga
una criatura es **esa base + su Fuerza**, más lo que sumen mochilas y alforjas; vale igual
para personajes y mascotas, y el campo muestra la cuenta mientras lo editás. Los valores
del manual son 4 · 6 · 10 · 15 · 20: subilos para una mesa menos ajustada, bajalos para
que cada slot importe. La sobrecarga sigue avisando sin bloquear.

**Qué ven los jugadores** — por separado para **enemigos** y para **otros jugadores**, y
por cada stat (vida, focus, investidura):

| Modo | Qué ve el jugador |
|---|---|
| **No lo ven** | nada: ni barra |
| **Solo el color** | la barra queda llena y cambia de color, con su etiqueta (*Malherido*, *A medias*) |
| **Barra que se vacía** | además se acorta a medida que el stat baja |
| **Números exactos** | `23 / 30` |

Más un interruptor para **estados y condiciones** de cada grupo. Su **propio** personaje y
sus mascotas los ven siempre con los números exactos, igual que las **mascotas de todos**
(las maneja cualquiera, así que no tiene sentido ocultárselas).

> Esto se aplica **en el servidor**: lo que no se ve tampoco se manda. En *solo el color*
> viaja únicamente el tramo (0-5), y en *barra que se vacía* un porcentaje redondeado de a
> 5, así que no hay forma de deducir el número exacto mirando la red.

**Tormentas y marcos** — los parámetros de siempre: días mínimo/máximo entre tormentas,
día de inicio y de apagado total de los marcos, **curva de descarga**, y el estado actual
del ciclo (día, día objetivo y momento). Incluye un **preview en vivo**: una mini gráfica
del **% de marcos con luz por día** que se recalcula al mover cualquier valor (es el
esperado; la descarga real es al azar).

**Calendario** — si los jugadores **ven** el calendario, si pueden **anotar** en él,
cuántos **días salta** el botón de avance rápido y **en qué día están** (mes, semana, día
y año), con la fecha de hoy escrita al pie. El interruptor de encendido está en
*Módulos*.

Por defecto: tormenta cada **8–12** días, descarga del día **5** al **15**, curva **2.0**,
salto rápido de **5** días, calendario **apagado** (y arrancando en **1173.1.1.1**), el
resto de los módulos encendidos y todo en **solo el color**. Una campaña sin ajustes
guardados toma estos valores.

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

Para hacer backup, copiá el archivo `cosmere.db`. La base corre en modo **WAL**, así que
además del `.db` pueden existir `cosmere.db-wal` y `cosmere.db-shm`: lo más seguro es
**parar el servidor antes de copiar** (al cerrarse deja todo en el `.db`), o copiar los
tres archivos juntos.

## Tests

Hay una suite de pruebas automatizadas (pytest) que cubre cuentas, campañas, personajes,
bestiario, encuentros, mascotas y combate. Corre siempre contra una base temporal, nunca
toca tu `cosmere.db`:

```bash
pip install -r requirements-dev.txt
pytest -q
```
