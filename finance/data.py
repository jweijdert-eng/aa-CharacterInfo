"""Rekenwerk voor de drie pagina's — Finance.

De ratting-logica is een port van `Ratting.tsx` uit het dashboard: dezelfde twee
ref_types, dezelfde totalen en dezelfde groepering per dag, zodat beide plekken
hetzelfde getal laten zien.
"""

from collections import defaultdict
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
            return f"{waarde / grens:,.2f} {achtervoegsel}".replace(",", ".")
    return f"{waarde:,.0f}".replace(",", ".")


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
            "saldo_fmt": fmt_isk(saldo) if saldo is not None else "—",
            "gekoppeld": saldo is not None,
        })

    # Rijkste eerst, en per character het aandeel in je totale vermogen. Met een
    # balkje ernaast zie je in één blik waar je ISK staat — bij zes characters
    # is dat uit losse getallen niet af te lezen.
    kleuren = _kleur_per_character(per_char)
    per_char.sort(key=lambda c: -(c["saldo"] or 0))
    for c in per_char:
        aandeel = (float(c["saldo"]) / totaal * 100) if (totaal and c["saldo"]) else 0
        c["aandeel"] = round(aandeel)
        c["aandeel_fmt"] = f"{aandeel:.0f}%" if aandeel >= 1 else "<1%"
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
    for e in regels:
        d = _parse(e.get("date"))
        if d and d >= grens:
            per_soort[e.get("ref_type") or "onbekend"] += float(e.get("amount") or 0)

    soorten = sorted(per_soort.items(), key=lambda kv: -abs(kv[1]))[:12]
    # Balkje op de grootste post schalen, zodat je in één blik ziet wat zwaar weegt.
    grootste = max((abs(v) for _, v in soorten), default=0) or 1
    return {
        "characters": per_char,
        "totaal": totaal,
        "totaal_fmt": fmt_isk(totaal),
        "regels": [_journaalregel(e) for e in regels[:200]],
        "soorten": [{"ref_type": k, "naam": k.replace("_", " ").capitalize(),
                     "bedrag": v, "bedrag_fmt": fmt_isk(v), "positief": v >= 0,
                     "pct": round(abs(v) / grootste * 100)}
                    for k, v in soorten],
    }


def _journaalregel(e):
    return {
        "datum": _parse(e.get("date")),
        "soort": (e.get("ref_type") or "").replace("_", " ").capitalize(),
        "omschrijving": e.get("description") or "",
        "bedrag": float(e.get("amount") or 0),
        "bedrag_fmt": fmt_isk(e.get("amount")),
        "positief": float(e.get("amount") or 0) >= 0,
        "saldo_fmt": fmt_isk(e.get("balance")),
        "character": e.get("_char", ""),
        "kleur": e.get("_kleur", ""),
    }


# --------------------------------------------------------------------------
# Ratting — port van Ratting.tsx
# --------------------------------------------------------------------------

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
            "naam": e["_char"], "kleur": e["_kleur"], "bedrag": 0.0, "aantal": 0})
        vak["bedrag"] += float(e["amount"])
        vak["aantal"] += 1
    verdeling = sorted(per_char.values(), key=lambda v: -v["bedrag"])
    hoogste_char = max((v["bedrag"] for v in verdeling), default=0) or 1
    for v in verdeling:
        v["bedrag_fmt"] = fmt_isk(v["bedrag"])
        v["pct"] = round(v["bedrag"] / hoogste_char * 100)

    bounty = sum(float(e["amount"]) for e in regels if e["ref_type"] == REF_BOUNTY)
    ess = sum(float(e["amount"]) for e in regels if e["ref_type"] == REF_ESS)
    # Eén "sessie" = één kopgeldbetaling. ESS-uitbetalingen tellen niet mee,
    # anders tel je dezelfde ratting-sessie dubbel.
    sessies = sum(1 for e in regels if e["ref_type"] == REF_BOUNTY)

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
        "sessies": sessies,
        "actieve_dagen": actieve_dagen,
        "gem_per_dag_fmt": fmt_isk(totaal / actieve_dagen) if actieve_dagen else "—",
        "grafiek": getoond,
        "verdeling": verdeling if len(verdeling) > 1 else [],
        "aantal_characters": len(chars),
        "regels": [{
            "datum": _parse(e.get("date")),
            "kleur": e.get("_kleur", ""),
            "is_ess": e["ref_type"] == REF_ESS,
            "soort": "ESS" if e["ref_type"] == REF_ESS else "Bounty",
            "bedrag_fmt": fmt_isk(e["amount"]),
            "systeem_id": e.get("context_id") if e.get("context_id_type") == "system_id" else None,
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
    namen = esi.names(ids) if ids else {}

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
            "onderpand_fmt": fmt_isk(k.get("collateral")),
            "volume_fmt": f"{float(k.get('volume') or 0):,.0f}".replace(",", "."),
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
    }


# --------------------------------------------------------------------------
# Mining
# --------------------------------------------------------------------------

def _type_gegevens(type_ids):
    """Volume, portionSize en reprocessing-materialen uit django-eveuniverse.

    Die database staat er lokaal al (volledige type-data geladen), dus hiervoor
    is geen enkele ESI-call nodig.
    """
    volumes, portie, materialen = {}, {}, {}
    try:
        from eveuniverse.models import EveType, EveTypeMaterial
    except ImportError:                 # eveuniverse niet geïnstalleerd
        return volumes, portie, materialen

    for t in EveType.objects.filter(id__in=list(type_ids)):
        volumes[t.id] = float(t.volume or 0)
        portie[t.id] = int(t.portion_size or 0)
    for m in EveTypeMaterial.objects.filter(eve_type_id__in=list(type_ids)):
        materialen.setdefault(m.eve_type_id, []).append(
            (m.material_eve_type_id, int(m.quantity or 0)))
    return volumes, portie, materialen


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
                "heeft_prijzen": False}



    type_ids = {e["type_id"] for e in regels}
    namen = esi.names(type_ids | {e["solar_system_id"] for e in regels})

    # Volume en reprocessing-opbrengst komen uit django-eveuniverse, dat lokaal
    # de volledige type-data heeft. Prijzen via Fuzzwork (Jita buy), dezelfde
    # bron als de dashboardpagina zodat beide hetzelfde bedrag tonen.
    volumes, portie, materialen = _type_gegevens(type_ids)
    mineraal_ids = {mid for mats in materialen.values() for mid, _ in mats}
    prijzen = esi.jita_buy(type_ids | mineraal_ids)

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
    systemen = _groepeer("solar_system_id", naam_van)

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
            "naam": e["_char"], "kleur": e["_kleur"], "aantal": 0})
        vak["aantal"] += int(e["quantity"])
    verdeling = sorted(per_char.values(), key=lambda v: -v["aantal"])
    hoogste_char = verdeling[0]["aantal"] if verdeling else 1
    for v in verdeling:
        v["aantal_fmt"] = f"{v['aantal']:,}".replace(",", ".")
        v["pct"] = round(v["aantal"] / hoogste_char * 100)

    regels.sort(key=lambda e: (e["date"], e["quantity"]), reverse=True)
    return {
        "totaal": totaal,
        "totaal_fmt": f"{totaal:,}".replace(",", "."),
        "totaal_m3_fmt": f"{totaal_m3:,.0f}".replace(",", "."),
        "totaal_isk_fmt": fmt_isk(totaal_isk),
        "totaal_ref_fmt": fmt_isk(totaal_ref),
        "heeft_prijzen": bool(prijzen),
        "soorten": len(ertsen),
        "actieve_dagen": len(per_dag),
        "ertsen": ertsen[:15],
        "systemen": systemen[:10],
        "grafiek": reeks,
        "schaal": schaal,
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


def pi(user):
    """Planetaire kolonies van alle characters."""
    chars = esi.characters(user)
    kleuren = _kleur_per_character([{"character_id": c.character_id} for c in chars])

    rijen = []
    for c in chars:
        for p in esi.planets(c.character_id):
            rijen.append({**p, "_char": c.character_name,
                          "_char_id": c.character_id,
                          "_kleur": kleuren[c.character_id]})
    if not rijen:
        return {"planeten": [], "aantal": 0, "aantal_characters": len(chars),
                "per_type": [], "verdeling": [], "pins": 0}

    # Alleen de systemen via /universe/names; planeet-ids kent die endpoint niet
    # (404), daar is /universe/planets/{id}/ voor.
    namen = esi.names({p["solar_system_id"] for p in rijen})
    planeetnamen = {p["planet_id"]: esi.planet_info(p["planet_id"]) for p in rijen}

    per_type = defaultdict(int)
    per_char = {}
    planeten = []
    for p in rijen:
        soort = p.get("planet_type") or "onbekend"
        per_type[soort] += 1
        vak = per_char.setdefault(p["_char_id"], {
            "naam": p["_char"], "kleur": p["_kleur"], "aantal": 0, "pins": 0})
        vak["aantal"] += 1
        vak["pins"] += int(p.get("num_pins") or 0)
        planeten.append({
            "planeet": planeetnamen.get(p["planet_id"]) or f"#{p['planet_id']}",
            "systeem": namen.get(p["solar_system_id"]) or f"#{p['solar_system_id']}",
            "type": PLANEET_LABEL.get(soort, soort.capitalize()),
            "type_ruw": soort,
            "niveau": p.get("upgrade_level"),
            "pins": p.get("num_pins"),
            "bijgewerkt": _parse(p.get("last_update")),
            "character": p["_char"],
            "kleur": p["_kleur"],
        })

    planeten.sort(key=lambda p: (p["character"], p["systeem"]))
    return {
        "planeten": planeten,
        "aantal": len(planeten),
        "pins": sum(int(p.get("num_pins") or 0) for p in rijen),
        "aantal_characters": len(chars),
        "per_type": sorted(({"naam": PLANEET_LABEL.get(k, k.capitalize()), "aantal": v}
                            for k, v in per_type.items()), key=lambda x: -x["aantal"]),
        "verdeling": sorted(per_char.values(), key=lambda v: -v["aantal"]),
    }
