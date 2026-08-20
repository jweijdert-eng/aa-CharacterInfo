/* Bouwproject — projecten, vinkjes en bouwen-of-kopen.
 *
 * Overgenomen van `BuildProject.tsx` van dutchlegionsdashboard.eu. De boom zelf
 * rekent de server uit (die heeft de recepten en je hangars), maar wat je er
 * zélf bij zet hoort in de browser: welke projecten je bewaart, hoe ver je bent
 * per onderdeel, en hoeveel je al gekocht hebt. Dat is jouw kladblok, geen
 * gegeven dat de server hoeft te weten.
 *
 * Eén ding gaat wél via de URL: onderdelen die je liever koopt. Die veranderen
 * de bóóm — de server moet die tak dan niet verder uitrekenen maar op de
 * inkooplijst zetten — en zo blijft een project ook deelbaar met een link.
 */
(function () {
  'use strict';

  var PROJECT_SLEUTEL = 'mijndashboard-bouwprojecten';
  var STAPPEN = ['todo', 'running', 'done'];
  var STAP_TEKST = { todo: 'Te doen', running: 'Job draait', done: 'Klaar' };
  var STAP_KLEUR = { todo: 'var(--fin-dim)', running: 'var(--fin-gold)', done: 'var(--fin-green)' };

  function lees() {
    try { return JSON.parse(localStorage.getItem(PROJECT_SLEUTEL) || '{}'); } catch (e) { return {}; }
  }
  function schrijf(alles) {
    try { localStorage.setItem(PROJECT_SLEUTEL, JSON.stringify(alles)); } catch (e) {}
  }

  document.addEventListener('DOMContentLoaded', function () {
    var paneel = document.querySelector('[data-bouwproject]');
    if (!paneel) return;

    var doelId = paneel.dataset.doel || '';
    var doelNaam = paneel.dataset.doelnaam || '';
    var aantal = paneel.dataset.aantal || '1';
    var me = paneel.dataset.me || '10';
    var koop = (paneel.dataset.koop || '').split(',').filter(Boolean);
    var plek = paneel.dataset.plek || '';
    var basisUrl = paneel.dataset.basis;

    // De sleutel van dit project: doel + aantal + ME. Verander je het aantal,
    // dan is het een ander project — anders staan er vinkjes van een run die
    // je niet meer doet.
    var sleutel = doelId + ':' + aantal + ':' + me;
    var alles = lees();
    var project = alles[sleutel] || { naam: '', voortgang: {}, gekocht: {} };

    function bewaar() {
      if (!doelId) return;
      project.doel = doelId;
      project.doelnaam = doelNaam;
      project.aantal = aantal;
      project.me = me;
      project.koop = koop.join(',');
      project.plek = plek;
      // Hoeveel onderdelen de boom telt, zodat de balk op de kaart klopt.
      project.knopen = paneel.querySelectorAll('[data-knoop][data-maken="1"]').length || project.knopen || 0;
      project.bijgewerkt = Date.now();
      alles[sleutel] = project;
      schrijf(alles);
      tekenProjectbalk();
    }

    function urlVoor(nieuweKoop) {
      var q = '?type=' + doelId + '&aantal=' + aantal + '&me=' + me;
      if (nieuweKoop && nieuweKoop.length) q += '&koop=' + nieuweKoop.join(',');
      if (plek) q += '&plek=' + plek;
      return basisUrl + q;
    }

    /* ── Balk met bewaarde projecten ────────────────────────────────────── */
    function tekenProjectbalk() {
      // De balk staat boven het zoekformulier en dus buiten het paneel.
      var balk = document.querySelector('[data-rol="projecten"]');
      if (!balk) return;
      balk.innerHTML = '';
      var namen = Object.keys(alles).sort(function (a, b) {
        return (alles[b].bijgewerkt || 0) - (alles[a].bijgewerkt || 0);
      });
      if (!namen.length) {
        var leeg = document.createElement('span');
        leeg.className = 'fin-dim';
        leeg.style.fontSize = '.74rem';
        leeg.textContent = 'Nog geen bewaarde projecten — open er een en klik Bewaren.';
        balk.appendChild(leeg);
        return;
      }
      namen.forEach(function (k) {
        var p = alles[k];
        var chip = document.createElement('span');
        chip.className = 'fin-bouwproject' + (k === sleutel ? ' is-actief' : '');

        var af = Object.keys(p.voortgang || {}).filter(function (t) {
          return p.voortgang[t] === 'done';
        }).length;
        var totaal = p.knopen || 0;
        var pct = totaal ? Math.round(af / totaal * 100) : 0;

        var link = document.createElement('a');
        link.href = basisUrl + '?type=' + p.doel + '&aantal=' + p.aantal + '&me=' + p.me +
                    (p.koop ? '&koop=' + p.koop : '') + (p.plek ? '&plek=' + p.plek : '');

        // Het plaatje van wat je bouwt: sneller te herkennen dan de naam, zeker
        // met een paar projecten naast elkaar.
        var icoon = document.createElement('img');
        icoon.className = 'fin-bouwproject-icoon';
        icoon.loading = 'lazy';
        icoon.alt = '';
        icoon.src = 'https://images.evetech.net/types/' + p.doel + '/icon?size=64';
        link.appendChild(icoon);

        var tekst = document.createElement('span');
        tekst.className = 'fin-bouwproject-tekst';
        var titel = document.createElement('span');
        titel.className = 'fin-bouwproject-naam';
        titel.textContent = (p.naam || p.doelnaam || 'project') + ' ×' + p.aantal;
        tekst.appendChild(titel);
        var sub = document.createElement('span');
        sub.className = 'fin-bouwproject-sub';
        // Klaar tegenover totaal zegt meer dan alleen "3 klaar".
        sub.textContent = totaal ? af + ' van ' + totaal + ' klaar' : 'nog niets afgevinkt';
        tekst.appendChild(sub);
        var voortgangsbalk = document.createElement('span');
        voortgangsbalk.className = 'fin-bouwproject-balk';
        var vulling = document.createElement('i');
        vulling.style.width = pct + '%';
        if (pct === 100) vulling.classList.add('is-af');
        voortgangsbalk.appendChild(vulling);
        tekst.appendChild(voortgangsbalk);
        link.appendChild(tekst);
        chip.appendChild(link);

        var weg = document.createElement('button');
        weg.type = 'button';
        weg.className = 'fin-bouwproject-weg';
        weg.title = 'Project verwijderen';
        weg.textContent = '×';
        weg.addEventListener('click', function (e) {
          e.preventDefault();
          delete alles[k];
          schrijf(alles);
          tekenProjectbalk();
        });
        chip.appendChild(weg);
        balk.appendChild(chip);
      });
    }

    /* ── Per rij: status, gekocht en bouwen/kopen ───────────────────────── */
    function tekenRijen() {
      Array.prototype.forEach.call(paneel.querySelectorAll('[data-knoop]'), function (rij) {
        var tid = rij.dataset.knoop;
        var maken = rij.dataset.maken === '1';
        var vak = rij.querySelector('[data-rol="voortgang"]');
        if (!vak) return;
        vak.innerHTML = '';

        takKnop(rij);

        // Wisselen tussen zelf bouwen en kopen: dat verandert de boom, dus dat
        // gaat via de URL en niet via de opslag.
        var wissel = rij.querySelector('[data-rol="wissel"]');
        if (wissel) {
          var staatOpKopen = koop.indexOf(tid) !== -1;
          wissel.textContent = staatOpKopen ? '→ bouwen' : '→ kopen';
          wissel.href = urlVoor(staatOpKopen
            ? koop.filter(function (x) { return x !== tid; })
            : koop.concat([tid]));
        }

        if (maken) {
          var stap = project.voortgang[tid] || 'todo';
          var knop = document.createElement('button');
          knop.type = 'button';
          knop.className = 'fin-stapknop';
          knop.title = 'Klik om door te schakelen: te doen → job draait → klaar';
          knop.textContent = STAP_TEKST[stap];
          knop.style.color = STAP_KLEUR[stap];
          knop.style.borderColor = STAP_KLEUR[stap];
          knop.addEventListener('click', function () {
            var nu = STAPPEN.indexOf(project.voortgang[tid] || 'todo');
            project.voortgang[tid] = STAPPEN[(nu + 1) % STAPPEN.length];
            bewaar();
            tekenRijen();
          });
          vak.appendChild(knop);
          rij.classList.toggle('is-klaar', stap === 'done');
        } else {
          var nodig = parseInt(rij.dataset.tekort || '0', 10);
          if (!nodig) {           // al gedekt uit voorraad: niets te kopen
            var ok = document.createElement('span');
            ok.className = 'fin-dim';
            ok.textContent = '—';
            vak.appendChild(ok);
            return;
          }
          var veld = document.createElement('input');
          veld.type = 'number';
          veld.min = '0';
          veld.className = 'fin-gekocht';
          veld.placeholder = '0';
          veld.title = 'Hoeveel je hiervan al gekocht hebt';
          veld.value = project.gekocht[tid] || '';
          veld.addEventListener('change', function () {
            var n = Math.max(0, parseInt(veld.value, 10) || 0);
            if (n) { project.gekocht[tid] = n; } else { delete project.gekocht[tid]; }
            bewaar();
            tekenRijen();
          });
          vak.appendChild(veld);
          var heb = project.gekocht[tid] || 0;
          rij.classList.toggle('is-klaar', heb >= nodig && nodig > 0);
          if (heb && heb < nodig) {
            var rest = document.createElement('span');
            rest.className = 'fin-dim fin-nog';
            rest.textContent = 'nog ' + (nodig - heb);
            vak.appendChild(rest);
          }
        }

      });
    }

    /* ── Eén tak in- of uitklappen ─────────────────────────────────────── */
    /* De boom staat plat in de tabel met een diepte per regel; een tak sluiten
       is dus: alle volgende regels verbergen tot er weer een even ondiepe komt. */
    function takKnop(rij) {
      var knop = rij.querySelector('[data-rol="tak"]');
      if (!knop) return;
      knop.addEventListener('click', function () {
        var diepte = parseInt(rij.dataset.diepte || '0', 10);
        var dicht = knop.textContent.trim() === '▾';
        knop.textContent = dicht ? '▸' : '▾';
        var volgende = rij.nextElementSibling;
        while (volgende && parseInt(volgende.dataset.diepte || '0', 10) > diepte) {
          volgende.style.display = dicht ? 'none' : '';
          // Een tak die zelf dicht stond blijft dicht als de ouder weer opengaat.
          var eigen = volgende.querySelector('[data-rol="tak"]');
          if (!dicht && eigen && eigen.textContent.trim() === '▸') {
            var binnen = parseInt(volgende.dataset.diepte || '0', 10);
            volgende = volgende.nextElementSibling;
            while (volgende && parseInt(volgende.dataset.diepte || '0', 10) > binnen) {
              volgende = volgende.nextElementSibling;
            }
            continue;
          }
          volgende = volgende.nextElementSibling;
        }
      });
    }

    /* ── In- en uitklappen ─────────────────────────────────────────────── */
    var ingeklapt = false;
    var klapKnop = paneel.querySelector('[data-rol="klap"]');
    if (klapKnop) {
      klapKnop.addEventListener('click', function () {
        ingeklapt = !ingeklapt;
        klapKnop.textContent = ingeklapt ? '⊞ Alles uitklappen' : '⊟ Alles inklappen';
        Array.prototype.forEach.call(paneel.querySelectorAll('[data-knoop]'), function (rij) {
          // Alleen de eerste laag blijft staan; de rest vouwt weg.
          if (parseInt(rij.dataset.diepte || '0', 10) > 1) {
            rij.style.display = ingeklapt ? 'none' : '';
          }
          var tak = rij.querySelector('[data-rol="tak"]');
          if (tak && parseInt(rij.dataset.diepte || '0', 10) >= 1) {
            tak.textContent = ingeklapt ? '▸' : '▾';
          }
        });
      });
    }

    var bewaarKnop = paneel.querySelector('[data-rol="bewaren"]');
    if (bewaarKnop) {
      bewaarKnop.addEventListener('click', function () {
        var naam = window.prompt('Naam van dit project:', project.naam || doelNaam);
        if (naam === null) return;
        project.naam = naam.trim();
        bewaar();
      });
    }

    tekenProjectbalk();
    tekenRijen();
  });
})();
