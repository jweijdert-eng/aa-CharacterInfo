"""Rekenwerk voor de drie pagina's — Finance.

De ratting-logica is een port van `Ratting.tsx` uit het dashboard: dezelfde twee
ref_types, dezelfde totalen en dezelfde groepering per dag, zodat beide plekken
hetzelfde getal laten zien.
"""

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from . import esi

# EVE boekt ratting-inkomsten in twee soorten journaalregels weg. Nagekeken in
# echte data: bounty_prizes is de kopgeldbetaling, ess_escrow_transfer is de
# uitbetaling uit de Encounter Surveillance System in nullsec. Beide dragen het
# systeem mee in context_id (context_id_type = "system_id").
REF_BOUNTY = "bounty_prizes"
REF_ESS = "ess_escrow_transfer"

FINISHED = ("finished", "finished_contractor", "finished_issuer")


def fmt_isk(waarde):
    """1234567890 → '1,23 mld'. Kort genoeg voor een tabelcel."""
    try:
        waarde = float(waarde or 0)
    except (TypeError, ValueError):
        return "0"
    for grens, achtervoegsel in ((1e12, "bln"), (1e9, "mld"), (1e6, "mln"), (1e3, "k")):
        if abs(waarde) >= grens:
            heel = f"{waarde / grens:,.2f}"          # 1,234.56 → 1.234,56
            return f"{_nl(heel)} {achtervoegsel}"
    return _nl(f"{waarde:,.0f}")


def fmt_isk_vol(waarde):
    """1234567890 → '1.234.567.890'.

    Voor de wallet: daar wil je zien wat er écht staat, niet een afronding.
    Afgerond op hele ISK — de centen zijn nooit waar het om gaat.
    """
    try:
        waarde = float(waarde or 0)
    except (TypeError, ValueError):
        return "0"
    return _nl(f"{waarde:,.0f}")


def _nl(tekst):
    """Amerikaanse opmaak naar Nederlandse: 1,234.56 → 1.234,56."""
    return tekst.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


# Acht kleuren om characters uit elkaar te houden, nagerekend tegen de donkere
# achtergrond (#0f0f22): lichtheid binnen de band, chroma boven de vloer, en
# onderling nog ΔE 8,4 bij protanopie. De naam en het portret dragen de
# identiteit; de kleur is de snelle herkenning erbij.
CHAR_KLEUREN = ["#3987e5", "#d95926", "#199e70", "#c98500",
                "#d55181", "#008300", "#9085e9", "#e66767"]


def _kleur_per_character(chars):
    """Vaste kleur per character.

    Toegekend op volgorde van character_id, niet op saldo: anders verspringen
    alle kleuren zodra iemand ISK verplaatst, en dan zegt de kleur niets meer
    over wie het is.
    """
    op_id = sorted(chars, key=lambda c: c["character_id"])
    return {c["character_id"]: CHAR_KLEUREN[i % len(CHAR_KLEUREN)]
            for i, c in enumerate(op_id)}


def _nette_schaal(hoogste, stappen=4):
    """Rond de as af op een prettig getal.

    Zonder dit staat er '587,78 k' op de as, want dan is het gewoon een kwart
    van de hoogste balk. Een as hoort ronde getallen te tonen, dus we kiezen een
    stapgrootte uit 1 / 2 / 2,5 / 5 × een macht van tien en rekenen het maximum
    daar naartoe omhoog.
    """
    if hoogste <= 0:
        return 1.0, []
    import math

    ruw = hoogste / stappen
    macht = 10 ** math.floor(math.log10(ruw))
    for kandidaat in (1, 2, 2.5, 5, 10):
        stap = kandidaat * macht
        if stap >= ruw:
            break
    top = math.ceil(hoogste / stap) * stap
    punten = []
    n = int(round(top / stap))
    for i in range(n, 0, -1):
        waarde = stap * i
        punten.append({"pct": round(waarde / top * 100), "label": fmt_isk(waarde)})
    return top, punten


def _parse(waarde):
    if not waarde:
        return None
    try:
        return datetime.fromisoformat(str(waarde).replace("Z", "+00:00"))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Wallet
# --------------------------------------------------------------------------

def wallet(user):
    """Saldo per character + de laatste journaalregels van het hele account."""
    chars = esi.characters(user)
    per_char, totaal = [], 0.0
    for c in chars:
        saldo = esi.balance(c.character_id)
        if saldo is not None:
            totaal += float(saldo)
        per_char.append({
            "character_id": c.character_id,
            "naam": c.character_name,
            "saldo": saldo,
            "saldo_fmt": fmt_isk_vol(saldo) if saldo is not None else "—",
            "gekoppeld": saldo is not None,
        })

    # Rijkste eerst. Geen aandeelbalk of percentage meer: naast een volledig
    # saldo voegde dat niets toe en maakte het de tegel alleen maar druk.
    kleuren = _kleur_per_character(per_char)
    per_char.sort(key=lambda c: -(c["saldo"] or 0))
    for c in per_char:
        c["kleur"] = kleuren[c["character_id"]]

    regels = []
    for c in chars:
        for e in esi.journal(c.character_id):
            regels.append({**e, "_char": c.character_name,
                           "_kleur": kleuren[c.character_id]})
    regels.sort(key=lambda e: e.get("date") or "", reverse=True)

    # Inkomsten en uitgaven van de laatste 30 dagen, per soort opgeteld. Zo zie
    # je waar je geld vandaan komt zonder door duizend regels te scrollen.
    grens = datetime.now(timezone.utc) - timedelta(days=30)
    per_soort = defaultdict(float)
    binnen, eruit = 0.0, 0.0
    per_dag = defaultdict(float)
    for e in regels:
        d = _parse(e.get("date"))
        if not d or d < grens:
            continue
        bedrag = float(e.get("amount") or 0)
        per_soort[e.get("ref_type") or "onbekend"] += bedrag
        per_dag[d.date().isoformat()] += bedrag
        if bedrag >= 0:
            binnen += bedrag
        else:
            eruit += -bedrag

    def _posten(items):
        """Een lijstje ref_types met een balk op de grootste post.

        Posten die per saldo op nul uitkwamen (een borg die heen en weer ging)
        laten we weg: die vullen de lijst met regels waar niets gebeurde.
        """
        rijen = sorted((kv for kv in items if kv[1]),
                       key=lambda kv: -abs(kv[1]))[:10]
        grootste = max((abs(v) for _, v in rijen), default=0) or 1
        return [{"ref_type": k, "naam": k.replace("_", " ").capitalize(),
                 "bedrag": v, "bedrag_fmt": fmt_isk_vol(v), "positief": v >= 0,
                 "pct": round(abs(v) / grootste * 100)}
                for k, v in rijen]

    # Gesplitst in twee lijsten. In één gemengde lijst verdringen de grote
    # uitgaven de inkomsten (of andersom) en zie je van geen van beide de
    # opbouw — terwijl dat juist de vraag is.
    inkomsten = _posten([(k, v) for k, v in per_soort.items() if v >= 0])
    uitgaven = _posten([(k, v) for k, v in per_soort.items() if v < 0])

    # ── Resultaat per dag ─────────────────────────────────────────────────
    # Niet het saldo zelf: dat staat al op de tegels en beweegt procentueel
    # nauwelijks, dus een balk vanaf nul zou een vlakke muur zijn. Wat je wil
    # zien is wat er per dag bij kwam of af ging.
    vandaag = datetime.now(timezone.utc).date()
    reeks = []
    for i in range(29, -1, -1):
        dag = (vandaag - timedelta(days=i)).isoformat()
        reeks.append({"dag": dag, "bedrag": per_dag.get(dag, 0.0)})
    op = max((v["bedrag"] for v in reeks), default=0.0)
    af = -min((v["bedrag"] for v in reeks), default=0.0)
    bereik = (op + af) or 1.0
    nul_pct = round(af / bereik * 100)
    for i, v in enumerate(reeks):
        v["positief"] = v["bedrag"] >= 0
        v["pct"] = round(abs(v["bedrag"]) / bereik * 100)
        v["leeg"] = v["bedrag"] == 0
        v["bedrag_fmt"] = fmt_isk(v["bedrag"])
        d = datetime.fromisoformat(v["dag"]).date()
        v["dag_kort"] = f"{d.day}/{d.month}"
        v["toon_label"] = i % 3 == 0 or i == len(reeks) - 1

    return {
        "characters": per_char,
        "totaal": totaal,
        "totaal_fmt": fmt_isk_vol(totaal),
        "regels": [_journaalregel(e) for e in regels[:200]],
        "inkomsten": inkomsten,
        "uitgaven": uitgaven,
        "binnen_fmt": fmt_isk(binnen),
        "eruit_fmt": fmt_isk(eruit),
        "netto": binnen - eruit,
        "netto_fmt": fmt_isk(binnen - eruit),
        "netto_positief": binnen >= eruit,
        "verloop": reeks,
        "nul_pct": nul_pct,
        "nul_boven": 100 - nul_pct,
        "op_fmt": fmt_isk(op),
        "af_fmt": fmt_isk(af),
    }


PLAATJE = "https://images.evetech.net/types/%s/%s?size=32"
CAT_SCHIP = 6
CAT_BLUEPRINT = 9
CAT_SKIN = 91


def _skin_schepen(skinnamen):
    """Bij elke SKIN het schip zoeken waar hij op hoort.

    CCP heeft van SKINs geen plaatje, maar de naam begint met het schip
    ("Cormorant Navy Issue State Police SKIN"), en dát schip heeft wel een
    icoon. Langste voorvoegsel eerst, anders pakt "Cormorant Navy Issue" de
    gewone Cormorant.

    Op 400 willekeurige SKINs raakt dit er 399. De enige misser is het soort
    "Minmatar Victory SKIN": een factie-SKIN zonder eigen schip.
    """
    if not skinnamen:
        return {}
    try:
        from eveuniverse.models import EveType
    except ImportError:
        return {}

    schepen = {t.name: t.id for t in EveType.objects
               .select_related("eve_group")
               .filter(eve_group__eve_category_id=CAT_SCHIP)}
    uit = {}
    for naam in skinnamen:
        kern = naam[:-4].strip() if naam.endswith("SKIN") else naam
        woorden = kern.split()
        for n in range(len(woorden), 0, -1):
            schip = " ".join(woorden[:n])
            if schip in schepen:
                uit[naam] = (schip, schepen[schip])
                break
    return uit


def _type_info(type_ids):
    """Naam en plaatje-adres per type, uit django-eveuniverse (lokaal).

    Het plaatje-adres hangt van de categorie af, en dat kun je niet raden:
    - blueprints hebben `/bp`; `/icon` geeft daar een **400**
    - van SKINs bestaat bij CCP geen enkel plaatje: /icon, /bp en /render geven
      alle drie 404, en de plaatjes-server kent zo'n type niet eens (bij een
      gewoon item geeft /types/{id}/ netjes ["render","icon"] terug). Dat geldt
      voor alle ~11.800 SKINs. We tonen daarom het schip waar de SKIN op hoort;
      lukt dat niet, dan een penseeltje.
    """
    try:
        from eveuniverse.models import EveType
    except ImportError:
        return {}

    typen = list(EveType.objects.select_related("eve_group")
                 .filter(id__in=list(type_ids)))

    def _categorie(t):
        try:
            return t.eve_group.eve_category_id
        except Exception:  # noqa: BLE001 — groep niet geladen
            return 0

    schepen = _skin_schepen([t.name for t in typen if _categorie(t) == CAT_SKIN])

    uit = {}
    for t in typen:
        categorie = _categorie(t)
        skin = categorie == CAT_SKIN
        schip, schip_id = schepen.get(t.name, (None, None))
        if skin:
            plaatje = PLAATJE % (schip_id, "icon") if schip_id else ""
        else:
            plaatje = PLAATJE % (t.id, "bp" if categorie == CAT_BLUEPRINT else "icon")
        uit[t.id] = {"naam": t.name, "plaatje": plaatje, "skin": skin,
                     "schip": schip or ""}
    return uit


def transacties(user, limiet=200):
    """Markttransacties van alle characters: wat je gekocht en verkocht hebt.

    Andere gegevens dan het journaal: hier staat wélk item, hoeveel en tegen
    welke prijs per stuk. Het journaal kent alleen het bedrag.
    """
    chars = esi.characters(user)
    kleuren = _kleur_per_character([{"character_id": c.character_id} for c in chars])

    rijen = []
    for c in chars:
        for t in esi.transactions(c.character_id):
            rijen.append({**t, "_char": c.character_name,
                          "_kleur": kleuren[c.character_id]})
    if not rijen:
        return {"tx": [], "tx_aantal": 0, "tx_gekocht": 0, "tx_verkocht": 0,
                "tx_gekocht_fmt": "0", "tx_verkocht_fmt": "0"}

    rijen.sort(key=lambda t: t.get("date") or "", reverse=True)
    zichtbaar = rijen[:limiet]

    info = _type_info({t["type_id"] for t in zichtbaar})
    # Stations lossen op via /universe/names; spelersstructuren niet — die
    # hebben een eigen endpoint mét token. De grens ligt bij 1e12.
    locaties = {t["location_id"] for t in zichtbaar}
    stations = {i for i in locaties if i < 1_000_000_000_000}
    structuren = locaties - stations
    plaatsnamen = esi.names(stations | {t["client_id"] for t in zichtbaar})
    plaatsnamen.update(esi.structure_names(structuren,
                                           [c.character_id for c in chars]))

    tx = []
    for t in zichtbaar:
        aantal = int(t.get("quantity") or 0)
        prijs = float(t.get("unit_price") or 0)
        totaal = aantal * prijs
        koop = bool(t.get("is_buy"))
        tx.append({
            "datum": _parse(t.get("date")),
            "character": t["_char"],
            "kleur": t["_kleur"],
            "koop": koop,
            "item": (info.get(t["type_id"]) or {}).get("naam") or f"#{t['type_id']}",
            "plaatje": (info.get(t["type_id"]) or {}).get("plaatje", ""),
            "skin": (info.get(t["type_id"]) or {}).get("skin", False),
            "schip": (info.get(t["type_id"]) or {}).get("schip", ""),
            "aantal": aantal,
            "aantal_fmt": _nl(f"{aantal:,d}"),
            "prijs_fmt": fmt_isk_vol(prijs),
            # Koop is geld eruit, verkoop geld erin — zelfde tekens als het journaal.
            "totaal_fmt": fmt_isk_vol(-totaal if koop else totaal),
            "locatie": plaatsnamen.get(t["location_id"]) or f"#{t['location_id']}",
            "klant": plaatsnamen.get(t["client_id"]) or "",
        })

    gekocht = sum(int(t["quantity"] or 0) * float(t["unit_price"] or 0)
                  for t in rijen if t.get("is_buy"))
    verkocht = sum(int(t["quantity"] or 0) * float(t["unit_price"] or 0)
                   for t in rijen if not t.get("is_buy"))

    return {
        "tx": tx,
        "tx_aantal": len(rijen),
        "tx_gekocht": sum(1 for t in rijen if t.get("is_buy")),
        "tx_verkocht": sum(1 for t in rijen if not t.get("is_buy")),
        "tx_gekocht_fmt": fmt_isk_vol(gekocht),
        "tx_verkocht_fmt": fmt_isk_vol(verkocht),
    }


def _journaalregel(e):
    return {
        "datum": _parse(e.get("date")),
        "soort": (e.get("ref_type") or "").replace("_", " ").capitalize(),
        "omschrijving": e.get("description") or "",
        "bedrag": float(e.get("amount") or 0),
        "bedrag_fmt": fmt_isk_vol(e.get("amount")),
        "positief": float(e.get("amount") or 0) >= 0,
        "saldo_fmt": fmt_isk_vol(e.get("balance")),
        "character": e.get("_char", ""),
        "kleur": e.get("_kleur", ""),
    }


# --------------------------------------------------------------------------
# Ratting — port van Ratting.tsx
# --------------------------------------------------------------------------

# Scheepsklassen zoals ze in de groepsnaam van een NPC staan. De groep heet
# bijvoorbeeld "Deadspace Blood Raiders Battleship"; het laatste woord is de
# klasse. CCP schrijft er een paar met een hoofdletter middenin.
NPC_KLASSEN = {"battlecruiser": "Battlecruiser", "battleship": "Battleship",
               "cruiser": "Cruiser", "destroyer": "Destroyer",
               "frigate": "Frigate", "hauler": "Hauler", "officer": "Officer",
               "dreadnought": "Dreadnought", "titan": "Titan",
               "carrier": "Carrier", "sentry": "Sentry"}


def _npc_info(type_ids):
    """Naam en groep per NPC-type, uit eveuniverse met ESI als terugval.

    De rats staan als type-id in het `reason`-veld van een bounty-regel. De
    groepsnaam is meegenomen omdat daar de scheepsklasse in zit — zo is te zien
    of je battleships aan het opruimen was of frigaatjes.
    """
    ids = {int(i) for i in type_ids if i}
    uit = {}
    if not ids:
        return uit
    try:
        from eveuniverse.models import EveType

        for t in EveType.objects.filter(id__in=list(ids)).select_related("eve_group"):
            uit[t.id] = (t.name, t.eve_group.name)
    except ImportError:                 # eveuniverse niet geïnstalleerd
        pass
    ontbreekt = ids - set(uit)
    if ontbreekt:
        for tid, naam in esi.names(ontbreekt).items():
            uit[tid] = (naam or f"#{tid}", "")
    return uit


def ratting(user, dagen=30):
    """Bounty- en ESS-inkomsten over ALLE characters van dit account."""
    chars = esi.characters(user)
    kleuren = _kleur_per_character([{"character_id": c.character_id} for c in chars])
    regels = []
    for c in chars:
        for e in esi.journal(c.character_id):
            if e.get("ref_type") in (REF_BOUNTY, REF_ESS):
                regels.append({**e, "_char": c.character_name,
                               "_char_id": c.character_id,
                               "_kleur": kleuren[c.character_id]})
    regels.sort(key=lambda e: e.get("date") or "", reverse=True)

    # Verdeling per character. Ratten meerdere characters, dan wil je weten wie
    # wat bijdroeg; ratte er maar één, dan is die uitsplitsing alleen ruis.
    per_char = {}
    for e in regels:
        vak = per_char.setdefault(e["_char_id"], {
            "naam": e["_char"], "kleur": e["_kleur"], "character_id": e["_char_id"],
            "bedrag": 0.0, "aantal": 0, "bounty": 0.0, "ess": 0.0, "belasting": 0.0})
        vak["bedrag"] += float(e["amount"])
        vak["aantal"] += 1
        if e["ref_type"] == REF_BOUNTY:
            vak["bounty"] += float(e["amount"])
            vak["belasting"] += float(e.get("tax") or 0)
        else:
            vak["ess"] += float(e["amount"])
    verdeling = sorted(per_char.values(), key=lambda v: -v["bedrag"])
    hoogste_char = max((v["bedrag"] for v in verdeling), default=0) or 1
    for v in verdeling:
        v["bedrag_fmt"] = fmt_isk(v["bedrag"])
        v["bounty_fmt"] = fmt_isk(v["bounty"])
        v["ess_fmt"] = fmt_isk(v["ess"])
        v["belasting_fmt"] = fmt_isk(v["belasting"])
        v["pct"] = round(v["bedrag"] / hoogste_char * 100)
        # Aandeel bounty binnen de eigen balk, zodat de balk zelf de verhouding
        # bounty/ESS laat zien in plaats van alleen een lengte.
        v["pct_bounty"] = round(v["bounty"] / v["bedrag"] * 100) if v["bedrag"] else 0

    bounty = sum(float(e["amount"]) for e in regels if e["ref_type"] == REF_BOUNTY)
    ess = sum(float(e["amount"]) for e in regels if e["ref_type"] == REF_ESS)
    # Eén "sessie" = één kopgeldbetaling. ESS-uitbetalingen tellen niet mee,
    # anders tel je dezelfde ratting-sessie dubbel.
    sessies = sum(1 for e in regels if e["ref_type"] == REF_BOUNTY)

    # ── Belasting ─────────────────────────────────────────────────────────
    # Het bedrag in het journaal is netto: de corp-belasting is er al af. Die
    # staat apart in `tax`, dus bruto = netto + tax. Nagerekend op echte regels
    # klopt dat op de cent (10% tarief hier).
    belasting = sum(float(e.get("tax") or 0) for e in regels if e["ref_type"] == REF_BOUNTY)
    bruto = bounty + belasting

    # ── Wat je gedood hebt ────────────────────────────────────────────────
    # Het `reason`-veld van een bounty-regel is een lijstje "typeID: aantal".
    # Daar staat dus letterlijk in welke rats je afgeschoten hebt — informatie
    # die verder nergens in ESI te halen valt.
    geteld = Counter()
    for e in regels:
        if e["ref_type"] != REF_BOUNTY:
            continue
        for stuk in (e.get("reason") or "").split(","):
            if ":" not in stuk:
                continue
            tid, n = stuk.split(":", 1)
            try:
                geteld[int(tid.strip())] += int(n.strip())
            except ValueError:
                continue                # onverwachte opmaak: overslaan

    npc = _npc_info(geteld)
    rats_totaal = sum(geteld.values())
    hoogste_rat = max(geteld.values(), default=0) or 1
    rats = [{"type_id": tid, "naam": npc.get(tid, (f"#{tid}", ""))[0],
             "groep": npc.get(tid, ("", ""))[1], "aantal": n,
             "aantal_fmt": _getal(n),
             "pct": round(n / hoogste_rat * 100),
             "deel": round(n / rats_totaal * 100) if rats_totaal else 0}
            for tid, n in geteld.most_common(12)]

    # Per scheepsklasse: het laatste woord van de groepsnaam.
    per_klasse = Counter()
    for tid, n in geteld.items():
        groep = npc.get(tid, ("", ""))[1]
        laatste = groep.split()[-1] if groep else ""
        per_klasse[NPC_KLASSEN.get(laatste.lower(), laatste or "Overig")] += n
    hoogste_klasse = max(per_klasse.values(), default=0) or 1
    klassen = [{"naam": k, "aantal": n, "aantal_fmt": _getal(n),
                "pct": round(n / hoogste_klasse * 100),
                "deel": round(n / rats_totaal * 100) if rats_totaal else 0}
               for k, n in per_klasse.most_common()]

    # ── Per systeem ───────────────────────────────────────────────────────
    # Elke regel draagt het systeem mee in context_id; dat stond er al, maar
    # werd nooit gebruikt — terwijl "waar levert het wat op" precies is wat je
    # wil weten als je een plek kiest.
    sys_ids = {e.get("context_id") for e in regels
               if e.get("context_id_type") == "system_id" and e.get("context_id")}
    sys_namen = esi.names(sys_ids) if sys_ids else {}
    per_sys = {}
    for e in regels:
        if e.get("context_id_type") != "system_id" or not e.get("context_id"):
            continue
        sid = e["context_id"]
        vak = per_sys.setdefault(sid, {
            "id": sid, "naam": sys_namen.get(sid) or f"#{sid}",
            "bounty": 0.0, "ess": 0.0, "totaal": 0.0, "aantal": 0})
        bedrag = float(e["amount"])
        vak["totaal"] += bedrag
        vak["aantal"] += 1
        vak["bounty" if e["ref_type"] == REF_BOUNTY else "ess"] += bedrag
    systemen = sorted(per_sys.values(), key=lambda v: -v["totaal"])
    hoogste_sys = max((v["totaal"] for v in systemen), default=0) or 1
    for s in systemen:
        s["bounty_fmt"] = fmt_isk(s["bounty"])
        s["ess_fmt"] = fmt_isk(s["ess"])
        s["totaal_fmt"] = fmt_isk(s["totaal"])
        s["pct"] = round(s["totaal"] / hoogste_sys * 100)
        s["pct_bounty"] = round(s["bounty"] / s["totaal"] * 100) if s["totaal"] else 0

    per_dag = {}
    for e in regels:
        d = _parse(e.get("date"))
        if not d:
            continue
        sleutel = d.date().isoformat()
        vak = per_dag.setdefault(sleutel, {"dag": sleutel, "bounty": 0.0, "ess": 0.0})
        vak["bounty" if e["ref_type"] == REF_BOUNTY else "ess"] += float(e["amount"])

    # Alleen dagen waarop je daadwerkelijk gerat hebt, net als op het dashboard.
    # Dat maakt de grafiek compact in plaats van half leeg. De keerzijde is dat
    # de tijdas niet lineair is: twee balken naast elkaar kunnen weken uit elkaar
    # liggen. Daarom staat het aantal actieve dagen van het venster eronder.
    vandaag = datetime.now(timezone.utc).date()
    grens = vandaag - timedelta(days=dagen - 1)
    reeks = [dict(per_dag[k]) for k in sorted(per_dag)
             if datetime.fromisoformat(k).date() >= grens]

    piek = max((v["bounty"] + v["ess"] for v in reeks), default=0)
    # Balken schalen op de afgeronde astop, niet op de hoogste balk zelf —
    # anders klopt de hoogte niet meer met de aslabels.
    hoogste, schaalpunten = _nette_schaal(piek)
    for v in reeks:
        v["totaal"] = v["bounty"] + v["ess"]
        v["totaal_fmt"] = fmt_isk(v["totaal"])
        v["bounty_fmt"] = fmt_isk(v["bounty"])
        v["ess_fmt"] = fmt_isk(v["ess"])
        v["leeg"] = v["totaal"] <= 0
        # De balk is gestapeld: bounty onderaan, ESS erbovenop. De twee stukken
        # moeten dus optellen tot de totale hoogte, niet er allebei apart op staan.
        v["pct_bounty"] = round(v["bounty"] / hoogste * 100)
        v["pct_ess"] = round(v["totaal"] / hoogste * 100) - v["pct_bounty"]
        v["is_hoogste"] = piek > 0 and v["totaal"] >= piek
        d = datetime.fromisoformat(v["dag"]).date()
        v["dag_kort"] = f"{d.day}/{d.month}"

    actieve_dagen = len(per_dag)
    totaal = bounty + ess
    getoond = reeks[-dagen:]
    schaal = schaalpunten
    # Elke dag een datum eronder als het er weinig zijn; bij meer dagen om de
    # zoveel, anders overlappen de labels.
    stap = 1 if len(getoond) <= 20 else max(2, round(len(getoond) / 12))
    for i, v in enumerate(getoond):
        v["toon_label"] = (i % stap == 0) or (i == len(getoond) - 1)
    return {
        "totaal": totaal, "totaal_fmt": fmt_isk(totaal),
        "laatste_dag": getoond[-1]["dag"] if getoond else "",
        "schaal": schaal,
        "beste_dag_fmt": fmt_isk(piek),
        "bounty_fmt": fmt_isk(bounty), "ess_fmt": fmt_isk(ess),
        "ess_deel": round(ess / totaal * 100) if totaal else 0,
        "belasting_fmt": fmt_isk(belasting),
        "bruto_fmt": fmt_isk(bruto),
        "tarief_pct": round(belasting / bruto * 100) if bruto else 0,
        "rats": rats,
        "rats_totaal_fmt": _getal(rats_totaal),
        "rat_soorten": len(geteld),
        "klassen": klassen,
        "systemen": systemen,
        "sessies": sessies,
        "actieve_dagen": actieve_dagen,
        "gem_per_dag_fmt": fmt_isk(totaal / actieve_dagen) if actieve_dagen else "—",
        "gem_per_betaling_fmt": fmt_isk(bounty / sessies) if sessies else "—",
        "grafiek": getoond,
        "verdeling": verdeling if len(verdeling) > 1 else [],
        "aantal_characters": len(chars),
        "regels": [{
            "datum": _parse(e.get("date")),
            "kleur": e.get("_kleur", ""),
            "is_ess": e["ref_type"] == REF_ESS,
            "soort": "ESS" if e["ref_type"] == REF_ESS else "Bounty",
            "bedrag_fmt": fmt_isk(e["amount"]),
            "belasting_fmt": fmt_isk(e["tax"]) if e.get("tax") else "",
            "systeem": (sys_namen.get(e.get("context_id")) or "")
                       if e.get("context_id_type") == "system_id" else "",
            "omschrijving": e.get("description") or "",
            "character": e.get("_char", ""),
        } for e in regels[:150]],
    }


# --------------------------------------------------------------------------
# Contracten
# --------------------------------------------------------------------------

def contracten(user):
    """Alle persoonlijke contracten van het account, ontdubbeld en verrijkt."""
    chars = esi.characters(user)
    mijn = {c.character_id: c.character_name for c in chars}

    # Onthouden wélk character dit contract zag: alleen met díens token mogen we
    # straks de inhoud opvragen.
    ruw = {}
    for c in chars:
        for k in esi.contracts(c.character_id):
            ruw[k["contract_id"]] = (c.character_id, k)

    lijst = [k for _, k in ruw.values()]

    # De inhoud van alle contracten ophalen en de type-ids meteen meenemen in
    # dezelfde naam-opvraag als de characters — scheelt een tweede ronde.
    inhoud = {}
    for contract_id, (char_id, _k) in ruw.items():
        inhoud[contract_id] = esi.contract_items(char_id, contract_id)

    ids = set()
    for k in lijst:
        for veld in ("issuer_id", "assignee_id", "acceptor_id"):
            if k.get(veld):
                ids.add(k[veld])
    for spullen in inhoud.values():
        for it in spullen:
            if it.get("type_id"):
                ids.add(it["type_id"])

    # Waar een contract vandaan komt en heen moet. Stations lost /universe/names
    # op; spelersstructuren niet — daar is een eigen endpoint voor, en dat lukt
    # alleen met een token van iemand die er mag docken.
    locaties = {k[veld] for k in lijst
                for veld in ("start_location_id", "end_location_id") if k.get(veld)}
    stations = {i for i in locaties if i < 100_000_000}
    structuren = locaties - stations
    ids |= stations
    namen = esi.names(ids) if ids else {}
    if structuren:
        namen.update(esi.structure_names(structuren, list(mijn)))

    def _plek(loc_id):
        return namen.get(loc_id) or (f"#{loc_id}" if loc_id else "")

    def _plek_kort(naam):
        """'HB-5L3 - This is Sparta' → 'HB-5L3'. Het systeem is wat je zoekt."""
        return naam.split(" - ")[0] if naam else ""

    def _inhoud(contract_id):
        """Items van een contract, dubbele type-ids opgeteld, grootste eerst."""
        per_type = {}
        for it in inhoud.get(contract_id, []):
            tid = it.get("type_id")
            if not tid:
                continue
            vak = per_type.setdefault(tid, {"type_id": tid, "aantal": 0, "gevraagd": False})
            vak["aantal"] += int(it.get("quantity") or 0)
            # is_included=False betekent dat de uitgever dit juist vráágt in ruil.
            if not it.get("is_included", True):
                vak["gevraagd"] = True
        rijen = sorted(per_type.values(), key=lambda x: -x["aantal"])
        for r in rijen:
            r["naam"] = namen.get(r["type_id"]) or f"Type {r['type_id']}"
        return rijen

    rijen = []
    for k in lijst:
        beloning = float(k.get("reward") or 0)
        prijs = float(k.get("price") or 0)
        soort = k.get("type") or ""

        # Wie betaalt wie: `price` gaat van de acceptor naar de uitgever
        # (item exchange, veiling), `reward` gaat de andere kant op (koeriers).
        # Daarmee is uit te rekenen wat dit contract jóu opleverde of kostte.
        ben_uitgever = k.get("issuer_id") in mijn
        ben_acceptor = k.get("acceptor_id") in mijn
        if ben_uitgever:
            netto = prijs - beloning
        elif ben_acceptor:
            netto = beloning - prijs
        else:
            netto = 0.0

        # Richting per bedrag, zodat de kleur van het bedrag zelf al vertelt of
        # het jouw kant op kwam. Scheelt een aparte netto-kolom.
        # `price` loopt van acceptor naar uitgever, `reward` andersom.
        klaar = k.get("status") in FINISHED
        if not klaar or not prijs:
            prijs_richting = ""
        elif ben_uitgever:
            prijs_richting = "in"
        elif ben_acceptor:
            prijs_richting = "uit"
        else:
            prijs_richting = ""

        if not klaar or not beloning:
            beloning_richting = ""
        elif ben_acceptor:
            beloning_richting = "in"
        elif ben_uitgever:
            beloning_richting = "uit"
        else:
            beloning_richting = ""

        spullen = _inhoud(k["contract_id"])

        # Route, volume en onderpand: bij een koeriersrit is dát het contract.
        # Deze velden stonden altijd al in de ESI-data maar werden nooit getoond.
        van = _plek(k.get("start_location_id"))
        naar = _plek(k.get("end_location_id"))
        volume = float(k.get("volume") or 0)
        onderpand = float(k.get("collateral") or 0)
        is_koerier = soort == "courier"
        # Pas vanaf een kubieke meter zegt een prijs per m³ iets. Een rit van
        # 0,16 m³ met 20 mln beloning levert anders "125.000.000 ISK/m³" op —
        # rekenkundig waar, maar het is gewoon een vast bedrag voor een pakketje.
        per_m3 = beloning / volume if is_koerier and volume >= 1 else 0.0

        # Hoe lang een openstaand contract nog blijft staan. Bij afgeronde
        # contracten zegt de vervaldatum niets meer, dus die laten we weg.
        verloopt = _parse(k.get("date_expired"))
        rest = None
        if verloopt and k.get("status") in ("outstanding", "in_progress"):
            rest = (verloopt - datetime.now(timezone.utc)).total_seconds()

        rijen.append({
            "prijs_richting": prijs_richting,
            "beloning_richting": beloning_richting,
            "id": k["contract_id"],
            "inhoud": spullen,
            "inhoud_aantal": sum(s["aantal"] for s in spullen),
            "inhoud_soorten": len(spullen),
            "type": soort.replace("_", " "),
            "type_kort": {"item_exchange": "Exchange", "courier": "Koerier",
                          "auction": "Veiling"}.get(soort, soort or "—"),
            "type_klasse": soort.replace("_", "-"),
            "status": k.get("status") or "",
            "titel": k.get("title") or "",
            "uitgever": namen.get(k.get("issuer_id")) or mijn.get(k.get("issuer_id")) or "—",
            # De acceptor is degene die het contract aannam: bij een verkoop is
            # dat je koper, bij een koeriersrit je piloot.
            "koper": namen.get(k.get("acceptor_id")) or mijn.get(k.get("acceptor_id")) or "",
            "toegewezen": namen.get(k.get("assignee_id")) or "",
            "ben_uitgever": ben_uitgever,
            "ben_acceptor": ben_acceptor,
            "rol": "uitgegeven" if ben_uitgever else ("aangenomen" if ben_acceptor else ""),
            "prijs": prijs, "prijs_fmt": fmt_isk(prijs),
            "beloning": beloning, "beloning_fmt": fmt_isk(beloning),
            "netto": netto, "netto_fmt": fmt_isk(netto), "netto_positief": netto > 0,
            "onderpand": onderpand, "onderpand_fmt": fmt_isk(onderpand),
            "volume": volume,
            # Kleine vrachtjes met twee decimalen, anders staat er "0 m³".
            "volume_fmt": (f"{volume:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")
                           if 0 < volume < 10 else _getal(volume)),
            "is_koerier": is_koerier,
            "van": van, "van_kort": _plek_kort(van),
            "naar": naar, "naar_kort": _plek_kort(naar),
            "zelfde_plek": bool(van) and van == naar,
            "per_m3": per_m3,
            "per_m3_fmt": f"{per_m3:,.0f}".replace(",", ".") if per_m3 else "",
            "dagen_tijd": k.get("days_to_complete") or 0,
            "rest_fmt": _duur(rest) if rest and rest > 0 else "",
            "verlopen": rest is not None and rest <= 0,
            "spoed": rest is not None and 0 < rest < 2 * 86400,
            "uitgegeven": _parse(k.get("date_issued")),
            "verloopt": _parse(k.get("date_expired")),
            "voltooid": _parse(k.get("date_completed")),
            "is_klaar": k.get("status") in FINISHED,
            "is_open": k.get("status") == "outstanding",
            "is_bezig": k.get("status") == "in_progress",
            "character": mijn.get(k.get("issuer_id"), ""),
        })

    rijen.sort(key=lambda r: r["uitgegeven"] or datetime.min.replace(tzinfo=timezone.utc),
               reverse=True)

    klaar = [r for r in rijen if r["is_klaar"]]
    # Alleen afgeronde contracten hebben echt geld verplaatst; openstaande
    # zeggen nog niets over je saldo.
    verdiend = sum(r["netto"] for r in klaar if r["netto"] > 0)
    betaald = sum(-r["netto"] for r in klaar if r["netto"] < 0)

    # ── Koeriersritten ────────────────────────────────────────────────────
    koeriers = [r for r in rijen if r["is_koerier"] and r["is_klaar"]]
    vervoerd = sum(r["volume"] for r in koeriers)
    beloond = sum(r["beloning"] for r in koeriers)
    # Onderpand dat nú bij een ander in het ruim ligt: dat is het bedrag dat je
    # kwijt bent als de rit misgaat, en het enige getal hier met risico eraan.
    onderweg = sum(r["onderpand"] for r in rijen if r["is_koerier"] and r["is_bezig"])

    # Per route, want dezelfde rit gaat vaak vaker heen en weer.
    per_route = {}
    for r in koeriers:
        if not r["van"] or not r["naar"]:
            continue
        sleutel = (r["van_kort"], r["naar_kort"])
        vak = per_route.setdefault(sleutel, {
            "van": r["van_kort"], "naar": r["naar_kort"],
            "van_vol": r["van"], "naar_vol": r["naar"],
            "aantal": 0, "volume": 0.0, "beloning": 0.0, "onderpand": 0.0})
        vak["aantal"] += 1
        vak["volume"] += r["volume"]
        vak["beloning"] += r["beloning"]
        vak["onderpand"] += r["onderpand"]
    routes = sorted(per_route.values(), key=lambda v: -v["beloning"])
    hoogste_route = max((v["beloning"] for v in routes), default=0) or 1
    for v in routes:
        v["volume_fmt"] = _getal(v["volume"])
        v["beloning_fmt"] = fmt_isk(v["beloning"])
        v["onderpand_fmt"] = fmt_isk(v["onderpand"])
        v["per_m3_fmt"] = (f"{v['beloning'] / v['volume']:,.0f}".replace(",", ".")
                           if v["volume"] >= 1 else "—")
        v["pct"] = round(v["beloning"] / hoogste_route * 100)

    # ── Met wie je handelt ────────────────────────────────────────────────
    # De tegenpartij is de andere kant van hetzelfde contract: gaf jij het uit,
    # dan is dat wie het aannam, en andersom.
    per_partij = {}
    for r in klaar:
        if r["ben_uitgever"]:
            naam = r["koper"] or r["toegewezen"]
        elif r["ben_acceptor"]:
            naam = r["uitgever"]
        else:
            continue
        if not naam:
            continue
        vak = per_partij.setdefault(naam, {"naam": naam, "aantal": 0,
                                           "ontvangen": 0.0, "betaald": 0.0,
                                           "netto": 0.0})
        vak["aantal"] += 1
        vak["netto"] += r["netto"]
        if r["netto"] > 0:
            vak["ontvangen"] += r["netto"]
        else:
            vak["betaald"] += -r["netto"]
    # Contracten waar niets in verplaatst is (spullen tussen je eigen characters
    # doorschuiven) zeggen hier niets; die zouden de lijst alleen vullen met
    # nullen.
    partijen = sorted((v for v in per_partij.values() if v["ontvangen"] or v["betaald"]),
                      key=lambda v: -abs(v["netto"]))
    hoogste_partij = max((abs(v["netto"]) for v in partijen), default=0) or 1
    for v in partijen:
        v["ontvangen_fmt"] = fmt_isk(v["ontvangen"])
        v["betaald_fmt"] = fmt_isk(v["betaald"])
        v["netto_fmt"] = fmt_isk(abs(v["netto"]))
        v["positief"] = v["netto"] > 0
        v["pct"] = round(abs(v["netto"]) / hoogste_partij * 100)

    return {
        "rijen": rijen,
        "aantal": len(rijen),
        "open": sum(1 for r in rijen if r["is_open"]),
        "bezig": sum(1 for r in rijen if r["is_bezig"]),
        "klaar": len(klaar),
        "verdiend_fmt": fmt_isk(verdiend),
        "betaald_fmt": fmt_isk(betaald),
        "saldo": verdiend - betaald,
        "saldo_fmt": fmt_isk(verdiend - betaald),
        "beloning_fmt": fmt_isk(sum(r["beloning"] for r in klaar)),
        "koeriers": len(koeriers),
        "vervoerd_fmt": _getal(vervoerd),
        "beloond_fmt": fmt_isk(beloond),
        "gem_per_m3_fmt": f"{beloond / vervoerd:,.0f}".replace(",", ".") if vervoerd else "—",
        "onderweg_fmt": fmt_isk(onderweg),
        "heeft_onderweg": onderweg > 0,
        "routes": routes,
        "partijen": partijen[:10],
    }


# --------------------------------------------------------------------------
# Mining
# --------------------------------------------------------------------------

def _type_gegevens(type_ids):
    """Volume, portionSize, ertsgroep en reprocessing-materialen uit eveuniverse.

    Die database staat er lokaal al (volledige type-data geladen), dus hiervoor
    is geen enkele ESI-call nodig. De **groep** is er later bij gekomen: EVE
    kent van bijna elk erts meerdere varianten (Pyroxeres, Pyroxeres II-Grade,
    Pyroxeres III-Grade) en die horen in één regel thuis — apart zeggen ze
    weinig, samen zie je pas wat je gemijnd hebt.
    """
    volumes, portie, materialen, groepen = {}, {}, {}, {}
    try:
        from eveuniverse.models import EveType, EveTypeMaterial
    except ImportError:                 # eveuniverse niet geïnstalleerd
        return volumes, portie, materialen, groepen

    for t in EveType.objects.filter(id__in=list(type_ids)).select_related("eve_group"):
        volumes[t.id] = float(t.volume or 0)
        portie[t.id] = int(t.portion_size or 0)
        groepen[t.id] = (t.eve_group_id, t.eve_group.name)
    for m in EveTypeMaterial.objects.filter(eve_type_id__in=list(type_ids)):
        materialen.setdefault(m.eve_type_id, []).append(
            (m.material_eve_type_id, int(m.quantity or 0)))
    return volumes, portie, materialen, groepen


def mining(user, dagen=30):
    """Mining-ledger van alle characters, samengevat per erts, systeem en dag.

    ESI geeft de ledger al samengevat per dag en per ertssoort — geen losse
    cycli. Er zit geen ISK-waarde bij; die zou een marktprijs per erts vergen en
    dat is een ander verhaal dan "wat heb ik gehaald".
    """
    chars = esi.characters(user)
    kleuren = _kleur_per_character([{"character_id": c.character_id} for c in chars])

    regels = []
    for c in chars:
        for e in esi.mining(c.character_id):
            regels.append({**e, "_char": c.character_name,
                           "_char_id": c.character_id,
                           "_kleur": kleuren[c.character_id]})
    if not regels:
        return {"regels": [], "ertsen": [], "systemen": [], "grafiek": [],
                "totaal": 0, "totaal_fmt": "0", "soorten": 0, "actieve_dagen": 0,
                "verdeling": [], "aantal_characters": len(chars), "schaal": [],
                "totaal_m3_fmt": "0", "totaal_isk_fmt": "0", "totaal_ref_fmt": "0",
                "heeft_prijzen": False, "ertsgroepen": [], "mineralen": []}



    type_ids = {e["type_id"] for e in regels}
    namen = esi.names(type_ids | {e["solar_system_id"] for e in regels})

    # Volume en reprocessing-opbrengst komen uit django-eveuniverse, dat lokaal
    # de volledige type-data heeft. Prijzen via Fuzzwork (Jita buy), dezelfde
    # bron als de dashboardpagina zodat beide hetzelfde bedrag tonen.
    volumes, portie, materialen, groepen = _type_gegevens(type_ids)
    mineraal_ids = {mid for mats in materialen.values() for mid, _ in mats}
    prijzen = esi.jita_buy(type_ids | mineraal_ids)
    # De namen van de mineralen staan niet in `namen` (dat gaat over erts en
    # systemen), dus die erbij halen — één gecachte call.
    namen = {**esi.names(mineraal_ids), **namen} if mineraal_ids else namen

    def _ruwe_isk(tid, aantal):
        return aantal * prijzen.get(tid, 0.0)

    def _gerefined_isk(tid, aantal):
        """(aantal / portionSize) x som(mineraalAantal x Jita-buy)."""
        mats = materialen.get(tid)
        p = portie.get(tid) or 0
        if not mats or not p:
            return 0.0
        return (aantal / p) * sum(n * prijzen.get(mid, 0.0) for mid, n in mats)

    totaal = sum(int(e["quantity"]) for e in regels)
    totaal_m3 = sum(int(e["quantity"]) * volumes.get(e["type_id"], 0.0) for e in regels)
    totaal_isk = sum(_ruwe_isk(e["type_id"], int(e["quantity"])) for e in regels)
    totaal_ref = sum(_gerefined_isk(e["type_id"], int(e["quantity"])) for e in regels)

    def _groepeer(sleutel, label_van, met_waarde=False):
        vakken = {}
        for e in regels:
            k = e[sleutel]
            vak = vakken.setdefault(k, {"id": k, "naam": label_van(k), "aantal": 0,
                                        "m3": 0.0, "isk": 0.0, "ref_isk": 0.0})
            n = int(e["quantity"])
            vak["aantal"] += n
            vak["m3"] += n * volumes.get(e["type_id"], 0.0)
            if met_waarde:
                vak["isk"] += _ruwe_isk(e["type_id"], n)
                vak["ref_isk"] += _gerefined_isk(e["type_id"], n)
        rijen = sorted(vakken.values(), key=lambda v: -v["aantal"])
        hoogste = rijen[0]["aantal"] if rijen else 1
        for r in rijen:
            r["aantal_fmt"] = f"{r['aantal']:,}".replace(",", ".")
            r["m3_fmt"] = f"{r['m3']:,.0f}".replace(",", ".")
            r["isk_fmt"] = fmt_isk(r["isk"])
            r["ref_isk_fmt"] = fmt_isk(r["ref_isk"])
            # Refinen loont als de mineralen meer opbrengen dan het ruwe erts.
            r["ref_loont"] = r["ref_isk"] > r["isk"] > 0
            r["pct"] = round(r["aantal"] / hoogste * 100)
            r["deel"] = round(r["aantal"] / totaal * 100) if totaal else 0
        return rijen

    def naam_van(i):
        return namen.get(i) or f"#{i}"

    ertsen = _groepeer("type_id", naam_van, met_waarde=True)
    systemen = _groepeer("solar_system_id", naam_van, met_waarde=True)

    # ── Per ertsgroep ─────────────────────────────────────────────────────
    # Pyroxeres, Pyroxeres II-Grade en Pyroxeres III-Grade zijn drie regels in
    # de ledger maar één erts om over na te denken. Gegroepeerd zie je in één
    # oogopslag waar je tijd in zat; de varianten staan in de kaart zelf.
    vakken = {}
    for e in regels:
        tid = e["type_id"]
        gid, gnaam = groepen.get(tid, (0, naam_van(tid)))
        n = int(e["quantity"])
        vak = vakken.setdefault(gid, {
            "naam": gnaam, "aantal": 0, "m3": 0.0, "isk": 0.0, "ref_isk": 0.0,
            "varianten": {}, "opbrengst": defaultdict(float)})
        vak["aantal"] += n
        vak["m3"] += n * volumes.get(tid, 0.0)
        vak["isk"] += _ruwe_isk(tid, n)
        vak["ref_isk"] += _gerefined_isk(tid, n)
        v = vak["varianten"].setdefault(tid, {"type_id": tid, "naam": naam_van(tid), "aantal": 0})
        v["aantal"] += n
        # Wat dit erts na refinen oplevert, in mineralen. Zelfde formule als de
        # ISK-schatting, maar dan de hoeveelheden zelf.
        p = portie.get(tid) or 0
        if p:
            for mid, mn in materialen.get(tid, []):
                vak["opbrengst"][mid] += (n / p) * mn

    ertsgroepen = sorted(vakken.values(), key=lambda v: -v["aantal"])
    hoogste_groep = ertsgroepen[0]["aantal"] if ertsgroepen else 1
    for g in ertsgroepen:
        varianten = sorted(g["varianten"].values(), key=lambda v: -v["aantal"])
        for v in varianten:
            v["aantal_fmt"] = f"{v['aantal']:,}".replace(",", ".")
            v["deel"] = round(v["aantal"] / g["aantal"] * 100) if g["aantal"] else 0
        g["varianten"] = varianten
        g["type_id"] = varianten[0]["type_id"] if varianten else 0
        g["aantal_fmt"] = f"{g['aantal']:,}".replace(",", ".")
        g["m3_fmt"] = f"{g['m3']:,.0f}".replace(",", ".")
        g["isk_fmt"] = fmt_isk(g["isk"])
        g["ref_isk_fmt"] = fmt_isk(g["ref_isk"])
        g["ref_loont"] = g["ref_isk"] > g["isk"] > 0
        g["beste_fmt"] = fmt_isk(max(g["isk"], g["ref_isk"]))
        g["pct"] = round(g["aantal"] / hoogste_groep * 100)
        g["deel"] = round(g["aantal"] / totaal * 100) if totaal else 0
        g["opbrengst"] = sorted(
            ({"type_id": mid, "naam": naam_van(mid),
              "aantal": round(hoev), "aantal_fmt": _getal(hoev),
              "isk": hoev * prijzen.get(mid, 0.0)}
             for mid, hoev in g["opbrengst"].items() if hoev >= 1),
            key=lambda m: -m["isk"])

    # ── Wat alles bij elkaar oplevert als je het refinet ──────────────────
    opbrengst = defaultdict(float)
    for g in ertsgroepen:
        for m in g["opbrengst"]:
            opbrengst[m["type_id"]] += m["aantal"]
    mineralen_lijst = sorted(
        ({"type_id": mid, "naam": naam_van(mid), "aantal": round(hoev),
          "aantal_fmt": _getal(hoev), "isk": hoev * prijzen.get(mid, 0.0),
          "isk_fmt": fmt_isk(hoev * prijzen.get(mid, 0.0))}
         for mid, hoev in opbrengst.items() if hoev >= 1),
        key=lambda m: -m["isk"])
    hoogste_min = mineralen_lijst[0]["isk"] if mineralen_lijst else 1
    for m in mineralen_lijst:
        m["pct"] = round(m["isk"] / hoogste_min * 100) if hoogste_min else 0

    per_dag = defaultdict(int)
    for e in regels:
        per_dag[e["date"]] += int(e["quantity"])
    # Venster op de laatste dagen wáárin gemijnd is, niet op de kalender. De
    # ledger kan ouder zijn dan 30 dagen; met een venster vanaf vandaag stond de
    # grafiek leeg terwijl de tegels wel cijfers toonden.
    reeks = [{"dag": d, "aantal": per_dag[d]} for d in sorted(per_dag)][-dagen:]
    piek = max((v["aantal"] for v in reeks), default=0)
    top, schaal = _nette_schaal(piek)
    stap = 1 if len(reeks) <= 20 else max(2, round(len(reeks) / 12))
    for i, v in enumerate(reeks):
        v["pct"] = round(v["aantal"] / top * 100) if top else 0
        v["aantal_fmt"] = f"{v['aantal']:,}".replace(",", ".")
        v["is_hoogste"] = piek > 0 and v["aantal"] >= piek
        v["toon_label"] = (i % stap == 0) or (i == len(reeks) - 1)
        d = datetime.fromisoformat(v["dag"]).date()
        v["dag_kort"] = f"{d.day}/{d.month}"

    per_char = {}
    for e in regels:
        vak = per_char.setdefault(e["_char_id"], {
            "naam": e["_char"], "kleur": e["_kleur"], "character_id": e["_char_id"],
            "aantal": 0, "m3": 0.0, "isk": 0.0, "dagen": set()})
        n = int(e["quantity"])
        vak["aantal"] += n
        vak["m3"] += n * volumes.get(e["type_id"], 0.0)
        vak["isk"] += max(_ruwe_isk(e["type_id"], n), _gerefined_isk(e["type_id"], n))
        vak["dagen"].add(e["date"])
    verdeling = sorted(per_char.values(), key=lambda v: -v["aantal"])
    hoogste_char = verdeling[0]["aantal"] if verdeling else 1
    for v in verdeling:
        v["aantal_fmt"] = f"{v['aantal']:,}".replace(",", ".")
        v["m3_fmt"] = f"{v['m3']:,.0f}".replace(",", ".")
        v["isk_fmt"] = fmt_isk(v["isk"])
        v["dagen"] = len(v["dagen"])
        v["pct"] = round(v["aantal"] / hoogste_char * 100)
        v["deel"] = round(v["aantal"] / totaal * 100) if totaal else 0

    # Het verschil tussen ruw verkopen en refinen is het enige op deze pagina
    # waar een beslissing aan vastzit, dus dat rekenen we uit in plaats van twee
    # bedragen naast elkaar te zetten en de lezer te laten aftrekken.
    winst = totaal_ref - totaal_isk
    dagen_gesorteerd = sorted(per_dag)
    def _kort(d):
        x = datetime.fromisoformat(d).date()
        return f"{x.day}-{x.month}-{x.year}"

    regels.sort(key=lambda e: (e["date"], e["quantity"]), reverse=True)
    return {
        "totaal": totaal,
        "totaal_fmt": f"{totaal:,}".replace(",", "."),
        "totaal_m3_fmt": f"{totaal_m3:,.0f}".replace(",", "."),
        "totaal_isk_fmt": fmt_isk(totaal_isk),
        "totaal_ref_fmt": fmt_isk(totaal_ref),
        "refine_winst_fmt": fmt_isk(abs(winst)),
        "refine_pct": round(winst / totaal_isk * 100) if totaal_isk else 0,
        "refine_loont": winst > 0,
        "heeft_prijzen": bool(prijzen),
        "soorten": len(ertsen),
        "actieve_dagen": len(per_dag),
        "eerste_dag": _kort(dagen_gesorteerd[0]) if dagen_gesorteerd else "",
        "laatste_dag": _kort(dagen_gesorteerd[-1]) if dagen_gesorteerd else "",
        "ertsen": ertsen[:15],
        "ertsgroepen": ertsgroepen,
        "mineralen": mineralen_lijst,
        "systemen": systemen[:10],
        "grafiek": reeks,
        "schaal": schaal,
        # Bij een handvol actieve dagen rekt elke balk uit tot een blok van
        # honderden pixels; dan houden we ze smal en gecentreerd.
        "grafiek_smal": len(reeks) <= 10,
        "beste_dag_fmt": f"{piek:,}".replace(",", "."),
        "verdeling": verdeling if len(verdeling) > 1 else [],
        "aantal_characters": len(chars),
        "regels": [{
            "dag": e["date"],
            "erts": naam_van(e["type_id"]),
            "type_id": e["type_id"],
            "systeem": naam_van(e["solar_system_id"]),
            "aantal": int(e["quantity"]),
            "aantal_fmt": f"{int(e['quantity']):,}".replace(",", "."),
            "m3_fmt": f"{int(e['quantity']) * volumes.get(e['type_id'], 0.0):,.0f}".replace(",", "."),
            "isk_fmt": fmt_isk(_ruwe_isk(e["type_id"], int(e["quantity"]))),
            "character": e["_char"],
            "kleur": e["_kleur"],
        } for e in regels[:150]],
    }


# --------------------------------------------------------------------------
# Planetary Interaction
# --------------------------------------------------------------------------

# ESI geeft het planeettype als tekst; dit is puur voor de weergave.
PLANEET_LABEL = {
    "temperate": "Temperate", "barren": "Barren", "oceanic": "Oceanic",
    "ice": "Ice", "gas": "Gas", "lava": "Lava", "storm": "Storm",
    "plasma": "Plasma",
}

# De planeet zelf is ook een inventory-type, dus er is een echt plaatje van.
# Dat leest sneller dan een gekleurde badge alleen.
PLANEET_ICOON = {"temperate": 11, "ice": 12, "gas": 13, "oceanic": 2014,
                 "lava": 2015, "barren": 2016, "storm": 2017, "plasma": 2063}

# Wat een pin is, staat in de groep van z'n type — stabieler dan op naam
# matchen, want elk gebouw bestaat in acht planeetsmaken ("Barren Launchpad",
# "Gas Launchpad", …) met steeds een ander type_id.
GRP_COMMANDCENTER = 1027
GRP_FABRIEK = 1028                      # Processors: basic, advanced, high-tech
GRP_OPSLAG = 1029
GRP_LAUNCHPAD = 1030
GRP_EXTRACTOR = 1063
# Alles waar spullen blijven liggen. Het commandcenter telt mee: die heeft ook
# 500 m³ en mensen gebruiken 'm als buffer.
GRP_BEWAART = {GRP_COMMANDCENTER, GRP_OPSLAG, GRP_LAUNCHPAD}

# P0 t/m P4. Het tier staat in de groepsnaam ("Basic Commodities - Tier 1"),
# maar de id's zijn korter en veranderen niet.
TIER_VAN_GROEP = {1032: "P0", 1033: "P0", 1035: "P0",
                  1042: "P1", 1034: "P2", 1040: "P3", 1041: "P4"}

# Drempels voor het stoplicht op een kolonie.
OPSLAG_KRITIEK = 95
OPSLAG_WAARSCHUWING = 85
UREN_KRITIEK = 6
UREN_WAARSCHUWING = 24


def _getal(n):
    """1234567 → '1.234.567'."""
    try:
        return f"{float(n):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "0"


def _duur(seconden):
    """Aantal seconden als '2d 4u' / '3u 20m' / '45m'.

    Twee eenheden is genoeg: bij een extractor die over twee dagen afloopt doen
    de minuten er niet toe, en bij een half uur de dagen niet.
    """
    seconden = int(seconden or 0)
    if seconden <= 0:
        return "0m"
    dagen, rest = divmod(seconden, 86400)
    uren, rest = divmod(rest, 3600)
    minuten = rest // 60
    if dagen:
        return f"{dagen}d {uren}u" if uren else f"{dagen}d"
    if uren:
        return f"{uren}u {minuten}m" if minuten else f"{uren}u"
    return f"{minuten}m"


def _vulklasse(pct):
    """Kleurklasse voor een vulbalk: vol is hier een probleem, niet een prestatie."""
    if pct >= OPSLAG_KRITIEK:
        return "kritiek"
    if pct >= OPSLAG_WAARSCHUWING:
        return "waarschuwing"
    return "ok"


def _pi_types(type_ids):
    """naam, volume, capaciteit, groep en tier per type_id.

    Uit django-eveuniverse, dat de statische data lokaal heeft staan — dat
    scheelt een ESI-call per type. Types die er nog niet in staan halen we
    eenmalig op; daarna staan ze er voorgoed in.
    """
    ids = {int(i) for i in type_ids if i}
    uit = {}
    if not ids:
        return uit

    def _leeg(i, naam=""):
        return {"naam": naam or f"#{i}", "volume": 0.0, "capaciteit": 0.0,
                "groep": 0, "tier": ""}

    try:
        from eveuniverse.models import EveType
    except ImportError:                 # eveuniverse niet geïnstalleerd
        namen = esi.names(ids)
        return {i: _leeg(i, namen.get(i)) for i in ids}

    def _vul(t):
        uit[t.id] = {
            "naam": t.name,
            "volume": float(t.volume or 0),
            "capaciteit": float(t.capacity or 0),
            "groep": t.eve_group_id,
            "tier": TIER_VAN_GROEP.get(t.eve_group_id, ""),
        }

    for t in EveType.objects.filter(id__in=list(ids)).select_related("eve_group"):
        _vul(t)

    ontbreekt = ids - set(uit)
    for i in ontbreekt:
        try:
            t, _ = EveType.objects.get_or_create_esi(id=i)
            _vul(t)
        except Exception:               # noqa: BLE001 — dan maar zonder details
            uit[i] = _leeg(i)
    return uit


def _kolonie(p, det, typen, prijzen, nu):
    """Eén kolonie uitrekenen uit z'n ESI-detail (pins, links, routes)."""
    pins = det.get("pins") or []
    routes = det.get("routes") or []
    groep_van_pin = {}
    for pin in pins:
        groep_van_pin[pin["pin_id"]] = typen.get(pin.get("type_id"), {}).get("groep", 0)
    pin_op_id = {pin["pin_id"]: pin for pin in pins}

    def info(tid):
        return typen.get(tid) or {"naam": f"#{tid}", "volume": 0.0,
                                  "capaciteit": 0.0, "groep": 0, "tier": ""}

    def prijs(tid):
        return prijzen.get(int(tid), 0.0)

    # ── Extractors ────────────────────────────────────────────────────────
    extractors, aanvoer = [], defaultdict(float)
    for pin in pins:
        ed = pin.get("extractor_details")
        if not ed:
            continue
        cyclus = int(ed.get("cycle_time") or 0)
        per_cyclus = int(ed.get("qty_per_cycle") or 0)
        product_id = ed.get("product_type_id")
        per_uur = per_cyclus * 3600 / cyclus if cyclus else 0
        afloop = _parse(pin.get("expiry_time"))
        gestart = _parse(pin.get("install_time"))
        rest = (afloop - nu).total_seconds() if afloop else None
        loopt = rest is not None and rest > 0
        if loopt and product_id:
            aanvoer[int(product_id)] += per_uur
        # Voortgang door het programma: hoeveel van de looptijd is verstreken.
        pct = 0
        if afloop and gestart:
            heel = (afloop - gestart).total_seconds()
            if heel > 0:
                pct = max(0, min(100, round((heel - (rest or 0)) / heel * 100)))
        spoed = loopt and rest < 24 * 3600
        extractors.append({
            "product": info(product_id)["naam"] if product_id else "—",
            "product_id": product_id,
            "tier": info(product_id)["tier"] if product_id else "",
            "per_uur": per_uur,
            "per_uur_fmt": _getal(per_uur),
            "per_dag_fmt": _getal(per_uur * 24),
            "cyclus_min": round(cyclus / 60) if cyclus else 0,
            "koppen": len(ed.get("heads") or []),
            "loopt": loopt,
            "rest": rest,
            "rest_fmt": _duur(rest) if loopt else "",
            "pct": pct if afloop else 0,
            "afloop": afloop,
            "spoed": spoed,
            # De klasse bepaalt de kleur van de balk; rekenen doet de template
            # niet, die zet alleen neer wat hier al besloten is.
            "klasse": "afgelopen" if not loopt else ("spoed" if spoed else "ok"),
        })
    # Afgelopen extractors bovenaan, daarna wie het eerst aan de beurt is.
    extractors.sort(key=lambda e: (e["loopt"], e["rest"] if e["rest"] is not None else 0))

    # ── Fabrieken ─────────────────────────────────────────────────────────
    # Wat een fabriek maakt zegt ESI niet met zoveel woorden; het schema geeft
    # de naam en de cyclustijd, en de uitgaande route verklapt het producttype
    # en hoeveel er per cyclus vandaan komt.
    per_schema, cyclus_van_pin = defaultdict(lambda: {"pins": [], "aantal": 0}), {}
    for pin in pins:
        if groep_van_pin.get(pin["pin_id"]) != GRP_FABRIEK:
            continue
        sid = pin.get("schematic_id")
        per_schema[sid]["pins"].append(pin["pin_id"])
        per_schema[sid]["aantal"] += 1

    fabrieken, export = [], defaultdict(float)
    for sid, vak in per_schema.items():
        schema = esi.schematic(sid) if sid else {}
        cyclus = int(schema.get("cycle_time") or 0)
        for pin_id in vak["pins"]:
            cyclus_van_pin[pin_id] = cyclus
        uitgaand = [r for r in routes if r.get("source_pin_id") in vak["pins"]]
        product_id, per_cyclus, naar_opslag = None, 0.0, 0.0
        for r in uitgaand:
            product_id = product_id or r.get("content_type_id")
            per_cyclus += float(r.get("quantity") or 0)
            if groep_van_pin.get(r.get("destination_pin_id")) in GRP_BEWAART:
                naar_opslag += float(r.get("quantity") or 0)
        per_uur = per_cyclus * 3600 / cyclus if cyclus else 0
        uit_per_uur = naar_opslag * 3600 / cyclus if cyclus else 0
        if product_id:
            # Alles wat naar de opslag gaat is wat de planeet écht oplevert;
            # halffabrikaten die doorgaan naar de volgende fabriek zijn al in
            # dat eindproduct verwerkt en zouden dubbel tellen.
            export[int(product_id)] += uit_per_uur
            aanvoer[int(product_id)] += uit_per_uur
        fabrieken.append({
            "aantal": vak["aantal"],
            "schema": schema.get("schematic_name") or (f"Schema {sid}" if sid else "Niet ingesteld"),
            "product": info(product_id)["naam"] if product_id else "",
            "product_id": product_id,
            "tier": info(product_id)["tier"] if product_id else "",
            "per_uur_fmt": _getal(per_uur),
            "per_dag_fmt": _getal(per_uur * 24),
            "isk_dag": uit_per_uur * 24 * prijs(product_id) if product_id else 0.0,
            "isk_dag_fmt": fmt_isk(uit_per_uur * 24 * prijs(product_id)) if product_id else "",
            "cyclus_min": round(cyclus / 60) if cyclus else 0,
            "ingesteld": bool(sid),
        })
    fabrieken.sort(key=lambda f: -f["aantal"])

    # ── Verbruik van grondstoffen uit de opslag ───────────────────────────
    verbruik = defaultdict(float)
    for r in routes:
        if groep_van_pin.get(r.get("source_pin_id")) not in GRP_BEWAART:
            continue
        if groep_van_pin.get(r.get("destination_pin_id")) != GRP_FABRIEK:
            continue
        cyclus = cyclus_van_pin.get(r.get("destination_pin_id")) or 0
        if cyclus:
            verbruik[int(r["content_type_id"])] += float(r.get("quantity") or 0) * 3600 / cyclus

    # ── Opslag en voorraad ────────────────────────────────────────────────
    voorraad, gebruikt, capaciteit = defaultdict(int), 0.0, 0.0
    opslagpinnen = []
    for pin in pins:
        for c in pin.get("contents") or []:
            voorraad[int(c["type_id"])] += int(c.get("amount") or 0)
        if groep_van_pin.get(pin["pin_id"]) not in GRP_BEWAART:
            continue
        m3 = sum(int(c.get("amount") or 0) * info(c["type_id"])["volume"]
                 for c in pin.get("contents") or [])
        cap = info(pin.get("type_id"))["capaciteit"]
        gebruikt += m3
        capaciteit += cap
        pct = round(m3 / cap * 100) if cap else 0
        opslagpinnen.append({
            "naam": info(pin.get("type_id"))["naam"],
            "m3": m3, "cap": cap, "pct": pct,
            "m3_fmt": _getal(m3), "cap_fmt": _getal(cap),
            "klasse": _vulklasse(pct),
        })
    opslagpinnen.sort(key=lambda o: -o["pct"])
    opslag_pct = round(gebruikt / capaciteit * 100) if capaciteit else 0

    voorraadlijst = sorted(
        ({"type_id": tid, "naam": info(tid)["naam"], "tier": info(tid)["tier"],
          "aantal": n, "aantal_fmt": _getal(n),
          "m3": n * info(tid)["volume"],
          "isk": n * prijs(tid), "isk_fmt": fmt_isk(n * prijs(tid))}
         for tid, n in voorraad.items() if n),
        key=lambda v: -v["isk"])
    waarde = sum(v["isk"] for v in voorraadlijst)

    # ── Hoe lang loopt dit nog door zonder ingrijpen? ─────────────────────
    # Alleen wat er netto uit gaat kan opraken: draait er een extractor of een
    # fabriek die hetzelfde spul aanvult, dan telt alleen het verschil.
    uren, krappe = None, ""
    for tid, per_uur in verbruik.items():
        netto = per_uur - aanvoer.get(tid, 0.0)
        if netto <= 0:
            continue
        beschikbaar = voorraad.get(tid, 0) / netto
        if uren is None or beschikbaar < uren:
            uren, krappe = beschikbaar, info(tid)["naam"]

    # ── Stoplicht ─────────────────────────────────────────────────────────
    seinen = []
    # "Staat stil" dekt beide gevallen: een programma dat afgelopen is en een
    # extractor waar nooit een programma in gezet is.
    stil = [e for e in extractors if not e["loopt"]]
    if stil:
        seinen.append({"ernst": "kritiek",
                       "tekst": f"{len(stil)}× extractor staat stil"})
    spoed = [e for e in extractors if e["spoed"]]
    if spoed:
        eerst = min(e["rest"] for e in spoed)
        seinen.append({"ernst": "waarschuwing",
                       "tekst": f"extractor loopt af over {_duur(eerst)}"})
    if opslag_pct >= OPSLAG_KRITIEK:
        seinen.append({"ernst": "kritiek", "tekst": f"opslag {opslag_pct}% vol"})
    elif opslag_pct >= OPSLAG_WAARSCHUWING:
        seinen.append({"ernst": "waarschuwing", "tekst": f"opslag {opslag_pct}% vol"})
    if uren is not None:
        if uren < UREN_KRITIEK:
            seinen.append({"ernst": "kritiek",
                           "tekst": f"{krappe} op over {_duur(uren * 3600)}"})
        elif uren < UREN_WAARSCHUWING:
            seinen.append({"ernst": "waarschuwing",
                           "tekst": f"{krappe} op over {_duur(uren * 3600)}"})
    ernst = ("kritiek" if any(s["ernst"] == "kritiek" for s in seinen)
             else "waarschuwing" if seinen else "ok")

    isk_dag = sum(f["isk_dag"] for f in fabrieken)
    return {
        "extractors": extractors,
        "fabrieken": fabrieken,
        "opslagpinnen": opslagpinnen,
        "voorraad": voorraadlijst,
        "voorraad_top": voorraadlijst[:4],
        "voorraad_rest": max(0, len(voorraadlijst) - 4),
        "opslag_pct": opslag_pct,
        "opslag_klasse": _vulklasse(opslag_pct),
        "opslag_m3_fmt": _getal(gebruikt),
        "opslag_cap_fmt": _getal(capaciteit),
        "waarde": waarde,
        "waarde_fmt": fmt_isk(waarde),
        "isk_dag": isk_dag,
        "isk_dag_fmt": fmt_isk(isk_dag),
        "uren": uren,
        "uren_fmt": _duur(uren * 3600) if uren is not None else "",
        "krappe": krappe,
        "seinen": seinen,
        "ernst": ernst,
        "export": export,
        "verbruik": verbruik,
        "heeft_detail": bool(pins),
        # Zonder detail (geen token meer, of ESI hikte) tonen we de kaart wel,
        # maar zonder de lege blokken die dan zouden verschijnen.
        "aantal_pins": len(pins),
    }


def pi(user):
    """Planetaire kolonies van alle characters, met wat er op staat te draaien."""
    chars = esi.characters(user)
    kleuren = _kleur_per_character([{"character_id": c.character_id} for c in chars])

    rijen = []
    for c in chars:
        for p in esi.planets(c.character_id):
            rijen.append({**p, "_char": c.character_name,
                          "_char_id": c.character_id,
                          "_kleur": kleuren[c.character_id]})
    if not rijen:
        return {"kolonies": [], "aantal": 0, "aantal_characters": len(chars),
                "per_type": [], "verdeling": [], "pins": 0, "voorraad": [],
                "aandacht": [], "systemen": 0, "productie": [], "kritiek": 0,
                "extractors_totaal": 0, "extractors_lopend": 0}

    # Alleen de systemen via /universe/names; planeet-ids kent die endpoint niet
    # (404), daar is /universe/planets/{id}/ voor.
    namen = esi.names({p["solar_system_id"] for p in rijen})
    planeetnamen = {p["planet_id"]: esi.planet_info(p["planet_id"]) for p in rijen}
    details = {p["planet_id"]: esi.planet_detail(p["_char_id"], p["planet_id"])
               for p in rijen}

    # Alle types die op de pagina voorkomen in één keer opzoeken: de gebouwen
    # (voor hun soort en opslagruimte) en alles wat er ligt of gemaakt wordt.
    type_ids = set()
    for det in details.values():
        for pin in det.get("pins") or []:
            type_ids.add(pin.get("type_id"))
            for c in pin.get("contents") or []:
                type_ids.add(c.get("type_id"))
            ed = pin.get("extractor_details") or {}
            type_ids.add(ed.get("product_type_id"))
        for r in det.get("routes") or []:
            type_ids.add(r.get("content_type_id"))
    type_ids.discard(None)
    typen = _pi_types(type_ids)

    # Prijzen alleen voor handelswaar: gebouwen hebben geen zinnige Jita-prijs
    # en zouden de aanvraag alleen maar groter maken.
    handel = {tid for tid, t in typen.items() if t["tier"]}
    prijzen = esi.jita_buy(handel)

    nu = datetime.now(timezone.utc)
    per_type = defaultdict(int)
    per_char = {}
    kolonies = []
    voorraad_totaal = defaultdict(int)
    for p in rijen:
        soort = p.get("planet_type") or "onbekend"
        per_type[soort] += 1
        k = _kolonie(p, details.get(p["planet_id"]) or {}, typen, prijzen, nu)
        for v in k["voorraad"]:
            voorraad_totaal[v["type_id"]] += v["aantal"]
        vak = per_char.setdefault(p["_char_id"], {
            "naam": p["_char"], "kleur": p["_kleur"], "aantal": 0, "pins": 0,
            "waarde": 0.0, "isk_dag": 0.0})
        vak["aantal"] += 1
        vak["pins"] += int(p.get("num_pins") or 0)
        vak["waarde"] += k["waarde"]
        vak["isk_dag"] += k["isk_dag"]
        kolonies.append({
            **k,
            "planeet": planeetnamen.get(p["planet_id"]) or f"#{p['planet_id']}",
            "planeet_id": p["planet_id"],
            "systeem": namen.get(p["solar_system_id"]) or f"#{p['solar_system_id']}",
            "type": PLANEET_LABEL.get(soort, soort.capitalize()),
            "type_ruw": soort,
            "icoon_id": PLANEET_ICOON.get(soort, 2016),
            "niveau": p.get("upgrade_level") or 0,
            # Vijf bolletjes op de kaart; welke aan staan is hier al beslist,
            # zodat de template niet hoeft te rekenen.
            "niveaus": [{"aan": n <= (p.get("upgrade_level") or 0)}
                        for n in range(1, 6)],
            "pins": p.get("num_pins"),
            "bijgewerkt": _parse(p.get("last_update")),
            "character": p["_char"],
            "character_id": p["_char_id"],
            "kleur": p["_kleur"],
        })

    # Wat aandacht vraagt bovenaan: eerst kritiek, dan waarschuwingen, en
    # binnen die groep de kolonie die er het slechtst voor staat.
    volgorde = {"kritiek": 0, "waarschuwing": 1, "ok": 2}
    kolonies.sort(key=lambda k: (volgorde[k["ernst"]], k["character"],
                                 k["systeem"], k["planeet"]))
    aandacht = [{"planeet": k["planeet"], "character": k["character"],
                 "kleur": k["kleur"], "ernst": k["ernst"],
                 "tekst": ", ".join(s["tekst"] for s in k["seinen"])}
                for k in kolonies if k["seinen"]]

    def info(tid):
        return typen.get(tid) or {"naam": f"#{tid}", "volume": 0.0, "tier": ""}

    voorraadlijst = sorted(
        ({"type_id": tid, "naam": info(tid)["naam"], "tier": info(tid)["tier"],
          "aantal_fmt": _getal(n),
          "m3_fmt": _getal(n * info(tid)["volume"]),
          "isk": n * prijzen.get(tid, 0.0),
          "isk_fmt": fmt_isk(n * prijzen.get(tid, 0.0))}
         for tid, n in voorraad_totaal.items() if n),
        key=lambda v: -v["isk"])

    # Wat het account netto oplevert. Bruto optellen zou dubbeltellen: de P1 die
    # een extractieplaneet uitspuugt gaat vaak rechtstreeks een fabrieksplaneet
    # in, en zit dan al in de waarde van het eindproduct verwerkt. Dus per
    # product de export van alle planeten minus wat elders weer opgaat.
    export_totaal, verbruik_totaal = defaultdict(float), defaultdict(float)
    for k in kolonies:
        for tid, per_uur in k["export"].items():
            export_totaal[tid] += per_uur
        for tid, per_uur in k["verbruik"].items():
            verbruik_totaal[tid] += per_uur

    productie = []
    for tid, per_uur in export_totaal.items():
        netto = max(0.0, per_uur - verbruik_totaal.get(tid, 0.0))
        productie.append({
            "naam": typen.get(tid, {}).get("naam", f"#{tid}"),
            "tier": typen.get(tid, {}).get("tier", ""),
            "type_id": tid,
            "bruto_fmt": _getal(per_uur * 24),
            "eigen_fmt": _getal(min(per_uur, verbruik_totaal.get(tid, 0.0)) * 24),
            "netto": netto * 24,
            "netto_fmt": _getal(netto * 24),
            "isk": netto * 24 * prijzen.get(tid, 0.0),
            "isk_fmt": fmt_isk(netto * 24 * prijzen.get(tid, 0.0)),
        })
    productie.sort(key=lambda p: -p["isk"])

    extractors = [e for k in kolonies for e in k["extractors"]]
    lopend = [e for e in extractors if e["loopt"]]
    eerst = min((e["rest"] for e in lopend), default=None)
    waarde_totaal = sum(k["waarde"] for k in kolonies)
    isk_dag = sum(p["isk"] for p in productie)
    isk_dag_bruto = sum(k["isk_dag"] for k in kolonies)
    hoogste = max(per_type.values())

    verdeling = sorted(per_char.values(), key=lambda v: -v["aantal"])
    for v in verdeling:
        v["waarde_fmt"] = fmt_isk(v["waarde"])
        v["isk_dag_fmt"] = fmt_isk(v["isk_dag"])

    return {
        "kolonies": kolonies,
        "aantal": len(kolonies),
        "pins": sum(int(p.get("num_pins") or 0) for p in rijen),
        "aantal_characters": len(chars),
        "met_kolonies": len(per_char),
        "systemen": len({p["solar_system_id"] for p in rijen}),
        "op_max": sum(1 for p in rijen if (p.get("upgrade_level") or 0) >= 5),
        "extractors_totaal": len(extractors),
        "extractors_lopend": len(lopend),
        "eerst_af_fmt": _duur(eerst) if eerst else "",
        "waarde_fmt": fmt_isk(waarde_totaal),
        "isk_dag_fmt": fmt_isk(isk_dag),
        "isk_dag_bruto_fmt": fmt_isk(isk_dag_bruto),
        "isk_maand_fmt": fmt_isk(isk_dag * 30),
        "productie": productie,
        "eigen_gebruik": any(p["eigen_fmt"] != "0" for p in productie),
        "aandacht": aandacht,
        "kritiek": sum(1 for k in kolonies if k["ernst"] == "kritiek"),
        "voorraad": voorraadlijst,
        "voorraad_isk_fmt": fmt_isk(sum(v["isk"] for v in voorraadlijst)),
        # De typeverdeling is geen kerncijfer maar een verhouding, dus met een
        # balk in plaats van als tegel: anders verdringen zes types de cijfers
        # waar je echt naar kijkt.
        "per_type": sorted(({"naam": PLANEET_LABEL.get(k, k.capitalize()),
                             "ruw": k, "aantal": v,
                             "pct": round(v / hoogste * 100),
                             "deel": round(v / len(rijen) * 100)}
                            for k, v in per_type.items()), key=lambda x: -x["aantal"]),
        "verdeling": verdeling,
    }
