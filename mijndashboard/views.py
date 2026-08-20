"""Views — Finance."""

import json

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _
from django.utils.translation import ngettext

from esi.decorators import token_required

from . import __version__, data, esi

# Alle scopes tegelijk vragen: dan hoeft de gebruiker maar één keer te
# koppelen voor alle tabbladen.
SCOPES = [esi.WALLET_SCOPE, esi.CONTRACTS_SCOPE, esi.MINING_SCOPE,
          esi.PLANETS_SCOPE, esi.MAIL_SCOPE, esi.ORDERS_SCOPE,
          esi.SEND_MAIL_SCOPE]

# Apart, en met opzet niet in SCOPES: hiermee mag een site fleets beheren, en
# dat hoeft een gewoon lid niet weg te geven om z'n wallet te kunnen zien. Wie
# FC't koppelt één keer extra via de knop op het Fleet-tabblad.
FC_SCOPES = [esi.FLEET_READ_SCOPE, esi.FLEET_WRITE_SCOPE,
             esi.WAYPOINT_SCOPE, esi.LOCATIE_SCOPE]


def _character_ids(user):
    return [c.character_id for c in esi.characters(user)]


def _basis(request, actief):
    """Gedeelde context: welk tabblad actief is en of er iets gekoppeld is."""
    chars = esi.characters(request.user)
    ids = [c.character_id for c in chars]
    return {
        "actief": actief,
        # Voor het local-paneel: het kleurt je eigen namen groen en let op of je
        # genoemd wordt. Staat op twee tabbladen, dus hier één keer.
        "eigen_namen": json.dumps([c.character_name for c in chars]),
        # Hangt achter de stylesheet-URL. Zonder dat blijft een browser na een
        # nieuwe versie de oude CSS gebruiken — dan staat de hele pagina
        # ongestyled onder elkaar en lijkt er een bug te zijn.
        "versie": __version__,
        "heeft_wallet": esi.has_token(ids, esi.WALLET_SCOPE),
        "heeft_contracts": esi.has_token(ids, esi.CONTRACTS_SCOPE),
        "heeft_mining": esi.has_token(ids, esi.MINING_SCOPE),
        "heeft_pi": esi.has_token(ids, esi.PLANETS_SCOPE),
        "heeft_mail": esi.has_token(ids, esi.MAIL_SCOPE),
        "heeft_markt": esi.has_token(ids, esi.ORDERS_SCOPE),
    }


@login_required
@permission_required("mijndashboard.basic_access")
def dashboard(request: WSGIRequest) -> HttpResponse:
    """De startpagina: waar sta je, wat loopt er, en wat is het waard."""
    ctx = _basis(request, "dashboard")
    # Geen enkele scope nodig om de pagina te tonen: elk blok valt los weg als
    # het bijbehorende token er niet is.
    ctx.update(data.dashboard(request.user))
    return render(request, "mijndashboard/dashboard.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
def local(request: WSGIRequest) -> HttpResponse:
    """Local chat: de logs leest de browser zelf, wij kleuren de namen."""
    ctx = _basis(request, "local")
    return render(request, "mijndashboard/local.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
def local_standings(request: WSGIRequest) -> HttpResponse:
    """Namen uit local -> standing. Alleen POST, want het is een lijst namen.

    De browser vraagt dit per groepje op en onthoudt het antwoord; wij hoeven
    dus niet per bericht iets op te zoeken.
    """
    if request.method != "POST":
        return JsonResponse({"fout": "alleen POST"}, status=405)
    try:
        namen = (json.loads(request.body or b"{}") or {}).get("namen") or []
    except ValueError:
        return JsonResponse({"fout": "geen geldige JSON"}, status=400)
    if not isinstance(namen, list):
        return JsonResponse({"fout": "namen moet een lijst zijn"}, status=400)
    return JsonResponse(data.standings(request.user, [str(n) for n in namen]))


@login_required
@permission_required("mijndashboard.basic_access")
def wallet(request: WSGIRequest) -> HttpResponse:
    """Saldo per character plus het gecombineerde journaal."""
    ctx = _basis(request, "wallet")
    if ctx["heeft_wallet"]:
        ctx.update(data.wallet(request.user))
        # Beide tabellen meteen meesturen: het wisselen tussen de twee gebeurt
        # in CSS, dus een tweede paginaverzoek zou alleen maar vertragen.
        ctx.update(data.transacties(request.user))
    return render(request, "mijndashboard/wallet.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
def contracts(request: WSGIRequest) -> HttpResponse:
    """Je persoonlijke contracten."""
    ctx = _basis(request, "contracts")
    if ctx["heeft_contracts"]:
        ctx.update(data.contracten(request.user))
    return render(request, "mijndashboard/contracts.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
def ratting(request: WSGIRequest) -> HttpResponse:
    """Bounty- en ESS-inkomsten."""
    ctx = _basis(request, "ratting")
    if ctx["heeft_wallet"]:
        ctx.update(data.ratting(request.user))
    return render(request, "mijndashboard/ratting.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
def mining(request: WSGIRequest) -> HttpResponse:
    """Wat je bij elkaar gemijnd hebt."""
    ctx = _basis(request, "mining")
    if ctx["heeft_mining"]:
        # Refine-rendement uit de URL. Onzin negeren we stilzwijgend: de waarde
        # komt uit een link, niet uit een formulier waar iemand op wacht.
        try:
            rendement = int(request.GET.get("refine") or data.REFINE_STANDAARD)
        except ValueError:
            rendement = data.REFINE_STANDAARD
        ctx.update(data.mining(request.user, rendement=rendement))
    return render(request, "mijndashboard/mining.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
def pi(request: WSGIRequest) -> HttpResponse:
    """Je planetaire kolonies — sub-tabblad van Industry."""
    ctx = _basis(request, "industry")
    ctx["sub"] = "pi"
    if ctx["heeft_pi"]:
        ctx.update(data.pi(request.user))
    return render(request, "mijndashboard/pi.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
def industry(request: WSGIRequest, sub: str = "jobs") -> HttpResponse:
    """Industrie, met vier sub-tabbladen in de pagina zelf."""
    ctx = _basis(request, "industry")
    ctx["heeft_jobs"] = esi.has_token(_character_ids(request.user), esi.JOBS_SCOPE)
    ctx["heeft_bp"] = esi.has_token(_character_ids(request.user), esi.BLUEPRINTS_SCOPE)
    if sub == "bouwproject":
        # Het project staat in de URL, dus deelbaar; onzin negeren we stil.
        def _getal(naam, standaard, minimum, maximum):
            try:
                return max(minimum, min(int(request.GET.get(naam) or standaard), maximum))
            except ValueError:
                return standaard
        ctx.update(data.industrie(
            request.user, sub,
            type_id=_getal("type", 0, 0, 10 ** 9) or None,
            aantal=_getal("aantal", 1, 1, 10000),
            me=_getal("me", 10, 0, 10),
            zoek=(request.GET.get("q") or "").strip()[:60],
            koop=[int(x) for x in (request.GET.get("koop") or "").split(",")
                  if x.strip().isdigit()][:200],
            plek=_getal("plek", 0, 0, 10 ** 15) or None,
            ververs=request.GET.get("ververs") == "1"))
    else:
        ctx.update(data.industrie(request.user, sub))
    return render(request, "mijndashboard/industry.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
def markt(request: WSGIRequest) -> HttpResponse:
    """Je marktorders en hoe ze tegenover de concurrentie staan."""
    ctx = _basis(request, "markt")
    if ctx["heeft_markt"]:
        ctx.update(data.markt(request.user))
    return render(request, "mijndashboard/markt.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
def fleet(request: WSGIRequest) -> HttpResponse:
    """Fleetsessies verdelen, en de fleet zelf: wie zit erin, wie nodig je uit."""
    ctx = _basis(request, "fleet")
    if request.method == "POST":
        antwoord = _fleet_post(request)
        if antwoord is not None:
            return antwoord
    ctx["sub"] = "sessies"
    ctx["sessies"] = data.fleet_lijst(request.user)
    ctx["kandidaten"] = data.fleet_kandidaten()
    # Alleen voor het telletje op de sub-tab; de fleet zelf staat op Roam.
    fleet = data.fleet_paneel(request.user)
    ctx["in_fleet"] = fleet["in_fleet"]
    ctx["aantal"] = len(fleet["fleet_leden"])
    return render(request, "mijndashboard/fleet.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
def fleet_roam(request: WSGIRequest) -> HttpResponse:
    """De fleet zoals hij nu vliegt: samenstelling, waar iedereen staat, beheer."""
    if request.method == "POST":
        antwoord = _roam_post(request)
        if antwoord is not None:
            return antwoord

    ctx = _basis(request, "fleet")
    ctx["sub"] = "roam"
    # Het intel-kanaal staat in local.py als je een ander gebruikt; de chatlogs
    # worden in de browser gelezen, dus de server hoeft alleen de naam te weten.
    ctx["intel_kanaal"] = getattr(settings, "MIJNDASHBOARD_INTEL_KANAAL",
                                  "Insidious.Intel")
    ctx.update(data.fleet_roam(request.user))
    return render(request, "mijndashboard/fleet_roam.html", ctx)


def _roam_post(request):
    """De knoppen op de roam-pagina. Geeft altijd een redirect."""
    actie = request.POST.get("actie", "")

    if actie == "uitnodigen":
        wie = request.POST.getlist("uitnodigen")
        if not wie:
            messages.error(request, _("Niemand aangevinkt om uit te nodigen."))
            return redirect("mijndashboard:fleet_roam")
        gelukt, fouten = data.roam_uitnodigen(
            request.user, wie, request.POST.get("squad", ""))
        if gelukt:
            messages.success(request, ngettext(
                "%(n)d uitnodiging verstuurd — die staat nu als popup in het spel.",
                "%(n)d uitnodigingen verstuurd — die staan nu als popup in het spel.",
                gelukt) % {"n": gelukt})
        for fout in fouten:
            messages.error(request, fout)

    elif actie == "motd":
        ok, fout = data.roam_motd(request.user, request.POST.get("motd", ""))
        if ok:
            messages.success(request, _("MOTD aangepast."))
        else:
            messages.error(request, fout)

    elif actie == "schop":
        ok, fout = data.roam_schop(request.user, request.POST.get("character_id", 0))
        if ok:
            messages.success(request, _("Uit de fleet gezet."))
        else:
            messages.error(request, fout)

    return redirect("mijndashboard:fleet_roam")


def _fleet_post(request):
    """De drie knoppen op het tabblad. Geeft een redirect of None."""
    actie = request.POST.get("actie", "sessie")

    if actie == "uitnodigen":
        wie = request.POST.getlist("uitnodigen")
        if not wie:
            messages.error(request, _("Niemand aangevinkt om uit te nodigen."))
            return redirect("mijndashboard:fleet")
        gelukt, fouten = data.fleet_uitnodigen(request.user, wie)
        if gelukt:
            messages.success(request, ngettext(
                "%(n)d uitnodiging verstuurd — die staat nu als popup in het spel.",
                "%(n)d uitnodigingen verstuurd — die staan nu als popup in het spel.",
                gelukt) % {"n": gelukt})
        for fout in fouten:
            messages.error(request, fout)
        return redirect("mijndashboard:fleet")

    if actie == "sessie_uit_fleet":
        sessie, melding = data.fleet_sessie_uit_fleet(
            request.user, request.POST.get("naam", ""), request.POST.get("soort", ""))
        if not sessie:
            messages.error(request, melding)
            return redirect("mijndashboard:fleet")
        messages.success(request, _("Sessie gestart met iedereen in de fleet."))
        if melding:
            messages.warning(request, melding)
        return redirect("mijndashboard:fleet_sessie", sessie_id=sessie.id)

    sessie, fout = data.fleet_start(
        request.user,
        request.POST.get("naam", ""),
        request.POST.get("soort", ""),
        request.POST.getlist("deelnemers"),
    )
    if fout:
        messages.error(request, fout)
        return None
    messages.success(request, _("Sessie gestart — veel succes."))
    return redirect("mijndashboard:fleet_sessie", sessie_id=sessie.id)


@login_required
@permission_required("mijndashboard.basic_access")
def fleet_stand(request: WSGIRequest) -> JsonResponse:
    """De live stand van de fleet — de pagina haalt dit elke 15 seconden op."""
    return JsonResponse(data.roam_json(request.user))


@login_required
@permission_required("mijndashboard.basic_access")
def fleet_doe(request: WSGIRequest) -> JsonResponse:
    """Eén beheeractie: uitnodigen, kicken, verplaatsen, wings, MOTD."""
    if request.method != "POST":
        return JsonResponse({"ok": False, "melding": "POST verwacht."}, status=405)
    actie = request.POST.get("actie", "")

    if actie == "waypoint":
        ok, melding = data.roam_waypoint(request.user,
                                         request.POST.get("systeem_id") or 0,
                                         request.POST.get("modus", "set"))
    elif actie == "route":
        uitslag = data.roam_route(request.user, request.POST.get("systeem_id") or 0)
        return JsonResponse({"ok": not uitslag["fout"], "melding": uitslag["fout"],
                             "pad": uitslag["pad"], "jumps": uitslag.get("jumps", 0)})
    else:
        ok, melding = data.roam_actie(request.user, actie, request.POST)
    return JsonResponse({"ok": ok, "melding": melding})


@login_required
@permission_required("mijndashboard.basic_access")
def fleet_kaart(request: WSGIRequest) -> JsonResponse:
    """Alle systemen van New Eden met hun plek — één keer ophalen, dan cachen.

    Staat los van de pagina zelf omdat het 400 kB is: in de pagina zou het bij
    elke verversing opnieuw over de lijn gaan, als los bestand houdt de browser
    het vast.
    """
    kaart = dict(data.kaart_json())
    kaart["bruggen"] = data.jump_bridges()
    antwoord = JsonResponse(kaart)
    antwoord["Cache-Control"] = "private, max-age=3600"
    return antwoord


@login_required
@permission_required("mijndashboard.basic_access")
@token_required(scopes=FC_SCOPES)
def fleet_koppelen(request: WSGIRequest, token) -> HttpResponse:
    """Het character van de FC koppelen met de twee fleet-scopes."""
    messages.success(request, _(
        "%(naam)s is gekoppeld als FC. Maak de fleet in het spel; daarna staat "
        "hij hier.") % {"naam": token.character_name})
    return redirect("mijndashboard:fleet")


def _sessie_voor(user, sessie_id):
    """De sessie ophalen, maar alleen als deze gebruiker er iets mee te maken heeft.

    Een sessie gaat over andermans ISK, dus het id in de URL is niet genoeg: je
    moet hem gemaakt hebben of er zelf in staan.
    """
    from .models import Fleetsessie

    sessie = Fleetsessie.objects.filter(pk=sessie_id).first()
    if not sessie:
        return None
    if sessie.door_id == user.id:
        return sessie
    eigen = {c.character_id for c in esi.characters(user)}
    return sessie if eigen & set(sessie.deelnemers) else None


@login_required
@permission_required("mijndashboard.basic_access")
def fleet_sessie(request: WSGIRequest, sessie_id: int) -> HttpResponse:
    """Eén sessie: wie bracht wat in, en wie krijgt wat."""
    sessie = _sessie_voor(request.user, sessie_id)
    if not sessie:
        messages.error(request, _("Die sessie bestaat niet, of je doet er niet aan mee."))
        return redirect("mijndashboard:fleet")

    if request.method == "POST":
        if sessie.door_id != request.user.id:
            messages.error(request, _("Alleen wie de sessie startte kan hem beheren."))
        elif request.POST.get("actie") == "stop":
            data.fleet_stop(sessie)
            messages.success(request, _("Sessie gestopt — de eindstand staat vast."))
        elif request.POST.get("actie") == "verwijder":
            sessie.delete()
            messages.success(request, _("Sessie verwijderd."))
            return redirect("mijndashboard:fleet")
        return redirect("mijndashboard:fleet_sessie", sessie_id=sessie.id)

    ctx = _basis(request, "fleet")
    ctx.update(data.fleet_detail(request.user, sessie))
    return render(request, "mijndashboard/fleet_sessie.html", ctx)


def _antwoord(ctx, mail_id):
    """Het opstelvenster vast invullen als antwoord op een mail."""
    origineel = next((m for m in ctx.get("mails") or []
                      if str(m["id"]) == str(mail_id)), None)
    if not origineel:
        return None
    onderwerp = origineel["onderwerp"]
    if not onderwerp.lower().startswith("re:"):
        onderwerp = f"Re: {onderwerp}"
    # Versturen namens het character dat de mail kréég, als dat mag. Anders het
    # eerste character dat wél mag versturen — beter dan een leeg formulier.
    mag = {a["character_id"] for a in ctx.get("afzenders") or []}
    afzender = next((v["character_id"] for v in origineel["voor"]
                     if v["character_id"] in mag), None)
    if afzender is None:
        afzender = next(iter(mag), "")
    return {"afzender": str(afzender), "aan": origineel["afzender"],
            "onderwerp": onderwerp, "tekst": ""}


@login_required
@permission_required("mijndashboard.basic_access")
def mail(request: WSGIRequest) -> HttpResponse:
    """Je EVE-mail van al je characters bij elkaar, en versturen."""
    ctx = _basis(request, "mail")

    # Versturen eerst afhandelen, dan pas de pagina opbouwen: lukt het, dan
    # sturen we door naar een schone URL (anders verstuurt een verversing van de
    # pagina de mail nog een keer).
    uitslag = None
    if request.method == "POST" and ctx["heeft_mail"]:
        uitslag = data.verstuur_mail(request.user, request.POST)
        if uitslag["ok"]:
            messages.success(
                request,
                _("Mail verstuurd namens %(afzender)s aan %(n)s ontvanger(s): %(wie)s.")
                % {"afzender": uitslag["afzender"], "n": uitslag["aantal"],
                   "wie": ", ".join(uitslag["ontvangers"])})
            return redirect("mijndashboard:mail")
        messages.error(request, uitslag["fout"])

    if ctx["heeft_mail"]:
        # Filter en zoekterm staan in de URL, niet in de sessie: dan is een
        # zoekresultaat te delen en te bookmarken, en werkt de terugknop.
        ctx.update(data.mail(request.user,
                             vak=request.GET.get("vak") or "alles",
                             zoek=request.GET.get("q") or ""))

    if uitslag and not uitslag["ok"]:
        # Terug met wat er stond: opnieuw typen omdat één naam fout was is zonde.
        ctx["formulier"] = uitslag["formulier"]
        ctx["formulier_open"] = True
    elif request.GET.get("antwoord"):
        ctx["formulier"] = _antwoord(ctx, request.GET["antwoord"])
        ctx["formulier_open"] = bool(ctx["formulier"])
    return render(request, "mijndashboard/mail.html", ctx)


@login_required
@permission_required("mijndashboard.basic_access")
@token_required(scopes=SCOPES)
def koppelen(request: WSGIRequest, token) -> HttpResponse:
    """Character koppelen zodat we z'n wallet en contracten mogen lezen."""
    # De cache van dit character legen, anders blijft een eerder leeg antwoord
    # nog kwartieren hangen en lijkt het koppelen niet gewerkt te hebben.
    for sleutel in ("fin_bal", "fin_tx", "fin_contracts", "fin_mining", "fin_planets",
                    "fin_mailkop", "fin_maillabels", "fin_maillists",
                    "fin_orders", "fin_orderhist"):
        cache.delete(f"{sleutel}_{token.character_id}")
    cache.delete(f"fin_journal_{token.character_id}_{esi.JOURNAL_PAGES}")

    messages.success(
        request,
        _("%(naam)s is gekoppeld — je financiën worden nu getoond.")
        % {"naam": token.character_name},
    )
    return redirect("mijndashboard:wallet")
