/* ═══════════ Módulo de mapa ═══════════
   Lo comparten el panel del DM y el del jugador: las dos páginas montan el
   mismo componente y la API decide qué puede hacer cada uno (`editable`,
   `is_dm`). Ver app/routers/maps.py.

   Cómo funciona el lienzo: la imagen va dentro de un div escalado
   (`transform: translate(...) scale(z)`) y las chinches son hijas de ese div,
   ubicadas en pixeles de la imagen. Como las coordenadas que guarda la API son
   relativas (0..1), pasar de una a otra es multiplicar por el ancho o el alto
   en pixeles, y los puntos sobreviven a que el DM vuelva a subir la lámina en
   otra resolución.

   Las distancias NO se calculan acá: cada vez que cambia la ruta se le
   pregunta al servidor (`/measure`), que es el que sabe de escala y
   velocidades. Así la mesa y la API nunca dicen cosas distintas. */

window.MapaModulo = (function () {
  'use strict';

  var S = {
    el: null, cid: null, api: '', onState: null,
    enabled: false, visible: false, editable: false, isDm: false, unidad: 'km',
    yo: null,          // id del usuario que mira: sus puntos son los que edita
    maps: [], modes: [], cur: null,
    zoom: 1, ox: 0, oy: 0,
    modo: '',          // '' | 'punto' | 'medir' | 'regla'
    ruta: [],          // paradas de la medición: {x, y, name}
    regla: [],         // los dos puntos de la calibración
    sel: null,         // id del punto abierto
    medida: null       // última respuesta de /measure
  };

  var COLORES = ['var(--gold)', 'var(--blue)', 'var(--green)', 'var(--red)',
                 'var(--purple)', 'var(--orange)', 'var(--e-turquesa)', 'var(--e-rosa)'];
  var ICONOS = ['', '🏰', '🏘', '⛰', '🌲', '🌊', '⚔', '💀', '⚓', '🛖', '🕳', '⭐'];
  var ZMIN = 0.05, ZMAX = 24;

  // ── Utilidades ──────────────────────────────────────────

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }
  function escA(s) { return esc(s).replace(/"/g, '&quot;'); }
  function $(id) { return document.getElementById(id); }

  /* Cliente propio: las dos páginas tienen su `api()`, pero una avisa los
     errores y la otra no. Acá se avisan siempre. */
  async function req(path, method, body) {
    var opts = { method: method || 'GET', headers: { 'Content-Type': 'application/json' } };
    if (body) opts.body = JSON.stringify(body);
    var r = await fetch(path, opts);
    if (r.status === 401) { location.href = '/login'; throw new Error('401'); }
    if (!r.ok) {
      var e = await r.json().catch(function () { return { detail: 'Error' }; });
      alert(e.detail || 'Error');
      throw new Error(e.detail || 'error');
    }
    return r.status === 204 ? null : r.json();
  }

  async function subir(path, form) {
    var r = await fetch(path, { method: 'POST', body: form });
    if (r.status === 401) { location.href = '/login'; throw new Error('401'); }
    if (!r.ok) {
      var e = await r.json().catch(function () { return { detail: 'Error' }; });
      alert(e.detail || 'Error');
      throw new Error(e.detail || 'error');
    }
    return r.json();
  }

  /* Números para leer de un vistazo: lo chico con decimal, lo grande redondo. */
  function num(v) {
    v = Number(v) || 0;
    var d = v < 10 ? 2 : (v < 100 ? 1 : 0);
    return v.toLocaleString('es', { minimumFractionDigits: 0, maximumFractionDigits: d });
  }
  function dist(v) { return num(v) + ' ' + S.unidad; }

  /* "4 días" cuando hay jornadas, "6 h" cuando el viaje entra en una. */
  function tiempo(v) {
    if (!v.horas) return '—';
    if (!v.dias_enteros) return num(v.horas) + ' h';
    return v.dias_enteros + (v.dias_enteros === 1 ? ' día' : ' días');
  }

  // ── Armado del DOM ──────────────────────────────────────

  function mount(opts) {
    S.el = typeof opts.el === 'string' ? $(opts.el) : opts.el;
    S.cid = opts.cid;
    S.api = '/api/campaigns/' + opts.cid;
    S.onState = opts.onState || null;
    S.el.className = 'mapmod';
    S.el.innerHTML =
      '<div class="mapmod-bar">'
      + '<select class="mapmod-sel" id="mm-sel"></select>'
      + '<span class="mapmod-scale" id="mm-scale"></span>'
      + '<div class="sep"></div>'
      + '<button class="mapmod-btn" id="mm-punto">📍 Marcar punto</button>'
      + '<button class="mapmod-btn" id="mm-medir">📏 Medir</button>'
      + '<button class="mapmod-btn dm" id="mm-escala">⚖ Escala</button>'
      + '<button class="mapmod-btn dm" id="mm-modes">🐴 Transportes</button>'
      + '<button class="mapmod-btn dm" id="mm-editm">✎ Mapa</button>'
      + '<button class="mapmod-btn dm" id="mm-nuevo">＋ Subir mapa</button>'
      + '</div>'
      + '<div class="mapmod-hint" id="mm-hint"></div>'
      + '<div class="mapmod-stage" id="mm-stage">'
      + '  <div class="mapmod-in" id="mm-in">'
      + '    <img id="mm-img" alt=""/>'
      + '    <svg class="mapmod-svg" id="mm-svg"></svg>'
      + '    <div id="mm-pins"></div>'
      + '  </div>'
      + '  <div class="mapmod-ruler" id="mm-rul"><div class="mapmod-ruler-bar"></div><span></span></div>'
      + '  <div class="mapmod-zoom">'
      + '    <button title="Acercar" id="mm-zin">+</button>'
      + '    <button title="Alejar" id="mm-zout">−</button>'
      + '    <button title="Encajar el mapa" id="mm-zfit">⤢</button>'
      + '  </div>'
      + '  <div class="mapmod-empty" id="mm-empty" style="display:none"></div>'
      + '</div>'
      + '<div class="mapmod-panel" id="mm-panel"></div>';

    $('mm-sel').onchange = function () { abrir(parseInt(this.value)); };
    $('mm-punto').onclick = function () { setModo(S.modo === 'punto' ? '' : 'punto'); };
    $('mm-medir').onclick = function () { setModo(S.modo === 'medir' ? '' : 'medir'); };
    $('mm-escala').onclick = modalEscala;
    $('mm-modes').onclick = modalModes;
    $('mm-editm').onclick = modalEditarMapa;
    $('mm-nuevo').onclick = modalNuevoMapa;
    $('mm-zin').onclick = function () { zoomA(S.zoom * 1.4); };
    $('mm-zout').onclick = function () { zoomA(S.zoom / 1.4); };
    $('mm-zfit').onclick = encajar;
    lienzoEventos();
    window.addEventListener('resize', function () { if (S.cur) pintarRegla(); });
    return recargar();
  }

  // ── Carga ───────────────────────────────────────────────

  async function recargar() {
    var d;
    try { d = await req(S.api + '/maps'); } catch (e) { return; }
    S.enabled = !!d.enabled; S.visible = !!d.visible; S.editable = !!d.editable;
    S.isDm = !!d.is_dm; S.unidad = d.unidad || 'km'; S.yo = d.user_id;
    S.maps = d.maps || []; S.modes = d.modes || [];
    if (S.onState) S.onState({ enabled: S.enabled, visible: S.visible, editable: S.editable });
    if (!S.enabled || !S.visible) { S.cur = null; pintarVacio(); return; }
    // se queda en el mapa que ya estaba mirando, si sigue existiendo
    var quiere = S.cur && S.maps.some(function (m) { return m.id === S.cur.id; })
      ? S.cur.id : (S.maps[0] && S.maps[0].id);
    pintarSelector(quiere);
    if (quiere) await abrir(quiere, true);
    else { S.cur = null; pintarVacio(); }
  }

  function pintarSelector(activo) {
    var s = $('mm-sel');
    s.innerHTML = S.maps.map(function (m) {
      return '<option value="' + m.id + '"' + (m.id === activo ? ' selected' : '') + '>'
        + esc(m.name) + (m.secreto ? ' 🔒' : '')
        + (m.n_puntos ? ' · ' + m.n_puntos + ' pt' : '') + '</option>';
    }).join('');
    s.style.display = S.maps.length > 1 ? '' : (S.maps.length ? '' : 'none');
    S.el.querySelectorAll('.mapmod-btn.dm').forEach(function (b) {
      b.style.display = S.isDm ? '' : 'none';
    });
    var hayMapa = !!S.maps.length;
    $('mm-punto').style.display = (hayMapa && S.editable) ? '' : 'none';
    $('mm-medir').style.display = hayMapa ? '' : 'none';
    $('mm-escala').style.display = (hayMapa && S.isDm) ? '' : 'none';
    $('mm-editm').style.display = (hayMapa && S.isDm) ? '' : 'none';
  }

  function pintarVacio() {
    $('mm-in').style.display = 'none';
    $('mm-rul').style.display = 'none';
    S.el.querySelector('.mapmod-zoom').style.display = 'none';
    $('mm-panel').innerHTML = '';
    var e = $('mm-empty');
    e.style.display = '';
    e.textContent = !S.enabled
      ? 'El módulo de mapa está apagado en esta campaña.'
      : (!S.visible ? 'El DM todavía no comparte los mapas.'
        : (S.isDm ? 'Todavía no subiste ningún mapa. Tocá “Subir mapa”.'
          : 'El DM todavía no subió ningún mapa.'));
    pintarSelector(null);
  }

  async function abrir(mid, mantenerVista) {
    var d;
    try { d = await req(S.api + '/maps/' + mid); } catch (e) { return; }
    var mismo = S.cur && S.cur.id === d.id;
    S.cur = d; S.editable = !!d.editable; S.isDm = !!d.is_dm; S.yo = d.user_id;
    S.modes = d.modes || []; S.unidad = d.unidad || S.unidad;
    S.sel = null; S.ruta = []; S.regla = []; S.medida = null;
    $('mm-empty').style.display = 'none';
    $('mm-in').style.display = '';
    $('mm-rul').style.display = '';
    S.el.querySelector('.mapmod-zoom').style.display = '';
    var img = $('mm-img');
    img.src = S.api + '/maps/' + mid + '/image?v=' + Date.now();
    $('mm-in').style.width = d.img_w + 'px';
    $('mm-in').style.height = d.img_h + 'px';
    $('mm-svg').setAttribute('viewBox', '0 0 ' + d.img_w + ' ' + d.img_h);
    if (!(mantenerVista && mismo)) encajar();
    setModo('');
    pintar();
  }

  // ── Vista: zoom y arrastre ──────────────────────────────

  function aplicar() {
    $('mm-in').style.transform = 'translate(' + S.ox + 'px,' + S.oy + 'px) scale(' + S.zoom + ')';
    S.el.querySelectorAll('.mapmod-pin').forEach(function (p) {
      p.style.transform = 'translate(-50%,-100%) scale(' + (1 / S.zoom) + ')';
    });
    pintarRegla();
  }

  function encajar() {
    if (!S.cur) return;
    var w = $('mm-stage').clientWidth, h = $('mm-stage').clientHeight;
    S.zoom = Math.min(w / S.cur.img_w, h / S.cur.img_h) || 1;
    S.ox = (w - S.cur.img_w * S.zoom) / 2;
    S.oy = (h - S.cur.img_h * S.zoom) / 2;
    aplicar();
  }

  function zoomA(z, cx, cy) {
    if (!S.cur) return;
    var st = $('mm-stage');
    if (cx == null) { cx = st.clientWidth / 2; cy = st.clientHeight / 2; }
    z = Math.max(ZMIN, Math.min(ZMAX, z));
    // el punto del mapa que está bajo el cursor no se mueve
    S.ox = cx - (cx - S.ox) * (z / S.zoom);
    S.oy = cy - (cy - S.oy) * (z / S.zoom);
    S.zoom = z;
    aplicar();
  }

  function lienzoEventos() {
    var st = $('mm-stage');
    var punteros = {}, arrastre = null, pinch = null;

    /* La rueda pelada desplaza la página: si el mapa se la comiera, con el
       lienzo ocupando media pantalla no habría forma de bajar. El zoom va con
       Ctrl (o ⌘) apretado, que es además lo que manda el pellizco del trackpad,
       así que en una laptop se siente natural. Y siempre están los botones. */
    st.addEventListener('wheel', function (e) {
      if (!S.cur) return;
      if (!e.ctrlKey && !e.metaKey) { avisoRueda(); return; }
      e.preventDefault();
      var r = st.getBoundingClientRect();
      zoomA(S.zoom * (e.deltaY < 0 ? 1.18 : 1 / 1.18), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });

    st.addEventListener('pointerdown', function (e) {
      if (!S.cur) return;
      punteros[e.pointerId] = { x: e.clientX, y: e.clientY };
      var ids = Object.keys(punteros);
      if (ids.length === 2) {          // dos dedos: pellizco
        arrastre = null;
        pinch = { d: sep(punteros, ids), z: S.zoom };
        return;
      }
      if (e.target.closest('.mapmod-pin')) return;   // la chinche maneja su click
      st.setPointerCapture(e.pointerId);
      arrastre = { x: e.clientX, y: e.clientY, ox: S.ox, oy: S.oy, mov: 0, t: Date.now() };
      st.classList.add('grabbing');
    });

    st.addEventListener('pointermove', function (e) {
      if (!punteros[e.pointerId]) return;
      punteros[e.pointerId] = { x: e.clientX, y: e.clientY };
      var ids = Object.keys(punteros);
      if (pinch && ids.length === 2) {
        var r = st.getBoundingClientRect();
        var c = centro(punteros, ids);
        zoomA(pinch.z * (sep(punteros, ids) / pinch.d), c.x - r.left, c.y - r.top);
        return;
      }
      if (!arrastre) return;
      var dx = e.clientX - arrastre.x, dy = e.clientY - arrastre.y;
      arrastre.mov = Math.max(arrastre.mov, Math.abs(dx) + Math.abs(dy));
      S.ox = arrastre.ox + dx; S.oy = arrastre.oy + dy;
      aplicar();
    });

    function soltar(e) {
      delete punteros[e.pointerId];
      if (Object.keys(punteros).length < 2) pinch = null;
      if (!arrastre) return;
      st.classList.remove('grabbing');
      var quieto = arrastre.mov < 5;
      arrastre = null;
      // un click limpio (sin arrastrar) es lo que marca puntos y paradas
      if (quieto && S.modo) clickEnMapa(e);
    }
    st.addEventListener('pointerup', soltar);
    st.addEventListener('pointercancel', soltar);
  }

  /* La primera vez que alguien gira la rueda encima del mapa, contarle cómo se
     hace zoom. Se borra solo para no quedar pegado en la barra de ayuda. */
  var avisoTimer = null;
  function avisoRueda() {
    if (S.modo) return;                   // en medio de marcar o medir, no molestar
    var h = $('mm-hint');
    h.innerHTML = 'Para acercar el mapa: <b>Ctrl + rueda</b> (o los botones + / −). '
      + 'La rueda sola mueve la página.';
    clearTimeout(avisoTimer);
    avisoTimer = setTimeout(function () { if (!S.modo) setModo(''); }, 3500);
  }

  function sep(p, ids) {
    return Math.hypot(p[ids[0]].x - p[ids[1]].x, p[ids[0]].y - p[ids[1]].y) || 1;
  }
  function centro(p, ids) {
    return { x: (p[ids[0]].x + p[ids[1]].x) / 2, y: (p[ids[0]].y + p[ids[1]].y) / 2 };
  }

  /* Pantalla → coordenadas relativas del mapa (lo que guarda la API). */
  function aRelativo(e) {
    var r = $('mm-stage').getBoundingClientRect();
    var px = (e.clientX - r.left - S.ox) / S.zoom;
    var py = (e.clientY - r.top - S.oy) / S.zoom;
    return { x: Math.max(0, Math.min(1, px / S.cur.img_w)),
             y: Math.max(0, Math.min(1, py / S.cur.img_h)) };
  }

  function clickEnMapa(e) {
    var c = aRelativo(e);
    if (S.modo === 'punto') { modalPunto(null, c); return; }
    if (S.modo === 'medir') { S.ruta.push({ x: c.x, y: c.y, name: '' }); medir(); return; }
    if (S.modo === 'regla') {
      S.regla.push(c);
      if (S.regla.length === 2) { pintar(); pedirMedidaRegla(); }
      else pintar();
    }
  }

  // ── Modos ───────────────────────────────────────────────

  function setModo(m) {
    S.modo = m;
    if (m !== 'medir') { S.ruta = []; S.medida = null; }
    if (m !== 'regla') S.regla = [];
    $('mm-punto').classList.toggle('on', m === 'punto');
    $('mm-medir').classList.toggle('on', m === 'medir');
    $('mm-stage').classList.toggle('picking', !!m);
    $('mm-hint').innerHTML =
      m === 'punto' ? 'Tocá el mapa donde quieras clavar el punto.'
        : m === 'medir' ? 'Tocá puntos del mapa (o chinches ya marcadas) para armar el recorrido. <b>Cada parada suma un tramo.</b>'
          : m === 'regla' ? 'Marcá los dos extremos de algo que sepas cuánto mide: la escala del mapa, o dos ciudades a distancia conocida.'
            : (S.cur && !S.cur.escalado
              ? 'Este mapa todavía no tiene escala. ' + (S.isDm ? 'Ponésela en <b>Escala</b> y ahí se puede medir.' : 'El DM todavía no le puso escala.')
              : '');
    pintar();
  }

  // ── Pintado ─────────────────────────────────────────────

  function pintar() {
    if (!S.cur) return;
    pintarPins();
    pintarSvg();
    pintarEscalaChip();
    pintarPanel();
    aplicar();
  }

  function pintarEscalaChip() {
    var m = S.cur;
    $('mm-scale').innerHTML = m.escalado
      ? '<b>' + num(m.ancho_real) + ' × ' + num(m.alto_real) + ' ' + S.unidad + '</b>'
      : '<b>sin escala</b>';
  }

  function pintarPins() {
    var m = S.cur, cont = $('mm-pins');
    var enRuta = {};
    S.ruta.forEach(function (p, i) { if (p.id) enRuta[p.id] = i + 1; });
    cont.innerHTML = (m.points || []).map(function (p) {
      var col = p.color || 'var(--gold)';
      return '<div class="mapmod-pin' + (p.secreto ? ' secreto' : '')
        + (S.sel === p.id ? ' sel' : '') + (enRuta[p.id] ? ' paso' : '')
        + '" data-pid="' + p.id + '" style="left:' + (p.x * m.img_w) + 'px;top:'
        + (p.y * m.img_h) + 'px">'
        + '<div class="mapmod-pin-dot" style="background:' + escA(col) + '">'
        + (p.icono ? '<span class="mapmod-pin-ico">' + esc(p.icono) + '</span>' : '')
        + '</div>'
        + '<div class="mapmod-pin-lbl">' + (enRuta[p.id] ? enRuta[p.id] + '· ' : '')
        + esc(p.name) + '</div>'
        + '</div>';
    }).join('');
    cont.querySelectorAll('.mapmod-pin').forEach(function (el) {
      el.onclick = function (ev) {
        ev.stopPropagation();
        clickPin(parseInt(el.dataset.pid));
      };
    });
  }

  function punto(pid) {
    return (S.cur.points || []).filter(function (p) { return p.id === pid; })[0];
  }

  function clickPin(pid) {
    var p = punto(pid);
    if (!p) return;
    if (S.modo === 'medir') {
      S.ruta.push({ x: p.x, y: p.y, name: p.name, id: p.id });
      medir();
      return;
    }
    S.sel = S.sel === pid ? null : pid;
    pintar();
  }

  /* Líneas de la medición y de la regla. `non-scaling-stroke` mantiene el
     grosor en pantalla aunque el mapa esté muy acercado. */
  function pintarSvg() {
    var m = S.cur, out = '';
    function pts(arr) {
      return arr.map(function (p) { return (p.x * m.img_w) + ',' + (p.y * m.img_h); }).join(' ');
    }
    if (S.ruta.length > 1) {
      out += '<polyline points="' + pts(S.ruta) + '" fill="none" stroke="var(--gold)" '
        + 'stroke-width="2" stroke-dasharray="7 5" vector-effect="non-scaling-stroke"/>';
    }
    S.ruta.forEach(function (p, i) {
      if (p.id) return;    // las chinches ya se dibujan solas
      out += '<circle cx="' + (p.x * m.img_w) + '" cy="' + (p.y * m.img_h) + '" r="5" '
        + 'fill="var(--gold)" stroke="var(--bg)" stroke-width="1.5" '
        + 'vector-effect="non-scaling-stroke" style="r:5px"/>';
    });
    if (S.regla.length) {
      if (S.regla.length === 2) {
        out += '<line x1="' + (S.regla[0].x * m.img_w) + '" y1="' + (S.regla[0].y * m.img_h)
          + '" x2="' + (S.regla[1].x * m.img_w) + '" y2="' + (S.regla[1].y * m.img_h)
          + '" stroke="var(--blue)" stroke-width="2" vector-effect="non-scaling-stroke"/>';
      }
      S.regla.forEach(function (p) {
        out += '<circle cx="' + (p.x * m.img_w) + '" cy="' + (p.y * m.img_h) + '" r="5" '
          + 'fill="var(--blue)" stroke="var(--bg)" stroke-width="1.5" '
          + 'vector-effect="non-scaling-stroke"/>';
      });
    }
    $('mm-svg').innerHTML = out;
  }

  /* Regla de escala del lienzo: un largo redondo (1, 2 o 5 por diez elevado a
     lo que haga falta) que en pantalla mida más o menos 120 px. */
  function pintarRegla() {
    var r = $('mm-rul');
    if (!S.cur || !S.cur.escalado) { r.style.display = 'none'; return; }
    r.style.display = '';
    var porPx = S.cur.escala / S.zoom;              // unidades por pixel de pantalla
    var crudo = porPx * 120;
    var exp = Math.pow(10, Math.floor(Math.log(crudo) / Math.LN10));
    var mant = crudo / exp;
    var lindo = (mant >= 5 ? 5 : (mant >= 2 ? 2 : 1)) * exp;
    r.querySelector('.mapmod-ruler-bar').style.width = (lindo / porPx) + 'px';
    r.querySelector('span').textContent = num(lindo) + ' ' + S.unidad;
  }

  // ── Panel de abajo ──────────────────────────────────────

  function pintarPanel() {
    var h = '';
    if (S.modo === 'medir') h += cardMedicion();
    if (S.sel) h += cardPunto(punto(S.sel));
    h += cardPuntos();
    $('mm-panel').innerHTML = h;
    var b;
    if ((b = $('mm-ruta-clear'))) b.onclick = function () { S.ruta = []; S.medida = null; pintar(); };
    if ((b = $('mm-ruta-undo'))) b.onclick = function () { S.ruta.pop(); medir(); };
    if ((b = $('mm-pt-edit'))) b.onclick = function () { modalPunto(punto(S.sel)); };
    if ((b = $('mm-pt-del'))) b.onclick = borrarPunto;
    if ((b = $('mm-pt-medir'))) b.onclick = function () {
      var p = punto(S.sel);
      setModo('medir');
      S.ruta = [{ x: p.x, y: p.y, name: p.name, id: p.id }];
      pintar();
    };
    $('mm-panel').querySelectorAll('.mapmod-prow').forEach(function (el) {
      el.onclick = function () { clickPin(parseInt(el.dataset.pid)); };
    });
  }

  function cardMedicion() {
    var h = '<div class="mapmod-card"><h4>Recorrido</h4>';
    if (S.ruta.length < 2) {
      h += '<div class="mapmod-note">Marcá al menos dos paradas.</div>';
    } else if (!S.medida) {
      h += '<div class="mapmod-note">Midiendo…</div>';
    } else {
      h += '<div class="mapmod-dist">' + num(S.medida.distancia)
        + ' <small>' + esc(S.unidad) + '</small></div>';
      if (S.medida.tramos.length > 1) {
        h += '<div class="mapmod-legs">' + S.medida.tramos.map(function (t, i) {
          return (i + 1) + '. ' + dist(t.distancia);
        }).join(' · ') + '</div>';
      }
      h += '<div style="margin-top:10px">' + (S.medida.viajes || []).map(function (v) {
        return '<div class="mapmod-trip">'
          + '<span class="mapmod-trip-n">' + esc(v.icono || '') + ' ' + esc(v.name) + '</span>'
          + '<span class="mapmod-trip-d">' + tiempo(v) + '</span>'
          + '<span class="mapmod-trip-h">' + num(v.horas) + ' h · ' + num(v.velocidad)
          + ' ' + esc(S.unidad) + '/h</span></div>';
      }).join('') + '</div>';
      if (!(S.medida.viajes || []).length) {
        h += '<div class="mapmod-note">No hay transportes cargados'
          + (S.isDm ? ': agregalos en “Transportes”.' : '.') + '</div>';
      }
    }
    h += '<div class="mapmod-f" style="margin-top:10px"><div class="acts">'
      + '<button class="mapmod-btn" id="mm-ruta-undo">↶ Sacar la última</button>'
      + '<button class="mapmod-btn" id="mm-ruta-clear">Limpiar</button>'
      + '</div></div></div>';
    return h;
  }

  function cardPunto(p) {
    if (!p) return '';
    // Un jugador solo toca lo suyo (el servidor lo exige igual): sin esto, el
    // botón estaría ahí para pinchar y comerse un 403.
    var puedo = S.isDm || (S.editable && p.user_id === S.yo);
    var h = '<div class="mapmod-card"><h4>' + esc(p.icono || '📍') + ' ' + esc(p.name)
      + (p.secreto ? ' 🔒' : '') + '</h4>';
    h += p.descripcion
      ? '<div style="font-size:14px;white-space:pre-wrap">' + esc(p.descripcion) + '</div>'
      : '<div class="mapmod-note">Sin descripción.</div>';
    h += '<div class="mapmod-f" style="margin-top:10px"><div class="acts">'
      + (S.cur.escalado ? '<button class="mapmod-btn" id="mm-pt-medir">📏 Medir desde acá</button>' : '')
      + (puedo ? '<button class="mapmod-btn" id="mm-pt-edit">✎ Editar</button>'
        + '<button class="mapmod-btn danger" id="mm-pt-del">Borrar</button>' : '')
      + '</div></div></div>';
    return h;
  }

  function cardPuntos() {
    var ps = S.cur.points || [];
    var h = '<div class="mapmod-card"><h4>Puntos · ' + ps.length + '</h4>';
    if (!ps.length) {
      h += '<div class="mapmod-note">Todavía no hay puntos marcados.</div>';
    } else {
      h += '<div class="mapmod-plist">' + ps.map(function (p) {
        return '<div class="mapmod-prow' + (S.sel === p.id ? ' sel' : '') + '" data-pid="' + p.id + '">'
          + '<span class="mapmod-prow-c" style="background:' + escA(p.color || 'var(--gold)') + '"></span>'
          + '<span class="mapmod-prow-n">' + esc(p.icono ? p.icono + ' ' : '') + esc(p.name) + '</span>'
          + (p.secreto ? '<span class="mapmod-prow-s">🔒 secreto</span>' : '')
          + '</div>';
      }).join('') + '</div>';
    }
    return h + '</div>';
  }

  // ── Medir contra el servidor ────────────────────────────

  var medirTimer = null;
  function medir() {
    S.medida = null;
    pintar();
    clearTimeout(medirTimer);
    if (S.ruta.length < 2) return;
    medirTimer = setTimeout(async function () {
      var pedido = S.ruta.map(function (p) { return { x: p.x, y: p.y }; });
      var d;
      try {
        d = await req(S.api + '/maps/' + S.cur.id + '/measure', 'POST', { puntos: pedido });
      } catch (e) { return; }
      // el servidor no conoce los nombres de las paradas sueltas: los ponemos acá
      d.tramos.forEach(function (t, i) {
        t.desde = S.ruta[i].name; t.hasta = S.ruta[i + 1].name;
      });
      S.medida = d;
      pintar();
    }, 120);
  }

  // ── Modales ─────────────────────────────────────────────

  function abrirModal(html, ancho) {
    var m = $('modal');
    m.classList.toggle('wide', !!ancho);
    m.innerHTML = '<button class="modal-close" onclick="closeModal()">✕</button>' + html;
    $('overlay').classList.add('on');
  }
  function cerrar() { if (window.closeModal) window.closeModal(); }

  function selColores(actual) {
    return '<div class="mapmod-colors" id="mm-cols">' + COLORES.map(function (c) {
      return '<span data-c="' + escA(c) + '" class="' + (c === actual ? 'on' : '')
        + '" style="background:' + escA(c) + '"></span>';
    }).join('') + '</div>';
  }
  function engancharColores() {
    var cont = $('mm-cols');
    if (!cont) return;
    cont.querySelectorAll('span').forEach(function (s) {
      s.onclick = function () {
        cont.querySelectorAll('span').forEach(function (x) { x.classList.remove('on'); });
        s.classList.add('on');
      };
    });
  }
  function colorElegido() {
    var s = $('mm-cols') && $('mm-cols').querySelector('span.on');
    return s ? s.dataset.c : '';
  }

  function modalPunto(p, coord) {
    var esNuevo = !p;
    abrirModal(
      '<h3 style="font-family:var(--font-h);color:var(--gold)">'
      + (esNuevo ? 'Nuevo punto' : 'Editar punto') + '</h3>'
      + '<div class="mapmod-f">'
      + '<div><label>Nombre</label><input type="text" id="mm-p-name" value="'
      + escA(p ? p.name : '') + '" placeholder="Kholinar"/></div>'
      + '<div><label>Descripción</label><textarea id="mm-p-desc" '
      + 'placeholder="Qué hay acá, quién manda, qué pasó la última vez…">'
      + esc(p ? p.descripcion : '') + '</textarea></div>'
      + '<div class="r2">'
      + '<div><label>Ícono</label><select id="mm-p-ico">' + ICONOS.map(function (i) {
        return '<option value="' + i + '"' + (p && p.icono === i ? ' selected' : '') + '>'
          + (i || '— sin ícono —') + '</option>';
      }).join('') + '</select></div>'
      + '<div><label>Color</label>' + selColores(p ? p.color : COLORES[0]) + '</div>'
      + '</div>'
      + (S.isDm ? '<label class="chk"><input type="checkbox" id="mm-p-sec"'
        + (p && p.secreto ? ' checked' : '') + '/> Secreto: solo lo ves vos</label>' : '')
      + '<div class="acts"><button class="mapmod-btn" onclick="closeModal()">Cancelar</button>'
      + '<button class="mapmod-btn on" id="mm-p-ok">Guardar</button></div>'
      + '</div>');
    engancharColores();
    $('mm-p-name').focus();
    $('mm-p-ok').onclick = async function () {
      var body = {
        name: $('mm-p-name').value.trim(),
        descripcion: $('mm-p-desc').value,
        icono: $('mm-p-ico').value,
        color: colorElegido(),
        secreto: !!($('mm-p-sec') && $('mm-p-sec').checked)
      };
      if (!body.name) { alert('Ponele un nombre al punto'); return; }
      if (esNuevo) { body.x = coord.x; body.y = coord.y; }
      var url = S.api + '/maps/' + S.cur.id + '/points' + (esNuevo ? '' : '/' + p.id);
      var d;
      try { d = await req(url, esNuevo ? 'POST' : 'PUT', body); } catch (e) { return; }
      S.cur.points = d.points;
      if (esNuevo && d.point) S.sel = d.point.id;
      cerrar(); setModo(''); pintar();
    };
  }

  async function borrarPunto() {
    var p = punto(S.sel);
    if (!p || !confirm('¿Borrar “' + p.name + '”?')) return;
    var d;
    try { d = await req(S.api + '/maps/' + S.cur.id + '/points/' + p.id, 'DELETE'); }
    catch (e) { return; }
    S.cur.points = d.points; S.sel = null; pintar();
  }

  function modalNuevoMapa() {
    abrirModal(
      '<h3 style="font-family:var(--font-h);color:var(--gold)">Subir un mapa</h3>'
      + '<div class="mapmod-f">'
      + '<div class="mapmod-note">La imagen puede ser PNG, JPG, GIF o WEBP. Las medidas '
      + 'de la lámina las lee el servidor; vos solo decís cuánto mide el mapa en el mundo.</div>'
      + '<div><label>Imagen</label><input type="file" id="mm-m-file" accept="image/*"/></div>'
      + '<div><label>Nombre</label><input type="text" id="mm-m-name" placeholder="Roshar"/></div>'
      + '<div><label>Descripción</label><textarea id="mm-m-desc"></textarea></div>'
      + '<div><label>Ancho del mapa, de lado a lado (' + esc(S.unidad) + ')</label>'
      + '<input type="number" id="mm-m-w" min="0" step="any" placeholder="4000"/>'
      + '<div class="mapmod-note">Si no lo sabés, dejalo vacío y después calibralo '
      + 'con la regla desde “Escala”.</div></div>'
      + '<label class="chk"><input type="checkbox" id="mm-m-sec"/> Secreto: solo lo ves vos</label>'
      + '<div class="acts"><button class="mapmod-btn" onclick="closeModal()">Cancelar</button>'
      + '<button class="mapmod-btn on" id="mm-m-ok">Subir</button></div>'
      + '</div>');
    $('mm-m-ok').onclick = async function () {
      var f = $('mm-m-file').files[0];
      if (!f) { alert('Elegí una imagen'); return; }
      var fd = new FormData();
      fd.append('file', f);
      fd.append('name', $('mm-m-name').value.trim());
      fd.append('descripcion', $('mm-m-desc').value);
      fd.append('ancho_real', $('mm-m-w').value || '0');
      fd.append('secreto', $('mm-m-sec').checked ? 'true' : 'false');
      this.disabled = true;
      var d;
      try { d = await subir(S.api + '/maps', fd); }
      catch (e) { this.disabled = false; return; }
      cerrar();
      S.cur = { id: d.map.id };     // para que recargar() lo deje abierto
      await recargar();
    };
  }

  function modalEditarMapa() {
    var m = S.cur;
    abrirModal(
      '<h3 style="font-family:var(--font-h);color:var(--gold)">Mapa</h3>'
      + '<div class="mapmod-f">'
      + '<div><label>Nombre</label><input type="text" id="mm-e-name" value="'
      + escA(m.name) + '"/></div>'
      + '<div><label>Descripción</label><textarea id="mm-e-desc">' + esc(m.descripcion)
      + '</textarea></div>'
      + '<label class="chk"><input type="checkbox" id="mm-e-sec"' + (m.secreto ? ' checked' : '')
      + '/> Secreto: solo lo ves vos</label>'
      + '<div><label>Cambiar la imagen</label><input type="file" id="mm-e-file" accept="image/*"/>'
      + '<div class="mapmod-note">Los puntos se quedan donde están: sus coordenadas son '
      + 'relativas a la lámina, no pixeles fijos.</div></div>'
      + '<div class="acts">'
      + '<button class="mapmod-btn danger" id="mm-e-del">Borrar el mapa</button>'
      + '<button class="mapmod-btn" onclick="closeModal()">Cancelar</button>'
      + '<button class="mapmod-btn on" id="mm-e-ok">Guardar</button></div>'
      + '</div>');
    $('mm-e-ok').onclick = async function () {
      this.disabled = true;
      try {
        await req(S.api + '/maps/' + m.id, 'PUT', {
          name: $('mm-e-name').value.trim(),
          descripcion: $('mm-e-desc').value,
          secreto: $('mm-e-sec').checked
        });
        var f = $('mm-e-file').files[0];
        if (f) {
          var fd = new FormData(); fd.append('file', f);
          await subir(S.api + '/maps/' + m.id + '/image', fd);
        }
      } catch (e) { this.disabled = false; return; }
      cerrar(); await recargar();
    };
    $('mm-e-del').onclick = async function () {
      if (!confirm('¿Borrar el mapa “' + m.name + '” y todos sus puntos?')) return;
      try { await req(S.api + '/maps/' + m.id, 'DELETE'); } catch (e) { return; }
      cerrar(); S.cur = null; await recargar();
    };
  }

  function modalEscala() {
    var m = S.cur;
    abrirModal(
      '<h3 style="font-family:var(--font-h);color:var(--gold)">Escala del mapa</h3>'
      + '<div class="mapmod-f">'
      + '<div class="mapmod-note">La lámina mide ' + m.img_w + ' × ' + m.img_h
      + ' px. Con el ancho real alcanza: el alto sale solo, porque los pixeles son cuadrados.</div>'
      + '<div><label>Ancho del mapa, de lado a lado (' + esc(S.unidad) + ')</label>'
      + '<input type="number" id="mm-s-w" min="0" step="any" value="'
      + (m.ancho_real || '') + '"/></div>'
      + (m.escalado ? '<div class="mapmod-note">Así queda: <b>' + num(m.ancho_real) + ' × '
        + num(m.alto_real) + ' ' + esc(S.unidad) + '</b>, a ' + num(m.escala) + ' '
        + esc(S.unidad) + ' por pixel.</div>' : '')
      + '<div class="acts">'
      + '<button class="mapmod-btn" id="mm-s-regla">📐 Calibrar con una regla</button>'
      + '<button class="mapmod-btn" onclick="closeModal()">Cancelar</button>'
      + '<button class="mapmod-btn on" id="mm-s-ok">Guardar</button></div>'
      + '</div>');
    $('mm-s-ok').onclick = async function () {
      var d;
      try {
        d = await req(S.api + '/maps/' + m.id, 'PUT',
                      { ancho_real: parseFloat($('mm-s-w').value) || 0 });
      } catch (e) { return; }
      Object.assign(S.cur, d.map);
      cerrar(); setModo(''); pintar();
    };
    $('mm-s-regla').onclick = function () { cerrar(); setModo('regla'); };
  }

  /* Segundo paso de la calibración: ya están los dos extremos, falta cuánto
     mide el tramo. El servidor despeja el ancho del mapa. */
  function pedirMedidaRegla() {
    abrirModal(
      '<h3 style="font-family:var(--font-h);color:var(--gold)">¿Cuánto mide ese tramo?</h3>'
      + '<div class="mapmod-f">'
      + '<div><label>Distancia entre los dos puntos (' + esc(S.unidad) + ')</label>'
      + '<input type="number" id="mm-r-d" min="0" step="any" placeholder="500"/></div>'
      + '<div class="acts">'
      + '<button class="mapmod-btn" id="mm-r-otra">Marcar de nuevo</button>'
      + '<button class="mapmod-btn on" id="mm-r-ok">Calibrar</button></div>'
      + '</div>');
    $('mm-r-d').focus();
    $('mm-r-otra').onclick = function () { cerrar(); S.regla = []; setModo('regla'); };
    $('mm-r-ok').onclick = async function () {
      var v = parseFloat($('mm-r-d').value);
      if (!(v > 0)) { alert('Escribí cuánto mide el tramo'); return; }
      var d;
      try {
        d = await req(S.api + '/maps/' + S.cur.id + '/calibrate', 'POST', {
          x1: S.regla[0].x, y1: S.regla[0].y, x2: S.regla[1].x, y2: S.regla[1].y,
          distancia: v
        });
      } catch (e) { return; }
      Object.assign(S.cur, d.map);
      cerrar(); setModo('');
      alert('Listo: el mapa mide ' + num(d.map.ancho_real) + ' × ' + num(d.map.alto_real)
        + ' ' + S.unidad + '.');
    };
  }

  function modalModes() {
    var filas = S.modes.map(function (m) {
      return '<tr data-tid="' + m.id + '">'
        + '<td><input class="mm-t-ico w-ico" value="' + escA(m.icono) + '"/></td>'
        + '<td><input class="mm-t-name" value="' + escA(m.name) + '"/></td>'
        + '<td><input class="mm-t-vel w-num" type="number" step="any" min="0" value="'
        + m.velocidad + '"/></td>'
        + '<td><input class="mm-t-hd w-num" type="number" step="any" min="0" max="24" value="'
        + m.horas_dia + '"/></td>'
        + '<td class="w-act"><button class="mapmod-btn danger mm-t-del">✕</button></td></tr>';
    }).join('');
    abrirModal(
      '<h3 style="font-family:var(--font-h);color:var(--gold)">Medios de transporte</h3>'
      + '<div class="mapmod-note" style="margin-top:6px">La velocidad va en '
      + esc(S.unidad) + ' por hora y las horas son de marcha por jornada: con eso se '
      + 'calcula en cuántos días se hace el camino. Los cambios se guardan al tocar Guardar.</div>'
      + '<div class="mapmod-f">'
      + '<table class="mapmod-modes"><thead><tr><th></th><th>Transporte</th>'
      + '<th>' + esc(S.unidad) + '/h</th><th>h/día</th><th></th></tr></thead>'
      + '<tbody id="mm-t-body">' + filas + '</tbody></table>'
      + '<div class="acts">'
      + '<button class="mapmod-btn" id="mm-t-add">＋ Agregar</button>'
      + '<button class="mapmod-btn" id="mm-t-def">Sugeridos</button>'
      + '<button class="mapmod-btn on" id="mm-t-ok">Guardar</button></div>'
      + '</div>', true);

    function engancharBorrar() {
      $('mm-t-body').querySelectorAll('.mm-t-del').forEach(function (b) {
        b.onclick = function () { b.closest('tr').remove(); };
      });
    }
    engancharBorrar();
    $('mm-t-add').onclick = function () {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td><input class="mm-t-ico w-ico" placeholder="🐴"/></td>'
        + '<td><input class="mm-t-name" placeholder="A caballo"/></td>'
        + '<td><input class="mm-t-vel w-num" type="number" step="any" min="0" value="8"/></td>'
        + '<td><input class="mm-t-hd w-num" type="number" step="any" min="0" max="24" value="8"/></td>'
        + '<td class="w-act"><button class="mapmod-btn danger mm-t-del">✕</button></td>';
      $('mm-t-body').appendChild(tr);
      engancharBorrar();
      tr.querySelector('.mm-t-name').focus();
    };
    $('mm-t-def').onclick = async function () {
      var d;
      try { d = await req(S.api + '/travel-modes/defaults', 'POST'); } catch (e) { return; }
      S.modes = d.modes; cerrar(); modalModes();
    };
    $('mm-t-ok').onclick = async function () {
      this.disabled = true;
      var filas = [].slice.call($('mm-t-body').querySelectorAll('tr'));
      var vivos = {};
      try {
        for (var i = 0; i < filas.length; i++) {
          var tr = filas[i];
          var body = {
            name: tr.querySelector('.mm-t-name').value.trim(),
            icono: tr.querySelector('.mm-t-ico').value.trim(),
            velocidad: parseFloat(tr.querySelector('.mm-t-vel').value) || 0,
            horas_dia: parseFloat(tr.querySelector('.mm-t-hd').value) || 0
          };
          if (!body.name) continue;
          var tid = tr.dataset.tid;
          if (tid) { await req(S.api + '/travel-modes/' + tid, 'PUT', body); vivos[tid] = 1; }
          else await req(S.api + '/travel-modes', 'POST', body);
        }
        // los que sacaron de la tabla se borran
        for (var j = 0; j < S.modes.length; j++) {
          if (!vivos[S.modes[j].id]) await req(S.api + '/travel-modes/' + S.modes[j].id, 'DELETE');
        }
      } catch (e) { this.disabled = false; return; }
      var d = await req(S.api + '/travel-modes');
      S.modes = d.modes;
      cerrar();
      if (S.ruta.length > 1) medir(); else pintar();
    };
  }

  return { mount: mount, recargar: recargar, abrir: abrir,
           estado: function () { return { enabled: S.enabled, visible: S.visible }; } };
})();
