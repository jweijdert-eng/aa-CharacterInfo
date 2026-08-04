# Finance

Alliance Auth-plugin die je **eigen** financiën op één plek toont: wallet, contracten en ratting-inkomsten.

Drie tabbladen:

- **Wallet** — saldo per character, waar je geld de afgelopen 30 dagen vandaan kwam, en het gecombineerde journaal
- **Contracts** — je persoonlijke contracten met status en beloningen
- **Ratting** — bounty- en ESS-inkomsten, per dag uitgezet

## Installeren

```
pip install git+https://github.com/jweijdert-eng/aa-Finance.git
```

Voeg `finance` toe aan `INSTALLED_APPS`, draai `migrate` en `collectstatic`, en herstart web + worker.

## Menu

De plugin registreert drie losse menu-items. Wil je ze onder één kopje **Finance**, maak dan in de
admin een menu-map aan (Menu → toevoegen, zonder URL) en zet de drie items daarin. Dat blijft staan:
de hook-synchronisatie werkt alleen tekst en volgorde bij.

## Scopes

- `esi-wallet.read_character_wallet.v1` — saldo, journaal, ratting
- `esi-contracts.read_character_contracts.v1` — contracten

Beide worden in één keer gevraagd bij het koppelen.

## Permissie

`finance.basic_access` — mag de eigen financiën bekijken.
