"""ESI-laag — Finance.

Leest per character z'n eigen wallet en contracten. Platte `requests` in plaats
van de swagger-client: sneller en voorspelbaarder, zelfde patroon als
aa-corp-hauling. Alles gecached, want een wallet-journaal van vijf pagina's per
character is te duur om bij elke paginaweergave opnieuw op te halen.
"""

import logging
import time

import requests
from django.core.cache import cache

logger = logging.getLogger(__name__)

ESI = "https://esi.evetech.net/latest"
UA = {"User-Agent": "aa-finance (Alliance Auth plugin; maintainer: Dutch Legions)"}

WALLET_SCOPE = "esi-wallet.read_character_wallet.v1"
CONTRACTS_SCOPE = "esi-contracts.read_character_contracts.v1"
MINING_SCOPE = "esi-industry.read_character_mining.v1"
PLANETS_SCOPE = "esi-planets.manage_planets.v1"
# Niet gevraagd bij het koppelen: spelersstructuren zijn mooi meegenomen, maar
# er een herkoppeling voor afdwingen is het niet waard. Bijna elk account heeft
# dit token al liggen van een andere plugin — dan gebruiken we dat.
STRUCTURES_SCOPE = "esi-universe.read_structures.v1"

TTL_BALANCE = 300           # saldo verandert vaak, maar niet elke seconde
TTL_JOURNAL = 900           # journaal-regels zijn onveranderlijk zodra ze er staan
TTL_CONTRACTS = 600
TTL_MINING = 1800           # de ledger vat per dag samen, dus dit hoeft niet vers
TTL_PLANETS = 900           # extractors lopen af, dus niet te lang vasthouden
JOURNAL_PAGES = 5           # ESI geeft 1000 regels per pagina, 5 is ruim een maand

# Statussen waarbij opnieuw proberen zin heeft: foutlimiet, rate limit, storing.
RETRY_STATUS = {420, 429, 500, 502, 503, 504}
MAX_TRIES = 4

# Eén sessie voor het hele proces: hergebruik van TLS-verbindingen in plaats van
# er honderden opzetten (scheelt tijd en voorkomt poortuitputting op Windows).
_session = requests.Session()
_session.mount("https://", requests.adapters.HTTPAdapter(
    pool_connections=8, pool_maxsize=8, max_retries=0,
))


def _request_met_headers(path, token, params=None):
    """Eén ESI-call met backoff. Geeft (data, headers) of (None, {})."""
    headers = {**UA, "Authorization": f"Bearer {token}"}
    for poging in range(1, MAX_TRIES + 1):
        try:
            r = _session.get(f"{ESI}{path}", headers=headers,
                             params={"datasource": "tranquility", **(params or {})},
                             timeout=20)
        except requests.RequestException as exc:
            logger.info("Finance: %s mislukt (poging %s): %s", path, poging, exc)
            time.sleep(min(2 ** poging * 0.25, 4))
            continue

        if r.status_code == 200:
            resterend = r.headers.get("X-Esi-Error-Limit-Remain")
            if resterend is not None and int(resterend) < 10:
                wachten = int(r.headers.get("X-Esi-Error-Limit-Reset", 5))
                logger.warning("Finance: ESI-foutlimiet bijna op (%s over) — %ss wachten",
                               resterend, wachten)
                time.sleep(min(wachten, 10))
            try:
                return r.json(), r.headers
            except ValueError:
                return None, r.headers

        if r.status_code in RETRY_STATUS and poging < MAX_TRIES:
            time.sleep(int(r.headers.get("Retry-After", 0)) or min(2 ** poging * 0.5, 8))
            continue

        logger.info("Finance: %s gaf %s", path, r.status_code)
        return None, r.headers
    return None, {}


def _request(path, token, params=None):
    """Eén ESI-call met backoff. Geeft de data of None."""
    data, _ = _request_met_headers(path, token, params)
    return data


def _paged(path, token, params=None, max_pages=20):
    """Alle pagina's van een gepagineerde endpoint.

    **Niet** stoppen zodra een pagina korter is dan 1000 regels. Dat leek een
    veilige aanname — een volle pagina is 1000, dus korter is de laatste — maar
    ESI vult een pagina niet altijd helemaal: de assets van één character gaven
    999 regels op pagina 1 van 3. Met de lengte als stopteken verlies je dan
    stilzwijgend tweederde van de gegevens, zonder fout en zonder waarschuwing.
    Het echte aantal pagina's staat in de **X-Pages**-header.
    """
    eerste, headers = _request_met_headers(path, token, {**(params or {}), "page": 1})
    if not eerste:
        return []
    rijen = list(eerste)
    try:
        paginas = int(headers.get("X-Pages") or 1)
    except (TypeError, ValueError):
        paginas = 1
    for p in range(2, min(paginas, max_pages) + 1):
        blok = _request(path, token, {**(params or {}), "page": p})
        if not blok:
            break
        rijen.extend(blok)
    return rijen


# --------------------------------------------------------------------------
# Characters en tokens
# --------------------------------------------------------------------------

def characters(user):
    """De EveCharacters van een gebruiker, main eerst."""
    try:
        from allianceauth.eveonline.models import EveCharacter

        qs = list(EveCharacter.objects.filter(character_ownership__user=user))
        main = getattr(getattr(user, "profile", None), "main_character", None)
        if main:
            qs.sort(key=lambda c: c.character_id != main.character_id)
        return qs
    except Exception:  # noqa: BLE001
        return []


def token_for(character_id, scope):
    """Een geldig token van dít character met deze scope, of None."""
    from esi.models import Token

    for token in (Token.objects
                  .filter(character_id=character_id, scopes__name=scope)
                  .order_by("-created")):
        try:
            return token.valid_access_token()
        except Exception:  # noqa: BLE001 — verlopen of ingetrokken
            continue
    return None


def _tokens_met_scope(character_ids, scope):
    """Alle geldige tokens van deze characters met deze scope, nieuwste eerst."""
    from esi.models import Token

    uit = []
    for token in (Token.objects
                  .filter(character_id__in=list(character_ids), scopes__name=scope)
                  .order_by("-created")):
        try:
            uit.append(token.valid_access_token())
        except Exception:  # noqa: BLE001 — verlopen of ingetrokken
            continue
    return uit


def structure_names(structure_ids, character_ids):
    """Namen van spelersstructuren.

    Eén token is niet genoeg: `/universe/structures/{id}/` geeft **403** als dát
    character geen dockingrechten heeft. Dat is per structuur verschillend, dus
    we proberen ze allemaal tot er één werkt — de les uit aa-corp-hauling, waar
    alleen het eerste token geprobeerd werd en alles "Onbekende locatie" heette.

    Namen veranderen zelden, dus 7 dagen cache. Een mislukking cachen we kort
    (een uur), zodat een nieuw token of nieuwe dockingrechten vanzelf helpt.
    """
    uit, ontbreekt = {}, []
    for sid in set(structure_ids):
        hit = cache.get(f"fin_struct_{sid}")
        if hit is not None:
            uit[sid] = hit
        else:
            ontbreekt.append(sid)
    if not ontbreekt:
        return uit

    tokens = _tokens_met_scope(character_ids, STRUCTURES_SCOPE)
    for sid in ontbreekt:
        naam = ""
        for token in tokens:
            r = _request(f"/universe/structures/{sid}/", token)
            if r:
                naam = r.get("name") or ""
                break
        uit[sid] = naam
        cache.set(f"fin_struct_{sid}", naam, 7 * 86400 if naam else 3600)
    return uit


def has_token(character_ids, scope):
    """Of minstens één van deze characters een token met die scope heeft."""
    from esi.models import Token

    try:
        return Token.objects.filter(character_id__in=list(character_ids),
                                    scopes__name=scope).exists()
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------
# Wallet
# --------------------------------------------------------------------------

def balance(character_id):
    """Wallet-saldo in ISK, of None als we er niet bij kunnen."""
    key = f"fin_bal_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return None if hit == "geen" else hit

    token = token_for(character_id, WALLET_SCOPE)
    waarde = _request(f"/characters/{character_id}/wallet/", token) if token else None
    cache.set(key, "geen" if waarde is None else waarde, TTL_BALANCE)
    return waarde


def journal(character_id, pages=JOURNAL_PAGES):
    """Wallet-journaal van dit character (meerdere pagina's, gecached)."""
    key = f"fin_journal_{character_id}_{pages}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    token = token_for(character_id, WALLET_SCOPE)
    if not token:
        cache.set(key, [], TTL_JOURNAL)
        return []

    regels = _paged(f"/characters/{character_id}/wallet/journal/", token,
                    max_pages=pages)
    cache.set(key, regels, TTL_JOURNAL)
    return regels


def transactions(character_id):
    """Markttransacties van dit character (gecached)."""
    key = f"fin_tx_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    token = token_for(character_id, WALLET_SCOPE)
    rows = _request(f"/characters/{character_id}/wallet/transactions/", token) if token else None
    rows = rows or []
    cache.set(key, rows, TTL_JOURNAL)
    return rows


# --------------------------------------------------------------------------
# Contracten
# --------------------------------------------------------------------------

def contracts(character_id):
    """Persoonlijke contracten van dit character (gecached)."""
    key = f"fin_contracts_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    token = token_for(character_id, CONTRACTS_SCOPE)
    if not token:
        cache.set(key, [], TTL_CONTRACTS)
        return []

    rows = _paged(f"/characters/{character_id}/contracts/", token)
    cache.set(key, rows, TTL_CONTRACTS)
    return rows


def mining(character_id):
    """Mining-ledger van dit character (gepagineerd, gecached).

    ESI houdt hier ongeveer 30 dagen van bij, per dag en per ertssoort
    samengevat — geen losse cycli.
    """
    key = f"fin_mining_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    token = token_for(character_id, MINING_SCOPE)
    if not token:
        cache.set(key, [], TTL_MINING)
        return []

    rows = _paged(f"/characters/{character_id}/mining/", token)
    cache.set(key, rows, TTL_MINING)
    return rows


def planets(character_id):
    """De planetaire kolonies van dit character (gecached)."""
    key = f"fin_planets_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    token = token_for(character_id, PLANETS_SCOPE)
    rows = _request(f"/characters/{character_id}/planets/", token) if token else None
    rows = rows or []
    cache.set(key, rows, TTL_PLANETS)
    return rows


def planet_detail(character_id, planet_id):
    """De inrichting van één kolonie: pins, links en routes.

    Hier zit alles in wat de lijst-endpoint níet geeft: welke extractor wat
    haalt en wanneer z'n programma afloopt, welke fabrieken draaien, en wat er
    in de opslag en op de launchpad ligt. Eén call per planeet, dus even lang
    gecached als de lijst zelf — anders kost elke paginaweergave vijftien calls.
    """
    key = f"fin_planetdet_{character_id}_{planet_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    token = token_for(character_id, PLANETS_SCOPE)
    data = _request(f"/characters/{character_id}/planets/{planet_id}/", token) if token else None
    data = data or {}
    cache.set(key, data, TTL_PLANETS)
    return data


def schematic(schematic_id):
    """Naam en cyclustijd van een productieschema (publiek, geen token).

    ESI zegt niet wát een fabriek maakt — alleen welk schema erin zit. De naam
    van het schema ís de productnaam, en met de cyclustijd valt de productie per
    uur uit te rekenen. Schema's veranderen alleen bij een patch, dus 30 dagen.
    """
    key = f"fin_schem_{schematic_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    uit = {}
    try:
        r = _session.get(f"{ESI}/universe/schematics/{schematic_id}/", headers=UA,
                         params={"datasource": "tranquility"}, timeout=20)
        if r.status_code == 200:
            uit = r.json() or {}
    except (requests.RequestException, ValueError):
        return {}                       # niet cachen: volgende keer opnieuw
    cache.set(key, uit, 30 * 86400)
    return uit


def planet_info(planet_id):
    """Naam van een planeet.

    `/universe/names` kent planeet-ids niet (die geeft er 404 op), dus daarvoor
    is een eigen endpoint nodig. Planeetnamen veranderen nooit, dus 30 dagen
    cache. Publiek, geen token.
    """
    key = f"fin_planet_{planet_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    naam = ""
    try:
        r = _session.get(f"{ESI}/universe/planets/{planet_id}/", headers=UA,
                         params={"datasource": "tranquility"}, timeout=20)
        if r.status_code == 200:
            naam = r.json().get("name") or ""
    except requests.RequestException:
        return ""                       # niet cachen: volgende keer opnieuw proberen
    cache.set(key, naam, 30 * 86400)
    return naam


def contract_items(character_id, contract_id):
    """De spullen in één contract.

    De inhoud van een contract ligt vast zodra het bestaat, dus dit mag lang in
    de cache — anders zou elke paginaweergave één call per contract kosten.
    """
    key = f"fin_citems_{contract_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    token = token_for(character_id, CONTRACTS_SCOPE)
    rows = None
    if token:
        rows = _request(f"/characters/{character_id}/contracts/{contract_id}/items/", token)
    rows = rows or []
    cache.set(key, rows, 7 * 86400)
    return rows


# --------------------------------------------------------------------------
# Namen
# --------------------------------------------------------------------------

def names(ids):
    """id → naam via /universe/names.

    Die endpoint weigert de **hele batch** zodra er één onresolvebaar id in zit
    (een verwijderd character, een structure). Daarom splitsen we een geweigerde
    batch binair op: de goede ids krijgen alsnog hun naam en alleen het rotte id
    valt af. Zonder dat verlies je in één klap álle namen op de pagina — precies
    de bug die aa-corp-killboard had.
    """
    ids = list({int(i) for i in ids if i})
    uit, missend = {}, []
    for i in ids:
        hit = cache.get(f"fin_name_{i}")
        if hit is not None:
            uit[i] = hit
        else:
            missend.append(i)

    def los_op(batch):
        if not batch:
            return
        try:
            r = _session.post(f"{ESI}/universe/names/", json=batch, headers=UA,
                              params={"datasource": "tranquility"}, timeout=20)
        except requests.RequestException:
            return
        if r.status_code == 200:
            for x in r.json():
                uit[x["id"]] = x["name"]
                cache.set(f"fin_name_{x['id']}", x["name"], 7 * 86400)
        elif r.status_code >= 500 or r.status_code == 429:
            return                       # storing: niet opsplitsen, gewoon opgeven
        elif len(batch) > 1:
            half = len(batch) // 2
            los_op(batch[:half])
            los_op(batch[half:])
        else:
            uit[batch[0]] = ""           # ESI kent dit id echt niet
            cache.set(f"fin_name_{batch[0]}", "", 7 * 86400)

    for i in range(0, len(missend), 1000):
        los_op(missend[i:i + 1000])
    return uit


# --------------------------------------------------------------------------
# Marktprijzen (Fuzzwork, publiek — geen token)
# --------------------------------------------------------------------------

FUZZWORK = "https://market.fuzzwork.co.uk/aggregates/"
JITA_REGIO = 10000002
TTL_PRIJZEN = 3600


def jita_buy(type_ids):
    """{type_id: hoogste Jita-buy} via Fuzzwork.

    Zelfde bron als de dashboard-pagina, zodat beide plekken dezelfde waarde
    laten zien. Publiek, dus geen token. Ontbrekende prijzen blijven weg in
    plaats van 0 te worden — dan kan de aanroeper 'onbekend' tonen.
    """
    ids = sorted({int(i) for i in type_ids if i})
    uit, missend = {}, []
    for i in ids:
        hit = cache.get(f"fin_prijs_{i}")
        if hit is not None:
            uit[i] = hit
        else:
            missend.append(i)

    # Fuzzwork slikt lange lijsten, maar niet oneindig; in blokken opvragen.
    for i in range(0, len(missend), 200):
        blok = missend[i:i + 200]
        try:
            r = _session.get(FUZZWORK, headers=UA, timeout=25,
                             params={"region": JITA_REGIO, "types": ",".join(map(str, blok))})
        except requests.RequestException as exc:
            logger.info("Finance: Fuzzwork onbereikbaar: %s", exc)
            continue
        if r.status_code != 200:
            logger.info("Finance: Fuzzwork gaf %s", r.status_code)
            continue
        try:
            data = r.json()
        except ValueError:
            continue
        for sleutel, waarde in data.items():
            try:
                prijs = float(waarde["buy"]["max"])
            except (KeyError, TypeError, ValueError):
                continue
            uit[int(sleutel)] = prijs
            cache.set(f"fin_prijs_{int(sleutel)}", prijs, TTL_PRIJZEN)
    return uit
