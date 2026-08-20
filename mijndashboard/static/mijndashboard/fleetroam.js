/* Fleet Roaming — de fleet-pagina van dutchlegionsdashboard.eu, hier in de plugin.
 *
 * Wat er anders moest: op de site praat de browser rechtstreeks met ESI met het
 * EVE-token van de gebruiker. Hier lopen die calls via de server, met de tokens
 * uit django-esi. De pagina haalt daarom één keer per vijftien seconden de hele
 * stand op (`stand.json`) en stuurt acties naar `doe/`.
 *
 * De kaart is wél hetzelfde verhaal: sterren, stargate-lijnen, jump bridges,
 * fleet-markers, intel-markers, route, en een rechtsklikmenu om je autopilot te
 * zetten. De intel komt uit je eigen chatlogs, in de browser gelezen.
 */
(function () {
  'use strict';

  /* ══ Intel: chatlogs lezen ═════════════════════════════════════════════ */

  var IDB_NAAM = 'mijndashboard', IDB_STORE = 'fs-handles', IDB_SLEUTEL = 'intel-dir';
  var STAART_BYTES = 512 * 1024;
  var INTEL_MAX_MIN = 60, VERS_MIN = 5, RECENT_MIN = 15;
  var MSG_RE = /^\[\s*(\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})\s*\]\s*([^>]+)>\s*(.*)$/;
  var CLEAR_RE = /\b(clr|clear|clr\.|nv|no vis|niks|leeg)\b/i;
  var SPIKE_RE = /\b(spike|spiked|blob)\b/i;
  var THREAT_RE = /\b(red|reds|hostile|hostiles|neut|neuts|camp|gang|tackled|bubble|dread|carrier|titan|\+)\b/i;
  var AANTAL_RE = /\b(\d{1,3})\+?\b/;
  var LOGNAAM_RE = /^(.*)_\d{8}_\d{6}_\d+\.txt$/;

  function filterLogs(namen, kanaal) {
    var prefixen = String(kanaal || '').split(/\s*[|,]\s*/).filter(Boolean)
      .map(function (k) { return k.toLowerCase() + '_'; });
    var bestanden = [], kanalen = {};
    for (var i = 0; i < namen.length; i++) {
      var naam = namen[i];
      if (!/\.txt$/i.test(naam)) continue;
      var m = LOGNAAM_RE.exec(naam);
      if (m) kanalen[m[1]] = 1;
      var laag = naam.toLowerCase();
      for (var j = 0; j < prefixen.length; j++) {
        if (laag.indexOf(prefixen[j]) === 0) { bestanden.push(naam); break; }
      }
    }
    return { bestanden: bestanden, kanalen: Object.keys(kanalen).sort() };
  }

  function tijdVan(stempel) {
    var m = /^(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2}):(\d{2})$/.exec(stempel);
    return m ? Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]) : null;
  }

  /* Systeemnamen in een melding. Twee wegen, net als op het dashboard: eerst de
   * null-sec-code (X-XXXX), want die is onmiskenbaar; anders elk woord dat een
   * systeemnaam blijkt te zijn. */
  var CODE_RE = /\b([A-Z0-9]{1,4}-[A-Z0-9]{1,4})\b/g;

  function zoekSystemen(bericht, index) {
    var uit = [], gezien = {}, m;
    CODE_RE.lastIndex = 0;
    while ((m = CODE_RE.exec(bericht.toUpperCase())) !== null) {
      if (!/[A-Z]/.test(m[1])) continue;
      var sid = index[m[1].toLowerCase()];
      if (sid && !gezien[sid]) { gezien[sid] = 1; uit.push(sid); }
    }
    if (uit.length) return uit;
    var woorden = bericht.split(/[^A-Za-z0-9\-']+/);
    for (var i = 0; i < woorden.length; i++) {
      if (woorden[i].length < 3) continue;
      var s2 = index[woorden[i].toLowerCase()];
      if (s2 && !gezien[s2]) { gezien[s2] = 1; uit.push(s2); }
    }
    return uit;
  }

  function parseIntel(tekst, index, nu) {
    var regels = tekst.split(/\r?\n/), uit = [];
    for (var i = 0; i < regels.length; i++) {
      var m = MSG_RE.exec(regels[i].trim());
      if (!m) continue;
      var tijd = tijdVan(m[1]);
      if (tijd === null) continue;
      if (nu && nu - tijd > INTEL_MAX_MIN * 60000) continue;
      var zender = m[2].trim(), bericht = m[3].trim();
      if (!bericht || zender === 'EVE System') continue;
      var systemen = zoekSystemen(bericht, index);
      var clear = CLEAR_RE.test(bericht);
      // Het systeem eruit halen vóór het tellen: veel null-namen beginnen met
      // een cijfer (5-P1Y2), en dat cijfer is niet het aantal vijanden.
      var rest = bericht.replace(CODE_RE, ' ');
      var aantal = AANTAL_RE.exec(rest);
      uit.push({
        tijd: tijd, zender: zender, bericht: bericht, systemen: systemen,
        clear: clear, spike: SPIKE_RE.test(bericht),
        dreiging: !clear && THREAT_RE.test(rest),
        aantal: aantal ? +aantal[1] : 0
      });
    }
    return uit;
  }

  function perSysteem(meldingen) {
    var kaart = {};
    for (var i = 0; i < meldingen.length; i++) {
      var m = meldingen[i];
      for (var j = 0; j < m.systemen.length; j++) {
        var sid = m.systemen[j], b = kaart[sid];
        if (!b) { kaart[sid] = b = { regels: [] }; }
        b.regels.push(m);
      }
    }
    Object.keys(kaart).forEach(function (sid) {
      var b = kaart[sid];
      b.regels.sort(function (x, y) { return y.tijd - x.tijd; });
      var top = b.regels[0];
      b.tijd = top.tijd; b.clear = top.clear; b.dreiging = top.dreiging;
      b.aantal = top.aantal;
      b.spike = b.regels.some(function (r) { return r.spike; });
    });
    return kaart;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { parseIntel: parseIntel, perSysteem: perSysteem,
                       zoekSystemen: zoekSystemen, tijdVan: tijdVan,
                       filterLogs: filterLogs };
  }
  if (typeof window === 'undefined') return;

  function idb() {
    return new Promise(function (resolve, reject) {
      var req = indexedDB.open(IDB_NAAM, 1);
      req.onupgradeneeded = function () {
        if (!req.result.objectStoreNames.contains(IDB_STORE)) req.result.createObjectStore(IDB_STORE);
      };
      req.onsuccess = function () { resolve(req.result); };
      req.onerror = function () { reject(req.error); };
    });
  }
  function idbGet() {
    return idb().then(function (db) {
      return new Promise(function (resolve) {
        var r = db.transaction(IDB_STORE, 'readonly').objectStore(IDB_STORE).get(IDB_SLEUTEL);
        r.onsuccess = function () { resolve(r.result || null); };
        r.onerror = function () { resolve(null); };
      });
    }).catch(function () { return null; });
  }
  function idbZet(h) {
    return idb().then(function (db) {
      db.transaction(IDB_STORE, 'readwrite').objectStore(IDB_STORE).put(h, IDB_SLEUTEL);
    }).catch(function () {});
  }

  function decodeer(buf) {
    var b = new Uint8Array(buf);
    if (b.length >= 2 && b[0] === 0xff && b[1] === 0xfe) return new TextDecoder('utf-16le').decode(b.subarray(2));
    if (b.length >= 2 && b[0] === 0xfe && b[1] === 0xff) return new TextDecoder('utf-16be').decode(b.subarray(2));
    if (b.length >= 3 && b[0] === 0xef && b[1] === 0xbb && b[2] === 0xbf) return new TextDecoder('utf-8').decode(b.subarray(3));
    var monster = Math.min(b.length, 400), nul = 0;
    for (var i = 0; i < monster; i++) if (b[i] === 0) nul++;
    return new TextDecoder(monster > 0 && nul / monster > 0.2 ? 'utf-16le' : 'utf-8').decode(b);
  }

  /* ══ Hulpjes ══════════════════════════════════════════════════════════ */

  function el(tag, klasse, tekst) {
    var e = document.createElement(tag);
    if (klasse) e.className = klasse;
    if (tekst !== undefined && tekst !== null) e.textContent = String(tekst);
    return e;
  }
  function leeg(node) { while (node.firstChild) node.removeChild(node.firstChild); }
  // Letterlijk de schaal van het dashboard (src/utils/secColor.ts).
  function secKleur(sec) {
    if (sec >= 1.0) return '#2C75E1';
    if (sec >= 0.9) return '#399AEB';
    if (sec >= 0.8) return '#4ECEF8';
    if (sec >= 0.7) return '#60DBA3';
    if (sec >= 0.5) return '#3ECF6E';
    if (sec >= 0.2) return '#F0C040';
    if (sec >= 0.0) return '#F59E0B';
    if (sec >= -0.3) return '#FB923C';
    if (sec >= -0.6) return '#F97316';
    return '#EF4444';
  }

  // Tekst met een donkere rand eromheen, zoals paintOrder="stroke" in hun SVG.
  function omrand(ctx, tekst, x, y, grootte, kleur, dikte) {
    ctx.font = grootte + 'px system-ui, sans-serif';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = '#05050e';
    ctx.lineWidth = dikte || grootte * 0.14;
    ctx.strokeText(tekst, x, y);
    ctx.fillStyle = kleur;
    ctx.fillText(tekst, x, y);
  }
  var ROLLABEL = { fleet_commander: 'FC', wing_commander: 'WC',
                   squad_commander: 'SC', squad_member: '' };
  var ROLKLEUR = { fleet_commander: '#f0c040', wing_commander: '#00b4d8',
                   squad_commander: '#3ecf6e', squad_member: '' };

  function csrf() {
    var m = /(?:^|;\s*)csrftoken=([^;]+)/.exec(document.cookie);
    return m ? decodeURIComponent(m[1]) : '';
  }

  /* ══ De pagina ════════════════════════════════════════════════════════ */

  function start(wortel) {
    var URL_STAND = wortel.getAttribute('data-stand');
    var URL_KAART = wortel.getAttribute('data-kaart');
    var URL_DOE = wortel.getAttribute('data-doe');
    var URL_JUMPS = wortel.getAttribute('data-jumps');
    var kanaal = wortel.getAttribute('data-kanaal') || 'Insidious.Intel';

    var stand = null;              // laatste antwoord van stand.json
    var sys = {};                  // id → [id, x, z, sec, naam, regio]
    var naamIndex = {};            // naam (klein) → id
    var regios = {};
    var bruggen = [];
    var buren = {};                // id → [id, …] stargates
    var intel = {};                // id → {regels, tijd, …}
    var meldingen = [];
    var kanalenInMap = [];
    var dirHandle = null, intelToestand = 'geen-map';
    var routePad = null;
    var weergave = 'kaart';

    var deel = {
      stats: wortel.querySelector('[data-stats]'),
      knoppen: wortel.querySelector('[data-weergave]'),
      kaartVak: wortel.querySelector('[data-kaartvak]'),
      canvas: wortel.querySelector('[data-canvas]'),
      leden: wortel.querySelector('[data-leden]'),
      beheer: wortel.querySelector('[data-beheer]'),
      intelStatus: wortel.querySelector('[data-intel-status]'),
      intelLijst: wortel.querySelector('[data-intel-lijst]'),
      melding: wortel.querySelector('[data-melding]'),
      tip: wortel.querySelector('[data-tip]'),
      menu: wortel.querySelector('[data-menu]')
    };

    /* ── Meldingen onderin de kaart ───────────────────────────────────── */
    var meldingTimer = null;
    function zegHet(tekst, goed) {
      if (!deel.melding) return;
      deel.melding.textContent = (goed ? '📍 ' : '⚠ ') + tekst;
      deel.melding.className = 'fin-roam-melding ' + (goed ? 'is-goed' : 'is-fout');
      deel.melding.style.display = 'block';
      clearTimeout(meldingTimer);
      meldingTimer = setTimeout(function () { deel.melding.style.display = 'none'; }, 5000);
    }

    function doe(velden) {
      var body = new URLSearchParams(velden).toString();
      return fetch(URL_DOE, {
        method: 'POST', credentials: 'same-origin',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded',
                   'X-CSRFToken': csrf() },
        body: body
      }).then(function (r) { return r.json(); });
    }

    function actie(velden, daarna) {
      doe(velden).then(function (r) {
        zegHet(r.melding || (r.ok ? 'Gedaan.' : 'Mislukt.'), r.ok);
        if (r.ok && daarna !== false) haalStand();
      }).catch(function () { zegHet('De server antwoordde niet.', false); });
    }

    /* ── Kaartprojectie ──────────────────────────────────────────────── */
    // Vast tekenvlak, precies als op het dashboard: W=660, H=760, PAD=30. Alles
    // wordt daarop gerekend en daarna als geheel naar de doos geschaald, zodat
    // lettergroottes en markers dezelfde verhouding houden als daar.
    var W = 660, H = 760, PAD = 30;
    var tf = { k: 1, x: 0, y: 0 }, basis = null, autoGezoomd = false;
    var toonSchaal = 1;

    function maakBasis() {
      var lijst = Object.keys(sys);
      if (!lijst.length) return;
      var minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
      for (var i = 0; i < lijst.length; i++) {
        var s = sys[lijst[i]];
        if (s[1] < minX) minX = s[1]; if (s[1] > maxX) maxX = s[1];
        var z = -s[2];
        if (z < minZ) minZ = z; if (z > maxZ) maxZ = z;
      }
      var spanX = (maxX - minX) || 1, spanZ = (maxZ - minZ) || 1;
      var schaal = Math.min((W - 2 * PAD) / spanX, (H - 2 * PAD) / spanZ);
      var offX = (W - schaal * spanX) / 2 - minX * schaal;
      var offZ = (H - schaal * spanZ) / 2 - minZ * schaal;
      basis = function (x, z) { return [offX + x * schaal, offZ + (-z) * schaal]; };
    }

    function scherm(x, z) {
      var b = basis(x, z);
      return [b[0] * tf.k + tf.x, b[1] * tf.k + tf.y];
    }

    function zoomOp(sid, k) {
      var s = sys[sid];
      if (!s || !basis) return;
      var b = basis(s[1], s[2]);
      tf = { k: k || 24, x: W / 2 - b[0] * (k || 24), y: H / 2 - b[1] * (k || 24) };
      teken();
    }

    /* ── Tekenen ─────────────────────────────────────────────────────── */
    function ledenPerSysteem() {
      var uit = {};
      ((stand && stand.leden) || []).forEach(function (m) {
        var sid = m.systeem_id;
        if (!sid) return;
        if (!uit[sid]) uit[sid] = { leden: [], fc: false };
        uit[sid].leden.push(m);
        if (m.rol === 'fleet_commander') uit[sid].fc = true;
      });
      return uit;
    }

    function teken() {
      var cv = deel.canvas;
      if (!cv || !basis) return;
      var dpr = Math.min(window.devicePixelRatio || 1, 2);
      var f = toonSchaal * dpr;
      cv.width = Math.round(W * f); cv.height = Math.round(H * f);
      var ctx = cv.getContext('2d');
      ctx.setTransform(f, 0, 0, f, 0, 0);
      ctx.clearRect(0, 0, W, H);
      ctx.fillStyle = '#05050e';
      ctx.fillRect(0, 0, W, H);

      // 1) Stargate-lijnen, zoals de star map in het spel.
      ctx.strokeStyle = 'rgba(150,60,110,0.30)';
      ctx.lineWidth = Math.min(1.4, 0.5 + tf.k * 0.12);
      ctx.beginPath();
      Object.keys(buren).forEach(function (sid) {
        var a = sys[sid];
        if (!a) return;
        var pa = scherm(a[1], a[2]);
        if (pa[0] < -W || pa[0] > 2 * W || pa[1] < -H || pa[1] > 2 * H) return;
        buren[sid].forEach(function (nb) {
          if (+sid > nb) return;                      // elke gate één keer
          var b = sys[nb];
          if (!b) return;
          var pb = scherm(b[1], b[2]);
          if ((pa[0] < 0 && pb[0] < 0) || (pa[0] > W && pb[0] > W) ||
              (pa[1] < 0 && pb[1] < 0) || (pa[1] > H && pb[1] > H)) return;
          ctx.moveTo(pa[0], pa[1]); ctx.lineTo(pb[0], pb[1]);
        });
      });
      ctx.stroke();

      // 2) Jump bridges — groene boog, zoals in het spel.
      if (bruggen.length) {
        ctx.save();
        ctx.strokeStyle = 'rgba(82,224,128,0.5)';
        ctx.lineWidth = Math.min(2.4, 1 + tf.k * 0.07);
        ctx.lineCap = 'round';
        ctx.beginPath();
        bruggen.forEach(function (paar) {
          var a = sys[paar[0]], b = sys[paar[1]];
          if (!a || !b) return;
          var pa = scherm(a[1], a[2]), pb = scherm(b[1], b[2]);
          var mx = (pa[0] + pb[0]) / 2, my = (pa[1] + pb[1]) / 2;
          var dx = pb[0] - pa[0], dy = pb[1] - pa[1];
          var len = Math.hypot(dx, dy) || 1, off = len * 0.28;
          ctx.moveTo(pa[0], pa[1]);
          ctx.quadraticCurveTo(mx + (dy / len) * off, my - (dx / len) * off, pb[0], pb[1]);
        });
        ctx.stroke();
        ctx.restore();
      }

      // 3) De sterren zelf, gekleurd op security.
      var straal = Math.max(0.5, (1.0 + (tf.k - 1) * 0.16) / 2);
      ctx.globalAlpha = 0.85;
      Object.keys(sys).forEach(function (sid) {
        var s = sys[sid];
        var p = scherm(s[1], s[2]);
        if (p[0] < -4 || p[0] > W + 4 || p[1] < -4 || p[1] > H + 4) return;
        ctx.fillStyle = secKleur(s[3]);
        ctx.beginPath(); ctx.arc(p[0], p[1], straal, 0, Math.PI * 2); ctx.fill();
      });
      ctx.globalAlpha = 1;

      // 4) Regionamen: alleen in het overzicht. Ingezoomd kijk je naar de
      //    systemen zelf en liggen ze er alleen maar overheen.
      if (tf.k < 6) {
        var zwaarte = {};
        Object.keys(sys).forEach(function (sid) {
          var s = sys[sid], r = s[5];
          if (!r) return;
          var a = zwaarte[r] || (zwaarte[r] = { x: 0, z: 0, n: 0 });
          a.x += s[1]; a.z += s[2]; a.n++;
        });
        ctx.fillStyle = 'rgba(214,222,246,0.88)';
        ctx.font = Math.min(14, 11 + tf.k * 0.12) + 'px system-ui, sans-serif';
        ctx.textAlign = 'center';
        var bezet = [];
        // Waar de fleet staat gaat vóór: die regionaam mag nooit wegvallen
        // omdat er toevallig een buurregio overheen valt. Daarna de grootste,
        // want dat zijn de herkenningspunten.
        var fleetRegios = {};
        Object.keys(ledenPerSysteem()).forEach(function (sid) {
          if (sys[sid]) fleetRegios[sys[sid][5]] = 1;
        });
        Object.keys(zwaarte).sort(function (a, b) {
          var fa = fleetRegios[a] ? 1 : 0, fb = fleetRegios[b] ? 1 : 0;
          if (fa !== fb) return fb - fa;
          return zwaarte[b].n - zwaarte[a].n;
        }).forEach(function (r) {
          var naam = regios[r] || '';
          if (!naam) return;
          var a = zwaarte[r];
          var p = scherm(a.x / a.n, a.z / a.n);
          if (p[0] < 0 || p[0] > W || p[1] < 0 || p[1] > H) return;
          var halve = ctx.measureText(naam).width / 2 + 3;
          var vak = [p[0] - halve, p[1] - 9, p[0] + halve, p[1] + 4];
          for (var i = 0; i < bezet.length; i++) {
            var b2 = bezet[i];
            if (vak[0] < b2[2] && vak[2] > b2[0] && vak[1] < b2[3] && vak[3] > b2[1]) return;
          }
          bezet.push(vak);
          // Waar de fleet staat, staat ook de marker met z'n naam. De regionaam
          // een regel lager, anders schuift hij er precies achter — en juist die
          // wil je lezen: daar is je fleet.
          ctx.fillText(naam, p[0], p[1] + (fleetRegios[r] ? 18 : 0));
        });
        ctx.textAlign = 'left';
      }

      // 5) De route, als stippellijn.
      if (routePad && routePad.length > 1) {
        ctx.save();
        ctx.strokeStyle = '#00b4d8';
        ctx.lineWidth = 2;
        ctx.setLineDash([6, 5]);
        ctx.beginPath();
        for (var i = 0; i < routePad.length; i++) {
          var s2 = sys[routePad[i]];
          if (!s2) continue;
          var p2 = scherm(s2[1], s2[2]);
          if (i === 0) ctx.moveTo(p2[0], p2[1]); else ctx.lineTo(p2[0], p2[1]);
        }
        ctx.stroke();
        ctx.restore();
      }

      tekenMarkers(ctx);
    }

    /* Markers, één op één overgenomen van de fleet-kaart op het dashboard.
     *
     * Een fleet is daar een stip in de security-kleur van het systeem, met een
     * groene ring eromheen, een gouden streepjesring als de FC er staat, het
     * aantal ín de stip en de namen van de leden eronder. Intel is een gevulde
     * cirkel met een uitroepteken, een pulserende ring, het gemelde aantal
     * erboven en bij een spike een knipperend ⚠ SPIKE.
     *
     * De lettergroottes schalen met de zoom, net als daar:
     *   sysFont = min(16, 3 + k·0,45)   memFont = min(15, 3 + k·0,42)
     *   markerFont = min(17, 4 + k·0,48)
     */
    function tekenMarkers(ctx) {
      var nu = Date.now();
      var sysFont = Math.min(16, 3 + tf.k * 0.45);
      var memFont = Math.min(15, 3 + tf.k * 0.42);
      var markerFont = Math.min(17, 4 + tf.k * 0.48);
      var memLine = memFont * 1.18;
      var perSys = ledenPerSysteem();
      var meest = 1;
      Object.keys(perSys).forEach(function (sid) {
        meest = Math.max(meest, perSys[sid].leden.length);
      });
      var heeftMarker = {};
      Object.keys(perSys).forEach(function (sid) { heeftMarker[sid] = 1; });

      // Systeemnamen: bij hen komen die uit dezelfde overlay, met een donkere
      // rand eromheen. Systemen met een fleet-marker krijgen er geen, want die
      // marker zegt de naam al.
      if (tf.k > 3) {
        ctx.textAlign = 'left';
        Object.keys(sys).forEach(function (sid) {
          if (heeftMarker[sid]) return;
          var st = sys[sid];
          var p = scherm(st[1], st[2]);
          if (p[0] < 4 || p[0] > W - 4 || p[1] < 8 || p[1] > H - 2) return;
          omrand(ctx, st[4], p[0] + sysFont * 0.5, p[1] - sysFont * 0.4,
                 sysFont, 'rgba(225,228,240,0.8)', sysFont * 0.07);
        });
      }

      // ── Intel ──────────────────────────────────────────────────────────
      var puls = (nu % 1500) / 1500;          // radar-ping, 1,5 s rond
      var spikePuls = (nu % 1000) / 1000;
      Object.keys(intel).forEach(function (sid) {
        var st = sys[sid];
        if (!st) return;
        var b = intel[sid];
        if (b.clear) return;
        var p = scherm(st[1], st[2]);
        if (p[0] < -10 || p[0] > W + 10 || p[1] < -10 || p[1] > H + 10) return;
        var ir = Math.max(5, markerFont * 0.7);
        var col = b.dreiging ? '#e05555' : '#f0a030';

        if (b.spike) {
          // Dubbele puls, een halve slag uit fase — zoals de twee SVG-animaties.
          [spikePuls, (spikePuls + 0.5) % 1].forEach(function (t, i) {
            ctx.beginPath();
            ctx.arc(p[0], p[1], ir + (ir * 5) * t, 0, Math.PI * 2);
            ctx.strokeStyle = i ? '#f0a030' : '#e05555';
            ctx.globalAlpha = 0.95 * (1 - t);
            ctx.lineWidth = 3.5 - 3 * t;
            ctx.stroke();
          });
          ctx.globalAlpha = 1;
        }
        ctx.beginPath();
        ctx.arc(p[0], p[1], ir + (ir * 1.8) * puls, 0, Math.PI * 2);
        ctx.strokeStyle = col;
        ctx.globalAlpha = 0.85 * (1 - puls);
        ctx.lineWidth = 2 - 1.6 * puls;
        ctx.stroke();
        ctx.globalAlpha = 1;

        ctx.beginPath();
        ctx.arc(p[0], p[1], ir, 0, Math.PI * 2);
        ctx.fillStyle = col;
        ctx.fill();
        ctx.strokeStyle = '#05050e';
        ctx.lineWidth = ir * 0.12;
        ctx.stroke();

        ctx.textAlign = 'center';
        ctx.font = 'bold ' + (ir * 1.1) + 'px system-ui, sans-serif';
        ctx.fillStyle = '#fff';
        ctx.fillText('!', p[0], p[1] + ir * 0.36);

        if (b.aantal > 0) {
          ctx.font = 'bold ' + (ir * 0.95) + 'px system-ui, sans-serif';
          omrand(ctx, b.aantal + '+', p[0], p[1] - ir - 1, ir * 0.95, col, ir * 0.09);
        }
        if (b.spike) {
          ctx.font = 'bold ' + (markerFont * 1.3) + 'px system-ui, sans-serif';
          ctx.globalAlpha = spikePuls < 0.5 ? 1 : 0.45;
          omrand(ctx, '⚠ SPIKE', p[0], p[1] - ir - markerFont * 1.4,
                 markerFont * 1.3, spikePuls < 0.5 ? '#e05555' : '#f0a030',
                 markerFont * 0.12);
          ctx.globalAlpha = 1;
        }
        ctx.textAlign = 'left';
      });

      // ── De fleet ───────────────────────────────────────────────────────
      Object.keys(perSys).forEach(function (sid) {
        var st = sys[sid];
        if (!st) return;
        var b = perSys[sid];
        var p = scherm(st[1], st[2]);
        var r = 3 + (b.leden.length / meest) * 4;

        ctx.beginPath();
        ctx.arc(p[0], p[1], r + 4, 0, Math.PI * 2);
        ctx.fillStyle = 'rgba(62,207,110,0.12)';
        ctx.fill();

        ctx.beginPath();
        ctx.arc(p[0], p[1], r + 1.5, 0, Math.PI * 2);
        ctx.strokeStyle = '#3ecf6e';
        ctx.lineWidth = 1.4;
        ctx.stroke();

        if (b.fc) {
          ctx.save();
          ctx.beginPath();
          ctx.arc(p[0], p[1], r + 4, 0, Math.PI * 2);
          ctx.strokeStyle = '#f0c040';
          ctx.lineWidth = 1;
          ctx.setLineDash([3, 2]);
          ctx.stroke();
          ctx.restore();
        }

        ctx.beginPath();
        ctx.arc(p[0], p[1], r, 0, Math.PI * 2);
        ctx.fillStyle = secKleur(st[3]);
        ctx.fill();
        ctx.strokeStyle = '#05050e';
        ctx.lineWidth = 0.8;
        ctx.stroke();

        ctx.textAlign = 'center';
        ctx.font = 'bold ' + Math.min(9, r + 1.5) + 'px system-ui, sans-serif';
        ctx.fillStyle = '#05050e';
        ctx.fillText(String(b.leden.length), p[0], p[1] + 2.6);

        ctx.textAlign = 'left';
        ctx.font = 'bold ' + markerFont + 'px system-ui, sans-serif';
        omrand(ctx, st[4] + (b.fc ? ' · FC' : ''), p[0] + r + 4,
               p[1] + markerFont * 0.38, markerFont, '#fff', markerFont * 0.085);

        // De namen van de leden eronder, maximaal acht.
        ctx.textAlign = 'center';
        b.leden.slice(0, 8).forEach(function (m, i) {
          omrand(ctx, m.naam, p[0], p[1] + r + memFont + i * memLine,
                 memFont, 'rgba(225,232,245,0.92)', memFont * 0.07);
        });
        if (b.leden.length > 8) {
          omrand(ctx, '+' + (b.leden.length - 8) + ' meer', p[0],
                 p[1] + r + memFont + 8 * memLine, memFont, '#8a90b0', memFont * 0.07);
        }
        ctx.textAlign = 'left';
      });
    }

    // De pulserende intel-ringen vragen om hertekenen; zonder intel staat de
    // kaart stil en kost het niets.
    function pulsLoop() {
      if (Object.keys(intel).length) teken();
      window.requestAnimationFrame(pulsLoop);
    }
    window.requestAnimationFrame(pulsLoop);

    /* ── Het rechtsklikmenu ──────────────────────────────────────────── */
    function dichtstbij(px, py) {
      var beste = null, besteD = Infinity;
      Object.keys(sys).forEach(function (sid) {
        var s = sys[sid];
        var p = scherm(s[1], s[2]);
        var d = (p[0] - px) * (p[0] - px) + (p[1] - py) * (p[1] - py);
        if (d < besteD) { besteD = d; beste = sid; }
      });
      return beste;
    }

    function toonMenu(x, y, sid) {
      var s = sys[sid];
      if (!s || !deel.menu) return;
      leeg(deel.menu);
      deel.menu.appendChild(el('div', 'fin-roam-menukop', s[4] + '  ' + s[3].toFixed(1)));

      function knop(tekst, fn) {
        var b = el('button', 'fin-roam-menuknop', tekst);
        b.addEventListener('click', function (e) { e.stopPropagation(); sluitMenu(); fn(); });
        deel.menu.appendChild(b);
      }
      if (stand && stand.mag_waypoint) {
        knop('Set Destination', function () {
          actie({ actie: 'waypoint', systeem_id: sid, modus: 'set' }, false);
          toonRoute(sid);
        });
        knop('Waypoint toevoegen', function () {
          actie({ actie: 'waypoint', systeem_id: sid, modus: 'add' }, false);
        });
        knop('Route op al mijn characters', function () {
          actie({ actie: 'waypoint', systeem_id: sid, modus: 'alle' }, false);
        });
      } else {
        deel.menu.appendChild(el('div', 'fin-roam-menunoot',
          'Koppel opnieuw als FC voor het waypoint-recht'));
      }
      knop('Toon route', function () { toonRoute(sid); });
      if (routePad) knop('Route wissen', function () { routePad = null; teken(); });

      var links = el('div', 'fin-roam-menulinks');
      var a1 = el('a', null, 'Dotlan');
      a1.href = 'https://evemaps.dotlan.net/system/' + encodeURIComponent(s[4].replace(/ /g, '_'));
      a1.target = '_blank'; a1.rel = 'noopener';
      var a2 = el('a', null, 'zKillboard');
      a2.href = 'https://zkillboard.com/system/' + sid + '/';
      a2.target = '_blank'; a2.rel = 'noopener';
      links.appendChild(a1); links.appendChild(a2);
      deel.menu.appendChild(links);

      deel.menu.style.display = 'block';
      var vak = deel.kaartVak.getBoundingClientRect();
      deel.menu.style.left = Math.min(x - vak.left, vak.width - 190) + 'px';
      deel.menu.style.top = Math.min(y - vak.top, vak.height - 160) + 'px';
    }
    function sluitMenu() { if (deel.menu) deel.menu.style.display = 'none'; }

    function toonRoute(sid) {
      doe({ actie: 'route', systeem_id: sid }).then(function (r) {
        if (r.pad && r.pad.length > 1) {
          routePad = r.pad;
          zegHet('Route: ' + r.jumps + ' jumps naar ' + (sys[sid] ? sys[sid][4] : sid), true);
          teken();
        } else {
          zegHet(r.melding || 'Geen route gevonden.', false);
        }
      }).catch(function () { zegHet('De server antwoordde niet.', false); });
    }

    /* ── De tooltip bij een intel-marker ─────────────────────────────── */
    function toonTip(x, y, sid) {
      var b = intel[sid], s = sys[sid];
      if (!b || !s || !deel.tip) { verbergTip(); return; }
      leeg(deel.tip);
      var kop = el('div', 'fin-roam-tipkop');
      kop.appendChild(el('b', null, s[4]));
      kop.appendChild(el('span', 'fin-roam-tipsec', s[3].toFixed(1)));
      deel.tip.appendChild(kop);
      var nu = Date.now();
      b.regels.slice(0, 6).forEach(function (r) {
        var rij = el('div', 'fin-roam-tipregel' + (r.clear ? ' is-clear' : ''));
        var sec = Math.floor((nu - r.tijd) / 1000);
        rij.appendChild(el('span', 'fin-roam-tiptijd',
          Math.floor(sec / 60) + ':' + ('0' + (sec % 60)).slice(-2)));
        rij.appendChild(el('span', 'fin-roam-tiptekst', r.bericht));
        rij.appendChild(el('span', 'fin-roam-tipwie', r.zender));
        deel.tip.appendChild(rij);
      });
      deel.tip.style.display = 'block';
      var vak = deel.kaartVak.getBoundingClientRect();
      deel.tip.style.left = Math.min(x - vak.left + 12, vak.width - 320) + 'px';
      deel.tip.style.top = Math.min(y - vak.top + 12, vak.height - 120) + 'px';
    }
    function verbergTip() { if (deel.tip) deel.tip.style.display = 'none'; }

    /* ── De statusbalk ───────────────────────────────────────────────── */
    function tekenStats() {
      if (!deel.stats) return;
      leeg(deel.stats);
      if (!stand || !stand.in_fleet) return;

      function kaartje(label, waarde, sub, klasse) {
        var k = el('div', 'fin-stat ' + (klasse || ''));
        k.appendChild(el('span', 'l', label));
        var v = el('span', 'v');
        if (waarde instanceof Node) v.appendChild(waarde); else v.textContent = waarde;
        k.appendChild(v);
        if (sub) k.appendChild(el('span', 's', sub));
        deel.stats.appendChild(k);
      }

      var perSys = ledenPerSysteem();
      var fcSys = stand.fc && stand.fc.systeem_id;
      var los = 0;
      Object.keys(perSys).forEach(function (sid) {
        if (+sid !== fcSys) los += perSys[sid].leden.length;
      });

      kaartje('Fleet commander', stand.fc ? stand.fc.naam : '—',
              stand.fc && fcSys && sys[fcSys] ? sys[fcSys][4] : '');
      kaartje('Mijn rol', ROLLABEL[stand.mijn_rol] || 'Member',
              stand.ik ? stand.ik.naam : '');
      kaartje('Leden', stand.leden.length,
              Object.keys(perSys).length + ' systeem' +
              (Object.keys(perSys).length === 1 ? '' : 'en'));
      var vlaggen = el('span', 'fin-roam-vlaggen');
      var v1 = el('i', stand.free_move ? 'fin-pos' : 'fin-dim',
                  (stand.free_move ? '✓' : '✗') + ' Free move');
      var v2 = el('i', stand.geregistreerd ? 'fin-pos' : 'fin-dim',
                  (stand.geregistreerd ? '✓' : '✗') + ' Advert');
      vlaggen.appendChild(v1); vlaggen.appendChild(v2);
      kaartje('Status', vlaggen, los ? los + ' niet bij de FC' : 'iedereen bij elkaar',
              los ? 'fin-stat-belasting' : '');
    }

    /* ── De ledentabel ───────────────────────────────────────────────── */
    function tekenLeden() {
      if (!deel.leden) return;
      leeg(deel.leden);
      if (!stand || !stand.in_fleet) return;

      var squadNaam = {};
      (stand.wings || []).forEach(function (w) {
        (w.squads || []).forEach(function (s) {
          squadNaam[s.id] = (w.naam || 'Wing') + ' · ' + (s.naam || 'Squad');
        });
      });

      var tabel = el('table', 'fin-table');
      var thead = el('thead');
      var tr = el('tr');
      ['Piloot', 'Rol', 'Schip', 'Systeem', 'Squad', 'Sinds', ''].forEach(function (k) {
        tr.appendChild(el('th', null, k));
      });
      thead.appendChild(tr);
      tabel.appendChild(thead);
      var tbody = el('tbody');

      var leden = stand.leden.slice().sort(function (a, b) {
        if ((a.rol === 'fleet_commander') !== (b.rol === 'fleet_commander'))
          return a.rol === 'fleet_commander' ? -1 : 1;
        return a.naam.toLowerCase() < b.naam.toLowerCase() ? -1 : 1;
      });

      leden.forEach(function (m) {
        var rij = el('tr');
        if (m.ik) rij.className = 'is-ik';

        var td = el('td');
        var wie = el('span', 'fin-fleet-naamcel');
        var img = el('img');
        img.loading = 'lazy'; img.alt = '';
        img.src = 'https://images.evetech.net/characters/' + m.character_id + '/portrait?size=32';
        wie.appendChild(img);
        wie.appendChild(el('span', null, m.naam));
        td.appendChild(wie);
        rij.appendChild(td);

        var tdRol = el('td');
        if (ROLLABEL[m.rol]) {
          var badge = el('i', 'fin-roam-rolbadge', ROLLABEL[m.rol]);
          badge.style.color = ROLKLEUR[m.rol];
          badge.style.borderColor = ROLKLEUR[m.rol] + '66';
          tdRol.appendChild(badge);
        }
        rij.appendChild(tdRol);

        var tdSchip = el('td');
        var schip = el('span', 'fin-roam-schipcel');
        if (m.schip_id) {
          var si = el('img');
          si.loading = 'lazy'; si.alt = '';
          si.src = 'https://images.evetech.net/types/' + m.schip_id + '/icon?size=32';
          schip.appendChild(si);
        }
        schip.appendChild(el('span', null, m.schip || '—'));
        tdSchip.appendChild(schip);
        rij.appendChild(tdSchip);

        var tdSys = el('td');
        var s = sys[m.systeem_id];
        if (s) {
          var sec = el('span', 'fin-roam-sec', s[3].toFixed(1));
          sec.style.color = secKleur(s[3]);
          tdSys.appendChild(sec);
          var naam = el('a', 'fin-roam-syslink', s[4]);
          naam.href = '#';
          naam.addEventListener('click', function (e) {
            e.preventDefault();
            weergaveNaar('kaart');
            zoomOp(m.systeem_id, 24);
          });
          tdSys.appendChild(naam);
        } else {
          tdSys.appendChild(el('span', 'fin-dim', '—'));
        }
        rij.appendChild(tdSys);

        var tdSquad = el('td');
        if (stand.is_boss && stand.mag_schrijven && (stand.wings || []).length) {
          var kies = el('select', 'fin-zoekveld fin-roam-squadkies');
          var leegOpt = el('option', null, '—');
          leegOpt.value = '';
          kies.appendChild(leegOpt);
          (stand.wings || []).forEach(function (w) {
            (w.squads || []).forEach(function (sq) {
              var o = el('option', null, (w.naam || 'Wing') + ' · ' + (sq.naam || 'Squad'));
              o.value = w.id + ':' + sq.id;
              if (sq.id === m.squad_id) o.selected = true;
              kies.appendChild(o);
            });
          });
          kies.addEventListener('change', function () {
            var d = kies.value.split(':');
            actie({ actie: 'verplaats', character_id: m.character_id,
                    wing_id: d[0] || '', squad_id: d[1] || '' });
          });
          tdSquad.appendChild(kies);
        } else {
          tdSquad.appendChild(el('span', 'fin-dim', squadNaam[m.squad_id] || '—'));
        }
        rij.appendChild(tdSquad);

        rij.appendChild(el('td', 'fin-roam-sinds', m.sinds || ''));

        var tdWeg = el('td', 'fin-roam-wegcel');
        if (stand.is_boss && stand.mag_schrijven && !m.ik) {
          var weg = el('button', 'fin-roam-schopknop', '✕');
          weg.title = 'Uit de fleet zetten';
          weg.addEventListener('click', function () {
            if (window.confirm(m.naam + ' uit de fleet zetten?')) {
              actie({ actie: 'schop', character_id: m.character_id });
            }
          });
          tdWeg.appendChild(weg);
        }
        rij.appendChild(tdWeg);
        tbody.appendChild(rij);
      });
      tabel.appendChild(tbody);
      var wrap = el('div', 'fin-tablewrap');
      wrap.appendChild(tabel);
      deel.leden.appendChild(wrap);
    }

    /* ── Fleet-beheer ────────────────────────────────────────────────── */
    function tekenBeheer() {
      if (!deel.beheer) return;
      leeg(deel.beheer);
      if (!stand || !stand.in_fleet) return;
      if (!stand.is_boss || !stand.mag_schrijven) {
        if (stand.in_fleet && !stand.mag_schrijven) {
          deel.beheer.appendChild(el('p', 'fin-onderschrift',
            'Je kunt meekijken maar niet beheren: dit character mist het fleet-schrijfrecht.'));
        } else if (!stand.is_boss) {
          deel.beheer.appendChild(el('p', 'fin-onderschrift',
            'Uitnodigen, kicken en wings beheren kan alleen de fleet boss — degene die de fleet geopend heeft.'));
        }
        return;
      }

      var kop = el('h2', 'fin-kop', 'Fleet-beheer');
      kop.appendChild(el('span', null, ' — alleen jij als fleet boss ziet dit'));
      deel.beheer.appendChild(kop);

      /* MOTD + free move */
      var rij1 = el('div', 'fin-roam-beheerrij');
      var motd = el('textarea', 'fin-zoekveld');
      motd.rows = 2;
      motd.value = stand.motd_kaal || '';
      motd.placeholder = 'Broadcast targets only. Anchor op de FC.';
      var motdKnop = el('button', 'fin-knop fin-knop-klein', 'MOTD zetten');
      motdKnop.addEventListener('click', function () {
        actie({ actie: 'motd', motd: motd.value });
      });
      var vrij = el('button', 'fin-knop fin-knop-klein',
                    stand.free_move ? 'Free move uit' : 'Free move aan');
      vrij.addEventListener('click', function () {
        actie({ actie: 'free_move', aan: stand.free_move ? '0' : '1' });
      });
      var motdVak = el('div', 'fin-roam-motdvak');
      motdVak.appendChild(el('span', 'fin-roam-label', 'MOTD'));
      motdVak.appendChild(motd);
      var motdKnoppen = el('div', 'fin-roam-knoprij');
      motdKnoppen.appendChild(motdKnop);
      motdKnoppen.appendChild(vrij);
      motdVak.appendChild(motdKnoppen);
      rij1.appendChild(motdVak);

      /* Wings en squads */
      var wingVak = el('div', 'fin-roam-wingvak');
      var wingKop = el('div', 'fin-roam-knoprij');
      wingKop.appendChild(el('span', 'fin-roam-label', 'Wings en squads'));
      var nieuweWing = el('button', 'fin-knop fin-knop-klein', '+ wing');
      nieuweWing.addEventListener('click', function () {
        var naam = window.prompt('Naam van de wing (max 10 tekens)', 'Main');
        if (naam === null) return;
        actie({ actie: 'wing_nieuw', naam: naam });
      });
      wingKop.appendChild(nieuweWing);
      wingVak.appendChild(wingKop);

      (stand.wings || []).forEach(function (w) {
        var wr = el('div', 'fin-roam-wing');
        var wnaam = el('b', null, w.naam || 'Wing ' + w.id);
        wr.appendChild(wnaam);
        function mini(tekst, titel, fn) {
          var b = el('button', 'fin-roam-mini', tekst);
          b.title = titel;
          b.addEventListener('click', fn);
          return b;
        }
        wr.appendChild(mini('✎', 'Wing hernoemen', function () {
          var naam = window.prompt('Nieuwe naam (max 10 tekens)', w.naam || '');
          if (naam === null) return;
          actie({ actie: 'wing_naam', wing_id: w.id, naam: naam });
        }));
        wr.appendChild(mini('+', 'Squad toevoegen', function () {
          var naam = window.prompt('Naam van de squad (max 10 tekens)', 'Squad');
          if (naam === null) return;
          actie({ actie: 'squad_nieuw', wing_id: w.id, naam: naam });
        }));
        wr.appendChild(mini('✕', 'Wing verwijderen', function () {
          if (window.confirm('Wing ' + (w.naam || w.id) + ' verwijderen?')) {
            actie({ actie: 'wing_weg', wing_id: w.id });
          }
        }));
        wingVak.appendChild(wr);

        (w.squads || []).forEach(function (sq) {
          var sr = el('div', 'fin-roam-squad2');
          sr.appendChild(el('span', null, sq.naam || 'Squad ' + sq.id));
          sr.appendChild(mini('✎', 'Squad hernoemen', function () {
            var naam = window.prompt('Nieuwe naam (max 10 tekens)', sq.naam || '');
            if (naam === null) return;
            actie({ actie: 'squad_naam', squad_id: sq.id, naam: naam });
          }));
          sr.appendChild(mini('✕', 'Squad verwijderen', function () {
            if (window.confirm('Squad ' + (sq.naam || sq.id) + ' verwijderen?')) {
              actie({ actie: 'squad_weg', squad_id: sq.id });
            }
          }));
          wingVak.appendChild(sr);
        });
      });
      rij1.appendChild(wingVak);
      deel.beheer.appendChild(rij1);

      /* Uitnodigen: aanvinken uit de Auth, of namen typen */
      var nodig = el('div', 'fin-roam-nodigvak');
      nodig.appendChild(el('span', 'fin-roam-label', 'Uitnodigen'));

      var squadKies = el('select', 'fin-zoekveld');
      var opt0 = el('option', null, 'wachtruimte — zelf indelen');
      opt0.value = '';
      squadKies.appendChild(opt0);
      (stand.wings || []).forEach(function (w) {
        (w.squads || []).forEach(function (sq) {
          var o = el('option', null, (w.naam || 'Wing') + ' · ' + (sq.naam || 'Squad'));
          o.value = w.id + ':' + sq.id;
          squadKies.appendChild(o);
        });
      });

      var lijst = el('div', 'fin-fleet-lijst');
      var gekozen = {};
      (stand.kandidaten || []).forEach(function (k) {
        var r = el('div', 'fin-fleet-kandidaat');
        var vink = el('input', 'fin-fleet-vink');
        vink.type = 'checkbox';
        vink.id = 'fin-nodig-' + k.character_id;
        vink.addEventListener('change', function () { gekozen[k.character_id] = vink.checked; });
        var lab = el('label', 'fin-fleet-wie');
        lab.htmlFor = vink.id;
        var img = el('img');
        img.loading = 'lazy'; img.alt = '';
        img.src = 'https://images.evetech.net/characters/' + k.character_id + '/portrait?size=32';
        lab.appendChild(img);
        lab.appendChild(el('span', null, k.naam));
        if (k.corp) lab.appendChild(el('i', null, k.corp));
        r.appendChild(vink); r.appendChild(lab);
        lijst.appendChild(r);
      });
      if (!(stand.kandidaten || []).length) {
        lijst.appendChild(el('div', 'fin-leeg', 'Iedereen uit de Auth zit al in de fleet.'));
      }
      nodig.appendChild(lijst);

      // Namen typen, voor wie niet in de Auth staat — zoals op het dashboard.
      var namen = el('textarea', 'fin-zoekveld');
      namen.rows = 2;
      namen.placeholder = 'Of namen intypen — één per regel of gescheiden door komma\'s';
      nodig.appendChild(namen);

      var voet = el('div', 'fin-roam-knoprij');
      var squadLabel = el('label', 'fin-roam-squadlabel');
      squadLabel.appendChild(el('span', null, 'In squad'));
      squadLabel.appendChild(squadKies);
      voet.appendChild(squadLabel);
      var stuur = el('button', 'fin-knop', 'Uitnodiging versturen');
      stuur.addEventListener('click', function () {
        var ids = Object.keys(gekozen).filter(function (i) { return gekozen[i]; });
        if (!ids.length && !namen.value.trim()) {
          zegHet('Niemand aangevinkt of ingetypt.', false);
          return;
        }
        var d = squadKies.value.split(':');
        var velden = { actie: 'uitnodigen', namen: namen.value,
                       wing_id: d[0] || '', squad_id: d[1] || '' };
        // URLSearchParams kan meerdere waarden onder dezelfde naam.
        var body = new URLSearchParams(velden);
        ids.forEach(function (i) { body.append('character_id', i); });
        fetch(URL_DOE, {
          method: 'POST', credentials: 'same-origin',
          headers: { 'Content-Type': 'application/x-www-form-urlencoded',
                     'X-CSRFToken': csrf() },
          body: body.toString()
        }).then(function (r) { return r.json(); }).then(function (r) {
          zegHet(r.melding, r.ok);
          namen.value = '';
          haalStand();
        }).catch(function () { zegHet('De server antwoordde niet.', false); });
      });
      voet.appendChild(stuur);
      nodig.appendChild(voet);
      deel.beheer.appendChild(nodig);
    }

    /* ── Intel ───────────────────────────────────────────────────────── */
    function zetIntelStatus(tekst, soort) {
      if (!deel.intelStatus) return;
      deel.intelStatus.textContent = tekst;
      deel.intelStatus.className = 'fin-intel-status is-' + (soort || 'dim');
    }

    function tekenIntelLijst() {
      if (!deel.intelLijst) return;
      leeg(deel.intelLijst);
      var nu = Date.now();
      var recent = meldingen.slice().sort(function (a, b) { return b.tijd - a.tijd; }).slice(0, 25);
      if (!recent.length) {
        deel.intelLijst.appendChild(el('div', 'fin-leeg', 'Nog niets gemeld in ' + kanaal + '.'));
        return;
      }
      recent.forEach(function (m) {
        var min = Math.floor((nu - m.tijd) / 60000);
        var klasse = m.clear ? 'is-clear' : (min < VERS_MIN ? 'is-vers' :
                     (min < RECENT_MIN ? 'is-recent' : 'is-oud'));
        var r = el('div', 'fin-intel-regel ' + klasse);
        r.appendChild(el('span', 'fin-intel-tijd', min < 1 ? 'nu' : min + 'm'));
        var namen = m.systemen.map(function (sid) { return sys[sid] ? sys[sid][4] : sid; });
        if (namen.length) {
          var sysSpan = el('span', 'fin-intel-sys', namen.join(', '));
          sysSpan.addEventListener('click', function () {
            weergaveNaar('kaart');
            zoomOp(m.systemen[0], 24);
          });
          r.appendChild(sysSpan);
        }
        r.appendChild(el('span', 'fin-intel-tekst', m.bericht));
        r.appendChild(el('span', 'fin-intel-wie', m.zender));
        deel.intelLijst.appendChild(r);
      });
    }

    function leesStaart(file) {
      var start = file.size > STAART_BYTES ? file.size - STAART_BYTES : 0;
      return file.slice(0, 2).arrayBuffer().then(function (kop) {
        var b = new Uint8Array(kop);
        var utf16 = (b[0] === 0xff && b[1] === 0xfe) || (b[0] === 0xfe && b[1] === 0xff);
        if (utf16 && start % 2 !== 0) start++;
        return file.slice(start).arrayBuffer().then(decodeer);
      });
    }

    var intelBezig = false;
    function leesIntel() {
      if (intelBezig || !dirHandle) return;
      intelBezig = true;
      var namen = [], handles = {};
      var it = dirHandle.values();
      (function volgende() {
        return it.next().then(function (r) {
          if (r.done) return;
          if (r.value.kind === 'file') { namen.push(r.value.name); handles[r.value.name] = r.value; }
          return volgende();
        });
      })().then(function () {
        var uitslag = filterLogs(namen, kanaal);
        kanalenInMap = uitslag.kanalen;
        if (!uitslag.bestanden.length) {
          zetIntelStatus(kanalenInMap.length
            ? 'Geen ' + kanaal + ' in deze map. Wel gevonden: ' +
              kanalenInMap.slice(0, 8).join(', ') + (kanalenInMap.length > 8 ? ', …' : '')
            : 'Geen chatlogs in deze map. Kies de map EVE/logs/Chatlogs.', 'let-op');
          return;
        }
        var nu = Date.now();
        return Promise.all(uitslag.bestanden.map(function (naam) {
          return handles[naam].getFile().then(leesStaart)
            .then(function (t) { return parseIntel(t, naamIndex, nu); })
            .catch(function () { return []; });
        })).then(function (lijsten) {
          var gezien = {}, samen = [];
          lijsten.forEach(function (l) {
            l.forEach(function (m) {
              var k = m.zender + '|' + m.tijd + '|' + m.bericht;
              if (gezien[k]) return;
              gezien[k] = 1; samen.push(m);
            });
          });
          meldingen = samen;
          intel = perSysteem(meldingen);
          zetIntelStatus(kanaal + ' — ' + meldingen.length + ' meldingen · ' +
                         uitslag.bestanden.length + ' logbestand' +
                         (uitslag.bestanden.length === 1 ? '' : 'en'), 'aan');
          tekenIntelLijst();
          teken();
        });
      }).catch(function (err) {
        if (err && err.name === 'NotAllowedError') {
          intelToestand = 'toestemming';
          zetIntelStatus('Klik op de knop om je Chatlogs-map opnieuw vrij te geven', 'let-op');
        }
      }).then(function () { intelBezig = false; });
    }

    function volgMap(h) {
      dirHandle = h;
      idbZet(h);
      zetIntelStatus('Zoeken naar ' + kanaal + '…', 'dim');
      leesIntel();
    }

    /* ── Weergave wisselen ───────────────────────────────────────────── */
    function weergaveNaar(w) {
      weergave = w;
      wortel.setAttribute('data-toont', w);
      if (deel.knoppen) {
        Array.prototype.forEach.call(deel.knoppen.querySelectorAll('button'), function (b) {
          b.classList.toggle('is-actief', b.getAttribute('data-naar') === w);
        });
      }
      if (w === 'kaart') { pasMaatAan(); teken(); }
    }

    function pasMaatAan() {
      if (!deel.canvas || !deel.kaartVak) return;
      // Het tekenvlak blijft 660×760; alleen hoe groot dat op het scherm komt
      // hangt van de ruimte af. Staand, want de cluster is hoger dan breed.
      var vh = window.innerHeight || 900;
      var kolom = (deel.kaartVak.parentNode && deel.kaartVak.parentNode.clientWidth) || 900;
      var hoogte = Math.max(420, Math.min(Math.round(vh * 0.84), 1100));
      var breedte = Math.min(kolom, Math.round(hoogte * (W / H)));
      hoogte = Math.round(breedte * (H / W));
      toonSchaal = breedte / W;
      deel.canvas.style.width = breedte + 'px';
      deel.canvas.style.height = hoogte + 'px';
      maakBasis();
    }

    /* ── Ophalen ─────────────────────────────────────────────────────── */
    function haalStand() {
      return fetch(URL_STAND, { credentials: 'same-origin' })
        .then(function (r) { return r.json(); })
        .then(function (s) {
          stand = s;
          wortel.setAttribute('data-toestand',
            !s.gekoppeld ? 'geen-fc' : (s.in_fleet ? 'fleet' : 'geen-fleet'));
          // Pas ná dat attribuut is het kaartvak zichtbaar en dus op te meten.
          // Ervoor is clientWidth nul en komt er een onbruikbare projectie uit —
          // dan zie je heel New Eden in plaats van de fleet.
          pasMaatAan();
          var naam = wortel.querySelector('[data-fcnaam]');
          if (naam && s.ik) naam.textContent = s.ik.naam;
          tekenStats();
          tekenLeden();
          tekenBeheer();
          // Eén keer inzoomen op de FC, zoals `didAuto` op het dashboard: k = 24,
          // gecentreerd op zijn systeem. Daarna blijft staan wat jij instelt.
          if (s.in_fleet && !autoGezoomd && basis) {
            var doel = (s.fc && s.fc.systeem_id) ||
                       (s.leden[0] && s.leden[0].systeem_id);
            if (doel && sys[doel]) {
              autoGezoomd = true;
              zoomOp(doel, 24);
              return;
            }
          }
          teken();
        }).catch(function () {});
    }

    Promise.all([
      fetch(URL_KAART, { credentials: 'same-origin' }).then(function (r) { return r.json(); }),
      fetch(URL_JUMPS, { credentials: 'same-origin' }).then(function (r) { return r.json(); })
        .catch(function () { return {}; })
    ]).then(function (paar) {
      var k = paar[0];
      k.s.forEach(function (rij) {
        sys[rij[0]] = rij;
        naamIndex[rij[4].toLowerCase()] = rij[0];
      });
      regios = k.r || {};
      bruggen = k.bruggen || [];
      buren = paar[1] || {};
      pasMaatAan();
      haalStand();
      idbGet().then(function (h) {
        if (!h) {
          zetIntelStatus('Kies je map EVE/logs/Chatlogs voor intel op de kaart', 'dim');
          return;
        }
        h.queryPermission({ mode: 'read' }).then(function (p) {
          if (p === 'granted') volgMap(h);
          else {
            dirHandle = h; intelToestand = 'toestemming';
            zetIntelStatus('Klik op de knop om je Chatlogs-map vrij te geven', 'let-op');
          }
        }).catch(function () {});
      });
    }).catch(function () {
      zetIntelStatus('De kaartgegevens konden niet geladen worden.', 'let-op');
    });

    /* ── Muis ────────────────────────────────────────────────────────── */
    if (deel.canvas) {
      var sleept = null;
      deel.canvas.addEventListener('mousedown', function (e) {
        if (e.button !== 0) return;
        sleept = { x: e.clientX, y: e.clientY, ox: tf.x, oy: tf.y };
        sluitMenu();
      });
      window.addEventListener('mouseup', function () { sleept = null; });
      deel.canvas.addEventListener('mousemove', function (e) {
        if (sleept) {
          tf.x = sleept.ox + (e.clientX - sleept.x);
          tf.y = sleept.oy + (e.clientY - sleept.y);
          teken();
          return;
        }
        // Boven een intel-marker? Dan de meldingen laten zien.
        var vak = deel.canvas.getBoundingClientRect();
        var px = e.clientX - vak.left, py = e.clientY - vak.top;
        var dichtbij = null;
        Object.keys(intel).forEach(function (sid) {
          var s = sys[sid];
          if (!s || intel[sid].clear) return;
          var p = scherm(s[1], s[2]);
          if (Math.hypot(p[0] - px, p[1] - py) < 14) dichtbij = sid;
        });
        if (dichtbij) toonTip(e.clientX, e.clientY, dichtbij); else verbergTip();
      });
      deel.canvas.addEventListener('mouseleave', verbergTip);
      deel.canvas.addEventListener('contextmenu', function (e) {
        e.preventDefault();
        var vak = deel.canvas.getBoundingClientRect();
        var sid = dichtstbij(e.clientX - vak.left, e.clientY - vak.top);
        if (sid) toonMenu(e.clientX, e.clientY, sid);
      });
      deel.canvas.addEventListener('wheel', function (e) {
        e.preventDefault();
        var vak = deel.canvas.getBoundingClientRect();
        var mx = e.clientX - vak.left, my = e.clientY - vak.top;
        var f = e.deltaY < 0 ? 1.18 : 1 / 1.18;
        var k = Math.max(0.8, Math.min(40, tf.k * f));
        var fr = k / tf.k;
        tf = { k: k, x: mx - (mx - tf.x) * fr, y: my - (my - tf.y) * fr };
        teken();
      }, { passive: false });
      window.addEventListener('click', sluitMenu);
      window.addEventListener('resize', function () { pasMaatAan(); teken(); });
    }

    if (deel.knoppen) {
      Array.prototype.forEach.call(deel.knoppen.querySelectorAll('button'), function (b) {
        b.addEventListener('click', function () { weergaveNaar(b.getAttribute('data-naar')); });
      });
    }
    var koppelKnop = wortel.querySelector('[data-intel-koppel]');
    if (koppelKnop) {
      koppelKnop.addEventListener('click', function () {
        if (dirHandle && intelToestand === 'toestemming') {
          dirHandle.requestPermission({ mode: 'read' }).then(function (p) {
            if (p === 'granted') volgMap(dirHandle);
          }).catch(function () {});
          return;
        }
        if (typeof window.showDirectoryPicker !== 'function') {
          window.alert('Deze browser kan geen map volgen; gebruik Chrome of Edge.');
          return;
        }
        window.showDirectoryPicker({ id: 'eve-chatlogs', mode: 'read' })
          .then(volgMap).catch(function () {});
      });
    }
    Array.prototype.forEach.call(wortel.querySelectorAll('[data-zoom]'), function (b) {
      b.addEventListener('click', function () {
        var wat = b.getAttribute('data-zoom');
        if (wat === 'alles') { tf = { k: 1, x: 0, y: 0 }; teken(); return; }
        var f = wat === 'in' ? 1.4 : 1 / 1.4;
        var k = Math.max(0.8, Math.min(40, tf.k * f));
        var fr = k / tf.k;
        tf = { k: k, x: W / 2 - (W / 2 - tf.x) * fr, y: H / 2 - (H / 2 - tf.y) * fr };
        teken();
      });
    });

    var fcZoom = wortel.querySelector('[data-naar-fc]');
    if (fcZoom) {
      fcZoom.addEventListener('click', function () {
        if (stand && stand.fc && stand.fc.systeem_id) zoomOp(stand.fc.systeem_id, 24);
      });
    }

    weergaveNaar('kaart');
    setInterval(haalStand, 15000);
    setInterval(function () { leesIntel(); tekenIntelLijst(); }, 5000);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var wortel = document.querySelector('[data-fleetroam]');
    if (wortel) start(wortel);
  });
})();
