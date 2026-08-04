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
    """Menu-item dat alleen zichtbaar is voor wie de plugin mag gebruiken."""

    def __init__(self, tekst, icoon, url_name, order):
        MenuItemHook.__init__(self, tekst, icoon, url_name,
                              order=order, navactive=[url_name])

    def render(self, request):
        if request.user.has_perm("finance.basic_access"):
            return MenuItemHook.render(self, request)
        return ""


@hooks.register("menu_item_hook")
def register_wallet():
    return _FinanceMenuItem(_("Wallet"), "fas fa-wallet fa-fw", "finance:wallet", 1010)


@hooks.register("menu_item_hook")
def register_contracts():
    return _FinanceMenuItem(_("Contracts"), "fas fa-file-signature fa-fw",
                            "finance:contracts", 1011)


@hooks.register("menu_item_hook")
def register_ratting():
    return _FinanceMenuItem(_("Ratting"), "fas fa-crosshairs fa-fw",
                            "finance:ratting", 1012)


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "finance", r"^finance/")
