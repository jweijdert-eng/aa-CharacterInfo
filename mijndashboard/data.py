"""Rekenwerk voor de tabbladen — Finance.

De ratting-logica is een port van `Ratting.tsx` uit het dashboard: dezelfde twee
ref_types, dezelfde totalen en dezelfde groepering per dag, zodat beide plekken
hetzelfde getal laten zien.
"""

import math
import re
from collections import Counter, defaultdict
from html import escape
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from . import esi, mailtekst

MAAND_KORT = ["", "jan", "feb", "mrt", "apr", "mei", "jun",
              "jul", "aug", "sep", "okt", "nov", "dec"]
WEEKDAG_KORT = ["ma", "di", "wo", "do", "vr", "za", "zo"]

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


# Refine-rendement. Tot v2.13 rekende deze pagina met een **volledige** refine,
# en dat bestaat in EVE niet: wat je terugkrijgt hangt af van je skills, het
# station of de structuur en z'n rigs. Zelfs met alles op V kom je niet aan de
# 100%. Een te hoog rendement maakt van "refinen loont" een advies dat in het
# spel niet uitkomt, dus staat het nu op een realistische waarde die je zelf kunt
# bijstellen. De opbrengst schaalt lineair, dus een andere stand is één klik.
REFINE_STANDAARD = 80
REFINE_KEUZES = (50, 60, 70, 80, 90, 100)


def mining(user, dagen=30, rendement=REFINE_STANDAARD):
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
    # Ook de volumes van de mineralen. Erts is bijna alleen maar ruimte: pas als
    # je weet hoeveel m³ er ná de oven overblijft, weet je of het de moeite is om
    # het ruwe spul überhaupt te verslepen.
    mineraal_volumes = _type_gegevens(mineraal_ids)[0] if mineraal_ids else {}
    # De namen van de mineralen staan niet in `namen` (dat gaat over erts en
    # systemen), dus die erbij halen — één gecachte call.
    namen = {**esi.names(mineraal_ids), **namen} if mineraal_ids else namen

    def _ruwe_isk(tid, aantal):
        return aantal * prijzen.get(tid, 0.0)

    # Alles wat uit de oven komt gaat door dit rendement heen: de ISK-schatting,
    # de mineralen-chips op de kaarten en de totaaltabel. Eén plek, zodat die
    # drie nooit uit elkaar kunnen lopen.
    factor = max(min(int(rendement or REFINE_STANDAARD), 100), 10) / 100

    def _gerefined_isk(tid, aantal):
        """(aantal / portionSize) x som(mineraalAantal x Jita-buy) x rendement."""
        mats = materialen.get(tid)
        p = portie.get(tid) or 0
        if not mats or not p:
            return 0.0
        return (aantal / p) * sum(n * prijzen.get(mid, 0.0) for mid, n in mats) * factor

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
                vak["opbrengst"][mid] += (n / p) * mn * factor

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
        # Wat een kubieke meter ruimte in je hold opbrengt. Dát is waar een
        # miner op kiest: je hebt geen tekort aan erts, je hebt een tekort aan
        # ruimte — in het schip, in de hangar en in de vracht naar Jita.
        g["per_m3"] = (max(g["isk"], g["ref_isk"]) / g["m3"]) if g["m3"] else 0.0
        g["per_m3_fmt"] = fmt_isk(g["per_m3"])
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

    # Wat er ná de oven aan volume overblijft. Erts is grotendeels lucht, dus dit
    # is niet alleen een ISK-vraag maar ook een vrachtvraag: het scheelt bij deze
    # hoeveelheid het verschil tussen een paar Deep Space Transports en een hele
    # jump freighter.
    mineraal_m3 = sum(m["aantal"] * mineraal_volumes.get(m["type_id"], 0.0)
                      for m in mineralen_lijst)
    beste_erts = max(ertsgroepen, key=lambda g: g["per_m3"], default=None)

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

    # ── Per dag: wát je gemijnd hebt, niet alleen hoeveel ─────────────────
    # De grafiek hierboven geeft één balk per dag; die zegt hoe groot een dag
    # was maar niet waar hij uit bestond. Dit blok vult dat aan zonder dat je de
    # hele ledger hoeft door te lopen: per dag de ertsen bij elkaar opgeteld
    # over al je characters en systemen.
    dagvakken = {}
    for e in regels:
        n = int(e["quantity"])
        tid = e["type_id"]
        vak = dagvakken.setdefault(e["date"], {
            "dag": e["date"], "aantal": 0, "m3": 0.0, "isk": 0.0, "ref_isk": 0.0,
            "ertsen": defaultdict(int), "characters": {}, "systemen": set()})
        vak["aantal"] += n
        vak["m3"] += n * volumes.get(tid, 0.0)
        vak["isk"] += _ruwe_isk(tid, n)
        vak["ref_isk"] += _gerefined_isk(tid, n)
        vak["ertsen"][tid] += n
        vak["characters"].setdefault(e["_char"], e["_kleur"])
        vak["systemen"].add(naam_van(e["solar_system_id"]))

    # Nieuwste bovenaan: de laatste sessie is waar je naar kijkt.
    dagen_detail = sorted(dagvakken.values(), key=lambda d: d["dag"], reverse=True)
    hoogste_dag = max((d["aantal"] for d in dagen_detail), default=0) or 1
    for d in dagen_detail:
        datum = datetime.fromisoformat(d["dag"]).date()
        d["dag_fmt"] = f"{datum.day}-{datum.month}-{datum.year}"
        d["weekdag"] = WEEKDAG_KORT[datum.weekday()]
        d["aantal_fmt"] = _getal(d["aantal"])
        d["m3_fmt"] = f"{d['m3']:,.0f}".replace(",", ".")
        d["isk_fmt"] = fmt_isk(d["isk"])
        d["ref_isk_fmt"] = fmt_isk(d["ref_isk"])
        d["pct"] = round(d["aantal"] / hoogste_dag * 100)
        d["beste"] = d["aantal"] >= hoogste_dag
        # Grootste eerst: wat de dag bepaalde staat vooraan.
        d["ertsen"] = [{"type_id": tid, "naam": naam_van(tid), "aantal": n,
                        "aantal_fmt": _getal(n),
                        "isk_fmt": fmt_isk(_ruwe_isk(tid, n))}
                       for tid, n in sorted(d["ertsen"].items(), key=lambda kv: -kv[1])]
        d["characters"] = [{"naam": naam, "kleur": kleur}
                           for naam, kleur in sorted(d["characters"].items())]
        d["systemen"] = sorted(d["systemen"])

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
        # Het rendement staat in de URL, dus een andere stand is te delen en de
        # terugknop werkt. 100% blijft kiesbaar, maar is nadrukkelijk theorie.
        "rendement": round(factor * 100),
        "refine_keuzes": [{"waarde": k, "actief": k == round(factor * 100)}
                          for k in REFINE_KEUZES],
        "mineraal_m3_fmt": f"{mineraal_m3:,.0f}".replace(",", "."),
        "krimp_pct": round((1 - mineraal_m3 / totaal_m3) * 100) if totaal_m3 else 0,
        # ISK per m³ vóór en ná de oven: het ruwe erts per m³ erts, de mineralen
        # per m³ mineralen. Zo zie je wat een m³ vracht je in beide gevallen waard is.
        "ruw_per_m3_fmt": fmt_isk(totaal_isk / totaal_m3) if totaal_m3 else "0",
        "ref_per_m3_fmt": fmt_isk(totaal_ref / mineraal_m3) if mineraal_m3 else "0",
        "beste_erts": beste_erts,
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
        "dagen_detail": dagen_detail,
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
    # `extractie` staat los van `aanvoer`: alleen wat er uit de grond komt is
    # nieuwe grondstof voor het account. Wat een fabriek uitspuugt is elders al
    # als verbruik geteld en mag in de ketenberekening niet als bron dubbeltellen.
    extractors, aanvoer, extractie = [], defaultdict(float), defaultdict(float)
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
            extractie[int(product_id)] += per_uur
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

    fabrieken, export, verbruik = [], defaultdict(float), defaultdict(float)
    groepen = []
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

        # Wat deze fabrieken uit de opslag trekken. Per receptgroep bijhouden en
        # niet alleen per planeet, want straks moet elke groep apart teruggezet
        # kunnen worden op het tempo van z'n krapste grondstof.
        inputs = defaultdict(float)
        for r in routes:
            if r.get("destination_pin_id") not in vak["pins"]:
                continue
            if groep_van_pin.get(r.get("source_pin_id")) not in GRP_BEWAART:
                continue
            if cyclus:
                inputs[int(r["content_type_id"])] += float(r.get("quantity") or 0) * 3600 / cyclus
        for tid, hoeveel in inputs.items():
            verbruik[tid] += hoeveel

        if product_id:
            # Alles wat naar de opslag gaat is wat de planeet écht oplevert;
            # halffabrikaten die doorgaan naar de volgende fabriek zijn al in
            # dat eindproduct verwerkt en zouden dubbel tellen.
            export[int(product_id)] += uit_per_uur
            aanvoer[int(product_id)] += uit_per_uur
        fabriek = {
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
        }
        fabrieken.append(fabriek)
        groepen.append({
            "product_id": int(product_id) if product_id else None,
            "uit_per_uur": uit_per_uur,
            "per_uur": per_uur,
            "inputs": dict(inputs),
            "isk_uur": uit_per_uur * prijs(product_id) if product_id else 0.0,
            "fabriek": fabriek,
        })
    fabrieken.sort(key=lambda f: -f["aantal"])

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
    # Alleen grondstof die van BUITEN komt kan de kolonie stilleggen. Wordt het
    # spul hier zelf gemaakt (extractor of fabriek), dan raakt de voorraad
    # hooguit leeg en zakken de fabrieken terug naar het tempo van de aanvoer —
    # ze vallen niet stil. Dat als "op over 15m" melden is loos alarm: die
    # planeet draait al maanden zo en de echte klok is het extractorprogramma.
    uren, krappe = None, ""
    onderbezet = []
    for tid, per_uur in verbruik.items():
        lokaal = aanvoer.get(tid, 0.0)

        if lokaal > 0:
            # Pas melden bij een gat van minstens 5%. Anders levert afronding
            # "fabrieken draaien op 100% — meer capaciteit dan aanvoer" op, en
            # dat spreekt zichzelf tegen.
            if lokaal < per_uur * 0.95:
                onderbezet.append({
                    "naam": info(tid)["naam"],
                    "aanvoer_fmt": _getal(lokaal),
                    "capaciteit_fmt": _getal(per_uur),
                    "pct": round(lokaal / per_uur * 100),
                })
            continue

        if per_uur <= 0:
            continue
        beschikbaar = voorraad.get(tid, 0) / per_uur
        if uren is None or beschikbaar < uren:
            uren, krappe = beschikbaar, info(tid)["naam"]

    # De zwaarst onderbezette bovenaan: die zegt het meest over de planeet.
    onderbezet.sort(key=lambda o: o["pct"])

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
        "onderbezet": onderbezet,
        "seinen": seinen,
        "ernst": ernst,
        "export": export,
        "verbruik": verbruik,
        "extractie": extractie,
        "groepen": groepen,
        "heeft_detail": bool(pins),
        # Zonder detail (geen token meer, of ESI hikte) tonen we de kaart wel,
        # maar zonder de lege blokken die dan zouden verschijnen.
        "aantal_pins": len(pins),
    }


def _doorstroom(groepen, extractie):
    """Hoe hard draait elke receptgroep werkelijk, als aandeel van z'n capaciteit?

    Een fabriek kan niet meer verwerken dan er binnenkomt. Rekenen met
    capaciteit geeft daardoor cijfers die nergens op slaan zodra één schakel
    krap zit: acht Robotics-fabrieken die maar voor vier fabrieken grondstof
    krijgen leveren de helft, niet het volle pond.

    Het account is hier één voorraadpot per product — waar iets vandaan komt
    doet er niet toe, want je vliegt het zelf rond. Beginnen op vol vermogen en
    net zo lang terugschroeven tot het klopt: elke ronde kan een groep alleen
    maar langzamer gaan, dus dit loopt naar één antwoord toe.

    Producten die je nergens zelf maakt tellen als onbeperkt: die koop of haal
    je van buiten, en dan is een tekort geen ketenprobleem maar een voorraad-
    kwestie — daar gaat de opraak-melding op de kaart al over.
    """
    eigen_bron = set(extractie) | {g["product_id"] for g in groepen if g["product_id"]}
    factor = [1.0] * len(groepen)

    for _ in range(50):
        aanbod = defaultdict(float, extractie)
        vraag = defaultdict(float)
        for i, g in enumerate(groepen):
            if g["product_id"]:
                aanbod[g["product_id"]] += g["uit_per_uur"] * factor[i]
            for tid, per_uur in g["inputs"].items():
                vraag[tid] += per_uur * factor[i]

        dekking = {}
        for tid, gevraagd in vraag.items():
            if tid not in eigen_bron:
                dekking[tid] = 1.0
            elif gevraagd > 0:
                dekking[tid] = min(1.0, aanbod.get(tid, 0.0) / gevraagd)
            else:
                dekking[tid] = 1.0

        # Vermenigvuldigen, niet overschrijven. Zet je de factor gelijk aáń de
        # dekking, dan slingert het: op halve kracht is de dekking weer 100%,
        # dus gaat hij terug naar vol, en zakt de dekking weer naar de helft.
        nieuw = [factor[i] * min([1.0] + [dekking.get(tid, 1.0) for tid in g["inputs"]])
                 for i, g in enumerate(groepen)]
        if all(abs(a - b) < 1e-6 for a, b in zip(nieuw, factor)):
            factor = nieuw
            break
        factor = nieuw

    # Welk product houdt de boel tegen? Bij het vaste punt is de dekking overal
    # 100% — de vraag is immers teruggeschroefd tot wat er is — dus daar valt
    # het niet meer aan af te lezen. Krap is wat er in z'n geheel opgaat: alles
    # wat je ervan maakt wordt ook opgestookt, dus meer ervan is meer eindproduct.
    aanbod, vraag = defaultdict(float, extractie), defaultdict(float)
    for i, g in enumerate(groepen):
        if g["product_id"]:
            aanbod[g["product_id"]] += g["uit_per_uur"] * factor[i]
        for tid, per_uur in g["inputs"].items():
            vraag[tid] += per_uur * factor[i]
    krap = {tid for tid, gevraagd in vraag.items()
            if tid in eigen_bron and gevraagd > 0 and aanbod.get(tid, 0.0) <= gevraagd * 1.001}

    return factor, krap


def _netto_isk(groepen, factoren, prijzen):
    """Wat de hele keten per dag oplevert bij deze doorstroom, na eigen gebruik."""
    gemaakt, opgestookt = defaultdict(float), defaultdict(float)
    for g, f in zip(groepen, factoren):
        if g["product_id"]:
            gemaakt[g["product_id"]] += g["uit_per_uur"] * f
        for tid, per_uur in g["inputs"].items():
            opgestookt[tid] += per_uur * f

    return sum(max(0.0, per_uur - opgestookt.get(tid, 0.0)) * 24 * prijzen.get(tid, 0.0)
               for tid, per_uur in gemaakt.items())


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
    # Sleutel op character én planeet. Meerdere characters mogen elk een eigen
    # kolonie op dezelfde planeet hebben; op alleen planeet-id overschrijft de
    # ene de andere, en dan telt de extractie van die planeet dubbel terwijl de
    # productie van de overschreven kolonie uit de keten verdwijnt.
    details = {(p["_char_id"], p["planet_id"]): esi.planet_detail(p["_char_id"], p["planet_id"])
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
        k = _kolonie(p, details.get((p["_char_id"], p["planet_id"])) or {}, typen, prijzen, nu)
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

    # ── Wat er werkelijk doorheen komt ────────────────────────────────────
    # Eerst de hele keten doorrekenen, want een fabriek die z'n grondstof niet
    # krijgt haalt z'n capaciteit niet. Zonder deze stap staat er een dagbedrag
    # op de pagina dat je alleen zou halen als álles altijd vol aangevoerd wordt.
    alle_groepen = [g for k in kolonies for g in k["groepen"]]
    extractie_totaal = defaultdict(float)
    for k in kolonies:
        for tid, per_uur in k["extractie"].items():
            extractie_totaal[tid] += per_uur
    factoren, krap = _doorstroom(alle_groepen, extractie_totaal)
    for g, f in zip(alle_groepen, factoren):
        g["factor"] = f
        g["fabriek"]["benutting"] = round(f * 100)
        g["fabriek"]["isk_dag_echt_fmt"] = fmt_isk(g["isk_uur"] * f * 24)
        g["fabriek"]["per_dag_echt_fmt"] = _getal(g["per_uur"] * f * 24)

    # Per kolonie: hoe hard draait die, en welke grondstof houdt 'm tegen?
    for k in kolonies:
        capaciteit_isk = sum(g["isk_uur"] for g in k["groepen"])
        echt_isk = sum(g["isk_uur"] * g["factor"] for g in k["groepen"])
        # Wegen op ISK: een groep die niets opbrengt hoort het percentage van de
        # kolonie niet te bepalen. Zonder opbrengst valt het terug op de traagste.
        k["benutting"] = (round(echt_isk / capaciteit_isk * 100) if capaciteit_isk
                          else (round(min(g["factor"] for g in k["groepen"]) * 100)
                                if k["groepen"] else 100))
        k["isk_dag"] = echt_isk * 24
        k["isk_dag_fmt"] = fmt_isk(k["isk_dag"])
        k["isk_dag_capaciteit_fmt"] = fmt_isk(capaciteit_isk * 24)
        # De groep die het hardst geremd wordt bepaalt waar je naar moet kijken.
        traagste = min(k["groepen"], key=lambda g: g["factor"], default=None)
        knelpunten = [tid for tid in (traagste["inputs"] if traagste else {}) if tid in krap]
        k["knelpunt"] = typen.get(knelpunten[0], {}).get("naam", "") if knelpunten else ""

    # De character-tegels zijn hierboven met de capaciteit gevuld; nu de kolonies
    # bijgesteld zijn moeten die mee, anders tellen de tegels niet op tot het
    # bedrag dat bovenaan de pagina staat.
    for vak in per_char.values():
        vak["isk_dag"] = 0.0
    for k in kolonies:
        per_char[k["character_id"]]["isk_dag"] += k["isk_dag"]

    # Wat het account netto oplevert. Bruto optellen zou dubbeltellen: de P1 die
    # een extractieplaneet uitspuugt gaat vaak rechtstreeks een fabrieksplaneet
    # in, en zit dan al in de waarde van het eindproduct verwerkt. Dus per
    # product de export van alle planeten minus wat elders weer opgaat.
    export_totaal, verbruik_totaal = defaultdict(float), defaultdict(float)
    capaciteit_totaal = defaultdict(float)
    for g in alle_groepen:
        if g["product_id"]:
            export_totaal[g["product_id"]] += g["uit_per_uur"] * g["factor"]
            capaciteit_totaal[g["product_id"]] += g["uit_per_uur"]
        for tid, per_uur in g["inputs"].items():
            verbruik_totaal[tid] += per_uur * g["factor"]

    productie = []
    for tid, per_uur in export_totaal.items():
        netto = max(0.0, per_uur - verbruik_totaal.get(tid, 0.0))
        productie.append({
            "naam": typen.get(tid, {}).get("naam", f"#{tid}"),
            "tier": typen.get(tid, {}).get("tier", ""),
            "type_id": tid,
            "bruto_fmt": _getal(per_uur * 24),
            "capaciteit_fmt": _getal(capaciteit_totaal.get(tid, 0.0) * 24),
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

    # Wat je zou halen als elke fabriek altijd vol aangevoerd werd. Op dezelfde
    # manier gerekend als het echte bedrag — dus nétto, na aftrek van wat je
    # zelf weer opstookt. De som van alle fabriekscapaciteit ernaast zetten zou
    # een veel groter getal geven, maar dat telt halffabrikaten dubbel.
    isk_dag_capaciteit = _netto_isk(alle_groepen, [1.0] * len(alle_groepen), prijzen)
    # Alleen tonen als het echt scheelt; anders lijkt er een probleem te zijn
    # waar de keten gewoon rondloopt.
    toont_capaciteit = isk_dag_capaciteit > isk_dag * 1.05
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
        "isk_dag_capaciteit_fmt": fmt_isk(isk_dag_capaciteit),
        "toont_capaciteit": toont_capaciteit,
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


# --------------------------------------------------------------------------
# Mail
# --------------------------------------------------------------------------

# De vier vaste labels van elke mailbox. Eigen labels die iemand zelf aanmaakt
# komen met naam en al uit ESI mee.
MAIL_VASTE_LABELS = {1: "Inbox", 2: "Verzonden", 4: "Corp", 8: "Alliance"}

VAK_LABEL = {"inbox": "Inbox", "corp": "Corp", "alliance": "Alliance",
             "verzonden": "Verzonden"}

# Welk dogma-effect een module in welk slot hangt. Hiermee is uit een kale
# fitting-link (die alleen type-ids draagt) alsnog een echte EFT-uitdraai te
# maken, mét de slots in de goede volgorde.
EFFECT_SLOT = {12: "hoog", 13: "midden", 11: "laag", 2663: "rig", 3772: "subsysteem"}
CAT_DRONE = 18

MAIL_MAX = 300              # zoveel mails tonen we; meer scrollt niemand door
MAIL_KORT = 190             # lengte van het voorproefje onder het onderwerp

PORTRET = {
    "character": "https://images.evetech.net/characters/%s/portrait?size=64",
    "corporation": "https://images.evetech.net/corporations/%s/logo?size=64",
    "alliance": "https://images.evetech.net/alliances/%s/logo?size=64",
}


def _mail_typen(type_ids):
    """Naam, groep en slot van alles wat in een fitting-link voorkomt.

    Komt uit eveuniverse, dus zonder ESI-call. Het slot staat er niet als veld
    in maar volgt uit de dogma-effects: een module met effect 11 past in een
    laag slot, 13 in een midden, enzovoort. Heeft een type helemaal geen
    slot-effect, dan is het geen module maar lading — drones apart, want die
    horen in EFT in hun eigen blok.
    """
    typen = {}
    ids = [int(i) for i in type_ids if i]
    if not ids:
        return typen
    try:
        from eveuniverse.models import EveType, EveTypeDogmaEffect
    except ImportError:                 # eveuniverse niet geïnstalleerd
        return typen

    for t in EveType.objects.filter(id__in=ids).select_related("eve_group"):
        typen[t.id] = {
            "naam": t.name,
            "groep": t.eve_group.name if t.eve_group_id else "",
            "categorie": t.eve_group.eve_category_id if t.eve_group_id else 0,
            "slot": "",
        }
    for type_id, effect_id in (EveTypeDogmaEffect.objects
                               .filter(eve_type_id__in=ids,
                                       eve_dogma_effect_id__in=list(EFFECT_SLOT))
                               .values_list("eve_type_id", "eve_dogma_effect_id")):
        vak = typen.get(type_id)
        if vak and not vak["slot"]:
            vak["slot"] = EFFECT_SLOT[effect_id]
    for vak in typen.values():
        if not vak["slot"]:
            vak["slot"] = "drone" if vak["categorie"] == CAT_DRONE else "lading"

    # Wat eveuniverse niet kent (een type uit een gloednieuwe patch) halen we
    # alsnog bij naam op, anders staat er "Type 12345" in de fit.
    ontbreekt = [i for i in ids if i not in typen]
    if ontbreekt:
        for tid, naam in esi.names(ontbreekt).items():
            typen[tid] = {"naam": naam or f"Type {tid}", "groep": "",
                          "categorie": 0, "slot": "lading"}
    return typen


def _mail_vak(kop, labels, mijn):
    """In welk vak deze mail hoort.

    Zelf verstuurd gaat vóór alles: een corp-mail die jíj rondstuurde staat bij
    de ontvangers onder Corp, maar voor jou is het gewoon verzonden post. Daarna
    telt het label dat de client eraan hing.
    """
    if kop.get("from") in mijn or 2 in labels:
        return "verzonden"
    if 4 in labels:
        return "corp"
    if 8 in labels:
        return "alliance"
    return "inbox"


def mail(user, vak="alles", zoek=""):
    """Alle mail van al je characters op één hoop, ontdubbeld en doorzoekbaar.

    Dezelfde corp-mail komt in elke mailbox binnen — hier zijn dat 88 koppen
    voor 35 echte mails. Ontdubbelen dus op afzender + tijdstip + onderwerp, en
    onthouden wíe hem gekregen heeft; dat laatste is juist informatie ("dit ging
    alleen naar je main").

    De bodies halen we voor álle mails op en niet pas bij het openklappen: ze
    veranderen nooit meer en blijven dus een maand in de cache staan, en pas mét
    de tekst kun je je hele mailbox doorzoeken. Koud kost dat hier 2 seconden.
    """
    chars = esi.characters(user)
    mijn = {c.character_id: c.character_name for c in chars}
    kleuren = _kleur_per_character([{"character_id": cid} for cid in mijn])

    # De tokens hier ophalen, niet in de threads hieronder: dan raakt geen enkele
    # worker de database aan en hoeven we geen verbindingen op te ruimen.
    tokens = {cid: esi.token_for(cid, esi.MAIL_SCOPE) for cid in mijn}

    with ThreadPoolExecutor(max_workers=6) as pool:
        koppen = dict(zip(mijn, pool.map(
            lambda cid: esi.mail_headers(cid, tokens.get(cid)), list(mijn))))

    # Mailinglijsten: hun id lost /universe/names niet op, dus die namen moeten
    # van de lijst-endpoint komen.
    lijstnamen = {}
    for cid in mijn:
        for lijst in esi.mail_lists(cid, tokens.get(cid)):
            lijstnamen[lijst.get("mailing_list_id")] = lijst.get("name") or ""

    # Versturen is een aparte toestemming van lezen: wie 'm niet heeft staat er
    # niet bij als afzender.
    afzenders = [{"character_id": cid, "naam": naam} for cid, naam in mijn.items()
                 if esi.has_token([cid], esi.SEND_MAIL_SCOPE)]
    verstuurblok = {
        "afzenders": afzenders,
        "kan_versturen": bool(afzenders),
        "max_body": esi.MAIL_MAX_BODY,
        "mailinglijsten": sorted(n for n in lijstnamen.values() if n),
    }

    uniek = {}
    for cid, lijst in koppen.items():
        for kop in lijst:
            sleutel = (kop.get("from"), kop.get("timestamp"), kop.get("subject") or "")
            vakje = uniek.setdefault(sleutel, {
                "kop": kop, "bron": (cid, kop["mail_id"]),
                "chars": [], "ongelezen": [], "labels": set(),
            })
            vakje["chars"].append(cid)
            vakje["labels"].update(kop.get("labels") or [])
            if not kop.get("is_read"):
                vakje["ongelezen"].append(cid)

    if not uniek:
        return {"mails": [], "aantal": 0, "totaal": 0, "aantal_characters": len(chars),
                "ongelezen": 0, "fits_totaal": 0, "afzenders_top": [], "maanden": [],
                "per_char": [], "vakken": [], "zoek": zoek, "vak": vak,
                **verstuurblok}

    # Op tijd sorteren vóór het ophalen van de bodies: dan kappen we bij een
    # volle mailbox de oudste af in plaats van een willekeurige greep.
    op_tijd = sorted(uniek.values(),
                     key=lambda v: v["kop"].get("timestamp") or "", reverse=True)[:MAIL_MAX]

    with ThreadPoolExecutor(max_workers=8) as pool:
        bodies = list(pool.map(
            lambda v: esi.mail_body(v["bron"][0], v["bron"][1], tokens.get(v["bron"][0])),
            op_tijd))

    # Eerst alle fitting-links aftasten, dan in één keer de types opzoeken.
    fit_ids = set()
    for body in bodies:
        fit_ids |= mailtekst.fit_type_ids((body or {}).get("body") or "")
    typen = _mail_typen(fit_ids)

    # Afzenders en ontvangers: mét categorie, want die bepaalt of er een portret
    # of een corporatielogo bij hoort.
    ids = set()
    for vakje in op_tijd:
        kop = vakje["kop"]
        if kop.get("from"):
            ids.add(kop["from"])
        for ontv in kop.get("recipients") or []:
            if ontv.get("recipient_type") != "mailing_list" and ontv.get("recipient_id"):
                ids.add(ontv["recipient_id"])
    info = esi.name_info(ids) if ids else {}

    def _wie(entiteit_id, soort=""):
        gegevens = info.get(entiteit_id) or {}
        soort = soort or gegevens.get("soort") or ""
        naam = (gegevens.get("naam") or mijn.get(entiteit_id)
                or lijstnamen.get(entiteit_id) or f"#{entiteit_id}")
        return naam, soort

    mails = []
    for vakje, body in zip(op_tijd, bodies):
        kop = vakje["kop"]
        labels = vakje["labels"]
        opmaak = mailtekst.render((body or {}).get("body") or "", typen)

        afzender_id = kop.get("from")
        afzender, afzender_soort = _wie(afzender_id)
        if afzender_id in lijstnamen:
            afzender_soort = "mailing_list"

        ontvangers = []
        for ontv in kop.get("recipients") or []:
            oid, soort = ontv.get("recipient_id"), ontv.get("recipient_type") or ""
            if soort == "mailing_list":
                ontvangers.append({"naam": lijstnamen.get(oid) or f"Lijst #{oid}",
                                   "soort": soort, "id": oid})
            else:
                naam, _ = _wie(oid, soort)
                ontvangers.append({"naam": naam, "soort": soort, "id": oid})

        # Eigen labels van de gebruiker erbij: die zeggen vaak meer dan de vaste.
        eigen_labels = sorted(l for l in labels if l not in MAIL_VASTE_LABELS)

        # Veel mails openen met het onderwerp als kop in de tekst. In het
        # voorproefje staat dat dan twee keer onder elkaar, dus die eerste regel
        # slaan we over als hij het onderwerp herhaalt.
        tekst = opmaak["tekst"]
        onderwerp = kop.get("subject") or ""
        eerste, _, rest = tekst.partition("\n")
        if onderwerp and rest and eerste.strip().lower() == onderwerp.strip().lower():
            voorproef = rest.strip()
        else:
            voorproef = tekst
        # Mails met opmaak zitten vol scheidingslijnen van ═ of ─. Op één regel
        # samengeperst vult zo'n lijn het halve voorproefje met streepjes.
        voorproef = re.sub(r"[═─━=_~-]{4,}", "·", voorproef)

        mails.append({
            "id": kop["mail_id"],
            "onderwerp": kop.get("subject") or "(geen onderwerp)",
            "datum": _parse(kop.get("timestamp")),
            "afzender": afzender,
            "afzender_id": afzender_id,
            "afzender_soort": afzender_soort,
            "afzender_plaatje": (PORTRET[afzender_soort] % afzender_id
                                 if afzender_soort in PORTRET and afzender_id else ""),
            "eigen": afzender_id in mijn,
            "ontvangers": ontvangers,
            "ontvangers_kort": ", ".join(o["naam"] for o in ontvangers[:3]),
            "ontvangers_meer": max(len(ontvangers) - 3, 0),
            "vak": _mail_vak(kop, labels, mijn),
            "vak_naam": VAK_LABEL[_mail_vak(kop, labels, mijn)],
            "eigen_labels": eigen_labels,
            "ongelezen": bool(vakje["ongelezen"]),
            # Wie van jouw characters hem kreeg. Bij een corp-mail zijn dat er
            # vijf, bij een persoonlijk berichtje precies één — en dát verschil
            # is nou net wat je wil zien.
            "voor": [{"naam": mijn[cid], "character_id": cid, "kleur": kleuren[cid],
                      "ongelezen": cid in vakje["ongelezen"]}
                     for cid in sorted(set(vakje["chars"]), key=lambda c: mijn[c])],
            "html": opmaak["html"],
            "tekst": tekst,
            "kort": (voorproef[:MAIL_KORT] + "…") if len(voorproef) > MAIL_KORT else voorproef,
            "leeg": not tekst.strip(),
            "fits": opmaak["fits"],
            "zoektekst": f"{kop.get('subject') or ''}\n{afzender}\n{tekst}".lower(),
        })

    # ── Tellingen over álles, dus vóór het filteren ────────────────────────
    tellers = Counter(m["vak"] for m in mails)
    ongelezen = sum(1 for m in mails if m["ongelezen"])
    fits_totaal = sum(len(m["fits"]) for m in mails)

    vakken = [{"sleutel": "alles", "naam": "Alles", "aantal": len(mails)}]
    for sleutel in ("inbox", "corp", "alliance", "verzonden"):
        if tellers.get(sleutel):
            vakken.append({"sleutel": sleutel, "naam": VAK_LABEL[sleutel],
                           "aantal": tellers[sleutel]})
    if ongelezen:
        vakken.append({"sleutel": "ongelezen", "naam": "Ongelezen", "aantal": ongelezen})
    for v in vakken:
        v["actief"] = v["sleutel"] == vak

    # ── Afzenders, maanden en characters (over alles, niet over de selectie) ─
    per_afzender = {}
    for m in mails:
        if m["eigen"]:
            continue                    # jezelf in "wie mailt jou" is ruis
        gegevens = per_afzender.setdefault(m["afzender_id"], {
            "naam": m["afzender"], "plaatje": m["afzender_plaatje"],
            "aantal": 0, "laatste": None, "soort": m["afzender_soort"]})
        gegevens["aantal"] += 1
        if m["datum"] and (not gegevens["laatste"] or m["datum"] > gegevens["laatste"]):
            gegevens["laatste"] = m["datum"]
    # `afzenders_top` en niet `afzenders`: dat tweede is in dit tabblad de lijst
    # characters waarmee je zélf mag versturen, en die twee door elkaar halen
    # levert een pagina op waar de verkeerde namen in het formulier staan.
    afzenders_top = sorted(per_afzender.values(), key=lambda a: -a["aantal"])[:10]
    hoogste = max((a["aantal"] for a in afzenders_top), default=0) or 1
    for a in afzenders_top:
        a["pct"] = round(a["aantal"] / hoogste * 100)

    # Per maand, en **gevensterd op de mail zelf** — niet op de laatste twaalf
    # maanden vanaf vandaag. Een mailbox loopt hier van 2021 tot nu maar staat
    # het grootste deel van die tijd stil; een vaste jaarreeks zou dus tien lege
    # kolommen tonen en de twee maanden waar het gebeurde platdrukken. Dezelfde
    # les als bij de mining-grafiek. De tijdas is daardoor niet aaneengesloten,
    # dus staat eronder hoeveel maanden er mail hebben.
    per_maand = Counter((m["datum"].year, m["datum"].month) for m in mails if m["datum"])
    reeks = sorted(per_maand)[-12:]
    top = max((per_maand[s] for s in reeks), default=0) or 1
    maanden = [{"label": f"{MAAND_KORT[m]} {str(j)[2:]}",
                "aantal": per_maand[(j, m)],
                "pct": round(per_maand[(j, m)] / top * 100)}
               for j, m in reeks]

    per_char = []
    for cid, naam in mijn.items():
        eigen = [m for m in mails if any(v["character_id"] == cid for v in m["voor"])]
        laatste = max((m["datum"] for m in eigen if m["datum"]), default=None)
        per_char.append({
            "character_id": cid, "naam": naam, "kleur": kleuren[cid],
            "aantal": len(eigen),
            "ongelezen": sum(1 for m in eigen
                             if any(v["character_id"] == cid and v["ongelezen"]
                                    for v in m["voor"])),
            "laatste": laatste,
            "gekoppeld": bool(tokens.get(cid)),
        })
    per_char.sort(key=lambda c: -c["aantal"])

    # ── Filter en zoek ────────────────────────────────────────────────────
    getoond = mails
    if vak == "ongelezen":
        getoond = [m for m in getoond if m["ongelezen"]]
    elif vak in VAK_LABEL:
        getoond = [m for m in getoond if m["vak"] == vak]
    zoek = (zoek or "").strip()
    if zoek:
        naald = zoek.lower()
        getoond = [m for m in getoond if naald in m["zoektekst"]]

    return {
        "mails": getoond,
        "aantal": len(getoond),
        "totaal": len(mails),
        "koppen_totaal": sum(len(v) for v in koppen.values()),
        "gefilterd": len(getoond) != len(mails),
        "aantal_characters": len(chars),
        "ongelezen": ongelezen,
        "ontvangen": (tellers.get("inbox", 0) + tellers.get("corp", 0)
                      + tellers.get("alliance", 0)),
        "verzonden": tellers.get("verzonden", 0),
        "fits_totaal": fits_totaal,
        "afzenders_top": afzenders_top,
        "maanden": maanden,
        **verstuurblok,
        "per_char": per_char,
        "vakken": vakken,
        "vak": vak,
        "zoek": zoek,
        "oudste": min((m["datum"] for m in mails if m["datum"]), default=None),
        "nieuwste": max((m["datum"] for m in mails if m["datum"]), default=None),
    }


# --------------------------------------------------------------------------
# Markt
# --------------------------------------------------------------------------

REF_BROKER = "brokers_fee"
REF_TAX = "transaction_tax"

ORDER_SPOED_UREN = 72       # binnen drie dagen weg: dan wil je het weten
MARKT_ITEMS = 25            # zoveel artikelen in de handelstabel
HISTORIE_DAGEN = 90         # zover reikt ESI's orderhistorie terug


def _marktpositie(order, boek, mijn_order_ids, systeem=None):
    """Waar deze order in het boek staat, en wie er vóór ligt.

    We vergelijken binnen **hetzelfde systeem** (of dezelfde structuur), niet
    binnen de hele regio. Kopen kan alleen waar de order ligt, dus een order in
    een andere regio is geen concurrent. Maar Jita heeft meerdere stations, en
    daar wíl je juist weten dat je 30k boven de goedkoopste in het systeem zit —
    dat is precies waarom je spullen blijven liggen.

    Onze eigen orders vallen af op order-id: die staan gewoon in het publieke
    boek en zouden zichzelf anders als concurrent tellen.
    """
    koop = bool(order.get("is_buy_order"))
    prijs = float(order.get("price") or 0)
    anderen = [b for b in boek
               if b["koop"] == koop and b["order_id"] not in mijn_order_ids
               and (systeem is None or b.get("systeem") == systeem)]
    if not anderen:
        return {"rang": 1, "totaal": 1, "concurrenten": 0, "beste": 0.0,
                "beste_locatie": None, "positie": "alleen"}

    if koop:
        beter = [b for b in anderen if b["prijs"] > prijs]
        beste = max(anderen, key=lambda b: b["prijs"])
    else:
        beter = [b for b in anderen if b["prijs"] < prijs]
        beste = min(anderen, key=lambda b: b["prijs"])
    return {
        "rang": len(beter) + 1,
        # Het totaal telt onze eigen order mee: "rang 3 van 2" is onzin, want de
        # rang komt uit een rij waar wij zelf ook in staan.
        "totaal": len(anderen) + 1,
        "concurrenten": len(anderen),
        "beste": beste["prijs"],
        "beste_locatie": beste.get("locatie"),
        "positie": "beste" if not beter else "onderboden",
    }


def markt(user):
    """Je marktorders, hoe ze ervoor staan, en wat er de laatste 90 dagen liep.

    Het punt van dit tabblad is de vergelijking: een lijst met je eigen orders
    staat ook in het spel, maar dáár zie je pas dat je onderboden bent als je
    elk artikel apart opzoekt. Hier staat het bij elke order.
    """
    chars = esi.characters(user)
    mijn = {c.character_id: c.character_name for c in chars}
    kleuren = _kleur_per_character([{"character_id": cid} for cid in mijn])
    tokens = {cid: esi.token_for(cid, esi.ORDERS_SCOPE) for cid in mijn}
    nu = datetime.now(timezone.utc)

    # Tokens vooraf, dan hoeven de threads niet bij de database te zijn.
    with ThreadPoolExecutor(max_workers=6) as pool:
        open_per_char = dict(zip(mijn, pool.map(
            lambda cid: esi.orders(cid, tokens.get(cid)), list(mijn))))
        hist_per_char = dict(zip(mijn, pool.map(
            lambda cid: esi.order_history(cid, tokens.get(cid)), list(mijn))))

    open_orders = [{**o, "_char": cid}
                   for cid, lijst in open_per_char.items() for o in lijst]
    historie = [{**h, "_char": cid}
                for cid, lijst in hist_per_char.items() for h in lijst]
    mijn_order_ids = {o.get("order_id") for o in open_orders}

    # ── De boeken van de markten waar iets ligt ───────────────────────────
    # Spelersstructuren zitten niet in de regio-orders; die hebben een eigen
    # endpoint en een token met dockingrechten.
    stations = {o["location_id"] for o in open_orders if o["location_id"] < 100_000_000}
    structuren = {o["location_id"] for o in open_orders if o["location_id"] >= 100_000_000}
    paren = sorted({(o["region_id"], o["type_id"]) for o in open_orders
                    if o["location_id"] in stations})

    regioboeken, structuurboeken = {}, {}
    if paren or structuren:
        with ThreadPoolExecutor(max_workers=8) as pool:
            regioboeken = dict(zip(paren, pool.map(
                lambda p: esi.markt_regio(p[0], p[1]), paren)))
            structuurlijst = sorted(structuren)
            structuurboeken = dict(zip(structuurlijst, pool.map(
                lambda sid: esi.markt_structuur(sid, list(mijn)), structuurlijst)))

    # ── Positie per order bepalen ─────────────────────────────────────────
    posities, nodig = {}, set(stations)
    for o in open_orders:
        loc = o["location_id"]
        if loc in structuren:
            boek = (structuurboeken.get(loc) or {}).get(o["type_id"], [])
            bekend = bool(structuurboeken.get(loc))
            pos = _marktpositie(o, boek, mijn_order_ids)
        else:
            boek = regioboeken.get((o["region_id"], o["type_id"]), [])
            bekend = bool(boek)
            # Ons eigen systeem staat in het publieke boek bij onze eigen order;
            # alleen als die er niet in staat moeten we het station opzoeken.
            eigen = next((b for b in boek if b["order_id"] == o.get("order_id")), None)
            systeem = eigen["systeem"] if eigen else esi.station_systeem(loc)
            pos = _marktpositie(o, boek, mijn_order_ids, systeem)
        if not bekend:
            pos = {**pos, "positie": "onbekend"}
        posities[o["order_id"]] = pos
        if pos.get("beste_locatie"):
            nodig.add(pos["beste_locatie"])

    # Namen: stations via /universe/names, structuren via hun eigen endpoint.
    st_ids = {i for i in nodig if i < 100_000_000}
    str_ids = ({i for i in nodig if i >= 100_000_000} | structuren)
    locatienamen = esi.names(st_ids) if st_ids else {}
    if str_ids:
        locatienamen.update(esi.structure_names(str_ids, list(mijn)))

    typen = _type_info({o["type_id"] for o in open_orders}
                       | {h["type_id"] for h in historie})

    def _plek(loc_id):
        return locatienamen.get(loc_id) or (f"#{loc_id}" if loc_id else "")

    # Kort label per locatie. Normaal is het systeem genoeg ('Jita IV'), maar
    # Jita heeft meerdere stations en dan staan er twee regels 'Jita IV' onder
    # elkaar — terwijl het verschil tussen die twee juist de reden is dat je
    # spullen blijven liggen. Alleen bij zo'n botsing komt de stationsnaam erbij.
    def _plek_kort(naam):
        return naam.split(" - ")[0] if naam else ""

    kop_teller = Counter(_plek_kort(n) for n in
                         {i: _plek(i) for i in nodig | structuren}.values() if n)

    def _label(loc_id):
        naam = _plek(loc_id)
        if not naam:
            return ""
        kort = _plek_kort(naam)
        if kop_teller.get(kort, 0) < 2 or " - " not in naam:
            return kort
        staart = naam.split(" - ")[-1]
        if len(staart) > 22:
            staart = staart[:21] + "…"
        return f"{kort} · {staart}"

    # ── De orders zelf ────────────────────────────────────────────────────
    rijen, verkoopwaarde, escrow_totaal = [], 0.0, 0.0
    for o in open_orders:
        koop = bool(o.get("is_buy_order"))
        prijs = float(o.get("price") or 0)
        rest_aantal = int(o.get("volume_remain") or 0)
        totaal_aantal = int(o.get("volume_total") or 0)
        waarde = prijs * rest_aantal
        if koop:
            escrow_totaal += float(o.get("escrow") or 0)
        else:
            verkoopwaarde += waarde

        pos = posities[o["order_id"]]
        verschil = (pos["beste"] - prijs) if koop else (prijs - pos["beste"])
        verschil_pct = (verschil / pos["beste"] * 100) if pos["beste"] else 0.0

        verloopt = _parse(o.get("issued"))
        seconden = None
        if verloopt:
            verloopt += timedelta(days=int(o.get("duration") or 0))
            seconden = (verloopt - nu).total_seconds()

        info = typen.get(o["type_id"], {})
        plek = _plek(o["location_id"])
        beste_plek = _plek(pos["beste_locatie"]) if pos.get("beste_locatie") else ""
        rijen.append({
            "type_id": o["type_id"],
            "naam": info.get("naam") or f"Type {o['type_id']}",
            "plaatje": info.get("plaatje") or "",
            "koop": koop,
            "prijs": prijs, "prijs_fmt": fmt_isk_vol(prijs),
            "aantal": rest_aantal, "totaal": totaal_aantal,
            "aantal_fmt": _getal(rest_aantal), "totaal_fmt": _getal(totaal_aantal),
            # Hoeveel er al weg is: bij een order van 100 waarvan er 3 over zijn
            # is "3" alleen niet het hele verhaal.
            "gevuld_pct": round((totaal_aantal - rest_aantal) / totaal_aantal * 100)
                          if totaal_aantal else 0,
            "waarde": waarde, "waarde_fmt": fmt_isk(waarde),
            "escrow_fmt": fmt_isk(float(o.get("escrow") or 0)),
            "locatie": plek, "locatie_kort": _label(o["location_id"]),
            "structuur": o["location_id"] in structuren,
            "rest_fmt": _duur(seconden) if seconden and seconden > 0 else "",
            "spoed": bool(seconden and 0 < seconden < ORDER_SPOED_UREN * 3600),
            "rang": pos["rang"], "totaal": pos["totaal"],
            "concurrenten": pos["concurrenten"],
            "positie": pos["positie"],
            "beste_fmt": fmt_isk_vol(pos["beste"]) if pos["beste"] else "",
            "beste_plek": _label(pos["beste_locatie"]) if pos.get("beste_locatie") else "",
            "elders": bool(beste_plek) and beste_plek != plek,
            # Bij een verkooporder zit je bóven de beste prijs, bij een kooporder
            # eronder. Het teken zegt dus welke kant je ernaast zit.
            "verschil_teken": "−" if koop else "+",
            "verschil_fmt": fmt_isk(abs(verschil)),
            "verschil_pct": abs(verschil_pct),
            "verschil_pct_fmt": _nl(f"{abs(verschil_pct):,.1f}"),
            "char": mijn[o["_char"]], "kleur": kleuren[o["_char"]],
        })

    # Onderboden bovenaan: dat is waar je iets aan moet doen. Daarbinnen het
    # grootste bedrag eerst, want daar hangt het meeste geld aan vast.
    volgorde = {"onderboden": 0, "onbekend": 1, "beste": 2, "alleen": 3}
    rijen.sort(key=lambda r: (volgorde.get(r["positie"], 9), -r["waarde"]))

    onderboden = [r for r in rijen if r["positie"] == "onderboden"]
    bijna_weg = sorted((r for r in rijen if r["spoed"]), key=lambda r: -r["waarde"])

    # ── Per markt ─────────────────────────────────────────────────────────
    per_markt = {}
    for r in rijen:
        vak = per_markt.setdefault(r["locatie"], {
            "naam": r["locatie"], "kort": r["locatie_kort"] or r["locatie"],
            "structuur": r["structuur"], "aantal": 0, "waarde": 0.0,
            "onderboden": 0})
        vak["aantal"] += 1
        vak["waarde"] += r["waarde"]
        if r["positie"] == "onderboden":
            vak["onderboden"] += 1
    markten = sorted(per_markt.values(), key=lambda m: -m["waarde"])
    hoogste = max((m["waarde"] for m in markten), default=0) or 1
    for m in markten:
        m["waarde_fmt"] = fmt_isk(m["waarde"])
        m["pct"] = round(m["waarde"] / hoogste * 100)

    # ── Historie: wat er de laatste 90 dagen doorheen ging ────────────────
    # Een volledig gevulde order krijgt state `expired` met volume_remain 0 —
    # er bestaat geen status "verkocht". Alleen een order die met spullen erin
    # afloopt is écht verlopen, en dat is precies het verschil dat je wil zien.
    volledig = sum(1 for h in historie if not h.get("volume_remain"))
    ingetrokken = sum(1 for h in historie if h.get("state") == "cancelled")
    blijven_liggen = sum(1 for h in historie
                         if h.get("state") == "expired" and h.get("volume_remain"))

    per_type = {}
    omzet = inkoop = 0.0
    for h in historie:
        gevuld = int(h.get("volume_total") or 0) - int(h.get("volume_remain") or 0)
        if gevuld <= 0:
            continue
        isk = gevuld * float(h.get("price") or 0)
        vak = per_type.setdefault(h["type_id"], {
            "type_id": h["type_id"], "verkocht": 0, "gekocht": 0,
            "omzet": 0.0, "inkoop": 0.0})
        if h.get("is_buy_order"):
            vak["gekocht"] += gevuld
            vak["inkoop"] += isk
            inkoop += isk
        else:
            vak["verkocht"] += gevuld
            vak["omzet"] += isk
            omzet += isk

    items = []
    for vak in per_type.values():
        gem_verkoop = vak["omzet"] / vak["verkocht"] if vak["verkocht"] else 0.0
        gem_inkoop = vak["inkoop"] / vak["gekocht"] if vak["gekocht"] else 0.0
        # Resultaat alleen waar je het artikel écht allebei deed. Verkocht je
        # spullen die je niet in deze periode kocht (loot, gemijnd erts), dan is
        # de "winst" het hele verkoopbedrag en dat zegt niets over handelen.
        beide = bool(vak["verkocht"] and vak["gekocht"])
        stuks = min(vak["verkocht"], vak["gekocht"]) if beide else 0
        resultaat = (gem_verkoop - gem_inkoop) * stuks
        info = typen.get(vak["type_id"], {})
        items.append({
            **vak,
            "naam": info.get("naam") or f"Type {vak['type_id']}",
            "plaatje": info.get("plaatje") or "",
            "verkocht_fmt": _getal(vak["verkocht"]), "gekocht_fmt": _getal(vak["gekocht"]),
            "omzet_fmt": fmt_isk(vak["omzet"]), "inkoop_fmt": fmt_isk(vak["inkoop"]),
            "gem_verkoop_fmt": fmt_isk(gem_verkoop), "gem_inkoop_fmt": fmt_isk(gem_inkoop),
            "marge_pct": ((gem_verkoop - gem_inkoop) / gem_inkoop * 100) if beide and gem_inkoop else 0.0,
            "beide": beide,
            "resultaat": resultaat, "resultaat_fmt": fmt_isk(resultaat),
            "stuks": stuks,
        })
    handelsitems = sorted((i for i in items if i["beide"]),
                          key=lambda i: -i["resultaat"])[:MARKT_ITEMS]
    for i in handelsitems:
        i["marge_pct_fmt"] = _nl(f"{i['marge_pct']:,.1f}")
    # Wat je verkocht zonder het gekocht te hebben: loot, erts, eigen productie.
    eigen_waar = sorted((i for i in items if i["verkocht"] and not i["gekocht"]),
                        key=lambda i: -i["omzet"])[:10]
    handelsresultaat = sum(i["resultaat"] for i in items if i["beide"])

    # ── Broker fee en sales tax ───────────────────────────────────────────
    # Die staan niet in de orders maar in het journaal. Het journaal reikt maar
    # zover als we ophalen, dus we zeggen erbij over hoeveel dagen het gaat —
    # anders lijkt het bedrag over 90 dagen te gaan terwijl het er 20 zijn.
    kosten, oudste_journaal = 0.0, None
    for c in chars:
        for e in esi.journal(c.character_id):
            datum = _parse(e.get("date"))
            if not datum:
                continue
            if oudste_journaal is None or datum < oudste_journaal:
                oudste_journaal = datum
            if e.get("ref_type") in (REF_BROKER, REF_TAX):
                kosten += abs(float(e.get("amount") or 0))
    kosten_dagen = (nu - oudste_journaal).days if oudste_journaal else 0

    per_char = []
    for cid, naam in mijn.items():
        eigen_orders = [r for r in rijen if r["char"] == naam]
        eigen_hist = [h for h in historie if h["_char"] == cid]
        per_char.append({
            "character_id": cid, "naam": naam, "kleur": kleuren[cid],
            "aantal": len(eigen_orders),
            "waarde_fmt": fmt_isk(sum(r["waarde"] for r in eigen_orders if not r["koop"])),
            "onderboden": sum(1 for r in eigen_orders if r["positie"] == "onderboden"),
            "historie": len(eigen_hist),
            "gekoppeld": bool(tokens.get(cid)),
        })
    per_char.sort(key=lambda c: (-c["aantal"], -c["historie"]))

    return {
        "rijen": rijen,
        "aantal_open": len(rijen),
        "koop_aantal": sum(1 for r in rijen if r["koop"]),
        "verkoop_aantal": sum(1 for r in rijen if not r["koop"]),
        "verkoopwaarde_fmt": fmt_isk(verkoopwaarde),
        "escrow_fmt": fmt_isk(escrow_totaal),
        "onderboden": onderboden,
        "onderboden_aantal": len(onderboden),
        "onderboden_waarde_fmt": fmt_isk(sum(r["waarde"] for r in onderboden)),
        "beste_aantal": sum(1 for r in rijen if r["positie"] == "beste"),
        "bijna_weg": bijna_weg,
        "markten": markten,
        "historie_aantal": len(historie),
        "historie_dagen": HISTORIE_DAGEN,
        "volledig": volledig,
        "volledig_pct": round(volledig / len(historie) * 100) if historie else 0,
        "ingetrokken": ingetrokken,
        "blijven_liggen": blijven_liggen,
        "omzet_fmt": fmt_isk(omzet),
        "inkoop_fmt": fmt_isk(inkoop),
        "handelsitems": handelsitems,
        "eigen_waar": eigen_waar,
        "handelsresultaat": handelsresultaat,
        "handelsresultaat_fmt": fmt_isk(handelsresultaat),
        "kosten_fmt": fmt_isk(kosten),
        "kosten_dagen": kosten_dagen,
        "per_char": per_char,
        "aantal_characters": len(chars),
    }


def _ontvangers_uit_tekst(tekst):
    """'Jan, Piet; Klaas' → ['Jan', 'Piet', 'Klaas'].

    Komma, puntkomma én regeleinde als scheiding: mensen plakken een lijstje uit
    van alles, en een naam met een komma erin bestaat in EVE niet.
    """
    ruw = re.split(r"[,;\n]+", tekst or "")
    return [n.strip() for n in ruw if n.strip()]


def verstuur_mail(user, gegevens):
    """Een mail versturen namens een van je characters.

    Geeft {"ok", "fout", "formulier", "aantal"} terug in plaats van een
    uitzondering: de aanroeper is een formulier en die wil een zin die je aan de
    gebruiker kunt tonen, mét wat er ingevuld stond zodat je niet opnieuw hoeft
    te typen.
    """
    from django.core.cache import cache

    chars = esi.characters(user)
    mijn = {c.character_id: c.character_name for c in chars}
    formulier = {
        "afzender": (gegevens.get("afzender") or "").strip(),
        "aan": (gegevens.get("aan") or "").strip(),
        "onderwerp": (gegevens.get("onderwerp") or "").strip(),
        "tekst": gegevens.get("tekst") or "",
    }

    def _fout(bericht):
        return {"ok": False, "fout": bericht, "formulier": formulier, "aantal": 0}

    try:
        afzender_id = int(formulier["afzender"])
    except (TypeError, ValueError):
        return _fout("Kies een character om mee te versturen.")
    # Alleen je eigen characters: het id komt uit een formulier, dus het kan er
    # ook eentje zijn die iemand er zelf in gezet heeft.
    if afzender_id not in mijn:
        return _fout("Dat character is niet van jou.")
    if not esi.has_token([afzender_id], esi.SEND_MAIL_SCOPE):
        return _fout(f"{mijn[afzender_id]} mag geen mail versturen. Koppel het "
                     "character opnieuw, dan wordt die toestemming gevraagd.")

    namen = _ontvangers_uit_tekst(formulier["aan"])
    if not namen:
        return _fout("Vul minstens één ontvanger in.")

    # Eerst wat we zelf al weten: je eigen characters en je mailinglijsten. Die
    # laatste kent /universe/ids niet eens, dus zonder dit kun je nooit naar een
    # lijst mailen.
    eigen = {naam.lower(): cid for cid, naam in mijn.items()}
    lijsten = {}
    for cid in mijn:
        for lijst in esi.mail_lists(cid):
            if lijst.get("name"):
                lijsten[lijst["name"].lower()] = lijst.get("mailing_list_id")

    ontvangers, rest, getoond = [], [], []
    for naam in namen:
        sleutel = naam.lower()
        if sleutel in lijsten:
            ontvangers.append(("mailing_list", lijsten[sleutel]))
            getoond.append(naam)
        elif sleutel in eigen:
            ontvangers.append(("character", eigen[sleutel]))
            getoond.append(mijn[eigen[sleutel]])
        else:
            rest.append(naam)

    if rest:
        gevonden = esi.zoek_ids(rest)
        onbekend = []
        for naam in rest:
            vak = gevonden.get(naam.lower())
            if vak and vak.get("id"):
                ontvangers.append((vak["soort"], vak["id"]))
                getoond.append(vak["naam"])
            else:
                onbekend.append(naam)
        if onbekend:
            # Niet half versturen: dan gaat de mail wél weg maar mist er iemand.
            return _fout("Niet gevonden in EVE: " + ", ".join(onbekend)
                         + ". Namen moeten precies kloppen (hoofdletters mogen "
                           "wel schelen).")

    # Dubbele ontvangers eruit, met behoud van volgorde: ESI weigert de mail als
    # dezelfde ontvanger er twee keer in staat.
    uniek, gezien = [], set()
    for paar in ontvangers:
        if paar not in gezien:
            gezien.add(paar)
            uniek.append(paar)

    if not formulier["onderwerp"]:
        return _fout("Vul een onderwerp in.")
    if not formulier["tekst"].strip():
        return _fout("De mail is leeg.")

    # EVE's mailbody is opmaak, geen platte tekst: zonder deze omzetting komt
    # alles als één lange regel aan. Escapen omdat een < in je tekst anders als
    # opmaak gelezen wordt.
    inhoud = escape(formulier["tekst"], quote=False).replace("\r\n", "\n").replace("\n", "<br>")

    mail_id, fout = esi.stuur_mail(afzender_id, uniek, formulier["onderwerp"], inhoud)
    if fout:
        return _fout(fout)

    # De koppen van de afzender opnieuw ophalen, anders staat de mail die je net
    # verstuurde er tien minuten lang niet bij en lijkt het mislukt.
    cache.delete(f"fin_mailkop_{afzender_id}")
    return {"ok": True, "fout": "", "formulier": None, "aantal": len(uniek),
            "ontvangers": getoond, "mail_id": mail_id,
            "afzender": mijn[afzender_id]}



# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------
#
# Dit is de startpagina van dutchlegionsdashboard.eu, nagebouwd. Niet "in de
# geest van", maar de blokken zelf: `eve-dashboard/src/pages/Dashboard.tsx` en
# de componenten WalletChart, RattingWidget, MiningWidget en KillsTable. Waar de
# site een getal op een bepaalde manier opmaakt of een grens hanteert, doen we
# dat hier hetzelfde — anders staat dezelfde naam op twee plekken met een ander
# getal eronder. Vandaar ook `fmt_site()` naast onze eigen `fmt_isk()`: de site
# schrijft 3.21B en niet 3,21 mld.

REF_ICONS = {
    "market_transaction": "◊", "contract_price": "◧", "industry_job_tax": "◫",
    "bounty_prizes": "◉", "agent_mission_reward": "◎", "manufacturing": "◫",
    "player_trading": "◊", "contract_reward": "◧", "skill_purchase": "◎",
}

INCOME_CATS = {
    "market_transaction": "Market", "market_escrow_refund": "Market",
    "transaction_tax": "Market",
    "bounty_prizes": "Ratting", "bounty_prize": "Ratting",
    "ess_escrow_transfer": "Ratting", "security_funds_redistribution": "Ratting",
    "contract_price": "Contracten", "contract_reward": "Contracten",
    "contract_deposit_refund": "Contracten",
    "industry_job_tax": "Industry", "manufacturing": "Industry",
    "mining_income": "Mining",
    "agent_mission_reward": "Missions", "agent_mission_time_bonus_reward": "Missions",
}
INCOME_COLORS = {
    "Market": "#3ecf6e", "Ratting": "#e05555", "Contracten": "#00b4d8",
    "Industry": "#a78bfa", "Mining": "#f0c040", "Missions": "#f97316",
}

OWNER_COLOR = {"eve_server": "#3ecf6e", "corporation": "#f0c040",
               "alliance": "#00b4d8", "character": "#a78bfa", "faction": "#f97316"}
OWNER_LABEL = {"eve_server": "EVE", "corporation": "Corp", "alliance": "Alliance",
               "character": "Persoonlijk", "faction": "Factie"}
RSVP_COLOR = {"accepted": "#3ecf6e", "declined": "#e05555",
              "tentative": "#f0c040", "not_responded": "#1c1c35"}
RSVP_LABEL = {"accepted": "Ja", "declined": "Nee", "tentative": "?",
              "not_responded": "—"}

ACTIVITEIT = {1: "Bouwen", 3: "TE-research", 4: "ME-research", 5: "Kopiëren",
              7: "Reverse engineering", 8: "Invention", 9: "Reacties"}
DAG_KORT = ["ma", "di", "wo", "do", "vr", "za", "zo"]


def fmt_site(waarde):
    """1234567890 → '1.23B'. De opmaak van de site, letterlijk.

    Het minteken is U+2212 en niet een gewone streep, precies zoals `fmtISK` in
    `Dashboard.tsx`.
    """
    try:
        waarde = float(waarde or 0)
    except (TypeError, ValueError):
        return "0"
    teken = "−" if waarde < 0 else ""
    a = abs(waarde)
    if a >= 1e9:
        return f"{teken}{a / 1e9:.2f}B"
    if a >= 1e6:
        return f"{teken}{a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{teken}{a / 1e3:.0f}K"
    return f"{teken}{a:.0f}"


def _over(seconden):
    """Resterende tijd zoals `timeLeft()`: '2d 4u', '3u 12m', '45m', 'Klaar'."""
    if seconden is None or seconden <= 0:
        return "Klaar"
    d, rest = divmod(int(seconden), 86400)
    u, rest = divmod(rest, 3600)
    m = rest // 60
    if d:
        return f"{d}d {u}u"
    if u:
        return f"{u}u {m}m"
    return f"{m}m"


def _geleden(moment, nu):
    """'12m' / '3u' / '5d', zoals de kolom rechts in RECENTE TRANSACTIES."""
    if not moment:
        return ""
    s = (nu - moment).total_seconds()
    if s < 3600:
        return f"{int(s // 60)}m"
    if s < 86400:
        return f"{int(s // 3600)}u"
    return f"{int(s // 86400)}d"


def _op_dag(journal, dagen_terug=0):
    """Som van de **positieve** journaalregels van die dag — `dayEarnings()`."""
    dag = (datetime.now(timezone.utc) - timedelta(days=dagen_terug)).date().isoformat()
    return sum(float(e.get("amount") or 0) for e in journal
               if (e.get("date") or "").startswith(dag) and float(e.get("amount") or 0) > 0)


def _voortgang(start, eind, nu):
    """Hoe ver een skill of job is, in procenten."""
    if not start or not eind or eind <= start:
        return 0
    return max(0, min(100, round((nu - start).total_seconds()
                                 / (eind - start).total_seconds() * 100)))


def dashboard(user):
    """De startpagina van de site, met jouw gegevens uit ESI."""
    chars = esi.characters(user)
    if not chars:
        return {"leeg": True}
    mijn = {c.character_id: c for c in chars}
    kleuren = _kleur_per_character([{"character_id": cid} for cid in mijn])
    nu = datetime.now(timezone.utc)

    def _alles(cid):
        return {
            "saldo": esi.balance(cid), "journal": esi.journal(cid),
            "orders": esi.orders(cid), "locatie": esi.locatie(cid),
            "schip": esi.schip(cid), "online": esi.online(cid),
            "queue": esi.skillqueue(cid), "jobs": esi.industry_jobs(cid),
            "agenda": esi.agenda(cid), "zkill": esi.zkill_stats(cid),
            "mining": esi.mining(cid),
        }

    with ThreadPoolExecutor(max_workers=6) as pool:
        per_char = dict(zip(mijn, pool.map(_alles, list(mijn))))

    # ── Namen ─────────────────────────────────────────────────────────────
    systeem_ids, type_ids = set(), set()
    for vak in per_char.values():
        systeem_ids.add(vak["locatie"].get("solar_system_id"))
        type_ids.add(vak["schip"].get("ship_type_id"))
        type_ids.update(q.get("skill_id") for q in vak["queue"])
        type_ids.update(j.get("product_type_id") or j.get("blueprint_type_id")
                        for j in vak["jobs"])
        type_ids.update(e.get("type_id") for e in vak["mining"])
    systeem_ids.discard(None)
    type_ids.discard(None)
    systeemnamen = esi.names(systeem_ids) if systeem_ids else {}
    typen = _type_info(type_ids)

    def _tnaam(tid):
        return (typen.get(tid) or {}).get("naam") or (f"Type {tid}" if tid else "")

    # ── Banner en character-kaarten ───────────────────────────────────────
    kaarten, totaal_wallet, vandaag, gisteren = [], 0.0, 0.0, 0.0
    for cid, c in mijn.items():
        vak = per_char[cid]
        saldo = float(vak["saldo"] or 0)
        totaal_wallet += saldo
        dag = _op_dag(vak["journal"])
        vandaag += dag
        gisteren += _op_dag(vak["journal"], 1)
        kaarten.append({
            "character_id": cid, "naam": c.character_name, "kleur": kleuren[cid],
            "isk": fmt_site(saldo), "vandaag": dag, "vandaag_fmt": fmt_site(dag),
            "systeem": systeemnamen.get(vak["locatie"].get("solar_system_id"), ""),
            "schip": vak["schip"].get("ship_name") or "",
            "schip_type_id": vak["schip"].get("ship_type_id") or 0,
            "online": bool(vak["online"].get("online")),
        })

    main = chars[0]
    mv = per_char[main.character_id]
    banner = {
        "character_id": main.character_id, "naam": main.character_name,
        "corp": main.corporation_name or "", "corp_ticker": main.corporation_ticker or "",
        "corp_id": main.corporation_id or 0,
        "alliance": main.alliance_name or "", "alliance_ticker": main.alliance_ticker or "",
        "alliance_id": main.alliance_id or 0,
        "systeem": systeemnamen.get(mv["locatie"].get("solar_system_id"), ""),
        "schip": mv["schip"].get("ship_name") or "",
        "schip_type": _tnaam(mv["schip"].get("ship_type_id")),
        "schip_type_id": mv["schip"].get("ship_type_id") or 0,
        "online": bool(mv["online"].get("online")),
    }

    alle_orders = [o for vak in per_char.values() for o in vak["orders"]]
    alle_jobs = [{**j, "_char": mijn[cid].character_name}
                 for cid, vak in per_char.items() for j in vak["jobs"]]
    alle_queue = [q for cid in mijn for q in per_char[cid]["queue"]]     # main eerst
    alle_journal = [{**e, "_char": mijn[cid].character_name, "_kleur": kleuren[cid]}
                    for cid, vak in per_char.items() for e in vak["journal"]]
    alle_journal.sort(key=lambda e: e.get("date") or "", reverse=True)

    # ── Widget: SKILL QUEUE ───────────────────────────────────────────────
    # De site pakt de eerste skill die nu loopt uit de samengevoegde lijst; die
    # staat vooraan omdat de main vooraan staat.
    actief = None
    for q in alle_queue:
        start, eind = _parse(q.get("start_date")), _parse(q.get("finish_date"))
        if start and eind and start <= nu < eind:
            actief = {"naam": _tnaam(q.get("skill_id")), "level": q.get("finished_level") or 0,
                      "rest": _over((eind - nu).total_seconds()),
                      "pct": _voortgang(start, eind, nu)}
            break
    laatste = max((_parse(q.get("finish_date")) for q in alle_queue
                   if q.get("finish_date")), default=None)
    skillblok = {
        "actief": actief, "aantal": len(alle_queue),
        "leeg_over": _over((laatste - nu).total_seconds()) if laatste else "",
    }

    # ── Widget: INDUSTRY JOBS ─────────────────────────────────────────────
    klaar = [j for j in alle_jobs if j.get("status") == "ready"]
    bezig = sorted((j for j in alle_jobs if j.get("status") == "active"),
                   key=lambda j: j.get("end_date") or "")[:4]
    jobregels = []
    for j in bezig:
        start, eind = _parse(j.get("start_date")), _parse(j.get("end_date"))
        rest = (eind - nu).total_seconds() if eind else 0
        jobregels.append({
            "naam": _tnaam(j.get("product_type_id") or j.get("blueprint_type_id")) or "Onbekend",
            "runs": j.get("runs") or 0, "rest": _over(rest),
            "spoed": 0 < rest < 3600,           # binnen een uur: goud, zoals op de site
            "pct": _voortgang(start, eind, nu),
        })
    industrieblok = {"klaar": len(klaar), "regels": jobregels,
                     "actief": sum(1 for j in alle_jobs if j.get("status") == "active")}

    # ── Widget: MARKET ORDERS ─────────────────────────────────────────────
    verkoop = [o for o in alle_orders if not o.get("is_buy_order")]
    koop = [o for o in alle_orders if o.get("is_buy_order")]
    verkoop_isk = sum(float(o.get("price") or 0) * int(o.get("volume_remain") or 0)
                      for o in verkoop)
    escrow = sum(float(o.get("escrow") or 0) for o in alle_orders)
    verloopt = 0
    for o in alle_orders:
        uitgegeven = _parse(o.get("issued"))
        if uitgegeven and (uitgegeven + timedelta(days=int(o.get("duration") or 0))
                           - nu).total_seconds() < 86400:
            verloopt += 1
    orderblok = {"verkoop": len(verkoop), "verkoop_isk": fmt_site(verkoop_isk),
                 "koop": len(koop), "escrow": fmt_site(escrow), "verloopt": verloopt}

    # ── Widget: RECENTE TRANSACTIES ───────────────────────────────────────
    transacties = []
    for e in alle_journal[:6]:
        bedrag = float(e.get("amount") or 0)
        transacties.append({
            "icoon": REF_ICONS.get(e.get("ref_type"), "·"),
            "tekst": e.get("description") or (e.get("ref_type") or "").replace("_", " "),
            "bedrag": ("+" if bedrag >= 0 else "−") + fmt_site(abs(bedrag)),
            "positief": bedrag >= 0,
            "geleden": _geleden(_parse(e.get("date")), nu),
        })

    # ── Widget: NETTO WAARDE ──────────────────────────────────────────────
    nettoblok = {
        "totaal": fmt_site(totaal_wallet + verkoop_isk + escrow),
        "delen": [{"label": "Wallet", "waarde": fmt_site(totaal_wallet), "kleur": "var(--dl-gold)"},
                  {"label": "Sell orders", "waarde": fmt_site(verkoop_isk), "kleur": "var(--dl-green)"},
                  {"label": "Escrow", "waarde": fmt_site(escrow), "kleur": "var(--dl-blue)"}],
    }

    # ── Widget: KILL STATISTIEKEN ─────────────────────────────────────────
    kills = verloren = 0
    kapot = kwijt = 0.0
    for vak in per_char.values():
        z = vak["zkill"] or {}
        kills += int(z.get("shipsDestroyed") or 0)
        verloren += int(z.get("shipsLost") or 0)
        kapot += float(z.get("iskDestroyed") or 0)
        kwijt += float(z.get("iskLost") or 0)
    eff = round(kapot / (kapot + kwijt) * 100) if (kapot + kwijt) else 0
    killblok = {
        "heeft": bool(kills or verloren), "eff": eff,
        # Zelfde drempels als op de site: 60 en 40.
        "kleur": "var(--dl-green)" if eff >= 60 else ("var(--dl-gold)" if eff >= 40 else "var(--dl-red)"),
        "kills": kills, "verloren": verloren,
        "kapot": fmt_site(kapot), "kwijt": fmt_site(kwijt),
    }

    # ── Widget: INKOMSTENVERDELING ────────────────────────────────────────
    # De site rekent over het hele opgehaalde journaal, niet over 30 dagen.
    cats = defaultdict(float)
    for e in alle_journal:
        bedrag = float(e.get("amount") or 0)
        if bedrag > 0:
            cats[INCOME_CATS.get(e.get("ref_type"), "Overig")] += bedrag
    cat_totaal = sum(cats.values())
    inkomsten = [{"naam": k, "pct": round(v / cat_totaal * 100) if cat_totaal else 0,
                  "bedrag": fmt_site(v), "kleur": INCOME_COLORS.get(k, "var(--dl-dim)")}
                 for k, v in sorted(cats.items(), key=lambda kv: -kv[1])[:5]]

    # ── Widget: AANKOMEND ─────────────────────────────────────────────────
    aankomend = []
    for q in alle_queue:
        eind = _parse(q.get("finish_date"))
        if eind and eind > nu:
            aankomend.append({"tijd": eind, "label": _tnaam(q.get("skill_id")),
                              "sub": f"Lvl {q.get('finished_level') or 0}",
                              "kleur": "var(--dl-blue)"})
    for j in alle_jobs:
        eind = _parse(j.get("end_date"))
        if j.get("status") == "active" and eind and eind > nu:
            aankomend.append({"tijd": eind,
                              "label": _tnaam(j.get("product_type_id")) or "Job",
                              "sub": f"×{j.get('runs') or 0} klaar", "kleur": "#a78bfa"})
    aankomend.sort(key=lambda e: e["tijd"])
    aankomend = [{**e, "rest": _over((e["tijd"] - nu).total_seconds())}
                 for e in aankomend[:6]]

    # ── WALLET-grafiek: saldo per dag ─────────────────────────────────────
    # Het journaal staat nieuwste eerst, dus de eerste regel van een dag draagt
    # het eindsaldo van die dag — precies wat `buildChartData()` doet.
    saldo_per_dag = {}
    for e in per_char[main.character_id]["journal"]:
        dag = (e.get("date") or "")[:10]
        if dag and dag not in saldo_per_dag and e.get("balance") is not None:
            saldo_per_dag[dag] = float(e["balance"])
    reeks = [{"dag": d, "saldo": s} for d, s in sorted(saldo_per_dag.items())]
    laag = min((r["saldo"] for r in reeks), default=0.0)
    hoog = max((r["saldo"] for r in reeks), default=0.0)
    bereik = (hoog - laag) or 1.0
    for i, r in enumerate(reeks):
        r["pct"] = round((r["saldo"] - laag) / bereik * 100)
        r["label"] = f"{int(r['dag'][8:10])}/{int(r['dag'][5:7])}"
        r["toon"] = i % max(1, round(len(reeks) / 6)) == 0 or i == len(reeks) - 1
    verschil = (reeks[-1]["saldo"] - reeks[0]["saldo"]) if len(reeks) > 1 else 0.0
    walletgrafiek = {"reeks": reeks, "dagen": len(reeks),
                     "verschil": ("+" if verschil >= 0 else "") + fmt_site(verschil),
                     "positief": verschil >= 0,
                     "hoog": fmt_site(hoog), "laag": fmt_site(laag)}

    # ── RATTEN / ESS: zeven dagen, gestapeld ──────────────────────────────
    rat_per_dag = defaultdict(lambda: {"bounty": 0.0, "ess": 0.0})
    for e in alle_journal:
        bedrag = float(e.get("amount") or 0)
        if bedrag <= 0:
            continue
        dag = (e.get("date") or "")[:10]
        if e.get("ref_type") == REF_BOUNTY:
            rat_per_dag[dag]["bounty"] += bedrag
        elif e.get("ref_type") == REF_ESS:
            rat_per_dag[dag]["ess"] += bedrag
    ratdagen = []
    for i in range(6, -1, -1):
        d = (nu - timedelta(days=i)).date()
        vak = rat_per_dag.get(d.isoformat(), {"bounty": 0.0, "ess": 0.0})
        ratdagen.append({"label": "Today" if i == 0 else DAG_KORT[d.weekday()],
                         "bounty": vak["bounty"], "ess": vak["ess"],
                         "totaal": vak["bounty"] + vak["ess"]})
    rattop = max((d["totaal"] for d in ratdagen), default=0) or 1
    for d in ratdagen:
        d["bounty_pct"] = round(d["bounty"] / rattop * 100)
        d["ess_pct"] = round(d["ess"] / rattop * 100)
        d["tip"] = f"{fmt_site(d['bounty'])} bounty · {fmt_site(d['ess'])} ESS"
    vandaag_rat = ratdagen[-1]
    ratblok = {"dagen": ratdagen, "bounty": fmt_site(vandaag_rat["bounty"]),
               "ess": fmt_site(vandaag_rat["ess"]),
               "totaal": fmt_site(vandaag_rat["totaal"]),
               "heeft_vandaag": vandaag_rat["totaal"] > 0}

    # ── MINING: zeven dagen ───────────────────────────────────────────────
    mijn_regels = [e for vak in per_char.values() for e in vak["mining"]]
    prijzen = esi.jita_buy({e["type_id"] for e in mijn_regels}) if mijn_regels else {}
    mijn_per_dag = defaultdict(float)
    for e in mijn_regels:
        mijn_per_dag[e["date"]] += int(e.get("quantity") or 0) * prijzen.get(e["type_id"], 0.0)
    mijndagen = []
    for i in range(6, -1, -1):
        d = (nu - timedelta(days=i)).date()
        mijndagen.append({"label": "Today" if i == 0 else DAG_KORT[d.weekday()],
                          "isk": mijn_per_dag.get(d.isoformat(), 0.0)})
    mijntop = max((d["isk"] for d in mijndagen), default=0) or 1
    for d in mijndagen:
        d["pct"] = round(d["isk"] / mijntop * 100)
        d["tip"] = fmt_site(d["isk"])
    mijnblok = {"dagen": mijndagen, "vandaag": fmt_site(mijndagen[-1]["isk"]),
                "totaal": fmt_site(sum(mijn_per_dag.values())),
                "heeft": any(d["isk"] for d in mijndagen)}

    # ── IN-GAME AGENDA ────────────────────────────────────────────────────
    gezien, agenda = set(), []
    for cid, vak in per_char.items():
        for e in vak["agenda"]:
            sleutel = (e.get("event_id"), e.get("event_date"))
            if sleutel in gezien:
                continue        # dezelfde fleet-op komt bij elke uitgenodigde binnen
            gezien.add(sleutel)
            wanneer = _parse(e.get("event_date"))
            if not wanneer or wanneer < nu - timedelta(hours=6):
                continue
            soort = e.get("owner_type") or ""
            antwoord = e.get("event_response") or "not_responded"
            agenda.append({
                "titel": e.get("title") or "", "wanneer": wanneer,
                "belangrijk": int(e.get("importance") or 0) == 1,
                "bezig": wanneer <= nu,
                "over": "Nu" if wanneer <= nu else _over((wanneer - nu).total_seconds()),
                "datum": f"{wanneer.day} {MAAND_KORT[wanneer.month]}",
                "soort_label": OWNER_LABEL.get(soort, soort),
                "soort_kleur": OWNER_COLOR.get(soort, "var(--dl-dim)"),
                "eigenaar": e.get("owner_name") or "",
                "rsvp": RSVP_LABEL.get(antwoord, "—"),
                "rsvp_kleur": RSVP_COLOR.get(antwoord, "#1c1c35"),
            })
    agenda.sort(key=lambda e: e["wanneer"])

    # ── RECENTE KILLS & LOSSES ────────────────────────────────────────────
    # De site haalt deze alleen voor de main op (`killCharId`), en dat is maar
    # goed ook: elke regel kost een eigen killmail-call. Ze veranderen nooit
    # meer, dus na één keer staan ze een maand in de cache.
    ruw = ([{**k, "soort": "kill"} for k in esi.zkill_lijst(main.character_id, "kills", 6)]
           + [{**k, "soort": "loss"} for k in esi.zkill_lijst(main.character_id, "losses", 4)])
    with ThreadPoolExecutor(max_workers=8) as pool:
        mails = list(pool.map(lambda k: esi.killmail(k["id"], k["hash"]) if k["hash"] else {}, ruw))

    kill_ids = set()
    for km in mails:
        if not km:
            continue
        slachtoffer = km.get("victim") or {}
        kill_ids.update([slachtoffer.get("character_id"), slachtoffer.get("corporation_id"),
                         slachtoffer.get("alliance_id"), slachtoffer.get("ship_type_id"),
                         km.get("solar_system_id")])
        for a in km.get("attackers") or []:
            if a.get("final_blow"):
                kill_ids.update([a.get("character_id"), a.get("corporation_id")])
    kill_ids.discard(None)
    killnamen = esi.names(kill_ids) if kill_ids else {}

    killrijen = []
    for k, km in zip(ruw, mails):
        if not km:
            continue
        slachtoffer = km.get("victim") or {}
        laatste_klap = next((a for a in km.get("attackers") or [] if a.get("final_blow")), {})
        moment = _parse(km.get("killmail_time"))
        killrijen.append({
            "id": k["id"], "kill": k["soort"] == "kill", "solo": k["solo"],
            "isk": fmt_site(k["waarde"]),
            "tijd": moment,
            "schip_id": slachtoffer.get("ship_type_id") or 0,
            "schip": killnamen.get(slachtoffer.get("ship_type_id"), ""),
            "systeem": killnamen.get(km.get("solar_system_id"), ""),
            "slachtoffer": killnamen.get(slachtoffer.get("character_id"), ""),
            "slachtoffer_id": slachtoffer.get("character_id") or 0,
            "slachtoffer_corp": killnamen.get(slachtoffer.get("corporation_id"), ""),
            "klap": killnamen.get(laatste_klap.get("character_id"), ""),
            "klap_id": laatste_klap.get("character_id") or 0,
            "klap_corp": killnamen.get(laatste_klap.get("corporation_id"), ""),
        })
    killrijen.sort(key=lambda r: r["tijd"] or nu, reverse=True)

    return {
        "leeg": False,
        "killrijen": killrijen,
        "banner": banner,
        "kaarten": kaarten,
        "meerdere": len(kaarten) > 1,
        "wallet_totaal": fmt_site(totaal_wallet),
        "wallet_negatief": totaal_wallet < 0,
        "vandaag": fmt_site(vandaag),
        "omhoog": vandaag >= gisteren,
        "gisteren": fmt_site(gisteren),
        "gisteren_meer": gisteren > 0,
        "orders_aantal": len(alle_orders),
        "orders_isk": fmt_site(verkoop_isk),
        "jobs_actief": industrieblok["actief"],
        "jobs_klaar": industrieblok["klaar"],
        "skillblok": skillblok, "industrieblok": industrieblok,
        "orderblok": orderblok, "transacties": transacties,
        "nettoblok": nettoblok, "killblok": killblok, "inkomsten": inkomsten,
        "aankomend": aankomend, "walletgrafiek": walletgrafiek,
        "ratblok": ratblok, "mijnblok": mijnblok,
        "agenda": agenda[:8],
        "aantal_characters": len(chars),
    }


# --------------------------------------------------------------------------
# Local chat
# --------------------------------------------------------------------------
#
# De chatlogs zelf leest de browser: EVE schrijft ze op de pc van het lid en de
# server heeft er niets mee te maken (en zou er ook niet bij kunnen). Wat de
# server wél weet is wie er vriendelijk is — daar zijn tokens voor nodig. Deze
# functie beantwoordt die vraag per naam, zodat de browser alleen nog hoeft te
# kleuren.
#
# De regels komen van `standingView.ts`: eigen character > eigen corp > eigen
# alliance > contacten. Wat daarna overblijft is **niet** neutraal maar vijandig
# — een onbekende in local is een risico, geen gegeven. Die laatste stap zet de
# browser, zodat een handmatige override er nog tussen kan.

STANDING_ONBEKEND = "neutral"


def standings(user, namen):
    """{naam: 'own'|'corp'|'alliance'|'friend'|'enemy'|'neutral'} voor deze namen."""
    chars = esi.characters(user)
    if not chars:
        return {}
    eigen = {c.character_name.lower(): c for c in chars}
    main = chars[0]
    mijn_corp = main.corporation_id
    mijn_alliance = main.alliance_id

    namen = [n.strip() for n in namen if n and n.strip()][:200]
    uit, zoeken = {}, []
    for naam in namen:
        if naam.lower() in eigen:
            uit[naam] = "own"
        else:
            zoeken.append(naam)

    if not zoeken:
        return uit

    gevonden = esi.zoek_ids(zoeken)
    contacten = esi.contacten([c.character_id for c in chars], mijn_corp, mijn_alliance)

    for naam in zoeken:
        vak = gevonden.get(naam.lower())
        if not vak or vak.get("soort") != "character":
            uit[naam] = STANDING_ONBEKEND      # naam bestaat niet (of is geen character)
            continue
        char_id = vak["id"]
        info = esi.character_info(char_id)
        corp_id, alliance_id = info.get("corporation_id"), info.get("alliance_id")

        # Je eigen corp en alliance staan niet in je contactenlijst — je zet geen
        # standing op jezelf. Zonder deze twee kleurde je eigen corp rood.
        if corp_id and corp_id == mijn_corp:
            uit[naam] = "corp"
            continue
        if alliance_id and mijn_alliance and alliance_id == mijn_alliance:
            uit[naam] = "alliance"
            continue

        # Meest specifieke contact wint: character vóór corp vóór alliance.
        waarde = None
        for sleutel in (char_id, corp_id, alliance_id):
            if sleutel and sleutel in contacten:
                waarde = contacten[sleutel]
                break
        if waarde is None:
            uit[naam] = STANDING_ONBEKEND
        elif waarde > 0:
            uit[naam] = "friend"
        elif waarde < 0:
            uit[naam] = "enemy"
        else:
            uit[naam] = STANDING_ONBEKEND
    return uit


# --------------------------------------------------------------------------
# Industrie
# --------------------------------------------------------------------------
#
# Vier sub-tabbladen: jobs, blueprints, bouwwinst en bouwen-of-kopen. De laatste
# twee rekenen met dezelfde cijfers en verschillen alleen in de vraag die ze
# beantwoorden: "waar valt geld mee te verdienen" tegenover "moet ik dit zelf
# maken of kopen".
#
# De rekenwijze is overgenomen van /build-profit op dutchlegionsdashboard.eu:
# materialen tegen de Jita-vráágprijs (dat betaal je als je ze nu koopt), plus
# geschatte installatiekosten van EIV × 8%, en de opbrengst tegen de Jita-
# vraagprijs minus 3,6% verkoopkosten (broker fee + sales tax). Eén verschil:
# de site laat je een ME kiezen, wij kennen de **echte ME van jouw blueprint**.

MANUFACTURING = 1               # activity_id van bouwen
JOBKOSTEN_PCT = 0.08            # installatiekosten ≈ 8% van de EIV
VERKOOPKOSTEN_PCT = 0.036       # broker fee + sales tax bij verkopen op Jita
BP_ORIGINEEL = -1               # quantity -1 = een BPO, -2 = een BPC

# Waar een blueprint ligt is niet altijd een station: ligt hij in een container,
# dan wijst location_id naar dat kistje en kent /universe/names hem niet. De
# location_flag weet het dan nog wel, en dat is beter dan een streepje.
BP_PLEK = {"Hangar": "Persoonlijke hangar", "AssetSafety": "Asset safety",
           "Deliveries": "Deliveries", "Unlocked": "In een container",
           "Locked": "In een container (locked)", "CorpSAG1": "Corp-hangar 1",
           "CorpSAG2": "Corp-hangar 2", "CorpSAG3": "Corp-hangar 3",
           "CorpSAG4": "Corp-hangar 4", "CorpSAG5": "Corp-hangar 5",
           "CorpSAG6": "Corp-hangar 6", "CorpSAG7": "Corp-hangar 7"}

JOB_STATUS = {"active": "Loopt", "paused": "Gepauzeerd", "ready": "Klaar",
              "delivered": "Afgeleverd", "cancelled": "Geannuleerd",
              "reverted": "Teruggedraaid"}


def _bp_namen_en_plekken(rijen, character_ids):
    """Namen van blueprints en van de plekken waar ze liggen."""
    type_ids = {r["type_id"] for r in rijen}
    locaties = {r["location_id"] for r in rijen if r.get("location_id")}
    stations = {i for i in locaties if i < 100_000_000}
    structuren = locaties - stations
    namen = esi.names(stations) if stations else {}
    if structuren:
        namen.update(esi.structure_names(structuren, character_ids))
    return _type_info(type_ids), namen


def industrie_jobs(user):
    """Lopende en afgeronde industry jobs van al je characters."""
    chars = esi.characters(user)
    mijn = {c.character_id: c.character_name for c in chars}
    kleuren = _kleur_per_character([{"character_id": cid} for cid in mijn])
    nu = datetime.now(timezone.utc)

    with ThreadPoolExecutor(max_workers=6) as pool:
        per_char = dict(zip(mijn, pool.map(esi.industry_jobs_compleet, list(mijn))))

    ruw = [{**j, "_char": cid} for cid, lijst in per_char.items() for j in lijst]
    typen, plekken = _bp_namen_en_plekken(
        [{"type_id": j.get("product_type_id") or j.get("blueprint_type_id"),
          "location_id": j.get("station_id") or j.get("output_location_id")} for j in ruw],
        list(mijn))

    rijen = []
    for j in ruw:
        eind = _parse(j.get("end_date"))
        start = _parse(j.get("start_date"))
        rest = (eind - nu).total_seconds() if eind else 0
        status = j.get("status") or ""
        product_id = j.get("product_type_id") or j.get("blueprint_type_id")
        plek = j.get("station_id") or j.get("output_location_id")
        rijen.append({
            "activiteit": ACTIVITEIT.get(j.get("activity_id"), "Job"),
            "activiteit_id": j.get("activity_id"),
            "naam": (typen.get(product_id) or {}).get("naam") or f"Type {product_id}",
            "plaatje": (typen.get(product_id) or {}).get("plaatje") or "",
            "runs": j.get("runs") or 0,
            "status": JOB_STATUS.get(status, status),
            "loopt": status == "active",
            "klaar": status == "ready",
            "afgerond": status in ("delivered", "cancelled", "reverted"),
            "rest_fmt": _over(rest) if rest > 0 else "",
            "pct": _voortgang(start, eind, nu) if status == "active" else 100,
            "eind": eind,
            "plek": _kort_plek(plekken.get(plek, "")),
            "kosten_fmt": fmt_isk(float(j.get("cost") or 0)),
            "char": mijn[j["_char"]], "kleur": kleuren[j["_char"]],
        })

    # Wat nu speelt eerst: klaar om op te halen, dan lopend, dan de rest.
    volgorde = {True: 0, False: 1}
    rijen.sort(key=lambda r: (volgorde[not r["klaar"]], volgorde[not r["loopt"]],
                              r["eind"] or nu), reverse=False)
    lopend = [r for r in rijen if r["loopt"]]
    return {
        "sub": "jobs",
        "jobs": rijen[:200],
        "jobs_totaal": len(rijen),
        "jobs_lopend": len(lopend),
        "jobs_klaar": sum(1 for r in rijen if r["klaar"]),
        "jobs_kosten_fmt": fmt_isk(sum(float(j.get("cost") or 0) for j in ruw)),
        "eerst_klaar": min((r["eind"] for r in lopend if r["eind"]), default=None),
    }


def _kort_plek(naam):
    """'Jita IV - Moon 4 - Caldari Navy…' → 'Jita IV'."""
    return naam.split(" - ")[0] if naam else ""


def industrie_blueprints(user):
    """Alle blueprints van je characters bij elkaar."""
    chars = esi.characters(user)
    mijn = {c.character_id: c.character_name for c in chars}
    kleuren = _kleur_per_character([{"character_id": cid} for cid in mijn])

    with ThreadPoolExecutor(max_workers=6) as pool:
        per_char = dict(zip(mijn, pool.map(esi.blueprints, list(mijn))))

    ruw = [{**b, "_char": cid} for cid, lijst in per_char.items() for b in lijst]
    typen, plekken = _bp_namen_en_plekken(ruw, list(mijn))

    rijen = []
    for b in ruw:
        origineel = int(b.get("quantity") or 0) == BP_ORIGINEEL
        rijen.append({
            "type_id": b["type_id"],
            "naam": (typen.get(b["type_id"]) or {}).get("naam") or f"Type {b['type_id']}",
            "plaatje": (typen.get(b["type_id"]) or {}).get("plaatje") or "",
            "origineel": origineel,
            "me": int(b.get("material_efficiency") or 0),
            "te": int(b.get("time_efficiency") or 0),
            # Een BPO heeft oneindig runs (-1); een kopie telt ze af.
            "runs": int(b.get("runs") or 0),
            "runs_fmt": "∞" if int(b.get("runs") or 0) < 0 else _getal(b.get("runs") or 0),
            "plek": (_kort_plek(plekken.get(b.get("location_id"), ""))
                     or BP_PLEK.get(b.get("location_flag"), b.get("location_flag") or "")),
            "plek_vol": (plekken.get(b.get("location_id"), "")
                         or BP_PLEK.get(b.get("location_flag"), "")),
            "char": mijn[b["_char"]], "kleur": kleuren[b["_char"]],
        })
    # Originelen eerst, en daarbinnen de best onderzochte: dat is de blueprint
    # waar je mee zou bouwen.
    rijen.sort(key=lambda r: (not r["origineel"], -r["me"], r["naam"]))

    per_plek = Counter(r["plek"] for r in rijen if r["plek"])
    return {
        "sub": "blueprints",
        "blueprints": rijen[:400],
        "bp_totaal": len(rijen),
        "bp_originelen": sum(1 for r in rijen if r["origineel"]),
        "bp_kopieen": sum(1 for r in rijen if not r["origineel"]),
        "bp_soorten": len({r["type_id"] for r in rijen}),
        "bp_perfect": sum(1 for r in rijen if r["origineel"] and r["me"] >= 10),
        "bp_plekken": [{"naam": n, "aantal": a} for n, a in per_plek.most_common(8)],
    }


def _industrie_materialen(type_ids):
    """Materialen en product per blueprint, uit de SDE via eveuniverse.

    ESI kent geen blueprints, dus dit komt uit de SDE-tabellen die eveuniverse
    **per type** ophaalt. Dat is de eerste keer een seconde per blueprint, dus
    parallel; daarna staat het in de database en is het gratis. Bewust geen
    massale load van alle types in het spel — dat was ooit goed voor een
    wachtrij van 700.000 taken.
    """
    try:
        from eveuniverse.models import (EveIndustryActivityMaterial,
                                        EveIndustryActivityProduct, EveType)
    except ImportError:
        return {}, {}

    ids = [int(i) for i in type_ids if i]
    if not ids:
        return {}, {}

    from django.db import connections

    def _laad(tid):
        try:
            EveType.objects.get_or_create_esi(
                id=tid, enabled_sections=[EveType.Section.INDUSTRY_ACTIVITIES])
        except Exception:                   # noqa: BLE001 — één rot type mag de rest niet slopen
            pass
        finally:
            connections.close_all()         # elke thread ruimt z'n eigen verbinding op

    ontbreekt = [t for t in ids
                 if not EveIndustryActivityProduct.objects.filter(eve_type_id=t).exists()]
    if ontbreekt:
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(_laad, ontbreekt))

    materialen = defaultdict(list)
    for m in (EveIndustryActivityMaterial.objects
              .filter(eve_type_id__in=ids, activity_id=MANUFACTURING)
              .values_list("eve_type_id", "material_eve_type_id", "quantity")):
        materialen[m[0]].append((m[1], int(m[2])))
    producten = {}
    for p in (EveIndustryActivityProduct.objects
              .filter(eve_type_id__in=ids, activity_id=MANUFACTURING)
              .values_list("eve_type_id", "product_eve_type_id", "quantity")):
        producten[p[0]] = (p[1], int(p[2]))
    return materialen, producten


def industrie_bouwen(user, sub="bouwwinst"):
    """Wat het kost om je blueprints te bouwen, en wat het oplevert."""
    chars = esi.characters(user)
    mijn = {c.character_id: c.character_name for c in chars}

    with ThreadPoolExecutor(max_workers=6) as pool:
        per_char = dict(zip(mijn, pool.map(esi.blueprints, list(mijn))))
    ruw = [b for lijst in per_char.values() for b in lijst]

    # Per blueprint-soort de beste ME die je bezit: dáármee zou je bouwen.
    beste_me = {}
    for b in ruw:
        tid = b["type_id"]
        me = int(b.get("material_efficiency") or 0)
        if tid not in beste_me or me > beste_me[tid]:
            beste_me[tid] = me

    materialen, producten = _industrie_materialen(beste_me)
    if not producten:
        return {"sub": sub, "bouw": [], "geen_data": True}

    # Alles wat we moeten prijzen: de materialen en de producten.
    prijs_ids = {p[0] for p in producten.values()}
    for mats in materialen.values():
        prijs_ids.update(m[0] for m in mats)
    prijzen = esi.jita_prijzen(prijs_ids)
    aangepast = esi.markt_prijzen()
    typen = _type_info(prijs_ids | set(beste_me))

    rijen = []
    for bp_id, me in beste_me.items():
        mats = materialen.get(bp_id)
        product = producten.get(bp_id)
        if not mats or not product:
            continue
        product_id, per_run = product

        kosten, eiv, ontbrekend = 0.0, 0.0, False
        materiaallijst = []
        for mat_id, basis in mats:
            # ME verlaagt het materiaalverbruik; afronden naar boven, en nooit
            # onder één stuk per run — zo rekent het spel het ook.
            nodig = max(1, math.ceil(basis * (1 - me / 100)))
            prijs = (prijzen.get(mat_id) or {}).get("verkoop") or 0.0
            if not prijs:
                ontbrekend = True
            kosten += nodig * prijs
            eiv += basis * aangepast.get(mat_id, 0.0)
            materiaallijst.append({
                "type_id": mat_id,
                "naam": (typen.get(mat_id) or {}).get("naam") or f"Type {mat_id}",
                "aantal": nodig, "aantal_fmt": _getal(nodig),
                "isk_fmt": fmt_isk(nodig * prijs),
            })

        jobkosten = eiv * JOBKOSTEN_PCT
        verkoopprijs = (prijzen.get(product_id) or {}).get("verkoop") or 0.0
        opbrengst = verkoopprijs * per_run * (1 - VERKOOPKOSTEN_PCT)
        totaal_kosten = kosten + jobkosten
        winst = opbrengst - totaal_kosten
        marge = (winst / totaal_kosten * 100) if totaal_kosten else 0.0

        rijen.append({
            "bp_id": bp_id,
            "bp_naam": (typen.get(bp_id) or {}).get("naam") or f"Type {bp_id}",
            "product_id": product_id,
            "product": (typen.get(product_id) or {}).get("naam") or f"Type {product_id}",
            "plaatje": (typen.get(product_id) or {}).get("plaatje") or "",
            "me": me, "per_run": per_run,
            "materialen": sorted(materiaallijst, key=lambda m: -m["aantal"])[:12],
            "kosten": totaal_kosten, "kosten_fmt": fmt_isk(totaal_kosten),
            "materiaal_fmt": fmt_isk(kosten), "job_fmt": fmt_isk(jobkosten),
            "opbrengst": opbrengst, "opbrengst_fmt": fmt_isk(opbrengst),
            "koopprijs": verkoopprijs * per_run,
            "koopprijs_fmt": fmt_isk(verkoopprijs * per_run),
            "winst": winst, "winst_fmt": fmt_isk(abs(winst)), "loont": winst > 0,
            "marge": marge, "marge_fmt": _nl(f"{marge:,.1f}"),
            # Zonder marktprijs voor een materiaal is de uitkomst een gok; dat
            # zeggen we erbij in plaats van een mooi getal te tonen.
            "onvolledig": ontbrekend,
            # Voor "bouwen of kopen": goedkoper zelf maken dan kant-en-klaar kopen?
            "zelf_goedkoper": totaal_kosten < verkoopprijs * per_run,
            "verschil_fmt": fmt_isk(abs(verkoopprijs * per_run - totaal_kosten)),
        })

    if sub == "bouwenkopen":
        rijen.sort(key=lambda r: -(r["koopprijs"] - r["kosten"]))
    else:
        rijen.sort(key=lambda r: -r["winst"])

    winstgevend = [r for r in rijen if r["loont"]]
    return {
        "sub": sub,
        "bouw": rijen[:100],
        "bouw_totaal": len(rijen),
        "bouw_winstgevend": len(winstgevend),
        "bouw_beste_fmt": fmt_isk(winstgevend[0]["winst"]) if winstgevend else "0",
        "bouw_beste_naam": winstgevend[0]["product"] if winstgevend else "",
        "bouw_zelf": sum(1 for r in rijen if r["zelf_goedkoper"]),
        "jobkosten_pct": round(JOBKOSTEN_PCT * 100),
        "verkoopkosten_pct": _nl(f"{VERKOOPKOSTEN_PCT * 100:,.1f}"),
    }


def industrie(user, sub="jobs", **kwargs):
    """Het Industry-tabblad; `sub` kiest welk sub-tabblad."""
    if sub == "pi":
        return {"sub": "pi", **pi(user)}
    if sub == "bouwproject":
        return bouwproject(user, **kwargs)
    if sub == "blueprints":
        return industrie_blueprints(user)
    if sub in ("bouwwinst", "bouwenkopen"):
        return industrie_bouwen(user, sub)
    return industrie_jobs(user)


# --------------------------------------------------------------------------
# Bouwproject
# --------------------------------------------------------------------------
#
# Overgenomen van /build op dutchlegionsdashboard.eu: je kiest wat je wil maken
# en hoeveel, en de pagina rekent de hele boom uit — het schip vraagt om
# onderdelen, die onderdelen vragen weer om andere. Wat je al in je hangars hebt
# wordt afgetrokken, en wat overblijft is je inkooplijst.
#
# Verschil met de site: die bewaart projecten en vinkjes in de browser. Hier
# staat het project in de URL (?type=&aantal=&me=), dus deelbaar en zonder
# opslag — vinkjes voor "job draait" zitten er (nog) niet in.

BOUW_MAX_DIEPTE = 6             # ruim genoeg voor schip → onderdeel → grondstof
BOUW_MAX_KNOPEN = 400           # een boom die groter is leest niemand meer


def _voorraad(user, plek=None):
    """{type_id: aantal} uit je assets, eventueel alleen op één locatie.

    Let op waar spullen volgens ESI "liggen": van 3326 assets hadden er hier
    3229 een **kist of schip** als locatie, niet een station. Je moet dus de
    keten omhoog lopen — item in kist, kist in hangar, hangar in station — tot
    je bij iets uitkomt dat geen eigen asset meer is. Zonder dat lijkt vrijwel
    niets ergens te liggen en filtert een locatiekeuze alles weg.
    """
    chars = esi.characters(user)
    alles = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        for rijen in pool.map(esi.assets, [c.character_id for c in chars]):
            alles.extend(rijen)

    # Waar zit elk van onze eigen items in? Daarmee is de keten te volgen.
    ouder = {a["item_id"]: (a.get("location_id"), a.get("location_type"))
             for a in alles if a.get("item_id")}

    def _wortel(loc_id, loc_type):
        for _ in range(12):                 # een keten is nooit twaalf diep
            if loc_type != "item" or loc_id not in ouder:
                return loc_id
            loc_id, loc_type = ouder[loc_id]
        return loc_id

    voorraad = defaultdict(int)
    per_plek = defaultdict(int)
    plekken = defaultdict(int)
    for a in alles:
        if not a.get("type_id"):
            continue
        wortel = _wortel(a.get("location_id"), a.get("location_type"))
        aantal = int(a.get("quantity") or 0)
        plekken[wortel] += 1
        if plek is None or wortel == plek:
            voorraad[a["type_id"]] += aantal
        per_plek[wortel] += aantal
    return voorraad, plekken


def bouwproject(user, type_id=None, aantal=1, me=10, zoek="", koop=None, plek=None):
    """De bouwboom voor een doel, met voorraad, tekorten en een inkooplijst."""
    recepten = esi.sde_recepten()
    # Onderdelen die je liever koopt dan zelf maakt. Ze staan in de URL en niet
    # in de browseropslag, want ze veranderen de bóóm: de server moet die tak
    # dan niet verder uitrekenen maar op de inkooplijst zetten.
    koop = set(koop or ())
    basis = {"sub": "bouwproject", "zoek": zoek, "aantal": aantal, "me": me,
             "me_keuzes": [0, 5, 10], "heeft_recepten": bool(recepten),
             "koop_lijst": sorted(koop), "koop_param": ",".join(str(k) for k in sorted(koop))}

    # ── Zoeken naar het doel ──────────────────────────────────────────────
    if zoek and not type_id:
        treffers = []
        try:
            from eveuniverse.models import EveType

            qs = (EveType.objects.filter(name__icontains=zoek, published=True)
                  .order_by("name")[:40])
            treffers = [{"type_id": t.id, "naam": t.name}
                        for t in qs if t.id in recepten]
        except ImportError:
            pass
        return {**basis, "treffers": treffers, "boom": [], "doel": None}

    if not type_id or type_id not in recepten:
        return {**basis, "treffers": [], "boom": [], "doel": None}

    voorraad, plekken = _voorraad(user, plek)
    eigen_bps = set()
    for c in esi.characters(user):
        for b in esi.blueprints(c.character_id):
            eigen_bps.add(b["type_id"])

    # ── De boom aflopen ───────────────────────────────────────────────────
    # Voorraad wordt onderweg opgesoupeerd: heb je 100 van iets en vraagt de
    # boom er twee keer 60, dan is de tweede keer maar 40 gedekt. Zonder dat
    # zou dezelfde voorraad twee keer meetellen.
    rest_voorraad = dict(voorraad)
    knopen, inkoop = [], defaultdict(int)

    def _loop(tid, nodig, diepte):
        if len(knopen) >= BOUW_MAX_KNOPEN:
            return
        beschikbaar = min(rest_voorraad.get(tid, 0), nodig)
        rest_voorraad[tid] = rest_voorraad.get(tid, 0) - beschikbaar
        tekort = nodig - beschikbaar
        recept = recepten.get(tid)
        # Zelf maken kan alleen als er een recept is, jij het niet op "kopen"
        # hebt gezet, en we niet te diep zitten.
        maken = (bool(recept) and tid not in koop
                 and diepte < BOUW_MAX_DIEPTE and tekort > 0)
        runs = math.ceil(tekort / recept["per_run"]) if maken else 0

        knopen.append({
            "type_id": tid, "diepte": diepte, "nodig": nodig,
            "nodig_fmt": _getal(nodig), "voorraad": beschikbaar,
            "voorraad_fmt": _getal(beschikbaar), "tekort": tekort,
            "tekort_fmt": _getal(tekort), "maken": maken, "runs": runs,
            "heeft_bp": bool(recept) and recept["bp"] in eigen_bps,
            "bouwbaar": bool(recept),
            "gekozen_kopen": tid in koop,
            # Het eindproduct zelf mag je niet op "kopen" zetten; dan bouw je niets.
            "mag_wisselen": bool(recept) and diepte > 0,
        })
        if not maken:
            if tekort > 0:
                inkoop[tid] += tekort
            return
        for mat_id, basis_aantal in recept["materialen"]:
            # ME verlaagt het verbruik; per run naar boven afronden en nooit
            # onder één stuk — zo rekent het spel het ook.
            per_run = max(1, math.ceil(basis_aantal * (1 - me / 100)))
            _loop(mat_id, per_run * runs, diepte + 1)

    _loop(type_id, aantal, 0)

    # ── Namen en prijzen erbij ────────────────────────────────────────────
    top_plekken = [p for p, _ in sorted(plekken.items(), key=lambda kv: -kv[1])[:12] if p]
    stations = {p for p in top_plekken if p < 100_000_000}
    plek_namen = esi.names(stations) if stations else {}
    rest = set(top_plekken) - stations
    if rest:
        plek_namen.update(esi.structure_names(rest, [c.character_id for c in esi.characters(user)]))
    plek_keuzes = [{"id": p, "naam": plek_namen.get(p) or f"#{p}", "aantal": plekken[p]}
                   for p in top_plekken if plek_namen.get(p)]

    ids = {k["type_id"] for k in knopen} | set(inkoop)
    typen = _type_info(ids)
    prijzen = esi.jita_prijzen(ids)

    for k in knopen:
        info = typen.get(k["type_id"]) or {}
        k["naam"] = info.get("naam") or f"Type {k['type_id']}"
        k["plaatje"] = info.get("plaatje") or ""
        prijs = (prijzen.get(k["type_id"]) or {}).get("verkoop") or 0.0
        k["isk_fmt"] = fmt_isk(prijs * k["tekort"])
        # Inspringen doen we met een marge in de template; hier alleen het getal.
        k["inspring"] = min(k["diepte"], 5)

    inkooplijst = []
    for tid, n in sorted(inkoop.items(), key=lambda kv: -kv[1]):
        info = typen.get(tid) or {}
        prijs = (prijzen.get(tid) or {}).get("verkoop") or 0.0
        inkooplijst.append({
            "type_id": tid, "naam": info.get("naam") or f"Type {tid}",
            "plaatje": info.get("plaatje") or "",
            "aantal": n, "aantal_fmt": _getal(n),
            "isk": prijs * n, "isk_fmt": fmt_isk(prijs * n),
            "geen_prijs": not prijs,
        })
    inkooplijst.sort(key=lambda r: -r["isk"])
    kosten = sum(r["isk"] for r in inkooplijst)

    doel_info = typen.get(type_id) or {}
    # Niet alles is op de markt te koop: een supercapital of titan heeft geen
    # Jita-prijs. Dan is "kopen is goedkoper" geen advies maar een verkeerde
    # conclusie uit een ontbrekend getal.
    stukprijs = (prijzen.get(type_id) or {}).get("verkoop") or 0.0
    opbrengst = stukprijs * aantal

    return {
        **basis,
        "plek": plek, "plek_keuzes": plek_keuzes,
        "plek_naam": plek_namen.get(plek, "") if plek else "",
        "doel": {"type_id": type_id, "naam": doel_info.get("naam") or f"Type {type_id}",
                 "plaatje": doel_info.get("plaatje") or "", "aantal": aantal},
        "boom": knopen,
        "knopen": len(knopen),
        "afgekapt": len(knopen) >= BOUW_MAX_KNOPEN,
        "inkoop": inkooplijst[:60],
        "inkoop_regels": len(inkooplijst),
        "kosten": kosten, "kosten_fmt": fmt_isk(kosten),
        "opbrengst_fmt": fmt_isk(opbrengst),
        "winst_fmt": fmt_isk(opbrengst - kosten),
        "loont": bool(stukprijs) and opbrengst > kosten,
        "geen_marktprijs": not stukprijs,
        # Om in het multibuy-venster van het spel te plakken.
        "multibuy": "\n".join(f"{r['naam']} {r['aantal']}" for r in inkooplijst),
        "uit_voorraad": sum(1 for k in knopen if k["voorraad"]),
        "zelf_maken": sum(1 for k in knopen if k["maken"]),
        "mist_bp": sum(1 for k in knopen if k["maken"] and not k["heeft_bp"]),
    }
