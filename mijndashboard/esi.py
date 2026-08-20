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
UA = {"User-Agent": "aa-mijndashboard (Alliance Auth plugin; maintainer: Dutch Legions)"}

WALLET_SCOPE = "esi-wallet.read_character_wallet.v1"
CONTRACTS_SCOPE = "esi-contracts.read_character_contracts.v1"
MINING_SCOPE = "esi-industry.read_character_mining.v1"
PLANETS_SCOPE = "esi-planets.manage_planets.v1"
MAIL_SCOPE = "esi-mail.read_mail.v1"
SEND_MAIL_SCOPE = "esi-mail.send_mail.v1"
ORDERS_SCOPE = "esi-markets.read_character_orders.v1"
# Voor het Dashboard-tabblad. Geen van deze wordt bij het koppelen gevraagd: ze
# zijn leuk maar niet essentieel, en bijna elk account heeft ze al liggen van
# CharLink of Member Audit. Ontbreekt er een, dan valt alleen dat blokje weg.
LOCATIE_SCOPE = "esi-location.read_location.v1"
SCHIP_SCOPE = "esi-location.read_ship_type.v1"
ONLINE_SCOPE = "esi-location.read_online.v1"
SKILLQUEUE_SCOPE = "esi-skills.read_skillqueue.v1"
JOBS_SCOPE = "esi-industry.read_character_jobs.v1"
AGENDA_SCOPE = "esi-calendar.read_calendar_events.v1"
# Ook niet gevraagd bij het koppelen: zonder dit token kunnen we het orderboek
# van een spelersstructuur niet lezen en valt alleen de vergelijking met de
# concurrentie daar weg — de orders zelf blijven gewoon staan.
STRUCTUURMARKT_SCOPE = "esi-markets.structure_markets.v1"
# Niet gevraagd bij het koppelen: spelersstructuren zijn mooi meegenomen, maar
# er een herkoppeling voor afdwingen is het niet waard. Bijna elk account heeft
# dit token al liggen van een andere plugin — dan gebruiken we dat.
STRUCTURES_SCOPE = "esi-universe.read_structures.v1"

TTL_BALANCE = 300           # saldo verandert vaak, maar niet elke seconde
TTL_JOURNAL = 900           # journaal-regels zijn onveranderlijk zodra ze er staan
TTL_CONTRACTS = 600
TTL_MINING = 1800           # de ledger vat per dag samen, dus dit hoeft niet vers
TTL_PLANETS = 900           # extractors lopen af, dus niet te lang vasthouden
TTL_LOCATIE = 60            # waar je bent verandert elke warp; kort vasthouden
TTL_QUEUE = 900             # skill queue en industry jobs lopen in uren, niet in seconden
TTL_AGENDA = 1800
TTL_ZKILL = 3600
TTL_ORDERS = 300            # je eigen orders veranderen zodra er iets verkoopt
TTL_ORDERHIST = 1800        # afgelopen orders veranderen niet meer
TTL_BOEK = 900              # de markt beweegt, maar niet elke paginaweergave
TTL_MAIL = 600              # nieuwe mail mag best een paar minuten op zich laten wachten
TTL_MAILBODY = 30 * 86400   # een verzonden mail verandert nooit meer
JOURNAL_PAGES = 5           # ESI geeft 1000 regels per pagina, 5 is ruim een maand
MAIL_RONDES = 6             # 50 koppen per ronde, dus 300 mails terug

# Grenzen bij het versturen. De ESI-spec noemt 10.000 tekens voor de body, maar
# de server weigert alles boven de 8000 met "Maximum body length is 8000" — dat
# is dezelfde 8000 als de teller in het mailvenster van de game. Afgaan op de
# spec kostte in aa-vkvnieuws een mislukte verzending, dus: 8000.
MAIL_MAX_BODY = 8000
MAIL_MAX_ONDERWERP = 1000
MAIL_MAX_ONTVANGERS = 50

# Statussen waarbij opnieuw proberen zin heeft: foutlimiet, rate limit, storing.
RETRY_STATUS = {420, 429, 500, 502, 503, 504}
MAX_TRIES = 4

# Eén sessie voor het hele proces: hergebruik van TLS-verbindingen in plaats van
# er honderden opzetten (scheelt tijd en voorkomt poortuitputting op Windows).
_session = requests.Session()
_session.mount("https://", requests.adapters.HTTPAdapter(
    pool_connections=8, pool_maxsize=8, max_retries=0,
))


def _request_met_headers(path, token=None, params=None):
    """Eén ESI-call met backoff. Geeft (data, headers) of (None, {}).

    Zonder token gaat de call ongeauthenticeerd de deur uit — dat mag voor de
    publieke endpoints (marktorders van een regio bijvoorbeeld). Aanroepers van
    de persoonlijke endpoints controleren zelf of er een token is.
    """
    headers = {**UA}
    if token:
        headers["Authorization"] = f"Bearer {token}"
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


def _request(path, token=None, params=None):
    """Eén ESI-call met backoff. Geeft de data of None."""
    data, _ = _request_met_headers(path, token, params)
    return data


def _paged(path, token=None, params=None, max_pages=20):
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
# Dashboard: waar je bent, wat je traint, wat er in de oven staat
# --------------------------------------------------------------------------

def _persoonlijk(character_id, pad, scope, sleutel, ttl, leeg):
    """Kleine gecachte GET op een character-endpoint.

    Deze zes blokjes lijken zo op elkaar dat ze anders zes keer hetzelfde
    zouden zijn. Zonder token geven we de lege waarde terug: het dashboard laat
    dat blok dan gewoon weg in plaats van te klagen.
    """
    key = f"fin_{sleutel}_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    token = token_for(character_id, scope)
    data = _request(pad.format(id=character_id), token) if token else None
    if data is None:
        data = leeg
    cache.set(key, data, ttl)
    return data


def locatie(character_id):
    """Systeem (en eventueel station of structuur) waar dit character is."""
    return _persoonlijk(character_id, "/characters/{id}/location/",
                        LOCATIE_SCOPE, "loc", TTL_LOCATIE, {})


def schip(character_id):
    """Het schip waar dit character in zit: type en de naam die het draagt."""
    return _persoonlijk(character_id, "/characters/{id}/ship/",
                        SCHIP_SCOPE, "schip", TTL_LOCATIE, {})


def online(character_id):
    """Of het character ingelogd is, en wanneer het voor het laatst was."""
    return _persoonlijk(character_id, "/characters/{id}/online/",
                        ONLINE_SCOPE, "online", TTL_LOCATIE, {})


def skillqueue(character_id):
    """De trainingswachtrij."""
    return _persoonlijk(character_id, "/characters/{id}/skillqueue/",
                        SKILLQUEUE_SCOPE, "queue", TTL_QUEUE, [])


def industry_jobs(character_id):
    """Lopende en net afgeronde industry jobs."""
    return _persoonlijk(character_id, "/characters/{id}/industry/jobs/",
                        JOBS_SCOPE, "jobs", TTL_QUEUE, [])


def agenda(character_id):
    """De in-game agenda: fleet-ops en andere uitnodigingen."""
    return _persoonlijk(character_id, "/characters/{id}/calendar/",
                        AGENDA_SCOPE, "agenda", TTL_AGENDA, [])


def zkill_lijst(character_id, soort="kills", limiet=10):
    """De laatste kills of losses van dit character volgens zKillboard.

    Geeft alleen killmail-id, hash en de waarde; het verhaal (schip, systeem,
    wie) zit in de killmail zelf, die je met die hash publiek uit ESI haalt.
    """
    key = f"fin_zk{soort}_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    uit = []
    try:
        r = _session.get(
            f"https://zkillboard.com/api/{soort}/characterID/{character_id}/",
            headers=UA, timeout=25)
        if r.status_code == 200:
            for rij in (r.json() or [])[:limiet]:
                zkb = rij.get("zkb") or {}
                uit.append({"id": rij.get("killmail_id"), "hash": zkb.get("hash"),
                            "waarde": float(zkb.get("totalValue") or 0),
                            "solo": bool(zkb.get("solo"))})
    except (requests.RequestException, ValueError) as exc:
        logger.info("Finance: zKillboard %s onbereikbaar: %s", soort, exc)
        return []
    cache.set(key, uit, TTL_ZKILL)
    return uit


def killmail(killmail_id, killmail_hash):
    """Eén killmail, publiek op te vragen met z'n hash.

    Een killmail verandert nooit meer, dus die mag een maand blijven staan.
    """
    key = f"fin_km_{killmail_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    data = _request(f"/killmails/{killmail_id}/{killmail_hash}/") or {}
    if data:
        cache.set(key, data, 30 * 86400)
    return data


def zkill_stats(character_id):
    """Kills en verliezen van zKillboard (publiek, geen token).

    ESI kan dit niet in één klap: `/characters/{id}/killmails/recent/` geeft
    alleen ids en hashes, en dan moet je elke killmail apart ophalen. zKillboard
    heeft de totalen al geteld. Een uur cache — het zijn statistieken, geen
    live-feed.
    """
    key = f"fin_zkill_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    uit = {}
    try:
        r = _session.get(f"https://zkillboard.com/api/stats/characterID/{character_id}/",
                         headers=UA, timeout=20)
        if r.status_code == 200:
            uit = r.json() or {}
    except (requests.RequestException, ValueError) as exc:
        logger.info("Finance: zKillboard onbereikbaar: %s", exc)
        return {}
    cache.set(key, uit, TTL_ZKILL)
    return uit


# --------------------------------------------------------------------------
# Markt
# --------------------------------------------------------------------------

def orders(character_id, token=None):
    """De openstaande marktorders van dit character."""
    key = f"fin_orders_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    token = token or token_for(character_id, ORDERS_SCOPE)
    rows = _request(f"/characters/{character_id}/orders/", token) if token else None
    rows = rows or []
    cache.set(key, rows, TTL_ORDERS)
    return rows


def order_history(character_id, token=None):
    """Afgelopen orders van dit character (ESI houdt ~90 dagen bij).

    Let op bij het lezen: een order die **helemaal gevuld** is krijgt state
    `expired` met `volume_remain` 0 — er bestaat geen aparte status "verkocht".
    Alleen een order die met spullen erin afloopt is écht verlopen.
    """
    key = f"fin_orderhist_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    token = token or token_for(character_id, ORDERS_SCOPE)
    rows = _paged(f"/characters/{character_id}/orders/history/", token) if token else []
    cache.set(key, rows, TTL_ORDERHIST)
    return rows


def markt_regio(region_id, type_id):
    """Alle orders van één type in een regio. Publiek, dus zonder token.

    Met het type-filter is dit één pagina — een heel regio-orderboek ophalen
    voor één artikel zou onbeschoft zijn tegenover ESI én traag.
    """
    key = f"fin_mregio_{region_id}_{type_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    rijen = _paged(f"/markets/{region_id}/orders/", None,
                   {"type_id": type_id, "order_type": "all"}, max_pages=5)
    # Alleen wat we nodig hebben bewaren: het order-id om onze eigen orders eruit
    # te kunnen filteren, de prijs, de kant en waar hij ligt.
    klein = [{"order_id": o.get("order_id"), "prijs": float(o.get("price") or 0),
              "koop": bool(o.get("is_buy_order")), "systeem": o.get("system_id"),
              "locatie": o.get("location_id")}
             for o in rijen]
    cache.set(key, klein, TTL_BOEK)
    return klein


def markt_structuur(structure_id, character_ids):
    """Het orderboek van een spelersstructuur, per type gegroepeerd.

    Een structuurmarkt staat **niet** in de regio-orders: die endpoint kent
    alleen NPC-stations. Hier is dus een token nodig van iemand die er mag
    docken — welke dat is verschilt per structuur, dus we proberen ze allemaal,
    net als bij `structure_names`.

    Het hele boek moet in één keer opgehaald (geen type-filter beschikbaar),
    maar dat valt mee: de grootste markt hier is 4 pagina's in 0,8s. We bewaren
    het per type uitgesplitst, want dat is waar we het voor gebruiken.
    """
    key = f"fin_mstruct_{structure_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    boek = {}
    for token in _tokens_met_scope(character_ids, STRUCTUURMARKT_SCOPE):
        rijen = _paged(f"/markets/structures/{structure_id}/", token, max_pages=30)
        if rijen:
            for o in rijen:
                boek.setdefault(o.get("type_id"), []).append(
                    {"order_id": o.get("order_id"), "prijs": float(o.get("price") or 0),
                     "koop": bool(o.get("is_buy_order")),
                     "locatie": o.get("location_id")})
            break
    # Geen toegang? Kort cachen, zodat nieuwe dockingrechten vanzelf helpen.
    cache.set(key, boek, TTL_BOEK if boek else 300)
    return boek


def station_systeem(station_id):
    """In welk systeem een NPC-station staat (publiek, verandert nooit)."""
    key = f"fin_station_{station_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    data = _request(f"/universe/stations/{station_id}/")
    systeem = (data or {}).get("system_id")
    if systeem:
        cache.set(key, systeem, 30 * 86400)
    return systeem


# --------------------------------------------------------------------------
# Mail
# --------------------------------------------------------------------------

def mail_headers(character_id, token=None, rondes=MAIL_RONDES):
    """De mailkoppen van dit character, nieuwste eerst.

    **Deze endpoint pagineert niet zoals de rest.** Geen `page` en geen
    X-Pages: je krijgt 50 koppen per keer en vraagt de volgende vijftig op met
    `last_mail_id` = het laagste id dat je al hebt. Zoek je hier naar X-Pages,
    dan blijf je bij die eerste vijftig steken zonder dat er iets misgaat.

    `token` mag meegegeven worden: dan doet deze functie geen database-werk en
    kan de aanroeper alle characters tegelijk ophalen.
    """
    key = f"fin_mailkop_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    token = token or token_for(character_id, MAIL_SCOPE)
    if not token:
        cache.set(key, [], TTL_MAIL)
        return []

    alles, laatste = [], None
    for _ in range(rondes):
        blok = _request(f"/characters/{character_id}/mail/", token,
                        {"last_mail_id": laatste} if laatste else None)
        if not blok:
            break
        alles.extend(blok)
        if len(blok) < 50:              # minder dan een volle ronde = einde mailbox
            break
        laatste = min(m["mail_id"] for m in blok)
    cache.set(key, alles, TTL_MAIL)
    return alles


def mail_body(character_id, mail_id, token=None):
    """De inhoud van één mail.

    Een verstuurde mail verandert nooit meer, dus dit mag een maand blijven
    staan: daarna kost het openslaan van je mailbox geen enkele ESI-call meer.
    """
    key = f"fin_mailbody_{mail_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    token = token or token_for(character_id, MAIL_SCOPE)
    data = _request(f"/characters/{character_id}/mail/{mail_id}/", token) if token else None
    data = data or {}
    if data:                            # een mislukte poging niet een maand vasthouden
        cache.set(key, data, TTL_MAILBODY)
    return data


def mail_labels(character_id, token=None):
    """Labels van dit character met hun ongelezen-tellers."""
    key = f"fin_maillabels_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    token = token or token_for(character_id, MAIL_SCOPE)
    data = _request(f"/characters/{character_id}/mail/labels/", token) if token else None
    data = data or {}
    cache.set(key, data, TTL_MAIL)
    return data


def zoek_ids(namen):
    """Namen → ids via /universe/ids/ (publiek).

    `/universe/names` gaat van id naar naam; deze endpoint doet het andersom en
    is de enige manier om een ingetypte ontvanger op te zoeken. Het antwoord is
    per categorie gesorteerd (characters / corporations / alliances …), en die
    categorie is precies het `recipient_type` dat de mail-endpoint wil hebben.

    Hoofdlettergevoelig is het niet, maar exact wél: 'brandweer' vindt niets.
    """
    namen = [n for n in {n.strip() for n in namen} if n]
    if not namen:
        return {}
    try:
        r = _session.post(f"{ESI}/universe/ids/", json=namen, headers=UA,
                          params={"datasource": "tranquility", "language": "en"},
                          timeout=20)
    except requests.RequestException as exc:
        logger.info("Finance: /universe/ids onbereikbaar: %s", exc)
        return {}
    if r.status_code != 200:
        logger.info("Finance: /universe/ids gaf %s", r.status_code)
        return {}
    try:
        data = r.json() or {}
    except ValueError:
        return {}

    # Een naam kan in meerdere categorieën voorkomen (een corp die net zo heet
    # als een character). Volgorde is de voorrang: een persoon eerst.
    uit = {}
    for sleutel, soort in (("characters", "character"),
                           ("corporations", "corporation"),
                           ("alliances", "alliance")):
        for rij in data.get(sleutel) or []:
            uit.setdefault((rij.get("name") or "").lower(),
                           {"id": rij.get("id"), "naam": rij.get("name"),
                            "soort": soort})
    return uit


def stuur_mail(character_id, ontvangers, onderwerp, inhoud, token=None):
    """Verstuur één mail. Geeft (mail_id, "") of (None, "wat er misging").

    Geen uitzonderingen: de aanroeper is een formulier, en dat wil een zin die
    je aan de gebruiker kunt laten zien.
    """
    token = token or token_for(character_id, SEND_MAIL_SCOPE)
    if not token:
        return None, ("Dit character mag geen mail versturen. Koppel het "
                      "opnieuw, dan wordt die toestemming meteen gevraagd.")
    if not ontvangers:
        return None, "Geen ontvangers."
    if len(ontvangers) > MAIL_MAX_ONTVANGERS:
        return None, (f"ESI staat hoogstens {MAIL_MAX_ONTVANGERS} ontvangers per "
                      f"mail toe; dit bericht heeft er {len(ontvangers)}.")
    if not (inhoud or "").strip():
        return None, "Lege mail."
    if len(inhoud) > MAIL_MAX_BODY:
        # Niet afkappen: een half verstuurd bericht is erger dan geen.
        return None, (f"Te lang voor EVE: {_nl_getal(len(inhoud))} tekens "
                      f"inclusief opmaak, en de grens is "
                      f"{_nl_getal(MAIL_MAX_BODY)}.")

    lading = {
        "subject": (onderwerp or "")[:MAIL_MAX_ONDERWERP],
        "body": inhoud,
        "recipients": [{"recipient_type": soort, "recipient_id": int(eve_id)}
                       for soort, eve_id in ontvangers],
        # De CSPA-heffing die iemand op z'n mailbox kan zetten. Nul betekent:
        # alleen versturen als het gratis is. ESI zegt het als dat niet zo is.
        "approved_cost": 0,
    }

    for poging in (1, 2, 3):
        try:
            r = _session.post(
                f"{ESI}/characters/{character_id}/mail/",
                headers={**UA, "Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                params={"datasource": "tranquility"}, json=lading, timeout=30)
        except requests.RequestException as exc:
            if poging == 3:
                return None, f"ESI niet bereikbaar: {exc}"
            time.sleep(2 ** poging)
            continue

        if r.status_code in (200, 201):
            try:
                mail_id = r.json()
            except ValueError:
                mail_id = 0
            logger.info("Finance: mail verstuurd door %s naar %s ontvangers",
                        character_id, len(ontvangers))
            return mail_id, ""

        # Alleen bij een tijdelijke storing opnieuw. Een 403 verandert niet door
        # het nog eens te doen, en bij 520 (CCP's eigen maillimiet) maak je het
        # juist erger.
        if r.status_code in (500, 502, 503, 504) and poging < 3:
            time.sleep(2 ** poging)
            continue
        return None, _mailfout(r)
    return None, "Verzenden lukte niet na drie pogingen."


def _nl_getal(n):
    return f"{n:,}".replace(",", ".")


def _mailfout(respons):
    """Van een ESI-foutcode iets maken waar de gebruiker wat aan heeft."""
    try:
        melding = (respons.json() or {}).get("error", "")
    except ValueError:
        melding = (respons.text or "")[:200]

    uitleg = {
        400: ("ESI weigerde het bericht. Meestal een ontvanger die niet bestaat, "
              "een lege tekst, of een body boven de 8000 tekens."),
        401: "Het token is verlopen of ingetrokken. Koppel het character opnieuw.",
        403: ("Dit character mag deze mail niet versturen. Voor een mail aan een "
              "hele corporatie of alliantie heb je in de game de rol "
              "Communications Officer nodig."),
        404: "ESI kent dit character niet.",
        420: "ESI-foutlimiet bereikt. Even wachten en het daarna opnieuw proberen.",
        520: ("EVE's eigen limiet op mail versturen is bereikt. Dat is een limiet "
              "van CCP op het aantal mails per tijdseenheid — wachten helpt."),
    }.get(respons.status_code, f"ESI gaf {respons.status_code} terug.")
    return f"{uitleg} ({melding})" if melding else uitleg


def mail_lists(character_id, token=None):
    """De mailinglijsten waar dit character op zit.

    Nodig voor de namen: een mailinglijst-id lost `/universe/names` niet op, dus
    zonder deze lijst staat er een kaal nummer bij de ontvangers.
    """
    key = f"fin_maillists_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    token = token or token_for(character_id, MAIL_SCOPE)
    rows = _request(f"/characters/{character_id}/mail/lists/", token) if token else None
    rows = rows or []
    cache.set(key, rows, TTL_MAIL)
    return rows


# --------------------------------------------------------------------------
# Namen
# --------------------------------------------------------------------------

def name_info(ids):
    """id → {"naam", "soort"} via /universe/names.

    Zelfde bron als `names()`, maar hier houden we ook de **categorie** vast.
    Bij een mail weet je van de afzender alleen het id, en zonder categorie kun
    je niet zien of daar een portret of een corporatielogo bij hoort. Een eigen
    cachesleutel, want `names()` bewaart alleen de kale naam en die twee door
    elkaar halen zou de ene of de andere aanroeper laten struikelen.
    """
    ids = list({int(i) for i in ids if i})
    uit, missend = {}, []
    for i in ids:
        hit = cache.get(f"fin_naamsoort_{i}")
        if hit is not None:
            uit[i] = hit
        else:
            missend.append(i)

    def los_op(batch):
        """Zelfde binaire splitsing als in `names()`: één rot id sloopt de batch."""
        if not batch:
            return
        try:
            r = _session.post(f"{ESI}/universe/names/", json=batch, headers=UA,
                              params={"datasource": "tranquility"}, timeout=20)
        except requests.RequestException:
            return
        if r.status_code == 200:
            for x in r.json():
                vak = {"naam": x.get("name") or "", "soort": x.get("category") or ""}
                uit[x["id"]] = vak
                cache.set(f"fin_naamsoort_{x['id']}", vak, 7 * 86400)
        elif r.status_code >= 500 or r.status_code == 429:
            return
        elif len(batch) > 1:
            half = len(batch) // 2
            los_op(batch[:half])
            los_op(batch[half:])
        else:
            vak = {"naam": "", "soort": ""}
            uit[batch[0]] = vak
            cache.set(f"fin_naamsoort_{batch[0]}", vak, 7 * 86400)

    for i in range(0, len(missend), 1000):
        los_op(missend[i:i + 1000])
    return uit


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


# --------------------------------------------------------------------------
# Local chat: wie is er vriendelijk?
# --------------------------------------------------------------------------

CONTACTS_SCOPE = "esi-characters.read_contacts.v1"
CORP_CONTACTS_SCOPE = "esi-corporations.read_contacts.v1"
ALLIANCE_CONTACTS_SCOPE = "esi-alliances.read_contacts.v1"
TTL_CONTACTEN = 900
TTL_CHARINFO = 86400


def character_info(character_id):
    """Publieke gegevens van een character: in welke corp en alliance hij zit."""
    key = f"fin_charinfo_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    data = _request(f"/characters/{character_id}/") or {}
    if data:
        cache.set(key, {"corporation_id": data.get("corporation_id"),
                        "alliance_id": data.get("alliance_id")}, TTL_CHARINFO)
    return cache.get(key) or {}


def _contacten_van(pad, scope, character_ids):
    """Contacten van een endpoint, met het eerste token dat er langs mag."""
    for token in _tokens_met_scope(character_ids, scope):
        rijen = _paged(pad, token)
        if rijen:
            return rijen
    return []


def contacten(character_ids, corporation_id=None, alliance_id=None):
    """{entiteit_id: standing} uit alliance-, corp- én persoonlijke contacten.

    De volgorde is oplopende voorrang, net als op de site: alliance wordt
    overschreven door corp, en corp door je persoonlijke contacten.
    """
    key = f"fin_contacten_{corporation_id}_{alliance_id}_{min(character_ids) if character_ids else 0}"
    hit = cache.get(key)
    if hit is not None:
        return hit

    uit = {}
    if alliance_id:
        for c in _contacten_van(f"/alliances/{alliance_id}/contacts/",
                               ALLIANCE_CONTACTS_SCOPE, character_ids):
            uit[c.get("contact_id")] = float(c.get("standing") or 0)
    if corporation_id:
        for c in _contacten_van(f"/corporations/{corporation_id}/contacts/",
                               CORP_CONTACTS_SCOPE, character_ids):
            uit[c.get("contact_id")] = float(c.get("standing") or 0)
    for cid in character_ids:
        token = token_for(cid, CONTACTS_SCOPE)
        if token:
            for c in _paged(f"/characters/{cid}/contacts/", token):
                uit[c.get("contact_id")] = float(c.get("standing") or 0)
            break
    cache.set(key, uit, TTL_CONTACTEN)
    return uit


# --------------------------------------------------------------------------
# Industrie
# --------------------------------------------------------------------------

BLUEPRINTS_SCOPE = "esi-characters.read_blueprints.v1"
TTL_BLUEPRINTS = 1800
TTL_MARKTPRIJZEN = 3600


def blueprints(character_id):
    """De blueprints van dit character (gepagineerd, gecached)."""
    key = f"fin_bp_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    token = token_for(character_id, BLUEPRINTS_SCOPE)
    rijen = _paged(f"/characters/{character_id}/blueprints/", token) if token else []
    cache.set(key, rijen, TTL_BLUEPRINTS)
    return rijen


def industry_jobs_compleet(character_id):
    """Industry jobs inclusief de afgeronde.

    Aparte cachesleutel: de lijst met afgeronde jobs is veel langer en wordt
    alleen op het Industry-tabblad gebruikt, niet in het dashboardblokje.
    """
    key = f"fin_jobsall_{character_id}"
    hit = cache.get(key)
    if hit is not None:
        return hit
    token = token_for(character_id, JOBS_SCOPE)
    rijen = _paged(f"/characters/{character_id}/industry/jobs/", token,
                   {"include_completed": "true"}) if token else []
    cache.set(key, rijen, TTL_QUEUE)
    return rijen


def markt_prijzen():
    """{type_id: adjusted_price} — CCP's eigen waardering, publiek.

    Hier rekent het spel de installatiekosten van een job mee uit (de EIV), dus
    dit is de enige juiste bron voor die schatting; een marktprijs is het niet.
    """
    key = "fin_marktprijzen"
    hit = cache.get(key)
    if hit is not None:
        return hit
    uit = {}
    for rij in _request("/markets/prices/") or []:
        if rij.get("adjusted_price"):
            uit[rij["type_id"]] = float(rij["adjusted_price"])
    if uit:
        cache.set(key, uit, TTL_MARKTPRIJZEN)
    return uit


def jita_prijzen(type_ids):
    """{type_id: {"koop": hoogste bod, "verkoop": laagste vraag}} via Fuzzwork.

    `jita_buy()` geeft alleen de biedprijs; voor bouwen heb je juist de
    vraagprijs nodig — dát betaal je als je de materialen nu koopt.
    """
    ids = sorted({int(i) for i in type_ids if i})
    uit, missend = {}, []
    for i in ids:
        hit = cache.get(f"fin_prijs2_{i}")
        if hit is not None:
            uit[i] = hit
        else:
            missend.append(i)

    for i in range(0, len(missend), 200):
        blok = missend[i:i + 200]
        try:
            r = _session.get(FUZZWORK, headers=UA, timeout=25,
                             params={"region": JITA_REGIO, "types": ",".join(map(str, blok))})
        except requests.RequestException as exc:
            logger.info("Finance: Fuzzwork onbereikbaar: %s", exc)
            continue
        if r.status_code != 200:
            continue
        try:
            data = r.json()
        except ValueError:
            continue
        for sleutel, waarde in data.items():
            try:
                vak = {"koop": float(waarde["buy"]["max"]),
                       "verkoop": float(waarde["sell"]["min"])}
            except (KeyError, TypeError, ValueError):
                continue
            uit[int(sleutel)] = vak
            cache.set(f"fin_prijs2_{int(sleutel)}", vak, TTL_PRIJZEN)
    return uit
