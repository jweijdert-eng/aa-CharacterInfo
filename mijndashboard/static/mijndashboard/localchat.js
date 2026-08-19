/* Local chat — leest EVE's chatlogs rechtstreeks in de browser.
 *
 * Overgenomen van `useLocalChat.ts` + `LocalChatWidget.tsx` van
 * dutchlegionsdashboard.eu. Waarom in de browser en niet op de server: EVE
 * schrijft die logs op de pc van het lid, en een Alliance Auth-server komt daar
 * niet bij. Het lid wijst zijn Chatlogs-map één keer aan; de verwijzing gaat in
 * IndexedDB zodat het daarna vanzelf blijft werken.
 *
 * Wat de server wél doet: zeggen wie er vriendelijk is. Die standings hangen aan
 * tokens, en die horen niet in een browser thuis.
 */
(function () {
  'use strict';

  // Zelfde regel als in de logs zelf: [ 2024.01.12 14:30:00 ] Naam > Bericht
  var MSG_RE = /^\[ (\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2}) \] (.+?) > (.+)$/;
  var POLL_MS = 1500;
  var RESCAN_ELKE = 7;          // ~10s: EVE begint bij een nieuwe sessie een nieuw bestand
  var MAX_BERICHTEN = 1000;
  var IDB_NAAM = 'mijndashboard', IDB_STORE = 'fs-handles', IDB_SLEUTEL = 'chatlogs-dir';
  var OVERRIDE_SLEUTEL = 'mijndashboard-standings';

  /* ── IndexedDB: de mapverwijzing bewaren ──────────────────────────────── */
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

  /* ── EVE-logs zijn UTF-16LE met BOM; combat-logs zijn UTF-8 ───────────── */
  function decodeer(buf) {
    var b = new Uint8Array(buf);
    if (b.length >= 2 && b[0] === 0xff && b[1] === 0xfe) return new TextDecoder('utf-16le').decode(b.subarray(2));
    if (b.length >= 2 && b[0] === 0xfe && b[1] === 0xff) return new TextDecoder('utf-16be').decode(b.subarray(2));
    if (b.length >= 3 && b[0] === 0xef && b[1] === 0xbb && b[2] === 0xbf) return new TextDecoder('utf-8').decode(b.subarray(3));
    // Geen BOM: veel NUL-bytes wijst op UTF-16LE.
    var monster = Math.min(b.length, 400), nul = 0;
    for (var i = 0; i < monster; i++) if (b[i] === 0) nul++;
    if (monster > 0 && nul / monster > 0.2) return new TextDecoder('utf-16le').decode(b);
    return new TextDecoder('utf-8').decode(b);
  }

  function parse(tekst) {
    var uit = [];
    var regels = tekst.split(/\r?\n/);
    for (var i = 0; i < regels.length; i++) {
      // EVE zet aan het begin van ELKE regel een BOM, niet alleen bovenaan het
      // bestand. trim() haalt die weg — in JavaScript telt U+FEFF als witruimte —
      // en zonder dat matcht geen enkele regel: nagemeten op een echte
      // Local_*.txt van 317 kB gaf de kale regex 0 van de 1547 regels.
      var regel = regels[i].trim();
      var m = MSG_RE.exec(regel);
      if (m) uit.push({ tijd: m[1], zender: m[2].trim(), bericht: m[3] });
    }
    return uit.slice(-MAX_BERICHTEN);
  }

  /* ── Standings ────────────────────────────────────────────────────────── */
  function overrides() {
    try { return JSON.parse(localStorage.getItem(OVERRIDE_SLEUTEL) || '{}'); } catch (e) { return {}; }
  }
  function zetOverride(naam, waarde) {
    var o = overrides();
    if (waarde) { o[naam] = waarde; } else { delete o[naam]; }
    try { localStorage.setItem(OVERRIDE_SLEUTEL, JSON.stringify(o)); } catch (e) {}
  }

  /* Handmatig > eigen character > eigen corp/alliance > contacten. Wie daarna
     overblijft kleurt rood: een onbekende in local is een risico, geen neutraal
     gegeven. Zelfde regel als `effectiveStanding()` op de site. */
  function standingVan(naam, eigenNamen, serverStanding, hand) {
    var laag = naam.toLowerCase();
    for (var i = 0; i < eigenNamen.length; i++) {
      if (eigenNamen[i].toLowerCase() === laag) return 'own';
    }
    if (hand[naam] === 'friend') return 'friend';
    if (hand[naam] === 'enemy') return 'enemy';
    if (serverStanding === 'corp' || serverStanding === 'alliance' || serverStanding === 'friend') {
      return serverStanding;
    }
    return 'enemy';
  }
  function vriendelijk(s) { return s === 'own' || s === 'corp' || s === 'alliance' || s === 'friend'; }
  function kleurVan(s) {
    if (s === 'own' || s === 'corp' || s === 'friend') return 'var(--dl-green)';
    if (s === 'alliance') return '#7fe0ff';                 // lichtblauw, zoals in-game
    return 'var(--dl-red)';
  }
  function rijAchtergrond(s, genoemd, oneven) {
    if (s === 'enemy') return 'rgba(224,85,85,0.09)';
    if (s === 'alliance') return 'rgba(0,180,216,0.07)';
    if (vriendelijk(s)) return 'rgba(62,207,110,0.07)';
    if (genoemd) return 'rgba(240,192,64,0.06)';
    return oneven ? 'rgba(15,15,34,0.35)' : 'transparent';
  }
  function teken(s) {
    if (s === 'enemy') return '▼';
    if (s === 'own') return '';
    return vriendelijk(s) ? '▲' : '';
  }

  /* ── Eén paneel ───────────────────────────────────────────────────────── */
  function start(paneel) {
    var url = paneel.dataset.standingsUrl;
    var csrf = paneel.dataset.csrf;
    var eigen = JSON.parse(paneel.dataset.eigen || '[]');
    var maxRijen = parseInt(paneel.dataset.max || '60', 10);
    var lijst = paneel.querySelector('[data-rol="lijst"]');
    var statusEl = paneel.querySelector('[data-rol="status"]');
    var knop = paneel.querySelector('[data-rol="kies"]');
    var bestandKnop = paneel.querySelector('[data-rol="kies-bestand"]');
    var naamEl = paneel.querySelector('[data-rol="bestand"]');

    var berichten = [], status = 'idle', bestandsnaam = null;
    var dirHandle = null, fileHandle = null, teller = 0, bekend = {}, bezig = false;

    var kanMap = typeof window.showDirectoryPicker === 'function';
    var kanBestand = typeof window.showOpenFilePicker === 'function';
    if (!kanMap && !kanBestand) status = 'unsupported';

    function statusTekst() {
      if (status === 'watching') return '● Live';
      if (status === 'no-file') return '● Geen logbestand';
      if (status === 'unsupported') return '● Niet ondersteund';
      if (status === 'needs-permission') return '● Toegang nodig';
      return '● Niet ingesteld';
    }
    function statusKleur() {
      if (status === 'watching') return 'var(--dl-green)';
      if (status === 'no-file' || status === 'needs-permission') return 'var(--dl-gold)';
      return 'var(--dl-red)';
    }
    function tekenStatus() {
      statusEl.textContent = statusTekst();
      statusEl.style.color = statusKleur();
      if (naamEl) naamEl.textContent = bestandsnaam || '';
      if (knop && status !== 'needs-permission') knop.textContent = dirHandle ? 'Andere map' : 'Kies Chatlogs-map';
    }

    /* Namen die we nog niet kennen in één keer aan de server vragen. */
    function haalStandings(namen) {
      var nieuw = namen.filter(function (n) { return !(n in bekend); });
      if (nieuw.length === 0 || !url) return Promise.resolve(false);
      nieuw.forEach(function (n) { bekend[n] = 'neutral'; });   // niet twee keer vragen
      return fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf },
        body: JSON.stringify({ namen: nieuw.slice(0, 200) })
      }).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (data) {
          if (!data) return false;
          Object.keys(data).forEach(function (n) { bekend[n] = data[n]; });
          return true;
        }).catch(function () { return false; });
    }

    function tekenLijst() {
      var recent = berichten.slice(-maxRijen).slice().reverse();
      var hand = overrides();
      lijst.innerHTML = '';

      if (status !== 'watching') {
        var leeg = document.createElement('div');
        leeg.className = 'lc-leeg';
        if (status === 'unsupported') {
          leeg.textContent = 'Deze browser kan geen bestanden volgen — gebruik Chrome, Edge of Opera.';
        } else if (status === 'no-file') {
          leeg.textContent = 'Geen Local_*.txt in die map gevonden.';
        } else if (status === 'needs-permission') {
          leeg.textContent = 'Geef opnieuw toegang tot je Chatlogs-map.';
        } else {
          leeg.textContent = 'Kies je EVE Chatlogs-map om local te volgen.';
        }
        lijst.appendChild(leeg);
        return;
      }
      if (recent.length === 0) {
        var w = document.createElement('div');
        w.className = 'lc-leeg';
        w.textContent = 'Wachtend op berichten…';
        lijst.appendChild(w);
        return;
      }

      recent.forEach(function (m, i) {
        var s = standingVan(m.zender, eigen, bekend[m.zender], hand);
        var genoemd = s !== 'own' && eigen.some(function (n) {
          return m.bericht.toLowerCase().indexOf(n.toLowerCase()) !== -1;
        });
        var rij = document.createElement('div');
        rij.className = 'lc-rij';
        rij.style.background = rijAchtergrond(s, genoemd, i % 2 === 0);
        if (s === 'enemy') { rij.style.borderLeft = '2px solid var(--dl-red)'; }
        else if (s === 'alliance') { rij.style.borderLeft = '2px solid #7fe0ff'; }
        else if (vriendelijk(s)) { rij.style.borderLeft = '2px solid var(--dl-green)'; }
        else if (genoemd) { rij.style.borderLeft = '2px solid var(--dl-gold)'; }

        var tijd = document.createElement('span');
        tijd.className = 'lc-tijd';
        tijd.textContent = m.tijd.slice(11);

        var naam = document.createElement('span');
        naam.className = 'lc-naam';
        naam.style.color = kleurVan(s);
        naam.textContent = (teken(s) ? teken(s) + ' ' : '') + m.zender + (hand[m.zender] ? ' ✎' : '');
        if (s !== 'own') {
          naam.title = 'Rechtermuisknop voor een handmatige override';
          naam.addEventListener('contextmenu', function (e) {
            e.preventDefault();
            e.stopPropagation();
            menu(e.clientX, e.clientY, m.zender);
          });
        }

        var tekst = document.createElement('span');
        tekst.className = 'lc-tekst';
        if (genoemd) tekst.style.color = 'var(--dl-gold)';
        tekst.textContent = m.bericht;

        rij.appendChild(tijd);
        rij.appendChild(naam);
        rij.appendChild(tekst);
        lijst.appendChild(rij);
      });
    }

    /* Rechtsklikmenu: altijd vriend / altijd vijand / weer op ESI. */
    function menu(x, y, naam) {
      var bestaand = document.querySelector('.lc-menu');
      if (bestaand) bestaand.remove();
      var el = document.createElement('div');
      el.className = 'lc-menu';
      el.style.left = x + 'px';
      el.style.top = y + 'px';
      var kop = document.createElement('div');
      kop.className = 'lc-menu-kop';
      kop.textContent = naam + ' — override';
      el.appendChild(kop);
      var keuzes = [['friend', '▲ Altijd vriend'], ['enemy', '▼ Altijd vijand'], [null, '— ESI standing gebruiken']];
      keuzes.forEach(function (paar) {
        var item = document.createElement('div');
        item.className = 'lc-menu-item';
        item.textContent = paar[1];
        item.addEventListener('click', function () {
          zetOverride(naam, paar[0]);
          el.remove();
          tekenLijst();
        });
        el.appendChild(item);
      });
      document.body.appendChild(el);
      setTimeout(function () {
        document.addEventListener('mousedown', function sluit() {
          el.remove();
          document.removeEventListener('mousedown', sluit);
        });
      }, 0);
    }

    /* Het nieuwste Local_*.txt in de map opzoeken. EVE begint bij elke sessie
       een nieuw bestand, dus alleen de eerste keer zoeken is niet genoeg. */
    function zoekBestand() {
      if (!dirHandle) return Promise.resolve(null);
      return (async function () {
        var beste = null;
        for await (var entry of dirHandle.values()) {
          if (entry.kind !== 'file' || entry.name.indexOf('Local_') !== 0) continue;
          if (!beste || entry.name > beste.name) beste = entry;
        }
        return beste;
      })();
    }

    function lees() {
      if (bezig) return;
      bezig = true;
      var stap = Promise.resolve(null);
      if (dirHandle && (!fileHandle || teller % RESCAN_ELKE === 0)) stap = zoekBestand();
      teller++;
      stap.then(function (handle) {
        if (handle) { fileHandle = handle; bestandsnaam = handle.name; }
        if (!fileHandle) {
          if (dirHandle) status = 'no-file';
          tekenStatus();
          tekenLijst();
          return null;
        }
        return fileHandle.getFile().then(function (f) { return f.arrayBuffer(); }).then(function (buf) {
          berichten = parse(decodeer(buf));
          status = 'watching';
          var namen = {};
          berichten.slice(-maxRijen).forEach(function (m) { namen[m.zender] = 1; });
          return haalStandings(Object.keys(namen));
        }).then(function () {
          tekenStatus();
          tekenLijst();
        });
      }).catch(function (err) {
        if (err && err.name === 'NotAllowedError') status = 'needs-permission';
        tekenStatus();
        tekenLijst();
      }).then(function () { bezig = false; });
    }

    function volgMap(handle) {
      dirHandle = handle;
      fileHandle = null;
      teller = 0;
      idbZet(handle);
      lees();
    }

    if (knop) {
      knop.addEventListener('click', function () {
        // Toestemming vragen mag alleen na een klik; daarom loopt het herstel
        // van een eerder gekozen map ook via deze knop.
        if (dirHandle && status === 'needs-permission') {
          dirHandle.requestPermission({ mode: 'read' }).then(function (p) {
            if (p === 'granted') volgMap(dirHandle);
          }).catch(function () {});
          return;
        }
        if (!kanMap) { window.alert('Deze browser kan geen map volgen; kies een los bestand.'); return; }
        window.showDirectoryPicker({ id: 'eve-chatlogs', mode: 'read' })
          .then(volgMap).catch(function () {});
      });
    }
    if (bestandKnop) {
      bestandKnop.addEventListener('click', function () {
        if (!kanBestand) return;
        window.showOpenFilePicker({ multiple: false }).then(function (h) {
          dirHandle = null;
          fileHandle = h[0];
          bestandsnaam = h[0].name;
          lees();
        }).catch(function () {});
      });
    }

    idbGet().then(function (handle) {
      if (!handle) { tekenStatus(); tekenLijst(); return; }
      handle.queryPermission({ mode: 'read' }).then(function (p) {
        if (p === 'granted') {
          volgMap(handle);
        } else {
          status = 'needs-permission';
          dirHandle = handle;
          tekenStatus();
          tekenLijst();
          if (knop) knop.textContent = 'Toegang geven';
        }
      }).catch(function () { tekenStatus(); tekenLijst(); });
    });

    setInterval(lees, POLL_MS);
    tekenStatus();
    tekenLijst();
  }

  document.addEventListener('DOMContentLoaded', function () {
    Array.prototype.forEach.call(document.querySelectorAll('[data-localchat]'), start);
  });
})();
