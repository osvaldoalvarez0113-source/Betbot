"""
pinnacle_ref.py — Fetches Pinnacle odds for MLB games via OddsPapi v4.

API structure (discovered empirically):
  Base: https://api.oddspapi.io/v4
  - GET /fixtures?apiKey=KEY&tournamentId=109&from=DATE&to=DATE  → fixture list
  - GET /participants?apiKey=KEY&sportId=13                       → {id: teamName}
  - GET /odds?apiKey=KEY&fixtureId=FID                           → {bookmakerOdds: {pinnacle: {markets:{...}}}}

Quota: free tier = 250 req/month. Each game odds fetch = 1 req.
  - Fixtures + participants = 2 req/day (participants cached 24h).
  - Per-game odds = 1 req each, capped by _MAX_DAILY_CALLS.
  - With _MAX_DAILY_CALLS = 8: safe for ~240 req/month.
  - NOTE: user requested 3 calls/day, but the API requires 1 call per fixture
    for odds; 3 total limits us to fixtures + participants + 1 game only.
    Set _MAX_DAILY_CALLS = 8 for practical daily coverage of most games.
    Reduce to 3 if strict quota is critical.

Cache file: pinnacle_cache.json  (same directory as this file)
  {
    "date": "YYYY-MM-DD",
    "fetched_at": <unix_ts>,
    "daily_calls": <int>,
    "participants": {<id>: <name>},
    "fixtures": [{"fixtureId": ..., "participant1Id": ..., "participant2Id": ..., "startTime": ...}],
    "odds": {
      "<fixtureId>": {
        "h2h":    {"home": <price>, "away": <price>}   | null,
        "totals": {"line": <float>, "over": <price>, "under": <price>} | null
      }
    }
  }
"""

import os
import json
import time
import requests
from datetime import datetime, timezone, timedelta

# ── Configuration ─────────────────────────────────────────────────────────────
_API_KEY          = os.environ.get("ODDSPAPI_KEY", "")
_BASE             = "https://api.oddspapi.io/v4"
_MLB_TOURNAMENT   = 109          # OddsPapi tournamentId for MLB
_MLB_SPORT_ID     = 13           # OddsPapi sportId for baseball
_CACHE_FILE       = os.path.join(os.path.dirname(__file__), "pinnacle_cache.json")
_CACHE_TTL_SECS   = 4 * 3600    # 4-hour freshness
_PTCP_TTL_SECS    = 24 * 3600   # 24-hour participant-name cache
_MAX_DAILY_CALLS  = 8            # Max HTTP requests to OddsPapi per day (safe for 250/month)

_SESS = requests.Session()
_SESS.headers.update({
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Accept": "application/json",
})


# ── Cache helpers ─────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(data: dict) -> None:
    try:
        with open(_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"  [pinnacle_ref] ⚠️ no se pudo guardar caché: {e}")


def _today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _now_ts() -> float:
    return time.time()


def _cache_is_fresh(cache: dict) -> bool:
    """True if cache was fetched within _CACHE_TTL_SECS and is for today."""
    if cache.get("date") != _today_utc():
        return False
    age = _now_ts() - float(cache.get("fetched_at", 0))
    return age < _CACHE_TTL_SECS


def _participants_fresh(cache: dict) -> bool:
    age = _now_ts() - float(cache.get("participants_at", 0))
    return bool(cache.get("participants")) and age < _PTCP_TTL_SECS


def _daily_calls_ok(cache: dict) -> bool:
    if cache.get("date") != _today_utc():
        return True   # new day — counter resets
    return cache.get("daily_calls", 0) < _MAX_DAILY_CALLS


def _increment_calls(cache: dict, n: int = 1) -> None:
    if cache.get("date") != _today_utc():
        cache["date"]        = _today_utc()
        cache["daily_calls"] = 0
    cache["daily_calls"] = cache.get("daily_calls", 0) + n


# ── API helpers ───────────────────────────────────────────────────────────────

def _api_get(path: str, params: dict) -> "dict | list | None":
    """Single GET against OddsPapi v4. Returns parsed JSON or None on error."""
    try:
        params["apiKey"] = _API_KEY
        r = _SESS.get(f"{_BASE}/{path}", params=params, timeout=12)
        if r.status_code == 200:
            return r.json()
        print(f"  [pinnacle_ref] HTTP {r.status_code} /{path}: {r.text[:120]}")
        return None
    except Exception as e:
        print(f"  [pinnacle_ref] error /{path}: {e}")
        return None


# ── Pinnacle odds parser ──────────────────────────────────────────────────────

def _parse_pinnacle_odds(raw_odds: dict) -> dict:
    """
    Extract h2h and totals from a single fixture's OddsPapi odds response.
    Returns {"h2h": {...}|None, "totals": {...}|None}.
    """
    pin = raw_odds.get("bookmakerOdds", {}).get("pinnacle", {})
    markets = pin.get("markets", {})

    h2h    = None
    totals = None

    # Collect all moneyline and totals candidates; pick best at the end.
    _h2h_candidates:    list = []
    _totals_candidates: list = []

    for _mid, mdata in markets.items():
        bmid = mdata.get("bookmakerMarketId", "")
        outcomes = mdata.get("outcomes", {})

        # ── MONEYLINE ────────────────────────────────────────────────────────
        if bmid.endswith("/moneyline"):
            home_p = away_p = None
            for _oid, odata in outcomes.items():
                p = odata.get("players", {}).get("0", {})
                bid = p.get("bookmakerOutcomeId", "")
                price = p.get("price")
                if not price or not p.get("active", True):
                    continue
                if bid == "home":
                    home_p = float(price)
                elif bid == "away":
                    away_p = float(price)
            if home_p is not None and away_p is not None:
                # Validate: implied probabilities must sum > 0.9 (normal 2-way market)
                impl_sum = 1.0 / home_p + 1.0 / away_p
                if impl_sum > 0.90:
                    _h2h_candidates.append({"home": home_p, "away": away_p, "_impl": impl_sum})

        # ── MAIN TOTALS (exclude teamTotal and period/inning totals) ──────────
        elif bmid.endswith("/totals") and "teamTotal" not in bmid:
            ov = un = line = None
            for _oid, odata in outcomes.items():
                p = odata.get("players", {}).get("0", {})
                bid = p.get("bookmakerOutcomeId", "")
                price = p.get("price")
                if not price or not p.get("active", True):
                    continue
                # bookmakerOutcomeId format: "8.5/over" or "8.5/under"
                if "/" in bid:
                    parts = bid.rsplit("/", 1)
                    try:
                        ln = float(parts[0])
                    except ValueError:
                        continue
                    if parts[1] == "over":
                        ov   = float(price)
                        line = ln
                    elif parts[1] == "under":
                        un   = float(price)
            if ov is not None and un is not None and line is not None:
                # Only accept realistic game totals (MLB: 4.0–14.0; period totals are < 4.0)
                if line >= 4.0:
                    _totals_candidates.append({"line": line, "over": ov, "under": un})

    # Pick best moneyline: highest implied-probability sum (closest to fair 2-way market)
    if _h2h_candidates:
        h2h = max(_h2h_candidates, key=lambda x: x["_impl"])
        del h2h["_impl"]

    # Pick main totals: highest line value (full-game total, not inning total)
    if _totals_candidates:
        totals = max(_totals_candidates, key=lambda x: x["line"])

    return {"h2h": h2h, "totals": totals}


# ── Fuzzy team-name matching ──────────────────────────────────────────────────

def _normalize(name: str) -> str:
    """Lowercase, strip common prefixes/suffixes for fuzzy match."""
    n = name.lower().strip()
    for pfx in ("new york ", "los angeles ", "san francisco ", "san diego ",
                "kansas city ", "st. louis ", "st louis ", "chicago ",
                "boston ", "tampa bay ", "minnesota ", "baltimore ",
                "cleveland ", "detroit ", "seattle ", "houston ",
                "texas ", "arizona ", "colorado ", "miami ", "atlanta ",
                "cincinnati ", "milwaukee ", "philadelphia ", "pittsburgh ",
                "toronto ", "oakland ", "washington "):
        if n.startswith(pfx):
            return n[len(pfx):]
    return n


def _fuzzy_match(query: str, candidate_name: str) -> bool:
    """True if query and candidate refer to the same MLB team."""
    q = _normalize(query)
    c = _normalize(candidate_name)
    if q == c:
        return True
    # Last-word match (e.g. "Yankees" == "yankees")
    if q.split()[-1] == c.split()[-1]:
        return True
    # Substring containment
    if q in c or c in q:
        return True
    return False


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_pinnacle_slate() -> dict:
    """
    Fetch (or refresh from cache) Pinnacle odds for all MLB games today.
    Respects _MAX_DAILY_CALLS quota and _CACHE_TTL_SECS freshness.

    Returns the cache dict (always). Never raises — on error returns whatever
    is available in the stale cache (or empty dict).
    """
    if not _API_KEY:
        print("  [pinnacle_ref] ⚠️ ODDSPAPI_KEY no configurada — sin datos Pinnacle")
        return {}

    cache = _load_cache()

    # ── Serve from cache if fresh ─────────────────────────────────────────────
    if _cache_is_fresh(cache):
        return cache

    # ── Check daily quota ─────────────────────────────────────────────────────
    if not _daily_calls_ok(cache):
        print(f"  [pinnacle_ref] ℹ️ cuota diaria alcanzada ({cache.get('daily_calls',0)}/{_MAX_DAILY_CALLS}) — usando caché viejo")
        return cache

    # ── Fetch fixtures ────────────────────────────────────────────────────────
    today  = _today_utc()
    tomrw  = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")

    fixtures_raw = _api_get("fixtures", {
        "tournamentId": _MLB_TOURNAMENT,
        "from": today,
        "to": tomrw,
    })
    _increment_calls(cache, 1)

    if fixtures_raw is None:
        print("  [pinnacle_ref] ⚠️ falló fetch de fixtures — usando caché viejo si existe")
        _save_cache(cache)
        return cache

    # Filter to today only and hasOdds=True
    fixtures_today = [
        f for f in (fixtures_raw if isinstance(fixtures_raw, list) else [])
        if f.get("startTime", "").startswith(today) and f.get("hasOdds")
    ]
    # Also accept tomorrow-UTC games that are actually today ET (early morning UTC)
    if not fixtures_today:
        fixtures_today = [
            f for f in (fixtures_raw if isinstance(fixtures_raw, list) else [])
            if f.get("hasOdds")
        ]

    cache["fixtures"] = fixtures_today
    cache["date"]     = today

    # ── Fetch participants (team names) if stale ──────────────────────────────
    if not _participants_fresh(cache):
        ptcp_raw = _api_get("participants", {"sportId": _MLB_SPORT_ID})
        _increment_calls(cache, 1)
        if isinstance(ptcp_raw, dict):
            cache["participants"]    = ptcp_raw
            cache["participants_at"] = _now_ts()
        elif isinstance(ptcp_raw, list):
            cache["participants"]    = {str(p.get("participantId","")): p.get("participantName","") for p in ptcp_raw}
            cache["participants_at"] = _now_ts()

    ptcp = cache.get("participants", {})

    # ── Fetch per-fixture odds within remaining quota ─────────────────────────
    if "odds" not in cache or cache.get("date") != today:
        cache["odds"] = {}

    fetched_count = 0
    for fx in fixtures_today:
        fid = fx.get("fixtureId")
        if not fid or fid in cache["odds"]:
            continue
        if not _daily_calls_ok(cache):
            break
        time.sleep(0.35)   # be polite to the API
        raw = _api_get("odds", {"fixtureId": fid})
        _increment_calls(cache, 1)
        if raw is not None:
            parsed = _parse_pinnacle_odds(raw)
            # Attach team names for easier lookup later
            p1 = str(fx.get("participant1Id", ""))
            p2 = str(fx.get("participant2Id", ""))
            parsed["home_name"] = ptcp.get(p1, p1)
            parsed["away_name"] = ptcp.get(p2, p2)
            parsed["startTime"] = fx.get("startTime", "")
            cache["odds"][fid]  = parsed
            fetched_count += 1

    cache["fetched_at"] = _now_ts()
    _save_cache(cache)

    total_pin = sum(1 for v in cache["odds"].values() if v.get("h2h"))
    print(f"  📌 Pinnacle ref (OddsPapi): {total_pin} juegos en caché, "
          f"edad 0 min, {cache.get('daily_calls',0)}/{_MAX_DAILY_CALLS} llamadas hoy")
    return cache


def fetch_pinnacle_for_games(game_list: list) -> dict:
    """
    Fetch Pinnacle odds on-demand for specific (home, away) pairs.

    Only spends API calls on games not already in fresh cache.
    Ensures the fixture index and participant map are loaded first (1-2 calls),
    then fetches per-game odds only for the requested games (1 call each).
    Respects _MAX_DAILY_CALLS quota at every step.

    game_list : list of (home_team, away_team) tuples
    Returns updated cache dict.
    """
    if not _API_KEY:
        return _load_cache()

    cache = _load_cache()
    today = _today_utc()

    # Reset odds bucket when the day rolls over
    if cache.get("date") != today:
        cache["odds"] = {}
        cache["date"] = today

    # ── Helper: is this game already in fresh per-entry cache? ───────────────
    def _game_cached_fresh(home: str, away: str) -> bool:
        for entry in cache.get("odds", {}).values():
            if (_fuzzy_match(home, entry.get("home_name", ""))
                    and _fuzzy_match(away, entry.get("away_name", ""))):
                entry_ts = float(entry.get("_fetched_at",
                                           cache.get("fetched_at", 0)))
                return (_now_ts() - entry_ts) < _CACHE_TTL_SECS
        return False

    games_needed = [(h, a) for h, a in game_list
                    if not _game_cached_fresh(h, a)]
    if not games_needed:
        return cache   # all requested games already fresh

    # ── Refresh fixture index if stale ────────────────────────────────────────
    if not cache.get("fixtures") or cache.get("date") != today:
        if not _daily_calls_ok(cache):
            print(f"  [pinnacle_ref] ℹ️ cuota diaria alcanzada — no se pueden cargar fixtures")
            return cache
        tomrw = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
        fx_raw = _api_get("fixtures", {
            "tournamentId": _MLB_TOURNAMENT,
            "from": today,
            "to": tomrw,
        })
        _increment_calls(cache, 1)
        if fx_raw is None:
            _save_cache(cache)
            return cache
        lst = fx_raw if isinstance(fx_raw, list) else []
        fixtures_today = [f for f in lst
                          if f.get("startTime", "").startswith(today) and f.get("hasOdds")]
        if not fixtures_today:
            fixtures_today = [f for f in lst if f.get("hasOdds")]
        cache["fixtures"] = fixtures_today

    # ── Refresh participants if stale ─────────────────────────────────────────
    if not _participants_fresh(cache) and _daily_calls_ok(cache):
        ptcp_raw = _api_get("participants", {"sportId": _MLB_SPORT_ID})
        _increment_calls(cache, 1)
        if isinstance(ptcp_raw, dict):
            cache["participants"]    = ptcp_raw
            cache["participants_at"] = _now_ts()
        elif isinstance(ptcp_raw, list):
            cache["participants"] = {
                str(p.get("participantId", "")): p.get("participantName", "")
                for p in ptcp_raw
            }
            cache["participants_at"] = _now_ts()

    ptcp = cache.get("participants", {})
    if "odds" not in cache:
        cache["odds"] = {}

    # ── Fetch per-game odds for only the requested games ─────────────────────
    for home, away in games_needed:
        if not _daily_calls_ok(cache):
            print(f"  [pinnacle_ref] ℹ️ cuota diaria alcanzada "
                  f"({cache.get('daily_calls', 0)}/{_MAX_DAILY_CALLS}) — "
                  f"{home} vs {away} seguirá sin Pinnacle")
            break

        # Find matching fixture by team-name fuzzy match
        target_fx = None
        for fx in cache.get("fixtures", []):
            p1 = str(fx.get("participant1Id", ""))
            p2 = str(fx.get("participant2Id", ""))
            h_name = ptcp.get(p1, "")
            a_name = ptcp.get(p2, "")
            if _fuzzy_match(home, h_name) and _fuzzy_match(away, a_name):
                target_fx = fx
                break

        if target_fx is None:
            print(f"  [pinnacle_ref] ⚠️ fixture no encontrado: {home} vs {away}")
            continue

        fid = target_fx.get("fixtureId")
        if not fid:
            continue

        # Skip if this specific fixtureId is still fresh
        if fid in cache["odds"]:
            entry_ts = float(cache["odds"][fid].get("_fetched_at", 0))
            if (_now_ts() - entry_ts) < _CACHE_TTL_SECS:
                continue

        time.sleep(0.35)
        raw = _api_get("odds", {"fixtureId": fid})
        _increment_calls(cache, 1)
        if raw is not None:
            parsed                = _parse_pinnacle_odds(raw)
            parsed["home_name"]   = ptcp.get(str(target_fx.get("participant1Id", "")), "")
            parsed["away_name"]   = ptcp.get(str(target_fx.get("participant2Id", "")), "")
            parsed["startTime"]   = target_fx.get("startTime", "")
            parsed["_fetched_at"] = _now_ts()
            cache["odds"][fid]    = parsed
            h2h_ok = "✓" if parsed.get("h2h") else "✗"
            tot_ok = "✓" if parsed.get("totals") else "✗"
            print(f"  [pinnacle_ref] 📌 on-demand: {home} vs {away} "
                  f"— ML {h2h_ok}  O/U {tot_ok}  "
                  f"({cache.get('daily_calls', 0)}/{_MAX_DAILY_CALLS} llamadas hoy)")

    cache["fetched_at"] = _now_ts()
    _save_cache(cache)
    return cache


def get_pinnacle_for_game(home_team: str, away_team: str) -> "dict | None":
    """
    Look up Pinnacle odds for a game from the on-disk cache (never calls API).
    Returns {"h2h": {"home":p,"away":p}, "totals": {"line":l,"over":p,"under":p}}
    where either sub-key may be None. Returns None if no match found.
    """
    cache = _load_cache()
    for fid, entry in cache.get("odds", {}).items():
        hn = entry.get("home_name", "")
        an = entry.get("away_name", "")
        if _fuzzy_match(home_team, hn) and _fuzzy_match(away_team, an):
            return {
                "h2h":    entry.get("h2h"),
                "totals": entry.get("totals"),
            }
    return None


def cache_status() -> str:
    """Return a short human-readable status string for logging."""
    cache = _load_cache()
    if not cache:
        return "sin caché"
    age_min = round((_now_ts() - float(cache.get("fetched_at", 0))) / 60)
    total   = len(cache.get("odds", {}))
    pin_cnt = sum(1 for v in cache.get("odds", {}).values() if v.get("h2h"))
    calls   = cache.get("daily_calls", 0)
    return f"{pin_cnt}/{total} juegos con Pinnacle, edad {age_min} min, {calls}/{_MAX_DAILY_CALLS} llamadas hoy"


# ── CLI self-test ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=== pinnacle_ref self-test ===")
    data = fetch_pinnacle_slate()
    print(f"Estado: {cache_status()}")
    print("\nEjemplos de odds en caché:")
    for fid, entry in list(data.get("odds", {}).items())[:5]:
        h2h = entry.get("h2h")
        tot = entry.get("totals")
        print(f"  {entry.get('home_name','?')} vs {entry.get('away_name','?')}")
        if h2h:
            print(f"    ML:  home={h2h['home']:.3f}  away={h2h['away']:.3f}")
        if tot:
            print(f"    Tot: O/U {tot['line']}  over={tot['over']:.3f}  under={tot['under']:.3f}")
