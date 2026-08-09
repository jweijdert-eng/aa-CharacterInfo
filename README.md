# Character Info

Alliance Auth-plugin die je **eigen** gegevens op één plek toont: wallet, contracten en ratting-inkomsten.

> De interne naam is `finance` — app-label, URL (`/finance/`) en migraties hangen daaraan.
> Alleen wat je in de interface ziet heet Character Info.

Vijf tabbladen:

- **Wallet** — saldo per character, je resultaat per dag over 30 dagen, waar je geld vandaan kwam tegenover
  waar het heen ging, en twee tabellen: **Transactions** (het journaal) en **Market Transactions** (wat je
  gekocht en verkocht hebt, per item)
- **Contracts** — je persoonlijke contracten met status, beloningen en de inhoud, plus de route van elke
  koeriersrit (volume, onderpand en ISK/m³), een overzicht per route en per tegenpartij
- **Ratting** — bounty- en ESS-inkomsten per dag, plus in welke systemen je ze verdiende, hoeveel corp-belasting
  eraf ging (het journaal boekt netto weg) en welke rats je precies afgeschoten hebt — dat laatste staat in het
  `reason`-veld van elke bounty-regel
- **Mining** — per erts een kaart (varianten als Pyroxeres II/III-Grade staan bij elkaar) met volume, wat het
  ruw opbrengt tegenover een perfecte refine, en welke mineralen eruit komen. Bovenaan het verschil tussen ruw
  verkopen en refinen, daaronder wat je hele voorraad na refinen zou opleveren, plus per character, per systeem
  en de ledger zelf
- **PI** — je planetaire kolonies als kaarten: wat elke extractor haalt en hoe lang z'n programma nog loopt,
  welke fabrieken draaien en wat ze per dag opleveren, hoe vol de opslag zit en wat erin ligt. Bovenaan staat
  wat aandacht vraagt (stilstaande extractor, volle opslag, grondstof die opraakt), en onderaan wat je account
  netto per dag maakt — eigen verbruik telt niet mee, zodat een halffabrikaat niet dubbel geteld wordt.

## Installeren

```
pip install git+https://github.com/jweijdert-eng/aa-Finance.git
```

Voeg `finance` toe aan `INSTALLED_APPS`, draai `migrate` en `collectstatic`, en herstart web + worker.

## Menu

Eén menu-item **Character Info**. De drie onderdelen zitten als tabbladen in de pagina zelf, dus je hoeft
niets in de admin in te richten.

## Scopes

- `esi-wallet.read_character_wallet.v1` — saldo, journaal, ratting
- `esi-contracts.read_character_contracts.v1` — contracten
- `esi-industry.read_character_mining.v1` — mining-ledger
- `esi-planets.manage_planets.v1` — planetaire kolonies

Beide worden in één keer gevraagd bij het koppelen.

## Permissie

`finance.basic_access` — mag de eigen financiën bekijken.
