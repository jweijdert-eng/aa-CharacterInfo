"""Hook into Alliance Auth — Mijn Dashboard.

Eén menu-item. De onderdelen (Wallet, Contracts, Ratting, Mining, PI, Market,
Mail) zitten als tabbladen in de pagina zelf, dus losse menu-items zouden
hetzelfde werk dubbel doen. `navactive` staat op de hele namespace, zodat het
menu-item opgelicht blijft welk tabblad je ook open hebt.

De klassenaam blijft `CharacterInfoMenuItem`: AA identificeert een menu-item met
`sha256(module.KlasseNaam)`, dus hernoemen zou een nieuw item aanmaken en de
volgorde en de instellingen van het oude kwijtraken. Alleen de tekst verandert,
en die werkt de sync vanzelf bij.
"""

from django.utils.translation import gettext_lazy as _

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from finance import urls


class CharacterInfoMenuItem(MenuItemHook):
    """Menu-item dat alleen zichtbaar is voor wie de plugin mag gebruiken.

    LET OP als je hier ooit meerdere items van maakt: AA bepaalt de identiteit
    van een menu-item met `sha256(f"{klasse.__module__}.{klasse.__name__}")`
    (`allianceauth/menu/core/menu_item_hooks.py`), dus niet met de tekst of de
    URL. Twee hooks die dezelfde klasse teruggeven worden tot één item
    samengevouwen — elk item heeft dan een eigen klasse nodig.
    """

    def __init__(self):
        MenuItemHook.__init__(
            self,
            _("Mijn Dashboard"),
            "fas fa-id-card fa-fw",
            "finance:index",
            order=1010,
            navactive=["finance:"],
        )

    def render(self, request):
        if request.user.has_perm("finance.basic_access"):
            return MenuItemHook.render(self, request)
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return CharacterInfoMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "finance", r"^finance/")
