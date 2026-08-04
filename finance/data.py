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

    regels = []
    for c in chars:
        for e in esi.journal(c.character_id):
            regels.append({**e, "_char": c.character_name})
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
    }


# --------------------------------------------------------------------------
# Ratting — port van Ratting.tsx
# --------------------------------------------------------------------------

def ratting(user, dagen=30):
    """Bounty- en ESS-inkomsten van het hele account."""
    chars = esi.characters(user)
    regels = []
    for c in chars:
        for e in esi.journal(c.character_id):
            if e.get("ref_type") in (REF_BOUNTY, REF_ESS):
                regels.append({**e, "_char": c.character_name})
    regels.sort(key=lambda e: e.get("date") or "", reverse=True)

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

    reeks = [per_dag[k] for k in sorted(per_dag)]
    hoogste = max((v["bounty"] + v["ess"] for v in reeks), default=0) or 1
    for v in reeks:
        v["totaal"] = v["bounty"] + v["ess"]
        v["totaal_fmt"] = fmt_isk(v["totaal"])
        # De balk is gestapeld: bounty onderaan, ESS erbovenop. De twee stukken
        # moeten dus optellen tot de totale hoogte, niet er allebei apart op staan.
        v["pct_bounty"] = round(v["bounty"] / hoogste * 100)
        v["pct_ess"] = round(v["totaal"] / hoogste * 100) - v["pct_bounty"]
        v["dag_kort"] = v["dag"][5:]

    actieve_dagen = len(per_dag)
    totaal = bounty + ess
    getoond = reeks[-dagen:]
    return {
        "totaal": totaal, "totaal_fmt": fmt_isk(totaal),
        "laatste_dag": getoond[-1]["dag"] if getoond else "",
        "bounty_fmt": fmt_isk(bounty), "ess_fmt": fmt_isk(ess),
        "sessies": sessies,
        "actieve_dagen": actieve_dagen,
        "gem_per_dag_fmt": fmt_isk(totaal / actieve_dagen) if actieve_dagen else "—",
        "grafiek": getoond,
        "regels": [{
            "datum": _parse(e.get("date")),
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

    ruw = {}
    for c in chars:
        for k in esi.contracts(c.character_id):
            ruw[k["contract_id"]] = k

    lijst = list(ruw.values())
    ids = set()
    for k in lijst:
        for veld in ("issuer_id", "assignee_id", "acceptor_id"):
            if k.get(veld):
                ids.add(k[veld])
    namen = esi.names(ids) if ids else {}

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

        rijen.append({
            "id": k["contract_id"],
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
