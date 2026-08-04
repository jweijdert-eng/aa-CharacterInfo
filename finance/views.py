"""Views — Finance."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.core.handlers.wsgi import WSGIRequest
from django.http import HttpResponse
from django.shortcuts import redirect, render
from django.utils.translation import gettext_lazy as _

from esi.decorators import token_required

from . import data, esi

# Beide scopes tegelijk vragen: dan hoeft de gebruiker maar één keer te
# koppelen voor alle drie de tabbladen.
SCOPES = [esi.WALLET_SCOPE, esi.CONTRACTS_SCOPE]


def _character_ids(user):
    return [c.character_id for c in esi.characters(user)]


def _basis(request, actief):
    """Gedeelde context: welk tabblad actief is en of er iets gekoppeld is."""
    ids = _character_ids(request.user)
    return {
        "actief": actief,
        "heeft_wallet": esi.has_token(ids, esi.WALLET_SCOPE),
        "heeft_contracts": esi.has_token(ids, esi.CONTRACTS_SCOPE),
    }


@login_required
@permission_required("finance.basic_access")
def wallet(request: WSGIRequest) -> HttpResponse:
    """Saldo per character plus het gecombineerde journaal."""
    ctx = _basis(request, "wallet")
    if ctx["heeft_wallet"]:
        ctx.update(data.wallet(request.user))
    return render(request, "finance/wallet.html", ctx)


@login_required
@permission_required("finance.basic_access")
def contracts(request: WSGIRequest) -> HttpResponse:
    """Je persoonlijke contracten."""
    ctx = _basis(request, "contracts")
    if ctx["heeft_contracts"]:
        ctx.update(data.contracten(request.user))
    return render(request, "finance/contracts.html", ctx)


@login_required
@permission_required("finance.basic_access")
def ratting(request: WSGIRequest) -> HttpResponse:
    """Bounty- en ESS-inkomsten."""
    ctx = _basis(request, "ratting")
    if ctx["heeft_wallet"]:
        ctx.update(data.ratting(request.user))
    return render(request, "finance/ratting.html", ctx)


@login_required
@permission_required("finance.basic_access")
@token_required(scopes=SCOPES)
def koppelen(request: WSGIRequest, token) -> HttpResponse:
    """Character koppelen zodat we z'n wallet en contracten mogen lezen."""
    # De cache van dit character legen, anders blijft een eerder leeg antwoord
    # nog kwartieren hangen en lijkt het koppelen niet gewerkt te hebben.
    for sleutel in ("fin_bal", "fin_tx", "fin_contracts"):
        cache.delete(f"{sleutel}_{token.character_id}")
    cache.delete(f"fin_journal_{token.character_id}_{esi.JOURNAL_PAGES}")

    messages.success(
        request,
        _("%(naam)s is gekoppeld — je financiën worden nu getoond.")
        % {"naam": token.character_name},
    )
    return redirect("finance:wallet")
