# Mijn Dashboard

Alliance Auth-plugin die je **eigen** gegevens op één plek toont: wallet, contracten, ratting, mining, PI, markt en mail.

> De interne naam is `finance` — app-label, URL (`/finance/`), pip-naam en migraties hangen daaraan.
> Alleen wat je in de interface ziet heet Mijn Dashboard. De plugin heette eerder Finance en daarna Character
> Info; die laatste botste in het menu met **Character Scan**, dat over een heel ander soort character gaat.

Zeven tabbladen:

- **Wallet** — saldo per character, je resultaat per dag over 30 dagen, waar je geld vandaan kwam tegenover
  waar het heen ging, en twee tabellen: **Transactions** (het journaal) en **Market Transactions** (wat je
  gekocht en verkocht hebt, per item)
- **Contracts** — je persoonlijke contracten met status, beloningen en de inhoud, plus de route van elke
  koeriersrit (volume, onderpand en ISK/m³), een overzicht per route en per tegenpartij
- **Ratting** — bounty- en ESS-inkomsten per dag, plus in welke systemen je ze verdiende, hoeveel corp-belasting
  eraf ging (het journaal boekt netto weg) en welke rats je precies afgeschoten hebt — dat laatste staat in het
  `reason`-veld van elke bounty-regel
- **Mining** — per erts een kaart (varianten als Pyroxeres II/III-Grade staan bij elkaar) met volume,
  **ISK per m³**, wat het ruw opbrengt tegenover refinen, en welke mineralen eruit komen. Het
  **refine-rendement is instelbaar** (50–100%, standaard 80): een volledige refine bestaat in het spel niet, en
  bij een realistisch rendement kantelt het advies voor sommige ertsen. Bovenaan het verschil tussen ruw
  verkopen en refinen, en wat refinen met je vrachtvolume doet; daaronder wat je hele voorraad zou opleveren,
  plus per character, per systeem en de ledger zelf
- **PI** — je planetaire kolonies als kaarten: wat elke extractor haalt en hoe lang z'n programma nog loopt,
  welke fabrieken draaien en wat ze per dag opleveren, hoe vol de opslag zit en wat erin ligt. Bovenaan staat
  wat aandacht vraagt (stilstaande extractor, volle opslag, grondstof die opraakt), en onderaan wat je account
  netto per dag maakt — eigen verbruik telt niet mee, zodat een halffabrikaat niet dubbel geteld wordt.
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

## Installeren

```
pip install git+https://github.com/jweijdert-eng/aa-CharacterInfo.git
```

Voeg `finance` toe aan `INSTALLED_APPS`, draai `migrate` en `collectstatic`, en herstart web + worker.

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

Ze worden in één keer gevraagd bij het koppelen.

Twee scopes worden **niet** gevraagd maar wel gebruikt als een van je characters ze al heeft — zonder valt
alleen dat stukje weg:

- `esi-universe.read_structures.v1` — namen van spelersstructuren
- `esi-markets.structure_markets.v1` — het orderboek van een spelersstructuur, voor de vergelijking met de
  concurrentie op die markt

## Permissie

`finance.basic_access` — mag de eigen financiën bekijken.
