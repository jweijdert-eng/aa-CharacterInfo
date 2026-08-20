"""Models — Finance (zichtbaar: Mijn Dashboard).

De plugin haalt alles live uit ESI en bewaart niets — op één ding na: een
**fleetsessie**. Die kán niet uit ESI komen, want ESI weet niet dat vijf mensen
tussen 20:00 en 22:00 samen aan het mijnen waren. Dat is een afspraak tussen
mensen, en die moet dus ergens staan.

Let op bij het wijzigen van de permissie-omschrijving: Django houdt Meta-opties
bij ook bij een `managed = False`-model, dus dat vraagt een migratie.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _


class General(models.Model):
    """Meta-model voor permissies."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", _("Kan de eigen EVE-gegevens bekijken")),
        )


class Fleetsessie(models.Model):
    """Een gezamenlijke mining- of ratting-sessie, om de opbrengst te verdelen.

    **Waarom er momentopnames in staan.** Voor ratting is dat niet nodig: het
    wallet-journaal heeft een tijdstempel per regel, dus daar volstaat het
    tijdvak. De mining-ledger van ESI vat per **dag** samen — daar kun je geen
    uur uit knippen. Daarom nemen we bij de start en bij het eind een stand op;
    het verschil is wat er in de sessie gemijnd is. Dat betekent ook dat een
    sessie vooraf gestart moet worden, niet achteraf aangemaakt.
    """

    MINING = "mining"
    RATTING = "ratting"
    SOORTEN = ((MINING, _("Mining")), (RATTING, _("Ratting")))

    naam = models.CharField(max_length=100, verbose_name=_("Naam"))
    soort = models.CharField(max_length=10, choices=SOORTEN, default=MINING,
                             verbose_name=_("Soort"))
    door = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="fleetsessies", verbose_name=_("Aangemaakt door"))
    gestart = models.DateTimeField(auto_now_add=True, verbose_name=_("Gestart"))
    gestopt = models.DateTimeField(null=True, blank=True, verbose_name=_("Gestopt"))

    deelnemers = models.JSONField(default=list, blank=True,
                                  verbose_name=_("Deelnemers"),
                                  help_text=_("character-ids van iedereen die meedoet"))

    # {character_id: {sleutel: aantal}} bij de start en bij het eind.
    begin = models.JSONField(default=dict, blank=True, verbose_name=_("Beginstand"))
    eind = models.JSONField(default=dict, blank=True, verbose_name=_("Eindstand"))

    class Meta:
        verbose_name = _("fleetsessie")
        verbose_name_plural = _("fleetsessies")
        ordering = ("-gestart",)

    def __str__(self):
        return f"{self.naam} ({self.get_soort_display()})"

    @property
    def loopt(self):
        return self.gestopt is None
