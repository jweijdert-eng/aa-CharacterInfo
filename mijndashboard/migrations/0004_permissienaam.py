"""De omschrijving van de permissie ook echt in de database bijwerken.

`AlterModelOptions` (migratie 0002 en 0003) verandert alleen de migratiestaat.
De rij in `auth_permission` wordt aangemaakt door het `post_migrate`-signaal, en
dat **maakt** alleen ontbrekende permissies aan — hernoemen doet het niet. Op een
verse installatie klopt de tekst dus wel, maar op een bestaande bleef "Kan de
eigen financiën bekijken" staan, ook al zegt het model al twee versies iets
anders. Dit zet 'm alsnog goed.
"""

from django.db import migrations

NIEUW = "Kan de eigen EVE-gegevens bekijken"


def naam_bijwerken(apps, schema_editor):
    Permission = apps.get_model("auth", "Permission")
    ContentType = apps.get_model("contenttypes", "ContentType")
    ct = ContentType.objects.filter(app_label="mijndashboard", model="general").first()
    if ct:
        Permission.objects.filter(content_type=ct, codename="basic_access").update(name=NIEUW)


def terug(apps, schema_editor):
    """Niets doen: de oude tekst terugzetten heeft geen enkel nut."""


class Migration(migrations.Migration):

    dependencies = [
        ("mijndashboard", "0003_alter_general_options"),
        ("auth", "0001_initial"),
        ("contenttypes", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(naam_bijwerken, terug),
    ]
