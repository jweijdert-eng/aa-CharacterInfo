"""Views — Finance."""

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from esi.decorators import token_required

from . import __version__, data, esi

# Alle scopes tegelijk vragen: dan hoeft de gebruiker maar één keer te
# koppelen voor alle tabbladen.
SCOPES = [esi.WALLET_SCOPE, esi.CONTRACTS_SCOPE, esi.MINING_SCOPE,
          esi.PLANETS_SCOPE, esi.MAIL_SCOPE, esi.ORDERS_SCOPE,
          esi.SEND_MAIL_SCOPE]


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
            zoek=(request.GET.get("q") or "").strip()[:60]))
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
