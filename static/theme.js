/* ═══════════════════════════════════════════════════════════════
   Cosmere Combat Tracker — selector de theme
   Se enlaza en los 4 HTML, dentro del <head> y ANTES del <body>
   (así no hay parpadeo del theme viejo al cargar):

       <script src="/static/theme.js"></script>

   Guarda la elección en el navegador de cada uno (localStorage),
   así que cada jugador puede tener el suyo: el DM en claro en la
   compu, los jugadores en nocturno en el celular.

   Para que aparezca el desplegable, poné un contenedor vacío
   donde quieras que salga:

       <span id="themePicker"></span>

   (en la topbar de home.html y dm.html, al lado de "Cuenta";
    en el .header de player.html; en el .card de login.html)
   ═══════════════════════════════════════════════════════════════ */
(function () {
  var KEY = 'cosmere-theme';
  var THEMES = [
    ['oscuro',     'Pergamino oscuro'],
    ['claro',      'Pergamino claro'],
    ['ac-oscuro',  'Alto contraste oscuro'],
    ['ac-claro',   'Alto contraste claro'],
    ['nocturno',   'Nocturno (baja luz)'],
    ['stormlight', 'Stormlight'],
    ['brutalista', 'Brutalista'],
    ['editorial',  'Editorial'],
    ['terminal',   'Terminal'],
    ['arcano',     'Arcano'],
    ['arena',      'Arena']
  ];
  var IDS = THEMES.map(function (t) { return t[0]; });

  function saved() {
    try { var v = localStorage.getItem(KEY); return IDS.indexOf(v) >= 0 ? v : null; }
    catch (e) { return null; }
  }

  // Si nunca eligió, respetamos lo que pide el sistema operativo.
  function inicial() {
    var s = saved();
    if (s) return s;
    try {
      if (window.matchMedia('(prefers-contrast: more)').matches) {
        return window.matchMedia('(prefers-color-scheme: light)').matches ? 'ac-claro' : 'ac-oscuro';
      }
      if (window.matchMedia('(prefers-color-scheme: light)').matches) return 'claro';
    } catch (e) {}
    return 'oscuro';
  }

  function aplicar(id) {
    document.documentElement.setAttribute('data-theme', id);
    try { localStorage.setItem(KEY, id); } catch (e) {}
  }

  // Se aplica ya, con el <head> a medio parsear: sin parpadeo.
  aplicar(inicial());

  // El desplegable se monta cuando el DOM está listo.
  function montar() {
    var host = document.getElementById('themePicker');
    if (!host) return;
    var sel = document.createElement('select');
    sel.className = 'theme-pick';
    sel.title = 'Tema visual (se guarda en este navegador)';
    THEMES.forEach(function (t) {
      var o = document.createElement('option');
      o.value = t[0]; o.textContent = t[1];
      sel.appendChild(o);
    });
    sel.value = document.documentElement.getAttribute('data-theme');
    sel.addEventListener('change', function () { aplicar(sel.value); });
    host.appendChild(sel);
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', montar);
  } else {
    montar();
  }

  // Por si querés cambiarlo desde la consola o desde otro botón.
  window.setTheme = aplicar;
})();
