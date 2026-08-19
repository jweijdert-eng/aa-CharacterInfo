"""Hook into Alliance Auth — Mijn Dashboard.

Eén menu-item. De onderdelen (Wallet, Contracts, Ratting, Mining, PI, Market,
Mail) zitten als tabbladen in de pagina zelf, dus losse menu-items zouden
hetzelfde werk dubbel doen. `navactive` staat op de hele namespace, zodat het
menu-item opgelicht blijft welk tabblad je ook open hebt.

AA identificeert een menu-item met `sha256(module.KlasseNaam)`. Bij het omdopen
van de package veranderde dat modulepad toch al, dus is de klasse in v3.0.0
meteen meehernoemd; AA ruimt het oude item zelf op (`_delete_obsolete_app_items`)
en maakt een nieuw aan met de `order` uit deze hook. Bij een *losse*
naamswijziging is hernoemen juist géén goed idee: dan raak je de handmatige
volgorde en instellingen van het bestaande item kwijt.
"""

from django.utils.translation import gettext_lazy as _

from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook

from mijndashboard import urls


class MijnDashboardMenuItem(MenuItemHook):
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
            "mijndashboard:index",
            order=1010,
            navactive=["mijndashboard:"],
        )

    def render(self, request):
        if request.user.has_perm("mijndashboard.basic_access"):
            return MenuItemHook.render(self, request)
        return ""


@hooks.register("menu_item_hook")
def register_menu():
    return MijnDashboardMenuItem()


@hooks.register("url_hook")
def register_urls():
    return UrlHook(urls, "mijndashboard", r"^mijndashboard/")
