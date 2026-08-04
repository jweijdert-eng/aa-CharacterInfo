"""Models — Finance.

De plugin slaat zelf niets op: alles komt live uit ESI en gaat in de cache. Dit
model bestaat alleen om er een permissie aan te hangen.
"""

from django.db import models
from django.utils.translation import gettext_lazy as _


class General(models.Model):
    """Meta-model voor permissies."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = (
            ("basic_access", _("Kan de eigen financiën bekijken")),
        )
