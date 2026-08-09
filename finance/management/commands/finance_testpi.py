"""Testkolonies in de cache zetten om de PI-pagina met data te kunnen bekijken.

Waarom dit bestaat: de PI-pagina is niet tegen echte data te toetsen zolang er
geen kolonies zijn — ESI geeft dan een lege lijst en je ziet alleen de lege
staat. Dit commando vult dezelfde cachesleutel die `esi.planets()` leest, dus de
pagina doorloopt precies dezelfde weg als in het echt.

Alleen de cache wordt aangeraakt, nooit de database. Na het aflopen van de
bewaartijd (of met --wis) staat er vanzelf weer de echte ESI-data.

Let op: dit vult alleen de lijst met kolonies. De inrichting per planeet
(extractors, fabrieken, opslag) komt uit een tweede endpoint die deze verzonnen
kolonies niet kent, dus de kaarten blijven dan leeg op de kop na.

    python manage.py finance_testpi
    python manage.py finance_testpi --wis
"""

from datetime import datetime, timedelta, timezone

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError

from finance import esi

BEWAARTIJD = 6 * 3600

# Echte planeet-ids, opgehaald bij ESI, met het type dat ze werkelijk hebben.
# Verzonnen ids zouden als "#40267830" in beeld komen en dan toets je de
# naamopzoeking niet — juist dat ging eerder mis.
HB, N2, AM = 30004228, 30002945, 30002537

OPZET = [
    # (planeet_id, systeem_id, type, commandcenter-niveau, aantal pins)
    [(40267833, HB, "temperate", 5, 12),   # HB-5L3 III
     (40267869, HB, "temperate", 5, 12),   # HB-5L3 VIII
     (40267830, HB, "barren", 4, 8),       # HB-5L3 I
     (40267841, HB, "oceanic", 4, 9),      # HB-5L3 VI
     (40267837, HB, "gas", 3, 6),          # HB-5L3 V
     (40267872, HB, "temperate", 5, 11)],  # HB-5L3 IX
    [(40187005, N2, "temperate", 5, 13),   # N2IS-B III
     (40187004, N2, "lava", 5, 10),        # N2IS-B II
     (40187007, N2, "lava", 4, 10),        # N2IS-B IV
     (40187011, N2, "oceanic", 3, 7),      # N2IS-B VI
     (40187059, N2, "plasma", 2, 5)],      # N2IS-B XI
    [(40161469, AM, "barren", 4, 9),       # Amamake IV
     (40161467, AM, "plasma", 3, 6),       # Amamake III
     (40161476, AM, "gas", 1, 3)],         # Amamake VI
]


class Command(BaseCommand):
    help = "Zet testkolonies in de cache zodat de PI-pagina met data te zien is."

    def add_arguments(self, parser):
        parser.add_argument("--gebruiker", default=None,
                            help="Gebruikersnaam (standaard: de eerste met characters)")
        parser.add_argument("--wis", action="store_true",
                            help="Testdata weghalen; PI toont daarna weer echte ESI-data")

    def handle(self, *args, **opties):
        User = get_user_model()
        if opties["gebruiker"]:
            user = User.objects.filter(username=opties["gebruiker"]).first()
            if not user:
                raise CommandError(f"geen gebruiker {opties['gebruiker']!r}")
        else:
            user = (User.objects.filter(character_ownerships__isnull=False)
                    .distinct().order_by("id").first())
            if not user:
                raise CommandError("geen enkele gebruiker heeft een character")

        chars = esi.characters(user)
        self.stdout.write(f"{user.username} — {len(chars)} characters")

        if opties["wis"]:
            for c in chars:
                cache.delete(f"fin_planets_{c.character_id}")
            self.stdout.write(self.style.SUCCESS(
                "testdata weg — PI toont weer wat ESI echt teruggeeft"))
            return

        nu = datetime.now(timezone.utc)
        totaal = 0
        for index, c in enumerate(chars):
            kolonies = OPZET[index] if index < len(OPZET) else []
            rijen = [{
                "planet_id": pid,
                "solar_system_id": sid,
                "planet_type": soort,
                "upgrade_level": niveau,
                "num_pins": pins,
                "owner_id": c.character_id,
                # Oplopend ouder, zodat de kolom "bijgewerkt" iets laat zien.
                "last_update": (nu - timedelta(hours=2 + i * 5))
                .isoformat().replace("+00:00", "Z"),
            } for i, (pid, sid, soort, niveau, pins) in enumerate(kolonies)]

            # Ook de characters zonder kolonies expliciet vullen: anders gaat de
            # pagina voor hen alsnog live naar ESI en wordt het een mengelmoes.
            cache.set(f"fin_planets_{c.character_id}", rijen, BEWAARTIJD)
            totaal += len(rijen)
            if rijen:
                self.stdout.write(
                    f"  {c.character_name:<24} {len(rijen)} kolonies, "
                    f"{sum(r['num_pins'] for r in rijen)} pins")

        self.stdout.write(self.style.SUCCESS(
            f"{totaal} testkolonies, {BEWAARTIJD // 3600} uur geldig"))
        self.stdout.write("weghalen kan met:  manage.py finance_testpi --wis")
