"""Hook into Alliance Auth — Finance.

Drie losse menu-items in plaats van één. AA 5.2 kent mappen in het zijmenu: een
map is een menu-item zonder hook en zonder URL, dat je zelf in de admin aanmaakt
(Menu → toevoegen). Zet je deze drie items daarna in een map "Finance", dan
blijft dat staan — de hook-synchronisatie werkt alleen `text` en `order` bij en
laat `parent` met rust.
"""

from django.utils.translation import gettext_lazy as _

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from finance import urls


class _FinanceMenuItem(MenuItemHook):
    """Basis voor onze menu-items: alleen tonen aan wie de plugin mag gebruiken.

    LET OP — elk menu-item moet een **eigen klasse** zijn. AA bepaalt de identiteit
    van een item met `sha256(f"{klasse.__module__}.{klasse.__name__}")`
    (`allianceauth/menu/core/menu_item_hooks.py`), dus niet met de tekst of de URL.
    Drie hooks die dezelfde klasse teruggeven krijgen dezelfde hash en worden dan
    tot één menu-item samengevouwen.
    """

    tekst = ""
    icoon = ""
    doel = ""
    volgorde = 1010

    def __init__(self):
        MenuItemHook.__init__(self, self.tekst, self.icoon, self.doel,
                              order=self.volgorde, navactive=[self.doel])

    def render(self, request):
        if request.user.has_perm("finance.basic_access"):
            return MenuItemHook.render(self, request)
        return ""


class WalletMenuItem(_FinanceMenuItem):
    tekst = _("Wallet")
    icoon = "fas fa-wallet fa-fw"
    doel = "finance:wallet"
    volgorde = 1010


class ContractsMenuItem(_FinanceMenuItem):
    tekst = _("Contracts")
    icoon = "fas fa-file-signature fa-fw"
    doel = "finance:contracts"
    volgorde = 1011


class RattingMenuItem(_FinanceMenuItem):
    tekst = _("Ratting")
    icoon = "fas fa-crosshairs fa-fw"
    doel = "finance:ratting"
    volgorde = 1012


@hooks.register("menu_item_hook")
def register_wallet():
    return WalletMenuItem()


@hooks.register("menu_item_hook")
def register_contracts():
    return ContractsMenuItem()


@hooks.register("menu_item_hook")
def register_ratting():
    return RattingMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "finance", r"^finance/")
