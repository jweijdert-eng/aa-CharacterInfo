"""EVE-mailopmaak omzetten naar iets wat een browser veilig mag tonen.

Een mailbody uit ESI is geen HTML zoals het web die kent: het is de opmaak van
de EVE-client. `<font size="12" color="#ffFFD700">`, `<br>` in plaats van
alinea's, `<loc>`-omhulsels, tags die nooit gesloten worden, en links met een
eigen protocol (`showinfo:`, `fitting:`, `killReport:`, `contract:`).

Zo'n body rechtstreeks in de pagina zetten mag sowieso niet — dan bepaalt een
willekeurige afzender wat er in jouw dashboard staat. Daarom lezen we het hier
zelf uit en bouwen we er nieuwe HTML van waarin alleen voorkomt wat wij er zelf
in zetten. Alles wat we niet kennen wordt platte, ge-escapete tekst.

Twee dingen die hier meer doen dan omzetten:

* **Kleuren worden opgehelderd waar dat moet.** EVE-mail wordt geschreven tegen
  de achtergrond van de client en gebruikt graag donkergrijs voor scheidings-
  lijnen (`#ff5A5A5A` haalt hier 2,6:1). Op deze kaart is dat niet te lezen, dus
  elke kleur wordt tegen de kaartachtergrond nagerekend en zo nodig naar wit toe
  gemengd tot 4,5:1. De tint blijft, de leesbaarheid komt erbij.
* **Fitting-links worden een echte fit.** Zo'n link draagt de type-ids van het
  schip en alle modules mee; met eveuniverse erbij (zie `data.py`) levert dat
  namen, slots en een EFT-blok dat je zo in het spel kunt plakken.
"""

import html
import re
from html.parser import HTMLParser

from django.utils.safestring import mark_safe

# De kaart waar de mail op staat (surface-2 uit de huisstijl). Kleuren uit de
# mail worden hiertegen afgewogen — en tegen deze kleur mengen we de alpha weg
# die EVE in z'n kleurwaarden meestuurt (#bfffffff = 75% wit).
ACHTERGROND = (0x13, 0x13, 0x2B)
MIN_CONTRAST = 4.5              # lopende tekst; WCAG AA

# In-game links dragen het type-id van waar ze heen wijzen. Die paar ids die
# een entiteit aanduiden herkennen we, de rest is gewoon een item uit de
# database (een schip, een module, een erts).
SHOWINFO_SOORT = {2: "corp", 16159: "alliance", 5: "plek", 3: "plek",
                  4: "plek", 15: "plek"}
CHARACTER_TYPES = set(range(1373, 1387))    # de veertien bloodline-types

SOORT_TITEL = {"char": "character", "corp": "corporation", "alliance": "alliance",
               "plek": "locatie", "item": "item", "kill": "killmail",
               "contract": "contract"}

# EFT zet de slots in deze volgorde onder elkaar, met een lege regel ertussen.
EFT_VOLGORDE = ["laag", "midden", "hoog", "rig", "subsysteem", "drone", "lading"]
SLOT_NAAM = {"laag": "Laag", "midden": "Midden", "hoog": "Hoog", "rig": "Rigs",
             "subsysteem": "Subsystemen", "drone": "Drones", "lading": "Lading"}


# --------------------------------------------------------------------------
# Kleur
# --------------------------------------------------------------------------

def _lineair(kanaal):
    c = kanaal / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _lichtheid(rgb):
    r, g, b = (_lineair(k) for k in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


_ACHTERGROND_L = _lichtheid(ACHTERGROND)


def _contrast(rgb):
    licht = _lichtheid(rgb)
    hoog, laag = max(licht, _ACHTERGROND_L), min(licht, _ACHTERGROND_L)
    return (hoog + 0.05) / (laag + 0.05)


def _leesbaar(rgb):
    """Meng naar wit tot de kleur 4,5:1 haalt tegen de kaart.

    Naar wít mengen en niet gewoon vervangen: dan blijft het goud goud en het
    oranje oranje, terwijl het donkergrijs van een scheidingslijn oplicht tot
    iets wat je kunt lezen. Twintig halveringen is ruim genoeg om het punt te
    vinden waarop het net haalt — verder ophelderen dan nodig zou de opmaak van
    de afzender onnodig platslaan.
    """
    if _contrast(rgb) >= MIN_CONTRAST:
        return rgb
    laag, hoog = 0.0, 1.0
    for _ in range(20):
        mid = (laag + hoog) / 2
        kandidaat = tuple(round(k + (255 - k) * mid) for k in rgb)
        if _contrast(kandidaat) >= MIN_CONTRAST:
            hoog = mid
        else:
            laag = mid
    return tuple(round(k + (255 - k) * hoog) for k in rgb)


def _kleur(waarde):
    """EVE-kleur (#aarrggbb of #rrggbb) naar een leesbare CSS-kleur, of ''."""
    if not waarde:
        return ""
    m = re.fullmatch(r"#?([0-9a-fA-F]{6}|[0-9a-fA-F]{8})", waarde.strip())
    if not m:
        return ""
    hexwaarde = m.group(1)
    alpha = 1.0
    if len(hexwaarde) == 8:
        alpha = int(hexwaarde[0:2], 16) / 255
        hexwaarde = hexwaarde[2:]
    rgb = tuple(int(hexwaarde[i:i + 2], 16) for i in (0, 2, 4))
    # Doorzichtigheid meteen wegrekenen tegen de achtergrond: dan hoeft de CSS
    # het niet nog eens te doen en klopt de contrastberekening hieronder.
    rgb = tuple(round(k * alpha + a * (1 - alpha)) for k, a in zip(rgb, ACHTERGROND))
    return "#%02x%02x%02x" % _leesbaar(rgb)


def _grootte(waarde):
    """EVE-lettergrootte naar rem, met een dak erop.

    In een mail staat van alles tussen 10 en 24. Zonder begrenzing zou één
    afzender met size=24 z'n kop dwars door de pagina-opmaak heen zetten.
    """
    try:
        n = int(waarde)
    except (TypeError, ValueError):
        return ""
    return "%.2frem" % min(max(0.055 * n + 0.18, 0.78), 1.25)


# --------------------------------------------------------------------------
# Fitting-links
# --------------------------------------------------------------------------

def fit_type_ids(body):
    """Alle type-ids uit de fitting-links van deze body.

    Apart van het omzetten, zodat de aanroeper eerst álle mails kan aftasten en
    daarna in één database-vraag de namen ophaalt.
    """
    ids = set()
    for ruw in re.findall(r'(?:href|url)\s*=\s*"?(fitting:[^"\'>\s]+)', body or ""):
        schip, modules = _lees_fit(ruw)
        if schip:
            ids.add(schip)
        ids.update(t for t, _ in modules)
    return ids


def _lees_fit(href):
    """`fitting:11174:2048;1:31304;1:…::` → (schip_id, [(type_id, aantal), …])."""
    rest = href[len("fitting:"):]
    delen = [d for d in rest.split(":") if d]
    if not delen:
        return None, []
    try:
        schip = int(delen[0])
    except ValueError:
        return None, []
    modules = []
    for deel in delen[1:]:
        tid, _, aantal = deel.partition(";")
        try:
            modules.append((int(tid), int(aantal or 1)))
        except ValueError:
            continue
    return schip, modules


def _eft(schipnaam, fitnaam, modules):
    """De fit als EFT-blok, klaar om in het spel te plakken.

    De volgorde komt niet uit de link — die geeft alleen ids — maar uit de
    dogma-effects in eveuniverse, want dáár staat in welk slot een module past.
    Modules met een slot krijgen een regel per stuk (zo verwacht EFT het),
    drones en lading houden hun aantal achter de naam.
    """
    per_slot = {}
    for m in modules:
        per_slot.setdefault(m["slot"], []).append(m)

    blokken = []
    for slot in EFT_VOLGORDE:
        rijen = per_slot.get(slot)
        if not rijen:
            continue
        regels = []
        for m in rijen:
            if slot in ("drone", "lading"):
                regels.append(f"{m['naam']} x{m['aantal']}" if m["aantal"] > 1 else m["naam"])
            else:
                regels.extend([m["naam"]] * max(m["aantal"], 1))
        blokken.append("\n".join(regels))
    # Geen lege regel na de kop: het spel zet de eerste module er direct onder,
    # en zo plakt het blok terug zoals EVE het zelf uitspuugt.
    kop = f"[{schipnaam}, {fitnaam or schipnaam}]"
    return kop + "\n" + "\n\n".join(blokken) if blokken else kop


# --------------------------------------------------------------------------
# De omzetting zelf
# --------------------------------------------------------------------------

class _Lezer(HTMLParser):
    """Leest EVE-opmaak en schrijft er veilige HTML van.

    Tags worden in de client zelden netjes gesloten, dus we houden zelf bij wat
    er open staat en sluiten aan het eind af. Alles wat we niet kennen laten we
    vallen — de tekst erbinnen blijft gewoon staan.
    """

    def __init__(self, typen):
        super().__init__(convert_charrefs=True)
        self.typen = typen or {}
        self.fits = []
        self._uit = []
        self._plat = []
        self._stapel = []           # (tag, afsluiter)
        self._href = None           # de <a> waar we nu in zitten
        self._linktekst = []

    # -- tekst ------------------------------------------------------------
    def handle_data(self, data):
        if not data:
            return
        if self._href is not None:
            self._linktekst.append(data)
            return
        self._uit.append(html.escape(data, quote=False))
        self._plat.append(data)

    # -- tags -------------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        a = {k.lower(): (v or "") for k, v in attrs}

        if tag == "br":
            self._uit.append("<br>")
            self._plat.append("\n")
            return
        if tag == "a":
            self._href = a.get("href") or a.get("url") or ""
            self._linktekst = []
            return
        if self._href is not None:
            return                  # opmaak binnen een link doet er niet toe
        if tag == "font":
            stijl = []
            kleur = _kleur(a.get("color"))
            if kleur:
                stijl.append(f"color:{kleur}")
            grootte = _grootte(a.get("size"))
            if grootte:
                stijl.append(f"font-size:{grootte}")
            if stijl:
                self._uit.append('<span style="%s">' % html.escape(";".join(stijl)))
                self._stapel.append((tag, "</span>"))
            else:
                self._stapel.append((tag, ""))
            return
        if tag in ("b", "i", "u"):
            self._uit.append(f"<{tag}>")
            self._stapel.append((tag, f"</{tag}>"))
            return
        # <loc> en al het onbekende: geen opmaak, wel de inhoud
        self._stapel.append((tag, ""))

    def handle_startendtag(self, tag, attrs):
        if tag.lower() == "br":
            self._uit.append("<br>")
            self._plat.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "a":
            if self._href is not None:
                tekst = "".join(self._linktekst)
                self._uit.append(self._link_html(self._href, tekst))
                self._plat.append(tekst)
                self._href = None
                self._linktekst = []
            return
        if self._href is not None or tag == "br":
            return
        # Van achter naar voren de bijbehorende opening zoeken. Sluit een mail
        # een tag die nooit geopend werd, dan negeren we dat gewoon.
        for i in range(len(self._stapel) - 1, -1, -1):
            if self._stapel[i][0] == tag:
                for _, afsluiter in reversed(self._stapel[i:]):
                    if afsluiter:
                        self._uit.append(afsluiter)
                del self._stapel[i:]
                return

    # -- links ------------------------------------------------------------
    def _link_html(self, href, tekst):
        veilig = html.escape(tekst, quote=False) or "link"
        laag = (href or "").lower()

        if laag.startswith("fitting:"):
            return self._fit_html(href, tekst)

        if laag.startswith("http://") or laag.startswith("https://"):
            # De enige link die een browser echt kan volgen. `nofollow` en
            # `noopener` omdat de afzender bepaalt waar hij heen wijst.
            return ('<a class="fin-mail-url" target="_blank" rel="noopener noreferrer nofollow" '
                    'href="%s">%s ↗</a>' % (html.escape(href, quote=True), veilig))

        if laag.startswith("showinfo:"):
            m = re.match(r"showinfo:(\d+)", href)
            type_id = int(m.group(1)) if m else 0
            if type_id in CHARACTER_TYPES:
                soort = "char"
            else:
                soort = SHOWINFO_SOORT.get(type_id, "item")
        elif laag.startswith("killreport:"):
            soort = "kill"
        elif laag.startswith("contract:"):
            soort = "contract"
        else:
            return veilig           # onbekend protocol: alleen de tekst

        return ('<span class="fin-mail-link is-%s" title="%s">%s</span>'
                % (soort, SOORT_TITEL.get(soort, soort), veilig))

    def _fit_html(self, href, tekst):
        """Een fitting-link: chip in de tekst, de uitwerking eronder op de kaart."""
        schip_id, modules = _lees_fit(href)
        if not schip_id:
            return html.escape(tekst, quote=False)

        schip = self.typen.get(schip_id, {})
        schipnaam = schip.get("naam") or f"Type {schip_id}"
        rijen = []
        for tid, aantal in modules:
            info = self.typen.get(tid, {})
            rijen.append({
                "type_id": tid,
                "naam": info.get("naam") or f"Type {tid}",
                "groep": info.get("groep") or "",
                "slot": info.get("slot") or "lading",
                "aantal": aantal,
            })

        # Per slot gegroepeerd voor de weergave; de EFT-uitdraai hieronder
        # gebruikt dezelfde volgorde, zodat scherm en plakblok gelijk lopen.
        per_slot = {}
        for r in rijen:
            per_slot.setdefault(r["slot"], []).append(r)
        groepen = [{"slot": s, "naam": SLOT_NAAM[s], "modules": per_slot[s],
                    "aantal": sum(x["aantal"] for x in per_slot[s])}
                   for s in EFT_VOLGORDE if s in per_slot]

        naam = tekst.strip() or schipnaam
        self.fits.append({
            "slots": groepen,
            "nr": len(self.fits) + 1,
            "naam": naam,
            "schip_id": schip_id,
            "schip": schipnaam,
            "modules": rijen,
            # Modules apart van wat er in de bays ligt: een fit met 500 raketten
            # en 600 boosters zou anders "2111 modules" heten.
            "aantal_modules": sum(r["aantal"] for r in rijen
                                  if r["slot"] not in ("drone", "lading")),
            "aantal_lading": sum(r["aantal"] for r in rijen
                                 if r["slot"] in ("drone", "lading")),
            "eft": _eft(schipnaam, naam, rijen),
        })
        return ('<span class="fin-mail-fit-chip" title="%s">'
                '<img loading="lazy" alt="" src="https://images.evetech.net/types/%s/icon?size=32">'
                '%s <i>%s</i></span>'
                % (html.escape(schipnaam, quote=True), schip_id,
                   html.escape(naam, quote=False), html.escape(schipnaam, quote=False)))

    # -- afsluiten --------------------------------------------------------
    def resultaat(self):
        if self._href is not None:      # een <a> die nooit gesloten werd
            tekst = "".join(self._linktekst)
            self._uit.append(self._link_html(self._href, tekst))
            self._plat.append(tekst)
            self._href = None
        for _, afsluiter in reversed(self._stapel):
            if afsluiter:
                self._uit.append(afsluiter)
        self._stapel = []
        return "".join(self._uit), "".join(self._plat)


def render(body, typen=None):
    """Body uit ESI → {html, tekst, fits}.

    `typen` is {type_id: {"naam", "groep", "slot"}} en komt uit eveuniverse;
    zonder die kaart blijven fitting-links staan maar heten de modules "Type
    1234".
    """
    lezer = _Lezer(typen)
    try:
        lezer.feed(body or "")
        lezer.close()
    except Exception:                   # noqa: BLE001 — kapotte opmaak, geen 500
        pass
    opmaak, plat = lezer.resultaat()
    # Meerdere lege regels achter elkaar zeggen niets extra's en maken van een
    # nette mail een lange rol wit.
    opmaak = re.sub(r"(?:<br>\s*){3,}", "<br><br>", opmaak)
    plat = re.sub(r"\n{3,}", "\n\n", plat).strip()
    return {"html": mark_safe(opmaak), "tekst": plat, "fits": lezer.fits}
