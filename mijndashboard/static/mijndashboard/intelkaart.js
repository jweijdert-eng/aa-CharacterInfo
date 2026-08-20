/* Intel-kaart voor Fleet Roaming.
 *
 * Twee dingen op één plek: waar de fleet staat (uit ESI, komt van de server mee)
 * en wat er in het intel-kanaal gemeld wordt (uit je eigen EVE-chatlogs, hier in
 * de browser gelezen).
 *
 * De map wordt hier **apart** gekozen, net als op dutchlegionsdashboard.eu/fleet.
 * Eerst hergebruikte deze pagina de mapverwijzing van het Local-tabblad, maar dat
 * hoeft niet dezelfde map te zijn — en dan zoekt hij in de verkeerde. Je kiest
 * hier dus bewust je Chatlogs-map, en die wordt onder een eigen sleutel bewaard.
 *
 * Wat de kaart NIET doet: sprongen tekenen. De stargate-tabel is in deze
 * installatie leeg, dus afstanden op deze kaart zijn hemelsbreed en niet in
 * jumps. Beter geen getal dan een verkeerd getal.
 */
(function () {
  'use strict';

  var IDB_NAAM = 'mijndashboard', IDB_STORE = 'fs-handles', IDB_SLEUTEL = 'intel-dir';
  var POLL_MS = 5000;              // even vaak als het spel z'n log wegschrijft
  var STAART_BYTES = 512 * 1024;   // recente meldingen staan achteraan
  var INTEL_MAX_MIN = 60;          // ouder dan een uur is geen intel meer
  var VERS_MIN = 5, RECENT_MIN = 15;
  var MSG_RE = /^\[\s*(\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})\s*\]\s*([^>]+)>\s*(.*)$/;
  var CLEAR_RE = /\b(clr|clear|clr\.|nv|no vis|niks|leeg)\b/i;
  var SPIKE_RE = /\b(spike|spiked|blob)\b/i;

  /* ── IndexedDB — eigen sleutel, los van localchat.js ──────────────────── */
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
  function idbZet(handle) {
    return idb().then(function (db) {
      db.transaction(IDB_STORE, 'readwrite').objectStore(IDB_STORE).put(handle, IDB_SLEUTEL);
    }).catch(function () {});
  }

  /* ── EVE-logs zijn UTF-16LE, met een BOM aan het begin van elke regel ──── */
  function decodeer(buf) {
    var b = new Uint8Array(buf);
    if (b.length >= 2 && b[0] === 0xff && b[1] === 0xfe) return new TextDecoder('utf-16le').decode(b.subarray(2));
    if (b.length >= 2 && b[0] === 0xfe && b[1] === 0xff) return new TextDecoder('utf-16be').decode(b.subarray(2));
    if (b.length >= 3 && b[0] === 0xef && b[1] === 0xbb && b[2] === 0xbf) return new TextDecoder('utf-8').decode(b.subarray(3));
    var monster = Math.min(b.length, 400), nul = 0;
    for (var i = 0; i < monster; i++) if (b[i] === 0) nul++;
    if (monster > 0 && nul / monster > 0.2) return new TextDecoder('utf-16le').decode(b);
    return new TextDecoder('utf-8').decode(b);
  }

  function tijdVan(stempel) {
    // "2026.08.20 14:30:00" — EVE schrijft in UTC.
    var m = /^(\d{4})\.(\d{2})\.(\d{2})\s+(\d{2}):(\d{2}):(\d{2})$/.exec(stempel);
    if (!m) return null;
    return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  }

  /* ── Systeemnamen in een bericht herkennen ────────────────────────────────
   * Een intel-melding is "Y-2ANO 3 reds" of "clr J5A-IX". De namen komen uit de
   * kaart, dus we hoeven niets te raden — wel moeten we op hele woorden matchen,
   * anders vindt "Jita" ook een stuk van een langere naam.
   */
  function zoekSystemen(bericht, index) {
    var uit = [], gezien = {};
    var woorden = bericht.split(/[^A-Za-z0-9\-']+/);
    for (var i = 0; i < woorden.length; i++) {
      var w = woorden[i];
      if (w.length < 3) continue;
      var sid = index[w.toLowerCase()];
      // Een naam van drie letters ("Amo") komt ook in gewone tekst voor; die
      // nemen we alleen als het bericht verder niets oplevert.
      if (sid && !gezien[sid]) { gezien[sid] = 1; uit.push(sid); }
    }
    return uit;
  }

  function parseIntel(tekst, index, nu) {
    var regels = tekst.split(/\r?\n/), uit = [];
    for (var i = 0; i < regels.length; i++) {
      // trim() haalt de BOM weg die EVE vóór elke regel zet; zonder dat matcht
      // de regex op geen enkele regel.
      var m = MSG_RE.exec(regels[i].trim());
      if (!m) continue;
      var tijd = tijdVan(m[1]);
      if (tijd === null) continue;
      if (nu && nu - tijd > INTEL_MAX_MIN * 60000) continue;
      var bericht = m[3].trim();
      if (!bericht) continue;
      // "EVE System > Channel changed to Local" enzovoort is geen intel.
      if (m[2].trim() === 'EVE System') continue;
      var systemen = zoekSystemen(bericht, index);
      uit.push({
        tijd: tijd, zender: m[2].trim(), bericht: bericht,
        systemen: systemen,
        clear: CLEAR_RE.test(bericht),
        spike: SPIKE_RE.test(bericht)
      });
    }
    return uit;
  }

  /* Welke bestanden in een map bij dit kanaal horen, en welke kanalen er verder
   * in staan. Los van de File System API gehouden zodat het te testen is: dit is
   * precies het stuk dat "verkeerde map" zichtbaar moet maken.
   *
   * EVE noemt een chatlog "<Kanaal>_YYYYMMDD_HHMMSS_<charid>.txt" en maakt er één
   * per client-sessie. Met drie accounts open staan er dus drie actuele bestanden
   * van hetzelfde kanaal; alleen het nieuwste pakken laat meldingen liggen.
   */
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

  /* Per systeem de laatste stand: gemeld of vrijgegeven. */
  function perSysteem(meldingen) {
    var kaart = {};
    for (var i = 0; i < meldingen.length; i++) {
      var m = meldingen[i];
      for (var j = 0; j < m.systemen.length; j++) {
        var sid = m.systemen[j];
        var b = kaart[sid];
        if (!b || m.tijd >= b.tijd) {
          kaart[sid] = { tijd: m.tijd, clear: m.clear, spike: m.spike,
                         bericht: m.bericht, zender: m.zender, aantal: (b ? b.aantal : 0) + 1 };
        } else {
          b.aantal++;
        }
      }
    }
    return kaart;
  }

  // Voor de test in node.
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = { parseIntel: parseIntel, perSysteem: perSysteem,
                       zoekSystemen: zoekSystemen, tijdVan: tijdVan,
                       filterLogs: filterLogs };
  }
  if (typeof window === 'undefined') return;

  /* ── De kaart ─────────────────────────────────────────────────────────── */
  function Kaart(canvas, systemen) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.systemen = systemen;
    this.schaal = 1;
    this.midX = 0;
    this.midZ = 0;
    this.fleet = {};
    this.intel = {};
    this.hover = null;
  }

  Kaart.prototype.pasIn = function (ids) {
    // Inzoomen op wat er toe doet: de fleet en de intel. Zonder dat kijk je
    // naar heel New Eden en is alles een stip.
    var punten = [];
    for (var i = 0; i < this.systemen.length; i++) {
      var s = this.systemen[i];
      if (ids[s[0]]) punten.push(s);
    }
    if (!punten.length) punten = this.systemen;
    var minX = Infinity, maxX = -Infinity, minZ = Infinity, maxZ = -Infinity;
    for (var j = 0; j < punten.length; j++) {
      minX = Math.min(minX, punten[j][1]); maxX = Math.max(maxX, punten[j][1]);
      minZ = Math.min(minZ, punten[j][2]); maxZ = Math.max(maxZ, punten[j][2]);
    }
    var marge = 60;
    this.midX = (minX + maxX) / 2;
    this.midZ = (minZ + maxZ) / 2;
    var b = this.canvas.width, h = this.canvas.height;
    this.schaal = Math.min(b / (maxX - minX + marge), h / (maxZ - minZ + marge));
    if (!isFinite(this.schaal) || this.schaal <= 0) this.schaal = 1;
    this.schaal = Math.min(this.schaal, 8);
  };

  Kaart.prototype.naarScherm = function (x, z) {
    return [this.canvas.width / 2 + (x - this.midX) * this.schaal,
            this.canvas.height / 2 + (z - this.midZ) * this.schaal];
  };

  Kaart.prototype.leeftijdKleur = function (min) {
    if (min < VERS_MIN) return '#ef4444';
    if (min < RECENT_MIN) return '#f0932b';
    return '#8a90b0';
  };

  Kaart.prototype.teken = function () {
    var ctx = this.ctx, b = this.canvas.width, h = this.canvas.height;
    ctx.clearRect(0, 0, b, h);
    ctx.fillStyle = '#07070f';
    ctx.fillRect(0, 0, b, h);

    var nu = Date.now();
    var self = this;
    var labels = [];

    // 1) Alle sterren, flauw. Zo zie je de vorm van de cluster eromheen.
    ctx.fillStyle = 'rgba(120,130,170,0.28)';
    for (var i = 0; i < this.systemen.length; i++) {
      var s = this.systemen[i];
      var p = this.naarScherm(s[1], s[2]);
      if (p[0] < -20 || p[0] > b + 20 || p[1] < -20 || p[1] > h + 20) continue;
      ctx.fillRect(p[0], p[1], 1.2, 1.2);
    }

    // 2) Intel — rood als het net gemeld is, oranje daarna, grijs als het oud is.
    Object.keys(this.intel).forEach(function (sid) {
      var s = self.index[sid];
      if (!s) return;
      var i2 = self.intel[sid];
      var p = self.naarScherm(s[1], s[2]);
      var min = (nu - i2.tijd) / 60000;
      var kleur = i2.clear ? '#3ecf6e' : self.leeftijdKleur(min);
      ctx.beginPath();
      ctx.arc(p[0], p[1], i2.spike ? 9 : 6, 0, Math.PI * 2);
      ctx.strokeStyle = kleur;
      ctx.lineWidth = i2.spike ? 2.5 : 1.5;
      ctx.stroke();
      if (!i2.clear) {
        ctx.fillStyle = kleur;
        ctx.font = 'bold 11px system-ui, sans-serif';
        ctx.fillText(i2.spike ? '!!' : '!', p[0] - 3, p[1] + 4);
      }
      labels.push([p[0], p[1], s[4], kleur]);
    });

    // 3) De fleet — groene ring met het aantal erin, zoals de kaart in het spel.
    Object.keys(this.fleet).forEach(function (sid) {
      var s = self.index[sid];
      if (!s) return;
      var f = self.fleet[sid];
      var p = self.naarScherm(s[1], s[2]);
      ctx.beginPath();
      ctx.arc(p[0], p[1], 11, 0, Math.PI * 2);
      ctx.strokeStyle = f.fc ? '#f0c040' : '#3ecf6e';
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.fillStyle = 'rgba(62,207,110,0.14)';
      ctx.fill();
      ctx.fillStyle = f.fc ? '#f0c040' : '#3ecf6e';
      ctx.font = 'bold 11px system-ui, sans-serif';
      ctx.textAlign = 'center';
      ctx.fillText(String(f.aantal), p[0], p[1] + 4);
      ctx.textAlign = 'left';
      labels.push([p[0], p[1], s[4], f.fc ? '#f0c040' : '#3ecf6e']);
    });

    // 4) Namen bovenop, zodat ze niet onder een stip verdwijnen.
    ctx.font = '10px system-ui, sans-serif';
    for (var k = 0; k < labels.length; k++) {
      ctx.fillStyle = labels[k][3];
      ctx.fillText(labels[k][2], labels[k][0] + 13, labels[k][1] + 3);
    }
  };

  /* ── Alles aan elkaar knopen ──────────────────────────────────────────── */
  function start(paneel) {
    var canvas = paneel.querySelector('[data-kaart]');
    var lijst = paneel.querySelector('[data-intel-lijst]');
    var status = paneel.querySelector('[data-intel-status]');
    var knop = paneel.querySelector('[data-intel-koppel]');
    var kanaal = paneel.getAttribute('data-kanaal') || 'Insidious.Intel';
    var fleet = {};
    try { fleet = JSON.parse(paneel.getAttribute('data-fleet') || '{}'); } catch (e) { fleet = {}; }

    var kaart = null, index = {}, naamIndex = {};
    var dirHandle = null, toestand = 'geen-map', meldingen = [], bezig = false;
    var kanalenInMap = [];

    function zetStatus(tekst, soort) {
      if (!status) return;
      status.textContent = tekst;
      status.className = 'fin-intel-status is-' + (soort || 'dim');
    }

    function tekenLijst() {
      if (!lijst) return;
      var nu = Date.now();
      var recent = meldingen.slice().sort(function (a, b) { return b.tijd - a.tijd; }).slice(0, 25);
      if (!recent.length) {
        lijst.innerHTML = '<div class="fin-leeg">Nog niets gemeld in ' + kanaal + '.</div>';
        return;
      }
      var html = '';
      for (var i = 0; i < recent.length; i++) {
        var m = recent[i];
        var min = Math.floor((nu - m.tijd) / 60000);
        var klasse = m.clear ? 'is-clear' : (min < VERS_MIN ? 'is-vers' : (min < RECENT_MIN ? 'is-recent' : 'is-oud'));
        var namen = m.systemen.map(function (sid) {
          return index[sid] ? index[sid][4] : sid;
        }).join(', ');
        html += '<div class="fin-intel-regel ' + klasse + '">' +
          '<span class="fin-intel-tijd">' + (min < 1 ? 'nu' : min + 'm') + '</span>' +
          (namen ? '<span class="fin-intel-sys">' + namen + '</span>' : '') +
          '<span class="fin-intel-tekst"></span>' +
          '<span class="fin-intel-wie"></span></div>';
      }
      lijst.innerHTML = html;
      // Tekst als tekstknoop zetten, niet als HTML: het komt uit een chatvenster.
      var regels = lijst.querySelectorAll('.fin-intel-regel');
      for (var j = 0; j < regels.length; j++) {
        regels[j].querySelector('.fin-intel-tekst').textContent = recent[j].bericht;
        regels[j].querySelector('.fin-intel-wie').textContent = recent[j].zender;
      }
    }

    function tekenKaart() {
      if (!kaart) return;
      kaart.intel = perSysteem(meldingen);
      kaart.teken();
    }

    function pasCanvasAan() {
      if (!canvas) return;
      // Begrensd: op een breed scherm werd de kaart anders zo hoog dat de
      // ledenlijst eronder van het scherm viel.
      var breed = canvas.parentNode.clientWidth || 600;
      canvas.width = breed;
      canvas.height = Math.min(400, Math.max(240, Math.round(breed * 0.30)));
    }

    /* De logbestanden zoeken. EVE noemt ze "<Kanaal>_YYYYMMDD_HHMMSS_<id>.txt"
     * en maakt er één per client-sessie. Met meerdere accounts open staan er dus
     * meerdere actuele bestanden van hetzelfde kanaal; alleen het nieuwste pakken
     * laat de meldingen van je andere characters liggen. We nemen ze allemaal.
     *
     * Onderweg noteren we ook wélke kanalen er in de map zitten. Dat is het enige
     * dat "je hebt de verkeerde map gekozen" zichtbaar maakt: staat je kanaal er
     * niet bij, dan zie je meteen wat er wél is. */
    function zoekLogbestanden() {
      if (!dirHandle) return Promise.resolve({ bestanden: [], kanalen: [] });
      var namen = [], handles = {};
      var it = dirHandle.values();
      function volgende() {
        return it.next().then(function (r) {
          if (r.done) {
            var uitslag = filterLogs(namen, kanaal);
            return { bestanden: uitslag.bestanden.map(function (n) { return handles[n]; }),
                     kanalen: uitslag.kanalen };
          }
          if (r.value.kind === 'file') {
            namen.push(r.value.name);
            handles[r.value.name] = r.value;
          }
          return volgende();
        });
      }
      return volgende();
    }

    /* Alleen de staart lezen: een chatlog van een lange avond wordt megabytes
     * groot, en de recente meldingen staan achteraan. Bij UTF-16 moet de knip op
     * een even byte vallen, anders schuift elk teken een halve positie op. */
    function leesStaart(file) {
      var start = file.size > STAART_BYTES ? file.size - STAART_BYTES : 0;
      return file.slice(0, 2).arrayBuffer().then(function (kop) {
        var b = new Uint8Array(kop);
        var utf16 = (b[0] === 0xff && b[1] === 0xfe) || (b[0] === 0xfe && b[1] === 0xff);
        if (utf16 && start % 2 !== 0) start++;
        return file.slice(start).arrayBuffer().then(function (buf) {
          // Zonder de BOM van het begin herkent decodeer() UTF-16 aan de
          // NUL-bytes; dat is precies waar die telling voor is.
          return start === 0 ? decodeer(buf) : decodeer(buf);
        });
      });
    }

    function lees() {
      if (bezig || !dirHandle) return;
      bezig = true;
      zoekLogbestanden().then(function (r) {
        kanalenInMap = r.kanalen;
        if (!r.bestanden.length) {
          toestand = 'geen-bestand';
          zetStatus(geenBestandTekst(), 'let-op');
          tekenLijst();
          return null;
        }
        var nu = Date.now();
        return Promise.all(r.bestanden.map(function (h) {
          return h.getFile()
            .then(leesStaart)
            .then(function (tekst) { return parseIntel(tekst, naamIndex, nu); })
            .catch(function () { return []; });
        })).then(function (lijsten) {
          // Meerdere clients loggen hetzelfde kanaal, dus dezelfde melding komt
          // in meerdere bestanden voor. Ontdubbelen op zender + tijd + tekst.
          var gezien = {}, samen = [];
          lijsten.forEach(function (lijst) {
            lijst.forEach(function (m) {
              var sleutel = m.zender + '|' + m.tijd + '|' + m.bericht;
              if (gezien[sleutel]) return;
              gezien[sleutel] = 1;
              samen.push(m);
            });
          });
          meldingen = samen;
          toestand = 'volgt';
          zetStatus(kanaal + ' — ' + meldingen.length + ' meldingen in het laatste uur (' +
                    r.bestanden.length + ' logbestand' + (r.bestanden.length === 1 ? '' : 'en') + ')', 'aan');
          tekenLijst();
          tekenKaart();
        });
      }).catch(function (err) {
        if (err && err.name === 'NotAllowedError') {
          toestand = 'toestemming';
          zetStatus('Klik op de knop om je Chatlogs-map opnieuw vrij te geven', 'let-op');
        }
      }).then(function () { bezig = false; });
    }

    /* De belangrijkste melding van deze pagina: als het kanaal er niet bij zit,
     * zeggen wélke kanalen er dan wel in de gekozen map staan. Anders blijf je
     * raden of je de verkeerde map hebt of dat het kanaal dicht staat. */
    function geenBestandTekst() {
      if (!kanalenInMap.length) {
        return 'Geen chatlogs in deze map. Kies de map EVE/logs/Chatlogs.';
      }
      var lijst = kanalenInMap.slice(0, 8).join(', ');
      if (kanalenInMap.length > 8) lijst += ', …';
      return 'Geen ' + kanaal + ' in deze map. Wel gevonden: ' + lijst;
    }

    function volgMap(handle) {
      dirHandle = handle;
      idbZet(handle);
      zetStatus('Zoeken naar ' + kanaal + '…', 'dim');
      lees();
    }

    // Kaartgegevens ophalen — één bestand, daarna cachet de browser het.
    fetch(paneel.getAttribute('data-kaart-url'), { credentials: 'same-origin' })
      .then(function (r) { return r.json(); })
      .then(function (k) {
        for (var i = 0; i < k.s.length; i++) {
          index[k.s[i][0]] = k.s[i];
          naamIndex[k.s[i][4].toLowerCase()] = k.s[i][0];
        }
        if (canvas) {
          pasCanvasAan();
          kaart = new Kaart(canvas, k.s);
          kaart.index = index;
          kaart.fleet = fleet;
          var belang = {};
          Object.keys(fleet).forEach(function (sid) { belang[sid] = 1; });
          kaart.pasIn(belang);
          kaart.teken();
        }
        lees();
      })
      .catch(function () { zetStatus('Kaartgegevens konden niet geladen worden.', 'let-op'); });

    if (knop) {
      knop.addEventListener('click', function () {
        if (dirHandle && toestand === 'toestemming') {
          dirHandle.requestPermission({ mode: 'read' }).then(function (p) {
            if (p === 'granted') volgMap(dirHandle);
          }).catch(function () {});
          return;
        }
        if (typeof window.showDirectoryPicker !== 'function') {
          window.alert('Deze browser kan geen map volgen; gebruik Chrome of Edge.');
          return;
        }
        // Altijd de kiezer openen, ook als er al een map staat: zo kun je een
        // verkeerd gekozen map corrigeren zonder eerst iets te wissen.
        window.showDirectoryPicker({ id: 'eve-chatlogs', mode: 'read' })
          .then(volgMap).catch(function () {});
      });
    }

    // Slepen en zoomen op de kaart.
    if (canvas) {
      var sleept = false, vorig = null;
      canvas.addEventListener('mousedown', function (e) { sleept = true; vorig = [e.clientX, e.clientY]; });
      window.addEventListener('mouseup', function () { sleept = false; });
      window.addEventListener('mousemove', function (e) {
        if (!sleept || !kaart) return;
        kaart.midX -= (e.clientX - vorig[0]) / kaart.schaal;
        kaart.midZ -= (e.clientY - vorig[1]) / kaart.schaal;
        vorig = [e.clientX, e.clientY];
        kaart.teken();
      });
      canvas.addEventListener('wheel', function (e) {
        if (!kaart) return;
        e.preventDefault();
        kaart.schaal *= e.deltaY < 0 ? 1.15 : 0.87;
        kaart.schaal = Math.max(0.05, Math.min(kaart.schaal, 30));
        kaart.teken();
      }, { passive: false });
      window.addEventListener('resize', function () {
        pasCanvasAan();
        if (kaart) kaart.teken();
      });
    }
    var terug = paneel.querySelector('[data-kaart-terug]');
    if (terug) {
      terug.addEventListener('click', function () {
        if (!kaart) return;
        var belang = {};
        Object.keys(fleet).forEach(function (sid) { belang[sid] = 1; });
        Object.keys(kaart.intel).forEach(function (sid) { belang[sid] = 1; });
        kaart.pasIn(belang);
        kaart.teken();
      });
    }

    idbGet().then(function (handle) {
      if (!handle) {
        zetStatus('Kies je map EVE/logs/Chatlogs om intel op de kaart te krijgen', 'dim');
        return;
      }
      handle.queryPermission({ mode: 'read' }).then(function (p) {
        if (p === 'granted') {
          volgMap(handle);
        } else {
          dirHandle = handle;
          toestand = 'toestemming';
          zetStatus('Klik op de knop om de Chatlogs-map opnieuw vrij te geven', 'let-op');
          if (knop) knop.textContent = 'Toegang geven';
        }
      }).catch(function () {});
    });

    setInterval(function () { lees(); tekenLijst(); }, POLL_MS);
  }

  document.addEventListener('DOMContentLoaded', function () {
    var paneel = document.querySelector('[data-intelkaart]');
    if (paneel) start(paneel);
  });
})();
