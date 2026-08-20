# Mijn Dashboard

Alliance Auth-plugin die je **eigen** gegevens op één plek toont: wallet, contracten, ratting, mining, PI, markt en mail.

> De plugin heette eerder **Finance** en daarna **Character Info**. Sinds **v3.0.0** heet ook de binnenkant
> `mijndashboard`: package, app-label, URL (`/mijndashboard/`) en pip-naam (`aa-mijndashboard`).

## Bijwerken van v2.x naar v3.0.0

De omdoping raakt de database, dus dit gaat niet vanzelf. De plugin heeft geen eigen tabellen
(`managed = False`), dus er is niets te verhuizen — alleen het app-label staat op twee plekken.

1. Zet de site stil (web + worker). Een menu-item dat naar een verdwenen hook wijst laat het hele
   dashboard omvallen, dus je wil niet halverwege blijven hangen.
2. `pip uninstall aa-finance` en installeer deze versie.
3. In `local.py`: `'finance'` → `'mijndashboard'` in `INSTALLED_APPS`.
4. Werk het app-label bij en gooi het oude menu-item weg:

   ```sql
   UPDATE django_migrations   SET app       = 'mijndashboard' WHERE app       = 'finance';
   UPDATE django_content_type SET app_label = 'mijndashboard' WHERE app_label = 'finance';
   DELETE FROM menu_menuitem
    WHERE hook_hash = SHA2('finance.auth_hooks.CharacterInfoMenuItem', 256);
   ```

   Het content type hernoemen in plaats van weggooien is belangrijk: de permissie `basic_access` hangt
   eraan, en daarmee iedereen aan wie je hem gegeven hebt.
5. `manage.py migrate` (0004 zet de omschrijving van de permissie goed), `manage.py collectstatic`,
   en start web + worker weer.

Let op: de URL verandert van `/finance/` naar `/mijndashboard/`, dus oude bladwijzers werken niet meer.
Wie de rechten per groep heeft ingesteld hoeft niets te doen; de permissie heet nu
`mijndashboard.basic_access` en houdt dezelfde toewijzingen.

Negen tabbladen:

- **Dashboard** — de voorpagina, **nagebouwd naar de startpagina van**
  [dutchlegionsdashboard.eu](https://dutchlegionsdashboard.eu/): dezelfde banner (waar je main is, waarin,
  ingelogd of niet, en het saldo), dezelfde kaartjes per character, dezelfde vier stat-kaarten, dezelfde acht
  widgets (skill queue, industry jobs, market orders, recente transacties, netto waarde, kill statistieken,
  inkomstenverdeling, aankomend), de in-game agenda, de grafiekrij (wallet / ratten-ESS / mining) en de tabel
  met recente kills & losses. Ook het palet, de maatvoering en de ISK-notatie (3.21B) komen van daar. Wat niet
  meekomt: widgets verslepen en per widget bijladen — dat is JavaScript-werk.
- **Wallet** — saldo per character, je resultaat per dag over 30 dagen, waar je geld vandaan kwam tegenover
  waar het heen ging, en twee tabellen: **Transactions** (het journaal) en **Market Transactions** (wat je
  gekocht en verkocht hebt, per item)
- **Contracts** — je persoonlijke contracten met status, beloningen en de inhoud, plus de route van elke
  koeriersrit (volume, onderpand en ISK/m³), een overzicht per route en per tegenpartij
- **Ratting** — bounty- en ESS-inkomsten per dag, plus in welke systemen je ze verdiende, hoeveel corp-belasting
  eraf ging (het journaal boekt netto weg) en welke rats je precies afgeschoten hebt — dat laatste staat in het
  `reason`-veld van elke bounty-regel
- **Mining** — per erts een kaart (varianten als Pyroxeres II/III-Grade staan bij elkaar) met volume,
  **ISK per m³**, wat het ruw opbrengt tegenover refinen, en welke mineralen eruit komen. Er wordt met een
  **rendement van 80%** gerekend — een volledige refine bestaat in het spel niet, en bij een realistisch
  rendement kantelt het advies voor sommige ertsen. Een andere stand kan met `?refine=` in de URL. Bovenaan het verschil tussen ruw
  verkopen en refinen, en wat refinen met je vrachtvolume doet; daaronder wat je hele voorraad zou opleveren,
  plus per character, per systeem en de ledger zelf
- **Market** — je openstaande orders met bij elke order je **plek op die markt**: rang, de beste prijs en
  hoeveel je ernaast zit. Vergeleken binnen hetzelfde systeem (of dezelfde structuur), want kopen kan alleen
  waar de order ligt. Verder wat er per markt uitstaat, en uit de orderhistorie van 90 dagen: hoeveel orders
  helemaal gevuld raakten tegenover blijven liggen, en per artikel wat je ervoor betaalde, wat het opbracht
  en wat de marge was. Broker fee en sales tax staan er als aparte post bij.
- **Mail** — de mailbox van al je characters op één hoop en **doorzoekbaar op tekst**, niet alleen op onderwerp.
  Dezelfde corp-mail komt in elke mailbox binnen, dus mails worden ontdubbeld en tonen bij wie ze binnenkwamen.
  De opmaak van de EVE-client (kleuren, in-game links) wordt omgezet naar veilige HTML, waarbij kleuren die op
  deze achtergrond onleesbaar zijn worden opgehelderd. **Fitting-links worden uitgeschreven**: schip, modules
  per slot en een EFT-blok dat je zo in het spel plakt. En je kunt **mail versturen en beantwoorden**:
  ontvangers typ je op naam (character, corporatie, alliantie of mailinglijst), de rest zoekt de plugin op.

- **Local** — de local-chat van je EVE-client, met de namen gekleurd op standing: je eigen characters en
  corpgenoten groen, je alliance lichtblauw, contacten met een positieve standing groen, en al het andere
  **rood** — onbekend in local is een risico, geen neutraal gegeven. Rechtermuisknop op een naam voor een
  eigen oordeel (blijft in je browser staan). Word je bij naam genoemd, dan licht de regel goud op.

  De logs worden **door je browser gelezen**, niet door de server: EVE schrijft ze op jouw pc en een
  Auth-server komt daar niet bij. Je wijst je map `Documents/EVE/logs/Chatlogs` eenmalig aan (Chrome, Edge of
  Opera; de keuze gaat in IndexedDB) en het paneel volgt daarna het nieuwste `Local_*.txt`. De server doet
  alleen de standings, want daar zijn tokens voor nodig.

## Installeren

```
pip install git+https://github.com/jweijdert-eng/aa-CharacterInfo.git
```

Voeg `mijndashboard` toe aan `INSTALLED_APPS`, draai `migrate` en `collectstatic`, en herstart web + worker.

## Menu

Eén menu-item **Mijn Dashboard**. De onderdelen zitten als tabbladen in de pagina zelf, dus je hoeft
niets in de admin in te richten.

## Scopes

- `esi-wallet.read_character_wallet.v1` — saldo, journaal, ratting
- `esi-contracts.read_character_contracts.v1` — contracten
- `esi-industry.read_character_mining.v1` — mining-ledger
- `esi-planets.manage_planets.v1` — planetaire kolonies
- `esi-mail.read_mail.v1` — mail lezen
- `esi-mail.send_mail.v1` — mail versturen; alleen characters die deze toestemming hebben staan in het
  opstelvenster als afzender
- `esi-markets.read_character_orders.v1` — je marktorders
- `esi-characters.read_blueprints.v1` — je blueprints (Industry)

Ze worden in één keer gevraagd bij het koppelen.

Deze worden **niet** gevraagd maar wel gebruikt als een van je characters ze al heeft — zonder valt alleen dat
stukje weg:

- `esi-universe.read_structures.v1` — namen van spelersstructuren
- `esi-markets.structure_markets.v1` — het orderboek van een spelersstructuur, voor de vergelijking met de
  concurrentie op die markt
- `esi-location.read_location.v1`, `esi-location.read_ship_type.v1`, `esi-location.read_online.v1` — waar je
  bent, waarin, en of je ingelogd bent (dashboard)
- `esi-skills.read_skillqueue.v1` — skill queue (dashboard)
- `esi-industry.read_character_jobs.v1` — industry jobs (dashboard)
- `esi-calendar.read_calendar_events.v1` — in-game agenda (dashboard)
- `esi-characters.read_contacts.v1`, `esi-corporations.read_contacts.v1`, `esi-alliances.read_contacts.v1` —
  standings voor het kleuren van local

Wie via CharLink of Member Audit gekoppeld heeft, heeft die meestal al.

## Fleetsessies

Het tabblad **Fleet** verdeelt de opbrengst van een gezamenlijke mining- of ratting-run.

Een fleet **aanmaken** kan niet vanuit hier: ESI heeft daar geen endpoint voor. Je vormt de fleet in het
spel; deze pagina gaat over wie wat inbracht en wie wat krijgt.

Zo werkt het:

1. Start de sessie **voordat** je begint, geef hem een naam en vink de deelnemers aan. Deelnemers komen uit
   Alliance Auth zelf — iedereen met een gekoppeld character staat in de lijst, dus niemand hoeft zich apart
   aan te melden en de fleet-scope is niet nodig.
2. Stop de sessie als je klaar bent. De opbrengst gaat gelijk over alle deelnemers; de laatste kolom laat
   zien wat er van wie naar wie moet.

**Mining** meet met momentopnames: de ledger bij de start, de ledger bij het eind, en het verschil is van
deze sessie. Dat moet zo, want de mining-ledger van EVE telt per **dag** — daar is achteraf geen uur uit te
knippen, en een sessie achteraf aanmaken kan dus ook niet. EVE werkt de ledger ongeveer elke tien minuten
bij, dus het laatste kwartier van een lopende sessie kan nog ontbreken. Waarde tegen Jita buy.

**Ratting** heeft dat niet nodig: het wallet-journaal heeft een tijdstempel per regel, dus daar telt gewoon
het tijdvak van de sessie. Bounty en ESS staan er netto in, dus na corp-belasting.

Een sessie is zichtbaar voor wie hem startte en voor de deelnemers; stoppen en verwijderen mag alleen wie hem
startte. Dit is het enige onderdeel van de plugin dat iets in de database bewaart (tabel
`mijndashboard_fleetsessie`).

## Permissie

`mijndashboard.basic_access` — mag de eigen EVE-gegevens bekijken.
