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

TTL_BALANCE = 300           # saldo verandert vaak, maar niet elke seconde
TTL_JOURNAL = 900           # journaal-regels zijn onveranderlijk zodra ze er staan
TTL_CONTRACTS = 600
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


def _request(path, token, params=None):
    """Eén ESI-call met backoff. Geeft de data of None."""
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
                return r.json()
            except ValueError:
                return None

        if r.status_code in RETRY_STATUS and poging < MAX_TRIES:
            time.sleep(int(r.headers.get("Retry-After", 0)) or min(2 ** poging * 0.5, 8))
            continue

        logger.info("Finance: %s gaf %s", path, r.status_code)
        return None
    return None


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

    regels = []
    for p in range(1, pages + 1):
        blok = _request(f"/characters/{character_id}/wallet/journal/", token, {"page": p})
        if not blok:
            break
        regels.extend(blok)
        if len(blok) < 1000:            # laatste pagina
            break
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

    rows, page = [], 1
    while page <= 20:
        blok = _request(f"/characters/{character_id}/contracts/", token, {"page": page})
        if not blok:
            break
        rows.extend(blok)
        if len(blok) < 1000:
            break
        page += 1
    cache.set(key, rows, TTL_CONTRACTS)
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
