"""Models — Finance (zichtbaar: Mijn Dashboard).

De plugin slaat zelf niets op: alles komt live uit ESI en gaat in de cache. Dit
model bestaat alleen om er een permissie aan te hangen.

Let op bij het wijzigen van de permissie-omschrijving: Django houdt Meta-opties
bij ook bij een `managed = False`-model, dus dat vraagt een migratie.
"""

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
