# Character Info

Alliance Auth-plugin die je **eigen** gegevens op één plek toont: wallet, contracten en ratting-inkomsten.

> De interne naam is `finance` — app-label, URL (`/finance/`) en migraties hangen daaraan.
> Alleen wat je in de interface ziet heet Character Info.

Vijf tabbladen:

- **Wallet** — saldo per character, waar je geld de afgelopen 30 dagen vandaan kwam, en twee tabellen: **Transactions** (het journaal) en **Market Transactions** (wat je gekocht en verkocht hebt, per item)
- **Contracts** — je persoonlijke contracten met status, beloningen en de inhoud
- **Ratting** — bounty- en ESS-inkomsten, per dag uitgezet
- **Mining** — je ledger per ertssoort, systeem en dag, met volume en geschatte waarde
- **PI** — je planetaire kolonies per character

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
