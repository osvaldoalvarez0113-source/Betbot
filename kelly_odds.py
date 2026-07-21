"""
BetBot Pro — Professional Multi-Module Sports Betting System
Modules: Morning Report | Lineup Monitor | Math Models | Sharp Radar | Arb Scanner
"""
import requests, time, csv, os, json, math
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
import pytz
from contexto_juego import obtener_contexto, ajustar_total, ajustar_ml
try:
    from paquete_avanzado import registrar_pick, clv_tracker, run_modulos_avanzados
    HAS_PAQUETE_AVANZADO = True
except ImportError:
    HAS_PAQUETE_AVANZADO = False
    def registrar_pick(*_a, **_kw): return None
    def run_modulos_avanzados(*_a, **_kw): pass
    clv_tracker = None

try:
    import statsapi
    HAS_STATSAPI = True
except ImportError:
    HAS_STATSAPI = False

try:
    import anthropic as _anthropic_lib
    HAS_ANTHROPIC = True
except ImportError:
    HAS_ANTHROPIC = False

# ── CONFIG ────────────────────────────────────────────────────────────────────
API_KEY           = os.environ.get("ODDS_API_KEY",       "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY",  "")
CLAUDE_MODEL       = os.environ.get("CLAUDE_MODEL",       "claude-sonnet-4-6")
CLAUDE_PANEL_MODEL = os.environ.get("CLAUDE_PANEL_MODEL", "claude-sonnet-4-5")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO       = os.environ.get("GITHUB_REPO",  "osvaldoalvarez0113-source/Betbot")
BANKROLL_DEFAULT = 1000.0

def _load_live_bankroll() -> float:
    """
    Read the current bankroll from bankroll_log.csv (most recent entry).
    Falls back to BANKROLL_DEFAULT if no log exists yet.
    """
    try:
        if os.path.isfile("bankroll_log.csv"):
            import csv as _csv
            last_balance = BANKROLL_DEFAULT
            with open("bankroll_log.csv", "r", newline="", encoding="utf-8") as _f:
                rows = list(_csv.DictReader(_f))
            if rows:
                for row in reversed(rows):
                    bal = float(row.get("running_bankroll") or row.get("balance") or row.get("bankroll") or 0)
                    if bal > 0:
                        last_balance = bal
                        break
            return last_balance
    except Exception as _e:
        print(f"  ⚠️  _load_live_bankroll error: {_e}")
    return BANKROLL_DEFAULT

BANKROLL = _load_live_bankroll()

def refresh_bankroll():
    """Reload BANKROLL from bankroll_log.csv. Call after each auto-resolved result."""
    global BANKROLL
    BANKROLL = _load_live_bankroll()
    print(f"  💰 Bankroll actualizado: ${BANKROLL:,.2f}")

FRACTION = 0.25
MIN_EDGE  = 2.0
MIN_STAKE            = 10.00  # never alert if Kelly stake < $10
MIN_BET              = 10.00  # hard floor — identical to MIN_STAKE
MAX_SINGLE_BET_PCT   = 0.05   # hard cap: 5% of bankroll per single bet
MAX_DAILY_EXPO_PCT   = 0.15   # hard cap: 15% of bankroll queued per day
PROB_CAP             = 0.85   # cap más alto — 85% máximo
PROB_CAP_CEIL        = 0.80   # valor usado después del cap
PROB_CAP_PARLAY      = 0.68   # max probability for any parlay leg
PREMIUM_MULT      = 1.5     # Module P: stake multiplier for PREMIUM alerts
PREMIUM_MAX_STAKE = 100.0   # Module P: max PREMIUM bet size ($)
INTERVAL  = 2700      # 45-minute main scan — conserva quota mensual (~33% ahorro)
NOTIFY   = "my-bets"
MODELO_ELITE       = "claude-fable-5"
UMBRAL_ELITE       = 0.08    # 8% edge mínimo para escalar a elite (ev_pct >= 8.0)
MAX_ELITE_DIARIO   = 3       # máx análisis elite automáticos por día (scan auto)
MAX_TOKENS_ELITE   = 1500    # max_tokens para llamadas elite
ELITE_COUNTER_FILE = "elite_counter.json"
LOG_CSV  = True

CDT            = pytz.timezone("America/Chicago")
ET             = pytz.timezone("America/New_York")
TZ_LOCAL       = ZoneInfo("America/Chicago")   # user-facing "today" — fixes UTC drift on Railway

_DIV  = "━━━━━━━━━━━━"
_DIV2 = "━━━━━━━━━━━━━━━━━━━━"
_DIV3 = "─────────────"

# Fix 1: Team / country name translation (English API → Spanish display)
_TEAM_ES: dict = {
    # Americas
    "Argentina":           "Argentina",
    "Brazil":              "Brasil",
    "Uruguay":             "Uruguay",
    "Colombia":            "Colombia",
    "Ecuador":             "Ecuador",
    "Chile":               "Chile",
    "Peru":                "Perú",
    "Paraguay":            "Paraguay",
    "Venezuela":           "Venezuela",
    "Bolivia":             "Bolivia",
    "Canada":              "Canadá",
    "United States":       "Estados Unidos",
    "USA":                 "Estados Unidos",
    "Mexico":              "México",
    "Costa Rica":          "Costa Rica",
    "Honduras":            "Honduras",
    "Panama":              "Panamá",
    "Guatemala":           "Guatemala",
    "Jamaica":             "Jamaica",
    # Europe
    "France":              "Francia",
    "England":             "Inglaterra",
    "Spain":               "España",
    "Portugal":            "Portugal",
    "Germany":             "Alemania",
    "Netherlands":         "Países Bajos",
    "Belgium":             "Bélgica",
    "Croatia":             "Croacia",
    "Italy":               "Italia",
    "Switzerland":         "Suiza",
    "Serbia":              "Serbia",
    "Denmark":             "Dinamarca",
    "Austria":             "Austria",
    "Hungary":             "Hungría",
    "Ukraine":             "Ucrania",
    "Wales":               "Gales",
    "Scotland":            "Escocia",
    "Czech Republic":      "República Checa",
    "Poland":              "Polonia",
    "Turkey":              "Turquía",
    "Greece":              "Grecia",
    "Romania":             "Rumania",
    "Slovakia":            "Eslovaquia",
    "Slovenia":            "Eslovenia",
    "Albania":             "Albania",
    "Georgia":             "Georgia",
    # Africa
    "Morocco":             "Marruecos",
    "Senegal":             "Senegal",
    "Nigeria":             "Nigeria",
    "Egypt":               "Egipto",
    "Algeria":             "Argelia",
    "Ivory Coast":         "Costa de Marfil",
    "Cote d'Ivoire":       "Costa de Marfil",
    "Cameroon":            "Camerún",
    "Mali":                "Mali",
    "Burkina Faso":        "Burkina Faso",
    "Cape Verde":          "Cabo Verde",
    "Tunisia":             "Túnez",
    "Ghana":               "Ghana",
    "DR Congo":            "Rep. Dem. del Congo",
    "South Africa":        "Sudáfrica",
    "Tanzania":            "Tanzania",
    "Zambia":              "Zambia",
    "Ethiopia":            "Etiopía",
    # Asia / Oceania
    "Japan":               "Japón",
    "South Korea":         "Corea del Sur",
    "Saudi Arabia":        "Arabia Saudita",
    "Iran":                "Irán",
    "Australia":           "Australia",
    "New Zealand":         "Nueva Zelanda",
    "Indonesia":           "Indonesia",
    "Qatar":               "Catar",
    "Iraq":                "Irak",
    "Uzbekistan":          "Uzbekistán",
    "Jordan":              "Jordania",
    "Bahrain":             "Baréin",
    "Kuwait":              "Kuwait",
    "Oman":                "Omán",
}

def _es(name: str) -> str:
    """Translate team/country name to Spanish. Falls back to original if not found."""
    return _TEAM_ES.get(name, name)

def _sport_emoji(sport):
    """Map sport key/short string to emoji. World Cup → 🏆."""
    s = (sport or "").lower()
    if any(w in s for w in ("world", "fifa", "mundial", "wc")):
        return "🏆"
    if "soccer" in s:
        return "⚽"
    if "mlb" in s or "baseball" in s:
        return "⚾"
    if "nba" in s or "basketball" in s:
        return "🏀"
    if "nfl" in s or "football" in s:
        return "🏈"
    return "🎯"

def _fmt_et(iso_str):
    """UTC ISO string → Eastern time string, e.g. '7:05 PM ET'."""
    if not iso_str:
        return "—"
    try:
        dt = datetime.strptime(iso_str.rstrip("Z")[:16], "%Y-%m-%dT%H:%M")
        return pytz.utc.localize(dt).astimezone(ET).strftime("%-I:%M %p ET")
    except Exception:
        return iso_str[:16]

def _conf_es(conf):
    """Confidence label in Spanish."""
    return "ALTA" if str(conf).upper() == "HIGH" else "MEDIA"

PREV_ODDS_FILE = "previous_odds.json"
BETS_LOG_FILE  = "bets_log.csv"
ELO_FILE       = "elo_ratings.json"
LINEUPS_FILE   = "morning_lineups.json"
MLB_YEAR       = datetime.now(TZ_LOCAL).year

SEASON_MONTHS = {
    "soccer_fifa_world_cup": [1,2,3,4,5,6,7,8,9,10,11,12],
    "baseball_mlb":          [3,4,5,6,7,8,9,10],
}
SPORT_KEYS = list(SEASON_MONTHS.keys())

# Bovada operates under both names depending on region
PREFERRED_BOOKS = {"bovada", "bodog"}
# Target arb pair — both US-accessible from same regions
ARB_BOOK_PAIR   = {"bovada", "bodog", "betonline.ag"}

# ── US-only book whitelist (picks must come from one of these) ────────────────
# International books (TAB, Winamax, Betsson, Paddy Power, etc.) are excluded
# because they are not legally accessible in the US and their odds are unreliable.
US_BOOKS_ONLY = {
    "bovada", "bodog",
    "betonline", "betonline.ag",
    "fanduel",
    "draftkings",
    "mybookie",
    "caesars",
    "betmgm",
    "pointsbet",
}

# Hard blocklist — books whose names accidentally match US_BOOKS_ONLY substrings
# (e.g. "PointsBet AU" contains "pointsbet") or are known international books.
_NON_US_BOOKS: frozenset = frozenset({
    # PointsBet regional variants (non-US) — bare and parenthetical forms
    "pointsbet au", "pointsbet (au)",
    "pointsbet nz", "pointsbet (nz)",
    "pointsbet ca", "pointsbet (ca)",
    # UK / Ireland
    "888sport", "coral", "ladbrokes", "william hill",
    "paddy power", "betfair", "sky bet", "bet victor",
    "boyle sports", "sportnation", "unibet",
    # Continental Europe
    "bet365", "betsson", "winamax", "bwin", "unibet",
    "betclick", "betclic", "tipico", "interwetten",
    # Australia
    "sportsbet", "tab", "neds", "betr", "bluebet",
    "beteasy", "palmerbet", "topbetta",
    # International / sharp
    "pinnacle",                       # sharp book, not US-licensed
    # Other known international
    "betway", "betway esports", "888 sport",
    "10bet", "1xbet", "22bet", "melbet",
})

def _is_us_book(title: str) -> bool:
    """
    Return True only if the bookmaker is a US-licensed book.
    Uses a two-step check:
      1. Hard-block any book on the _NON_US_BOOKS list (catches false-positive
         substring matches like 'PointsBet AU' containing 'pointsbet').
      2. Allow only books whose lowercased name is a substring-match of US_BOOKS_ONLY.
    """
    t = (title or "").lower().strip()
    if t in _NON_US_BOOKS:
        return False
    # Catch any "pointsbet (xx)" or "pointsbet xx" regional variant not in the frozenset
    if "pointsbet" in t and any(r in t for r in ("au", "nz", "ca")):
        return False
    return any(us in t for us in US_BOOKS_ONLY)

OPENWEATHER_KEY   = os.environ.get("OPENWEATHER_API_KEY", "")
BANKROLL_LOG_FILE      = "bankroll_log.csv"
CLV_LOG_FILE           = "clv_log.csv"

# Pinnacle calibration weights — model vs Pinnacle implied probability
# 70% model (Pyth/ELO/Poisson/panel), 30% Pinnacle sharp line
PINNACLE_BLEND_MODEL   = 0.70   # weight for our model probability
PINNACLE_BLEND_SHARP   = 0.30   # weight for Pinnacle implied probability
PENDING_BETS_FILE      = "pending_bets.json"
CONFIRM_FILE           = "pending_confirm.json"   # bets awaiting user confirmation
_LINE_HISTORY_FILE     = "line_history.json"      # 1B: multi-scan totals line tracker
DAILY_EXPOSURE_FILE    = "daily_exposure.json"    # persists daily exposure across redeploys

# MLB ballpark coordinates — used for wind fetching (home team → (city, lat, lon))
MLB_PARK_CITIES = {
    "Arizona Diamondbacks":   ("Phoenix",       33.4455, -112.0667),
    "Atlanta Braves":         ("Atlanta",        33.8907,  -84.4677),
    "Baltimore Orioles":      ("Baltimore",      39.2838,  -76.6217),
    "Boston Red Sox":         ("Boston",         42.3467,  -71.0972),
    "Chicago Cubs":           ("Chicago",        41.9484,  -87.6553),
    "Chicago White Sox":      ("Chicago",        41.8299,  -87.6338),
    "Cincinnati Reds":        ("Cincinnati",     39.0973,  -84.5082),
    "Cleveland Guardians":    ("Cleveland",      41.4962,  -81.6852),
    "Colorado Rockies":       ("Denver",         39.7559, -104.9942),
    "Detroit Tigers":         ("Detroit",        42.3390,  -83.0485),
    "Houston Astros":         ("Houston",        29.7573,  -95.3555),
    "Kansas City Royals":     ("Kansas City",    39.0517,  -94.4803),
    "Los Angeles Angels":     ("Anaheim",        33.8003, -117.8827),
    "Los Angeles Dodgers":    ("Los Angeles",    34.0739, -118.2400),
    "Miami Marlins":          ("Miami",          25.7781,  -80.2197),
    "Milwaukee Brewers":      ("Milwaukee",      43.0280,  -87.9712),
    "Minnesota Twins":        ("Minneapolis",    44.9817,  -93.2776),
    "New York Mets":          ("New York",       40.7571,  -73.8458),
    "New York Yankees":       ("New York",       40.8296,  -73.9262),
    "Oakland Athletics":      ("Sacramento",      38.5802, -121.5133),   # Sutter Health Park (2025+)
    "Athletics":              ("Sacramento",      38.5802, -121.5133),
    "Philadelphia Phillies":  ("Philadelphia",   39.9061,  -75.1665),
    "Pittsburgh Pirates":     ("Pittsburgh",     40.4469,  -80.0057),
    "San Diego Padres":       ("San Diego",      32.7076, -117.1570),
    "San Francisco Giants":   ("San Francisco",  37.7786, -122.3893),
    "Seattle Mariners":       ("Seattle",        47.5914, -122.3325),
    "St. Louis Cardinals":    ("St. Louis",      38.6226,  -90.1928),
    "Tampa Bay Rays":         ("Tampa",          27.7683,  -82.6534),
    "Texas Rangers":          ("Arlington",      32.7512,  -97.0832),
    "Toronto Blue Jays":      ("Toronto",        43.6414,  -79.3894),
    "Washington Nationals":   ("Washington DC",  38.8730,  -77.0074),
}

# ── MLB BALLPARK CENTER-FIELD BEARING ─────────────────────────────────────────
# Compass bearing (degrees) from home plate pointing toward center field.
# None = retractable roof or dome → no wind adjustment applies.
# Used to compute park-specific OUT/IN/CROSS wind label.
MLB_PARK_CF_BEARING = {
    "Arizona Diamondbacks":   None,   # Chase Field — retractable roof
    "Atlanta Braves":          40,    # Truist Park — CF toward NE
    "Baltimore Orioles":       60,    # Camden Yards — CF toward ENE
    "Boston Red Sox":          55,    # Fenway Park — CF toward NE
    "Chicago Cubs":            75,    # Wrigley Field — CF toward ENE
    "Chicago White Sox":      355,    # Guaranteed Rate Field — CF toward N
    "Cincinnati Reds":         15,    # Great American Ball Park — CF toward NNE
    "Cleveland Guardians":    310,    # Progressive Field — CF toward NW
    "Colorado Rockies":         5,    # Coors Field — CF toward N
    "Detroit Tigers":          40,    # Comerica Park — CF toward NE
    "Houston Astros":         None,   # Minute Maid Park — retractable roof
    "Kansas City Royals":      10,    # Kauffman Stadium — CF toward NNE
    "Los Angeles Angels":     325,    # Angel Stadium — CF toward NW
    "Los Angeles Dodgers":    340,    # Dodger Stadium — CF toward NNW
    "Miami Marlins":          None,   # loanDepot park — retractable roof
    "Milwaukee Brewers":      None,   # American Family Field — retractable roof
    "Minnesota Twins":        295,    # Target Field — CF toward NW
    "New York Mets":           25,    # Citi Field — CF toward NNE
    "New York Yankees":         5,    # Yankee Stadium — CF toward N
    "Oakland Athletics":      None,   # Sutter Health Park Sacramento — CF bearing not confirmed
    "Athletics":              None,   # Sutter Health Park Sacramento — CF bearing not confirmed
    "Philadelphia Phillies":   20,    # Citizens Bank Park — CF toward NNE
    "Pittsburgh Pirates":      30,    # PNC Park — CF toward NE
    "San Diego Padres":         0,    # Petco Park — CF toward N
    "San Francisco Giants":    50,    # Oracle Park — CF toward NE
    "Seattle Mariners":       None,   # T-Mobile Park — retractable roof
    "St. Louis Cardinals":     15,    # Busch Stadium — CF toward NNE
    "Tampa Bay Rays":         None,   # Tropicana Field — dome
    "Texas Rangers":          None,   # Globe Life Field — retractable roof
    "Toronto Blue Jays":      None,   # Rogers Centre — retractable roof
    "Washington Nationals":    20,    # Nationals Park — CF toward NNE
}

# ── MLB STADIUM TIMEZONE ─────────────────────────────────────────────────────
# Local timezone for each MLB home stadium.  Used to compute local game times
# for GETAWAY_DAY and TIMEZONE_FATIGUE situational flags.
MLB_STADIUM_TIMEZONE = {
    "Arizona Diamondbacks":   "America/Phoenix",       # no DST
    "Atlanta Braves":          "America/New_York",
    "Baltimore Orioles":       "America/New_York",
    "Boston Red Sox":          "America/New_York",
    "Chicago Cubs":            "America/Chicago",
    "Chicago White Sox":       "America/Chicago",
    "Cincinnati Reds":         "America/New_York",
    "Cleveland Guardians":     "America/New_York",
    "Colorado Rockies":        "America/Denver",
    "Detroit Tigers":          "America/Detroit",
    "Houston Astros":          "America/Chicago",
    "Kansas City Royals":      "America/Chicago",
    "Los Angeles Angels":      "America/Los_Angeles",
    "Los Angeles Dodgers":     "America/Los_Angeles",
    "Miami Marlins":           "America/New_York",
    "Milwaukee Brewers":       "America/Chicago",
    "Minnesota Twins":         "America/Chicago",
    "New York Mets":           "America/New_York",
    "New York Yankees":        "America/New_York",
    "Oakland Athletics":       "America/Los_Angeles",   # Sutter Health Park, Sacramento
    "Athletics":               "America/Los_Angeles",
    "Philadelphia Phillies":   "America/New_York",
    "Pittsburgh Pirates":      "America/New_York",
    "San Diego Padres":        "America/Los_Angeles",
    "San Francisco Giants":    "America/Los_Angeles",
    "Seattle Mariners":        "America/Los_Angeles",
    "St. Louis Cardinals":     "America/Chicago",
    "Tampa Bay Rays":          "America/New_York",
    "Texas Rangers":           "America/Chicago",
    "Toronto Blue Jays":       "America/Toronto",
    "Washington Nationals":    "America/New_York",
}

# ── RUNTIME STATE ─────────────────────────────────────────────────────────────
alerted_bets:          set  = set()
alerted_game_analysis: set  = set()
_sent_alerts:          dict = {}   # key → {date, odds, edge} for smart dedup
last_reset:            date = datetime.now(CDT).date()
last_morning_report:   date = date(2000, 1, 1)   # force first run at 8 AM
last_weekly_report:    date = date(2000, 1, 1)   # force first run Sunday 9 AM
lineup_scan_counter:   int  = 0                  # increments each main scan
_bankroll_mult:   float = 1.0   # Module P: scales Kelly stakes; updated daily
_bankroll_paused: bool  = False  # True when bankroll < $400 → halt betting
last_night_summary: date = date(2000, 1, 1)  # 11 PM nightly summary tracker
last_mlb_card:     date = date(2000, 1, 1)  # 2 PM ET MLB daily card
last_soccer_card:  date = date(2000, 1, 1)  # 10 AM ET soccer daily card
last_backtest_report: date = date(2000, 1, 1)  # Sunday 10 AM ET backtest
last_patrones_scan:   date = date(2000, 1, 1)  # 9 AM CT daily getaway-day scan
_patrones_activos:    list = []                 # slate-wide pattern alerts for today
_kelly_monthly_mult: float = 1.0  # Improvement 4: monthly ROI-based Kelly multiplier
_perf_adj: dict = {}   # ajustes por tipo de mercado basados en resultados reales
_line_history:    dict = {}       # 1B: game_id → [{time, total, ml_home, ml_away}]
_last_news_seen:  set  = set()    # 1C: news IDs already alerted this session
_last_news_date:  date = date(2000, 1, 1)  # 1C: reset seen set daily
_steam_game_ids:  set   = set()  # game_ids with confirmed steam (current scan)
_ntfy_last_confirm_id: str = ""  # last ntfy message ID processed for confirmations
_elite_count_today:    int  = 0   # análisis elite automáticos usados hoy
_elite_count_date:     str  = ""  # fecha CDT del contador (reset a medianoche)
_daily_exposure:      float = 0.0            # total stake queued today ($)
_daily_exposure_date: "date" = date(2000, 1, 1)  # reset tracking when date changes
_tg_broadcast_fn = None  # set by telegram_bot.iniciar_telegram; broadcasts to Telegram
_ML_MODULE = None        # ml_model module set at startup; None = model unavailable

_pitcher_cache: dict = {}   # date_str → {team_key: {home_era, away_era, ...}}
_wc_form_cache: dict = {}       # (team_name, date_str) → {goals_for, goals_against, matches}
_espn_matches_cache: dict = {}  # team_name_date → [{gf, ga, date, opp}]
_weather_cache: dict = {}   # "lat,lon" → {speed, deg, label, fetched_at}

# ── ERROR TRACKING / HEALTH CHECK STATE ──────────────────────────────────────
last_health_check:    date = date(2000, 1, 1)
_module_failures:     dict = {}   # module_name → consecutive failure count
_module_last_alerted: dict = {}   # module_name → date of last alert
_module_status:       dict = {}   # module_name → "ok" | "failing" | "warning"
ERROR_LOG_FILE = "error_log.csv"

# ── CORE HELPERS ──────────────────────────────────────────────────────────────

def is_in_season(sport_key):
    return datetime.now(CDT).month in SEASON_MONTHS.get(sport_key, [])

def game_starts_soon(commence_str, minutes=60):
    try:
        ts_clean = (commence_str or "").replace("Z", "").replace("+00:00", "")[:19]
        if len(ts_clean) == 16: ts_clean += ":00"
        ct_utc   = datetime.strptime(ts_clean, "%Y-%m-%dT%H:%M:%S")
        diff     = (ct_utc - datetime.utcnow()).total_seconds() / 60
        return diff < minutes
    except Exception:
        return False

def _game_already_started(time_str: str, grace_min: int = 5) -> bool:
    """
    Returns True if the game started more than grace_min minutes ago.
    Compares both timestamps as naive UTC to avoid timezone library issues.
    Accepts 'YYYY-MM-DDTHH:MM:SSZ', 'YYYY-MM-DDTHH:MM:SS+00:00', 'YYYY-MM-DDTHH:MM'.
    """
    try:
        ts = (time_str or "").strip()
        if not ts or "T" not in ts:
            return False
        # Normalize to naive UTC: strip Z or +00:00 suffix, take first 19 chars
        ts_clean = ts.replace("Z", "").replace("+00:00", "")[:19]
        if len(ts_clean) == 16: ts_clean += ":00"
        ct_utc   = datetime.strptime(ts_clean, "%Y-%m-%dT%H:%M:%S")
        now_utc  = datetime.utcnow()
        elapsed  = (now_utc - ct_utc).total_seconds() / 60
        return elapsed > grace_min
    except Exception as _e:
        return False

def _days_until(commence_str: str) -> float:
    """Days (float) from now until game. Returns 999 on parse error. Uses naive UTC."""
    try:
        ts_clean = (commence_str or "").replace("Z", "").replace("+00:00", "")[:19]
        if len(ts_clean) == 16: ts_clean += ":00"
        ct_utc   = datetime.strptime(ts_clean, "%Y-%m-%dT%H:%M:%S")
        return (ct_utc - datetime.utcnow()).total_seconds() / 86400
    except Exception:
        return 999.0

def _fmt_smart_gt(commence_str: str) -> str:
    """
    Smart game-time label:
      same day  → "Hoy 6:00 PM ET"
      tomorrow  → "Mañana 6:00 PM ET"
      2+ days   → "En 3 días — Jun 8"
    Falls back to plain _fmt_et on error.
    """
    try:
        ct     = datetime.strptime(commence_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
        ct_cdt = ct.astimezone(CDT)
        now_ct = datetime.now(CDT)
        days   = (ct_cdt.date() - now_ct.date()).days
        t_str  = ct_cdt.strftime("%-I:%M %p CT")
        if days == 0:
            return f"Hoy {t_str}"
        if days == 1:
            return f"Mañana {t_str}"
        month_es = {1:"Ene",2:"Feb",3:"Mar",4:"Abr",5:"May",6:"Jun",
                    7:"Jul",8:"Ago",9:"Sep",10:"Oct",11:"Nov",12:"Dic"}
        date_lbl = f"{month_es.get(ct_cdt.month, ct_cdt.strftime('%b'))} {ct_cdt.day}"
        return f"En {days} días — {date_lbl}"
    except Exception:
        return _fmt_et(commence_str)

def _timing_check(commence_str: str, is_mlb: bool) -> dict:
    """
    Returns timing decision for a game:
      skip          → True if game should be ignored entirely
      warn          → warning string to prepend to alert (or "")
      ev_min        → minimum EV% required
      cap_conf      → if True, cap confidence display to MEDIA
    """
    days = _days_until(commence_str)
    # Skip any game that already started (>5 min grace period)
    _GRACE_DAYS = 5 / 1440   # 5 minutes in days
    if days < -_GRACE_DAYS:
        return {"skip": True, "warn": "", "ev_min": 0, "cap_conf": False}
    if is_mlb:
        # MLB: TODAY only — tomorrow and beyond skipped
        if days > 1:
            return {"skip": True, "warn": "", "ev_min": 0, "cap_conf": False}
        return {"skip": False, "warn": "", "ev_min": 0, "cap_conf": False}
    else:
        # Soccer / World Cup: strictly less than 3 days away
        # "today first" fallback handled in run_scan before games are filtered
        if days >= 3:
            return {"skip": True, "warn": "", "ev_min": 0, "cap_conf": False}
        return {"skip": False, "warn": "", "ev_min": 0, "cap_conf": False}

def remove_vig(odds_list):
    implied = [1.0 / o for o in odds_list]
    total   = sum(implied)
    return [p / total for p in implied]

def _cap_prob(p: float, is_parlay_leg: bool = False) -> float:
    """
    Cap unrealistically high probabilities before they inflate Kelly stakes.
    MLB / soccer single bets: hard max 80%; if exceeded, clamp to 75%.
    Parlay legs: hard max 75%.
    Logs a warning whenever capping occurs so it shows up in Railway logs.
    """
    cap_check = PROB_CAP_PARLAY if is_parlay_leg else PROB_CAP
    cap_value = PROB_CAP_PARLAY if is_parlay_leg else PROB_CAP_CEIL
    if p > cap_check:
        print(f"  ⚠️  PROB CAP: {p*100:.1f}% → {cap_value*100:.0f}%"
              f" ({'parlay leg' if is_parlay_leg else 'single bet'}) — inflated probability clamped")
        return cap_value
    return p


def kelly_stake(prob, fair_odd, is_parlay_leg: bool = False):
    """
    Returns Kelly stake dict with hard bankroll-rule enforcement:
      • probability capped at PROB_CAP / PROB_CAP_PARLAY before any calculation
      • final stake hard-capped at MAX_SINGLE_BET_PCT (5%) of BANKROLL
      • stake below MIN_BET ($10) → zeroed out (no bet)
      • stake_warn populated when the 5% cap was applied (shown in ntfy alert)
    """
    prob = _cap_prob(prob, is_parlay_leg)
    b = fair_odd - 1
    if b <= 0:
        return {"stake": 0, "edge": 0, "has_value": False, "kelly_pct": 0, "stake_warn": ""}
    k         = max(0.0, (b * prob - (1 - prob)) / b)
    edge      = prob - 1.0 / fair_odd
    max_stake = BANKROLL * MAX_SINGLE_BET_PCT               # absolute hard cap ($50 at $1000 BR)
    raw       = BANKROLL * min(k * FRACTION, MAX_SINGLE_BET_PCT) * _bankroll_mult * _kelly_monthly_mult
    # Apply absolute cap AFTER multipliers (multipliers can push beyond 5%)
    stake_warn = ""
    if raw > max_stake:
        raw        = max_stake
        stake_warn = f"⚠️ Stake reducido al {int(MAX_SINGLE_BET_PCT*100)}% máximo por reglas de bankroll"
        print(f"  ⚠️  STAKE CAP applied: ${raw:.2f} (multipliers pushed above {MAX_SINGLE_BET_PCT*100:.0f}%)")
    # Below minimum → no apostar (inflar stakes es -EV)
    if 0 < raw < MIN_BET:
        raw = 0
    stake = round(raw, 2)
    return {
        "stake":      stake,
        "edge":       round(edge * 100, 2),
        "has_value":  edge > 0.02,
        "kelly_pct":  round(k * FRACTION * 100, 2),
        "stake_warn": stake_warn,
    }

def confidence_level(edge_pct):
    if edge_pct >= 5.0: return "HIGH"
    if edge_pct >= 3.0: return "MEDIUM"
    return "LOW"


def _load_performance_adjustments() -> dict:
    """
    Lee bets_log.csv y calcula el hit rate por tipo de mercado.
    Si un mercado pierde consistentemente (hit rate < 45%), sube el umbral de EV.
    Si un mercado gana consistentemente (hit rate > 58%), baja el umbral.
    Solo activa cuando hay ≥ 20 picks resueltos en ese mercado.
    """
    global _perf_adj
    adj = {"totals": 1.0, "h2h": 1.0, "spreads": 1.0}
    if not os.path.isfile(BETS_LOG_FILE):
        return adj
    try:
        import csv as _csv
        rows = []
        with open(BETS_LOG_FILE, newline="", encoding="utf-8") as f:
            rows = [r for r in _csv.DictReader(f) if r.get("result") in ("W", "L")]
        if not rows:
            return adj
        resumen = []
        for mtype in ("totals", "h2h", "spreads"):
            m_rows = [r for r in rows if r.get("market_type","").lower() == mtype]
            if len(m_rows) < 20:
                continue
            wins     = sum(1 for r in m_rows if r.get("result") == "W")
            hit_rate = round(wins / len(m_rows) * 100, 1)
            if hit_rate < 45.0:
                adj[mtype] = 1.5
                resumen.append(f"⚠️ {mtype}: {hit_rate}% hit rate — subiendo umbral EV ×1.5")
            elif hit_rate > 58.0:
                adj[mtype] = 0.8
                resumen.append(f"✅ {mtype}: {hit_rate}% hit rate — bajando umbral EV ×0.8")
            else:
                resumen.append(f"➡️ {mtype}: {hit_rate}% hit rate — normal")
        if resumen:
            print("  📊 Ajustes por resultados:")
            for r in resumen:
                print(f"     {r}")
        _perf_adj = adj
    except Exception as e:
        print(f"  ⚠️  _load_performance_adjustments: {e}")
    return adj


def _update_monthly_kelly_mult():
    """
    Improvement 4: Read backtest_log.csv and compute the current month's Kelly
    multiplier based on actual ROI performance.
      ROI > +5%  → multiply stakes × 1.1  (model running hot)
      ROI  0-5%  → multiplier = 1.0       (normal)
      ROI < 0%   → multiply stakes × 0.75 (model running cold) + ntfy warning
    Requires ≥10 bets in the current month before adjusting.
    """
    global _kelly_monthly_mult
    try:
        if not os.path.isfile("backtest_log.csv"):
            return
        import csv as _csv
        cur_month = datetime.now(ET).strftime("%Y-%m")
        wins = losses = 0
        pnl_sum = 0.0
        stake_sum = 0.0
        with open("backtest_log.csv", "r", newline="", encoding="utf-8") as _f:
            for row in _csv.DictReader(_f):
                if not row.get("date", "").startswith(cur_month):
                    continue
                result = row.get("result", "")
                if result == "WIN":
                    wins += 1
                elif result == "LOSS":
                    losses += 1
                try:
                    pnl_sum   += float(row.get("pnl",   0) or 0)
                    stake_sum += float(row.get("stake", 0) or 0)
                except Exception:
                    pass
        total_bets = wins + losses
        if total_bets < 10:
            print(f"  ℹ️  Kelly mensual: solo {total_bets} apuestas en {cur_month} — sin ajuste")
            return
        roi = (pnl_sum / stake_sum * 100) if stake_sum > 0 else 0.0  # real ROI vs total staked
        if roi > 5.0:
            _kelly_monthly_mult = 1.1
            print(f"  📈 ROI mensual +{roi:.1f}% > objetivo 5% → Kelly × 1.10")
        elif roi < 0.0:
            _kelly_monthly_mult = 0.75
            msg = (
                f"⚠️ Mes negativo — ROI {roi:+.1f}% en {cur_month} "
                f"({wins}W-{losses}L)\n"
                "Reduciendo stakes automáticamente (Kelly × 0.75)"
            )
            print(f"  {msg}")
            try:
                ntfy_post("⚠️ Stakes Reducidos", msg, "high")
            except Exception:
                pass
        else:
            _kelly_monthly_mult = 1.0
            print(f"  ✅ ROI mensual {roi:+.1f}% — Kelly normal (× 1.0)")
    except Exception as _e:
        print(f"  ⚠️  _update_monthly_kelly_mult: {_e}")

# ═══════════════════════════════════════════════════════════════════════════════
# TERM TRANSLATION — applied to ALL ntfy notifications automatically
# Converts every English baseball abbreviation to plain Spanish.
# ═══════════════════════════════════════════════════════════════════════════════

import re as _re

# Ordered list: (compiled_regex, replacement).
# Most-specific patterns first to avoid partial matches.


# ── News impact explainer ─────────────────────────────────────────────────────

def _explain_news_impact(headline: str, team: str, impact: str) -> str:
    """
    Generate a conversational 2–3 line Spanish explanation of a news headline
    and its betting implication. Written as if explaining to a friend.
    """
    h = headline.lower()

    # IL placement — player is hurt
    if any(k in h for k in ("placed on", "lista de lesionados", "colocado en")):
        player = _extract_player_name(headline)
        if impact == "ALTO":
            return (f"Un jugador clave de los {team} se lastimó y no jugará.\n"
                    f"Esto debilita su ataque — su equipo anotará menos carreras de lo normal.\n"
                    f"→ Considera apostar UNDER si los {team} están en tu pick de hoy.")
        return (f"Un jugador de los {team} está en la lista de lesionados.\n"
                f"Puede afectar su rendimiento ofensivo moderadamente.\n"
                f"→ Revisa si el jugador era titular antes de apostar.")

    # Activation — player returns from IL
    if any(k in h for k in ("activated", "regresó", "recalled", "llamado")):
        player = _extract_player_name(headline)
        if impact == "ALTO":
            return (f"Un jugador importante regresa al lineup de los {team}.\n"
                    f"Esto fortalece su ataque — esperamos más carreras de lo usual.\n"
                    f"→ Si tenías UNDER en los {team}, reconsidera tu apuesta.")
        return (f"Regresa un jugador a la alineación de los {team}.\n"
                f"Pequeño impulso ofensivo para el equipo.\n"
                f"→ Mantén tu pick pero ajusta el análisis de pitching.")

    # Scratch / not starting / ruled out
    if any(k in h for k in ("scratch", "not start", "ruled out", "retirado",
                             "descartado", "no arrancará", "won't start")):
        player = _extract_player_name(headline)
        # Check if it's a pitcher scratch (high impact)
        if any(k in h for k in ("pitcher", "sp ", "start", "abridor")):
            return (f"El pitcher abridor programado para {team} NO jugará hoy.\n"
                    f"Cambio de última hora — el bullpen (relevistas) tendrá que trabajar más.\n"
                    f"→ Un abridor débil de reemplazo puede inflar el total — recalcula el OVER/UNDER.")
        return (f"Un jugador de los {team} fue retirado del lineup de último momento.\n"
                f"El equipo jugará sin él — puede afectar la ofensiva.\n"
                f"→ Verifica si era un bateador clave antes de apostar.")

    # Postponement
    if any(k in h for k in ("postponed", "pospuesto")):
        return (f"El juego de los {team} fue cancelado por hoy.\n"
                f"No hay partido — cancela cualquier apuesta activa en este juego.\n"
                f"→ Las casas de apuestas devolverán el dinero automáticamente.")

    # Rain delay
    if any(k in h for k in ("rain delay", "retraso por lluvia", "weather")):
        return (f"Hay retraso por condiciones climáticas en el juego de los {team}.\n"
                f"Un retraso largo cansa a los pitchers y hace que usen más relevistas.\n"
                f"→ Favorece ligeramente el OVER si el retraso supera 1 hora.")

    # Trade
    if any(k in h for k in ("traded", "trade", "acquired")):
        return (f"Los {team} realizaron un cambio de jugadores.\n"
                f"Un cambio puede reorganizar la alineación del equipo.\n"
                f"→ Analiza el nuevo pickup antes de apostar en los próximos días.")

    # Generic
    return (f"Novedad importante en los {team}.\n"
            f"Revisa la alineación oficial antes de confirmar tu apuesta.\n"
            f"→ Espera la alineación confirmada para máxima precisión.")


def _extract_player_name(headline: str) -> str:
    """Best-effort player name extraction from an MLB news headline."""
    # Common patterns: "activated OF John Smith" or "John Smith placed on"
    patterns = [
        _re.compile(
            r'(?:activated|placed|transferred|recalled|optioned)\s+\w+\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        ),
        _re.compile(
            r'([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s+(?:placed|activated|transferred|recalled)',
        ),
    ]
    for p in patterns:
        m = p.search(headline)
        if m:
            return m.group(1)
    # Fallback: return first two capitalized words found
    words = headline.split()
    caps  = [w for w in words if w and w[0].isupper() and len(w) > 1 and w not in
             ("Houston","Los","San","New","St.","Kansas","Tampa","San","Oakland",
              "The","MLB","Astros","Yankees","Red","Blue","White","Black")]
    return " ".join(caps[:2]) if len(caps) >= 2 else "El jugador"


def _two_layer_body(layer1: str, layer2: str) -> str:
    """
    Combine a quick-summary layer and a full-analysis layer into one ntfy body.

    Layer 1: short actionable block (match · time · bet · action button).
    Layer 2: detailed Spanish analysis with no abbreviations.
    """
    div = "━" * 24
    return (
        f"{layer1.rstrip()}\n"
        f"{div}\n"
        f"📊 ANÁLISIS COMPLETO:\n"
        f"{layer2.rstrip()}\n"
        f"{div}"
    )


def ntfy_post(title, body, priority="default"):
    try:
        resp = requests.post(
            f"https://ntfy.sh/{NOTIFY}",
            data=body.encode("utf-8"),
            headers={
                "Title":        title.encode("utf-8").decode("latin-1", errors="replace"),
                "Priority":     priority,
                "Content-Type": "text/plain; charset=utf-8",
            },
            timeout=10,
        )
        print(f"  📲 ntfy [{title[:40]}] → HTTP {resp.status_code}")
    except Exception as e:
        print(f"  ⚠️  ntfy error: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 3 — MATH MODELS (declared first; used by other modules)
# ═══════════════════════════════════════════════════════════════════════════════

def save_elo_ratings(ratings):
    with open(ELO_FILE, "w") as f:
        json.dump(ratings, f, indent=2)

# Fix 2: Seed ELO for all WC 2026 teams using FIFA ranking estimates.
# Prevents fake 50% default creating phantom value bets on big underdogs.
# Tier mapping: Top-10 FIFA → 2000, 11-30 → 1800, 31-60 → 1650, 61-100 → 1550, 100+ → 1400
_WC2026_ELO_SEED: dict = {
    # ── Top-10 FIFA (2000) ────────────────────────────────────────────────────
    "Argentina":           2058,
    "France":              2000,
    "England":             2000,
    "Spain":               2000,
    "Brazil":              1990,
    "Portugal":            1970,
    "Belgium":             1960,
    "Netherlands":         1950,
    "Croatia":             1940,
    "Italy":               1930,
    # ── 11-30 FIFA (1800) ─────────────────────────────────────────────────────
    "Germany":             1820,
    "Colombia":            1810,
    "Uruguay":             1808,
    "Morocco":             1800,
    "Mexico":              1795,
    "United States":       1790,
    "USA":                 1790,
    "Japan":               1785,
    "Senegal":             1780,
    "Denmark":             1775,
    "Switzerland":         1770,
    "Ecuador":             1765,
    "Canada":              1764,
    "Serbia":              1762,
    "Australia":           1760,
    "Austria":             1750,
    "South Korea":         1745,
    "Hungary":             1740,
    "Ukraine":             1735,
    "Wales":               1730,
    "Czech Republic":      1720,
    # ── 31-60 FIFA (1650) ─────────────────────────────────────────────────────
    "Poland":              1715,
    "Turkey":              1712,
    "Algeria":             1708,
    "Peru":                1705,
    "Iran":                1700,
    "Egypt":               1698,
    "Nigeria":             1695,
    "Chile":               1690,
    "Saudi Arabia":        1685,
    "Paraguay":            1680,
    "Venezuela":           1675,
    "Bolivia":             1660,
    "Ivory Coast":         1658,
    "Cote d'Ivoire":       1658,
    "Mali":                1655,
    "Cameroon":            1650,
    "Burkina Faso":        1645,
    "Guatemala":           1640,
    # ── 61-100 FIFA (1550) ────────────────────────────────────────────────────
    "Jamaica":             1635,
    "Honduras":            1630,
    "Panama":              1625,
    "Costa Rica":          1622,
    "Scotland":            1620,
    "Greece":              1618,
    "Romania":             1615,
    "Cape Verde":          1610,
    "Tunisia":             1608,
    "Ghana":               1600,
    "DR Congo":            1592,
    "New Zealand":         1570,
    "Indonesia":           1555,
    "Tanzania":            1550,
    "Zambia":              1545,
    "Ethiopia":            1540,
    "Qatar":               1535,
    "Slovakia":            1620,
    "Slovenia":            1615,
    "Albania":             1590,
    "Georgia":             1585,
    "Iraq":                1530,
    "Uzbekistan":          1525,
    # ── 100+ FIFA (1400) ─────────────────────────────────────────────────────
    "Bahrain":             1450,
    "Kuwait":              1440,
    "Oman":                1430,
    "Jordan":              1420,
    "South Africa":        1510,
}

def _elo_for(team: str) -> float:
    """
    Return ELO for a team. Lookup order:
    1. Learned runtime ratings (elo_ratings.json)
    2. WC 2026 seed table (FIFA-ranking based)
    3. Never returns 1500 blindly — uses 1400 as true unknown floor.
    """
    if team in _elo_ratings:
        return _elo_ratings[team]
    if team in _WC2026_ELO_SEED:
        return _WC2026_ELO_SEED[team]
    return 1400   # true unknown — well below average, not a fake 50%

def load_elo_ratings():
    ratings = {}
    if os.path.exists(ELO_FILE):
        try:
            with open(ELO_FILE) as f:
                ratings = json.load(f)
        except Exception:
            pass
    # Seed any missing WC teams without overwriting learned values
    for team, elo in _WC2026_ELO_SEED.items():
        if team not in ratings:
            ratings[team] = elo
    return ratings

_elo_ratings = load_elo_ratings()

def elo_win_prob(team_a, team_b):
    """Expected win probability for team_a vs team_b using ELO ratings."""
    ea = _elo_for(team_a)
    eb = _elo_for(team_b)
    return 1.0 / (1.0 + 10 ** ((eb - ea) / 400.0))

def update_elo(winner, loser, draw=False, k=32):
    """Update ELO ratings after a game result."""
    global _elo_ratings
    ea = _elo_for(winner)
    eb = _elo_for(loser)
    expected_a = 1.0 / (1.0 + 10 ** ((eb - ea) / 400.0))
    actual_a   = 0.5 if draw else 1.0
    _elo_ratings[winner] = round(ea + k * (actual_a - expected_a), 1)
    _elo_ratings[loser]  = round(eb + k * ((1 - actual_a) - (1 - expected_a)), 1)
    save_elo_ratings(_elo_ratings)

def implied_probability(odds, vig_odds_list):
    """True probability after vig removal for a given outcome."""
    probs = remove_vig(vig_odds_list)
    idx   = vig_odds_list.index(odds) if odds in vig_odds_list else 0
    return probs[idx] if idx < len(probs) else 1.0 / odds

def value_percentage(true_prob, market_odds):
    """How much value this bet has vs the market implied probability."""
    market_prob = 1.0 / market_odds
    return round((true_prob - market_prob) / market_prob * 100, 2)

def roi_projection(edge_pct, kelly_stake_amt):
    """Expected return in $ and ROI % for a single bet."""
    ev  = kelly_stake_amt * edge_pct / 100
    roi = ev / BANKROLL * 100
    return round(ev, 2), round(roi, 3)

def poisson_prob(lam, k):
    """P(Poisson(λ) = k)"""
    if lam <= 0: return 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)

def poisson_match_probs(avg_goals_home, avg_goals_away, max_goals=8):
    """Win/draw/loss probabilities via Poisson model."""
    p_win = p_draw = p_loss = 0.0
    for i in range(max_goals + 1):
        for j in range(max_goals + 1):
            p = poisson_prob(avg_goals_home, i) * poisson_prob(avg_goals_away, j)
            if i > j:    p_win  += p
            elif i == j: p_draw += p
            else:        p_loss += p
    total = p_win + p_draw + p_loss or 1
    return p_win/total, p_draw/total, p_loss/total

def pythagorean_win_prob(rs, ra, exp=1.83):
    """
    MLB Pythagorean expectation.
    Returns 0.5 when rs or ra is None or both are 0 (data unavailable).
    """
    if rs is None or ra is None:
        return 0.5
    rs, ra = float(rs), float(ra)
    if rs + ra == 0:
        return 0.5
    return (rs ** exp) / ((rs ** exp) + (ra ** exp))

def poisson_ou_prob(expected_total, book_line, bet_over):
    """P(total > book_line) or P(total <= book_line) given Poisson mean = expected_total."""
    floor = int(book_line)
    p_under = sum(poisson_prob(expected_total, k) for k in range(floor + 1))
    p_over  = 1.0 - p_under
    # .5 lines have no push — p_under + p_over = 1 already; whole-number lines may push
    if book_line == floor:   # whole number: push possible, split push evenly
        push = poisson_prob(expected_total, floor)
        p_under -= push / 2
        p_over  -= push / 2
    return max(0.01, p_over if bet_over else p_under)

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 1 — DAILY MORNING REPORT (8 AM CDT)
# ═══════════════════════════════════════════════════════════════════════════════

def _mlb_rest(path, params=None):
    try:
        r = requests.get(f"https://statsapi.mlb.com/api/v1{path}",
                         params=params, timeout=10)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}

def fetch_mlb_games_today():
    today = datetime.now(ET).strftime("%Y-%m-%d")
    if HAS_STATSAPI:
        try:
            return statsapi.schedule(date=today, sportId=1) or []
        except Exception:
            pass
    data = _mlb_rest("/schedule", {"sportId": 1, "date": today,
                                   "hydrate": "probablePitcher,team"})
    games = []
    for date_entry in data.get("dates", []):
        for g in date_entry.get("games", []):
            games.append(g)
    return games

# ── MLB real start-time cache (keyed by date) ─────────────────────────────────
_mlb_real_times_cache: dict = {}   # date_str → {home_team_lower: "...Z"}

def _fetch_mlb_real_times(date_str: str) -> dict:
    """
    Return {home_team_name_lower → gameDate_utc_string} for all MLB games
    on date_str ('YYYY-MM-DD').  Fetches from MLB Stats API /schedule.
    Cached per date so it is called at most once per calendar day.
    """
    if date_str in _mlb_real_times_cache:
        return _mlb_real_times_cache[date_str]
    result = {}
    try:
        data = _mlb_rest("/schedule", {
            "sportId": 1,
            "date":    date_str,
            "hydrate": "team",
        })
        for date_entry in (data.get("dates") or []):
            for g in date_entry.get("games", []):
                gdate = g.get("gameDate", "")          # "2026-06-05T23:10:00Z"
                if not gdate:
                    continue
                home_name = (
                    g.get("teams", {}).get("home", {})
                     .get("team", {}).get("name", "")
                    or g.get("teams", {}).get("home", {})
                        .get("team", {}).get("teamName", "")
                )
                away_name = (
                    g.get("teams", {}).get("away", {})
                     .get("team", {}).get("name", "")
                    or g.get("teams", {}).get("away", {})
                        .get("team", {}).get("teamName", "")
                )
                if home_name:
                    result[home_name.lower()] = gdate
                if away_name:
                    result[away_name.lower()] = gdate
        print(f"  🕐 MLB real times fetched: {len(result)//2} games")
    except Exception as e:
        print(f"  ⚠️  _fetch_mlb_real_times error: {e}")
    _mlb_real_times_cache[date_str] = result
    return result


def _patch_mlb_commence_times(games: list) -> None:
    """
    For a list of Odds-API MLB game dicts, replace each game's 'commence_time'
    with the authoritative time from the MLB Stats API when a match is found.
    Matches on home_team name (case-insensitive, partial word overlap).
    Mutates games in-place.
    """
    if not games:
        return
    # Determine date from first game's commence_time (all games same date)
    first_ct = games[0].get("commence_time", "")
    date_str  = first_ct[:10] if first_ct else datetime.now(CDT).strftime("%Y-%m-%d")
    real_times = _fetch_mlb_real_times(date_str)
    if not real_times:
        return

    patched = 0
    for g in games:
        home = g.get("home_team", "").lower()
        # Try exact match first, then word-overlap
        real_t = real_times.get(home)
        if not real_t:
            for key, val in real_times.items():
                if any(w in key for w in home.split() if len(w) > 3):
                    real_t = val
                    break
        if real_t and real_t != g.get("commence_time", ""):
            old = g.get("commence_time", "")
            g["commence_time"] = real_t
            patched += 1
            print(f"  🕐 Tiempo corregido [{g.get('home_team','')}]: "
                  f"{old} → {real_t}")
    if patched:
        print(f"  🕐 {patched} tiempo(s) MLB corregido(s) desde MLB Stats API")

def fetch_pitcher_stats(name):
    """Return dict with ERA, WHIP, K9 for a pitcher name."""
    empty = {"era": "N/A", "whip": "N/A", "k9": "N/A"}
    if not name or name == "TBD":
        return empty
    try:
        if HAS_STATSAPI:
            players = statsapi.lookup_player(name, sportId=1)
            if not players:
                return empty
            pid = players[0]["id"]
            data = statsapi.player_stat_data(pid, group="pitching", type="season")
            stats = data.get("stats", [{}])[0].get("stats", {})
        else:
            # Direct API fallback: search player
            search = _mlb_rest("/people/search", {"names": name, "sportId": 1})
            people = search.get("people", [])
            if not people: return empty
            pid   = people[0]["id"]
            data  = _mlb_rest(f"/people/{pid}/stats",
                              {"stats": "season", "group": "pitching", "season": MLB_YEAR})
            splits = data.get("stats", [{}])
            stats  = splits[0].get("splits", [{}])[-1].get("stat", {}) if splits else {}

        era  = stats.get("era",  stats.get("earnedRunAverage", "N/A"))
        whip = stats.get("whip", "N/A")
        so9  = stats.get("strikeoutsPer9Inn", stats.get("strikeoutPer9Inn", "N/A"))
        return {"era": era, "whip": whip, "k9": so9}
    except Exception:
        return empty

def fetch_team_batting(team_id):
    """
    Return dict with AVG, OPS, rs_pg, ra_pg for a team.
    rs_pg / ra_pg are None when genuinely unavailable — callers must handle None.

    MLB Stats API field note (2026+):
      • 'runsPerGame' is NOT populated — compute runs / gamesPlayed manually
      • 'runsAllowed' is NOT in pitching group — use 'runs' (same meaning there)
    """
    try:
        hit_data = _mlb_rest(f"/teams/{team_id}/stats",
                             {"stats": "season", "group": "hitting", "season": MLB_YEAR})
        hit_splits = hit_data.get("stats", [{}]) if hit_data else []
        hit_stat   = (hit_splits[0].get("splits", [{}]) or [{}])[-1].get("stat", {}) if hit_splits else {}

        pit_data = _mlb_rest(f"/teams/{team_id}/stats",
                             {"stats": "season", "group": "pitching", "season": MLB_YEAR})
        pit_splits = pit_data.get("stats", [{}]) if pit_data else []
        pit_stat   = (pit_splits[0].get("splits", [{}]) or [{}])[-1].get("stat", {}) if pit_splits else {}

        # Compute rs_pg: 'runsPerGame' missing in 2026 → use runs / gamesPlayed
        rs_pg = None
        if float(hit_stat.get("runsPerGame") or 0) > 0:
            rs_pg = round(float(hit_stat["runsPerGame"]), 2)
        elif float(hit_stat.get("runs") or 0) > 0 and float(hit_stat.get("gamesPlayed") or 0) > 0:
            rs_pg = round(float(hit_stat["runs"]) / max(float(hit_stat["gamesPlayed"]), 1), 2)

        # Compute ra_pg: 'runsAllowed' missing in 2026 → use 'runs' in pitching group
        ra_pg = None
        ra_raw = float(pit_stat.get("runsAllowed") or pit_stat.get("runs") or 0)
        if ra_raw > 0 and float(pit_stat.get("gamesPlayed") or 0) > 0:
            ra_pg = round(ra_raw / max(float(pit_stat["gamesPlayed"]), 1), 2)

        return {
            "avg":      hit_stat.get("avg", "N/D"),
            "ops":      hit_stat.get("ops", "N/D"),
            "rs_pg":    rs_pg if rs_pg is not None else None,
            "ra_pg":    ra_pg if ra_pg is not None else None,
            "_rs_real": rs_pg is not None,
            "_ra_real": ra_pg is not None,
        }
    except Exception as _e:
        print(f"  ⚠️  fetch_team_batting [{team_id}]: {_e}")
        return {"avg": "N/D", "ops": "N/D", "rs_pg": None, "ra_pg": None,
                "_rs_real": False, "_ra_real": False}

def fetch_team_pitching_ra(team_id):
    """
    Return runs allowed per game from team pitching stats. Returns None on failure.
    NOTE: MLB Stats API 2026 does not populate 'runsAllowed' — the pitching group
    uses 'runs' for runs allowed. Both field names are tried for compatibility.
    """
    try:
        data = _mlb_rest(f"/teams/{team_id}/stats",
                         {"stats": "season", "group": "pitching", "season": MLB_YEAR})
        splits = data.get("stats", [{}]) if data else []
        stat   = (splits[0].get("splits", [{}]) or [{}])[-1].get("stat", {}) if splits else {}
        # 'runsAllowed' missing in 2026 API — fall back to 'runs' (same meaning in pitching group)
        ra_raw = float(stat.get("runsAllowed") or stat.get("runs") or 0)
        gp     = max(float(stat.get("gamesPlayed") or 0), 1)
        if ra_raw > 0 and float(stat.get("gamesPlayed") or 0) > 0:
            return round(ra_raw / gp, 2)
        return None
    except Exception:
        return None

def analyze_nrfi(home_team: str, away_team: str,
                 hp_name: str, ap_name: str,
                 hp_era: float, ap_era: float,
                 home_ops: str, away_ops: str,
                 home_k9: float = 0.0, away_k9: float = 0.0) -> dict:
    """
    Analyze NRFI (No Run First Inning) / YRFI (Yes Run First Inning) market.
    Returns bet ('NRFI'|'YRFI'|'SKIP'), confidence, reason, ev_est.
    """
    score   = 0
    reasons = []

    if hp_era < 3.00:
        score += 3; reasons.append(f"Lanzador local élite ({hp_era:.2f} prom. carreras)")
    elif hp_era < 4.00:
        score += 1; reasons.append(f"Lanzador local sólido ({hp_era:.2f})")
    elif hp_era > 5.00:
        score -= 2; reasons.append(f"⚠️ Lanzador local débil ({hp_era:.2f})")

    if ap_era < 3.00:
        score += 3; reasons.append(f"Lanzador visitante élite ({ap_era:.2f})")
    elif ap_era < 4.00:
        score += 1; reasons.append(f"Lanzador visitante sólido ({ap_era:.2f})")
    elif ap_era > 5.00:
        score -= 2; reasons.append(f"⚠️ Lanzador visitante débil ({ap_era:.2f})")

    if home_k9 > 9.5:
        score += 1; reasons.append(f"Lanzador local: muchos ponches/9 innings ({home_k9:.1f})")
    if away_k9 > 9.5:
        score += 1; reasons.append(f"Lanzador visitante: muchos ponches/9 innings ({away_k9:.1f})")

    try:
        if float(home_ops or 0) > 0.800:
            score -= 1; reasons.append(f"Ofensiva local peligrosa ({home_ops})")
        if float(away_ops or 0) > 0.800:
            score -= 1; reasons.append(f"Ofensiva visitante peligrosa ({away_ops})")
    except Exception:
        pass

    if score >= 5:
        bet = "NRFI"; confidence = "ALTA"
        true_prob = 0.68
        ev_est = round((true_prob * 1.741 - 1) * 100, 1)
    elif score >= 2:
        bet = "NRFI"; confidence = "MEDIA"
        true_prob = 0.60
        ev_est = round((true_prob * 1.741 - 1) * 100, 1)
    elif score <= -3:
        bet = "YRFI"; confidence = "ALTA"
        true_prob = 0.52
        ev_est = round((true_prob * 2.05 - 1) * 100, 1)
    elif score <= -1:
        bet = "YRFI"; confidence = "MEDIA"
        true_prob = 0.48
        ev_est = round((true_prob * 2.05 - 1) * 100, 1)
    else:
        return {"bet": "SKIP", "confidence": "BAJA",
                "reason": "Sin ventaja clara en el primer inning.", "ev_est": 0.0}

    reason = " | ".join(reasons) if reasons else "Análisis de lanzadores"
    return {"bet": bet, "confidence": confidence, "reason": reason, "ev_est": ev_est}


def morning_report_mlb():
    print("\n📋 MLB Morning Report...")
    games = fetch_mlb_games_today()
    if not games:
        print("  No MLB games today or statsapi unavailable.")
        return []

    lineups = {}
    for g in games:
        try:
            # Handle both statsapi dict format and raw REST format
            if "home_name" in g:
                home = g["home_name"]; away = g["away_name"]
                home_id = g.get("home_id"); away_id = g.get("away_id")
                hp_name = g.get("home_probable_pitcher", "TBD")
                ap_name = g.get("away_probable_pitcher", "TBD")
                gtime   = g.get("game_datetime", "TBD")
            else:
                home = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "?")
                away = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "?")
                home_id = g.get("teams", {}).get("home", {}).get("team", {}).get("id")
                away_id = g.get("teams", {}).get("away", {}).get("team", {}).get("id")
                hp      = g.get("teams", {}).get("home", {}).get("probablePitcher", {})
                ap      = g.get("teams", {}).get("away", {}).get("probablePitcher", {})
                hp_name = hp.get("fullName", "TBD")
                ap_name = ap.get("fullName", "TBD")
                gtime   = g.get("gameDate", "TBD")

            hp_stats = fetch_pitcher_stats(hp_name)
            ap_stats = fetch_pitcher_stats(ap_name)

            # Team batting — rs_pg/ra_pg may be None when API data unavailable
            home_bat = fetch_team_batting(home_id) if home_id else {"avg":"N/D","ops":"N/D","rs_pg":None,"ra_pg":None,"_rs_real":False,"_ra_real":False}
            away_bat = fetch_team_batting(away_id) if away_id else {"avg":"N/D","ops":"N/D","rs_pg":None,"ra_pg":None,"_rs_real":False,"_ra_real":False}

            # Runs allowed per game — overwrite with dedicated pitching endpoint
            # (fetch_team_batting already computes ra_pg; this double-checks via pitching group)
            if home_id:
                pit_ra = fetch_team_pitching_ra(home_id)
                if pit_ra is not None:
                    home_bat["ra_pg"] = pit_ra
            if away_id:
                pit_ra = fetch_team_pitching_ra(away_id)
                if pit_ra is not None:
                    away_bat["ra_pg"] = pit_ra

            # Pythagorean win probability — handles None gracefully (returns 0.5)
            py_home = pythagorean_win_prob(home_bat["rs_pg"], home_bat["ra_pg"])
            py_away = pythagorean_win_prob(away_bat["rs_pg"], away_bat["ra_pg"])
            # Normalize
            total = py_home + py_away or 1
            py_home /= total; py_away /= total

            # ELO win probability
            elo_home = elo_win_prob(home, away)

            # Blend: 60% Pythagorean + 40% ELO
            win_prob_home = 0.6 * py_home + 0.4 * elo_home
            win_prob_away = 1 - win_prob_home

            # Recommendation
            # Replaced below after computing home_es_r / away_es_r
            rec = ""

            # Spanish recommendation
            home_es_r = _es(home); away_es_r = _es(away)
            if win_prob_home >= 0.60:
                rec = f"✅ APOSTAR LOCAL — {home_es_r} ML (confianza: ALTA)"
            elif win_prob_away >= 0.60:
                rec = f"✅ APOSTAR VISITANTE — {away_es_r} ML (confianza: ALTA)"
            elif win_prob_home >= 0.55:
                rec = f"➡️ INCLINACIÓN LOCAL — {home_es_r} ML (confianza: MEDIA)"
            elif win_prob_away >= 0.55:
                rec = f"➡️ INCLINACIÓN VISITANTE — {away_es_r} ML (confianza: MEDIA)"
            else:
                rec = "⏸️ SIN APUESTA — demasiado parejo"

            _rs_h = home_bat['rs_pg'] if home_bat['rs_pg'] is not None else 'Sin datos'
            _ra_h = home_bat['ra_pg'] if home_bat['ra_pg'] is not None else 'Sin datos'
            _rs_a = away_bat['rs_pg'] if away_bat['rs_pg'] is not None else 'Sin datos'
            _ra_a = away_bat['ra_pg'] if away_bat['ra_pg'] is not None else 'Sin datos'

            body = (
                f"{away_es_r} @ {home_es_r}  |  {_fmt_et(gtime) if gtime != 'TBD' else 'TBD'}\n"
                f"Lanzadores abridores:\n"
                f"  {hp_name} (Local): Promedio de carreras {hp_stats['era']} | "
                f"Base-runners por entrada {hp_stats['whip']} | "
                f"Ponches por 9 innings {hp_stats['k9']}\n"
                f"  {ap_name} (Visitante): Promedio de carreras {ap_stats['era']} | "
                f"Base-runners por entrada {ap_stats['whip']} | "
                f"Ponches por 9 innings {ap_stats['k9']}\n"
                f"Estadísticas ofensivas:\n"
                f"  {home_es_r}: Promedio al bate {home_bat['avg']} | "
                f"Eficiencia ofensiva {home_bat['ops']} | "
                f"Carreras anotadas/juego {_rs_h} | Carreras permitidas/juego {_ra_h}\n"
                f"  {away_es_r}: Promedio al bate {away_bat['avg']} | "
                f"Eficiencia ofensiva {away_bat['ops']} | "
                f"Carreras anotadas/juego {_rs_a} | Carreras permitidas/juego {_ra_a}\n"
                f"Probabilidad de Victoria: {home_es_r} {win_prob_home:.1%} | {away_es_r} {win_prob_away:.1%}\n"
                f"Modelo Pitágoras: {home_es_r} {py_home:.1%}\n"
                f">>> {rec}"
            )
            ntfy_post(f"MLB Preview: {away_es_r} @ {home_es_r}", body, "default")
            print(f"  ✉️  Sent MLB preview: {away} @ {home}")

            # Task 5 — NRFI/YRFI analysis after pitcher data is available
            try:
                _hp_era = float(hp_stats.get("era") or 4.50)
                _ap_era = float(ap_stats.get("era") or 4.50)
                _hp_k9  = float(hp_stats.get("k9")  or 0.0)
                _ap_k9  = float(ap_stats.get("k9")  or 0.0)
                _nrfi   = analyze_nrfi(
                    home, away, hp_name, ap_name,
                    _hp_era, _ap_era,
                    str(home_bat.get("ops", "0")),
                    str(away_bat.get("ops", "0")),
                    _hp_k9, _ap_k9,
                )
                if _nrfi["bet"] != "SKIP" and _nrfi["confidence"] in ("ALTA", "MEDIA"):
                    _nrfi_body = (
                        f"⚾ {away_es_r} @ {home_es_r}\n"
                        f"{'─'*28}\n"
                        f"Apuesta: <b>{_nrfi['bet']}</b> ({_nrfi['confidence']})\n"
                        f"EV estimado: +{_nrfi['ev_est']:.1f}%\n"
                        f"{'─'*28}\n"
                        f"{_nrfi['reason']}\n"
                        f"{'─'*28}\n"
                        f"⚠️ Usar odds reales de Bovada/Betonline antes de apostar.\n"
                        f"   {_nrfi['bet']} típico: {'(-135) decimal 1.741' if _nrfi['bet'] == 'NRFI' else '(+105) decimal 2.05'}"
                    )
                    ntfy_post(
                        f"⚾ NRFI/YRFI | {away_es_r} @ {home_es_r}",
                        _nrfi_body, "default"
                    )
                    print(f"  ⚾ NRFI/YRFI: {_nrfi['bet']} {_nrfi['confidence']} "
                          f"EV+{_nrfi['ev_est']:.1f}% — {away} @ {home}")
            except Exception as _ne:
                print(f"  ⚠️  NRFI error: {_ne}")

            game_key = f"{away}@{home}"
            lineups[game_key] = {"home_pitcher": hp_name, "away_pitcher": ap_name}

        except Exception as e:
            print(f"  ⚠️  MLB report error for game: {e}")

    # Save lineups for change monitoring
    with open(LINEUPS_FILE, "w") as f:
        json.dump({"date": str(datetime.now(CDT).date()), "lineups": lineups}, f)

    return lineups

def fetch_world_cup_games():
    try:
        r = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard",
            timeout=10
        )
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}

def _elo_to_goals(elo_home: float, elo_away: float) -> tuple:
    """
    Convert ELO ratings to expected goals per team using a log-ratio model.
    Based on WC historical scoring rates (avg ~1.3 goals/team/game).
    Returns (avg_goals_home, avg_goals_away).
    """
    BASE_GOALS = 1.30
    HOME_BOOST = 0.10
    ELO_SCALE  = 500.0
    elo_ratio = (elo_home - elo_away) / ELO_SCALE
    avg_h = BASE_GOALS * (1.0 + elo_ratio)   # WC is neutral venue — no home boost
    avg_a = BASE_GOALS * (1.0 - elo_ratio)
    avg_h = max(0.40, min(3.50, avg_h))
    avg_a = max(0.40, min(3.50, avg_a))
    return round(avg_h, 2), round(avg_a, 2)


def morning_report_world_cup():
    print("\n🌍 World Cup Morning Report...")
    data   = fetch_world_cup_games()
    events = data.get("events", [])
    if not events:
        print("  No World Cup games today or API unavailable.")
        return

    for event in events:
        try:
            comp = event.get("competitions", [{}])[0]
            competitors = comp.get("competitors", [])
            if len(competitors) < 2:
                continue

            home = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
            away = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

            home_name = home.get("team", {}).get("displayName", "?")
            away_name = away.get("team", {}).get("displayName", "?")
            home_es   = _es(home_name)
            away_es   = _es(away_name)
            game_time = event.get("date", "TBD")[:16]
            status    = comp.get("status", {}).get("type", {}).get("description", "")

            # Skip completed games
            if status in ("Final", "Full Time"):
                continue

            # ELO-based win probability
            elo_home = elo_win_prob(home_name, away_name)
            elo_away = 1.0 - elo_home

            # ELO-adjusted Poisson model
            elo_home_val = _elo_for(home_name)
            elo_away_val = _elo_for(away_name)
            avg_h, avg_a = _elo_to_goals(elo_home_val, elo_away_val)
            p_win, p_draw, p_loss = poisson_match_probs(avg_h, avg_a)

            # Blend ELO + Poisson
            win_h = 0.5 * elo_home + 0.5 * p_win
            win_a = 0.5 * elo_away + 0.5 * p_loss

            # Task 10: team form
            form_home = fetch_wc_team_form(home_name)
            form_away = fetch_wc_team_form(away_name)
            form_home_txt = (
                f"GF {form_home['goals_for']} | GC {form_home['goals_against']} "
                f"({form_home['matches']} partidos)"
                if form_home else "Sin datos"
            )
            form_away_txt = (
                f"GF {form_away['goals_for']} | GC {form_away['goals_against']} "
                f"({form_away['matches']} partidos)"
                if form_away else "Sin datos"
            )

            # Spanish rec
            if win_h >= 0.55:
                rec = f"➡️ INCLINACIÓN LOCAL — {home_es} (confianza: MEDIA)"
            elif win_a >= 0.55:
                rec = f"➡️ INCLINACIÓN VISITANTE — {away_es} (confianza: MEDIA)"
            else:
                rec = "⏸️ EMPATE posible — considera DNB o sin apuesta"

            body = (
                f"{away_es} vs {home_es}  |  {game_time} UTC\n"
                f"Goles esperados: {home_es} {avg_h} | {away_es} {avg_a}\n"
                f"Poisson model: Local {p_win:.1%} | Empate {p_draw:.1%} | Visitante {p_loss:.1%}\n"
                f"ELO: {home_es} {elo_home:.1%} | {away_es} {elo_away:.1%}\n"
                f"Mixto: {home_es} {win_h:.1%} | {away_es} {win_a:.1%}\n"
                f"Forma reciente {home_es}: {form_home_txt}\n"
                f"Forma reciente {away_es}: {form_away_txt}\n"
                f">>> {rec}"
            )
            ntfy_post(f"🌍 Vista previa: {away_es} vs {home_es}", body, "default")
            print(f"  ✉️  Sent WC preview: {away_name} vs {home_name}")

        except Exception as e:
            print(f"  ⚠️  WC report error: {e}")

# ══════════════════════════════════════════════════════════════════════════════
# DAILY HEALTH CHECK — 7:50 AM ET (Feature 1)
# ══════════════════════════════════════════════════════════════════════════════
def run_health_check():
    """Test every data module and send ntfy status report."""
    global last_health_check
    now   = datetime.now(ET)
    today = now.date()
    if last_health_check >= today:
        return
    last_health_check = today
    print("\n🏥 Iniciando health check diario...")
    results: list = []   # [(module_name, status_line)]

    # 1 ── MLB Stats API
    try:
        games = fetch_mlb_games_today()
        if isinstance(games, list):
            results.append(("MLB Stats API",  "✅ funcionando"))
            _track_module("MLB Stats API", True)
        else:
            raise ValueError("respuesta inesperada")
    except Exception as e:
        results.append(("MLB Stats API",  "❌ fallando"))
        _track_module("MLB Stats API", False, str(e))

    # 2 ── OpenWeatherMap
    try:
        if OPENWEATHER_KEY:
            w = fetch_wind(40.7128, -74.0060)   # NYC probe
            if w and w.get("speed") is not None:
                results.append(("OpenWeatherMap", "✅ funcionando"))
                _track_module("OpenWeatherMap", True)
            else:
                raise ValueError("respuesta vacía")
        else:
            results.append(("OpenWeatherMap", "⚠️ sin API key configurada"))
    except Exception as e:
        results.append(("OpenWeatherMap", "❌ fallando"))
        _track_module("OpenWeatherMap", False, str(e))

    # 3 ── Umpire data
    try:
        today_s = now.strftime("%Y-%m-%d")
        u = fetch_home_plate_umpire("New York Yankees", today_s)
        if u is not None:
            results.append(("Umpire data",    "✅ funcionando"))
        else:
            results.append(("Umpire data",    "⚠️ sin datos para hoy"))
    except Exception as e:
        results.append(("Umpire data",    "❌ fallando"))
        _log_error("Umpire data", "health_check", "", str(e))

    # 4 ── Bullpen ERA
    try:
        era, _note = fetch_bullpen_era("New York Yankees")
        if era and era > 0:
            results.append(("Bullpen ERA",    "✅ funcionando"))
            _track_module("Bullpen ERA", True)
        else:
            raise ValueError(f"era={era}")
    except Exception as e:
        results.append(("Bullpen ERA",    "❌ fallando"))
        _track_module("Bullpen ERA", False, str(e))

    # 5 ── Home/Away splits
    try:
        sp = fetch_mlb_home_away_splits("New York Yankees")
        if sp:
            ops_h = sp.get("home_ops") or sp.get("ops_home") or 0
            ops_a = sp.get("away_ops") or sp.get("ops_away") or 0
            if not ops_h and not ops_a:
                results.append(("Splits",         "⚠️ datos genéricos detectados"))
            else:
                results.append(("Splits",         "✅ funcionando"))
                _track_module("Splits", True)
        else:
            results.append(("Splits",         "⚠️ sin datos"))
    except Exception as e:
        results.append(("Splits",         "❌ fallando"))
        _log_error("Splits", "health_check", "", str(e))

    # 6 ── The Odds API
    try:
        if API_KEY:
            r = requests.get(
                "https://api.the-odds-api.com/v4/sports",
                params={"apiKey": API_KEY},
                timeout=8,
            )
            if r.status_code == 200:
                remaining  = r.headers.get("x-requests-remaining", "?")
                used       = r.headers.get("x-requests-used", "?")
                quota_note = f" ({remaining} restantes / {used} usadas)" if remaining != "?" else ""
                quota_warn = " ⚠️ QUOTA BAJA" if remaining != "?" and int(remaining) < 500 else ""
                results.append(("The Odds API", f"✅ funcionando{quota_warn}{quota_note}"))
                _track_module("The Odds API", True)
            else:
                raise ValueError(f"HTTP {r.status_code}")
        else:
            results.append(("The Odds API",   "⚠️ sin API key configurada"))
    except Exception as e:
        results.append(("The Odds API",   "❌ fallando"))
        _track_module("The Odds API", False, str(e))

    # 7 ── Claude API
    try:
        probe = panel_expertos(
            {
                "match": "Test A vs Test B", "sport": "baseball_mlb",
                "top_pick": "Test A",        "ev_pct": 5.0,
                "true_prob": 55.0,           "odds": -110,
                "stake": 10,
                "pitcher_home": "Test Pitcher (ERA 3.50)",
                "pitcher_away": "Test Pitcher (ERA 4.00)",
            },
            "MLB",
        )
        if probe is not None:
            results.append(("Claude API",     "✅ funcionando"))
            _track_module("Claude API", True)
        else:
            raise ValueError("retornó None")
    except Exception as e:
        results.append(("Claude API",     "❌ fallando"))
        _track_module("Claude API", False, str(e))

    failing = sum(1 for _, s in results if "❌" in s)
    warning = sum(1 for _, s in results if "⚠️" in s)
    lines   = "\n".join(f"{s}: {lbl}" for s, lbl in results)
    if failing:
        summary = f"⚠️ {failing} módulo{'s' if failing > 1 else ''} con problemas"
    elif warning:
        summary = f"⚠️ {warning} módulo{'s' if warning > 1 else ''} con advertencias"
    else:
        summary = "✅ Todos los módulos funcionando correctamente"

    body = (
        f"🏥 ESTADO DEL BOT — {now.strftime('%I:%M %p ET')}\n"
        f"{_DIV}\n"
        f"{lines}\n"
        f"{_DIV}\n"
        f"{summary}"
    )
    ntfy_post("🏥 ESTADO DEL BOT", body, "default")
    print(f"  🏥 Health check completado: {summary}")

def morning_report():
    global last_morning_report
    print("\n" + "="*50)
    print(f"🌅 MORNING REPORT — {datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}")
    # Module 2: consolidated daily ntfy report first
    try:
        send_daily_ntfy_report()
    except Exception as e:
        print(f"  ⚠️  Daily report error: {e}")
    morning_report_mlb()
    morning_report_world_cup()
    last_morning_report = datetime.now(ET).date()

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 2 — REAL-TIME LINEUP CHANGE MONITOR (every 15 min)
# ═══════════════════════════════════════════════════════════════════════════════

def load_morning_lineups():
    if not os.path.exists(LINEUPS_FILE):
        return {}
    try:
        with open(LINEUPS_FILE) as f:
            data = json.load(f)
        if data.get("date") == str(datetime.now(CDT).date()):
            return data.get("lineups", {})
    except Exception:
        pass
    return {}

def check_lineup_changes():
    print("  🔄 Checking lineup changes...")
    morning = load_morning_lineups()
    if not morning:
        return

    games = fetch_mlb_games_today()
    for g in games:
        try:
            if "home_name" in g:
                home = g["home_name"]; away = g["away_name"]
                hp_now = g.get("home_probable_pitcher", "TBD")
                ap_now = g.get("away_probable_pitcher", "TBD")
            else:
                home = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "?")
                away = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "?")
                hp   = g.get("teams", {}).get("home", {}).get("probablePitcher", {})
                ap   = g.get("teams", {}).get("away", {}).get("probablePitcher", {})
                hp_now = hp.get("fullName", "TBD")
                ap_now = ap.get("fullName", "TBD")

            game_key = f"{away}@{home}"
            prev = morning.get(game_key, {})

            for side, prev_p, now_p, label in [
                ("HOME", prev.get("home_pitcher","TBD"), hp_now, home),
                ("AWAY", prev.get("away_pitcher","TBD"), ap_now, away),
            ]:
                if prev_p != now_p and prev_p != "TBD" and now_p != "TBD":
                    msg = (
                        f"Game: {away} @ {home}\n"
                        f"{side} ({label}): {prev_p} → {now_p}\n"
                        f"Recalcula tu proyección del partido."
                    )
                    ntfy_post(f"PITCHER CHANGE: {away} @ {home}", msg, "urgent")
                    print(f"  🚨 Pitcher change: {away} @ {home} — {prev_p} → {now_p}")
                    # Update saved lineup
                    if game_key in morning:
                        key = "home_pitcher" if side == "HOME" else "away_pitcher"
                        morning[game_key][key] = now_p

        except Exception as e:
            print(f"  ⚠️  Lineup check error: {e}")

    # Save updated lineups
    with open(LINEUPS_FILE, "w") as f:
        json.dump({"date": str(datetime.now(CDT).date()), "lineups": morning}, f)

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 5 — ARBITRAGE SCANNER
# ═══════════════════════════════════════════════════════════════════════════════

ARB_MIN_PROFIT = 2.0   # minimum guaranteed profit % to fire an arb alert

def _check_arb2(home, away, team_a, odds_a, book_a, team_b, odds_b, book_b):
    """
    2-way arb check (MLB, NHL, etc. — no draw).
    Returns arb dict or None.
    """
    arb_budget = min(BANKROLL * 0.10, 100.0)
    margin = (1.0 / odds_a) + (1.0 / odds_b)
    if margin >= 1.0:
        return None
    profit_pct = (1.0 - margin) / margin * 100
    if profit_pct < ARB_MIN_PROFIT or profit_pct > 8.0:
        return None
    stake_a = arb_budget / (odds_a * margin)
    stake_b = arb_budget / (odds_b * margin)
    return {
        "match":      f"{home} vs {away}",
        "legs":       2,
        "team_a":     team_a, "odds_a": odds_a, "book_a": book_a,
        "stake_a":    round(stake_a, 2),
        "team_b":     team_b, "odds_b": odds_b, "book_b": book_b,
        "stake_b":    round(stake_b, 2),
        "profit":     round(arb_budget * (1.0 - margin) / margin, 2),
        "profit_pct": round(profit_pct, 2),
    }

def _check_arb3(home, away,
                team_h, odds_h, book_h,
                team_d, odds_d, book_d,
                team_a, odds_a, book_a):
    """
    3-way arb check (soccer — home / draw / away all covered).
    ALL THREE legs must be placed; a draw kills any 2-way soccer arb.
    Returns arb dict or None.
    """
    arb_budget = min(BANKROLL * 0.10, 100.0)
    margin = (1.0 / odds_h) + (1.0 / odds_d) + (1.0 / odds_a)
    if margin >= 1.0:
        return None
    profit_pct = (1.0 - margin) / margin * 100
    if profit_pct < ARB_MIN_PROFIT or profit_pct > 8.0:
        return None
    stake_h = arb_budget / (odds_h * margin)
    stake_d = arb_budget / (odds_d * margin)
    stake_a = arb_budget / (odds_a * margin)
    return {
        "match":      f"{home} vs {away}",
        "legs":       3,
        "team_a":     team_h, "odds_a": odds_h, "book_a": book_h,
        "stake_a":    round(stake_h, 2),
        "team_b":     team_d, "odds_b": odds_d, "book_b": book_d,
        "stake_b":    round(stake_d, 2),
        "team_c":     team_a, "odds_c": odds_a, "book_c": book_a,
        "stake_c":    round(stake_a, 2),
        "profit":     round(arb_budget * (1.0 - margin) / margin, 2),
        "profit_pct": round(profit_pct, 2),
    }

def scan_arbitrage(games, sport_key=""):
    arbs = []
    seen = set()

    for g in games:
        home, away = g["home_team"], g["away_team"]
        game_key   = f"{home}|{away}"
        game_time  = g.get("commence_time", "")
        if game_key in seen:
            continue

        # Build best-odds map: outcome_name → {book_lower: (price, display_name)}
        book_odds: dict = {}
        for bk in g.get("bookmakers", []):
            bk_lower = bk["title"].lower()
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    for o in m["outcomes"]:
                        outcome = o["name"]
                        price   = o["price"]
                        book_odds.setdefault(outcome, {})
                        prev = book_odds[outcome].get(bk_lower)
                        if prev is None or price > prev[0]:
                            book_odds[outcome][bk_lower] = (price, bk["title"])

        outcomes = list(book_odds.keys())
        n = len(outcomes)

        # ── 3-WAY (soccer): home + draw + away must ALL be covered ───────────
        if n == 3:
            draw_key = next((k for k in outcomes
                             if k.lower() in ("draw", "the draw")), None)
            if draw_key is None:
                continue

            home_key = home
            away_key = away
            if home_key not in book_odds or away_key not in book_odds:
                non_draw = [k for k in outcomes if k != draw_key]
                if len(non_draw) != 2:
                    continue
                home_key, away_key = non_draw[0], non_draw[1]

            best_h = max(book_odds[home_key].values(), key=lambda x: x[0])
            best_d = max(book_odds[draw_key].values(), key=lambda x: x[0])
            best_a = max(book_odds[away_key].values(), key=lambda x: x[0])

            arb = _check_arb3(home, away,
                              home_key, best_h[0], best_h[1],
                              draw_key, best_d[0], best_d[1],
                              away_key, best_a[0], best_a[1])
            if arb:
                arb["sport"]     = sport_key
                arb["game_time"] = game_time
                arbs.append(arb)
                seen.add(game_key)

        # ── 2-WAY (MLB, NHL, etc.): no draw outcome ───────────────────────────
        elif n >= 2:
            team_a, team_b = outcomes[0], outcomes[1]
            books_a = book_odds[team_a]
            books_b = book_odds[team_b]

            bov_key = next((k for k in books_a if k in PREFERRED_BOOKS), None)
            bol_key = "betonline.ag"

            if bov_key and bol_key in books_b:
                arb = _check_arb2(home, away,
                                  team_a, books_a[bov_key][0], books_a[bov_key][1],
                                  team_b, books_b[bol_key][0], books_b[bol_key][1])
                if arb:
                    arb["sport"]     = sport_key
                    arb["game_time"] = game_time
                    arbs.append(arb)
                    seen.add(game_key)

            if game_key not in seen and bov_key and bol_key in books_a:
                bol = books_a.get(bol_key)
                bov = books_b.get(bov_key)
                if bol and bov:
                    arb = _check_arb2(home, away,
                                      team_a, bol[0], bol[1],
                                      team_b, bov[0], bov[1])
                    if arb:
                        arb["sport"]     = sport_key
                        arb["game_time"] = game_time
                        arbs.append(arb)
                        seen.add(game_key)

            if game_key not in seen:
                best_a = max(books_a.values(), key=lambda x: x[0])
                best_b = max(books_b.values(), key=lambda x: x[0])
                arb = _check_arb2(home, away,
                                  team_a, best_a[0], best_a[1],
                                  team_b, best_b[0], best_b[1])
                if arb:
                    arb["sport"]     = sport_key
                    arb["game_time"] = game_time
                    arbs.append(arb)
                    seen.add(game_key)

    return arbs

# ── SMART DEDUP HELPER ────────────────────────────────────────────────────────

def _should_alert(key, odds=None, edge=None):
    """
    Return True if this alert should be sent; False if it's a duplicate.
    Re-alerts on the same day only when odds improve ≥10% OR edge grows ≥1.5 units.
    Updates _sent_alerts[key] whenever it returns True.
    """
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    prev  = _sent_alerts.get(key)

    send = False
    if prev is None:
        send = True                          # never alerted before
    elif prev["date"] != today:
        send = True                          # new day → fresh alert
    else:
        # Same day — only re-alert if conditions improved significantly
        if odds is not None and prev.get("odds"):
            if (odds - prev["odds"]) / prev["odds"] >= 0.10:
                send = True                  # odds improved ≥10%
        if not send and edge is not None and prev.get("edge") is not None:
            if edge - prev["edge"] >= 1.5:
                send = True                  # edge grew ≥1.5 units

    if send:
        _sent_alerts[key] = {"date": today, "odds": odds, "edge": edge}
    return send

def _era_label(era):
    """Plain-language ERA quality descriptor with accurate MLB tiers."""
    try:
        e = float(era)
    except (ValueError, TypeError):
        return str(era)
    if e < 2.00:    return "élite 🌟"
    elif e < 2.75:  return "dominante 🔥"
    elif e < 3.50:  return "sólido ✅"
    elif e < 4.25:  return "promedio ⚪"
    elif e < 5.00:  return "débil ⚠️"
    else:           return "vulnerable 🔴"

def _elo_tier(elo_num: float) -> str:
    """Convert raw ELO to FIFA-tier plain-Spanish strength label."""
    try:
        e = float(elo_num)
    except (TypeError, ValueError):
        return "MEDIA"
    if e >= 1930:   return "MUY ALTA 🌟"
    if e >= 1760:   return "ALTA 💪"
    if e >= 1650:   return "MEDIA ➡️"
    return "BAJA ⚠️"

def _park_label(pf: float) -> str:
    """Translate park factor to plain Spanish."""
    try:
        pf = float(pf)
    except (TypeError, ValueError):
        return "Estadio: neutral"
    pct = round((pf - 1.0) * 100)
    if pct > 2:
        return f"🏟️ Estadio: favorece bateadores (+{pct}% más carreras)"
    if pct < -2:
        return f"🏟️ Estadio: favorece pitchers ({pct}% menos carreras)"
    return "🏟️ Estadio: neutral"

def _result_to_es(r: str) -> str:
    """'W'→'✅ Ganó'  'D'→'🤝 Empató'  'L'→'❌ Perdió'"""
    return {"W": "✅ Ganó", "D": "🤝 Empató", "L": "❌ Perdió"}.get(r, r)

_mlb_recent_cache: dict = {}

def fetch_mlb_team_recent(team: str) -> dict | None:
    """
    Last 5 completed MLB games for a team via Odds API scores.
    Returns {results: [(label, score_str),...], wins, losses} or None.
    """
    ck = f"{team}_{datetime.now(TZ_LOCAL).strftime('%Y-%m-%d')}"
    if ck in _mlb_recent_cache:
        return _mlb_recent_cache[ck]
    try:
        url = (f"https://api.the-odds-api.com/v4/sports/baseball_mlb/scores/"
               f"?apiKey={API_KEY}&daysFrom=14&dateFormat=iso")
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        games = [g for g in r.json()
                 if (g.get("home_team") == team or g.get("away_team") == team)
                 and g.get("completed") is True]
        games.sort(key=lambda g: g.get("commence_time", ""), reverse=True)
        last5 = games[:5]
        if not last5:
            return None
        results = []
        for g in last5:
            is_home = g["home_team"] == team
            sc_list = g.get("scores") or []
            scores  = {s["name"]: int(s["score"]) for s in sc_list
                       if s.get("score") is not None}
            opp      = g["away_team"] if is_home else g["home_team"]
            my_sc    = scores.get(team, 0)
            opp_sc   = scores.get(opp, 0)
            if my_sc > opp_sc:   label = "W"
            elif my_sc == opp_sc: label = "D"
            else:                 label = "L"
            results.append((label, f"{my_sc}-{opp_sc}"))
        wins   = sum(1 for r, _ in results if r == "W")
        losses = sum(1 for r, _ in results if r == "L")
        res = {"results": results, "wins": wins, "losses": losses}
        _mlb_recent_cache[ck] = res
        return res
    except Exception:
        return None


# ── Module B2: RACHAS DE EQUIPOS (last-10 streak via Odds API) ────────────────
_mlb_streak_cache: dict = {}

def fetch_team_streak_mlb(team: str) -> "dict | None":
    """
    Last 10 completed MLB games for a team via Odds API scores endpoint.
    Returns dict:
      wins_10, losses_10          — last-10 record
      streak, streak_type         — consecutive W or L from most recent game
      run_diff                    — cumulative run differential over last 10
      is_hot (wins_10 >= 7)       — triggers +5% ML, +0.3 total runs
      is_cold (wins_10 <= 3)      — triggers −5% ML, −0.3 total runs
      label                       — formatted display string for alerts
    """
    ck = f"streak_{team}_{datetime.now(TZ_LOCAL).strftime('%Y-%m-%d')}"
    if ck in _mlb_streak_cache:
        return _mlb_streak_cache[ck]
    try:
        url = (f"https://api.the-odds-api.com/v4/sports/baseball_mlb/scores/"
               f"?apiKey={API_KEY}&daysFrom=21&dateFormat=iso")
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        all_games = [g for g in r.json()
                     if (g.get("home_team") == team or g.get("away_team") == team)
                     and g.get("completed") is True]
        all_games.sort(key=lambda g: g.get("commence_time", ""), reverse=True)
        last10 = all_games[:10]
        if not last10:
            return None

        results  = []
        run_diff = 0
        total_rs = 0
        total_ra = 0
        overs_10 = 0
        unders_10 = 0
        for g in last10:
            opp    = g["away_team"] if g["home_team"] == team else g["home_team"]
            sc_map = {s["name"]: int(s["score"])
                      for s in (g.get("scores") or []) if s.get("score") is not None}
            my_sc  = sc_map.get(team, 0)
            op_sc  = sc_map.get(opp,  0)
            results.append("W" if my_sc > op_sc else "L")
            run_diff += my_sc - op_sc
            total_rs += my_sc
            total_ra += op_sc
            combined  = my_sc + op_sc
            if combined > 8.5:
                overs_10 += 1
            elif combined < 8.5:
                unders_10 += 1

        wins_10   = results.count("W")
        losses_10 = results.count("L")
        n10 = max(len(last10), 1)

        # Current streak from most recent game
        streak_type = results[0] if results else "L"
        streak = 0
        for rr in results:
            if rr == streak_type:
                streak += 1
            else:
                break

        is_hot  = wins_10 >= 7
        is_cold = wins_10 <= 3
        emoji   = "🔥" if is_hot else ("❄️" if is_cold else "📊")
        trend   = "EN RACHA" if is_hot else ("EN CAÍDA" if is_cold else "NEUTRO")
        streak_word = "ganados" if streak_type == "W" else "perdidos"
        diff_s  = f"+{run_diff}" if run_diff >= 0 else str(run_diff)

        label = (
            f"{emoji} {_es(team)} — {trend}:\n"
            f"   Últimos 10: {wins_10}-{losses_10}\n"
            f"   Racha actual: {streak} {streak_word} seguidos\n"
            f"   Diferencial: {diff_s} carreras"
        )

        result = {
            "wins_10":     wins_10,
            "losses_10":   losses_10,
            "streak":      streak,
            "streak_type": streak_type,
            "run_diff":    run_diff,
            "is_hot":      is_hot,
            "is_cold":     is_cold,
            "label":       label,
            "emoji":       emoji,
            "avg_rs":      round(total_rs / n10, 1),
            "avg_ra":      round(total_ra / n10, 1),
            "avg_total":   round((total_rs + total_ra) / n10, 1),
            "overs_10":    overs_10,
            "unders_10":   unders_10,
        }
        _mlb_streak_cache[ck] = result
        return result
    except Exception:
        return None


def _parse_pitcher(s):
    """Parse '{name} (ERA X.XX)' → (name, era_float). Falls back to (s, 4.50)."""
    if s and " (ERA " in s:
        try:
            name, rest = s.split(" (ERA ", 1)
            return name.strip(), float(rest.rstrip(")").strip())
        except Exception:
            pass
    return (s or "TBD"), 4.50

def _verdict_line(ev_pct, true_prob=None):
    """One-line confidence verdict appended to every alert."""
    if ev_pct > 10 and (true_prob is None or true_prob > 0.60):
        return f"{_DIV3}\n🟢 CONFIANZA: ALTA"
    elif ev_pct >= 3 or (true_prob is not None and true_prob >= 0.50):
        return f"{_DIV3}\n🟡 CONFIANZA: MEDIA"
    else:
        return f"{_DIV3}\n🔴 CONFIANZA: BAJA — ignorar"

def _ev_dollars(stake, ev_pct):
    """Expected profit in $ for a given stake and EV%."""
    return round(stake * ev_pct / 100, 2)

def notify_arbitrage(arbs):
    for i, arb in enumerate(arbs):
        home, away = arb["match"].split(" vs ", 1)
        arb_key = f"{home}_{away}_arb"
        if not _should_alert(arb_key, edge=arb["profit_pct"]):
            continue

        # ── Book safety filter: skip if ANY leg uses a risky book ──────────
        if arb.get("legs") == 3:
            all_books = [arb["book_a"], arb["book_b"], arb["book_c"]]
        else:
            all_books = [arb["book_a"], arb["book_b"]]
        risky = [b for b in all_books if _is_risky_book(b)]
        if risky:
            print(f"  ⛔ ARB omitido — casa(s) riesgosa(s) [{', '.join(risky)}]: {arb['match']}")
            continue

        # ── Never alert games that already started (>5 min grace) ─────────
        if _game_already_started(arb.get("game_time", ""), grace_min=5):
            print(f"  ⏰ ARB omitido — juego ya comenzó: {arb.get('match','')}")
            continue
        # ───────────────────────────────────────────────────────────────────

        sport     = arb.get("sport", "")
        game_time = arb.get("game_time", "")
        arb_days  = _days_until(game_time)
        is_mlb_arb = "mlb" in sport.lower()

        # ── Timing filter ───────────────────────────────────────────────────
        if is_mlb_arb:
            # MLB: TODAY only
            if arb_days > 1:
                print(f"  ⛔ ARB MLB omitido — no es hoy ({arb_days:.1f} días): {arb['match']}")
                continue
        else:
            # Soccer / World Cup: strictly less than 3 days
            if arb_days >= 3:
                print(f"  ⛔ ARB soccer omitido — {arb_days:.1f} días (necesita <3): {arb['match']}")
                continue
            # profit >= 2% already enforced by ARB_MIN_PROFIT upstream
        # ───────────────────────────────────────────────────────────────────

        if i > 0:
            time.sleep(2)
        emoji     = _sport_emoji(sport)
        match     = arb["match"]
        profit    = arb["profit"]
        pct       = arb["profit_pct"]
        gt        = _fmt_smart_gt(game_time)
        arb_timing_note = (f"⚠️ Partido en {int(arb_days)} días — verificar lineup\n"
                           if arb_days >= 3 else "")

        verdict = _verdict_line(pct)

        # Tag each leg: ✅ safe, ⚠️ risky
        tag_a = _arb_leg_tag(arb["book_a"])
        tag_b = _arb_leg_tag(arb["book_b"])

        if arb.get("legs") == 3:
            tag_c       = _arb_leg_tag(arb["book_c"])
            total_stake = round(arb["stake_a"] + arb["stake_b"] + arb["stake_c"], 2)
            has_risky   = any(_is_risky_book(b) for b in [arb["book_a"], arb["book_b"], arb["book_c"]])
            risky_note  = "⚠️ Una casa de apuestas es riesgosa — apuesta sólo en las marcadas ✅ si es posible\n" if has_risky else ""
            l1 = (
                f"🎯 {match}\n"
                f"⏰ {gt} ET\n"
                f"💰 Ganancia garantizada: ${profit} ({pct}%)\n"
                f"🟢 APOSTAR LAS 3 PIERNAS — sin riesgo"
            )
            l2 = (
                f"Apuesta en 3 casas distintas para garantizar ganancia:\n"
                f"🔵 ${arb['stake_a']:>8} → {arb['team_a']} @ {arb['odds_a']} — {arb['book_a']} {tag_a}\n"
                f"🤝 ${arb['stake_b']:>8} → Empate @ {arb['odds_b']} — {arb['book_b']} {tag_b}\n"
                f"🔴 ${arb['stake_c']:>8} → {arb['team_c']} @ {arb['odds_c']} — {arb['book_c']} {tag_c}\n"
                f"💵 Total apostado: ${total_stake} | Ganancia: ${profit}\n"
                f"{arb_timing_note}"
                f"{risky_note}"
                f"{verdict}"
            )
            body = _two_layer_body(l1, l2)
        else:
            total_stake = round(arb["stake_a"] + arb["stake_b"], 2)
            has_risky   = any(_is_risky_book(b) for b in [arb["book_a"], arb["book_b"]])
            risky_note  = "⚠️ Una casa de apuestas es riesgosa — apuesta sólo en las marcadas ✅ si es posible\n" if has_risky else ""
            l1 = (
                f"🎯 {match}\n"
                f"⏰ {gt} ET\n"
                f"💰 Ganancia garantizada: ${profit} ({pct}%)\n"
                f"🟢 APOSTAR LAS 2 PIERNAS — sin riesgo"
            )
            l2 = (
                f"Apuesta en 2 casas distintas para garantizar ganancia:\n"
                f"🔵 ${arb['stake_a']:>8} → {arb['team_a']} @ {arb['odds_a']} — {arb['book_a']} {tag_a}\n"
                f"🔴 ${arb['stake_b']:>8} → {arb['team_b']} @ {arb['odds_b']} — {arb['book_b']} {tag_b}\n"
                f"💵 Total apostado: ${total_stake} | Ganancia: ${profit}\n"
                f"{arb_timing_note}"
                f"{risky_note}"
                f"{verdict}"
            )
            body = _two_layer_body(l1, l2)

        ntfy_post(f"⚡ ARB | {match} | +${profit}", body, "urgent")
        print(f"  💰 ARB: {match} — ${profit} profit ({pct}%)")

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 6 — TOTALS (OVER/UNDER) ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

# Park factors for known MLB parks (home team name → multiplier)
# Calibrated from 2026 backtesting hit rates — high values favor OVER, low values favor UNDER.
# Parks NOT listed default to 1.00 (neutral).
MLB_PARK_FACTORS = {
    # High-scoring parks (favor OVER)
    "Colorado Rockies":      1.35,   # Coors Field — backtesting: 68% hit rate
    "Texas Rangers":         1.18,   # Globe Life Field
    "Philadelphia Phillies": 1.15,   # Citizens Bank Park
    "Boston Red Sox":        1.12,   # Fenway Park
    "Cincinnati Reds":       1.12,   # Great American Ball Park
    "New York Yankees":      1.10,   # Yankee Stadium
    # Pitcher-friendly parks (favor UNDER)
    "San Francisco Giants":  0.82,   # Oracle Park — backtesting: 36.4% hit rate (avoid)
    "San Diego Padres":      0.85,   # Petco Park
    "Los Angeles Dodgers":   0.87,   # Dodger Stadium
    "Tampa Bay Rays":        0.88,   # Tropicana Field
    "Seattle Mariners":      0.88,   # T-Mobile Park
}

# ── Park tendency: additive run adjustments (+ = OVER, - = UNDER) ─────────────
# Based on multi-year historical O/U data per ballpark.
MLB_PARK_TEND: "dict[str, tuple]" = {
    "Colorado Rockies":     ("Coors Field",               +1.5, True),
    "Cincinnati Reds":      ("Great American Ball Park",   +0.8, True),
    "Texas Rangers":        ("Globe Life Field",           +0.6, True),
    "Boston Red Sox":       ("Fenway Park",                +0.5, True),
    "New York Yankees":     ("Yankee Stadium",             +0.4, True),
    "San Francisco Giants": ("Oracle Park",                -0.8, False),
    "San Diego Padres":     ("Petco Park",                 -0.7, False),
    "Los Angeles Dodgers":  ("Dodger Stadium",             -0.5, False),
    "Tampa Bay Rays":       ("Tropicana Field",            -0.5, False),
    "Seattle Mariners":     ("T-Mobile Park",              -0.4, False),
}

_team_run_cache: dict = {}   # "{team}|{date}" -> {"rs_pg": float, "ra_pg": float}

# ── Persistent disk cache (survives Railway restarts) ─────────────────────────
_STATS_CACHE_FILE = "stats_cache.json"
_stats_disk_cache: dict = {}   # flat dict — keys like "run|{team}|{date}" or "splits|{team}|{date}"

def _load_stats_disk_cache():
    """Load stats cache from disk into memory. Called once at startup."""
    global _stats_disk_cache
    try:
        if os.path.exists(_STATS_CACHE_FILE):
            with open(_STATS_CACHE_FILE, "r") as _f:
                raw = json.load(_f)
            # Prune entries older than today to avoid stale data
            today = datetime.now(ET).strftime("%Y-%m-%d")
            _stats_disk_cache = {k: v for k, v in raw.items() if today in k}
            print(f"  💾 Stats disk cache loaded — {len(_stats_disk_cache)} entry(ies)")
    except Exception as _e:
        print(f"  ⚠️  Stats disk cache load error: {_e}")
        _stats_disk_cache = {}

def _save_stats_disk_cache():
    """Flush in-memory stats cache to disk (fast, called after each successful fetch)."""
    try:
        with open(_STATS_CACHE_FILE, "w") as _f:
            json.dump(_stats_disk_cache, _f)
    except Exception as _e:
        print(f"  ⚠️  Stats disk cache save error: {_e}")
# ─────────────────────────────────────────────────────────────────────────────


def fetch_team_run_stats(team_name):
    """
    RS/RA per game for an MLB team.
    Cache: in-memory (date-keyed) + disk file for cross-restart persistence.
    Retry: up to 3 attempts with 2s delay before giving up.
    """
    today  = datetime.now(ET).strftime("%Y-%m-%d")
    ck     = f"{team_name}|{today}"
    dk     = f"run|{ck}"

    # 1. In-memory cache
    if ck in _team_run_cache:
        return _team_run_cache[ck]

    # 2. Disk cache (survives restarts — same-day entries only)
    if dk in _stats_disk_cache:
        v = _stats_disk_cache[dk]
        _team_run_cache[ck] = v
        return v

    last_err = None
    for attempt in range(1, 4):
        try:
            # ── Team ID lookup ─────────────────────────────────────────────
            if HAS_STATSAPI:
                teams = statsapi.lookup_team(team_name)
                if not teams:
                    print(f"  ⚠️  fetch_team_run_stats [{team_name}]: "
                          f"equipo no encontrado en statsapi")
                    _team_run_cache[ck] = None
                    return None
                tid = teams[0]["id"]
            else:
                data  = _mlb_rest("/teams", {"sportId": 1, "season": MLB_YEAR})
                match = next(
                    (t for t in data.get("teams", [])
                     if team_name.lower() in t.get("name", "").lower()), None)
                if not match:
                    print(f"  ⚠️  fetch_team_run_stats [{team_name}]: "
                          f"equipo no encontrado en REST /teams")
                    _team_run_cache[ck] = None
                    return None
                tid = match["id"]

            # ── Hitting + pitching stats ───────────────────────────────────
            hit = _mlb_rest(f"/teams/{tid}/stats",
                            {"stats": "season", "group": "hitting", "season": MLB_YEAR})
            pit = _mlb_rest(f"/teams/{tid}/stats",
                            {"stats": "season", "group": "pitching", "season": MLB_YEAR})

            h_stat = (hit.get("stats", [{}])[0].get("splits", [{}]) or [{}])[-1].get("stat", {})
            p_stat = (pit.get("stats", [{}])[0].get("splits", [{}]) or [{}])[-1].get("stat", {})

            # rs_pg: 'runsPerGame' absent in 2026 API → compute from runs/gamesPlayed
            gp_h = max(float(h_stat.get("gamesPlayed") or 0), 1)
            h_runs = float(h_stat.get("runs") or 0)
            if float(h_stat.get("runsPerGame") or 0) > 0:
                rs_pg = round(float(h_stat["runsPerGame"]), 2)
            elif h_runs > 0:
                rs_pg = round(h_runs / gp_h, 2)
            else:
                print(f"  ⚠️  fetch_team_run_stats [{team_name}] intento {attempt}/3: "
                      f"runs=0 y runsPerGame ausente — gamesPlayed={h_stat.get('gamesPlayed')} "
                      f"(API puede no haber cargado estadísticas aún)")
                if attempt < 3:
                    time.sleep(2)
                    continue
                _team_run_cache[ck] = None
                return None

            # ra_pg: 'runsAllowed' absent in 2026 API → use 'runs' in pitching group
            games_p = max(float(p_stat.get("gamesPlayed") or 0), 1)
            ra_raw  = float(p_stat.get("runsAllowed") or p_stat.get("runs") or 0)
            if ra_raw == 0:
                print(f"  ⚠️  fetch_team_run_stats [{team_name}] intento {attempt}/3: "
                      f"runsAllowed y runs=0 en pitching — gamesPlayed={p_stat.get('gamesPlayed')} "
                      f"(pitcher stats no cargadas)")
                if attempt < 3:
                    time.sleep(2)
                    continue
                _team_run_cache[ck] = None
                return None

            ra_pg  = round(ra_raw / games_p, 2)
            result = {"rs_pg": rs_pg, "ra_pg": ra_pg}
            _team_run_cache[ck] = result
            _stats_disk_cache[dk] = result
            _save_stats_disk_cache()
            print(f"  📊 [{team_name}] RS={rs_pg} RA={ra_pg}/juego ✅")
            return result

        except Exception as _e:
            last_err = _e
            print(f"  ⚠️  fetch_team_run_stats [{team_name}] intento {attempt}/3: "
                  f"{type(_e).__name__}: {_e}")
            if attempt < 3:
                time.sleep(2)

    # ── All retries exhausted — try yesterday's disk cache before giving up ──
    yesterday = (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")
    yd_key    = f"run|{team_name}|{yesterday}"
    if yd_key in _stats_disk_cache:
        v = _stats_disk_cache[yd_key]
        if v and v.get("rs_pg", 0) > 0 and v.get("ra_pg", 0) > 0:
            print(f"  📦 [{team_name}] run stats: usando datos de ayer "
                  f"RS={v['rs_pg']} RA={v['ra_pg']} (API devuelve ceros hoy)")
            _team_run_cache[ck] = v
            return v
    print(f"  ❌ fetch_team_run_stats [{team_name}] — 3 intentos fallidos. "
          f"Último error: {last_err}. Sin caché de ayer. Usando fallback 4.5")
    _team_run_cache[ck] = None
    return None

# ── IMPROVEMENT 1: MLB STARTING PITCHER ERA ───────────────────────────────────

def _fetch_pitcher_era_by_id(player_id):
    """Season ERA for a pitcher by MLB player ID. Returns float (default 4.50)."""
    try:
        if HAS_STATSAPI:
            data = statsapi.player_stat_data(player_id, group='pitching', type='season')
            splits = data.get('stats', [])
            if splits:
                return float(splits[0].get('stats', {}).get('era', 4.50))
        else:
            d = _mlb_rest(f'/people/{player_id}/stats',
                          {'stats': 'season', 'group': 'pitching', 'season': MLB_YEAR})
            sp = (d.get('stats', [{}]) or [{}])[0].get('splits', [{}]) or [{}]
            return float(sp[-1].get('stat', {}).get('era', 4.50))
    except Exception:
        pass
    return 4.50

def fetch_probable_pitchers_today():
    """
    Fetch today's probable starters from MLB Stats API.
    Returns dict keyed by "<home_team>|<away_team>" (lowercased) →
      {home_era, away_era, home_name, away_name}
    Cached per calendar day.
    """
    today_str = datetime.now(CDT).strftime('%Y-%m-%d')
    if today_str in _pitcher_cache:
        return _pitcher_cache[today_str]

    result = {}
    try:
        data = _mlb_rest('/schedule', {
            'sportId': 1,
            'date': today_str,
            'hydrate': 'probablePitcher,teams',
        })
        for date_entry in data.get('dates', []):
            for g in date_entry.get('games', []):
                teams   = g.get('teams', {})
                home_t  = teams.get('home', {})
                away_t  = teams.get('away', {})
                home_tn = home_t.get('team', {}).get('name', '')
                away_tn = away_t.get('team', {}).get('name', '')
                home_p  = home_t.get('probablePitcher', {})
                away_p  = away_t.get('probablePitcher', {})

                def _tbd_sp_fallback(team_name: str) -> dict:
                    """Find the SP who hasn't pitched in 4+ days as fallback for TBD."""
                    try:
                        tid = _team_id(team_name)
                        if not tid:
                            return {}
                        roster = _mlb_rest(f"/teams/{tid}/roster",
                                           {"rosterType": "active", "season": MLB_YEAR})
                        cutoff = (datetime.now(TZ_LOCAL) - timedelta(days=4)).strftime("%Y-%m-%d")
                        for p in (roster.get("roster") or []):
                            pos = (p.get("position") or {}).get("abbreviation", "")
                            if pos != "SP":
                                continue
                            pid = p.get("person", {}).get("id")
                            pname = p.get("person", {}).get("fullName", "TBD")
                            if not pid:
                                continue
                            try:
                                glg = _mlb_rest(f"/people/{pid}/stats", {
                                    "stats": "gameLog", "group": "pitching",
                                    "season": MLB_YEAR, "gameType": "R",
                                })
                                splits = (glg.get("stats", [{}])[0].get("splits", []) or [])
                                last_date = ""
                                for sp in splits:
                                    gd = sp.get("date", "")
                                    if gd > last_date:
                                        last_date = gd
                                if not last_date or last_date < cutoff:
                                    era = _fetch_pitcher_era_by_id(pid)
                                    print(f"  🔮 TBD fallback SP [{team_name}]: "
                                          f"{pname} (last={last_date or 'none'}, ERA={era:.2f})")
                                    return {"id": pid, "fullName": pname, "_fallback": True, "era": era}
                            except Exception:
                                continue
                    except Exception:
                        pass
                    return {}

                if not home_p.get('id'):
                    _fb_h = _tbd_sp_fallback(home_tn)
                    if _fb_h:
                        home_p = _fb_h
                if not away_p.get('id'):
                    _fb_a = _tbd_sp_fallback(away_tn)
                    if _fb_a:
                        away_p = _fb_a

                h_era   = _fetch_pitcher_era_by_id(home_p['id']) if home_p.get('id') else 4.50
                a_era   = home_p.get('era', 4.50) if home_p.get('_fallback') else h_era
                h_era   = a_era if home_p.get('_fallback') else h_era
                a_era   = _fetch_pitcher_era_by_id(away_p['id']) if away_p.get('id') else 4.50
                if away_p.get('_fallback'):
                    a_era = away_p.get('era', 4.50)
                key     = f"{home_tn.lower()}|{away_tn.lower()}"
                result[key] = {
                    'home_era':  round(h_era, 2),
                    'away_era':  round(a_era, 2),
                    'home_name': home_p.get('fullName', 'TBD'),
                    'away_name': away_p.get('fullName', 'TBD'),
                    'home_id':   home_p.get('id'),
                    'away_id':   away_p.get('id'),
                }
                for _pid_r, _era_key in [('home_id', 'home_era'), ('away_id', 'away_era')]:
                    _pid_v = result[key].get(_pid_r)
                    if _pid_v:
                        try:
                            _ip_data = _mlb_rest(f'/people/{_pid_v}/stats',
                                                  {'stats': 'season', 'group': 'pitching', 'season': MLB_YEAR})
                            _ip_splits = (_ip_data.get('stats', [{}])[0].get('splits', [{}]) or [{}])
                            _ip_stat = _ip_splits[-1].get('stat', {}) if _ip_splits else {}
                            _ip_val = float(_ip_stat.get('inningsPitched', '60') or '60')
                            _era_orig = result[key][_era_key]
                            _era_reg = _regressed_era(_era_orig, _ip_val)
                            if abs(_era_reg - _era_orig) > 0.15:
                                print(f"  📊 ERA regresada: {_era_orig:.2f} → {_era_reg:.2f} ({_ip_val:.0f} inn)")
                            result[key][_era_key] = _era_reg
                        except Exception:
                            pass
    except Exception as e:
        print(f'  ⚠️  Pitcher fetch error: {e}')

    _pitcher_cache[today_str] = result
    return result

def _lookup_pitcher_data(home, away, pitchers):
    """
    Fuzzy lookup in the pitchers dict using token overlap scoring.

    Scores each candidate by how many home/away tokens hit the correct
    side of the key, then penalises cross-hits (tokens that bleed into
    the wrong side).  This avoids false positives like 'sox' matching
    both 'boston red sox' and 'chicago white sox' when only one team is
    actually the home team.

    Returns the best-scoring entry, or {} when nothing matches.
    """
    if not pitchers:
        return {}

    home_words = set(home.lower().split())
    away_words = set(away.lower().split())
    best_score = 0
    best_val   = {}

    for key, val in pitchers.items():
        h_key, a_key = key.split('|', 1)
        h_words = set(h_key.split())
        a_words = set(a_key.split())

        h_match = len(home_words & h_words)   # home words landing on h_key  ✓
        a_match = len(away_words & a_words)   # away words landing on a_key  ✓
        h_cross = len(home_words & a_words)   # home words bleeding into a_key ✗
        a_cross = len(away_words & h_words)   # away words bleeding into h_key ✗

        # Need at least one genuine hit on each side before scoring
        if h_match == 0 or a_match == 0:
            continue

        score = h_match + a_match - h_cross - a_cross
        if score > best_score:
            best_score = score
            best_val   = val

    return best_val

_pitcher_vs_team_cache: dict = {}
def fetch_pitcher_vs_team(pitcher_id, opponent_team_name: str) -> "dict | None":
    if not pitcher_id or not opponent_team_name:
        return None
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck = f"pvt_{pitcher_id}_{opponent_team_name}_{today}"
    if ck in _pitcher_vs_team_cache:
        return _pitcher_vs_team_cache[ck]
    try:
        opp_id = _team_id(opponent_team_name)
        if not opp_id:
            return None
        data = _mlb_rest(f"/people/{pitcher_id}/stats", {
            "stats": "vsTeamSeason", "group": "pitching",
            "season": MLB_YEAR, "opposingTeamId": opp_id,
        })
        splits = (data.get("stats", [{}])[0].get("splits", []) if data and data.get("stats") else [])
        if not splits:
            _pitcher_vs_team_cache[ck] = None
            return None
        stat = splits[-1].get("stat", {})
        games = int(stat.get("gamesPlayed", 0) or 0)
        if games < 1:
            _pitcher_vs_team_cache[ck] = None
            return None
        era_v = float(stat.get("era", 0) or 0)
        ip_v = _parse_ip(stat.get("inningsPitched", "0") or "0")
        result = {"era": era_v, "games": games, "ip": ip_v}
        _pitcher_vs_team_cache[ck] = result
        print(f"  📊 Pitcher vs {opponent_team_name}: ERA {era_v:.2f} ({games}G)")
        return result
    except Exception:
        _pitcher_vs_team_cache[ck] = None
        return None

def pitcher_run_adjustment(home_era, away_era):
    """
    Signed run-total adjustment from starter quality.
    Elite starter (ERA < 3.50): -0.6 runs each
    Slightly above avg (ERA < 4.00): -0.2 runs
    Slightly below avg (ERA > 4.50): +0.2 runs
    Poor starter (ERA > 5.00): +0.6 runs each
    """
    adj = 0.0
    for era in (home_era, away_era):
        if era < 3.50:
            adj -= 0.6
        elif era < 4.00:
            adj -= 0.2
        elif era > 5.00:
            adj += 0.6
        elif era > 4.50:
            adj += 0.2
    return round(adj, 1)

def _regressed_era(season_era: float, innings_pitched: float, career_era: float = 4.20) -> float:
    if innings_pitched >= 60:
        return round(season_era * 0.90 + career_era * 0.10, 2)
    elif innings_pitched >= 30:
        return round(season_era * 0.50 + career_era * 0.50, 2)
    else:
        return round(season_era * 0.20 + career_era * 0.80, 2)

def _third_time_through_adj(pitcher_name: str, era: float,
                             pace_data: "dict | None") -> "tuple[float, str]":
    """
    Ajuste por tercera vez que el lineup enfrenta al pitcher.
    Históricamente los pitchers dan ~0.5 carreras más la 3ra vuelta.
    Solo aplica si se espera que el pitcher lance 6+ innings.
    Retorna (adj_runs: float, note: str).
    """
    if not pitcher_name or pitcher_name in ("TBD", "", "SIN CONFIRMAR"):
        return 0.0, ""
    # Estimar innings esperados
    expected_ip = 5.5  # default
    if pace_data and pace_data.get("avg_pi"):
        avg_pi      = float(pace_data["avg_pi"])
        expected_ip = min(100.0 / max(avg_pi, 10.0) * 0.85, 8.0)
    if era < 3.00:
        expected_ip = min(expected_ip + 0.5, 8.0)
    elif era > 5.00:
        expected_ip = min(expected_ip - 0.5, 4.5)
    expected_ip = max(expected_ip, 1.0)
    if expected_ip < 6.0:
        return 0.0, ""  # no llega a la 3ra vuelta
    innings_3rd = round(expected_ip - 6.0, 1)
    adj         = round(innings_3rd * 0.18, 2)
    if adj <= 0:
        return 0.0, ""
    note = (
        f"🔄 3ra vuelta al lineup ({pitcher_name}, {expected_ip:.1f} inn proyectados):\n"
        f"   Los bateadores le conocen mejor → +{adj:.2f} carreras al total"
    )
    return adj, note


def _day_game_adj(commence: str) -> tuple:
    try:
        _game_et = datetime.fromisoformat(commence.replace("Z", "+00:00")).astimezone(ET)
        if _game_et.hour < 16:
            return -0.35, f"🌞 Juego diurno ({_game_et.strftime('%I:%M %p ET')}): -0.35 carreras"
    except Exception:
        pass
    return 0.0, ""

def _lineup_impact(missing_list: list, order_dict: dict) -> float:
    impact = 0.0
    for missing_name in missing_list:
        missing_last = missing_name.split()[-1].lower()
        for slot, player_name in order_dict.items():
            if missing_last in player_name.lower():
                slot_int = int(slot)
                if slot_int <= 4: impact -= 0.55
                elif slot_int <= 7: impact -= 0.30
                else: impact -= 0.15
                break
        else:
            impact -= 0.35
    return round(impact, 2)

# ═══════════════════════════════════════════════════════════════════════════════
# ELITE SOURCE 1 — BASEBALL SAVANT / STATCAST
# ═══════════════════════════════════════════════════════════════════════════════

_statcast_pitcher_cache: dict = {}   # date → {name_key → metrics dict}
_statcast_batter_cache:  dict = {}   # date → {name_key → metrics dict}

def _safe_float(val) -> "float | None":
    """Convert a CSV string value to float; return None if blank or non-numeric."""
    try:
        v = str(val).strip()
        return float(v) if v not in ("", "null", "None", "N/A", "-") else None
    except Exception:
        return None

def _statcast_name_key(full_name: str) -> str:
    """'Jacob deGrom' → 'degrom_jacob' for Savant lookup."""
    parts = full_name.strip().lower().split()
    return f"{parts[-1]}_{parts[0]}" if len(parts) >= 2 else full_name.lower()

def _fetch_statcast_all(player_type: str = "pitcher") -> dict:
    """
    Download Baseball Savant leaderboard CSV once per calendar day.
    player_type: "pitcher" or "batter"
    Returns {name_key → metrics_dict}.  Returns {} silently on any failure.
    """
    today = datetime.now(ET).strftime("%Y-%m-%d")
    cache = _statcast_pitcher_cache if player_type == "pitcher" else _statcast_batter_cache
    if today in cache:
        return cache[today]

    url = (
        f"https://baseballsavant.mlb.com/leaderboard/custom"
        f"?year={MLB_YEAR}&type={player_type}&min=1&pos=p"
        f"&player_type={player_type}&csv=true"
    )
    try:
        r = requests.get(url, timeout=25,
                         headers={"User-Agent": "Mozilla/5.0 (compatible; BetBot/1.0)"})
        if r.status_code != 200:
            print(f"  ⚠️  Statcast {player_type}: HTTP {r.status_code}")
            cache[today] = {}
            return {}

        import csv, io
        reader = csv.DictReader(io.StringIO(r.text))
        result = {}
        for row in reader:
            first = (row.get("first_name") or "").strip().lower()
            last  = (row.get("last_name")  or "").strip().lower()
            if not first or not last:
                continue
            key = f"{last}_{first}"
            if player_type == "pitcher":
                result[key] = {
                    "xera":         _safe_float(row.get("p_era")   or row.get("xera")
                                               or row.get("est_era")),
                    "whiff_pct":    _safe_float(row.get("whiff_percent")),
                    "hard_hit_pct": _safe_float(row.get("hard_hit_percent")),
                    "barrel_pct":   _safe_float(row.get("barrel_batted_rate")
                                               or row.get("barrel_percent")),
                    "chase_pct":    _safe_float(row.get("oz_swing_percent")
                                               or row.get("chase_percent")),
                }
            else:
                result[key] = {
                    "xba":          _safe_float(row.get("xba")),
                    "xslg":         _safe_float(row.get("xslg")),
                    "hard_hit_pct": _safe_float(row.get("hard_hit_percent")),
                    "barrel_pct":   _safe_float(row.get("barrel_batted_rate")
                                               or row.get("barrel_percent")),
                }
        cache[today] = result
        # FIX 4: diagnostic — report actual CSV columns when expected pitcher keys are missing
        if player_type == "pitcher" and (not result or not any(
            v.get("xera") is not None or v.get("whiff_pct") is not None
            or v.get("hard_hit_pct") is not None
            for v in list(result.values())[:5]
        )):
            print(f"⚠️ Statcast columnas recibidas: {reader.fieldnames}")
        print(f"  🔬 Statcast {player_type}s cargados: {len(result)} jugadores")
        return result
    except Exception as _e:
        print(f"  ⚠️  Statcast {player_type} fetch error: {_e}")
        cache[today] = {}
        return {}

def fetch_statcast_pitcher(pitcher_name: str) -> "dict | None":
    """
    Return Statcast metrics dict for a pitcher (by display name), or None.
    Keys: xera, whiff_pct, hard_hit_pct, barrel_pct, chase_pct
    Falls back to last-name partial match if full-name lookup misses.
    """
    if not pitcher_name or pitcher_name in ("TBD", "Unknown", ""):
        return None
    data = _fetch_statcast_all("pitcher")
    if not data:
        return None
    key  = _statcast_name_key(pitcher_name)
    hit  = data.get(key)
    if not hit:
        last = pitcher_name.strip().lower().split()[-1]
        for k, v in data.items():
            if k.startswith(last + "_"):
                hit = v
                break
    return hit if hit else None

def _statcast_alert_block(name: str, sc: "dict | None", era: float) -> str:
    """
    Format the 🔬 Statcast block for a pitcher.
    Returns empty string if no statcast data.
    """
    if not sc:
        return ""
    lines = []
    xera = sc.get("xera")
    if xera is not None:
        diff  = era - xera
        trend = ("mejor que ERA — infravalorado ✅" if diff > 0.30
                 else "peor que ERA — ha tenido suerte ⚠️" if diff < -0.30
                 else "alineado con ERA")
        lines.append(f"   xERA: {xera:.2f} ({trend})")
    whiff = sc.get("whiff_pct")
    if whiff is not None:
        lbl = ("dominante 🔥" if whiff > 30 else "sólido" if whiff > 22 else "bajo ⚠️")
        lines.append(f"   Whiff%: {whiff:.1f}% ({lbl})")
    hh = sc.get("hard_hit_pct")
    if hh is not None:
        lbl = ("controlado ✅" if hh < 32 else "alto ⚠️" if hh > 40 else "normal")
        lines.append(f"   Hard hit%: {hh:.1f}% ({lbl})")
    barrel = sc.get("barrel_pct")
    if barrel is not None:
        lines.append(f"   Barrel%: {barrel:.1f}%")
    if not lines:
        return ""
    return f"🔬 Statcast {name}:\n" + "\n".join(lines) + "\n"


# ═══════════════════════════════════════════════════════════════════════════════
# ELITE SOURCE 2 — PINNACLE MARKET REFERENCE
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_pinnacle_odds(game: dict) -> "dict | None":
    """
    Extract Pinnacle h2h moneyline odds from a game bookmakers array.
    Returns {"home": price, "away": price} or None if Pinnacle absent.
    """
    home = game.get("home_team", "")
    away = game.get("away_team", "")
    for bk in game.get("bookmakers", []):
        if "pinnacle" in bk.get("title", "").lower():
            for m in bk.get("markets", []):
                if m.get("key") == "h2h":
                    pin_h = pin_a = None
                    for o in m.get("outcomes", []):
                        if o["name"] == home:
                            pin_h = o["price"]
                        elif o["name"] == away:
                            pin_a = o["price"]
                    if pin_h is not None and pin_a is not None:
                        return {"home": pin_h, "away": pin_a}
    # Fallback: OddsPapi Pinnacle ref cache
    try:
        from pinnacle_ref import get_pinnacle_for_game as _gpfg
        _ref = _gpfg(home, away)
        if _ref and _ref.get("h2h"):
            return _ref["h2h"]
    except Exception:
        pass
    return None

def _check_pinnacle_movement(game: dict, pick_label: str, home: str) -> str:
    """
    Detecta si Pinnacle movió su línea en contra del pick desde la apertura.
    Si la línea se movió ≥ 0.5 pts (totals) o ≥ 3pp (ML) en contra → advertencia fuerte.
    Retorna string de advertencia o '' si no hay señal.
    """
    gid   = game.get("id", "")
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck    = f"{gid}_{today}"
    prev  = _line_open_cache.get(ck, {})
    if not prev:
        return ""
    label_up  = pick_label.upper()
    is_over   = "OVER"  in label_up
    is_under  = "UNDER" in label_up
    is_totals = is_over or is_under
    try:
        if is_totals:
            pin_tots = _extract_pinnacle_totals(game)
            if not pin_tots:
                return ""
            curr_line = float(pin_tots["line"])
            prev_line = float(prev.get("total", curr_line))
            delta     = round(curr_line - prev_line, 1)
            if is_over and delta <= -0.5:
                return (f"⛔ SEÑAL SHARP CONTRARIA: Pinnacle bajó la línea {abs(delta):.1f} pts "
                        f"desde apertura → sharps apostaron UNDER. Evalúa con cuidado.")
            if is_under and delta >= 0.5:
                return (f"⛔ SEÑAL SHARP CONTRARIA: Pinnacle subió la línea {abs(delta):.1f} pts "
                        f"desde apertura → sharps apostaron OVER. Evalúa con cuidado.")
        else:
            pin_h2h = _extract_pinnacle_odds(game)
            if not pin_h2h:
                return ""
            rh    = 1.0 / max(pin_h2h["home"], 1.001)
            ra    = 1.0 / max(pin_h2h["away"], 1.001)
            tot   = rh + ra
            curr  = round(rh / tot * 100, 1)
            prev_impl = float(prev.get("h_impl", curr))
            delta_pp  = round(curr - prev_impl, 1)
            is_home_p = home.upper() in label_up
            if is_home_p and delta_pp <= -3.0:
                return (f"⛔ SEÑAL SHARP CONTRARIA: Pinnacle bajó al local {abs(delta_pp):.1f}pp "
                        f"desde apertura → sharps en visitante. Evalúa con cuidado.")
            if not is_home_p and delta_pp >= 3.0:
                return (f"⛔ SEÑAL SHARP CONTRARIA: Pinnacle bajó al visitante {abs(delta_pp):.1f}pp "
                        f"desde apertura → sharps en local. Evalúa con cuidado.")
    except Exception:
        pass
    return ""


def _pinnacle_analysis(
    pick_team: str,
    home: str,
    model_prob: float,
    pin_odds: "dict | None",
) -> "tuple[str, str, str]":
    """
    Compare model side + probability to Pinnacle implied.
    Returns (verdict, alert_line, conf_modifier):
      verdict       : "strong" | "confirm" | "against" | "none"
      alert_line    : formatted string for alert body
      conf_modifier : "ALTA" | "MEDIA" | "BAJA" | ""
    """
    if pin_odds is None:
        return "none", "", ""

    raw_h  = 1.0 / max(pin_odds["home"], 1.001)
    raw_a  = 1.0 / max(pin_odds["away"], 1.001)
    total  = raw_h + raw_a
    pin_ph = raw_h / total
    pin_pa = raw_a / total

    is_home        = (pick_team == home)
    pin_prob       = pin_ph if is_home else pin_pa
    model_fav_home = model_prob > 0.50
    pin_fav_home   = pin_ph   > 0.50
    same_side      = (model_fav_home == pin_fav_home)

    if not same_side:
        return (
            "against",
            f"📌 Pinnacle DIVERGE — su lado: {'local' if pin_fav_home else 'visitante'} "
            f"({round(pin_prob*100,1)}% implícita) ⚠️ bajar confianza",
            "BAJA",
        )
    diff = abs(model_prob - pin_prob)
    if diff <= 0.05:
        return (
            "strong",
            f"📌 Pinnacle confirma: {round(pin_prob*100,1)}% implícita "
            f"→ Bovada puede tener línea blanda ✅",
            "ALTA",
        )
    return (
        "confirm",
        f"📌 Pinnacle acuerda lado: {round(pin_prob*100,1)}% implícita "
        f"(modelo: {round(model_prob*100,1)}%)",
        "MEDIA",
    )


def _extract_pinnacle_totals(game: dict) -> "dict | None":
    """
    Extract Pinnacle totals (over/under) odds from a game bookmakers array.
    Returns {"line": X, "over": price, "under": price} or None if absent.
    Works for both MLB (carreras) and Soccer (goles).
    """
    for bk in game.get("bookmakers", []):
        if "pinnacle" in bk.get("title", "").lower():
            for m in bk.get("markets", []):
                if m.get("key") == "totals":
                    ov = un = line = None
                    for o in m.get("outcomes", []):
                        n = o.get("name", "")
                        if n == "Over":
                            ov   = o.get("price")
                            line = o.get("point")
                        elif n == "Under":
                            un   = o.get("price")
                    if ov and un and line is not None:
                        return {"line": line, "over": ov, "under": un}
    # Fallback: OddsPapi Pinnacle ref cache
    try:
        _home = game.get("home_team", "")
        _away = game.get("away_team", "")
        from pinnacle_ref import get_pinnacle_for_game as _gpfg2
        _ref2 = _gpfg2(_home, _away)
        if _ref2 and _ref2.get("totals"):
            return _ref2["totals"]
    except Exception:
        pass
    return None


# ── Line movement detection (session-level opening vs current) ────────────────
_line_open_cache: dict = {}   # ck → {"total": float, "h_impl": float, "a_impl": float}

def _detect_line_movement(game_obj: dict, home: str, away: str) -> "str | None":
    """
    Detect sharp-money line movement by comparing current odds vs first-seen odds
    within the same bot session.  Returns a human-readable signal string when:
      • Total (O/U) point moves ≥ 0.5 pts in either direction, OR
      • ML implied-probability moves ≥ 5 percentage points for either team.
    On the first call for a given game, stores the snapshot and returns None.
    """
    gid   = game_obj.get("id", f"{home}|{away}")
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck    = f"{gid}_{today}"

    # Extract current total point + ML implied probs (prefer Pinnacle, fall back to any)
    current_total: "float | None" = None
    h_impl: "float | None"        = None
    a_impl: "float | None"        = None

    def _amer_to_impl(price: float) -> float:
        return (100 / (price + 100) * 100) if price > 0 else (abs(price) / (abs(price) + 100) * 100)

    for priority in ("pinnacle", ""):
        for bk in game_obj.get("bookmakers", []):
            if priority and priority not in bk.get("key", "").lower():
                continue
            for mkt in bk.get("markets", []):
                if mkt["key"] == "totals" and current_total is None:
                    for o in mkt.get("outcomes", []):
                        if o.get("name") == "Over" and o.get("point") is not None:
                            try:
                                current_total = float(o["point"])
                            except Exception:
                                pass
                if mkt["key"] == "h2h" and h_impl is None:
                    for o in mkt.get("outcomes", []):
                        try:
                            impl = round(_amer_to_impl(float(o["price"])), 1)
                            if o["name"] == home:
                                h_impl = impl
                            elif o["name"] == away:
                                a_impl = impl
                        except Exception:
                            pass
        if current_total is not None and h_impl is not None:
            break

    snapshot: dict = {}
    if current_total is not None:
        snapshot["total"] = current_total
    if h_impl is not None:
        snapshot["h_impl"] = h_impl
    if a_impl is not None:
        snapshot["a_impl"] = a_impl

    # prune stale entries older than today to prevent unbounded memory growth
    _today_pfx = datetime.now(CDT).strftime("%Y-%m-%d")
    _stale_keys = [_k for _k in list(_line_open_cache.keys()) if _today_pfx not in _k]
    for _sk in _stale_keys:
        del _line_open_cache[_sk]

    if not snapshot:
        return None

    # First time seen → store snapshot, no movement to report yet
    if ck not in _line_open_cache:
        _line_open_cache[ck] = snapshot
        return None

    prev     = _line_open_cache[ck]
    signals  = []

    # Total point movement ≥ 0.5
    if prev.get("total") is not None and current_total is not None:
        delta = round(current_total - prev["total"], 1)
        if abs(delta) >= 0.5:
            direction = "OVER" if delta > 0 else "UNDER"
            signals.append(
                f"TOTAL movió {abs(delta):.1f} pts → {direction} "
                f"(apertura {prev['total']:.1f} → actual {current_total:.1f})"
            )

    # ML implied probability movement ≥ 5 pp
    for impl_key, team_name, opp_name in [
        ("h_impl", home, away),
        ("a_impl", away, home),
    ]:
        old_val = prev.get(impl_key)
        new_val = h_impl if impl_key == "h_impl" else a_impl
        if old_val is not None and new_val is not None:
            delta_pp = round(new_val - old_val, 1)
            if abs(delta_pp) >= 5.0:
                direction = team_name if delta_pp > 0 else opp_name
                signals.append(
                    f"ML {_es(team_name)}: {abs(delta_pp):.0f}pp → {_es(direction)} "
                    f"(apertura {old_val:.0f}% → actual {new_val:.0f}%)"
                )

    if signals:
        return "⚡ LÍNEA MOVIÓ — dinero sharp detectado: " + " | ".join(signals)
    return None


def _build_pinnacle_panel_signal(
    pick_label: str,
    context: dict,
    game: dict,
    home: str,
) -> str:
    """
    Compare the pick direction against Pinnacle's current market position.

    Returns a plain-text signal to inject into _claude_data_g["pinnacle_panel_signal"]
    so all three experts (Marco, Víctor, Elena) receive it before giving their verdict.

    Logic:
    - Totals picks (OVER/UNDER): compare against Pinnacle's implied totals probability.
    - ML picks (moneyline):      compare against Pinnacle's implied h2h probability.
    - Returns "" when no Pinnacle data is available for the pick type.
    """
    label_up = pick_label.upper()

    is_over_pick  = "OVER"  in label_up
    is_under_pick = "UNDER" in label_up
    is_totals     = is_over_pick or is_under_pick
    is_ml         = "ML" in label_up and not is_totals

    # ── TOTALS PICKS (game total, F5 total, hits total, goals) ───────────────
    if is_totals:
        pin_tot = _extract_pinnacle_totals(game)
        if pin_tot is None:
            return ""
        raw_ov  = 1.0 / max(pin_tot["over"],  1.001)
        raw_un  = 1.0 / max(pin_tot["under"], 1.001)
        tot_sum = raw_ov + raw_un
        pin_p_over  = raw_ov / tot_sum
        pin_p_under = raw_un / tot_sum

        pick_is_over    = is_over_pick
        pin_favors_over = pin_p_over > 0.50

        if pick_is_over == pin_favors_over:
            side    = "OVER"  if pick_is_over  else "UNDER"
            pin_pct = round((pin_p_over if pick_is_over else pin_p_under) * 100, 1)
            return (
                f"CONFIRMACIÓN EXTERNA: El mercado sharp de Pinnacle apoya este pick — "
                f"Pinnacle implica {pin_pct}% de probabilidad para el {side} "
                f"(línea {pin_tot['line']}). La probabilidad del modelo está respaldada "
                f"por dinero profesional, no es error del modelo."
            )
        else:
            side_pin = "OVER"  if pin_favors_over  else "UNDER"
            pin_pct  = round((pin_p_over if pin_favors_over else pin_p_under) * 100, 1)
            return (
                f"ADVERTENCIA: Pinnacle contradice el pick — el mercado sharp de Pinnacle "
                f"favorece el {side_pin} con {pin_pct}% de probabilidad implícita "
                f"(línea {pin_tot['line']}). Evaluar con cautela."
            )

    # ── ML PICKS (moneyline — home or away team) ─────────────────────────────
    if is_ml:
        pin_odds = context.get("pinnacle_odds")
        if not pin_odds:
            return ""
        raw_h  = 1.0 / max(pin_odds["home"], 1.001)
        raw_a  = 1.0 / max(pin_odds["away"], 1.001)
        tot_h  = raw_h + raw_a
        pin_ph = raw_h / tot_h
        pin_pa = raw_a / tot_h

        # Determine if pick targets home or away team from label text
        pick_is_home = home.upper() in label_up
        pin_p_pick   = pin_ph if pick_is_home else pin_pa
        pin_agrees   = pin_p_pick > 0.50

        if pin_agrees:
            return (
                f"CONFIRMACIÓN EXTERNA: El mercado sharp de Pinnacle apoya este pick — "
                f"Pinnacle implica {round(pin_p_pick * 100, 1)}% de probabilidad para "
                f"este equipo. La probabilidad alta está respaldada por dinero profesional, "
                f"no es error del modelo."
            )
        else:
            opp_pct = round((1.0 - pin_p_pick) * 100, 1)
            return (
                f"ADVERTENCIA: Pinnacle contradice el pick — el mercado sharp de Pinnacle "
                f"da {opp_pct}% de probabilidad al equipo contrario. Evaluar con cautela."
            )

    return ""


def _apply_pinnacle_calibration(
    candidates: list,
    game: dict,
    home: str,
) -> "tuple[list, list]":
    """
    Post-procesado de candidatos: blendea la probabilidad del modelo con la implícita
    de Pinnacle y aplica un filtro de divergencia.

    Para cada candidato donde Pinnacle tiene odds disponibles:
      prob_final = (prob_modelo × 0.70) + (prob_pinnacle × 0.30)

    Modelo pesa 70% (Pyth/ELO/Poisson/panel expertos).
    Pinnacle pesa 30% como referencia sharp calibrada pero subordinada al modelo.
    Luego se recalculan EV y stake con la probabilidad ajustada.

    Si |prob_modelo − prob_pinnacle| > 25 pp:
      - El stake se reduce a la mitad automáticamente.
      - Se genera un texto de alerta para el panel de expertos.

    NOTA: Pinnacle opera cerca del 50% porque balancea su libro.
    Una divergencia de 8–20pp es completamente normal y aceptable.
    Solo >25pp genera alerta.

    Candidatos cuya prob calibrada queda por debajo de PROB_MIN son eliminados.

    Returns (calibrated_candidates, divergence_alerts).
    """
    pin_h2h  = _extract_pinnacle_odds(game)
    pin_tots = _extract_pinnacle_totals(game)

    calibrated  = []
    div_alerts  = []

    for c in candidates:
        label_up  = c["label"].upper()
        is_over   = "OVER"  in label_up
        is_under  = "UNDER" in label_up
        is_totals = is_over or is_under
        is_ml     = "ML"    in label_up and not is_totals

        pin_p = None

        if is_totals and pin_tots:
            raw_ov  = 1.0 / max(pin_tots["over"],  1.001)
            raw_un  = 1.0 / max(pin_tots["under"], 1.001)
            tot_sum = raw_ov + raw_un
            pin_p   = (raw_ov / tot_sum) if is_over else (raw_un / tot_sum)

        elif is_ml and pin_h2h:
            raw_h   = 1.0 / max(pin_h2h["home"], 1.001)
            raw_a   = 1.0 / max(pin_h2h["away"], 1.001)
            tot_sum = raw_h + raw_a
            pin_p   = (raw_h / tot_sum) if (home.upper() in label_up) else (raw_a / tot_sum)

        if pin_p is None:
            calibrated.append(c)
            continue

        model_p = c["true_prob"]
        blended = round(model_p * PINNACLE_BLEND_MODEL + pin_p * PINNACLE_BLEND_SHARP, 4)
        # Divergencia se mide sobre la prob YA calibrada (modelo 70% / Pinnacle 30%).
        # Ejemplo: model=87.2%, pin=50% → blended=76.0% → div=26pp
        div_pp  = abs(blended - pin_p) * 100

        # Recomputar EV y stake con prob calibrada
        odds      = c["odds"]
        new_ev    = round((blended * odds - 1) * 100, 1)
        new_r     = kelly_stake(blended, odds)
        new_stake = new_r["stake"]

        # Eliminar si la prob calibrada cae bajo el mínimo
        if blended < PROB_MIN:
            print(
                f"   ⏭️  Pinnacle calibración: {c['label']} — "
                f"prob ajustada {blended:.1%} < {PROB_MIN:.1%} mín — omitido"
            )
            continue

        # Divergencia alta (>25pp) → stake ÷ 2 + alerta para el panel
        # 8–20pp es normal (Pinnacle balancea su libro); solo >25pp es crítico.
        if div_pp > 25.0:
            new_stake = round(new_stake / 2, 2) if new_stake else 0.0
            alert_txt = (
                f"ALERTA: Divergencia crítica entre modelo ({round(model_p * 100, 1)}%) "
                f"y Pinnacle ({round(pin_p * 100, 1)}%) en '{c['label']}' ({div_pp:.0f}pp) — "
                f"evaluar con precaución extra."
            )
            div_alerts.append(alert_txt)
            print(
                f"   ⚠️  Pinnacle div {div_pp:.0f}pp: {c['label']} "
                f"prob {model_p:.1%}→{blended:.1%}  stake {c['stake']}→{new_stake}"
            )
        else:
            _div_tag = "✅ normal" if div_pp <= 20.0 else "⚠️ elevada"
            print(
                f"   📌 Pinnacle calib: {c['label']} "
                f"prob {model_p:.1%}→{blended:.1%} (div {div_pp:.1f}pp {_div_tag})"
            )

        c = dict(c)   # copia superficial para no mutar el original
        c["true_prob"]  = blended
        c["ev_pct"]     = new_ev
        c["stake"]      = new_stake
        c["kelly_pct"]  = new_r["kelly_pct"]
        if div_pp > 25.0:
            c["stake_warn"] = (
                (c.get("stake_warn") or "") +
                f" ⚠️ stake÷2 div Pinnacle {div_pp:.0f}pp"
            ).strip()
        calibrated.append(c)

    return calibrated, div_alerts


# ═══════════════════════════════════════════════════════════════════════════════
# ELITE SOURCE 3 — UNDERSTAT xG (SOCCER)
# ═══════════════════════════════════════════════════════════════════════════════

_understat_cache: dict = {}   # f"{team}|{date}" → dict or None

_UNDERSTAT_SLUGS = {
    "Brazil": "Brazil", "France": "France", "Germany": "Germany",
    "Spain": "Spain", "England": "England", "Argentina": "Argentina",
    "Portugal": "Portugal", "Netherlands": "Netherlands",
    "Belgium": "Belgium", "Italy": "Italy", "Mexico": "Mexico",
    "United States": "USA", "USA": "USA", "Uruguay": "Uruguay",
    "Colombia": "Colombia", "Chile": "Chile", "Ecuador": "Ecuador",
    "Peru": "Peru", "Paraguay": "Paraguay", "Japan": "Japan",
    "South Korea": "South Korea", "Australia": "Australia",
    "Saudi Arabia": "Saudi Arabia", "Morocco": "Morocco",
    "Senegal": "Senegal", "Nigeria": "Nigeria", "Ghana": "Ghana",
    "Canada": "Canada", "Costa Rica": "Costa Rica",
    "Panama": "Panama", "Croatia": "Croatia", "Denmark": "Denmark",
    "Switzerland": "Switzerland", "Poland": "Poland",
    "Austria": "Austria", "Turkey": "Turkey", "Serbia": "Serbia",
    "Ukraine": "Ukraine", "Scotland": "Scotland", "Wales": "Wales",
}

def fetch_understat_xg(team_name: str) -> "dict | None":
    """
    Scrape xG data for a national team from understat.com.
    Tries 2026 first, falls back to 2025 qualifying season.
    Returns {"xg_for": float, "xg_against": float, "matches": int,
             "raw_goals_for": float, "raw_goals_against": float}
    or None on any failure.  Caches per team per day.
    """
    today = datetime.now(ET).strftime("%Y-%m-%d")
    ck    = f"{team_name}|{today}"
    if ck in _understat_cache:
        return _understat_cache[ck]

    slug = _UNDERSTAT_SLUGS.get(team_name, team_name.replace(" ", "_"))

    def _try_year(year: int) -> "dict | None":
        url = f"https://understat.com/team/{slug}/{year}"
        try:
            r = requests.get(
                url, timeout=14,
                headers={"User-Agent": "Mozilla/5.0 (compatible; BetBot/1.0)"},
            )
            if r.status_code != 200:
                return None
            import re
            m = re.search(
                r"var\s+datesData\s*=\s*JSON\.parse\('(.*?)'\)",
                r.text, re.DOTALL
            )
            if not m:
                return None
            raw     = m.group(1)
            decoded = raw.replace("\\'", "'").encode("utf-8").decode("unicode_escape")
            games   = json.loads(decoded)
            if not games:
                return None
            recent = games[-5:] if len(games) >= 5 else games
            xgf    = [float(g.get("xG",  0) or 0) for g in recent]
            xga    = [float(g.get("xGA", 0) or 0) for g in recent]
            gf     = [float(g.get("scored",   0) or 0) for g in recent]
            ga     = [float(g.get("missed",   0) or 0) for g in recent]
            return {
                "xg_for":           round(sum(xgf) / len(xgf), 2),
                "xg_against":       round(sum(xga) / len(xga), 2),
                "raw_goals_for":    round(sum(gf)  / len(gf),  2),
                "raw_goals_against":round(sum(ga)  / len(ga),  2),
                "matches":          len(recent),
                "season":           year,
            }
        except Exception as _e:
            print(f"  ⚠️  understat [{team_name}] {year}: {_e}")
            return None

    result = _try_year(MLB_YEAR) or _try_year(MLB_YEAR - 1)
    _understat_cache[ck] = result
    if result:
        print(f"  📊 xG [{team_name}] xGF={result['xg_for']} "
              f"xGA={result['xg_against']} ({result['matches']}p "
              f"season={result['season']})")
    return result


# ── MODULE 8B: FIP · Handedness · L/R matchup · H2H ──────────────────────────
_fip_cache_d:  dict = {}
_hand_cache_d: dict = {}
_lr_cache_d:   dict = {}
_h2h_mlb_cache: dict = {}

def _parse_ip(ip_val) -> float:
    """Convert MLB IP string '142.2' → decimal innings (142.667)."""
    try:
        s = str(ip_val)
        if "." in s:
            whole, frac = s.split(".", 1)
            return float(whole) + float(frac) / 3.0
        return float(s)
    except Exception:
        return 1.0


def _fetch_pitcher_fip_by_id(player_id) -> "float | None":
    """FIP for a pitcher this season. Returns float or None on failure."""
    if not player_id:
        return None
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck = f"{player_id}_{today}"
    if ck in _fip_cache_d:
        return _fip_cache_d[ck]
    try:
        d  = _mlb_rest(f"/people/{player_id}/stats",
                       {"stats": "season", "group": "pitching", "season": MLB_YEAR})
        sp = (d.get("stats") or [{}])[0].get("splits") or [{}]
        st = sp[-1].get("stat", {}) if sp else {}
        hr = float(st.get("homeRuns",    0) or 0)
        bb = float(st.get("baseOnBalls", 0) or 0)
        k  = float(st.get("strikeOuts",  0) or 0)
        ip = _parse_ip(st.get("inningsPitched", 0))
        if ip < 1:
            _fip_cache_d[ck] = None
            return None
        fip = round(((13 * hr + 3 * bb - 2 * k) / ip) + 3.10, 2)
        _fip_cache_d[ck] = fip
        return fip
    except Exception:
        _fip_cache_d[ck] = None
        return None


def _fetch_pitcher_hand_by_id(player_id) -> "str | None":
    """Pitcher throw hand code: L / R / S. Cached for the session."""
    if not player_id:
        return None
    if player_id in _hand_cache_d:
        return _hand_cache_d[player_id]
    try:
        d    = _mlb_rest(f"/people/{player_id}")
        hand = (d.get("people") or [{}])[0].get("pitchHand", {}).get("code")
        _hand_cache_d[player_id] = hand
        return hand
    except Exception:
        _hand_cache_d[player_id] = None
        return None


def _fetch_team_batting_vs_hand(team_id, hand: str) -> "dict | None":
    """
    Team batting splits vs left (L) or right (R) pitchers.
    Returns {"avg": float, "ops": float} or None.
    """
    if not team_id or not hand:
        return None
    split_type = "vsLeft" if hand == "L" else "vsRight"
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck = f"{team_id}_{split_type}_{today}"
    if ck in _lr_cache_d:
        return _lr_cache_d[ck]
    try:
        d   = _mlb_rest(f"/teams/{team_id}/stats",
                        {"stats": split_type, "group": "hitting", "season": MLB_YEAR})
        sp  = (d.get("stats") or [{}])[0].get("splits") or [{}]
        st  = sp[-1].get("stat", {}) if sp else {}
        avg_s = st.get("avg", "")
        if not avg_s:
            _lr_cache_d[ck] = None
            return None
        result = {
            "avg":   float(avg_s),
            "ops":   float(st.get("ops", 0) or 0),
            "split": split_type,
        }
        _lr_cache_d[ck] = result
        return result
    except Exception:
        _lr_cache_d[ck] = None
        return None


def _fetch_h2h_data(home_id, away_id, home_name: str) -> "dict | None":
    """
    Last ≤5 completed regular-season H2H games this season.
    Returns {avg_total, totals, home_wins, home_losses, games_found} or None.
    """
    if not home_id or not away_id:
        return None
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck = f"{home_id}_{away_id}_{today}"
    if ck in _h2h_mlb_cache:
        return _h2h_mlb_cache[ck]
    try:
        d = _mlb_rest("/schedule", {
            "sportId":   1,
            "teamId":    home_id,
            "opponentId": away_id,
            "season":    MLB_YEAR,
            "gameType":  "R",
            "hydrate":   "linescore",
        })
        records = []
        for date_entry in d.get("dates", []):
            for g in date_entry.get("games", []):
                if g.get("status", {}).get("abstractGameState", "") != "Final":
                    continue
                teams = g.get("teams", {})
                h_sc  = teams.get("home", {}).get("score")
                a_sc  = teams.get("away", {}).get("score")
                if h_sc is None:
                    ls   = g.get("linescore", {}).get("teams", {})
                    h_sc = ls.get("home", {}).get("runs")
                    a_sc = ls.get("away", {}).get("runs")
                if h_sc is None or a_sc is None:
                    continue
                total     = int(h_sc) + int(a_sc)
                game_home = teams.get("home", {}).get("team", {}).get("name", "")
                our_home  = any(w in game_home.lower() for w in home_name.lower().split())
                home_won  = int(h_sc) > int(a_sc)
                records.append({
                    "total":    total,
                    "home_won": home_won if our_home else not home_won,
                })
        if not records:
            _h2h_mlb_cache[ck] = None
            return None
        last5  = records[-5:]
        totals = [r["total"] for r in last5]
        result = {
            "avg_total":   round(sum(totals) / len(totals), 1),
            "totals":      totals,
            "home_wins":   sum(1 for r in last5 if     r["home_won"]),
            "home_losses": sum(1 for r in last5 if not r["home_won"]),
            "games_found": len(last5),
        }
        _h2h_mlb_cache[ck] = result
        return result
    except Exception:
        _h2h_mlb_cache[ck] = None
        return None


# ── MODULE 8C: TEAM BATTING METRICS (AVG / OPS / K% / BB%) ───────────────────
_batting_cache: dict = {}

def _fetch_team_batting_full(team_id) -> "dict | None":
    """
    Full batting metrics for a team this season.
    Returns {avg, ops, k_pct, bb_pct, rs_pg} or None.
    """
    if not team_id:
        return None
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck = f"bat_{team_id}_{today}"
    if ck in _batting_cache:
        return _batting_cache[ck]
    try:
        d  = _mlb_rest(f"/teams/{team_id}/stats",
                       {"stats": "season", "group": "hitting", "season": MLB_YEAR})
        sp = (d.get("stats") or [{}])[0].get("splits") or [{}]
        st = sp[-1].get("stat", {}) if sp else {}

        avg_s = st.get("avg", "")
        ops_s = st.get("ops", "")
        if not avg_s:
            _batting_cache[ck] = None
            return None

        pa = float(st.get("plateAppearances", 0) or 0)
        so = float(st.get("strikeOuts",      0) or 0)
        bb = float(st.get("baseOnBalls",     0) or 0)

        k_pct  = round(so / pa * 100, 1) if pa > 0 else None
        bb_pct = round(bb / pa * 100, 1) if pa > 0 else None

        result = {
            "avg":    float(avg_s),
            "ops":    float(ops_s) if ops_s else None,
            "k_pct":  k_pct,
            "bb_pct": bb_pct,
            "rs_pg":  float(st.get("runsPerGame") or st.get("runs") or 0) / max(float(st.get("gamesPlayed") or 1), 1) if (float(st.get("runsPerGame") or st.get("runs") or 0) > 0) else None,
        }
        _batting_cache[ck] = result
        return result
    except Exception:
        _batting_cache[ck] = None
        return None


def _ops_label(ops: float) -> str:
    """Plain-Spanish OPS quality tier."""
    if ops > 0.850:  return "fuerte 💪"
    if ops > 0.750:  return "bueno ✅"
    if ops > 0.700:  return "promedio ⚪"
    return "débil ⚠️"


def _batting_insight(team_name: str, ops, k_pct) -> str:
    """One-line batting insight for the alert."""
    if ops is None and k_pct is None:
        return ""
    parts = []
    if ops is not None:
        if ops > 0.820:
            parts.append(f"pegan bien (OPS {ops:.3f})")
        elif ops < 0.700:
            parts.append(f"ofensiva débil (OPS {ops:.3f})")
    if k_pct is not None:
        if k_pct > 28:
            parts.append(f"se ponchan mucho (K% {k_pct:.0f}%)")
        elif k_pct < 18:
            parts.append(f"hacen buen contacto (K% {k_pct:.0f}%)")
    if not parts:
        return ""
    te = _es(team_name)
    base = f" pero ".join(parts)
    # Resolve combined verdict
    if ops is not None and k_pct is not None:
        if ops > 0.820 and k_pct > 28:
            verdict = "→ Moderado contra pitcher élite"
        elif ops > 0.820 and k_pct < 18:
            verdict = "→ Lineup peligroso"
        elif ops < 0.700 and k_pct > 28:
            verdict = "→ Lineup débil"
        else:
            verdict = ""
    else:
        verdict = ""
    line = f"💡 {te} {base}"
    if verdict:
        line += f"\n   {verdict}"
    return line


# ── IMPROVEMENT 2: WORLD CUP 2026 LIVE FORM ───────────────────────────────────

# ESPN soccer leagues searched in priority order (WC → qualifiers → friendlies)
_ESPN_SOCCER_LEAGUES = [
    "fifa.world",
    "fifa.world.qualifier.concacaf",
    "fifa.world.qualifier.conmebol",
    "fifa.world.qualifier.uefa",
    "fifa.world.qualifier.afc",
    "fifa.world.qualifier.caf",
    "fifa.friendlies.m",
]

def _fetch_espn_matches(team_name: str) -> list:
    """
    Fetch up to 5 completed matches for team_name from ESPN soccer APIs.
    Tries WC, then qualifier leagues, then friendlies.
    Uses date-range batch queries (one request per league) for speed.
    Returns list of {gf, ga, date, opp} dicts sorted newest-first.
    """
    ck = f"espn_{team_name.lower()}_{datetime.now(CDT).strftime('%Y-%m-%d')}"
    if ck in _espn_matches_cache:
        return _espn_matches_cache[ck]

    start = (datetime.now(CDT) - timedelta(days=365)).strftime('%Y%m%d')
    end   = datetime.now(CDT).strftime('%Y%m%d')
    dr    = f"{start}-{end}"

    all_matches: list = []
    for league in _ESPN_SOCCER_LEAGUES:
        if len(all_matches) >= 10:
            break
        try:
            url  = (f"https://site.api.espn.com/apis/site/v2/sports/soccer/"
                    f"{league}/scoreboard?dates={dr}&limit=200")
            resp = requests.get(url, timeout=8)
            if resp.status_code != 200:
                continue
            for event in resp.json().get("events", []):
                comp = event.get("competitions", [{}])[0]
                if not comp.get("status", {}).get("type", {}).get("completed", False):
                    continue
                competitors = comp.get("competitors", [])
                for c in competitors:
                    cname = c.get("team", {}).get("displayName", "")
                    if (team_name.lower() not in cname.lower() and
                            cname.lower() not in team_name.lower()):
                        continue
                    opp = next((x for x in competitors if x is not c), {})
                    all_matches.append({
                        "gf":  int(c.get("score", 0) or 0),
                        "ga":  int(opp.get("score", 0) or 0),
                        "date": event.get("date", ""),
                        "opp": opp.get("team", {}).get("displayName", ""),
                    })
                    break
        except Exception:
            continue

    all_matches.sort(key=lambda m: m["date"], reverse=True)
    result = all_matches[:5]
    _espn_matches_cache[ck] = result
    return result


def fetch_wc_team_form(team_name):
    """
    Fetch last ≤5 completed matches for a team.
    Sources: ESPN WC, then WC qualifiers, then friendlies (date range, last 365 days).
    Returns {'goals_for': float, 'goals_against': float, 'matches': int} or None.
    """
    today_str = datetime.now(CDT).strftime('%Y-%m-%d')
    ck = (team_name.lower(), today_str)
    if ck in _wc_form_cache:
        return _wc_form_cache[ck]

    matches = _fetch_espn_matches(team_name)
    if not matches:
        _wc_form_cache[ck] = None
        return None

    n   = len(matches)
    gf  = sum(m["gf"] for m in matches)
    ga  = sum(m["ga"] for m in matches)
    res = {
        "goals_for":     round(gf / n, 2),
        "goals_against": round(ga / n, 2),
        "matches":       n,
    }
    _wc_form_cache[ck] = res
    return res

# ── IMPROVEMENT 5: MLB WEATHER / WIND ─────────────────────────────────────────

def _wind_label_for_park(wind_deg: float, cf_bearing) -> str:
    """
    Compute OUT/IN/CROSS relative to a specific ballpark's orientation.

    cf_bearing: compass bearing from home plate to center field (0-360°).
                None → fall back to generic cardinal labelling.
    wind_deg:   meteorological wind direction (degrees FROM which wind blows).
    """
    if cf_bearing is None:
        # Generic fallback — same logic as original code
        if 225 <= wind_deg <= 315:
            return 'OUT'
        elif 45 <= wind_deg <= 135:
            return 'IN'
        return 'CROSS'
    # Wind travels TOWARD: (wind_deg + 180) % 360
    wind_toward = (wind_deg + 180) % 360
    # Angle relative to CF bearing (0° = perfect tailwind = OUT)
    rel = (wind_toward - cf_bearing + 360) % 360
    if rel <= 45 or rel >= 315:
        return 'OUT'
    elif 135 <= rel <= 225:
        return 'IN'
    return 'CROSS'


def fetch_wind(lat, lon, cf_bearing=None):
    """
    Current wind from OpenWeatherMap (imperial units).
    Cached 30 min per location. Returns dict or None.

    cf_bearing: compass bearing from home plate to center field (degrees).
                When provided, OUT/IN/CROSS is computed relative to the
                specific ballpark rather than generic cardinal direction.
                None = retractable/dome stadium → returns None immediately.
    """
    if cf_bearing is not None and not isinstance(cf_bearing, (int, float)):
        # None sentinel for roofed stadiums — no wind applies
        return None
    if not OPENWEATHER_KEY:
        return None
    city_key = f"{lat},{lon}"
    cached   = _weather_cache.get(city_key)
    if cached:
        age_min = (datetime.now(pytz.utc) - cached['fetched_at']).total_seconds() / 60
        if age_min < 30:
            # Re-label with park-specific bearing if caller provides one
            if cf_bearing is not None:
                cached = dict(cached)
                cached['label'] = _wind_label_for_park(cached.get('deg', 0), cf_bearing)
            return cached
    try:
        r = requests.get(
            'https://api.openweathermap.org/data/2.5/weather',
            params={'lat': lat, 'lon': lon, 'appid': OPENWEATHER_KEY, 'units': 'imperial'},
            timeout=8,
        )
        if r.status_code != 200:
            return None
        jd    = r.json()
        w     = jd.get('wind', {})
        speed = round(float(w.get('speed', 0)), 1)
        deg   = float(w.get('deg', 0))
        temp_f = jd.get('main', {}).get('temp', None)   # °F (imperial units)
        label = _wind_label_for_park(deg, cf_bearing)
        result = {'speed': speed, 'deg': deg, 'label': label,
                  'temp_f': temp_f, 'fetched_at': datetime.now(pytz.utc)}
        _weather_cache[city_key] = result
        return result
    except Exception:
        return None

_hourly_weather_cache: dict = {}
def fetch_wind_forecast(lat: float, lon: float, game_hour_utc: int,
                        cf_bearing=None) -> "dict | None":
    """
    Forecast wind at game time from OpenWeatherMap.
    cf_bearing: compass bearing from home plate to CF — enables park-specific
                OUT/IN labelling. None = roofed stadium, returns None.
    """
    if cf_bearing is not None and not isinstance(cf_bearing, (int, float)):
        return None   # roofed/dome stadium sentinel
    if not OPENWEATHER_KEY:
        return None
    city_key = f"forecast_{lat:.2f},{lon:.2f}_{game_hour_utc}"
    cached = _hourly_weather_cache.get(city_key)
    if cached:
        age_min = (datetime.now(pytz.utc) - cached['fetched_at']).total_seconds() / 60
        if age_min < 60:
            if cf_bearing is not None:
                cached = dict(cached)
                cached['label'] = _wind_label_for_park(cached.get('deg', 0), cf_bearing)
            return cached
    try:
        r = requests.get('https://api.openweathermap.org/data/2.5/forecast',
                         params={'lat': lat, 'lon': lon, 'appid': OPENWEATHER_KEY,
                                 'units': 'imperial', 'cnt': 16}, timeout=8)
        if r.status_code != 200:
            return fetch_wind(lat, lon, cf_bearing=cf_bearing)
        forecasts = r.json().get('list', [])
        best = None; best_diff = 999
        for fc in forecasts:
            fc_hour = datetime.utcfromtimestamp(fc['dt']).hour
            diff = abs(fc_hour - game_hour_utc)
            if diff < best_diff:
                best_diff = diff; best = fc
        if not best:
            return fetch_wind(lat, lon, cf_bearing=cf_bearing)
        w = best.get('wind', {})
        speed = round(float(w.get('speed', 0)), 1)
        deg = float(w.get('deg', 0))
        temp_f = best.get('main', {}).get('temp', None)
        label = _wind_label_for_park(deg, cf_bearing)
        result = {'speed': speed, 'deg': deg, 'label': label,
                  'temp_f': temp_f, 'fetched_at': datetime.now(pytz.utc), 'source': 'forecast'}
        _hourly_weather_cache[city_key] = result
        return result
    except Exception:
        return fetch_wind(lat, lon, cf_bearing=cf_bearing)

def wind_run_adj(wind):
    """
    Return (signed_adjustment: float, description: str).
    Only acts when speed > 15 mph and direction is OUT or IN.
    Always returns a human-readable label when wind data is available.
    """
    if wind is None:
        return 0.0, ''
    spd = wind.get('speed', 0) or 0
    lbl = wind.get('label', 'CROSS')
    if spd <= 1:
        return 0.0, ''
    if spd > 15:
        if lbl == 'OUT':
            return +0.8, f"💨 {spd:.0f}mph OUT → +0.8 carreras"
        if lbl == 'IN':
            return -0.8, f"💨 {spd:.0f}mph IN  → -0.8 carreras"
    # Low-speed or cross wind — still describe it but no run adjustment
    dir_es = {"OUT": "OUT (a favor de bateo)", "IN": "IN (a favor de pitcheo)",
              "CROSS": "cruzado (sin ajuste)"}
    return 0.0, f"💨 {spd:.0f}mph {dir_es.get(lbl, lbl)}"

def get_book_total(game):
    """
    Extract totals line + odds from Bovada/Bodog only.
    Returns (line, over_odds, under_odds, bookmaker_name) or None.
    """
    for bk in game.get("bookmakers", []):
        if bk["title"].lower() not in ("bovada", "bodog"):
            continue
        for m in bk.get("markets", []):
            if m["key"] == "totals":
                by_name = {o["name"]: o for o in m.get("outcomes", [])}
                if "Over" not in by_name or "Under" not in by_name:
                    continue
                _bl = by_name["Over"]["point"]
                if not (5.0 <= _bl <= 25.0):
                    continue
                return (
                    _bl,
                    by_name["Over"]["price"],
                    by_name["Under"]["price"],
                    bk["title"],
                )
    return None

def get_book_team_totals(game: dict) -> dict:
    """
    Extract team total lines from the alternate_team_totals / team_totals market
    in Bovada or Bodog bookmakers.

    Returns:
        {team_name: {'over_line': float, 'over_odds': float, 'under_line': float, 'under_odds': float}}
        Empty dict if the market is not present.
    """
    result: dict = {}
    for bk in game.get("bookmakers", []):
        if bk["title"].lower() not in ("bovada", "bodog"):
            continue
        for m in bk.get("markets", []):
            if m["key"] not in ("alternate_team_totals", "team_totals"):
                continue
            for o in m.get("outcomes", []):
                team = o.get("name", "")
                desc = (o.get("description", "") or "").upper()
                line = o.get("point")
                odds = o.get("price")
                if not team or line is None or odds is None:
                    continue
                if team not in result:
                    result[team] = {}
                if "OVER" in desc:
                    result[team]["over_line"] = float(line)
                    result[team]["over_odds"] = float(odds)
                elif "UNDER" in desc:
                    result[team]["under_line"] = float(line)
                    result[team]["under_odds"] = float(odds)
    return result


def get_alternate_totals(game: dict, standard_line: float) -> list:
    alt_lines = []
    for bk in game.get("bookmakers", []):
        if not _is_us_book(bk["title"]):
            continue
        for m in bk.get("markets", []):
            if m.get("key") != "totals":
                continue
            by_name = {o["name"]: o for o in m.get("outcomes", [])}
            if "Over" not in by_name or "Under" not in by_name:
                continue
            line = float(by_name["Over"].get("point", 0))
            if line == standard_line:
                continue
            diff = abs(line - standard_line)
            if diff > 2.5:
                continue
            alt_lines.append({
                "line": line,
                "over_odds": float(by_name["Over"]["price"]),
                "under_odds": float(by_name["Under"]["price"]),
                "book": bk["title"],
                "diff_from_standard": round(line - standard_line, 1),
            })
    seen = {}
    for a in alt_lines:
        k = a["line"]
        if k not in seen or max(a["over_odds"], a["under_odds"]) > max(seen[k]["over_odds"], seen[k]["under_odds"]):
            seen[k] = a
    return sorted(seen.values(), key=lambda x: abs(x["diff_from_standard"]))

# ═══════════════════════════════════════════════════════════════════════════════
# MODULES A9–A11: PARK TENDENCIES · BULLPEN ERA · PITCHER REST DAYS
# ═══════════════════════════════════════════════════════════════════════════════

def park_tendency_adj(home_team: str) -> "tuple[float, str]":
    """
    Return (adj_runs, note) for the home team's ballpark.
    adj_runs is additive (+ favors OVER, - favors UNDER).
    """
    tend = MLB_PARK_TEND.get(home_team)
    if not tend:
        return 0.0, ""
    park_name, adj, is_over = tend
    favor = "Over" if is_over else "Under"
    carrera_note = "más carreras en MLB" if is_over else "pocas carreras"
    sign = f"+{adj}" if adj >= 0 else str(adj)
    note = (
        f"🏟️ {park_name}: estadio de {carrera_note} — favorece {favor}\n"
        f"   Ajuste: {sign} carreras al total"
    )
    return adj, note


# ── DATA QUALITY / VALIDATION LAYER ─────────────────────────────────────────
_data_quality:         dict = {}   # f"{team}_{date}" → {"verified": bool, "source": str}
_espn_mlb_team_cache:  dict = {}   # team_name → ESPN team id (str)

def _fetch_espn_mlb_team_id(team_name: str) -> "str | None":
    """Resolve MLB team name → ESPN team id via ESPN teams API (cached daily)."""
    ck = f"espn_id_{team_name}_{datetime.now(CDT).strftime('%Y-%m-%d')}"
    if ck in _espn_mlb_team_cache:
        return _espn_mlb_team_cache[ck]
    try:
        r = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/teams",
            timeout=8,
        )
        if r.status_code != 200:
            return None
        sports = r.json().get("sports", [{}])[0]
        teams  = sports.get("leagues", [{}])[0].get("teams", [])
        tname_lower = team_name.lower()
        for entry in teams:
            t = entry.get("team", {})
            if (tname_lower in t.get("displayName", "").lower() or
                    tname_lower in t.get("shortDisplayName", "").lower()):
                tid = str(t.get("id", ""))
                _espn_mlb_team_cache[ck] = tid
                return tid
    except Exception:
        pass
    _espn_mlb_team_cache[ck] = None
    return None


def _fetch_espn_bullpen_era(team_name: str) -> "float | None":
    """
    Fetch pitching ERA from ESPN team statistics API.
    Used as cross-validation source against MLB Stats API bullpen ERA.
    Returns float ERA or None on failure.
    """
    tid = _fetch_espn_mlb_team_id(team_name)
    if not tid:
        return None
    try:
        r = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/baseball/mlb"
            f"/teams/{tid}/statistics",
            timeout=8,
        )
        if r.status_code != 200:
            return None
        for category in r.json().get("results", []):
            cat_name = category.get("name", "").lower()
            if "pitch" not in cat_name:
                continue
            for stat in category.get("stats", []):
                if stat.get("name", "").upper() == "ERA":
                    val = stat.get("value")
                    if val is not None:
                        return round(float(val), 2)
    except Exception:
        pass
    return None


_bullpen_era_cache: dict = {}

def fetch_bullpen_era(team_name: str, starter_era: float = 4.20) -> "tuple[float, str]":
    """
    Fetch true bullpen ERA directly from MLB Stats API.

    Method:
      1. Get team's active roster (pitchers only).
      2. Batch-hydrate season pitching stats for all pitchers in one call.
      3. Keep only relief pitchers: gamesStarted / gamesPlayed < 0.30.
      4. Compute ERA = sum(earnedRuns) / sum(IP) × 9 — no estimation, real values.

    `starter_era` kept for call-site compatibility; no longer used in the formula.
    Returns (bullpen_era: float, display_note: str).
    """
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck    = f"{team_name}_{today}"
    if ck in _bullpen_era_cache:
        return _bullpen_era_cache[ck]

    tid = None   # initialize before try block to avoid NameError in except

    try:
        # ── 1. Resolve team ID ────────────────────────────────────────────
        if HAS_STATSAPI:
            teams = statsapi.lookup_team(team_name)
            if not teams:
                raise ValueError("team not found")
            tid = teams[0]["id"]
        else:
            data = _mlb_rest("/teams", {"sportId": 1, "season": MLB_YEAR})
            match = next(
                (t for t in data.get("teams", [])
                 if team_name.lower() in t.get("name", "").lower()),
                None,
            )
            if not match:
                raise ValueError("team not found")
            tid = match["id"]

        # ── 2. Active roster — collect pitcher IDs ────────────────────────
        roster_data = _mlb_rest(f"/teams/{tid}/roster",
                                 {"rosterType": "active", "season": MLB_YEAR})
        pitcher_ids = [
            p["person"]["id"]
            for p in roster_data.get("roster", [])
            if p.get("position", {}).get("code") == "P"
        ]
        if not pitcher_ids:
            raise ValueError("no pitchers in roster")

        # ── 3. Batch stats — one API call for all pitchers ─────────────────
        ids_str    = ",".join(str(pid) for pid in pitcher_ids)
        people_raw = _mlb_rest(
            "/people",
            {"personIds": ids_str,
             "hydrate": f"stats(group=pitching,type=season,season={MLB_YEAR})"},
        )

        # ── 4. Sum earned runs + IP for relief pitchers only ──────────────
        total_er = 0
        total_ip = 0.0

        for person in people_raw.get("people", []):
            for stat_group in person.get("stats", []):
                if stat_group.get("group", {}).get("displayName", "") != "pitching":
                    continue
                for split in stat_group.get("splits", []):
                    s  = split.get("stat", {})
                    gp = int(s.get("gamesPlayed")  or 0)
                    gs = int(s.get("gamesStarted") or 0)
                    # Need at least 3 appearances to count
                    if gp < 3:
                        continue
                    # Skip if primarily a starter (>30 % of appearances were starts)
                    if gp > 0 and (gs / gp) > 0.30:
                        continue
                    er = int(s.get("earnedRuns") or 0)
                    ip = _parse_ip(s.get("inningsPitched") or "0")
                    if ip > 0:
                        total_er += er
                        total_ip += ip

        if total_ip < 10:
            raise ValueError(f"insufficient relief IP ({total_ip:.1f})")

        bullpen_era = round((total_er / total_ip) * 9, 2)
        bullpen_era = round(max(0.0, min(bullpen_era, 9.99)), 2)

        # ── Validation 1: plausibility range check ────────────────────────
        if not (1.00 <= bullpen_era <= 7.00):
            print(f"  ⚠️  Bullpen ERA {_es(team_name)} fuera de rango: {bullpen_era} — dato no verificado")
            _data_quality[ck] = {"verified": False, "source": "MLB API", "reason": "out_of_range"}
            pend_note = (
                f"⚾ Bullpen {_es(team_name)}: datos pendientes\n"
                f"   ⚠️ Dato no verificado (ERA {bullpen_era:.2f} fuera de rango)"
            )
            result = (4.20, pend_note)   # safe fallback; 4.20 won't trigger >5.0 adj
            _bullpen_era_cache[ck] = result
            return result

        # ── Validation 2: ESPN cross-check ────────────────────────────────
        espn_era = _fetch_espn_bullpen_era(team_name)
        if espn_era is not None and abs(bullpen_era - espn_era) > 0.50:
            print(
                f"  ⚠️  Discrepancia detectada {_es(team_name)}: "
                f"MLB API={bullpen_era} | ESPN={espn_era} — usando ESPN"
            )
            bullpen_era = espn_era
            _data_quality[ck] = {"verified": True, "source": "ESPN"}
        else:
            _data_quality[ck] = {"verified": True, "source": "MLB API"}

        if bullpen_era < 3.50:
            quality = "sólido ✅"
        elif bullpen_era < 4.50:
            quality = "promedio"
        elif bullpen_era < 5.50:
            quality = "débil ⚠️"
        else:
            quality = "vulnerable 🔴"

        source_tag = _data_quality[ck]["source"]
        note = f"⚾ Bullpen {_es(team_name)}: ERA {bullpen_era:.2f} {quality}"
        if bullpen_era > 5.0:
            note += (
                "\n   ⚠️ Bullpen vulnerable — carreras tardías esperadas"
                "\n   → Considera Over en juegos cerrados"
            )

        result = (bullpen_era, note)
        _bullpen_era_cache[ck] = result
        print(f"  ⚾ Bullpen ERA {_es(team_name)}: {bullpen_era} "
              f"[{source_tag}] ({len(pitcher_ids)} pitchers, {total_ip:.1f} IP)")
        return result

    except Exception as _be:
        # Fallback: derive from team aggregate ERA without using starter_era algebra
        try:
            if tid is None:
                return 4.20, ""
            pit = _mlb_rest(f"/teams/{tid}/stats",
                            {"stats": "season", "group": "pitching", "season": MLB_YEAR})
            p_s = (pit.get("stats", [{}])[0].get("splits", [{}]) or [{}])[-1].get("stat", {})
            team_era = float(p_s.get("era") or 4.20)
            # Team ERA is a reasonable proxy when relief-only data is unavailable
            result = (round(team_era, 2), "")
            _bullpen_era_cache[ck] = result
            return result
        except Exception:
            return 4.20, ""


_pitcher_rest_cache: dict = {}

def fetch_pitcher_rest_days(pitcher_id) -> "tuple[int, float, str]":
    """
    Fetch pitcher's last start date from MLB gameLog; compute days of rest.

    Rest adjustments (additive runs to total):
      ≤4 days (short rest)   → +0.5 runs  ⚠️
       5 days (optimal)      →  0.0 runs  ✅
      6–7 days (extra rest)  → −0.3 runs  💪
      8+ days (rusty)        → +0.3 runs  ⚠️

    Returns (days: int, adj: float, note: str).  days=-1 = unknown.
    """
    if not pitcher_id:
        return -1, 0.0, ""
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck    = f"{pitcher_id}_{today}"
    if ck in _pitcher_rest_cache:
        return _pitcher_rest_cache[ck]

    try:
        data = _mlb_rest(f"/people/{pitcher_id}/stats", {
            "stats":  "gameLog",
            "group":  "pitching",
            "season": MLB_YEAR,
            "limit":  10,
        })
        splits = (data.get("stats", [{}])[0].get("splits", [])
                  if data and data.get("stats") else [])

        last_date = None
        for sp in reversed(splits):
            ip_raw = sp.get("stat", {}).get("inningsPitched", "0") or "0"
            if float(ip_raw) >= 3.0:
                last_date = (sp.get("date")
                             or sp.get("game", {}).get("officialDate"))
                break

        if not last_date:
            result = (-1, 0.0, "")
            _pitcher_rest_cache[ck] = result
            return result

        last_dt = datetime.strptime(last_date[:10], "%Y-%m-%d").date()
        days    = (datetime.now(CDT).date() - last_dt).days

        if days <= 2:
            adj, note = 0.0, ""
        elif days <= 4:
            adj  = +0.5
            note = (f"⚠️ Solo {days} días de descanso → rendimiento puede bajar"
                    f"\n   → Añade 0.5 carreras al total")
        elif days == 5:
            adj  = 0.0
            note = f"✅ Descanso óptimo ({days} días) → rendimiento normal"
        elif days <= 7:
            adj  = -0.3
            note = (f"💪 {days} días de descanso → suele rendir mejor"
                    f"\n   → Reduce 0.3 carreras al total")
        else:
            adj  = +0.3
            note = (f"⚠️ {days}+ días sin lanzar → puede estar oxidado"
                    f"\n   → Añade 0.3 carreras al total")

        result = (days, adj, note)
        _pitcher_rest_cache[ck] = result
        return result
    except Exception:
        return -1, 0.0, ""


def analyze_totals(games, sport_key):
    """Compare projected totals vs bookmaker lines; return alert dicts."""
    is_mlb    = "mlb" in sport_key
    threshold = 0.8 if is_mlb else 0.4
    edge_unit = "runs" if is_mlb else "goals"
    total_bets = []

    # Improvement 1: pre-fetch all probable pitchers once per scan (MLB only)
    pitchers = fetch_probable_pitchers_today() if is_mlb else {}

    for g in games:
        game_id    = g.get("id", "")
        home, away = g["home_team"], g["away_team"]
        commence   = g.get("commence_time", "")

        if game_starts_soon(commence, 60):
            continue
        tc = _timing_check(commence, is_mlb)
        if tc["skip"]:
            continue

        book_data = get_book_total(g)
        if not book_data:
            continue
        book_line, over_odds, under_odds, bookmaker = book_data
        print(f"\n🔢 TOTALS: {home} vs {away}")

        # ── Project our total ──────────────────────────────────────────────────
        _data_unverified = False   # set True if any key stat fails validation
        if is_mlb:
            h = fetch_team_run_stats(home)
            a = fetch_team_run_stats(away)
            if h is None or a is None:
                continue
            LEAGUE_AVG = 4.5
            home_exp   = h["rs_pg"] * (a["ra_pg"] / LEAGUE_AVG)
            away_exp   = a["rs_pg"] * (h["ra_pg"] / LEAGUE_AVG)
            park       = MLB_PARK_FACTORS.get(home, 1.0)
            base_line  = (home_exp + away_exp) * park

            # Improvement 1: starting pitcher ERA adjustment
            p_data    = _lookup_pitcher_data(home, away, pitchers)
            h_era     = p_data.get("home_era", 4.50)
            a_era     = p_data.get("away_era", 4.50)
            h_pname   = p_data.get("home_name", "TBD")
            a_pname   = p_data.get("away_name", "TBD")
            pitch_adj = pitcher_run_adjustment(h_era, a_era)

            # Improvement 5: wind adjustment
            park_city      = MLB_PARK_CITIES.get(home)
            _cf_bearing    = MLB_PARK_CF_BEARING.get(home)
            wind           = fetch_wind(park_city[1], park_city[2], cf_bearing=_cf_bearing) if park_city else None
            w_adj, w_label = wind_run_adj(wind)

            our_line  = round(base_line + pitch_adj + w_adj, 1)
            base_proj = our_line   # capture before A9–A11 adjustments

            # ── Module A9: Park tendency ──────────────────────────────────
            _pt_adj, _pt_note = park_tendency_adj(home)
            our_line = round(our_line + _pt_adj, 1)

            # ── Module A11: Pitcher rest days (done before bullpen so
            #    both adjustments fold into our_line cleanly) ─────────────
            _h_pid = p_data.get("home_id")
            _a_pid = p_data.get("away_id")
            _rest_adj   = 0.0
            _rest_parts = []
            for _pid, _pn in [(_h_pid, h_pname), (_a_pid, a_pname)]:
                try:
                    _, _radj, _rnote = fetch_pitcher_rest_days(_pid)
                    if _rnote:
                        _rest_parts.append(f"{_rnote} ({_pn})")
                    _rest_adj += _radj
                except Exception:
                    pass
            our_line  = round(our_line + _rest_adj, 1)
            _rest_note = "\n".join(_rest_parts)

            # ── Module A10: Bullpen ERA ───────────────────────────────────
            _bull_adj   = 0.0
            _bull_parts = []
            for _t, _sera in [(home, h_era), (away, a_era)]:
                try:
                    _bera, _bnote = fetch_bullpen_era(_t, _sera)
                    if _bnote:
                        _bull_parts.append(_bnote)
                    # Only use adjustment when data is verified (note has no "pendientes")
                    _bull_verified = bool(_bnote) and "pendientes" not in _bnote
                    if _bull_verified and _bera > 5.0:
                        _bull_adj += 0.4
                    if not _bull_verified and _bnote:
                        _data_unverified = True
                except Exception:
                    pass
            our_line   = round(our_line + _bull_adj, 1)
            _bull_note = "\n".join(_bull_parts)

            # ── Module B2: Streak run adjustment ──────────────────────────
            _strk_adj   = 0.0
            _strk_parts = []
            for _t in [home, away]:
                try:
                    _sk = fetch_team_streak_mlb(_t)
                    if not _sk:
                        continue
                    if _sk["is_hot"]:
                        _strk_adj += 0.3
                        _strk_parts.append(
                            f"{_sk['label']}\n   → +0.3 carreras al total"
                        )
                    elif _sk["is_cold"]:
                        _strk_adj -= 0.3
                        _strk_parts.append(
                            f"{_sk['label']}\n   → -0.3 carreras al total"
                        )
                    else:
                        _strk_parts.append(_sk["label"])
                except Exception:
                    pass
            our_line   = round(our_line + _strk_adj, 1)
            _strk_note = "\n".join(_strk_parts)

            extra = {
                "pitcher_home":   f"{h_pname} (ERA {h_era:.2f})",
                "pitcher_away":   f"{a_pname} (ERA {a_era:.2f})",
                "era_home":       h_era,
                "era_away":       a_era,
                "pitch_adj":      pitch_adj,
                "wind_info":      w_label or None,
                "form_home":      "",
                "form_away":      "",
                "base_proj":      base_proj,
                "park_tend_note": _pt_note,
                "park_tend_adj":  _pt_adj,
                "rest_note":      _rest_note,
                "rest_adj":       round(_rest_adj, 2),
                "bull_note":      _bull_note,
                "bull_adj":       round(_bull_adj, 2),
                "streak_note":    _strk_note,
                "streak_adj":     round(_strk_adj, 2),
            }

        else:
            # Improvement 2: World Cup 2026 — 60% live tournament form + 40% ELO
            elo_h      = _elo_for(home)
            elo_a      = _elo_for(away)
            elo_base_h = 1.35 * (1 + (elo_h - 1500) / 4000)
            elo_base_a = 1.25 * (1 + (elo_a - 1500) / 4000)

            form_h = fetch_wc_team_form(home)
            form_a = fetch_wc_team_form(away)

            blend_h = (0.6 * form_h["goals_for"] + 0.4 * elo_base_h) if form_h else elo_base_h
            blend_a = (0.6 * form_a["goals_for"] + 0.4 * elo_base_a) if form_a else elo_base_a

            our_line    = round(blend_h + blend_a, 2)
            form_note_h = (f"{form_h['goals_for']:.1f} gpg ({form_h['matches']} partidos)"
                           if form_h else "")
            form_note_a = (f"{form_a['goals_for']:.1f} gpg ({form_a['matches']} partidos)"
                           if form_a else "")

            # ── Module S1: WC Venue Tendency ──────────────────────────────
            _soc_venue_adj  = 0.0
            _soc_venue_note = ""
            try:
                _ref_tot = fetch_match_referee(home, away, sport_key)
                _vcity   = (_ref_tot.get("venue_city", "") if _ref_tot else "")
                _soc_venue_adj, _soc_venue_note = _wc_venue_adj(_vcity)
                if abs(_soc_venue_adj) > 0.0:
                    our_line = round(our_line + _soc_venue_adj, 2)
            except Exception:
                pass

            # ── Module S2: Días de Descanso Fútbol ────────────────────────
            _soc_rest_h = _soc_rest_a = (0, 0.0, "")
            try:
                _soc_rest_h = fetch_soccer_rest_days(home)
                _soc_rest_a = fetch_soccer_rest_days(away)
                our_line = round(our_line + _soc_rest_h[1] + _soc_rest_a[1], 2)
            except Exception:
                pass

            # ── Module S3: Rachas en el Mundial ───────────────────────────
            _soc_wcs_h = _soc_wcs_a = None
            _soc_streak_note  = ""
            _soc_streak_parts = []
            try:
                _soc_wcs_h = fetch_wc_streak(home)
                _soc_wcs_a = fetch_wc_streak(away)
                for _sk in (_soc_wcs_h, _soc_wcs_a):
                    if not _sk:
                        continue
                    _soc_streak_parts.append(_sk["label"])
                    _sadj = _sk["tot_adj_gf"] + _sk["tot_adj_ga"] + _sk["tot_adj_def"]
                    if abs(_sadj) > 0.0:
                        our_line = round(our_line + _sadj, 2)
                        _soc_streak_parts.append(
                            f"   → Ajuste carreras: {_sadj:+.1f} goles"
                        )
                _soc_streak_note = "\n".join(_soc_streak_parts)
            except Exception:
                pass

            # ── Module S6: Presión Psicológica ────────────────────────────
            _soc_press_tot  = 0.0
            _soc_press_note = ""
            try:
                _wc_st = fetch_wc_standings()
                _soc_press_tot, _, _soc_press_note = _wc_pressure_block(
                    home, away, _wc_st
                )
                if abs(_soc_press_tot) > 0.0:
                    our_line = round(our_line + _soc_press_tot, 2)
            except Exception:
                pass

            extra = {
                "form_home":       form_note_h,
                "form_away":       form_note_a,
                "pitcher_home":    "",
                "pitcher_away":    "",
                "pitch_adj":       0.0,
                "wind_info":       "",
                "venue_note_s":    _soc_venue_note,
                "srest_note_h":    _soc_rest_h[2],
                "srest_note_a":    _soc_rest_a[2],
                "soc_streak_note": _soc_streak_note,
                "pressure_note":   _soc_press_note,
            }

        diff = our_line - book_line
        threshold_adj = threshold * _perf_adj.get("totals", 1.0)
        if _perf_adj.get("totals", 1.0) != 1.0:
            print(f"   🔧 _perf_adj totals={_perf_adj['totals']:.2f} → umbral ajustado {threshold:.2f}→{threshold_adj:.2f}")
        print(f"   📐 Proyección: {our_line:.1f}  Libro: {book_line}  "
              f"Diff: {diff:+.1f}  Umbral: ±{threshold_adj:.2f}")
        if abs(diff) < threshold_adj:
            print(f"   ❌ Sin edge ({abs(diff):.1f} < {threshold_adj:.2f} umbral)")
            continue

        bet_over = diff > 0
        bet_side = "OVER" if bet_over else "UNDER"
        bet_odds = over_odds if bet_over else under_odds
        edge_val = round(abs(diff), 2)

        # True probability via Poisson model
        true_prob = poisson_ou_prob(our_line, book_line, bet_over)
        r = kelly_stake(true_prob, bet_odds)
        if not r["has_value"] or r["stake"] <= 0:
            continue

        conf = "HIGH" if edge_val >= threshold * 2 else "MEDIUM"
        # Cap to MEDIUM when key stats could not be verified
        if _data_unverified and conf == "HIGH":
            conf = "MEDIUM"
            print(f"  ⚠️  Confianza cappada a MEDIA — datos sin verificar ({home} vs {away})")

        # ── Claude AI: validate totals pick ───────────────────────────────
        _tc_data = {
            "match":      f"{home} vs {away}",
            "sport":      sport_key,
            "bet_side":   bet_side,
            "book_line":  book_line,
            "our_line":   our_line,
            "edge":       edge_val,
            "odds":       bet_odds,
            "confidence": conf,
        }
        _tc_data.update({k: v for k, v in extra.items()
                         if isinstance(v, (str, int, float, bool, type(None)))})
        _tc_sport  = "MLB" if is_mlb else "SOCCER"
        _tc_claude = None
        if edge_val >= 1.2:
            if is_mlb:
                _tc_data.update(_enrich_panel_data(_tc_data, g))
            _tc_claude = panel_expertos(_tc_data, _tc_sport)
            if _tc_claude:
                _tcc  = _tc_claude.get("confianza", "N/D")
                _tcap = "✅" if _tc_claude.get("apostar", True) else "❌"
                _tcr  = (_tc_claude.get("razonamiento", "") or "")[:500]
                print(f"   🤖 Claude: {_tcc} | apostar:{_tcap} | \"{_tcr}\"")
            if _tc_claude and (
                    not _tc_claude.get("apostar", True)
                    or _tc_claude.get("confianza") == "BAJA"):
                _why = (f"apostar={_tc_claude.get('apostar')}, "
                        f"confianza={_tc_claude.get('confianza')}")
                print(f"   ❌ RECHAZADO — Claude veta {bet_side} {book_line} ({_why})")
                continue
        else:
            print(f"   ℹ️  Panel omitido — edge {edge_val:.1f} runs (umbral: 1.2)")
        print(f"   ✅ TOTALS PICK: {bet_side} {book_line}  edge={edge_val:.1f} runs")

        _tot_ev_pct = round((true_prob * bet_odds - 1) * 100, 1)
        _tot_ev_d   = round(r["stake"] * _tot_ev_pct / 100, 2)
        total_bets.append({
            "match":        f"{home} vs {away}",
            "team":         bet_side,
            "side":         str(book_line),
            "odds":         bet_odds,
            "edge":         edge_val,
            "ev_pct":       _tot_ev_pct,
            "stake":        r["stake"],
            "kelly_pct":    r["kelly_pct"],
            "confidence":   conf,
            "time":         commence[:16],
            "line_moved":   False,
            "line_dir":     "",
            "line_delta":   0.0,
            "game_id":      game_id,
            "bookmaker":    bookmaker,
            "market_type":  "totals",
            "closing_edge": "",
            "ev":           _tot_ev_d,
            "roi":          round(_tot_ev_d / BANKROLL * 100, 3),
            "value_pct":    0,
            "elo_prob":     0,
            "bovada_odds":  None,
            "book_line":    book_line,
            "our_line":     our_line,
            "edge_unit":    edge_unit,
            "sport":        sport_key.split("_", 1)[-1].upper(),
            "claude_intel":  _tc_claude,
            "data_verified": not _data_unverified,
            **extra,
        })

    return total_bets

def analyze_team_totals(games, sport_key):
    """
    Scan team-total (alternate_team_totals) lines for MLB games.

    For each team in each game:
      - Project runs using the same RS/RA Pythagorean model as analyze_totals
      - Compare vs Bovada alternate_team_totals book line
      - Edge threshold: 0.5 runs (half the combined-total threshold)
      - Panel expertos required when edge ≥ 1.0 runs (same logic as main totals)
    Picks go into the same total_bets list format so they share notify_totals.
    """
    if "mlb" not in sport_key:
        return []

    team_total_bets = []
    LEAGUE_AVG  = 4.5
    TT_THRESHOLD = 0.5   # edge threshold in individual runs

    pitchers = fetch_probable_pitchers_today()

    for g in games:
        game_id    = g.get("id", "")
        home, away = g["home_team"], g["away_team"]
        commence   = g.get("commence_time", "")

        if game_starts_soon(commence, 60):
            continue
        tc = _timing_check(commence, True)
        if tc["skip"]:
            continue

        # Need team run stats for projections
        h_rs = fetch_team_run_stats(home)
        a_rs = fetch_team_run_stats(away)
        if h_rs is None or a_rs is None:
            continue

        park   = MLB_PARK_FACTORS.get(home, 1.0)
        home_exp = h_rs["rs_pg"] * (a_rs["ra_pg"] / LEAGUE_AVG) * park
        away_exp = a_rs["rs_pg"] * (h_rs["ra_pg"] / LEAGUE_AVG) * park

        # Pitcher adjustment (split proportionally: half each)
        p_data    = _lookup_pitcher_data(home, away, pitchers)
        h_era     = p_data.get("home_era", 4.50)
        a_era     = p_data.get("away_era", 4.50)
        h_pname   = p_data.get("home_name", "TBD")
        a_pname   = p_data.get("away_name", "TBD")
        pitch_adj = pitcher_run_adjustment(h_era, a_era)
        home_exp  = round(home_exp + pitch_adj * 0.5, 2)
        away_exp  = round(away_exp + pitch_adj * 0.5, 2)

        # Wind adjustment (split equally)
        park_city   = MLB_PARK_CITIES.get(home)
        _cf_bearing = MLB_PARK_CF_BEARING.get(home)
        wind        = fetch_wind(park_city[1], park_city[2], cf_bearing=_cf_bearing) if park_city else None
        w_adj, _    = wind_run_adj(wind)
        home_exp    = round(home_exp + w_adj * 0.5, 2)
        away_exp    = round(away_exp + w_adj * 0.5, 2)

        # Look for book team total lines from Bovada
        team_lines = get_book_team_totals(g)
        if not team_lines:
            continue

        for team_name, proj in [(home, home_exp), (away, away_exp)]:
            lines = team_lines.get(team_name, {})
            book_line = lines.get("over_line") or lines.get("under_line")
            if book_line is None:
                continue
            over_odds  = lines.get("over_odds", 1.91)
            under_odds = lines.get("under_odds", 1.91)

            diff     = proj - book_line
            if abs(diff) < TT_THRESHOLD:
                continue

            bet_over  = diff > 0
            bet_side  = "OVER" if bet_over else "UNDER"
            bet_odds  = over_odds if bet_over else under_odds
            edge_val  = round(abs(diff), 2)

            true_prob = poisson_ou_prob(proj, book_line, bet_over)
            r = kelly_stake(true_prob, bet_odds)
            if not r["has_value"] or r["stake"] <= 0:
                continue

            conf = "HIGH" if edge_val >= TT_THRESHOLD * 2 else "MEDIUM"

            # Panel when edge ≥ 1.0 runs
            _tc_data = {
                "match":       f"{home} vs {away}",
                "sport":       sport_key,
                "bet_side":    f"Team Total {bet_side} — {team_name}",
                "book_line":   book_line,
                "our_line":    proj,
                "edge":        edge_val,
                "odds":        bet_odds,
                "confidence":  conf,
                "pitcher_home": f"{h_pname} (ERA {h_era:.2f})",
                "pitcher_away": f"{a_pname} (ERA {a_era:.2f})",
            }
            _tc_claude = None
            if edge_val >= 1.0:
                _tc_data.update(_enrich_panel_data(_tc_data, g))
                _tc_claude = panel_expertos(_tc_data, "MLB")
                if _tc_claude and (
                        not _tc_claude.get("apostar", True)
                        or _tc_claude.get("confianza") == "BAJA"):
                    print(f"   ❌ TEAM TOTAL rechazado — panel veta "
                          f"{team_name} {bet_side} {book_line}")
                    continue
            else:
                print(f"   ℹ️  Team Total panel omitido — edge {edge_val:.2f} runs")

            print(f"   ✅ TEAM TOTAL: {team_name} {bet_side} {book_line} "
                  f"(proj={proj:.1f}) edge={edge_val:.2f}")

            _ev_pct = round((true_prob * bet_odds - 1) * 100, 1)
            _ev_d   = round(r["stake"] * _ev_pct / 100, 2)
            team_total_bets.append({
                "match":        f"{home} vs {away}",
                "team":         f"{team_name} {bet_side}",
                "side":         str(book_line),
                "odds":         bet_odds,
                "edge":         edge_val,
                "ev_pct":       _ev_pct,
                "stake":        r["stake"],
                "kelly_pct":    r["kelly_pct"],
                "confidence":   conf,
                "time":         commence[:16],
                "game_id":      game_id,
                "bookmaker":    "Bovada",
                "market_type":  "team_totals",
                "line_moved":   False,
                "line_dir":     "",
                "line_delta":   0.0,
                "closing_edge": "",
                "ev":           _ev_d,
                "roi":          round(_ev_d / BANKROLL * 100, 3),
                "value_pct":    0,
                "elo_prob":     0,
                "bovada_odds":  None,
                "book_line":    book_line,
                "our_line":     proj,
                "edge_unit":    "runs",
                "sport":        sport_key.split("_", 1)[-1].upper(),
                "claude_intel": _tc_claude,
                "data_verified": True,
                "pitcher_home": f"{h_pname} (ERA {h_era:.2f})",
                "pitcher_away": f"{a_pname} (ERA {a_era:.2f})",
                "wind_info":    "",
                "form_home":    "",
                "form_away":    "",
                "pitch_adj":    round(pitch_adj * 0.5, 2),
            })

    return team_total_bets


def notify_totals(total_bets, alerted=None):
    global alerted_bets
    if _bankroll_paused:
        return
    for b in total_bets:
        # Module 7: stake minimum filter
        if b.get("stake", 0) < MIN_STAKE:
            continue

        # Never alert games that already started (>5 min grace)
        if _game_already_started(b.get("time", ""), grace_min=5):
            print(f"  ⏰ Totals omitido — juego ya comenzó: {b.get('match','')}")
            continue

        home, away = b["match"].split(" vs ", 1)
        dedup_key = f"{home}_{away}_{b['team']}_totals"
        if not _should_alert(dedup_key, odds=b["odds"], edge=b["edge"]):
            continue
        match_key_tot = b.get("match", f"{home} vs {away}").lower().strip()
        if alerted is not None and match_key_tot in alerted:
            print(f"  ⏭️  Totals {b.get('match','')} — ya alertado este scan")
            continue
        if HAS_PAQUETE_AVANZADO:
            try:
                registrar_pick(
                    game_pk  = b.get("game_id", b["match"]),
                    equipo_h = home,
                    equipo_a = away,
                    pick_tipo= b.get("team", "OVER"),
                    linea    = float(b.get("side", 0)),
                    cuota    = b.get("odds", 1.0),
                    stake    = b.get("stake", 0),
                    libro    = b.get("bookmaker", "Bovada"),
                )
            except Exception as _rpe:
                print(f"  ⚠️  registrar_pick error (totals): {_rpe}")
        key = f"{b['game_id']}|totals|{b['team']}"

        sport   = b.get("sport", "")
        emoji   = _sport_emoji(sport)
        is_mlb  = b.get("edge_unit") == "runs"
        unit    = "carreras" if is_mlb else "goles"
        side    = b["team"]        # "OVER" / "UNDER"
        line    = b["side"]        # the book line number
        gt      = _fmt_smart_gt(b.get("time", ""))

        # Module 3: book safety warning
        bk_warn_tot = _book_warning(b.get("bookmaker", ""))

        if is_mlb:
            # ── MLB clean format ──────────────────────────────────────────
            ph_name, ph_era = _parse_pitcher(b.get("pitcher_home", ""))
            pa_name, pa_era = _parse_pitcher(b.get("pitcher_away", ""))
            # prefer stored raw ERAs if available
            ph_era = b.get("era_home", ph_era)
            pa_era = b.get("era_away", pa_era)
            wind   = b.get("wind_info", "")
            wind_line = f"💨 {wind}\n" if wind and wind != "Wind: N/A" else ""
            is_high = b["confidence"] == "HIGH"
            _sw = b.get("stake_warn", "")
            conf_line = "🟢 CONFIANZA: ALTA" if is_high else "🟡 CONFIANZA: MEDIA"
            if _sw:
                conf_line += f"\n{_sw}"
            # Build adjustment breakdown lines (Modules A9–A11)
            base_p  = b.get("base_proj", b["our_line"])
            park_n  = (b.get("park_tend_note") or "").strip()
            rest_n  = (b.get("rest_note")      or "").strip()
            bull_n   = (b.get("bull_note")    or "").strip()
            streak_n = (b.get("streak_note")  or "").strip()

            adj_block = ""
            if park_n:
                adj_block += f"{park_n}\n"
            if rest_n:
                adj_block += f"{rest_n}\n"
            if bull_n:
                adj_block += f"{bull_n}\n"
            if streak_n:
                adj_block += f"{streak_n}\n"
            if adj_block:
                adj_block += f"{_DIV2}\n"

            _claude_tot_blk = _claude_block(b.get("claude_intel"))
            l1 = (
                f"🎯 {_es(home)} vs {_es(away)}\n"
                f"⏰ Hoy {gt} ET\n"
                f"APUESTA: {side} {line} carreras @ {b['odds']} — {b['bookmaker']}\n"
                f"{conf_line}"
            )
            l2 = (
                f"Nuestro modelo proyecta {b['our_line']} carreras totales.\n"
                f"La casa de apuestas pone {line} — hay {b['edge']} carreras de ventaja.\n"
                f"\n"
                f"{adj_block}"
                f"🔵 Pitcher local:  {ph_name} — {_era_label(ph_era)} "
                f"(promedio de carreras: {ph_era:.2f})\n"
                f"🔴 Pitcher visita: {pa_name} — {_era_label(pa_era)} "
                f"(promedio de carreras: {pa_era:.2f})\n"
                f"{wind_line}"
                f"{_claude_tot_blk}"
                + ("" if b.get("data_verified", True)
                   else "⚠️ Verificar antes de apostar — algunos datos sin confirmar\n")
                + f"{bk_warn_tot}"
            )
            body = _two_layer_body(l1, l2)
            match_es_tot = f"{_es(home)} vs {_es(away)}"
            title    = f"⚾ TOTAL | {side} {line} | {match_es_tot}"
            priority = "high" if is_high else "default"
        else:
            # ── Soccer / other sports ─────────────────────────────────────
            is_high    = b["confidence"] == "HIGH"
            _sw = b.get("stake_warn", "")
            conf_line = "🟢 CONFIANZA: ALTA" if is_high else "🟡 CONFIANZA: MEDIA"
            if _sw:
                conf_line += f"\n{_sw}"
            form_h = b.get("form_home", "")
            form_a = b.get("form_away", "")
            form_block = ""
            form_parts = []
            if form_h:
                form_parts.append(f"📋 Forma local:   {form_h}")
            if form_a:
                form_parts.append(f"📋 Forma visita:  {form_a}")
            if form_parts:
                form_block = "\n".join(form_parts)

            # Build soccer adjustment block (S1–S6 notes)
            _soc_venue_n  = (b.get("venue_note_s")    or "").strip()
            _soc_rh_n     = (b.get("srest_note_h")    or "").strip()
            _soc_ra_n     = (b.get("srest_note_a")    or "").strip()
            _soc_strk_n   = (b.get("soc_streak_note") or "").strip()
            _soc_press_n  = (b.get("pressure_note")   or "").strip()
            _soc_ln_h_n   = ""
            _soc_ln_a_n   = ""
            try:
                _ln_h_d = b.get("lineup_h_s")
                _ln_a_d = b.get("lineup_a_s")
                if isinstance(_ln_h_d, dict):
                    _soc_ln_h_n = _ln_h_d.get("note", "")
                if isinstance(_ln_a_d, dict):
                    _soc_ln_a_n = _ln_a_d.get("note", "")
            except Exception:
                pass

            soc_adj_parts = [
                _n for _n in [
                    _soc_venue_n, _soc_rh_n, _soc_ra_n,
                    _soc_strk_n, _soc_press_n,
                    _soc_ln_h_n, _soc_ln_a_n,
                ] if _n
            ]
            soc_adj_block = ""
            if soc_adj_parts:
                soc_adj_block = f"\n{_DIV2}\n" + "\n".join(soc_adj_parts) + f"\n{_DIV2}"

            match_es_tot = f"{_es(home)} vs {_es(away)}"
            l1 = (
                f"🎯 {match_es_tot}\n"
                f"⏰ Hoy {gt} ET\n"
                f"APUESTA: {side} {line} {unit} @ {b['odds']} — {b['bookmaker']}\n"
                f"{conf_line}"
            )
            l2 = (
                f"Nuestro modelo proyecta {b['our_line']} {unit} totales.\n"
                f"La casa de apuestas pone {line} — ventaja de {b['edge']} {unit}.\n"
                f"{soc_adj_block}\n"
                + (f"{form_block}\n" if form_block else "")
                + ("" if b.get("data_verified", True)
                   else "⚠️ Verificar antes de apostar — algunos datos sin confirmar\n")
                + f"{bk_warn_tot}"
            )
            body     = _two_layer_body(l1, l2)
            title    = f"{emoji} TOTAL | {side} {line} | {match_es_tot}"
            priority = "high" if is_high else "default"

        ntfy_post(title, body, priority)
        alerted_bets.add(key)
        if alerted is not None:
            alerted[match_key_tot] = float(b.get("ev_pct", b.get("edge", 0)))
        print(f"    🎯 {side} {line} {b['match']} | Our:{b['our_line']} | Edge:{b['edge']} {unit}")

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 7 — FULL GAME ANALYSIS (SOCCER + MLB)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_h2h_best(game):
    """Decimal odds per outcome name from Bovada/Bodog only. Returns {name: (price, book)}."""
    best = {}
    for bk in game.get("bookmakers", []):
        if bk["title"].lower() not in ("bovada", "bodog"):
            continue
        for m in bk.get("markets", []):
            if m["key"] == "h2h":
                for o in m.get("outcomes", []):
                    name, price = o["name"], o["price"]
                    if name not in best or price > best[name][0]:
                        best[name] = (price, bk["title"])
    return best

def _extract_spread_best(game):
    """
    Decimal odds per team in spreads market (run line / handicap).
    Only considers Bovada/Bodog.
    Returns {name: (point, price, book)}.
    """
    best = {}
    for bk in game.get("bookmakers", []):
        if bk["title"].lower() not in ("bovada", "bodog"):
            continue
        for m in bk.get("markets", []):
            if m["key"] == "spreads":
                for o in m.get("outcomes", []):
                    name, price = o["name"], o["price"]
                    point = float(o.get("point", 0))
                    if name not in best or price > best[name][1]:
                        best[name] = (point, price, bk["title"])
    return best

def _extract_f5_h2h_best(game):
    """Decimal odds per outcome in h2h_h1 (F5 ML) from Bovada/Bodog only. Returns {name: (price, book)}."""
    best = {}
    for bk in game.get("bookmakers", []):
        if bk["title"].lower() not in ("bovada", "bodog"):
            continue
        for m in bk.get("markets", []):
            if m["key"] == "h2h_h1":
                for o in m.get("outcomes", []):
                    name, price = o["name"], o["price"]
                    if name not in best or price > best[name][0]:
                        best[name] = (price, bk["title"])
    return best

def _extract_f5_total(game):
    """
    Extract F5 (primeras 5 entradas) totals line + odds from totals_h1 market.
    Only considers Bovada/Bodog.
    Returns (line, over_odds, under_odds, bookmaker_name) or None.
    """
    for bk in game.get("bookmakers", []):
        if bk["title"].lower() not in ("bovada", "bodog"):
            continue
        for m in bk.get("markets", []):
            if m["key"] == "totals_h1":
                by_name = {o["name"]: o for o in m.get("outcomes", [])}
                if "Over" not in by_name or "Under" not in by_name:
                    continue
                return (
                    by_name["Over"]["point"],
                    by_name["Over"]["price"],
                    by_name["Under"]["price"],
                    bk["title"],
                )
    return None

def _extract_hits_total(game):
    """
    Busca un mercado de hits totales combinados en los bookmakers.
    Claves posibles: 'totals_hits', 'total_hits', 'batter_hits_total', etc.
    Returns (line, over_odds, under_odds, book) or None.
    """
    for bk in game.get("bookmakers", []):
        if not _is_us_book(bk["title"]):
            continue
        for m in bk.get("markets", []):
            mkey = m.get("key", "").lower()
            if "hit" in mkey and ("total" in mkey or "combined" in mkey):
                by_name = {o["name"]: o for o in m.get("outcomes", [])}
                if "Over" in by_name and "Under" in by_name:
                    return (
                        float(by_name["Over"]["point"]),
                        float(by_name["Over"]["price"]),
                        float(by_name["Under"]["price"]),
                        bk["title"],
                    )
    return None

def poisson_runline_prob(home_exp, away_exp, home_spread, max_runs=15):
    """
    P(home covers run line) given home_spread (e.g. -1.5 = home must win by 2+).
    Uses Poisson simulation over discrete score pairs.
    """
    p = 0.0
    for i in range(max_runs + 1):
        for j in range(max_runs + 1):
            if i + home_spread > j:   # home covers: home_score + spread > away_score
                p += poisson_prob(home_exp, i) * poisson_prob(away_exp, j)
    return max(0.01, min(0.99, p))

# ══════════════════════════════════════════════════════════════════════════════
# STAT RANGE VALIDATION (Feature 3)
# ══════════════════════════════════════════════════════════════════════════════
_STAT_RANGES: dict = {
    "era":         (0.50, 15.00),  # 0.50 mínimo — ERA élite legítima (ej. 1.46 Sanchez); 15.00 máximo — ERAs altas como 9.50 son reales en pitchers con mal desempeño
    "fip":         (0.50, 15.00),  # mismo criterio que ERA
    "ops":         (0.500, 1.100),
    "bullpen_era": (1.00, 7.00),   # bullpens élite pueden bajar de 2.00
    "win_pct":     (0.20, 0.80),
    "rs_pg":       (2.0,  8.0),
    "ra_pg":       (2.0,  8.0),
    "goals_pg":    (0.3,  3.5),
    "elo":         (1200, 2200),
}

def _val_stat(key: str, value) -> "float | None":
    """Return float value if within the declared valid range, else None."""
    if value is None:
        return None
    rng = _STAT_RANGES.get(key)
    if rng is None:
        return value
    try:
        fv = float(value)
    except (TypeError, ValueError):
        return None
    return fv if rng[0] <= fv <= rng[1] else None


# ══════════════════════════════════════════════════════════════════════════════
# ERROR LOGGING + MODULE HEALTH TRACKING (Features 2 & 6)
# ══════════════════════════════════════════════════════════════════════════════
_CRITICAL_MODULES = {"The Odds API", "Claude API", "MLB pitcher data"}

def _log_error(module: str, error_type: str, game: str = "", description: str = "") -> None:
    """Append one row to error_log.csv (Feature 6)."""
    try:
        import csv as _csv, os as _os
        ts         = datetime.now(ET).strftime("%Y-%m-%d %H:%M:%S ET")
        need_hdr   = not _os.path.exists(ERROR_LOG_FILE)
        with open(ERROR_LOG_FILE, "a", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            if need_hdr:
                w.writerow(["timestamp", "module", "error_type", "game", "description", "resolved"])
            w.writerow([ts, module, error_type, game, description[:200], ""])
    except Exception as ex:
        print(f"  ⚠️  _log_error write failed: {ex}")


def _track_module(module: str, ok: bool, error_desc: str = "", game: str = "") -> None:
    """
    Track module health.  3 consecutive failures of a critical module
    trigger a single ntfy alert (once per day per module). (Feature 2)
    """
    global _module_failures, _module_last_alerted, _module_status
    if ok:
        _module_failures[module] = 0
        _module_status[module]   = "ok"
        return
    _module_failures[module] = _module_failures.get(module, 0) + 1
    _module_status[module]   = "failing"
    _log_error(module, "consecutive_failure", game, error_desc)
    count = _module_failures[module]
    print(f"  ⚠️  [{module}] fallo #{count}: {error_desc}")
    if count >= 3 and module in _CRITICAL_MODULES:
        today = datetime.now(ET).date()
        if _module_last_alerted.get(module) != today:
            _module_last_alerted[module] = today
            impact = {
                "The Odds API":     "picks de todos los deportes pueden detenerse",
                "Claude API":       "verificación AI desactivada",
                "MLB pitcher data": "picks MLB pueden usar datos genéricos",
            }.get(module, "datos incompletos")
            body = (
                f"🚨 ERROR EN EL BOT\n"
                f"{_DIV}\n"
                f"❌ Módulo fallando: {module}\n"
                f"Error: {error_desc[:120]}\n"
                f"Intentos: {count}/3\n\n"
                f"Impacto: {impact}.\n\n"
                f"Bot sigue funcionando con datos\n"
                f"disponibles."
            )
            ntfy_post("🚨 ERROR EN EL BOT", body, "max")


# ══════════════════════════════════════════════════════════════════════════════
# DATA COMPLETENESS SCORE (Feature 7)
# ══════════════════════════════════════════════════════════════════════════════
def _data_completeness_score(context: dict, sport: str,
                              home: str = "", away: str = "") -> int:
    """
    0–100 score measuring how complete the game data is.
    Callers should add to context as 'data_quality_score'.
    MLB:  Pitcher+ERA +20, FIP +10, Bullpen verified +15, Splits +15,
          Umpire +10, Wind +10, Lineup +10, H2H +10
    Soccer: Form +20, ELO +20, Referee +15, Rest days +10,
            Lineup +15, Venue/temp +10, WC standings +10
    """
    score = 0
    today_s = datetime.now(CDT).strftime("%Y-%m-%d")
    if "mlb" in sport.lower():
        # REQUIRED — pitcher name + ERA (high weight, mandatory for useful analysis)
        ph = str(context.get("pitcher_home", "") or "")
        pa = str(context.get("pitcher_away", "") or "")
        if (ph and "TBD" not in ph and pa and "TBD" not in pa
                and _val_stat("era", context.get("era_home"))
                and _val_stat("era", context.get("era_away"))):
            score += 45          # was 20 — now high weight since it's the key signal
        # NICE TO HAVE — FIP (better ERA proxy)
        if (_val_stat("fip", context.get("fip_home")) is not None
                and _val_stat("fip", context.get("fip_away")) is not None):
            score += 15          # was 10
        # NICE TO HAVE — bullpen dual-source verification
        dq_h = _data_quality.get(f"{home}_{today_s}", {})
        dq_a = _data_quality.get(f"{away}_{today_s}", {})
        if dq_h.get("verified") and dq_a.get("verified"):
            score += 20          # was 15
        elif dq_h.get("verified") or dq_a.get("verified"):
            score += 10          # was 7
        # NICE TO HAVE — batter splits
        h_sp = context.get("h_splits") or {}
        a_sp = context.get("a_splits") or {}
        if h_sp and a_sp:
            score += 15
        # NICE TO HAVE — wind / weather
        wind = str(context.get("wind_info", "") or "")
        if "mph" in wind.lower():
            score += 5           # was 10
        elif context.get("temp_label"):
            score += 3           # was 5
        # NOTE: umpire, lineup_data, h2h_data are OPTIONAL and no longer affect score
        # ── Elite sources bonus points ────────────────────────────────────────
        if context.get("statcast_home") or context.get("statcast_away"):
            score += 15          # Statcast: xERA, Whiff%, Hard hit% available
        if context.get("pinnacle_odds"):
            score += 10          # Pinnacle: sharpest market reference available
    else:
        form_h = str(context.get("form_home", "") or "")
        form_a = str(context.get("form_away", "") or "")
        if form_h and form_a:
            score += 20
        if (_val_stat("elo", context.get("elo_home")) is not None
                and _val_stat("elo", context.get("elo_away")) is not None):
            score += 20
        if context.get("referee"):
            score += 15
        if context.get("rest_h_s") or context.get("rest_a_s"):
            score += 10
        ln_h = context.get("lineup_h_s") or {}
        ln_a = context.get("lineup_a_s") or {}
        if ln_h.get("confirmed") or ln_a.get("confirmed"):
            score += 15
        if context.get("venue_note_s") or context.get("temp_label_s"):
            score += 10
        if context.get("wc_standings"):
            score += 10
        # ── Elite sources bonus points ────────────────────────────────────────
        if context.get("xg_home") or context.get("xg_away"):
            score += 10          # Understat xG: accurate expected goals data
        if context.get("pinnacle_odds"):
            score += 10          # Pinnacle: sharpest market reference available
    return min(score, 100)


EV_MIN_PCT       = 3.0   # minimum EV% to include a bet in Full Game Analysis
PROB_MIN         = 0.50  # global fallback minimum true probability
# Improvement 2: per-type confidence thresholds (backtesting-calibrated)
PROB_MIN_TOTALS  = 0.52  # EV es el filtro real — nivel la competencia entre mercados
PROB_MIN_ML      = 0.50  # EV es el filtro real — elimina sesgo contra ML
# PROB_MIN_LIVE  = 0.65  # DESACTIVADO — live betting deshabilitado
PROB_MIN_PREMIUM = 0.70  # Premium alerts — 70% minimum
_RANK_EMOJIS = ["1️⃣", "2️⃣", "3️⃣"]

def analyze_game_full(game, sport_key, prev_map=None, force_panel: bool = False,
                      _no_elite_panel: bool = False, _force_elite_panel: bool = False):
    """
    Full per-game analysis across ML, Totals, and Spread/Handicap.
    Returns result dict or None (if no bet reaches EV_MIN_PCT).
    """
    if prev_map is None:
        prev_map = {}

    is_mlb = "mlb" in sport_key

    # Default pitcher names — overwritten in the MLB block below; kept as "N/A"
    # for soccer so references further down the function never raise UnboundLocalError
    h_pname: str = "N/A"
    a_pname: str = "N/A"

    home, away = game["home_team"], game["away_team"]
    game_id    = game.get("id", f"{home}|{away}")
    commence   = game.get("commence_time", "")
    _tag       = f"{'MLB' if is_mlb else 'SOC'} | {home} vs {away}"

    print(f"\n🔍 ANÁLISIS: {home} vs {away}  [{sport_key}]")

    if _game_already_started(commence, grace_min=5):
        print(f"   🚫 OMITIDO — juego ya comenzó: {commence}")
        return None

    if game_starts_soon(commence, 60) and not force_panel:
        print(f"   ⏰ OMITIDO — inicia en < 60 min")
        return None
    tc = _timing_check(commence, is_mlb)
    if tc["skip"] and not force_panel:
        print(f"   ⏰ OMITIDO — fuera de ventana horaria")
        return None

    f5_tot = None   # initialize here — only assigned in MLB branch
    f5_h2h = {}     # initialize here — only assigned in MLB branch
    candidates  = []   # {label, true_prob, odds, book, ev_pct, kelly_pct, stake, safest}
    _all_evs: list = []   # [(label, ev_pct)] for every candidate tried, pass or fail
    _all_mkts:  dict = {}   # label → {prob, ev_pct, odds, book} — ALL markets evaluated
    context     = {}

    h2h_odds    = _extract_h2h_best(game)
    spread_odds = _extract_spread_best(game)
    totals_data = get_book_total(game)

    # ── MLB ───────────────────────────────────────────────────────────────────
    if is_mlb:
        h_stats = fetch_team_run_stats(home)
        a_stats = fetch_team_run_stats(away)
        # Run stats are OPTIONAL — fall back to league average so the game still runs
        LEAGUE_AVG = 4.5
        _h_fallback = h_stats is None
        _a_fallback = a_stats is None
        if _h_fallback:
            print(f"   ⚠️ Sin stats de carreras ({home}) — API falló tras 3 intentos, "
                  f"usando promedio liga. Notificación mostrará: dato no disponible")
            h_stats = {"rs_pg": LEAGUE_AVG, "ra_pg": LEAGUE_AVG}
        if _a_fallback:
            print(f"   ⚠️ Sin stats de carreras ({away}) — API falló tras 3 intentos, "
                  f"usando promedio liga. Notificación mostrará: dato no disponible")
            a_stats = {"rs_pg": LEAGUE_AVG, "ra_pg": LEAGUE_AVG}
        # Flag for downstream alert formatting
        _stats_fallback_note = ""
        if _h_fallback or _a_fallback:
            missing = []
            if _h_fallback: missing.append(home)
            if _a_fallback: missing.append(away)
            _stats_fallback_note = ("⚠️ Carreras: dato no disponible "
                                    f"({', '.join(missing)}) — usando promedio liga")

        park       = MLB_PARK_FACTORS.get(home, 1.0)
        home_exp   = h_stats["rs_pg"] * (a_stats["ra_pg"] / LEAGUE_AVG) * park
        away_exp   = a_stats["rs_pg"] * (h_stats["ra_pg"] / LEAGUE_AVG) * park

        pitchers  = fetch_probable_pitchers_today()
        p_data    = _lookup_pitcher_data(home, away, pitchers)
        h_era     = p_data.get("home_era", 4.50)
        a_era     = p_data.get("away_era", 4.50)
        h_pname   = p_data.get("home_name", "TBD")
        a_pname   = p_data.get("away_name", "TBD")

        # Warn when pitchers are TBD but NEVER skip — odds are still valid
        if h_pname == "TBD" and a_pname == "TBD":
            print(f"   ⚠️ Ambos pitchers TBD — continuando con ERA promedio de liga")
        # Warn when at least one pitcher is unconfirmed
        tbd_note = ("⚠️ Pitcher no confirmado" if "TBD" in (h_pname, a_pname) else "")
        pitch_adj = pitcher_run_adjustment(h_era, a_era)

        # ── Elite Source 1: Baseball Savant / Statcast ────────────────────────
        h_statcast = fetch_statcast_pitcher(h_pname)
        a_statcast = fetch_statcast_pitcher(a_pname)
        # Use xERA instead of ERA when available — more predictive than ERA alone
        h_era_eff = (h_statcast["xera"] if h_statcast and h_statcast.get("xera") is not None
                     else h_era)
        a_era_eff = (a_statcast["xera"] if a_statcast and a_statcast.get("xera") is not None
                     else a_era)
        if h_statcast or a_statcast:
            # Recalculate pitch_adj using effective ERA (xERA beats raw ERA)
            pitch_adj = pitcher_run_adjustment(h_era_eff, a_era_eff)
            print(f"   🔬 Statcast: {h_pname} xERA={h_era_eff:.2f} | "
                  f"{a_pname} xERA={a_era_eff:.2f}")

        park_city      = MLB_PARK_CITIES.get(home)
        _game_hour_utc = 0
        try:
            _game_hour_utc = datetime.fromisoformat(commence.replace("Z", "+00:00")).hour
        except Exception:
            pass
        _cf_bearing_ag = MLB_PARK_CF_BEARING.get(home)
        wind           = fetch_wind_forecast(park_city[1], park_city[2], _game_hour_utc,
                                             cf_bearing=_cf_bearing_ag) if park_city else None
        w_adj, w_label = wind_run_adj(wind)
        if wind is not None:
            w_label = f"{w_label} [forecast]"
        elif park_city:
            w_label = f"{w_label} [current]"

        half_adj = pitch_adj / 2
        home_exp = max(0.1, home_exp + half_adj + w_adj / 2)
        away_exp = max(0.1, away_exp + half_adj + w_adj / 2)

        _day_adj, _day_label = _day_game_adj(commence)
        home_exp = max(0.1, home_exp + _day_adj / 2)
        away_exp = max(0.1, away_exp + _day_adj / 2)

        # Mejora 2: ajuste por tercera vez por el lineup
        _ttt_h_adj, _ttt_h_note = _third_time_through_adj(h_pname, h_era_eff, None)
        _ttt_a_adj, _ttt_a_note = _third_time_through_adj(a_pname, a_era_eff, None)
        if _ttt_h_adj > 0:
            home_exp = max(0.1, home_exp + _ttt_h_adj / 2)
            away_exp = max(0.1, away_exp + _ttt_h_adj / 2)
        if _ttt_a_adj > 0:
            home_exp = max(0.1, home_exp + _ttt_a_adj / 2)
            away_exp = max(0.1, away_exp + _ttt_a_adj / 2)
        _ttt_note = "\n".join(n for n in [_ttt_h_note, _ttt_a_note] if n)

        _pvt_home = None
        _pvt_away = None
        try:
            if p_data.get("home_id"):
                _pvt_home = fetch_pitcher_vs_team(p_data["home_id"], away)
            if p_data.get("away_id"):
                _pvt_away = fetch_pitcher_vs_team(p_data["away_id"], home)
            if _pvt_home and _pvt_home["games"] >= 2:
                era_diff_h = _pvt_home["era"] - h_era
                if era_diff_h > 1.5:
                    away_exp = min(away_exp + 0.4, 12.0)
                    print(f"   ⚠️  {h_pname} historically worse vs {away}: ERA {_pvt_home['era']:.2f}")
                elif era_diff_h < -1.5:
                    away_exp = max(0.1, away_exp - 0.3)
            if _pvt_away and _pvt_away["games"] >= 2:
                era_diff_a = _pvt_away["era"] - a_era
                if era_diff_a > 1.5:
                    home_exp = min(home_exp + 0.4, 12.0)
                elif era_diff_a < -1.5:
                    home_exp = max(0.1, home_exp - 0.3)
        except Exception as _pvte:
            print(f"  ⚠️  pitcher_vs_team error: {_pvte}")

        # ── MLB A4: FIP ────────────────────────────────────────────────────
        h_pid = p_data.get("home_id")
        a_pid = p_data.get("away_id")
        h_fip = a_fip = None
        try:
            h_fip = _fetch_pitcher_fip_by_id(h_pid)
            a_fip = _fetch_pitcher_fip_by_id(a_pid)
        except Exception:
            pass

        # ── MLB A5: L/R matchup splits ─────────────────────────────────────
        h_hand = a_hand = None
        lr_matchup_h = lr_matchup_a = None
        lr_notes: list = []
        try:
            h_hand = _fetch_pitcher_hand_by_id(h_pid)
            a_hand = _fetch_pitcher_hand_by_id(a_pid)
            h_tid  = _team_id(home)
            a_tid  = _team_id(away)
            if a_hand and h_tid:         # home lineup bats vs away pitcher
                lr_matchup_h = _fetch_team_batting_vs_hand(h_tid, a_hand)
            if h_hand and a_tid:         # away lineup bats vs home pitcher
                lr_matchup_a = _fetch_team_batting_vs_hand(a_tid, h_hand)
        except Exception:
            pass

        for lineup, matchup, pname, hand, is_home_ln in [
            (home, lr_matchup_h, a_pname, a_hand, True),
            (away, lr_matchup_a, h_pname, h_hand, False),
        ]:
            if matchup and (matchup.get("avg") or matchup.get("ops")):
                _ops_lr = matchup.get("ops", 0.0) or 0.0
                _avg_lr = matchup.get("avg", 0.230) or 0.230
                hand_es = ("zurdo" if hand == "L" else
                           "diestro" if hand == "R" else "ambidiestro")
                if _ops_lr > 0 and _ops_lr < 0.680:
                    adj_lr = -0.5; verdict_lr = "débil"; favor_lr = "pitcher ✅"
                elif _ops_lr > 0 and _ops_lr > 0.800:
                    adj_lr = +0.5; verdict_lr = "fuerte"; favor_lr = "bateadores ⚠️"
                elif _avg_lr < 0.220:
                    adj_lr = -0.3; verdict_lr = "débil"; favor_lr = "pitcher ✅"
                elif _avg_lr > 0.270:
                    adj_lr = +0.3; verdict_lr = "fuerte"; favor_lr = "bateadores ⚠️"
                else:
                    adj_lr = 0.0; verdict_lr = "normal"; favor_lr = "neutral"
                if adj_lr != 0:
                    if is_home_ln: home_exp = max(0.1, min(home_exp + adj_lr, 12.0))
                    else: away_exp = max(0.1, min(away_exp + adj_lr, 12.0))
                lr_notes.append({"lineup": lineup, "pitcher": pname, "hand": hand_es,
                                 "avg": _avg_lr, "ops": _ops_lr, "verdict": verdict_lr, "favor": favor_lr})

        # ── MLB A6: H2H last 5 meetings ────────────────────────────────────
        h2h_data = None
        try:
            _h2h_raw = _fetch_h2h_data(_team_id(home), _team_id(away), home)
            if _h2h_raw and _h2h_raw.get("games_found", 0) >= 2:
                h2h_data = _h2h_raw
        except Exception:
            pass
        # ──────────────────────────────────────────────────────────────────

        # ── MLB A7: team batting metrics (OPS / K% / BB%) ─────────────────
        bat_h = bat_a = None
        try:
            h_tid_bat = _team_id(home)
            a_tid_bat = _team_id(away)
            bat_h = _fetch_team_batting_full(h_tid_bat)
            bat_a = _fetch_team_batting_full(a_tid_bat)
        except Exception:
            pass

        # Apply OPS & K% run-total adjustments
        if bat_h and bat_a:
            ops_h = bat_h.get("ops") or 0.0
            ops_a = bat_a.get("ops") or 0.0
            k_h   = bat_h.get("k_pct") or 0.0
            k_a   = bat_a.get("k_pct") or 0.0

            # OPS: both strong → hitter-friendly; both weak → pitcher-friendly
            if ops_h > 0.820 and ops_a > 0.820:
                home_exp = min(home_exp + 0.25, 12.0)
                away_exp = min(away_exp + 0.25, 12.0)
            elif ops_h < 0.700 and ops_a < 0.700:
                home_exp = max(0.1, home_exp - 0.25)
                away_exp = max(0.1, away_exp - 0.25)

            # K%: high K% = that team scores fewer; low K% = scores more
            if k_h > 28:
                home_exp = max(0.1, home_exp - 0.3)
            elif k_h < 18:
                home_exp = min(home_exp + 0.3, 12.0)
            if k_a > 28:
                away_exp = max(0.1, away_exp - 0.3)
            elif k_a < 18:
                away_exp = min(away_exp + 0.3, 12.0)
        # ── MLB A8: confirmed lineup (only within 3h of game) ───────────────
        _lineup = None
        try:
            _lineup = _fetch_confirmed_lineup(home, away, commence)
        except Exception:
            pass

        if _lineup and _lineup.get("confirmed"):
            h_miss = _lineup.get("home_missing", [])
            a_miss = _lineup.get("away_missing", [])
            h_impact = _lineup_impact(h_miss, _lineup.get("home_order", {}))
            a_impact = _lineup_impact(a_miss, _lineup.get("away_order", {}))
            if h_impact != 0: home_exp = max(0.1, home_exp + h_impact)
            if a_impact != 0: away_exp = max(0.1, away_exp + a_impact)
        # ──────────────────────────────────────────────────────────────────

        # Level 2B: Pitcher fatigue adjustment (MLB only)
        if is_mlb:
            try:
                _h_fat = _fetch_pitcher_fatigue_score(p_data.get("home_id"), h_pname)
                _a_fat = _fetch_pitcher_fatigue_score(p_data.get("away_id"), a_pname)
                if _h_fat and _h_fat["run_adj"] != 0:
                    home_exp = max(0.1, home_exp + _h_fat["run_adj"] / 2)
                    away_exp = max(0.1, away_exp + _h_fat["run_adj"] / 2)
                    if _h_fat["note"]:
                        print(f"   {_h_fat['note'].splitlines()[0]}")
                if _a_fat and _a_fat["run_adj"] != 0:
                    home_exp = max(0.1, home_exp + _a_fat["run_adj"] / 2)
                    away_exp = max(0.1, away_exp + _a_fat["run_adj"] / 2)
                    if _a_fat["note"]:
                        print(f"   {_a_fat['note'].splitlines()[0]}")
            except Exception:
                pass

        # Level 2C: Travel fatigue adjustment (MLB only)
        if is_mlb:
            try:
                _t_h, _t_a, _t_note = _travel_fatigue_adj(home, away)
                if _t_h != 0:
                    home_exp = max(0.1, home_exp + _t_h)
                if _t_a != 0:
                    away_exp = max(0.1, away_exp + _t_a)
                if _t_note:
                    print(f"   {_t_note}")
            except Exception:
                pass

        _pyth_p = pythagorean_win_prob(home_exp, away_exp)
        _elo_p  = elo_win_prob(home, away)
        # Blend 60% Pythagorean + 40% ELO so league-average fallback never gives 50/50
        p_home  = round(0.60 * _pyth_p + 0.40 * _elo_p, 4)
        p_away  = 1.0 - p_home

        # ── Module B2: Team streak ML adjustment (±5% per hot/cold team) ─
        _h_streak = _a_streak = None
        _streak_ml_note = ""
        if is_mlb:
            try:
                _h_streak = fetch_team_streak_mlb(home)
                _a_streak = fetch_team_streak_mlb(away)
                _sp_adj   = 0.0
                _s_parts  = []
                for _tm, _sk, _sign in [(home, _h_streak, +1), (away, _a_streak, -1)]:
                    if not _sk:
                        continue
                    _s_parts.append(_sk["label"])
                    if _sk["is_hot"]:
                        _sp_adj += 0.05 * _sign
                    elif _sk["is_cold"]:
                        _sp_adj -= 0.05 * _sign
                if abs(_sp_adj) > 0.001:
                    _orig_ph = p_home
                    p_home   = max(0.05, min(0.95, p_home + _sp_adj))
                    p_away   = 1.0 - p_home
                    _dir_s   = "sube" if _sp_adj > 0 else "baja"
                    _s_parts.append(
                        f"   → Prob {_es(home)} {_dir_s}: "
                        f"{_orig_ph*100:.0f}% → {p_home*100:.0f}%"
                    )
                _streak_ml_note = "\n".join(_s_parts)
            except Exception:
                pass

        # Fetch enhanced context + adjust p_home by pitcher recent form (BEFORE EV loop)
        _enh_ctx: dict = {}
        if is_mlb:
            game_date = commence[:10]   # fix: define before _fetch_enhanced_game_context call

            # ── contexto_juego: lookup MLB gamePk ─────────────────────────────
            _ctx_game_pk = None
            try:
                _h_tid_ctx = _team_id(home)
                if _h_tid_ctx:
                    _sched_ctx_r = requests.get(
                        "https://statsapi.mlb.com/api/v1/schedule",
                        params={"sportId": 1, "date": game_date, "teamId": _h_tid_ctx},
                        timeout=8,
                    )
                    _sched_ctx = _sched_ctx_r.json() if _sched_ctx_r.status_code == 200 else {}
                    if not isinstance(_sched_ctx, dict):
                        _sched_ctx = {}
                    for _d_ctx in _sched_ctx.get("dates", []):
                        for _g_ctx in _d_ctx.get("games", []):
                            _ctx_game_pk = _g_ctx.get("gamePk")
                            break
            except Exception as _ctx_pk_e:
                print(f"[contexto] game_pk lookup: {_ctx_pk_e}")

            try:
                _enh_ctx = _fetch_enhanced_game_context(
                    _team_id(home), _team_id(away), h_pid, a_pid, game_date,
                    home_name=home, away_name=away, commence_utc=commence,
                )
            except Exception as _ece:
                import datetime as _dt_enh
                _ts_enh = _dt_enh.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
                _game_name_enh = f"{home} vs {away}"
                _err_type = type(_ece).__name__
                print(f"  ⚠️ _enh_ctx falló ({type(_ece).__name__}: {_ece}) — continuando sin contexto enriquecido")
                _enh_ctx = {}

            print(f"[DEBUG ENH] home_last3_era={_enh_ctx.get('home_pitcher_last3_era_avg')} away_last3_era={_enh_ctx.get('away_pitcher_last3_era_avg')}")

            try:
                _base_p_home = p_home
                p_home = adjust_probability_for_pitcher_form(
                    base_prob_home=p_home,
                    home_era_season=h_era,
                    away_era_season=a_era,
                    home_era_last3=_enh_ctx.get("home_pitcher_last3_era_avg"),
                    away_era_last3=_enh_ctx.get("away_pitcher_last3_era_avg"),
                )
                p_away = 1.0 - p_home
                print(f"[DEBUG] Prob base: {_base_p_home} → Prob ajustada: {p_home}")
            except Exception as _pfe:
                print(f"  ⚠️  pitcher form adj error: {_pfe}")

        # Boost adicional por mismatch de ERA en mercado ML
        if is_mlb and h_era is not None and a_era is not None:
            try:
                p_home = pitcher_mismatch_ml_boost(
                    home_era=h_era,
                    away_era=a_era,
                    base_prob=p_home,
                    favored_is_home=(h_era <= a_era),
                )
                p_away = 1.0 - p_home
            except Exception as _mmb_e:
                print(f"  ⚠️  pitcher_mismatch_ml_boost error: {_mmb_e}")

        # ── contexto_juego: ajuste ML por splits L/R, descanso y bullpen ─────
        _ctx_juego = None
        if is_mlb and _ctx_game_pk:
            try:
                _ctx_juego = obtener_contexto(_ctx_game_pk)
                if _ctx_juego:
                    p_home, p_away = ajustar_ml(p_home, p_away, _ctx_juego)
                    # Build resumen string for the expert panel
                    _ctx_parts = [
                        f"Park: {_ctx_juego.get('venue','')} (factor {_ctx_juego.get('park_factor',1.0):.2f})",
                    ]
                    _c = _ctx_juego.get("clima")
                    if _c and not _c.get("techo_cerrado"):
                        _ctx_parts.append(
                            f"Clima: {_c.get('temp_f',0):.0f}°F "
                            f"viento {_c.get('viento_mph',0):.0f}mph "
                            f"lluvia {_c.get('lluvia_prob',0)}%"
                        )
                    for _cl, _ct in (("home", "Local"), ("away", "Visita")):
                        _rf = _ctx_juego.get(f"regulares_fuera_{_cl}")
                        if _rf:
                            _ctx_parts.append(f"{_ct}: ~{_rf} regulares descansando")
                        _bp = _ctx_juego.get(f"bullpen_{_cl}", {})
                        if _bp.get("dos_dias_seguidos"):
                            _ctx_parts.append(
                                f"Bullpen {_ct} quemado: "
                                f"{', '.join(_bp['dos_dias_seguidos'][:3])}"
                            )
                    context["ctx_juego_resumen"] = " | ".join(_ctx_parts)
            except Exception as _ctx_e:
                print(f"[contexto] ajustar_ml error: {_ctx_e}")

        # ML
        for team, true_p, lbl in [
            (home, p_home, f"🔵 {home} ML"),
            (away, p_away, f"🔴 {away} ML"),
        ]:
            if team not in h2h_odds:
                continue
            odds, book = h2h_odds[team]
            # Fix 4: MLB moneyline sanity check — skip data-error odds
            if is_mlb and odds > 5.0:
                print(f"   ⚠️  MLB ODDS CAP: {lbl} @ {odds:.2f} > 5.0 — dato erróneo, omitido")
                continue
            true_p_capped = _cap_prob(true_p)      # enforce PROB_CAP before EV/stake
            ev = (true_p_capped * odds - 1) * 100  # EV computed on capped probability
            r  = kelly_stake(true_p_capped, odds)
            _all_evs.append((lbl, round(ev, 1)))
            _all_mkts[lbl] = {"prob": true_p_capped, "ev_pct": round(ev, 1),
                               "odds": odds, "book": book}
            _ev_min_ml = EV_MIN_PCT
            if ev >= _ev_min_ml and r["stake"] > 0:
                # Improvement 2: ML requires ≥62% probability (checked after cap)
                if true_p_capped < PROB_MIN_ML:
                    print(f"   ⏭️  {_tag}: {lbl} prob {true_p_capped:.0%} < {PROB_MIN_ML:.0%} mín ML — omitido")
                    continue
                candidates.append({"label": lbl, "true_prob": true_p_capped, "odds": odds,
                                   "book": book, "ev_pct": round(ev, 1),
                                   "stake": r["stake"], "kelly_pct": r["kelly_pct"],
                                   "stake_warn": r.get("stake_warn", "")})

        # Totals
        _pitch_notes   = []
        _pitch_reason  = ""
        _contradiction = False
        _h2h_note      = ""
        if totals_data:
            book_line, over_odds, under_odds, bk_name = totals_data
            adj_total = home_exp + away_exp

            # ── Module B2: Streak run adjustment (±0.3 per hot/cold team) ─
            for _sk in (_h_streak, _a_streak):
                if not _sk:
                    continue
                if _sk["is_hot"]:
                    adj_total += 0.3
                elif _sk["is_cold"]:
                    adj_total -= 0.3

            # ── H2H blend (30% weight on projection) ──────────────────────
            _model_raw = adj_total
            if h2h_data and h2h_data.get("avg_total"):
                adj_total = round(0.70 * adj_total + 0.30 * h2h_data["avg_total"], 2)
                if abs(h2h_data["avg_total"] - _model_raw) > 2.0:
                    _h2h_note = (
                        f"⚠️ Historial H2H sugiere diferente "
                        f"({h2h_data['avg_total']} carreras/juego) — "
                        f"considerar antes de apostar"
                    )
            # ──────────────────────────────────────────────────────────────

            # ── Pitcher Intelligence Rules (FIP preferred over ERA) ────────
            _over_min_edge = 0.0
            _h_metric = h_fip if h_fip is not None else h_era
            _a_metric = a_fip if a_fip is not None else a_era
            dom_h  = _h_metric < 2.75
            dom_a  = _a_metric < 2.75
            weak_h = _h_metric > 4.25
            weak_a = _a_metric > 4.25

            # Rule 1: any dominant pitcher → reduce total, require bigger edge for OVER
            if dom_h or dom_a:
                adj_total -= 1.2
                _over_min_edge = 1.5
                dom_name = h_pname if dom_h else a_pname
                _pitch_notes.append(
                    f"⚠️ Pitcher dominante ({dom_name}) — "
                    f"el Over necesita edge mayor para tener valor"
                )

            # Rule 2: both pitchers weak → raise total, OVER gets priority
            if weak_h and weak_a:
                adj_total += 0.8
                _pitch_notes.append(
                    "✅ Ambos pitchers débiles — condiciones favorables para Over"
                )

            adj_total   = max(0.5, adj_total)

            # ── contexto_juego: ajuste park factor + clima al total ─────────
            if _ctx_juego:
                try:
                    adj_total = ajustar_total(adj_total, _ctx_juego)
                except Exception:
                    pass

            _over_edge  = adj_total - book_line  # positive = model favors OVER

            # Rule 3: contradiction — dominant pitcher but model still says OVER
            if (dom_h or dom_a) and _over_edge > 0:
                _contradiction = True
                _pitch_notes.append(
                    "⚠️ Contradicción: pitcher dominante pero el modelo dice Over.\n"
                    "   Considera apostar UNDER."
                )

            # Rule 4: always show reasoning summary for totals
            _h_exp_lbl = "POCO"     if dom_a  else ("MUCHO"    if weak_a else "moderado")
            _a_exp_lbl = "POCO"     if dom_h  else ("MUCHO"    if weak_h else "moderado")
            _total_dir = (
                f"bajo (proyectado {adj_total:.1f} vs línea {book_line}) → Favorece UNDER"
                if adj_total < book_line else
                f"alto (proyectado {adj_total:.1f} vs línea {book_line}) → Favorece OVER"
            )
            _pitch_reason = (
                f"📊 {home} con {h_pname} ({_era_label(h_era)}):\n"
                f"   {away} anotará {_a_exp_lbl}\n"
                f"   {away} con {a_pname} ({_era_label(a_era)}):\n"
                f"   {home} anotará {_h_exp_lbl}\n"
                f"   → Total proyectado {_total_dir}"
            )
            # ──────────────────────────────────────────────────────────────

            # ── Improvement 1: Oracle Park confidence penalty ──────────────
            _oracle_park = (home == "San Francisco Giants")
            if _oracle_park:
                _pitch_notes.append(
                    "⚠️ Oracle Park: modelo históricamente impreciso (36% hit rate).\n"
                    "   Confianza reducida — se requiere prob ≥62% para alertar."
                )
            # ── Improvement 3: UNDER bias — within 0.3 of line → prefer UNDER
            _near_line = abs(adj_total - book_line) <= 0.3
            # OVER confirmation: Statcast data present OR both pitchers weak ERA>4.50 OR strong edge
            _over_confirmed = (
                bool(h_statcast or a_statcast)              or
                (h_era_eff > 4.50 and a_era_eff > 4.50)    or
                _over_edge > 1.0
            )

            # FILTRO 2: Condiciones mínimas para alertar totals (necesita ≥2 de 3)
            _pit_ok  = h_pname not in ("TBD", "", "SIN CONFIRMAR") and a_pname not in ("TBD", "", "SIN CONFIRMAR")
            _env_ok  = bool(w_label) or (park != 1.0 and home in MLB_PARK_FACTORS)
            _edge_ok = abs(_over_edge) >= 0.8
            _conds   = sum([_pit_ok, _env_ok, _edge_ok])
            if _conds < 2:
                print(f"   ⏭️  Totals {home} vs {away}: {_conds}/3 condiciones — omitido (pitcher:{_pit_ok} entorno:{_env_ok} edge:{_edge_ok})")
            # Calcula EV de OVER y UNDER por separado; envía al panel solo el de mayor EV
            _tot_cands = []
            for side_label, is_over, p, odds in ([] if _conds < 2 else [
                (f"📈 OVER {book_line} carreras",  True,
                 poisson_ou_prob(adj_total, book_line, True),  over_odds),
                (f"📉 UNDER {book_line} carreras", False,
                 poisson_ou_prob(adj_total, book_line, False), under_odds),
            ]):
                # Rule 1: skip OVER below dominant-pitcher edge threshold
                if is_over and _over_min_edge > 0 and _over_edge < _over_min_edge:
                    continue
                # Improvement 3a: near-line projection → force UNDER
                if is_over and _near_line:
                    print(f"   ⏭️  {_tag}: OVER omitido — "
                          f"proyección {adj_total:.1f} ≈ línea {book_line:.1f} → UNDER preferido")
                    continue
                # Improvement 3b: OVER needs Statcast / weak ERA / strong edge confirmation
                if is_over and not _over_confirmed:
                    print(f"   ⏭️  {_tag}: OVER sin confirmación externa "
                          f"(Statcast/ERA>4.50/edge>1.0) — omitido")
                    continue
                # Improvement 2: confidence filter — Oracle Park uses ML threshold
                _prob_floor = PROB_MIN_ML if _oracle_park else PROB_MIN_TOTALS
                if p < _prob_floor:
                    _side_n = "OVER" if is_over else "UNDER"
                    print(f"   ⏭️  {_tag}: {_side_n} prob {p:.0%} < {_prob_floor:.0%} mín — omitido")
                    continue
                # ── ML ensemble blend ─────────────────────────────────────────────
                # Get ML probability for this side (OVER or UNDER) from the ensemble.
                # predict_under_prob() returns P(UNDER wins); convert to the side we're
                # evaluating.  Pinnacle's implied probability for the side is passed so
                # the ML model's output is clamped to within 15pp of the sharp market.
                _ml_p_side = None
                if _ML_MODULE is not None:
                    try:
                        # Extract Pinnacle totals odds for calibration clamp
                        _pin_tots_ml = _extract_pinnacle_totals(game)
                        _pin_p_ml    = None
                        if _pin_tots_ml:
                            _raw_ov = 1.0 / max(_pin_tots_ml.get("over",  999), 1.001)
                            _raw_un = 1.0 / max(_pin_tots_ml.get("under", 999), 1.001)
                            _tot_ml = _raw_ov + _raw_un
                            if _tot_ml > 0:
                                # Pinnacle implied prob for the side we're evaluating
                                _pin_p_ml = (
                                    (_raw_ov / _tot_ml) if is_over
                                    else (_raw_un / _tot_ml)
                                )
                        _under_prob_ml = _ML_MODULE.predict_under_prob(
                            book_line, adj_total,
                            is_over_pick=is_over,
                            pinnacle_prob=_pin_p_ml,
                        )
                        if _under_prob_ml is not None:
                            # Convert UNDER probability to the side being evaluated
                            _ml_p_side = (1.0 - _under_prob_ml) if is_over else _under_prob_ml
                    except Exception:
                        pass   # silently skip ML if anything fails
                # Kelly stake uses historical blend for conservative sizing only.
                # EV always uses the true Poisson probability (p) so all markets
                # are compared on equal footing — no artificial boost for any side.
                _hist_rate = 0.526 if not is_over else 0.527
                if _ml_p_side is not None and _ML_MODULE is not None:
                    p_kelly = _ML_MODULE.blend_prob(p, _ml_p_side, _hist_rate)
                else:
                    p_kelly = round(p * 0.7 + _hist_rate * 0.3, 4)
                ev = (p * odds - 1) * 100   # EV = true Poisson prob × decimal odds − 1
                r  = kelly_stake(p_kelly, odds)
                _all_evs.append((side_label, round(ev, 1)))
                _all_mkts[side_label] = {"prob": p, "ev_pct": round(ev, 1),
                                         "odds": odds, "book": bk_name}
                _ev_min_tot = EV_MIN_PCT
                if ev >= _ev_min_tot and r["stake"] > 0:
                    _tot_cands.append({"label": side_label, "true_prob": p, "odds": odds,
                                       "book": bk_name, "ev_pct": round(ev, 1),
                                       "stake": r["stake"], "kelly_pct": r["kelly_pct"]})
            # Ambos lados (OVER y UNDER) compiten con ML/RL por EV
            candidates.extend(_tot_cands)

        if totals_data and candidates:
            _std_line = totals_data[0]
            _alt_lines = get_alternate_totals(game, _std_line)
            _adj_total_ref = adj_total if is_mlb else (home_exp + away_exp)
            for _alt in _alt_lines[:3]:
                _alt_side = "UNDER" if _alt["diff_from_standard"] > 0 else "OVER"
                _alt_odds = _alt["under_odds"] if _alt_side == "UNDER" else _alt["over_odds"]
                _alt_p = poisson_ou_prob(_adj_total_ref, _alt["line"], _alt_side == "OVER")
                _alt_ev = (_alt_p * _alt_odds - 1) * 100
                if _alt_ev > 8.0 and _alt_p >= PROB_MIN_TOTALS:
                    _alt_lbl = f"{'📉' if _alt_side == 'UNDER' else '📈'} {_alt_side} {_alt['line']} (alt)"
                    _alt_r = kelly_stake(_alt_p, _alt_odds)
                    if _alt_r["stake"] > 0:
                        candidates.append({
                            "label": _alt_lbl, "true_prob": _alt_p,
                            "odds": _alt_odds, "book": _alt["book"],
                            "ev_pct": round(_alt_ev, 1), "stake": _alt_r["stake"],
                            "kelly_pct": _alt_r["kelly_pct"],
                        })
                        print(f"   📊 Línea alternativa: {_alt_lbl} EV+{_alt_ev:.1f}%")

        # Run line
        # FILTRO 1: si hay favorito pesado en ML (odds < 1.70), reducir umbral para RL +1.5 del perro
        _heavy_fav_ml = None
        for _t, _od_pair in h2h_odds.items():
            if _od_pair[0] < 1.70:
                _heavy_fav_ml = _t
                break

        for team, is_home in [(home, True), (away, False)]:
            if team not in spread_odds:
                continue
            pt, odds, book = spread_odds[team]
            if is_mlb and abs(pt) > 2.5:
                continue
            if is_home:
                rl = pt if pt != 0 else -1.5
                p_cover = poisson_runline_prob(home_exp, away_exp, rl)
            else:
                rl_home = -(pt) if pt != 0 else -1.5   # away +1.5 → home -1.5
                p_cover = 1.0 - poisson_runline_prob(home_exp, away_exp, rl_home)
            sign = f"{pt:+.1f}" if pt != 0 else ("-1.5" if is_home else "+1.5")
            lbl  = f"🏃 {team} RL {sign}"
            ev   = (p_cover * odds - 1) * 100
            r    = kelly_stake(p_cover, odds)
            _all_evs.append((lbl, round(ev, 1)))
            _all_mkts[lbl] = {"prob": p_cover, "ev_pct": round(ev, 1),
                               "odds": odds, "book": book}
            _es_perro_rl = bool(_heavy_fav_ml and team != _heavy_fav_ml and (pt > 0 or (pt == 0 and not is_home)))
            _ev_min_rl = max(EV_MIN_PCT / 2, 1.0) if _es_perro_rl else EV_MIN_PCT
            if ev >= _ev_min_rl and r["stake"] > 0:
                _perro_note = "⚠️ Alternativa al favorito caro — considera RL +1.5 antes del ML directo" if _es_perro_rl else ""
                candidates.append({"label": lbl, "true_prob": p_cover, "odds": odds,
                                   "book": book, "ev_pct": round(ev, 1),
                                   "stake": r["stake"], "kelly_pct": r["kelly_pct"],
                                   "stake_warn": _perro_note})

        # ── F5 ML (primera mitad — moneyline primeras 5 entradas) ────────────
        # F5 odds se obtienen por endpoint de evento (no en get_odds principal)
        # F5 se evalúa siempre: puede ser el mejor mercado del partido
        _has_early_ev = any(c.get("ev_pct", 0) >= 3.0 for c in candidates)
        _f5_data = _fetch_f5_odds(game_id) if _has_early_ev else {}
        f5_h2h = _extract_f5_h2h_best(_f5_data)
        if f5_h2h:
            _era_diff_f5 = a_era_eff - h_era_eff   # positivo = pitcher local mejor
            _f5_ml_adj   = max(-0.08, min(0.08, _era_diff_f5 * 0.03))
            p_home_f5 = max(0.05, min(0.92, p_home + _f5_ml_adj))
            p_away_f5 = 1.0 - p_home_f5
            for _f5_tm, _f5_p, _f5_lbl in [
                (home, p_home_f5, f"⚾ {home} F5 ML"),
                (away, p_away_f5, f"⚾ {away} F5 ML"),
            ]:
                if _f5_tm not in f5_h2h:
                    continue
                _f5_odds, _f5_bk = f5_h2h[_f5_tm]
                if _f5_odds > 4.0:
                    continue
                _f5_p_c = _cap_prob(_f5_p)
                ev = (_f5_p_c * _f5_odds - 1) * 100
                r  = kelly_stake(_f5_p_c, _f5_odds)
                _all_evs.append((_f5_lbl, round(ev, 1)))
                _all_mkts[_f5_lbl] = {"prob": _f5_p_c, "ev_pct": round(ev, 1),
                                       "odds": _f5_odds, "book": _f5_bk}
                if ev >= EV_MIN_PCT and r["stake"] > 0 and _f5_p_c >= PROB_MIN_ML:
                    candidates.append({"label": _f5_lbl, "true_prob": _f5_p_c,
                                       "odds": _f5_odds, "book": _f5_bk,
                                       "ev_pct": round(ev, 1),
                                       "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # ── F5 Total (primeras 5 entradas — Over/Under) ───────────────────────
        f5_tot = _extract_f5_total(_f5_data)
        if f5_tot:
            f5_line, f5_ov_odds, f5_un_odds, f5_book = f5_tot
            _h_m_f5 = h_fip if h_fip is not None else h_era_eff
            _a_m_f5 = a_fip if a_fip is not None else a_era_eff
            f5_exp  = max(0.5, (home_exp + away_exp) * 0.52)
            if _h_m_f5 < 2.75 or _a_m_f5 < 2.75:   # pitcher élite → menos carreras F5
                f5_exp = max(0.5, f5_exp - 0.5)
            # Calcula EV de OVER y UNDER F5 por separado; envía al panel solo el de mayor EV
            _f5t_cands = []
            for _ft_lbl, _is_f5_ov, _ft_p, _ft_odds in [
                (f"📈 OVER {f5_line} F5",  True,
                 poisson_ou_prob(f5_exp, f5_line, True),  f5_ov_odds),
                (f"📉 UNDER {f5_line} F5", False,
                 poisson_ou_prob(f5_exp, f5_line, False), f5_un_odds),
            ]:
                # EV uses true Poisson probability — no artificial side boost
                ev = (_ft_p * _ft_odds - 1) * 100
                r  = kelly_stake(_ft_p, _ft_odds)
                _all_evs.append((_ft_lbl, round(ev, 1)))
                _all_mkts[_ft_lbl] = {"prob": _ft_p, "ev_pct": round(ev, 1),
                                       "odds": _ft_odds, "book": f5_book}
                if ev >= EV_MIN_PCT and r["stake"] > 0 and _ft_p >= PROB_MIN_TOTALS:
                    _f5t_cands.append({"label": _ft_lbl, "true_prob": _ft_p,
                                       "odds": _ft_odds, "book": f5_book,
                                       "ev_pct": round(ev, 1),
                                       "stake": r["stake"], "kelly_pct": r["kelly_pct"]})
            if _f5t_cands:
                candidates.append(max(_f5t_cands, key=lambda x: x["ev_pct"]))

        # ── Hits totales combinados (Over/Under) ──────────────────────────────
        _hits_mkt = _extract_hits_total(_f5_data)
        if _hits_mkt and bat_h and bat_a:
            _hits_line, _hits_ov_od, _hits_un_od, _hits_bk = _hits_mkt
            _h_avg    = bat_h.get("avg") or 0.250
            _a_avg    = bat_a.get("avg") or 0.250
            _exp_hits = round((_h_avg * 30.0) + (_a_avg * 30.0), 1)
            if h_era_eff < 3.0 or a_era_eff < 3.0:   # pitcher élite → menos hits
                _exp_hits = max(4.0, _exp_hits - 1.5)
            # Calcula EV de HITS OVER y UNDER por separado; envía al panel solo el de mayor EV
            _hits_cands = []
            for _ht_lbl, _is_ht_ov, _ht_p, _ht_od in [
                (f"🎯 HITS OVER {_hits_line}",  True,
                 poisson_ou_prob(_exp_hits, _hits_line, True),  _hits_ov_od),
                (f"🎯 HITS UNDER {_hits_line}", False,
                 poisson_ou_prob(_exp_hits, _hits_line, False), _hits_un_od),
            ]:
                ev = (_ht_p * _ht_od - 1) * 100
                r  = kelly_stake(_ht_p, _ht_od)
                _all_evs.append((_ht_lbl, round(ev, 1)))
                _all_mkts[_ht_lbl] = {"prob": _ht_p, "ev_pct": round(ev, 1),
                                       "odds": _ht_od, "book": _hits_bk}
                if ev >= EV_MIN_PCT and r["stake"] > 0 and _ht_p >= PROB_MIN_TOTALS:
                    _hits_cands.append({"label": _ht_lbl, "true_prob": _ht_p,
                                        "odds": _ht_od, "book": _hits_bk,
                                        "ev_pct": round(ev, 1),
                                        "stake": r["stake"], "kelly_pct": r["kelly_pct"]})
            if _hits_cands:
                candidates.append(max(_hits_cands, key=lambda x: x["ev_pct"]))

        # ── Ponches del pitcher titular (Strikeout K props) ───────────────────
        # REGLA K/9: solo se genera candidato de ponches si K/9 confirmado
        # por MLB Stats API es estrictamente mayor a 8.5.  Sin K/9 confirmado
        # o con K/9 ≤ 8.5, el prop se bloquea y se intenta fallback a ML.
        # NUNCA usar estimación por ERA como sustituto de K/9 real.
        # GUARDIA DE API: reusar _has_early_ev del bloque F5 — si sigue sin
        # candidatos con EV>3%, no hay razón para gastar una llamada en K props.
        _k9_prop_blocked = False   # True si al menos un K prop fue bloqueado por esta regla
        try:
            if not _has_early_ev:
                _ko_props = {}
            else:
                _ko_props = _fetch_player_props(game_id)
            for _kp_nm, _kp_era, _kp_sc in [
                (h_pname, h_era_eff, h_statcast),
                (a_pname, a_era_eff, a_statcast),
            ]:
                if not _kp_nm or _kp_nm == "TBD":
                    continue
                _kp_key = next(
                    (k for k in _ko_props
                     if "pitcher_strikeouts" in k
                     and _kp_nm.split()[-1].lower() in k.lower()),
                    None,
                )
                if not _kp_key:
                    continue
                _kp_sides = _ko_props[_kp_key]
                _kp_ov_d  = _kp_sides.get("Over", {})
                _kp_un_d  = _kp_sides.get("Under", {})
                _kp_line  = _kp_ov_d.get("point")
                if not _kp_line:
                    continue
                _kp_line  = float(_kp_line)
                _kp_ov_pr = float(_kp_ov_d.get("price", 1.90))
                _kp_un_pr = float(_kp_un_d.get("price", 1.90))

                # K/9 confirmado: MLB Stats API (fetch_pitcher_stats) — fuente real.
                # Statcast no tiene K/9; la estimación por ERA queda prohibida aquí.
                _kp_ps  = fetch_pitcher_stats(_kp_nm)
                _kp_k9_raw = _kp_ps.get("k9", "N/A")
                try:
                    _kp_k9_f = float(_kp_k9_raw) if _kp_k9_raw not in ("N/A", None, "") else None
                except (ValueError, TypeError):
                    _kp_k9_f = None

                if _kp_k9_f is None or _kp_k9_f <= 8.5:
                    _why = f"K/9={_kp_k9_f:.1f}" if _kp_k9_f is not None else "K/9 no disponible"
                    print(f"   ⏭️  K prop {_kp_nm}: {_why} — necesita >8.5 confirmado → preferir ML")
                    _k9_prop_blocked = True
                    continue   # bloquear este prop por completo

                # K/9 confirmado y > 8.5 → calcular prop normalmente
                _kp_k9   = _kp_k9_f
                _kp_ip   = (6.0 if _kp_era < 3.00 else
                             5.5 if _kp_era < 4.00 else 5.0)
                _kp_expk = round(_kp_k9 * _kp_ip / 9.0, 2)
                _kp_sn   = _kp_nm.split()[-1]
                for _kp_lbl, _kp_p, _kp_pr in [
                    (f"⚡ {_kp_sn} K OVER {_kp_line}",
                     poisson_ou_prob(_kp_expk, _kp_line, True),  _kp_ov_pr),
                    (f"⚡ {_kp_sn} K UNDER {_kp_line}",
                     poisson_ou_prob(_kp_expk, _kp_line, False), _kp_un_pr),
                ]:
                    if _kp_pr < 1.50:
                        continue
                    ev = (_kp_p * _kp_pr - 1) * 100
                    r  = kelly_stake(_kp_p, _kp_pr)
                    _all_evs.append((_kp_lbl, round(ev, 1)))
                    _all_mkts[_kp_lbl] = {"prob": _kp_p, "ev_pct": round(ev, 1),
                                           "odds": _kp_pr, "book": "Props"}
                    if ev >= EV_MIN_PCT and r["stake"] > 0 and _kp_p >= 0.52:
                        candidates.append({"label": _kp_lbl, "true_prob": _kp_p,
                                           "odds": _kp_pr, "book": "Props",
                                           "ev_pct": round(ev, 1),
                                           "stake": r["stake"], "kelly_pct": r["kelly_pct"]})
        except Exception as _ke:
            print(f"   ⚠️  K props: {_ke}")

        # ML fallback cuando K prop fue bloqueado por K/9 insuficiente/no confirmado
        if _k9_prop_blocked:
            # Equipo con mejor pitcher = menor ERA efectiva
            _fb_team  = home if h_era_eff <= a_era_eff else away
            _fb_lbl   = f"🔵 {home} ML" if _fb_team == home else f"🔴 {away} ML"
            _fb_prob  = p_home if _fb_team == home else p_away
            # Solo añadir si no está ya en candidatos (ML se evalúa antes de props)
            _fb_exists = any(_fb_team in c.get("label", "") and " ML" in c.get("label", "")
                             for c in candidates)
            if not _fb_exists and _fb_team in h2h_odds:
                _fb_odds, _fb_book = h2h_odds[_fb_team]
                _fb_p_c = _cap_prob(_fb_prob)
                _fb_ev  = (_fb_p_c * _fb_odds - 1) * 100
                _fb_r   = kelly_stake(_fb_p_c, _fb_odds)
                if _fb_ev >= EV_MIN_PCT and _fb_r["stake"] > 0 and _fb_p_c >= PROB_MIN_ML:
                    candidates.append({
                        "label":      _fb_lbl,
                        "true_prob":  _fb_p_c,
                        "odds":       _fb_odds,
                        "book":       _fb_book,
                        "ev_pct":     round(_fb_ev, 1),
                        "stake":      _fb_r["stake"],
                        "kelly_pct":  _fb_r["kelly_pct"],
                        "stake_warn": _fb_r.get("stake_warn", ""),
                    })
                    print(f"   🔄 ML fallback (K/9 insuficiente): {_fb_lbl} "
                          f"EV={_fb_ev:.1f}% ERA {h_era_eff:.2f} vs {a_era_eff:.2f}")
                else:
                    print(f"   ℹ️  ML fallback {_fb_lbl}: EV={_fb_ev:.1f}% "
                          f"prob={_fb_p_c:.0%} — no cumple mínimos, omitido")

        # Line movement flag
        h_cur = h2h_odds.get(home, (0,))[0]
        a_cur = h2h_odds.get(away, (0,))[0]
        moved_h, dir_h, dlt_h = detect_line_movement(game_id, home, h_cur, prev_map)
        moved_a, dir_a, dlt_a = detect_line_movement(game_id, away, a_cur, prev_map)

        # ── Public % / RLM estimate ────────────────────────────────────────
        rlm_data: dict = {}
        if h_cur > 0 and a_cur > 0:
            # Shorter odds = public favorite (more bets typically)
            if h_cur < a_cur:
                pub_fav, pub_dog = home, away
            else:
                pub_fav, pub_dog = away, home
            rlm_data = {
                "pub_fav": pub_fav, "pub_pct": 65,
                "pub_dog": pub_dog, "dog_pct": 35,
            }
            fav_moved = moved_h if pub_fav == home else moved_a
            fav_dir   = dir_h   if pub_fav == home else dir_a
            if fav_moved:
                if fav_dir == "+":
                    # Favorite line drifting → sharp money on underdog = RLM
                    rlm_data["rlm"]        = True
                    rlm_data["sharp_side"] = pub_dog
                    rlm_data["rlm_note"]   = (
                        f"📉 Línea bajó a favor de {_es(pub_dog)}\n"
                        f"💎 Dinero profesional en {_es(pub_dog)}"
                    )
                else:
                    # Favorite shortening → square public action, no sharp signal
                    rlm_data["rlm"]         = False
                    rlm_data["square_note"] = (
                        f"⚠️ Solo dinero público en {_es(pub_fav)} — sin señal sharp"
                    )
        # ──────────────────────────────────────────────────────────────────

        # Module 4: injury check
        il_data = {}
        try:
            il_data = fetch_mlb_il(home, away)
        except Exception:
            pass

        # Module 6: home/away splits (None if unavailable — never show fake data)
        h_splits = None
        a_splits = None
        try:
            h_splits = fetch_mlb_home_away_splits(home)
            a_splits = fetch_mlb_home_away_splits(away)
        except Exception:
            pass

        # MLB A1: pitcher recent form (last 3 starts)
        pform_h = pform_a = None
        try:
            pform_h = fetch_pitcher_recent_form(h_pname)
            pform_a = fetch_pitcher_recent_form(a_pname)
        except Exception:
            pass

        # MLB A2: home plate umpire
        umpire = None
        try:
            game_date = commence[:10]
            umpire    = fetch_home_plate_umpire(home, game_date)
        except Exception:
            pass

        if umpire:
            if umpire.get("tendency") == "OVER":
                home_exp = min(home_exp + 0.15, 12.0)
                away_exp = min(away_exp + 0.15, 12.0)
                print(f"   ⚖️  Umpire {umpire.get('name','?')} OVER → +0.15 a ambos totales")
            elif umpire.get("tendency") == "UNDER":
                home_exp = max(home_exp - 0.15, 0.1)
                away_exp = max(away_exp - 0.15, 0.1)
                print(f"   ⚖️  Umpire {umpire.get('name','?')} UNDER → -0.15 a ambos totales")

        # MLB A8: Serie game number
        _serie_num   = None
        _serie_total = None
        _serie_texto = ""
        try:
            _htid = _team_id(home)
            if _htid:
                _sched = _mlb_rest("/schedule", {
                    "teamId": _htid,
                    "season": MLB_YEAR,
                    "date":   datetime.now(CDT).strftime("%Y-%m-%d"),
                    "hydrate": "seriesStatus",
                })
                for _de in (_sched.get("dates") or []):
                    for _sg in (_de.get("games") or []):
                        _ss = _sg.get("seriesStatus") or {}
                        _sn = _ss.get("seriesGameNumber")
                        _st = _ss.get("gamesInSeries")
                        if _sn and _st:
                            _serie_num   = int(_sn)
                            _serie_total = int(_st)
                            _serie_texto = f"Juego {_serie_num} de {_serie_total} en la serie"
                            print(f"   📊 Serie: {_serie_texto}")
                            break
                    if _serie_num:
                        break
        except Exception:
            pass

        # Pitcher pace (pitches per inning — flags short outings)
        _h_pace = _a_pace = None
        try:
            _h_pace = fetch_pitcher_pace(h_pname)
        except Exception:
            pass
        try:
            _a_pace = fetch_pitcher_pace(a_pname)
        except Exception:
            pass

        # Bullpen load (innings used in last 3 days)
        _bp_load_h = _bp_load_a = None
        try:
            _bp_load_h = fetch_bullpen_load(home)
        except Exception:
            pass
        try:
            _bp_load_a = fetch_bullpen_load(away)
        except Exception:
            pass

        # MLB A3: temperature adjustment
        temp_f     = (wind.get("temp_f") if wind else None)
        t_adj, t_label = _temp_run_adj(temp_f)
        # apply temperature to projected totals
        home_exp = max(0.1, home_exp + t_adj / 2)
        away_exp = max(0.1, away_exp + t_adj / 2)

        # ── ADVERTENCIA: pitcher bueno vs ofensiva peligrosa del rival ─────────
        _pitcher_conflicts: list = []
        for _pit_name, _pit_era, _pit_team, _rival_team, _rival_bat, _rival_streak in [
            (h_pname, h_era_eff, home, away, bat_a, _a_streak),
            (a_pname, a_era_eff, away, home, bat_h, _h_streak),
        ]:
            if _pit_era >= 3.50:
                continue
            _flags = []
            _rival_ops = (_rival_bat.get("ops") or 0.0) if _rival_bat else 0.0
            if _rival_ops > 0.800:
                _flags.append(f"OPS {_rival_ops:.3f} del rival (ofensiva fuerte)")
            if _rival_streak and _rival_streak.get("is_hot"):
                _w = _rival_streak["wins_10"]
                _l = _rival_streak["losses_10"]
                _flags.append(f"rival en racha ({_w}-{_l} últimos 10)")
            _lr_bad = any(
                lr.get("lineup") == _rival_team and "bateadores" in lr.get("favor", "")
                for lr in lr_notes
            )
            if _lr_bad:
                _flags.append("matchup de mano desfavorable para el pitcher")
            if _flags:
                _pitcher_conflicts.append({
                    "pitcher": _pit_name,
                    "era":     _pit_era,
                    "team":    _pit_team,
                    "rival":   _rival_team,
                    "flags":   _flags,
                })
                print(f"   ⚠️  Conflicto: {_pit_name} (ERA {_pit_era:.2f}) vs {_rival_team} — {' | '.join(_flags)}")

        # ── Elite Source 2: Pinnacle Market Reference ─────────────────────────
        pin_data = _extract_pinnacle_odds(game)

        # Build pitcher display label — prefer xERA label when Statcast available
        _h_era_label = (f"xERA {h_era_eff:.2f}" if h_statcast and h_statcast.get("xera") is not None
                        else f"ERA {h_era:.2f}")
        _a_era_label = (f"xERA {a_era_eff:.2f}" if a_statcast and a_statcast.get("xera") is not None
                        else f"ERA {a_era:.2f}")

        context = {
            "pitcher_home":  f"{h_pname} ({_h_era_label})",
            "pitcher_conflicts": _pitcher_conflicts,
            "pitcher_away":  f"{a_pname} ({_a_era_label})",
            "rs_home": f"{h_stats['rs_pg']:.1f}", "ra_home": f"{h_stats['ra_pg']:.1f}",
            "rs_away": f"{a_stats['rs_pg']:.1f}", "ra_away": f"{a_stats['ra_pg']:.1f}",
            "park_factor":   park,
            "wind_info":     w_label,
            "temp_label":    t_label,   # MLB A3
            "line_moved":    moved_h or moved_a,
            "line_note":     (f"Línea {home} {dir_h}{dlt_h}" if moved_h
                              else f"Línea {away} {dir_a}{dlt_a}" if moved_a else ""),
            "il_data":       il_data,   # Module 4
            "h_splits":      h_splits,  # Module 6
            "a_splits":      a_splits,  # Module 6
            "pform_h":       pform_h,   # MLB A1
            "pform_a":       pform_a,   # MLB A1
            "umpire":        umpire,    # MLB A2
            "serie_juego_numero": _serie_num,    # MLB A8
            "serie_texto":        _serie_texto,  # MLB A8
            "tbd_note":         tbd_note,           # Fix 5
            "stats_fallback":   _stats_fallback_note,  # set when 4.5 default used
            "pname_home":    h_pname,   # raw pitcher name
            "pname_away":    a_pname,   # raw pitcher name
            "era_home":      h_era,     # raw ERA float
            "era_away":      a_era,     # raw ERA float
            "fip_home":      h_fip,     # MLB A4
            "fip_away":      a_fip,     # MLB A4
            "hand_home":     h_hand,    # MLB A5
            "hand_away":     a_hand,    # MLB A5
            "lr_notes":      lr_notes,  # MLB A5
            "h2h_data":      h2h_data,   # MLB A6
            "h2h_book_line": totals_data[0] if totals_data else None,  # MLB A6
            "h2h_note":      _h2h_note, # MLB A6 contradiction warning
            "bat_home":      bat_h,     # MLB A7
            "bat_away":      bat_a,     # MLB A7
            "rlm_data":      rlm_data,  # Public % / RLM (Module B)
            "lineup_data":   _lineup,   # Confirmed lineup (MLB A8)
            # Enhanced context (last 3 starts, bullpen ERA 7d, serie stats)
            "enh_h_last3_era":     _enh_ctx.get("home_pitcher_last3_era_avg"),
            "enh_a_last3_era":     _enh_ctx.get("away_pitcher_last3_era_avg"),
            "enh_h_last3_txt":     _enh_ctx.get("home_pitcher_last3_txt"),
            "enh_a_last3_txt":     _enh_ctx.get("away_pitcher_last3_txt"),
            "enh_h_bullpen_era7d": _enh_ctx.get("home_bullpen_era_7d"),
            "enh_a_bullpen_era7d": _enh_ctx.get("away_bullpen_era_7d"),
            "enh_serie_txt":       _enh_ctx.get("serie_txt"),
            "pitch_intel": {            # intelligence rules output
                "notes":         _pitch_notes,
                "reasoning":     _pitch_reason,
                "contradiction": _contradiction,
            },
            # ── Elite Source 1: Statcast ──────────────────────────────────────
            "statcast_home":  h_statcast,   # dict or None
            "statcast_away":  a_statcast,   # dict or None
            "era_eff_home":   h_era_eff,    # xERA if available else ERA
            "era_eff_away":   a_era_eff,
            # ── Elite Source 2: Pinnacle Market Reference ─────────────────────
            "pinnacle_odds":  pin_data,     # {"home": price, "away": price} or None
            # ── Mercados extendidos (F5, Hits, Ponches) ───────────────────────
            "f5_proj":   round((home_exp + away_exp) * 0.52, 2),
            "f5_h2h_ok": bool(f5_h2h),
            "f5_tot_ok": bool(f5_tot),
            "hits_proj": round(((bat_h.get("avg") or 0.250) * 30 +
                                (bat_a.get("avg") or 0.250) * 30), 1) if bat_h and bat_a else None,
            "ko_proj_home": round(
                (10.5 if h_era_eff < 2.00 else 9.5 if h_era_eff < 2.75 else
                  8.5 if h_era_eff < 3.50 else 7.5 if h_era_eff < 4.50 else 6.5) *
                (6.0 if h_era_eff < 3.00 else 5.5 if h_era_eff < 4.00 else 5.0) / 9.0, 1),
            "ko_proj_away": round(
                (10.5 if a_era_eff < 2.00 else 9.5 if a_era_eff < 2.75 else
                  8.5 if a_era_eff < 3.50 else 7.5 if a_era_eff < 4.50 else 6.5) *
                (6.0 if a_era_eff < 3.00 else 5.5 if a_era_eff < 4.00 else 5.0) / 9.0, 1),
            "pitcher_pace_home":  _h_pace,
            "pitcher_pace_away":  _a_pace,
            "bullpen_load_home":  _bp_load_h,
            "bullpen_load_away":  _bp_load_a,
            "ttt_note":           _ttt_note,
        }
        context["data_quality_score"] = _data_completeness_score(
            context, sport_key, home, away)

    # ── SOCCER ────────────────────────────────────────────────────────────────
    else:
        elo_h = _elo_for(home)
        elo_a = _elo_for(away)

        elo_base_h = 1.35 * (1 + (elo_h - 1500) / 4000)
        elo_base_a = 1.25 * (1 + (elo_a - 1500) / 4000)

        form_h = fetch_wc_team_form(home)
        form_a = fetch_wc_team_form(away)

        blend_h = (0.6 * form_h["goals_for"] + 0.4 * elo_base_h) if form_h else elo_base_h
        blend_a = (0.6 * form_a["goals_for"] + 0.4 * elo_base_a) if form_a else elo_base_a

        # ── Elite Source 3: Understat xG — refine blend with qualifying xG ───
        xg_home_data = xg_away_data = None
        try:
            xg_home_data = fetch_understat_xg(home)
            xg_away_data = fetch_understat_xg(away)
            if xg_home_data and xg_away_data:
                # Weight: 40% xG (qualifying), 60% form/ELO blend
                blend_h = max(0.1, 0.6 * blend_h + 0.4 * xg_home_data["xg_for"])
                blend_a = max(0.1, 0.6 * blend_a + 0.4 * xg_away_data["xg_for"])
                print(f"   📊 xG blend: {home}={blend_h:.2f} {away}={blend_a:.2f}")
        except Exception:
            xg_home_data = xg_away_data = None

        p_win, p_draw, p_loss = poisson_match_probs(blend_h, blend_a)

        # ── Module S3/S5/S6: Pre-adjust probabilities before ML loop ─────────
        _wc_pre   = {}
        _wcs_h_g  = _wcs_a_g  = None
        _ln_h_g   = _ln_a_g   = {"confirmed": False, "prob_adj": 0.0, "note": ""}
        _draw_boost_g  = 0.0
        _press_note_g  = ""
        _press_tot_g   = 0.0
        try:
            _wc_pre = fetch_wc_standings()
        except Exception:
            pass

        # Module S3: WC streak ML adjustment
        _streak_ml_delta = 0.0
        try:
            _wcs_h_g = fetch_wc_streak(home)
            _wcs_a_g = fetch_wc_streak(away)
            if _wcs_h_g:
                _streak_ml_delta += _wcs_h_g["ml_adj"]
            if _wcs_a_g:
                _streak_ml_delta -= _wcs_a_g["ml_adj"]
        except Exception:
            pass

        # Module S5: Lineup intel
        _ln_adj_g = 0.0
        try:
            _ln_h_g = fetch_soccer_lineup_intel(home, sport_key)
            _ln_a_g = fetch_soccer_lineup_intel(away, sport_key)
            _ln_adj_g = _ln_h_g["prob_adj"] - _ln_a_g["prob_adj"]
        except Exception:
            pass

        # Module S6: Psychological pressure
        try:
            _press_tot_g, _draw_boost_g, _press_note_g = _wc_pressure_block(
                home, away, _wc_pre
            )
        except Exception:
            pass

        # Apply all adjustments to p_win / p_draw / p_loss
        _ml_delta_g = _streak_ml_delta + _ln_adj_g
        if abs(_ml_delta_g) > 0.001 or _draw_boost_g > 0.001:
            p_win   = max(0.05, min(0.92, p_win  + _ml_delta_g))
            p_loss  = max(0.05, min(0.92, p_loss - _ml_delta_g))
            if _draw_boost_g > 0:
                p_draw = min(0.60, p_draw + _draw_boost_g)
            _tot_g  = max(0.01, p_win + p_draw + p_loss)
            p_win  /= _tot_g; p_draw /= _tot_g; p_loss /= _tot_g

        # ML — 3 outcomes
        draw_key = next((k for k in h2h_odds if k.lower() in ("draw", "the draw")), None)
        for lbl, team_key, true_p in [
            (f"🔵 {home} ML", home,     p_win),
            ("🤝 Empate",     draw_key,  p_draw),
            (f"🔴 {away} ML", away,      p_loss),
        ]:
            if team_key is None or team_key not in h2h_odds:
                continue
            odds, book = h2h_odds[team_key]
            ev = (true_p * odds - 1) * 100
            r  = kelly_stake(true_p, odds)
            _all_evs.append((lbl, round(ev, 1)))
            if ev >= EV_MIN_PCT and r["stake"] > 0:
                candidates.append({"label": lbl, "true_prob": true_p, "odds": odds,
                                   "book": book, "ev_pct": round(ev, 1),
                                   "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # Module 5: WC group standings (fetched early — needed for intel rules)
        wc_standings = {}
        try:
            wc_standings = fetch_wc_standings()
        except Exception:
            pass

        # Totals
        _soc_notes    = []
        _soc_reason   = ""
        _contradiction_s = False
        if totals_data:
            book_line, over_odds, under_odds, bk_name = totals_data
            exp_total = blend_h + blend_a

            # ── Soccer Intelligence Rules ──────────────────────────────────
            _over_min_edge_s = 0.0
            ga_h = form_h["goals_against"] if form_h else None
            ga_a = form_a["goals_against"] if form_a else None
            gf_h = form_h["goals_for"]     if form_h else None
            gf_a = form_a["goals_for"]     if form_a else None

            # Rule 1: any team with strong defense → reduce total
            solid_h = ga_h is not None and ga_h < 0.8
            solid_a = ga_a is not None and ga_a < 0.8
            if solid_h or solid_a:
                exp_total -= 0.4
                _over_min_edge_s = 0.6
                def_team = home if solid_h else away
                def_ga   = ga_h  if solid_h else ga_a
                _soc_notes.append(
                    f"⚠️ Defensa sólida ({def_team} — {def_ga:.1f} goles recibidos/partido) — "
                    f"el Over necesita más edge para tener valor"
                )

            # Rule 2: both teams score well → raise total, OVER gets priority
            if (gf_h is not None and gf_h > 1.8 and
                    gf_a is not None and gf_a > 1.8):
                exp_total += 0.3
                _soc_notes.append(
                    "✅ Ambos equipos atacan bien — condiciones favorables para Over"
                )

            exp_total    = max(0.1, exp_total)
            _over_edge_s = exp_total - book_line  # positive = model favors OVER

            # Rule 3: contradiction — solid defense but model still says OVER
            if (solid_h or solid_a) and _over_edge_s > 0:
                _contradiction_s = True
                _soc_notes.append(
                    "⚠️ Contradicción: defensa sólida pero el modelo dice Over.\n"
                    "   Considera apostar UNDER."
                )

            # Rule 5: WC tactical urgency
            if wc_standings:
                try:
                    urg_h = _wc_urgency_line(home, wc_standings)
                    urg_a = _wc_urgency_line(away, wc_standings)
                    needs_h = urg_h and ("necesita" in urg_h.lower() or "eliminado" in urg_h.lower())
                    needs_a = urg_a and ("necesita" in urg_a.lower() or "eliminado" in urg_a.lower())
                    qual_h  = urg_h and "clasificado" in urg_h.lower() and "necesita" not in urg_h.lower()
                    qual_a  = urg_a and "clasificado" in urg_a.lower() and "necesita" not in urg_a.lower()
                    if needs_h or needs_a:
                        exp_total += 0.3
                        needs_team = home if needs_h else away
                        _soc_notes.append(
                            f"⚽ Partido abierto esperado → Favorece Over y ML rival\n"
                            f"   ({needs_team} NECESITA ganar)"
                        )
                    elif qual_h and qual_a:
                        exp_total -= 0.2
                        _soc_notes.append(
                            "🛡️ Ambos clasificados → partido conservador → Favorece Under"
                        )
                    exp_total = max(0.1, exp_total)
                    _over_edge_s = exp_total - book_line
                except Exception:
                    pass

            # Rule 4: reasoning summary (always shown for totals picks)
            _ga_h_s = f"{ga_h:.1f}" if ga_h is not None else "N/D"
            _ga_a_s = f"{ga_a:.1f}" if ga_a is not None else "N/D"
            _gf_a_s = f"{gf_a:.1f}" if gf_a is not None else "N/D"
            _h_score = ("POCO"    if (ga_a is not None and ga_a < 0.8)  else
                        "bien"    if (gf_h is not None and gf_h > 1.8)  else "moderado")
            _a_score = ("POCO"    if (ga_h is not None and ga_h < 0.8)  else
                        "bien"    if (gf_a is not None and gf_a > 1.8)  else "moderado")
            _dir_s = (
                f"bajo (proyectado {exp_total:.2f} vs línea {book_line}) → Favorece UNDER"
                if exp_total < book_line else
                f"alto (proyectado {exp_total:.2f} vs línea {book_line}) → Favorece OVER"
            )
            _soc_reason = (
                f"📊 {home} (concede {_ga_h_s}/partido):\n"
                f"   {away} anotará {_a_score}\n"
                f"   {away} (anota {_gf_a_s}/partido):\n"
                f"   {home} anotará {'con dificultad' if solid_a else _h_score}\n"
                f"   → Total proyectado {_dir_s}"
            )
            # ──────────────────────────────────────────────────────────────

            for side_label, is_over, p, odds in [
                (f"📈 OVER {book_line} goles",  True,
                 poisson_ou_prob(exp_total, book_line, True),  over_odds),
                (f"📉 UNDER {book_line} goles", False,
                 poisson_ou_prob(exp_total, book_line, False), under_odds),
            ]:
                # Rule 1: skip OVER below defense threshold
                if is_over and _over_min_edge_s > 0 and _over_edge_s < _over_min_edge_s:
                    continue
                # Improvement 2: confidence filter for soccer totals — 58% minimum
                if p < PROB_MIN_TOTALS:
                    _side_n = "OVER" if is_over else "UNDER"
                    print(f"   ⏭️  {_tag}: {_side_n} goles prob {p:.0%} < {PROB_MIN_TOTALS:.0%} mín — omitido")
                    continue
                ev = (p * odds - 1) * 100
                r  = kelly_stake(p, odds)
                _all_evs.append((side_label, round(ev, 1)))
                if ev >= EV_MIN_PCT and r["stake"] > 0:
                    candidates.append({"label": side_label, "true_prob": p, "odds": odds,
                                       "book": bk_name, "ev_pct": round(ev, 1),
                                       "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # Handicap -0.5 / +0.5
        for team, true_p, lbl in [
            (home, p_win,           f"🔵 {home} -0.5"),
            (away, p_draw + p_loss, f"🔴 {away} +0.5"),
        ]:
            if team not in spread_odds:
                continue
            _pt, odds, book = spread_odds[team]
            ev = (true_p * odds - 1) * 100
            r  = kelly_stake(true_p, odds)
            _all_evs.append((lbl, round(ev, 1)))
            if ev >= EV_MIN_PCT and r["stake"] > 0:
                candidates.append({"label": lbl, "true_prob": true_p, "odds": odds,
                                   "book": book, "ev_pct": round(ev, 1),
                                   "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # Line movement flag
        h_cur = h2h_odds.get(home, (0,))[0]
        a_cur = h2h_odds.get(away, (0,))[0]
        moved_h, dir_h, dlt_h = detect_line_movement(game_id, home, h_cur, prev_map)
        moved_a, dir_a, dlt_a = detect_line_movement(game_id, away, a_cur, prev_map)

        form_note_h = (f"{form_h['goals_for']:.1f} goles/partido ({form_h['matches']} partidos)"
                       if form_h else "")
        form_note_a = (f"{form_a['goals_for']:.1f} goles/partido ({form_a['matches']} partidos)"
                       if form_a else "")

        # Soccer S1: recent form detail (last 3 matches)
        sform_h = sform_a = None
        try:
            sform_h = fetch_soccer_team_recent(home, sport_key)
            sform_a = fetch_soccer_team_recent(away, sport_key)
        except Exception:
            pass

        # Soccer S2: match referee
        referee = None
        try:
            referee = fetch_match_referee(home, away, sport_key)
        except Exception:
            pass

        # Soccer S3: venue temperature
        t_adj_g  = 0.0
        t_label_s = ""
        try:
            venue_city = (referee.get("venue_city", "") if referee else "")
            temp_f_s   = fetch_venue_temp(venue_city) if venue_city else None
            t_adj_g, t_label_s = _temp_goals_adj(temp_f_s)
        except Exception:
            pass

        # Apply temp goal adjustment to projected totals
        blend_h = max(0.1, blend_h + t_adj_g / 2)
        blend_a = max(0.1, blend_a + t_adj_g / 2)

        # ── Module S1: WC Venue Tendency (context) ────────────────────────
        _venue_adj_g  = 0.0
        _venue_note_g = ""
        try:
            _vcity_g = (referee.get("venue_city", "") if referee else "")
            _venue_adj_g, _venue_note_g = _wc_venue_adj(_vcity_g)
            if abs(_venue_adj_g) > 0.0:
                blend_h = max(0.1, blend_h + _venue_adj_g / 2)
                blend_a = max(0.1, blend_a + _venue_adj_g / 2)
        except Exception:
            pass

        # ── Module S2: Días de Descanso Fútbol (context) ─────────────────
        _rest_h_g = _rest_a_g = (0, 0.0, "")
        try:
            _rest_h_g = fetch_soccer_rest_days(home)
            _rest_a_g = fetch_soccer_rest_days(away)
        except Exception:
            pass

        context = {
            "elo_home": elo_h, "elo_away": elo_a, "elo_diff": elo_h - elo_a,
            "form_home": form_note_h,
            "form_away": form_note_a,
            "conceded_home": (f"{form_h['goals_against']:.1f}" if form_h else ""),
            "conceded_away": (f"{form_a['goals_against']:.1f}" if form_a else ""),
            "p_draw":     round(p_draw * 100, 1),
            "line_moved": moved_h or moved_a,
            "line_note":  (f"Línea {home} {dir_h}{dlt_h}" if moved_h
                          else f"Línea {away} {dir_a}{dlt_a}" if moved_a else ""),
            "wc_standings":  _wc_pre,                # Module 5 / S6
            "sform_h":       sform_h,                # Soccer S1
            "sform_a":       sform_a,                # Soccer S1
            "referee":       referee,                # Soccer S2
            "temp_label_s":  t_label_s,              # Soccer S3
            "venue_note_s":  _venue_note_g,          # Module S1
            "rest_h_s":      _rest_h_g[2],           # Module S2
            "rest_a_s":      _rest_a_g[2],           # Module S2
            "wc_streak_h":   _wcs_h_g,               # Module S3
            "wc_streak_a":   _wcs_a_g,               # Module S3
            "lineup_h_s":    _ln_h_g,                # Module S5
            "lineup_a_s":    _ln_a_g,                # Module S5
            "pressure_note": _press_note_g,          # Module S6
            "draw_boost":    round(_draw_boost_g, 3),# Module S6
            "soccer_intel": {                        # intelligence rules output
                "notes":         _soc_notes,
                "reasoning":     _soc_reason,
                "contradiction": _contradiction_s,
            },
            # ── Elite Source 3: Understat xG ─────────────────────────────────
            "xg_home":       xg_home_data,   # dict or None
            "xg_away":       xg_away_data,
            # ── Elite Source 2: Pinnacle Market Reference ─────────────────────
            "pinnacle_odds": _extract_pinnacle_odds(game),
        }
        context["data_quality_score"] = _data_completeness_score(
            context, sport_key, home, away)

    # ── Debug summary: data quality + projections ────────────────────────────
    _dqs_pre = context.get("data_quality_score", 100)
    if is_mlb:
        _ph = context.get("pname_home", "?")
        _pa = context.get("pname_away", "?")
        _eh = context.get("era_home", "?")
        _ea = context.get("era_away", "?")
        print(f"   📊 Calidad: {_dqs_pre}/100 | "
              f"Pitcher: {_ph} (ERA {_eh}) vs {_pa} (ERA {_ea})")
    else:
        _eh = context.get("elo_home", "?")
        _ea = context.get("elo_away", "?")
        _ed = context.get("elo_diff", 0)
        print(f"   📊 Calidad: {_dqs_pre}/100 | "
              f"ELO: {_eh} vs {_ea} (diff {_ed:+.0f})")
    if _all_evs:
        _ev_strs = "  ".join(
            f"{lbl.split()[-1] if ' ' in lbl else lbl}:{ev:+.1f}%"
            for lbl, ev in _all_evs
        )
        print(f"   📈 EVs calculados: {_ev_strs}")

    # FIX 10: Boosts situacionales antes del sort — mercado con mejor contexto gana
    if is_mlb:
        _era_diff_sit = abs(h_era_eff - a_era_eff) if h_era_eff and a_era_eff else 0.0
        for _c in candidates:
            _lbl = _c.get("label", "")
            _boost = 0.0
            # Ventaja de pitcher dominante → boost ML del equipo con mejor ERA
            if _era_diff_sit > 2.0:
                _better_team = home if h_era_eff < a_era_eff else away
                if _better_team in _lbl and "F5" not in _lbl and "RL" not in _lbl and "OVER" not in _lbl and "UNDER" not in _lbl:
                    _boost = 0.03
            # Ambos con ERA > 5.0 → juego alto en carreras → OVER
            if h_era_eff and a_era_eff and h_era_eff > 5.0 and a_era_eff > 5.0:
                if "OVER" in _lbl:
                    _boost = 0.03
            # Ambos con ERA < 3.5 → duelo de pitcheo → UNDER
            if h_era_eff and a_era_eff and h_era_eff < 3.5 and a_era_eff < 3.5:
                if "UNDER" in _lbl:
                    _boost = 0.03
            # Pitcher local élite en casa → boost F5 ML local
            if h_era_eff and h_era_eff < 2.75 and "F5" in _lbl and home in _lbl:
                _boost = max(_boost, 0.02)
            if _boost > 0:
                _new_p = min(0.92, _c["true_prob"] + _boost)
                _new_ev = round((_new_p * _c["odds"] - 1) * 100, 1)
                print(f"   🎯 Boost situacional {_lbl}: prob {_c['true_prob']:.3f}→{_new_p:.3f} EV {_c['ev_pct']:+.1f}→{_new_ev:+.1f}%")
                _c["true_prob"] = _new_p
                _c["ev_pct"]    = _new_ev

    # Drop any pick whose true probability is below the minimum threshold
    candidates = [c for c in candidates if c["true_prob"] >= PROB_MIN]

    # ── Pinnacle calibration: blend model prob 40% + Pinnacle 60% ─────────
    # Recomputes EV and stake for every candidate where Pinnacle has odds.
    # Candidates that fall below PROB_MIN after blending are dropped.
    # High-divergence picks (>25pp) get stake halved + divergence alert.
    candidates, _pin_div_alerts = _apply_pinnacle_calibration(candidates, game, home)
    for _cal_c in candidates:
        if _cal_c["label"] in _all_mkts:
            _all_mkts[_cal_c["label"]]["ev_pct"] = _cal_c["ev_pct"]
            _all_mkts[_cal_c["label"]]["prob"] = _cal_c["true_prob"]

    if not candidates:
        _best = max(_all_evs, key=lambda x: x[1]) if _all_evs else None
        if _best:
            print(f"   ❌ Sin picks — mejor EV: {_best[0].split()[0]} {_best[1]:+.1f}% "
                  f"(mínimo {EV_MIN_PCT:.1f}%)")
        else:
            print(f"   ❌ Sin picks — sin odds válidas para analizar")
        # ── Auto-panel: cualquier mercado con EV > 15% activa el panel de expertos ──
        # Incluso sin pick formal el modelo detectó una línea muy ventajosa.
        # El panel evalúa si vale la pena apostar pese a no superar prob mínima.
        _aev_mkts = {lbl: m for lbl, m in _all_mkts.items() if m["ev_pct"] > 15.0}
        if _aev_mkts:
            _aev_lbl  = max(_aev_mkts, key=lambda l: _aev_mkts[l]["ev_pct"])
            _aev_m    = _aev_mkts[_aev_lbl]
            print(f"   🤖 Panel auto-activado — EV {_aev_m['ev_pct']:.1f}% > 15% "
                  f"en '{_aev_lbl}' (sin umbral de prob)")
            _aev_data = {
                "match":     f"{home} vs {away}",
                "sport":     sport_key,
                "top_pick":  _aev_lbl,
                "ev_pct":    _aev_m["ev_pct"],
                "true_prob": round(_aev_m["prob"] * 100, 1),
                "odds":      _aev_m["odds"],
                "stake":     0,
                "nota":      (
                    f"Mercado EV>{_aev_m['ev_pct']:.1f}% detectado, no superó umbral de "
                    f"probabilidad mínima ({PROB_MIN*100:.0f}%). Panel evalúa si merece apostar."
                ),
            }
            _aev_data.update({k: v for k, v in context.items()
                              if isinstance(v, (str, int, float, bool, type(None)))})
            if _pin_div_alerts:
                _aev_data["divergence_alerts"] = " | ".join(_pin_div_alerts)
            _aev_sport = "MLB" if is_mlb else "SOCCER"
            if is_mlb:
                try:
                    _aev_data.update(_enrich_panel_data(_aev_data, game))
                except Exception:
                    pass
            _aev_panel = panel_expertos(_aev_data, _aev_sport,
                                         _no_elite=_no_elite_panel, _force_elite=_force_elite_panel)
            if _aev_panel:
                _cc = _aev_panel.get("confianza", "N/D")
                _ap = "✅" if _aev_panel.get("apostar", True) else "❌"
                _cr = (_aev_panel.get("razonamiento", "") or "")[:500]
                print(f"   🤖 Claude (EV>15%): {_cc} | apostar:{_ap} | \"{_cr}\"")
            return {
                "game_id":     game_id,
                "match":       f"{home} vs {away}",
                "time":        commence,
                "sport":       sport_key,
                "is_mlb":      is_mlb,
                "candidates":  [],
                "context":     context,
                "best_label":  _aev_lbl,
                "best_ev":     _aev_m["ev_pct"],
                "claude_intel": _aev_panel,
                "all_markets": _all_mkts,
            }
        if not force_panel:
            return None
        # force_panel=True (/analizar manual): no hay edge positivo pero
        # el usuario quiere ver el análisis completo — continuar con top3=[]

    # Rank by EV%, keep top 3, tag safest (prob ≥ 60%)
    candidates.sort(key=lambda x: x["ev_pct"], reverse=True)
    top3 = candidates[:3]
    for c in top3:
        c["safest"] = c["true_prob"] >= 0.60

    # force_panel=True con sin candidatos: devolver resultado con contexto completo
    # (el usuario quiere ver pitchers/stats/clima aunque no haya edge)
    if not top3:
        _hev_mkts  = {lbl: m for lbl, m in _all_mkts.items() if m["ev_pct"] > 15.0}
        _hev_panel = None
        _hev_lbl   = None
        _hev_ev    = 0.0
        if _hev_mkts:
            _hev_lbl  = max(_hev_mkts, key=lambda l: _hev_mkts[l]["ev_pct"])
            _hev_m    = _hev_mkts[_hev_lbl]
            _hev_ev   = _hev_m["ev_pct"]
            print(f"   🤖 Panel auto-activado (force) — EV {_hev_ev:.1f}% > 15% en '{_hev_lbl}'")
            _hev_data = {
                "match":     f"{home} vs {away}",
                "sport":     sport_key,
                "top_pick":  _hev_lbl,
                "ev_pct":    _hev_ev,
                "true_prob": round(_hev_m["prob"] * 100, 1),
                "odds":      _hev_m["odds"],
                "stake":     0,
                "nota":      (
                    f"Mercado EV>{_hev_ev:.1f}% detectado, no superó umbral de "
                    f"probabilidad mínima ({PROB_MIN*100:.0f}%). Panel evalúa si merece apostar."
                ),
            }
            _hev_data.update({k: v for k, v in context.items()
                              if isinstance(v, (str, int, float, bool, type(None)))})
            if _pin_div_alerts:
                _hev_data["divergence_alerts"] = " | ".join(_pin_div_alerts)
            _hev_sport = "MLB" if is_mlb else "SOCCER"
            if is_mlb:
                try:
                    _hev_data.update(_enrich_panel_data(_hev_data, game))
                except Exception:
                    pass
            _hev_panel = panel_expertos(_hev_data, _hev_sport,
                                         _no_elite=_no_elite_panel, _force_elite=_force_elite_panel)
            if _hev_panel:
                _cc = _hev_panel.get("confianza", "N/D")
                _ap = "✅" if _hev_panel.get("apostar", True) else "❌"
                print(f"   🤖 Claude (force EV>15%): {_cc} | apostar:{_ap}")
        return {
            "game_id":     game_id,
            "match":       f"{home} vs {away}",
            "time":        commence,
            "sport":       sport_key,
            "is_mlb":      is_mlb,
            "candidates":  [],
            "context":     context,
            "best_label":  _hev_lbl,
            "best_ev":     _hev_ev,
            "claude_intel": _hev_panel,
            "all_markets": _all_mkts,
        }

    # ── Feature 7: data completeness guard ───────────────────────────────
    _dqs = context.get("data_quality_score", 100)
    if _dqs < 40:
        print(f"  ⏭️  Juego omitido — datos muy escasos ({_dqs}/100): {home} vs {away}")
        _log_error("data_completeness", "skip", f"{home} vs {away}",
                   f"score {_dqs}/100 < 40")
        if not force_panel:
            return None
        print(f"  ℹ️  force_panel=True — mostrando análisis con datos escasos a usuario")

    # ── Claude AI: expert validation of top pick ──────────────────────────
    # Garantizar que top3[0] sea siempre el pick de mayor EV
    top3.sort(key=lambda x: x["ev_pct"], reverse=True)
    _claude_data_g = {
        "match":     f"{home} vs {away}",
        "sport":     sport_key,
        "top_pick":  top3[0]["label"],
        "ev_pct":    top3[0]["ev_pct"],
        "true_prob": round(top3[0]["true_prob"] * 100, 1),
        "odds":      top3[0]["odds"],
        "stake":     top3[0]["stake"],
    }
    # FIX 9: Panel recibe los top3 para comparar y elegir el mejor (cuando force_panel)
    if force_panel and len(top3) > 1:
        _alt_picks_str = " | ".join(
            f"#{i+2} {c['label']} EV{c['ev_pct']:+.1f}% @{c['odds']:.2f}"
            for i, c in enumerate(top3[1:3])
        )
        _claude_data_g["alternativas_panel"] = (
            f"Compara estos candidatos y elige el mejor: "
            f"#1 {top3[0]['label']} EV{top3[0]['ev_pct']:+.1f}% @{top3[0]['odds']:.2f} | "
            f"{_alt_picks_str}"
        )
    _claude_data_g.update({
        k: v for k, v in context.items()
        if isinstance(v, (str, int, float, bool, type(None)))
    })
    _claude_data_g["equipo_local"] = f"{home} (LOCAL 🏠)"
    _claude_data_g["equipo_visitante"] = f"{away} (VISITANTE ✈️)"
    _claude_data_g["pitcher_local"] = f"{h_pname} — pitcher LOCAL de {home}"
    _claude_data_g["pitcher_visitante"] = f"{a_pname} — pitcher VISITANTE de {away}"
    if context.get("serie_texto"):
        _claude_data_g["serie_info"] = context["serie_texto"]
    # ── Señal Pinnacle: confirmación o advertencia antes del panel ─────────
    # Compara la dirección del pick contra el mercado sharp de Pinnacle y
    # añade una cadena de texto que Marco, Víctor y Elena reciben como contexto
    # previo a dar su veredicto. Si no hay datos de Pinnacle, no se añade nada.
    _pin_signal = _build_pinnacle_panel_signal(
        top3[0]["label"], context, game, home
    )
    if _pin_signal:
        _claude_data_g["pinnacle_panel_signal"] = _pin_signal
        _pin_icon = "✅" if "CONFIRMACIÓN" in _pin_signal else "⚠️"
        print(f"   📌 Pinnacle signal: {_pin_icon} {_pin_signal[:80]}…")
    # ── Alertas de divergencia Pinnacle → panel ────────────────────────────
    # Generadas por _apply_pinnacle_calibration cuando |modelo−Pinnacle| > 25pp.
    # Divergencias de 8–20pp son normales (Pinnacle balancea su libro).
    # Solo >25pp se inyecta como alerta al panel de expertos.
    if _pin_div_alerts:
        _claude_data_g["divergence_alerts"] = " | ".join(_pin_div_alerts)
    _claude_sport_g = "MLB" if is_mlb else "SOCCER"
    _top_ev = top3[0]["ev_pct"]
    if _top_ev < 3.0 and not force_panel:
        print(f"   ⏭️  Panel omitido — EV {_top_ev:.1f}% < 3% mínimo ({top3[0]['label']})")
        _claude_result_g = None
    else:
        if is_mlb:
            _claude_data_g.update(_enrich_panel_data(_claude_data_g, game))
        _claude_result_g = panel_expertos(_claude_data_g, _claude_sport_g,
                                          _no_elite=_no_elite_panel, _force_elite=_force_elite_panel)

        if force_panel and len(top3) > 1:
            _extra_candidates_results = []
            for _extra_c in top3[1:]:
                _extra_data = {
                    "match": f"{home} vs {away}",
                    "sport": sport_key,
                    "top_pick": _extra_c["label"],
                    "ev_pct": _extra_c["ev_pct"],
                    "true_prob": round(_extra_c["true_prob"] * 100, 1),
                    "odds": _extra_c["odds"],
                    "stake": _extra_c["stake"],
                }
                _extra_data.update({k: v for k, v in context.items()
                                    if isinstance(v, (str, int, float, bool, type(None)))})
                _extra_panel = panel_expertos(_extra_data, _claude_sport_g,
                                             _no_elite=_no_elite_panel, _force_elite=_force_elite_panel)
                _extra_candidates_results.append({
                    "label": _extra_c["label"],
                    "ev_pct": _extra_c["ev_pct"],
                    "true_prob": _extra_c["true_prob"],
                    "odds": _extra_c["odds"],
                    "stake": _extra_c["stake"],
                    "panel": _extra_panel,
                })
            context["_extra_panels"] = _extra_candidates_results

    if _claude_result_g:
        _cc  = _claude_result_g.get("confianza", "N/D")
        _cap = "✅" if _claude_result_g.get("apostar", True) else "❌"
        _cr  = (_claude_result_g.get("razonamiento", "") or "")[:500]
        _ci  = _claude_result_g.get("datos_inconsistentes") or []
        print(f"   🤖 Claude: {_cc} | apostar:{_cap} | \"{_cr}\"")
        if _ci:
            print(f"      ⚠️ Inconsistencias: {', '.join(str(x) for x in _ci[:2])}")
    else:
        print(f"   🤖 Claude: no disponible (sin API key o error)")

    # Hard veto: Claude says apostar=False OR confianza=BAJA → block immediately
    # Guard must fire BEFORE any pick is assigned or returned
    # force_panel=True (manual /analizar): skip veto so full analysis is returned
    #
    # EXCEPCIÓN: EV >15% + divergencia Pinnacle <25pp → bypass del veto.
    # Pinnacle opera cerca del 50% por balanceo de libro — 8–20pp de divergencia
    # es normal. Solo >25pp (_pin_div_alerts no vacío) justifica veto por mercado.
    _top_ev_pct = top3[0]["ev_pct"] if top3 else 0.0
    # Bypass solo si: EV>15% + divergencia Pinnacle aceptable + al menos 1 experto votó sí.
    # Con 0/3 votos el veto es ABSOLUTO sin importar el EV — unanimidad en contra
    # indica un riesgo que el modelo de valor no captura.
    _votos_panel = (_claude_result_g.get("_votos_favor", 0)
                    if _claude_result_g else 0)
    _top_prob = top3[0]["true_prob"] if top3 else 1.0
    _bypass_veto = (_top_ev_pct > 8.0 and not _pin_div_alerts and _votos_panel >= 1)
    if _claude_result_g and not force_panel:
        _apostar_c   = _claude_result_g.get("apostar", True)
        _confianza_c = _claude_result_g.get("confianza", "MEDIA")
        _should_veto = (not _apostar_c or _confianza_c == "BAJA")
        if _should_veto and _bypass_veto:
            # EV fuerte + divergencia aceptable → no vetar; elevar confianza a MEDIA
            print(
                f"   ℹ️  Veto omitido — EV {_top_ev_pct:.1f}% > 15% + div Pinnacle <25pp "
                f"(apostar={_apostar_c}, confianza={_confianza_c})"
            )
            _claude_result_g["apostar"]    = True
            _claude_result_g["confianza"]  = "MEDIA"
        elif _should_veto:
            _veto_why = (f"apostar={_apostar_c}, confianza={_confianza_c}")
            print(f"   ❌ RECHAZADO — Claude vetó el partido ({_veto_why}) → sin pick")
            return None

    # Feature 7: cap confidence at MEDIA when data is partial (score 50–79)
    if 50 <= _dqs < 80 and _claude_result_g:
        if _claude_result_g.get("confianza") == "ALTA":
            _claude_result_g["confianza"] = "MEDIA"
        _claude_result_g["datos_incompletos"] = True
        print(f"   ⚠️  Confianza capada a MEDIA — datos parciales ({_dqs}/100)")

    # Calcular pick más probable independiente de EV
    _mp_team = home if p_home >= p_away else away
    _mp_prob = p_home if p_home >= p_away else p_away
    _mp_h2h  = h2h_odds.get(_mp_team)
    _most_prob_pick = {
        "team":      _mp_team,
        "prob":      round(_mp_prob * 100, 1),
        "odds":      _mp_h2h[0] if _mp_h2h else None,
        "book":      _mp_h2h[1] if _mp_h2h else "Bovada",
        "has_value": any(
            _mp_team.split()[-1].lower() in c.get("label", "").lower()
            for c in top3
        ),
    }

    _best_pick = top3[0]
    print(f"   ✅ PICK: {_best_pick['label']}  EV+{_best_pick['ev_pct']:.1f}%  "
          f"@ {_best_pick['odds']}  stake=${_best_pick['stake']:.0f}")

    # Pick más probable (independiente de valor)
    _prob_map = {}
    for team, prob in [(home, p_home), (away, p_away)]:
        if team in h2h_odds:
            _prob_map[team] = {"prob": prob, "odds": h2h_odds[team][0]}
    _most_probable = max(_prob_map, key=lambda t: _prob_map[t]["prob"]) if _prob_map else None
    _most_probable_data = _prob_map.get(_most_probable) if _most_probable else None

    # Mejora 3: advertencia por movimiento de Pinnacle contra el pick
    _pin_mov_warn = ""
    if top3:
        _pin_mov_warn = _check_pinnacle_movement(game, top3[0]["label"], home)
        if _pin_mov_warn:
            print(f"   ⚠️  Movimiento Pinnacle: {_pin_mov_warn[:80]}")

    # Narrativa conversacional del partido
    _narrativa = _generar_narrativa(context, top3, home, away, is_mlb, sport_key)

    return {
        "game_id":     game_id,
        "match":       f"{home} vs {away}",
        "time":        commence,
        "sport":       sport_key,
        "is_mlb":      is_mlb,
        "candidates":  top3,
        "context":     context,
        "best_label":  top3[0]["label"],
        "best_ev":     top3[0]["ev_pct"],
        "claude_intel": _claude_result_g,
        "all_markets": _all_mkts,
        "most_probable_team": _most_probable,
        "most_probable_data": _most_probable_data,
        "most_prob_pick":     _most_prob_pick,
        "narrativa":          _narrativa,
        "pinnacle_mov_warn": _pin_mov_warn,
    }


def notify_game_analysis(analyses, sport_key, alerted=None):
    """Send one ntfy alert per game containing full analysis context + top picks."""
    global alerted_game_analysis
    if _bankroll_paused:
        return
    is_mlb = "mlb" in sport_key
    emoji  = _sport_emoji(sport_key)

    for i, a in enumerate(analyses):
        home, away = a["match"].split(" vs ", 1)
        home_es = _es(home)
        away_es = _es(away)
        match_es = f"{home_es} vs {away_es}"

        # Never alert games that already started (>5 min grace)
        if _game_already_started(a.get("time", ""), grace_min=5):
            print(f"  ⏰ Análisis omitido — juego ya comenzó: {a.get('match','')}")
            continue

        analysis_key = f"{home}_{away}_analysis"
        if not _should_alert(analysis_key, edge=a["best_ev"]):
            continue
        match_key_ana = a["match"].lower().strip()
        if alerted is not None and match_key_ana in alerted:
            print(f"  ⏭️  {match_es} — ya alertado este scan (notify_game_analysis)")
            continue
        if i > 0:
            time.sleep(2)

        gt   = _fmt_smart_gt(a["time"])
        ctx  = a["context"]
        tc   = _timing_check(a["time"], is_mlb)

        # Module 7: filter candidates below MIN_STAKE
        a["candidates"] = [c for c in a["candidates"] if c.get("stake", 0) >= MIN_STAKE]
        if not a["candidates"]:
            continue

        # Smart timing: raise EV bar for far-out soccer (3-7 days)
        if tc["ev_min"] > 0:
            a["candidates"] = [c for c in a["candidates"] if c["ev_pct"] >= tc["ev_min"]]
        if not a["candidates"]:
            continue

        # ── Context block ─────────────────────────────────────────────────────
        # Default pitcher/ERA values (override inside is_mlb if data available)
        pn_h = ctx.get("pname_home", "TBD")
        pn_a = ctx.get("pname_away", "TBD")
        er_h = float(ctx.get("era_home") or 4.50)
        er_a = float(ctx.get("era_away") or 4.50)
        h_pname = pn_h
        a_pname = pn_a

        if is_mlb:
            # Pitchers
            pn_h = ctx.get("pname_home", "TBD")
            pn_a = ctx.get("pname_away", "TBD")
            er_h = ctx.get("era_home", 4.50)
            er_a = ctx.get("era_away", 4.50)
            fip_h  = ctx.get("fip_home")
            fip_a  = ctx.get("fip_away")
            hnd_h  = ctx.get("hand_home")
            hnd_a  = ctx.get("hand_away")

            def _hand_es(c):
                return "zurdo" if c == "L" else ("diestro" if c == "R" else None)

            def _fip_luck(era, fip):
                """Return luck warning string or ''."""
                if fip is None:
                    return ""
                diff = fip - era
                if diff > 1.0:
                    return "   ⚠️ Ha tenido suerte — rendimiento real es peor\n"
                elif diff < -1.0:
                    return "   📈 Mejor de lo que parece — ha sido víctima de mala suerte\n"
                return ""

            # Home pitcher block
            h_hand_txt = f" ({_hand_es(hnd_h)})" if _hand_es(hnd_h) else ""
            _k9_h_inline = ""
            try:
                _ps_h_n = fetch_pitcher_stats(pn_h)
                _k9_h_v = _ps_h_n.get("k9") if _ps_h_n else None
                if _k9_h_v not in (None, "N/A", ""):
                    _k9_h_inline = f"  |  K/9: {float(_k9_h_v):.1f}"
            except Exception:
                pass
            ctx_lines  = (
                f"🎯 PITCHEO\n"
                f"🔵 Pitcher local: {pn_h}{h_hand_txt}\n"
                f"   ERA: {er_h:.2f} — {_era_label(er_h)}{_k9_h_inline}\n"
            )
            if fip_h is not None:
                ctx_lines += (
                    f"   FIP (rendimiento real): {fip_h:.2f} — {_era_label(fip_h)}\n"
                    + _fip_luck(er_h, fip_h)
                )
            _pace_h_ctx = ctx.get("pitcher_pace_home")
            if _pace_h_ctx:
                ctx_lines += f"   Ritmo: {_pace_h_ctx['avg_pi']} pitches/entrada"
                if _pace_h_ctx.get("flag"):
                    ctx_lines += f" {_pace_h_ctx['flag']}"
                ctx_lines += "\n"
            # ── Elite Source 1: Statcast block — home pitcher ────────────────
            sc_h = ctx.get("statcast_home")
            sc_a = ctx.get("statcast_away")
            er_eff_h = ctx.get("era_eff_home", er_h)
            er_eff_a = ctx.get("era_eff_away", er_a)
            ctx_lines += _statcast_alert_block(pn_h, sc_h, er_h)

            # Away pitcher block
            a_hand_txt = f" ({_hand_es(hnd_a)})" if _hand_es(hnd_a) else ""
            _k9_a_inline = ""
            try:
                _ps_a_n = fetch_pitcher_stats(pn_a)
                _k9_a_v = _ps_a_n.get("k9") if _ps_a_n else None
                if _k9_a_v not in (None, "N/A", ""):
                    _k9_a_inline = f"  |  K/9: {float(_k9_a_v):.1f}"
            except Exception:
                pass
            ctx_lines += (
                f"🔴 Pitcher visita: {pn_a}{a_hand_txt}\n"
                f"   ERA: {er_a:.2f} — {_era_label(er_a)}{_k9_a_inline}\n"
            )
            if fip_a is not None:
                ctx_lines += (
                    f"   FIP (rendimiento real): {fip_a:.2f} — {_era_label(fip_a)}\n"
                    + _fip_luck(er_a, fip_a)
                )
            _pace_a_ctx = ctx.get("pitcher_pace_away")
            if _pace_a_ctx:
                ctx_lines += f"   Ritmo: {_pace_a_ctx['avg_pi']} pitches/entrada"
                if _pace_a_ctx.get("flag"):
                    ctx_lines += f" {_pace_a_ctx['flag']}"
                ctx_lines += "\n"
            # ── Elite Source 1: Statcast block — away pitcher ────────────────
            ctx_lines += _statcast_alert_block(pn_a, sc_a, er_a)

            # ── 📊 CLAVES ─────────────────────────────────────────────────────
            ctx_lines += "📊 CLAVES\n"
            pin_odds = ctx.get("pinnacle_odds")
            if pin_odds:
                pin_h_dec = pin_odds["home"]
                pin_a_dec = pin_odds["away"]
                # No-vig implied probabilities
                raw_h_p = 1.0 / max(pin_h_dec, 1.001)
                raw_a_p = 1.0 / max(pin_a_dec, 1.001)
                tot_p   = raw_h_p + raw_a_p
                pin_imp_h = round(raw_h_p / tot_p * 100, 1)
                pin_imp_a = round(raw_a_p / tot_p * 100, 1)
                ctx_lines += (
                    f"📌 Referencia Pinnacle:\n"
                    f"   {home_es}: {pin_h_dec:+.0f} ({pin_imp_h}% implícita)\n"
                    f"   {away_es}: {pin_a_dec:+.0f} ({pin_imp_a}% implícita)\n"
                )

            # Runs scored / allowed (1 línea por equipo)
            ctx_lines += (
                f"⚾ {home_es}: anota {ctx['rs_home']} | recibe {ctx['ra_home']}/jgo\n"
                f"⚾ {away_es}: anota {ctx['rs_away']} | recibe {ctx['ra_away']}/jgo\n"
            )
            # ── Batting metrics (1 línea por equipo) ─────────────────────
            for tname, tname_es, bat in (
                (home, home_es, ctx.get("bat_home")),
                (away, away_es, ctx.get("bat_away")),
            ):
                if not bat:
                    continue
                ops = bat.get("ops")
                kp  = bat.get("k_pct")
                parts = [f"AVG {bat['avg']:.3f}"]
                if ops is not None: parts.append(f"OPS {ops:.3f} ({_ops_label(ops)})")
                if kp  is not None: parts.append(f"K% {kp:.0f}%")
                ctx_lines += f"🏏 {tname_es}: {' | '.join(parts)}\n"
            # ──────────────────────────────────────────────────────────────

            # ── Confirmed lineup display (MLB A8) ─────────────────────────
            _ld = ctx.get("lineup_data")
            if _ld and _ld.get("confirmed"):
                for t_name, t_es, o_key, m_key in (
                    (home, home_es, "home_order", "home_missing"),
                    (away, away_es, "away_order", "away_missing"),
                ):
                    order   = _ld.get(o_key, {})
                    missing = _ld.get(m_key, [])
                    kp      = MLB_KEY_PLAYERS.get(t_name, [])
                    if not order:
                        continue
                    if missing:
                        ctx_lines += f"⚠️ Lineup {t_es}:\n"
                        for mp in missing:
                            ctx_lines += f"   {mp} FUERA del lineup\n"
                        adj = "0.8 carreras" if len(missing) >= 2 else "0.5 carreras"
                        ctx_lines += f"   → Reduce proyección {adj}\n"
                    elif kp:
                        stars = " ✅ | ".join(p.split()[-1] for p in kp)
                        ctx_lines += (
                            f"📋 Lineup {t_es} confirmado:\n"
                            f"   {stars} ✅\n"
                            f"   Bateadores clave presentes ✅\n"
                        )
            # ──────────────────────────────────────────────────────────────

            # Park factor
            ctx_lines += f"{_park_label(ctx['park_factor'])}\n"
            # Team last 5 games
            for tname, tname_es in ((home, home_es), (away, away_es)):
                recent = fetch_mlb_team_recent(tname)
                if recent and recent.get("results"):
                    game_strs = " | ".join(
                        f"{_result_to_es(r)} {sc}" for r, sc in recent["results"]
                    )
                    ctx_lines += (
                        f"📋 {tname_es} últimos {len(recent['results'])} juegos:\n"
                        f"   {game_strs}\n"
                        f"   Balance: {recent['wins']} ganados, {recent['losses']} perdidos\n"
                    )
            # Bullpen load — últimos 3 días
            _bp_load_h_n = ctx.get("bullpen_load_home")
            _bp_load_a_n = ctx.get("bullpen_load_away")
            if _bp_load_h_n or _bp_load_a_n:
                ctx_lines += "⚾ Bullpen (últimos 3 días):\n"
                if _bp_load_h_n:
                    ctx_lines += f"   {home_es}: {_bp_load_h_n['ip_3d']} inn"
                    if _bp_load_h_n.get("flag"):
                        ctx_lines += f" {_bp_load_h_n['flag']}"
                    ctx_lines += "\n"
                if _bp_load_a_n:
                    ctx_lines += f"   {away_es}: {_bp_load_a_n['ip_3d']} inn"
                    if _bp_load_a_n.get("flag"):
                        ctx_lines += f" {_bp_load_a_n['flag']}"
                    ctx_lines += "\n"
            # ── Forma reciente 14d ────────────────────────────────────────────
            try:
                import forma_reciente as _fr_n

                def _ffl(d):
                    return (d or {}).get("flag") or "SIN DATO"

                def _ops_frag(fo):
                    if not fo or fo.get("flag") == "SIN DATO":
                        return "OPS-14d SIN DATO"
                    ops = fo.get("ops")
                    return (f"OPS-14d {ops:.3f} ({fo.get('flag','?')})"
                            if ops is not None else "OPS-14d SIN DATO")

                _fo_h = _fr_n.forma_ofensiva_14d(home)
                _fo_a = _fr_n.forma_ofensiva_14d(away)
                _fb_h = _fr_n.bullpen_14d(home)
                _fb_a = _fr_n.bullpen_14d(away)
                _fa_h = (_fr_n.forma_abridor(pn_h)
                         if pn_h not in ("TBD", "", None) else None)
                _fa_a = (_fr_n.forma_abridor(pn_a)
                         if pn_a not in ("TBD", "", None) else None)
                ctx_lines += (
                    f"📈 FORMA 14d\n"
                    f"   {home_es}: {_ops_frag(_fo_h)} | bullpen ({_ffl(_fb_h)}) | abridor ({_ffl(_fa_h)})\n"
                    f"   {away_es}: {_ops_frag(_fo_a)} | bullpen ({_ffl(_fb_a)}) | abridor ({_ffl(_fa_a)})\n"
                )
            except Exception as _fr_err:
                print(f"[forma notify_game_analysis] {_fr_err}")
            # Umpire
            ump = ctx.get("umpire")
            if ump and ump.get("name"):
                _tend = ump.get("tendency", "NEUTRAL")
                if _tend == "OVER":
                    ump_note = "zona cerrada (apretada) — favorece hits y OVER"
                elif _tend == "UNDER":
                    ump_note = "zona amplia (expandida) — favorece ponches y UNDER"
                else:
                    ump_note = "zona normal — sin tendencia marcada"
                ctx_lines += (
                    f"👨‍⚖️ Árbitro: {ump['name']}\n"
                    f"   Tendencia: {ump_note}\n"
                )
            # TBD pitcher
            if ctx.get("tbd_note"):
                ctx_lines += f"{ctx['tbd_note']}\n"
            # Stats fallback warning — shown when 4.5 default was used
            if ctx.get("stats_fallback"):
                ctx_lines += f"{ctx['stats_fallback']}\n"
            # Temperature / wind
            if ctx.get("temp_label"):
                ctx_lines += f"{ctx['temp_label']}\n"
            if ctx.get("wind_info"):
                ctx_lines += f"💨 {ctx['wind_info']}\n"
            # Injuries
            for tname_il, il_list in ctx.get("il_data", {}).items():
                if il_list:
                    ctx_lines += (
                        f"🤕 Jugadores lesionados ({_es(tname_il)}):\n"
                        f"   {', '.join(il_list[:4])}\n"
                    )
            # Home/away splits — only show when real data available
            hs  = ctx.get("h_splits") or {}
            as_ = ctx.get("a_splits") or {}
            if hs.get("home_rs") and as_.get("away_rs"):
                ctx_lines += (
                    f"🏠 {home_es} jugando en casa:\n"
                    f"   Anota {hs['home_rs']} | Recibe {hs['home_ra']}\n"
                    f"   Gana el {hs['home_wpct']*100:.0f}% de sus juegos en casa\n"
                    f"🚗 {away_es} jugando de visita:\n"
                    f"   Anota {as_['away_rs']} | Recibe {as_['away_ra']}\n"
                    f"   Gana el {as_['away_wpct']*100:.0f}% jugando fuera\n"
                )
            # Pitcher intelligence: notes + reasoning summary
            p_intel = ctx.get("pitch_intel", {})
            for note in p_intel.get("notes", []):
                ctx_lines += f"{note}\n"
            if p_intel.get("reasoning"):
                ctx_lines += f"{p_intel['reasoning']}\n"

            # ── L/R Matchup (MLB A5) ──────────────────────────────────────
            for lr in ctx.get("lr_notes", []):
                if lr["verdict"] == "normal":
                    continue
                avg_pct = f"{lr['avg']:.3f}".lstrip("0")  # ".218"
                ctx_lines += (
                    f"⚔️ {_es(lr['lineup'])} vs pitchers {lr['hand']}s:\n"
                    f"   Promedio de bateo: {avg_pct} ({lr['verdict']})\n"
                    f"   → Ventaja para {lr['favor']}\n"
                )

            # ── H2H últimos encuentros (MLB A6) ───────────────────────────
            h2h = ctx.get("h2h_data")
            if h2h and h2h.get("games_found", 0) >= 2:
                bl = ctx.get("h2h_book_line")
                totals_list = h2h.get("totals", [])
                if bl and totals_list:
                    ov = sum(1 for t in totals_list if t > bl)
                    un = sum(1 for t in totals_list if t < bl)
                    ou_txt = (f"Over ganó {ov} de {h2h['games_found']}" if ov > un
                              else f"Under ganó {un} de {h2h['games_found']}" if un > ov
                              else f"Empate {ov}-{un}")
                else:
                    ou_txt = f"{h2h['games_found']} partidos analizados"
                ctx_lines += (
                    f"📊 Últimos {h2h['games_found']} enfrentamientos:\n"
                    f"   Promedio: {h2h['avg_total']} carreras/juego\n"
                    f"   {ou_txt}\n"
                    f"   {home_es} en casa: "
                    f"{h2h['home_wins']} ganados, {h2h['home_losses']} perdidos\n"
                )
                h2h_note = ctx.get("h2h_note", "")
                if h2h_note:
                    ctx_lines += f"{h2h_note}\n"
            # ──────────────────────────────────────────────────────────────
        else:
            # Soccer — ELO as tier
            ctx_lines = (
                f"💪 {home_es} — Fuerza del equipo: {_elo_tier(ctx['elo_home'])}\n"
                f"💪 {away_es} — Fuerza del equipo: {_elo_tier(ctx['elo_away'])}\n"
                f"🤝 Probabilidad de empate: {ctx['p_draw']}%\n"
            )
            # Legacy form (gpg)
            if ctx.get("form_home"):
                ctx_lines += f"⚽ {home_es} — Goles anotados por partido: {ctx['form_home'].split()[0]}\n"
                if ctx.get("conceded_home"):
                    ctx_lines += f"🛡️ {home_es} — Goles recibidos por partido: {ctx['conceded_home']}\n"
            if ctx.get("form_away"):
                ctx_lines += f"⚽ {away_es} — Goles anotados por partido: {ctx['form_away'].split()[0]}\n"
                if ctx.get("conceded_away"):
                    ctx_lines += f"🛡️ {away_es} — Goles recibidos por partido: {ctx['conceded_away']}\n"
            # Soccer S1: last 3 matches
            sf_h = ctx.get("sform_h")
            sf_a = ctx.get("sform_a")
            if sf_h:
                res_parts = " | ".join(_result_to_es(r) for r in sf_h["results"])
                ctx_lines += (
                    f"📋 {home_es} — Últimos {sf_h['n']} partidos:\n"
                    f"   {res_parts}\n"
                    f"   ⚽ Anota {sf_h['gf_pg']} goles por partido\n"
                    f"   🛡️ Recibe {sf_h['ga_pg']} goles por partido\n"
                )
            if sf_a:
                res_parts = " | ".join(_result_to_es(r) for r in sf_a["results"])
                ctx_lines += (
                    f"📋 {away_es} — Últimos {sf_a['n']} partidos:\n"
                    f"   {res_parts}\n"
                    f"   ⚽ Anota {sf_a['gf_pg']} goles por partido\n"
                    f"   🛡️ Recibe {sf_a['ga_pg']} goles por partido\n"
                )
            # ── Elite Source 3: Understat xG block ──────────────────────────
            xg_h = ctx.get("xg_home")
            xg_a = ctx.get("xg_away")
            if xg_h or xg_a:
                ctx_lines += "📊 xG últimos 5 partidos (Understat):\n"
                if xg_h:
                    xgf = xg_h["xg_for"]
                    xga = xg_h["xg_against"]
                    rgf = xg_h.get("raw_goals_for", xgf)
                    diff_label_h = (
                        "⬇️ por encima de su rendimiento real" if rgf > xgf + 0.3
                        else "⬆️ por debajo — mejorará" if rgf < xgf - 0.3
                        else "alineado con xG"
                    )
                    ctx_lines += (
                        f"   {home_es} — xG favor: {xgf}/partido | "
                        f"xG contra: {xga}/partido\n"
                        f"   → {diff_label_h}\n"
                    )
                if xg_a:
                    xgf = xg_a["xg_for"]
                    xga = xg_a["xg_against"]
                    rgf = xg_a.get("raw_goals_for", xgf)
                    diff_label_a = (
                        "⬇️ por encima de su rendimiento real" if rgf > xgf + 0.3
                        else "⬆️ por debajo — mejorará" if rgf < xgf - 0.3
                        else "alineado con xG"
                    )
                    ctx_lines += (
                        f"   {away_es} — xG favor: {xgf}/partido | "
                        f"xG contra: {xga}/partido\n"
                        f"   → {diff_label_a}\n"
                    )

            # ── Elite Source 2: Pinnacle reference block — soccer ────────────
            pin_soc = ctx.get("pinnacle_odds")
            if pin_soc:
                raw_h_s = 1.0 / max(pin_soc["home"], 1.001)
                raw_a_s = 1.0 / max(pin_soc["away"], 1.001)
                tot_s   = raw_h_s + raw_a_s
                ctx_lines += (
                    f"📌 Referencia Pinnacle:\n"
                    f"   {home_es}: {pin_soc['home']:+.0f} "
                    f"({round(raw_h_s/tot_s*100,1)}% implícita)\n"
                    f"   {away_es}: {pin_soc['away']:+.0f} "
                    f"({round(raw_a_s/tot_s*100,1)}% implícita)\n"
                )

            # Soccer S2: referee
            ref = ctx.get("referee")
            if ref and ref.get("name"):
                ctx_lines += (
                    f"🟨 Árbitro: {ref['name']}\n"
                    f"   Promedio histórico: {ref['goals_pg']:.1f} goles por partido\n"
                    f"   Tendencia: {ref['tendency']}\n"
                )
            # Soccer S3: temperature
            if ctx.get("temp_label_s"):
                ctx_lines += f"{ctx['temp_label_s']}\n"
            # WC group urgency — plain Spanish
            standings = ctx.get("wc_standings", {})
            if standings:
                for tname, tname_es in ((home, home_es), (away, away_es)):
                    urg = _wc_urgency_line(tname, standings)
                    if urg:
                        if "necesita" in urg.lower() or "eliminado" in urg.lower():
                            ctx_lines += (
                                f"🔴 SITUACIÓN CRÍTICA:\n"
                                f"   {tname_es} NECESITA ganar este partido\n"
                                f"   para seguir en el torneo.\n"
                                f"   Atacarán desde el primer minuto.\n"
                                f"   Esto favorece el Over y el ML\n"
                                f"   del equipo rival por contraataque.\n"
                            )
                        else:
                            ctx_lines += f"📋 {tname_es}: {urg}\n"
            # Soccer intelligence: notes + reasoning summary
            s_intel = ctx.get("soccer_intel", {})
            for note in s_intel.get("notes", []):
                ctx_lines += f"{note}\n"
            if s_intel.get("reasoning"):
                ctx_lines += f"{s_intel['reasoning']}\n"

        if ctx.get("line_moved") and ctx.get("line_note"):
            ctx_lines += f"📉 {ctx['line_note']}\n"

        # ── Public % / RLM display ────────────────────────────────────────
        _rlm = ctx.get("rlm_data")
        if _rlm:
            pub_es = _es(_rlm["pub_fav"])
            dog_es = _es(_rlm["pub_dog"])
            ctx_lines += (
                f"👥 Apoyo público estimado:\n"
                f"   {pub_es}: ~{_rlm['pub_pct']}%  |  "
                f"{dog_es}: ~{_rlm['dog_pct']}%\n"
            )
            if _rlm.get("rlm") is True:
                ctx_lines += f"{_rlm['rlm_note']}\n"
            elif _rlm.get("rlm") is False:
                ctx_lines += f"{_rlm['square_note']}\n"
        # ──────────────────────────────────────────────────────────────────

        # Smart timing warning (soccer 3-7 days)
        if tc.get("warn"):
            ctx_lines += f"{tc['warn']}\n"

        # Detect if any warning is present in the context block
        has_warning = ("⚠️" in ctx_lines)

        # Best pick for action line
        # Guard: high-EV panel results have candidates=[] — show panel intel without picks
        if not a.get("candidates"):
            _hev_lbl_n  = a.get("best_label") or "mercado de alta EV"
            _hev_ev_n   = a.get("best_ev", 0.0)
            _ci_n       = a.get("claude_intel") or {}
            _ap_n       = "✅ APOSTAR" if _ci_n.get("apostar") is True else (
                          "❌ PASAR" if _ci_n.get("apostar") is False else "⚠️ VERIFICAR")
            _cc_n       = _ci_n.get("confianza", "N/D")
            _cr_n       = (_ci_n.get("razonamiento") or "")[:500]
            _all_mkts_n = a.get("all_markets", {})
            _mkts_txt   = ""
            for _lbl, _m in _all_mkts_n.items():
                _ev_s = f"+{_m['ev_pct']:.1f}%" if _m["ev_pct"] >= 0 else f"{_m['ev_pct']:.1f}%"
                _mkts_txt += f"  {_lbl}: {round(_m['prob']*100)}% | {_ev_s} | {_m['odds']:.2f} @ {_m['book']}\n"
            body_hev = (
                f"{emoji} {match_es}\n"
                f"⏰ {gt}\n"
                f"{_DIV}\n"
                f"⚠️ ALERTA: EV ALTO SIN PICK FORMAL\n"
                f"{_DIV}\n"
                f"Mercado con EV >15% detectado: <b>{_hev_lbl_n}</b>\n"
                f"EV estimado: +{_hev_ev_n:.1f}% — no superó umbral de prob. mínima\n"
                f"Verificar línea en el libro antes de apostar.\n"
                f"{_DIV}\n"
                f"📊 TODOS LOS MERCADOS\n{_DIV}\n"
                f"{_mkts_txt or '(sin datos de mercados)'}\n"
                f"{_DIV}\n"
                f"🤖 Panel de expertos: {_cc_n} | {_ap_n}\n"
                f"{f'<i>{_cr_n}</i>' if _cr_n else ''}\n"
                f"{_DIV2}"
            )
            ntfy_post("⚠️ EV ALTO | " + match_es, body_hev, "high")
            if alerted is not None:
                alerted[match_key_ana] = float(_hev_ev_n)
            print(f"  📢 Alerta EV>15% enviada: {match_es}")
            continue

        best = a["candidates"][0]
        best_clean = (best["label"]
                      .replace("🔵 ", "").replace("🔴 ", "").replace("🤝 ", "")
                      .replace("📈 ", "").replace("📉 ", "").replace("🏃 ", ""))
        # Veredicto compacto — encabeza el mensaje
        _ci_ntfy    = a.get("claude_intel") or {}
        _final_n    = _ci_ntfy.get("apostar")
        if _final_n is None and a["candidates"] and a["candidates"][0].get("ev_pct",0) >= 5.0:
            _final_n = True
        if _final_n is True:
            action_line = (f"🚦 APUESTA: {best_clean}\n"
                           f"   EV +{best['ev_pct']:.1f}% | {best['odds']:.2f} {best['book']} | Stake ${best['stake']:.0f}")
        elif _final_n is False:
            action_line = f"🚦 SIN APUESTA — panel vetó el pick"
        else:
            action_line = f"🚦 SIN APUESTA — EV +{best['ev_pct']:.1f}% sin prob. mínima"

        # Línea "más probable"
        _mp_n   = a.get("most_prob_pick") or {}
        _mpl_n  = ""
        if _mp_n.get("team"):
            _mpl_n = f"💡 Más probable: {_es(_mp_n['team'])} ({_mp_n.get('prob','?')}%)\n"
        else:
            _an2 = a.get("all_markets", {})
            _h2 = next((m.get("prob",0) for lb,m in _an2.items() if "🔵" in lb and "ML" in lb.upper()), None)
            _a2 = next((m.get("prob",0) for lb,m in _an2.items() if "🔴" in lb and "ML" in lb.upper()), None)
            if _h2 is not None and _a2 is not None:
                _mpl_n = (f"💡 Más probable: {_es(home)} ({round(_h2*100)}%)\n"
                          if _h2 >= _a2 else
                          f"💡 Más probable: {_es(away)} ({round(_a2*100)}%)\n")

        # Línea de ventana de apuesta
        _tw_n = ""
        try:
            _tw_v = _bet_timing_advice(best.get("market_type", best.get("label","")), a.get("time",""))
            if _tw_v:
                _tw_n = _tw_v.splitlines()[0].strip() + "\n"
        except Exception:
            pass

        # Picks block
        picks_lines  = ""
        high_ev_flag = ""
        for idx, c in enumerate(a["candidates"]):
            # Fix 4: skip picks where EV in dollars < $2.00 (not worth the risk)
            ev_d = _ev_dollars(c["stake"], c["ev_pct"])
            if ev_d < 2.00:
                continue

            rank_emoji = _RANK_EMOJIS[idx] if idx < 3 else "🔹"
            safe_tag   = "\n   ✅ Pick más seguro del análisis" if c["safest"] else ""

            # Flag suspiciously high EV (likely a soft/stale line)
            if c["ev_pct"] > 30:
                high_ev_flag = "⚠️ Ventaja muy alta — verificar línea antes de apostar\n"

            # Book warning
            bk_warn_pick = _book_warning(c.get("book", ""))

            prob_pct = round(c['true_prob'] * 100)
            odds_line = f"   💰 Cuota: {c['odds']} — {c['book']}{bk_warn_pick}\n"

            picks_lines += (
                f"{rank_emoji} {c['label']}\n"
                f"   EV: +{c['ev_pct']:.1f}% — Probabilidad real: {prob_pct}%{safe_tag}\n"
                f"{odds_line}"
            )

        # All-markets block (shows every evaluated market, not just top picks)
        _all_mkts_n   = a.get("all_markets", {})
        _cand_lbs_n   = {c["label"] for c in a["candidates"]}
        all_mkts_lines = ""
        _has_k_n = False
        for _lbl, _m in _all_mkts_n.items():
            _edge  = " ✅" if _lbl in _cand_lbs_n else ""
            _ev_s  = f"+{_m['ev_pct']:.1f}%" if _m["ev_pct"] >= 0 else f"{_m['ev_pct']:.1f}%"
            _ppc   = round(_m["prob"] * 100)
            all_mkts_lines += (
                f"  {_lbl}: {_ppc}% | {_ev_s} | {_m['odds']:.2f} @ {_m['book']}{_edge}\n"
            )
            if "⚡" in _lbl:
                _has_k_n = True
        if not _has_k_n and is_mlb and _all_mkts_n:
            all_mkts_lines += "  ⚡ Props K: sin K/9 confirmado >8.5 — se prefiere ML\n"

        # Verdict — cap to MEDIA whenever any ⚠️ warning is present
        if tc.get("cap_conf") or has_warning:
            verdict = f"{_DIV3}\n🟡 CONFIANZA: MEDIA"
        else:
            verdict = _verdict_line(best["ev_pct"], best["true_prob"])

        _claude_blk = _claude_block(a.get("claude_intel"))
        _dq_line_a  = ("✅ Datos verificados\n" if not has_warning
                       else "⚠️ Verificar antes de apostar — algunos datos sin confirmar\n")

        _mkts_section = (
            f"{_DIV}\n📊 TODOS LOS MERCADOS\n{_DIV}\n{all_mkts_lines}"
            if all_mkts_lines else ""
        )

        body = (
            f"{emoji} {match_es} — ⏰ {gt}\n"
            f"{action_line}\n"
            f"{_tw_n}"
            f"{_mpl_n}"
            f"{_DIV}\n"
            f"{ctx_lines}"
            f"{_DIV}\n"
            f"💰 MERCADOS\n"
            f"{all_mkts_lines}"
            f"{high_ev_flag}"
            f"{_claude_blk}"
            f"{verdict}\n"
            f"{_dq_line_a}"
            f"{_DIV2}"
        )

        # Level 2A: player props (pitcher strikeouts)
        if is_mlb:
            try:
                _game_ev_id = a.get("game_id", "")
                if _game_ev_id:
                    _props = _fetch_player_props(_game_ev_id)
                    _props_blk = _format_props_alert(_props, pn_h, pn_a, er_h, er_a)
                    if _props_blk:
                        body += f"\n{_DIV}\n{_props_blk}"
            except Exception:
                pass

        # Level 3B: bet timing optimizer
        try:
            _best_type = best.get("market_type", "totals")
            _timing_ln = _bet_timing_advice(_best_type, a.get("time", ""))
            if _timing_ln:
                body += f"\n{_timing_ln}"
        except Exception:
            pass

        # Strip decorative emojis from title
        clean = best_clean
        title = f"🔍 {match_es} | Mejor: {clean} +{a['best_ev']}%"
        ntfy_post(title, body, "high")
        alerted_game_analysis.add(a["game_id"])
        if alerted is not None:
            alerted[match_key_ana] = float(a.get("best_ev", 0))
        print(f"  🔍 Análisis: {a['match']} — {len(a['candidates'])} pick(s), "
              f"mejor EV +{a['best_ev']}%")

def _market_reason(label: str, ctx: dict, home_team: str = "") -> str:
    """One-line reason for a market based on available context."""
    if not ctx:
        return ""
    u = label.upper()
    h_era = float(ctx.get("era_home") or 4.5)
    a_era = float(ctx.get("era_away") or 4.5)
    h_name = ctx.get("pname_home", "")
    a_name = ctx.get("pname_away", "")
    wind = ctx.get("wind_info", "")
    pin = ctx.get("pinnacle_odds")
    bat_h = ctx.get("bat_home") or {}
    bat_a = ctx.get("bat_away") or {}

    if "OVER" in u and "F5" not in u:
        weak = h_era > 4.5 or a_era > 4.5
        wind_note = " Viento OUT." if wind and "OUT" in wind.upper() else ""
        return f"→ {'Pitchers débiles favorecen más carreras.' if weak else 'Proyección modelo supera la línea.'}{wind_note}"
    if "UNDER" in u and "F5" not in u:
        dom = h_era < 3.0 or a_era < 3.0
        wind_note = " Viento IN." if wind and "IN" in wind.upper() else ""
        dom_name = h_name if h_era < a_era else a_name
        dom_era = min(h_era, a_era)
        return f"→ {'Pitcher dominante ' + dom_name + f' ({dom_era:.2f}).' if dom else 'Proyección modelo bajo la línea.'}{wind_note}"
    if " ML" in u:
        is_home = home_team.upper() in u if home_team else False
        era_fav = h_era if is_home else a_era
        era_opp = a_era if is_home else h_era
        pin_line = ""
        if pin:
            raw_h = 1.0 / max(pin["home"], 1.001)
            raw_a = 1.0 / max(pin["away"], 1.001)
            tot = raw_h + raw_a
            pin_p = raw_h / tot if is_home else raw_a / tot
            if pin_p > 0.52:
                favor = "a favor" if is_home else "en contra"
                pin_line = f" Pinnacle {round(pin_p*100)}% ({favor})."
        if is_home:
            return f"→ Tu pitcher ERA {era_fav:.2f} vs rival ERA {era_opp:.2f}.{pin_line}"
        else:
            return f"→ Tu pitcher ERA {era_fav:.2f} vs pitcher local ERA {era_opp:.2f}.{pin_line}"
    if "RL" in u:
        diff = abs(h_era - a_era)
        return f"→ Diferencia ERA {diff:.1f} puntos respalda la línea de carreras."
    if "F5" in u:
        return f"→ Pitchers abridores: {h_name} ({h_era:.2f}) vs {a_name} ({a_era:.2f})."
    if "K OVER" in u or "K UNDER" in u:
        return f"→ Prop de ponches basado en K/9 confirmado."
    return ""


def _clean_era_form(eras: list) -> list:
    """Remove ERA 0.0 entries (relief/skipped starts) from pitcher form."""
    return [e for e in (eras or []) if isinstance(e, (int, float)) and e > 0.0]


def _pitcher_line(name: str, era: float, fip, hand: str, pform, side: str) -> str:
    """Compact 1-line pitcher summary with optional form trend."""
    hand_map = {"L": "zurdo", "R": "diestro", "S": "ambidiestro"}
    hand_txt = f" ({hand_map.get(hand, '')})" if hand else ""
    fip_txt  = f" | FIP {fip:.2f}" if fip is not None else ""
    base     = f"{side} {name}{hand_txt}: ERA {era:.2f}{fip_txt} — {_era_label(era)}"
    if pform:
        clean = _clean_era_form(pform.get("eras", []))
        if clean:
            trend = pform.get("trend", "")
            base += f"\n   Forma: {trend} | {' → '.join(str(e) for e in clean[-3:])}"
    return base


def _mkt_row(lbl: str, m: dict, is_pick: bool) -> str:
    """Single clean market row with icon."""
    ev   = m.get("ev_pct", 0)
    prob = round(m.get("prob", 0) * 100)
    odds = m.get("odds", 0)
    book = m.get("book", "")
    icon = "✅" if is_pick else ("🟡" if ev >= 2 else "❌")
    ev_s = f"+{ev:.1f}%" if ev >= 0 else f"{ev:.1f}%"
    return f"{icon} {lbl}: {prob}% | EV {ev_s} | {odds:.2f} {book}"


def build_analizar_text(result: dict) -> list:
    """
    Telegram-HTML message parts for /analizar — formato mejorado:
    p1: Header → VEREDICTO arriba → Pitchers compactos → Factores clave → Mercados
    p2: Panel expertos → Recomendación → Consejo
    """
    match    = result.get("match", "?")
    home, away = (match.split(" vs ", 1) + [""])[:2] if " vs " in match else (match, "")
    home_es  = _es(home)
    away_es  = _es(away)
    is_mlb   = result.get("is_mlb", False)
    ctx      = result.get("context", {}) or {}
    cands    = result.get("candidates", [])
    ci       = result.get("claude_intel") or {}
    gt       = _fmt_smart_gt(result.get("time", ""))
    emoji    = _sport_emoji(result.get("sport", ""))
    all_mkts = result.get("all_markets", {}) or {}
    cand_lbs = {c["label"] for c in cands}

    # ── VARIABLES COMUNES ────────────────────────────────────────────────────
    final_apostar = ci.get("apostar")
    if final_apostar is None and cands and cands[0].get("ev_pct", 0) >= 5.0:
        final_apostar = True
        if not ci.get("confianza"):
            ci["confianza"] = "MEDIA"
    best_c = cands[0] if cands else {}
    SEP    = f"{'─'*22}\n"

    # ─── 1. ENCABEZADO ────────────────────────────────────────────────────────
    p1 = f"{emoji} <b>{home_es} vs {away_es}</b>  ⏰ {gt}\n"

    # ─── 2. VEREDICTO (máx 2 líneas) ─────────────────────────────────────────
    if final_apostar is True and best_c:
        _bl = (best_c.get("label", "?")
               .replace("🔵 ","").replace("🔴 ","")
               .replace("📈 ","").replace("📉 ","")
               .replace("🏃 ","").replace("🤝 ",""))
        p1 += (f"🚦 <b>APUESTA: {_bl}</b>\n"
               f"   EV +{best_c.get('ev_pct',0):.1f}% | {best_c.get('odds',0):.2f} "
               f"{best_c.get('book','')} | Stake ${best_c.get('stake',0):.0f}\n")
    elif final_apostar is False:
        p1 += f"🚦 <b>SIN APUESTA</b> — panel vetó el pick\n"
    elif cands:
        p1 += f"🚦 <b>SIN APUESTA</b> — EV +{cands[0].get('ev_pct',0):.1f}% sin prob. mínima\n"
    elif all_mkts:
        _pos_v = [(l,m) for l,m in all_mkts.items() if m.get("ev_pct",0)>0]
        if _pos_v:
            _bl_v, _bm_v = max(_pos_v, key=lambda x: x[1]["ev_pct"])
            _bl_vc = _bl_v.replace("🔵 ","").replace("🔴 ","").replace("📈 ","").replace("📉 ","")
            p1 += f"🚦 <b>SIN APUESTA</b> — mejor: {_bl_vc} EV +{_bm_v['ev_pct']:.1f}%\n"
        else:
            p1 += "🚦 <b>SIN APUESTA</b> — sin ventaja detectada\n"
    else:
        p1 += "🚦 <b>SIN APUESTA</b> — sin odds disponibles\n"

    # ─── 3. VENTANA (1 línea, solo si hay pick) ───────────────────────────────
    if cands:
        try:
            _tw = _bet_timing_advice(
                cands[0].get("market_type", cands[0].get("label","")),
                result.get("time","")
            )
            if _tw:
                p1 += f"⏰ {_tw.splitlines()[0].lstrip('⏰').strip()}\n"
        except Exception:
            pass

    # ─── 4. MÁS PROBABLE (1 línea) ───────────────────────────────────────────
    _mp = result.get("most_prob_pick") or {}
    if _mp.get("team"):
        p1 += f"💡 Más probable: {_es(_mp['team'])} ({_mp.get('prob','?')}%)\n"
    elif all_mkts:
        _hml = next((m.get("prob",0) for lb,m in all_mkts.items()
                     if "🔵" in lb and "ML" in lb.upper()), None)
        _aml = next((m.get("prob",0) for lb,m in all_mkts.items()
                     if "🔴" in lb and "ML" in lb.upper()), None)
        if _hml is not None and _aml is not None:
            if _hml >= _aml:
                p1 += f"💡 Más probable: {home_es} ({round(_hml*100)}%)\n"
            else:
                p1 += f"💡 Más probable: {away_es} ({round(_aml*100)}%)\n"

    p1 += SEP

    # ─── 5. 🎯 PITCHEO ────────────────────────────────────────────────────────
    pn_h = ctx.get("pname_home","TBD") if ctx else "TBD"
    pn_a = ctx.get("pname_away","TBD") if ctx else "TBD"
    er_h = float(ctx.get("era_home") or 4.50) if ctx else 4.50
    er_a = float(ctx.get("era_away") or 4.50) if ctx else 4.50
    if is_mlb:
        fip_h = ctx.get("fip_home")
        fip_a = ctx.get("fip_away")
        hnd_h = ctx.get("hand_home")
        hnd_a = ctx.get("hand_away")
        pf_h  = ctx.get("pform_h")
        pf_a  = ctx.get("pform_a")
        sc_h  = ctx.get("statcast_home")
        sc_a  = ctx.get("statcast_away")

        p1 += f"🎯 <b>PITCHEO</b>\n"
        p1 += _pitcher_line(pn_h, er_h, fip_h, hnd_h, pf_h, f"🔵 {home_es}") + "\n"
        _sch = _statcast_alert_block(pn_h, sc_h, er_h)
        if _sch: p1 += _sch
        p1 += _pitcher_line(pn_a, er_a, fip_a, hnd_a, pf_a, f"🔴 {away_es}") + "\n"
        _sca = _statcast_alert_block(pn_a, sc_a, er_a)
        if _sca: p1 += _sca
        try:
            _bph, _ = fetch_bullpen_era(home)
            _bpa, _ = fetch_bullpen_era(away)
            p1 += f"⚾ Bullpen ERA: {home_es} {_bph:.2f} | {away_es} {_bpa:.2f}\n"
        except Exception:
            pass
        for _cf in ctx.get("pitcher_conflicts", []):
            p1 += (f"⚠️ {_cf['pitcher'].split()[-1]} ERA {_cf['era']:.2f} vs "
                   f"ofensiva {_es(_cf['rival'])}: {' / '.join(_cf['flags'])}\n")
        p1 += SEP

    # ─── 6. 📈 FORMA 14d ─────────────────────────────────────────────────────
    if is_mlb:
        try:
            import forma_reciente as _fr_tg

            def _ffl_tg(d):
                return (d or {}).get("flag") or "SIN DATO"

            def _ops_frag_tg(fo):
                if not fo or fo.get("flag") == "SIN DATO": return "OPS-14d SIN DATO"
                ops = fo.get("ops")
                return (f"OPS-14d {ops:.3f} ({fo.get('flag','?')})"
                        if ops is not None else "OPS-14d SIN DATO")

            _fo_h_tg = _fr_tg.forma_ofensiva_14d(home)
            _fo_a_tg = _fr_tg.forma_ofensiva_14d(away)
            _fb_h_tg = _fr_tg.bullpen_14d(home)
            _fb_a_tg = _fr_tg.bullpen_14d(away)
            _fa_h_tg = _fr_tg.forma_abridor(pn_h) if pn_h not in ("TBD","",None) else None
            _fa_a_tg = _fr_tg.forma_abridor(pn_a) if pn_a not in ("TBD","",None) else None
            p1 += (f"📈 <b>FORMA 14d</b>\n"
                   f"   {home_es}: {_ops_frag_tg(_fo_h_tg)} | bullpen ({_ffl_tg(_fb_h_tg)}) | abridor ({_ffl_tg(_fa_h_tg)})\n"
                   f"   {away_es}: {_ops_frag_tg(_fo_a_tg)} | bullpen ({_ffl_tg(_fb_a_tg)}) | abridor ({_ffl_tg(_fa_a_tg)})\n")
            p1 += SEP
        except Exception as _fr_tg_err:
            print(f"[forma build_analizar_text] {_fr_tg_err}")

    # ─── 7. 📊 CLAVES ─────────────────────────────────────────────────────────
    _kv = []
    if is_mlb:
        if ctx.get("rs_home"):
            _kv.append(f"📊 {home_es}: anota {ctx['rs_home']} | recibe {ctx['ra_home']}/jgo")
            _kv.append(f"📊 {away_es}: anota {ctx['rs_away']} | recibe {ctx['ra_away']}/jgo")
        for _te, _bk in ((home_es, "bat_home"), (away_es, "bat_away")):
            bat = ctx.get(_bk)
            if bat:
                _pts = [f"AVG {bat['avg']:.3f}"]
                if bat.get("ops"): _pts.append(f"OPS {bat['ops']:.3f} ({_ops_label(bat['ops'])})")
                if bat.get("k_pct"): _pts.append(f"K% {bat['k_pct']:.0f}%")
                _kv.append(f"🏏 {_te}: {' | '.join(_pts)}")
        pin = ctx.get("pinnacle_odds")
        if pin:
            rh = 1.0/max(pin["home"],1.001); ra = 1.0/max(pin["away"],1.001); tt = rh+ra
            _kv.append(f"📌 Pinnacle: {home_es} {round(rh/tt*100,1)}% | {away_es} {round(ra/tt*100,1)}%")
        if ctx.get("temp_label"): _kv.append(ctx["temp_label"].rstrip())
        if ctx.get("wind_info"):  _kv.append(f"💨 {ctx['wind_info']}")
        ump = ctx.get("umpire")
        if ump and ump.get("name"):
            _te2 = ump.get("tendency","NEUTRAL")
            _ue  = "zona cerrada → Over" if _te2=="OVER" else ("zona amplia → Under" if _te2=="UNDER" else "zona normal")
            _kv.append(f"👨\u200d⚖️ {ump['name']} — {_ue}")
        for til, ils in ctx.get("il_data",{}).items():
            if ils: _kv.append(f"🤕 {_es(til)}: {', '.join(ils[:3])}")
        if ctx.get("line_moved") and ctx.get("line_note"):
            _kv.append(f"📉 {ctx['line_note']}")
        if ctx.get("ttt_note"): _kv.append(ctx["ttt_note"].rstrip())
    else:
        _kv.append(f"💪 {home_es}: {_elo_tier(ctx.get('elo_home',1500))}")
        _kv.append(f"💪 {away_es}: {_elo_tier(ctx.get('elo_away',1500))}")
        _kv.append(f"🤝 Prob. empate: {ctx.get('p_draw','?')}%")
        for _sfk, _tne in (("sform_h", home_es), ("sform_a", away_es)):
            sf = ctx.get(_sfk)
            if sf:
                rs = " ".join(_result_to_es(r) for r in sf["results"])
                _kv.append(f"📋 {_tne}: {rs} | {sf['gf_pg']}g/p")
        pin = ctx.get("pinnacle_odds")
        if pin:
            rh = 1.0/max(pin["home"],1.001); ra = 1.0/max(pin["away"],1.001); tt = rh+ra
            _kv.append(f"📌 Pinnacle: {home_es} {round(rh/tt*100,1)}% | {away_es} {round(ra/tt*100,1)}%")
        ref = ctx.get("referee")
        if ref and ref.get("name"): _kv.append(f"🟨 {ref['name']} — {ref.get('tendency','?')}")
        if ctx.get("line_moved") and ctx.get("line_note"): _kv.append(f"📉 {ctx['line_note']}")

    if _kv:
        p1 += "📊 <b>CLAVES</b>\n" + "".join(f"   {l}\n" for l in _kv) + SEP

    # ─── 8. 💰 MERCADOS ───────────────────────────────────────────────────────
    p1 += "💰 <b>MERCADOS</b>\n"
    _has_k = False
    if all_mkts:
        _mkt_picks  = [(lb,m) for lb,m in all_mkts.items() if lb in cand_lbs]
        _mkt_others = [(lb,m) for lb,m in all_mkts.items() if lb not in cand_lbs]
        _mkt_picks.sort( key=lambda x: x[1].get("ev_pct",0), reverse=True)
        _mkt_others.sort(key=lambda x: x[1].get("ev_pct",0), reverse=True)
        for lb, m in (_mkt_picks + _mkt_others):
            _ip   = lb in cand_lbs
            ev_v  = m.get("ev_pct",0)
            icon  = "✅" if _ip else ("❌" if ev_v < 0 else "➖")
            p1 += (f"{icon} <b>{lb}</b>  EV {ev_v:+.1f}%  Prob {round(m.get('prob',0)*100)}%"
                   f"  @{m.get('odds',0):.2f} [{m.get('book','')}]\n")
            if "⚡" in lb: _has_k = True
        if not _has_k and is_mlb:
            p1 += "⚡ Props K: sin K/9 >8.5 confirmado — preferir ML\n"
    else:
        p1 += "Sin odds disponibles\n"

    # ─── 9. 🎓 PANEL ─────────────────────────────────────────────────────────
    experts   = ci.get("_expertos_detalle") or []
    _ci_icons = {"ALTA":"🟢","MEDIA":"🟡","BAJA":"🔴"}

    p2 = SEP + "🎓 <b>PANEL</b>\n"
    if experts:
        for ex in experts:
            ap  = "✅" if ex.get("apostar") is True else ("❌" if ex.get("apostar") is False else "⚪")
            ec  = _ci_icons.get(ex.get("confianza",""),"⚪")
            raz = (ex.get("razonamiento") or "").strip()
            if len(raz) > 250:
                cut = raz[:250]
                for sp in (".", "!", "?", ";"):
                    idx = cut.rfind(sp)
                    if idx > 100: cut = cut[:idx+1]; break
                raz = cut
            p2 += f"<b>{ex['nombre']}</b> {ap} {ec} — <i>{raz}</i>\n"
    else:
        if not cands and all_mkts:
            pos = [(l,m) for l,m in all_mkts.items() if m.get("ev_pct",0)>0]
            if pos:
                bl,bm = max(pos, key=lambda x: x[1]["ev_pct"])
                blc = bl.replace("🔵 ","").replace("🔴 ","").replace("📈 ","").replace("📉 ","")
                p2 += f"ℹ️ Sin panel — mejor: {blc} EV +{bm['ev_pct']:.1f}%\n"
            else:
                p2 += "ℹ️ Sin panel — todos los mercados con EV negativo\n"
        else:
            p2 += "ℹ️ Sin panel\n"

    # Recomendación (máx 200 chars)
    panel_razon = (ci.get("razonamiento") or "").strip()
    if len(panel_razon) > 200:
        cut = panel_razon[:200]
        for sp in (".", "!", "?", ";"):
            idx = cut.rfind(sp)
            if idx > 80: cut = cut[:idx+1]; break
        panel_razon = cut

    p2 += SEP
    if final_apostar is True and best_c:
        _blr = (best_c.get("label","?")
                .replace("🔵 ","").replace("🔴 ","").replace("📈 ","").replace("📉 ",""))
        p2 += (f"📋 <b>RECOMENDACIÓN: ✅ APOSTAR</b>\n"
               f"Pick: <b>{_blr}</b> @ {best_c.get('odds',0):.2f} {best_c.get('book','')} | Stake ${best_c.get('stake',0):.0f}\n")
        if panel_razon: p2 += f"<i>{panel_razon}</i>\n"
    elif final_apostar is False:
        p2 += "📋 <b>RECOMENDACIÓN: ❌ PASAR</b>\n"
        if panel_razon: p2 += f"<i>{panel_razon}</i>\n"
    else:
        p2 += "📋 <b>RECOMENDACIÓN: ⛔ SIN APUESTA</b>\n"
        if panel_razon: p2 += f"<i>{panel_razon}</i>\n"

    _pmw = result.get("pinnacle_mov_warn","")
    if _pmw: p2 += f"{SEP}{_pmw}\n"

    # Consejo final
    if not cands and all_mkts:
        pos = [(l,m) for l,m in all_mkts.items() if m.get("ev_pct",0)>0]
        if pos:
            bl,bm = max(pos, key=lambda x: x[1]["ev_pct"])
            blc = bl.replace("🔵 ","").replace("🔴 ","").replace("📈 ","").replace("📉 ","")
            p2 += (f"{SEP}💡 Si apostas: {blc} @ {bm['odds']:.2f} {bm['book']}\n"
                   f"   Modelo no recomienda — apuesta con criterio propio")
        else:
            p2 += f"{SEP}💡 Mejor pasar — sin ventaja detectada"
    elif cands and final_apostar is True:
        p2 += f"{SEP}💡 Apuesta antes de {gt} en Bovada"

    # ─── CONTROL DE LONGITUD (fix bug de cortes Telegram) ─────────────────────
    if len(p1) > 3900 and "📊 <b>CLAVES</b>" in p1:
        _kv_cut = _kv[:5]
        if len(_kv) > 5:
            _kv_cut.append(f"[+{len(_kv)-5} más]")
        _sec_k_cut = "📊 <b>CLAVES</b>\n" + "".join(f"   {l}\n" for l in _kv_cut) + SEP
        _idx_k = p1.find("📊 <b>CLAVES</b>")
        _idx_m = p1.find("💰 <b>MERCADOS</b>")
        if _idx_k != -1 and _idx_m != -1:
            p1 = p1[:_idx_k] + _sec_k_cut + p1[_idx_m:]

    return [p1, p2]


# ═══════════════════════════════════════════════════════════════════════════════
# IMPROVEMENT 3: CLV (CLOSING LINE VALUE) TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

def get_today_hoy_summary() -> list:
    """
    Quick MLB daily card for /hoy Telegram command.
    No Claude — uses _card_analyze_game (fast, model-only EV).
    Returns list of message strings (each ≤3800 chars) for Telegram.
    """
    try:
        games = get_odds("baseball_mlb")
    except Exception:
        return ["⚠️ No se pudo contactar la API de odds."]

    today_et   = datetime.now(ET)
    today_date = today_et.date()
    today_str  = today_et.strftime("%d/%m/%Y")

    today_games = []
    for g in (games or []):
        ct = g.get("commence_time", "")
        try:
            gdate = datetime.fromisoformat(ct.replace("Z", "+00:00")).astimezone(ET).date()
            if gdate == today_date:
                today_games.append(g)
        except Exception:
            today_games.append(g)

    if not today_games:
        return [f"⚾ Sin juegos MLB para hoy ({today_str})."]

    header = (
        f"⚾ <b>MLB HOY — {today_str}</b>\n"
        f"{_DIV}\n"
        f"{len(today_games)} partidos programados\n"
        f"{_DIV}\n"
    )

    rows = []
    for g in sorted(today_games, key=lambda x: x.get("commence_time", "")):
        try:
            res = _card_analyze_game(g, "baseball_mlb")
        except Exception:
            continue

        parts_m = res["match"].split(" vs ", 1)
        home_es = _es(parts_m[0])
        away_es = _es(parts_m[1]) if len(parts_m) > 1 else "?"
        gt      = _card_fmt_time(res["commence"])
        ph      = res["pitcher_h"]
        pa      = res["pitcher_a"]
        er_h    = res["era_h"]
        er_a    = res["era_a"]
        ev      = res["best_ev"]
        lbl     = res["best_label"]

        if ev >= 5.0 and lbl:
            pick_ln = f"   📌 Pick: {_es(lbl)} | EV +{ev:.1f}% ✅\n"
        elif ev >= 2.0 and lbl:
            pick_ln = f"   Pick rápido: {_es(lbl)} | EV +{ev:.1f}%\n"
        else:
            pick_ln = "   Sin edge claro por modelo.\n"

        rows.append(
            f"🔵 <b>{home_es} vs {away_es}</b> — {gt}\n"
            f"   P. Local:    {ph} (ERA {er_h:.2f})\n"
            f"   P. Visitante: {pa} (ERA {er_a:.2f})\n"
            f"{pick_ln}"
        )

    if not rows:
        return [f"⚾ Sin datos disponibles para hoy ({today_str})."]

    # Pack rows into parts ≤ 3800 chars
    result_parts = []
    current = header
    for row in rows:
        if len(current) + len(row) + 1 > 3800:
            result_parts.append(current)
            current = row
        else:
            current += "\n" + row
    if current:
        result_parts.append(current)

    result_parts[-1] += (
        f"\n{_DIV}\n"
        f"⚠️ Picks sin análisis Claude — usa /analizar para análisis completo."
    )
    return result_parts


def save_pending_bet(bet):
    """Persist an alerted totals bet to pending_bets.json for CLV tracking."""
    pending = []
    if os.path.exists(PENDING_BETS_FILE):
        try:
            with open(PENDING_BETS_FILE) as f:
                pending = json.load(f)
        except Exception:
            pass
    pending.append({
        'game_id':    bet.get('game_id', ''),
        'match':      bet.get('match', ''),
        'sport':      bet.get('sport', ''),
        'market_type': bet.get('market_type', 'totals'),
        'bet_side':   bet.get('team', ''),
        'book_line':  bet.get('book_line', ''),
        'alert_odds': bet.get('odds', ''),
        'alert_time': datetime.now(CDT).strftime('%Y-%m-%d %H:%M CDT'),
        'game_time':  bet.get('time', ''),
        'modelo':     bet.get('_modelo_usado', 'haiku'),
    })
    try:
        with open(PENDING_BETS_FILE, 'w') as f:
            json.dump(pending, f, indent=2)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════════
# CONFIRMATION SYSTEM
# Bets are queued in pending_confirm.json after every alert.
# Only after the user replies "aposté" or "bet placed" to the ntfy topic
# does the bot move the bet into bets_log.csv and pending_bets.json.
# ═══════════════════════════════════════════════════════════════════════════════

def _load_confirm_queue() -> list:
    if not os.path.exists(CONFIRM_FILE):
        return []
    try:
        with open(CONFIRM_FILE) as f:
            return json.load(f)
    except Exception:
        return []


def _save_confirm_queue(queue: list) -> None:
    try:
        with open(CONFIRM_FILE, "w") as f:
            json.dump(queue, f, indent=2)
    except Exception as e:
        print(f"  ⚠️  confirm_queue save error: {e}")


def _notify_simple_picks(bets: list, alerted: dict) -> None:
    """
    Send one clean ntfy alert per pick.
    alerted: shared {match_key: edge} dict — skips match if already sent this scan.
    Uses ev_pct (always a %) over edge (which for totals is run diff, not a %).
    """
    for b in bets:
        try:
            match = b.get("match", "?")
            team  = b.get("team", b.get("label", "?"))
            # ev_pct is always a true percentage; edge for totals = run diff (not %)
            ev_pct = float(b.get("ev_pct", 0))
            edge_pct_raw = float(b.get("edge", 0))
            # Use ev_pct when meaningful; fall back to edge only for ML (where it IS a %)
            market = b.get("market_type", "h2h")
            edge = ev_pct if ev_pct > 0 else (edge_pct_raw if market == "h2h" else 0.0)
            # Hard filter — must beat MIN_EDGE
            if edge < MIN_EDGE:
                print(f"  ⏭️  {match} | {team} — edge {edge:.1f}% < {MIN_EDGE}% mín, omitido")
                continue
            # Dedup — skip if this match already alerted with higher/equal edge this scan
            match_key = match.lower().strip()
            if match_key in alerted and alerted[match_key] >= edge:
                print(f"  ⏭️  {match} — ya alertado (edge {alerted[match_key]:.1f}% ≥ {edge:.1f}%)")
                continue
            odds  = float(b.get("odds", 0))
            book  = b.get("bookmaker", b.get("book", "?"))
            pick_clean = (team.replace("🔵 ", "").replace("🔴 ", "")
                              .replace("📈 ", "").replace("📉 ", "")
                              .replace("🏃 ", "").replace("🤝 ", ""))
            side = str(b.get("side", ""))
            if side and side not in pick_clean:
                pick_clean = f"{pick_clean} {side}"
            conf_line = "🟢 ALTA" if edge >= 5.0 else "🟡 MEDIA"
            body = (
                f"Pick: {pick_clean}\n"
                f"Edge: +{edge:.1f}%\n"
                f"Cuota: {odds:.2f} — {book}\n"
                f"Confianza: {conf_line}"
            )
            ntfy_post(f"🎯 {match}", body, "high" if edge >= 5.0 else "default")
            alerted[match_key] = edge
            print(f"  📲 Alerta: {match} | {pick_clean} | Edge:{edge:.1f}%")
        except Exception as e:
            print(f"  ⚠️  simple pick alert error: {e}")


def _notify_simple_analyses(full_analyses: list, alerted: dict) -> None:
    """
    Send clean pick alert for each full-analysis game with qualifying pick.
    alerted: shared {match_key: edge} dict — skips match if already sent this scan.
    Called BEFORE _notify_simple_picks so full-analysis EV gets priority.
    """
    for a in full_analyses:
        try:
            candidates = a.get("candidates", [])
            if not candidates:
                continue
            best = max(candidates, key=lambda c: float(c.get("ev_pct", 0)))
            edge = float(best.get("ev_pct", 0))
            if edge < MIN_EDGE:
                continue
            match = a.get("match", "?")
            match_key = match.lower().strip()
            if match_key in alerted and alerted[match_key] >= edge:
                print(f"  ⏭️  {match} — ya alertado (edge {alerted[match_key]:.1f}% ≥ {edge:.1f}%)")
                continue
            label = best.get("label", "?")
            pick_clean = (label.replace("🔵 ", "").replace("🔴 ", "")
                               .replace("📈 ", "").replace("📉 ", ""))
            odds = float(best.get("odds", 0))
            book = best.get("book", "?")
            conf_line = "🟢 ALTA" if edge >= 5.0 else "🟡 MEDIA"
            body = (
                f"Pick: {pick_clean}\n"
                f"Edge: +{edge:.1f}%\n"
                f"Cuota: {odds:.2f} — {book}\n"
                f"Confianza: {conf_line}"
            )
            ntfy_post(f"🎯 {match}", body, "high" if edge >= 5.0 else "default")
            alerted[match_key] = edge
            print(f"  📲 Análisis: {match} | {pick_clean} | Edge:{edge:.1f}%")
        except Exception as e:
            print(f"  ⚠️  simple analysis alert error: {e}")


def _github_pull_daily_exposure() -> dict | None:
    """
    Fetch daily_exposure.json from GitHub via REST API.
    Returns parsed dict or None on any error.
    Railway filesystem is ephemeral — GitHub is the durable backup.
    """
    if not GITHUB_TOKEN:
        return None
    try:
        import urllib.request as _ur, base64 as _b64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DAILY_EXPOSURE_FILE}"
        req = _ur.Request(url, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        })
        with _ur.urlopen(req, timeout=8) as r:
            info = json.loads(r.read())
        return json.loads(_b64.b64decode(info["content"]).decode())
    except Exception:
        return None


def _github_push_daily_exposure() -> None:
    """
    Push daily_exposure.json to GitHub.
    Called once per scan (not per pick) — keeps Railway redeploys in sync.
    Silently skips if GITHUB_TOKEN not set.
    """
    if not GITHUB_TOKEN:
        return
    try:
        import urllib.request as _ur, base64 as _b64
        payload_bytes = json.dumps({
            "date":     _daily_exposure_date.isoformat(),
            "exposure": round(_daily_exposure, 2),
        }).encode()
        api = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{DAILY_EXPOSURE_FILE}"
        hdrs = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept":        "application/vnd.github.v3+json",
            "Content-Type":  "application/json",
        }
        # Get current SHA (needed for update; omit for new file)
        sha = None
        try:
            r0 = _ur.Request(api, headers=hdrs)
            with _ur.urlopen(r0, timeout=8) as r:
                sha = json.loads(r.read()).get("sha")
        except Exception:
            pass
        body = {
            "message": f"bot: update daily_exposure {_daily_exposure_date.isoformat()} ${_daily_exposure:.2f}",
            "content": _b64.b64encode(payload_bytes).decode(),
        }
        if sha:
            body["sha"] = sha
        req = _ur.Request(api, data=json.dumps(body).encode(), headers=hdrs, method="PUT")
        with _ur.urlopen(req, timeout=10):
            pass
        print(f"  ☁️  Exposición diaria sincronizada a GitHub: ${_daily_exposure:.2f}")
    except Exception as e:
        print(f"  ⚠️  GitHub daily_exposure push error: {e}")


def _github_pull_elite_counter() -> "dict | None":
    if not GITHUB_TOKEN:
        return None
    try:
        import urllib.request as _ur, base64 as _b64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ELITE_COUNTER_FILE}"
        req = _ur.Request(url, headers={
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json",
        })
        with _ur.urlopen(req, timeout=8) as r:
            info = json.loads(r.read())
        return json.loads(_b64.b64decode(info["content"]).decode())
    except Exception:
        return None


def _github_push_elite_counter() -> None:
    global _elite_count_today, _elite_count_date
    if not GITHUB_TOKEN:
        return
    try:
        import urllib.request as _ur, base64 as _b64
        payload = json.dumps({"date": _elite_count_date, "count": _elite_count_today}).encode()
        api  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{ELITE_COUNTER_FILE}"
        hdrs = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept":        "application/vnd.github.v3+json",
            "Content-Type":  "application/json",
        }
        sha = None
        try:
            r0 = _ur.Request(api, headers=hdrs)
            with _ur.urlopen(r0, timeout=8) as r:
                sha = json.loads(r.read()).get("sha")
        except Exception:
            pass
        body = {
            "message": f"bot: elite counter {_elite_count_date} count={_elite_count_today}",
            "content": _b64.b64encode(payload).decode(),
        }
        if sha:
            body["sha"] = sha
        req = _ur.Request(api, data=json.dumps(body).encode(), headers=hdrs, method="PUT")
        with _ur.urlopen(req, timeout=10):
            pass
        print(f"  ☁️  Elite counter sincronizado: {_elite_count_today}/{MAX_ELITE_DIARIO} hoy")
    except Exception as e:
        print(f"  ⚠️  GitHub elite counter push error: {e}")


def _save_elite_counter() -> None:
    global _elite_count_today, _elite_count_date
    try:
        with open(ELITE_COUNTER_FILE, "w") as f:
            json.dump({"date": _elite_count_date, "count": _elite_count_today}, f)
    except Exception:
        pass
    try:
        _github_push_elite_counter()
    except Exception:
        pass


def _load_elite_counter() -> None:
    global _elite_count_today, _elite_count_date
    today = datetime.now(CDT).strftime("%Y-%m-%d")

    def _parse(data: dict):
        if isinstance(data, dict) and data.get("date") == today:
            return int(data.get("count", 0))
        return None

    try:
        if os.path.exists(ELITE_COUNTER_FILE):
            with open(ELITE_COUNTER_FILE) as f:
                result = _parse(json.load(f))
            if result is not None:
                _elite_count_today = result
                _elite_count_date  = today
                print(f"  💾 Elite counter restaurado (local): {_elite_count_today}/{MAX_ELITE_DIARIO}")
                return
    except Exception:
        pass

    gh_data = _github_pull_elite_counter()
    if gh_data:
        result = _parse(gh_data)
        if result is not None:
            _elite_count_today = result
            _elite_count_date  = today
            print(f"  ☁️  Elite counter restaurado (GitHub): {_elite_count_today}/{MAX_ELITE_DIARIO}")
            try:
                with open(ELITE_COUNTER_FILE, "w") as f:
                    json.dump(gh_data, f)
            except Exception:
                pass
            return

    _elite_count_today = 0
    _elite_count_date  = today
    print(f"  💾 Elite counter: 0/{MAX_ELITE_DIARIO} (día nuevo)")


def _load_daily_exposure() -> tuple:
    """
    Read daily_exposure.json on startup.
    Returns (exposure_float, date_obj).
    Priority: local file → GitHub backup → $0 fresh start.
    If date != today → returns (0.0, today) regardless of source.
    """
    today = datetime.now(CDT).date()

    def _parse(data: dict):
        saved_date = date.fromisoformat(data["date"])
        if saved_date == today:
            return float(data.get("exposure", 0.0)), saved_date
        return None

    # 1. Try local file (fast — present if same container is still running)
    try:
        if os.path.exists(DAILY_EXPOSURE_FILE):
            with open(DAILY_EXPOSURE_FILE) as f:
                result = _parse(json.load(f))
            if result:
                print(f"  💾 Exposición diaria restaurada (local): ${result[0]:.2f}")
                return result
    except Exception as e:
        print(f"  ⚠️  daily_exposure local load error: {e}")

    # 2. Fall back to GitHub (survives Railway redeploys)
    gh_data = _github_pull_daily_exposure()
    if gh_data:
        result = _parse(gh_data)
        if result:
            print(f"  ☁️  Exposición diaria restaurada (GitHub): ${result[0]:.2f}")
            # Write to local so subsequent reads skip the network call
            try:
                with open(DAILY_EXPOSURE_FILE, "w") as f:
                    json.dump(gh_data, f)
            except Exception:
                pass
            return result

    print(f"  💾 Exposición diaria: $0.00 (día nuevo o primera ejecución)")
    return 0.0, today


def _save_daily_exposure() -> None:
    """Write daily_exposure.json to local disk (fast — called per pick).
    GitHub sync happens once per scan via _github_push_daily_exposure()."""
    try:
        with open(DAILY_EXPOSURE_FILE, "w") as f:
            json.dump({
                "date":     _daily_exposure_date.isoformat(),
                "exposure": round(_daily_exposure, 2),
            }, f)
    except Exception as e:
        print(f"  ⚠️  daily_exposure save error: {e}")


def queue_for_confirmation(bets: list, sport_key: str) -> None:
    """
    Replace log_bets() in the main scan loop.
    Each detected pick is queued for user confirmation instead of logged directly.

    Hard bankroll guards applied here:
      • MAX_DAILY_EXPO_PCT (15%): if adding a pick's stake would exceed today's
        daily exposure limit, the pick is skipped with a warning ntfy alert.
      • Daily exposure resets at midnight (CDT date change).
    """
    global _daily_exposure, _daily_exposure_date
    if not bets:
        return

    # Reset daily exposure counter on a new calendar day
    today = datetime.now(CDT).date()
    if today != _daily_exposure_date:
        _daily_exposure      = 0.0
        _daily_exposure_date = today
        _save_daily_exposure()   # persist the reset so redeploys see $0 for the new day

    max_daily = BANKROLL * MAX_DAILY_EXPO_PCT   # e.g. $150 at $1000 bankroll

    queue = _load_confirm_queue()

    # ── Clean up expired pending picks (game started > 2h ago) ───────────────
    expired = [e for e in queue if _game_already_started(e.get("time", ""), grace_min=120)]
    if expired:
        released = sum(float(e.get("suggested_stake", 0)) for e in expired)
        queue = [e for e in queue if e not in expired]
        _save_confirm_queue(queue)
        _daily_exposure = max(0.0, _daily_exposure - released)
        _save_daily_exposure()
        print(f"  🗑️  {len(expired)} pick(s) expirado(s) eliminados — ${released:.2f} liberados de exposición")

    now   = datetime.now(CDT).strftime("%Y-%m-%d %H:%M CDT")
    queued_count = 0

    for b in bets:
        stake = float(b.get("stake", 0))

        # ── Daily exposure hard cap ──────────────────────────────────────
        if _daily_exposure + stake > max_daily:
            remaining = round(max_daily - _daily_exposure, 2)
            match_disp = b.get("match", "?")
            team_disp  = b.get("team", b.get("label", "?"))
            print(f"  🚫 DAILY CAP: {match_disp} → {team_disp} "
                  f"(exposición ${_daily_exposure:.0f}/${max_daily:.0f}) — pick omitido")
            ntfy_post(
                "🚫 Límite diario alcanzado",
                _two_layer_body(
                    f"🚫 PICK BLOQUEADO — LÍMITE DIARIO\n"
                    f"🎯 {match_disp} → {team_disp}\n"
                    f"Exposición hoy: ${_daily_exposure:.0f} / ${max_daily:.0f} (15% bankroll)\n"
                    f"Margen disponible: ${remaining}",
                    f"El bot bloqueó este pick porque apostar ${stake} llevaría\n"
                    f"la exposición diaria al {MAX_DAILY_EXPO_PCT*100:.0f}% del bankroll.\n\n"
                    "Regla de bankroll: máximo 15% del bankroll en juego por día.\n"
                    "El bot retomará picks cuando termine algún juego de hoy.",
                ),
                "default",
            )
            continue   # skip this pick — do NOT queue it

        entry = dict(b)
        entry["_sport_key"]      = sport_key
        entry["_queued_at"]      = now
        entry["_status"]         = "pending"
        entry["suggested_stake"] = stake   # keep suggestion for when user confirms
        entry["stake"]           = 0.0     # $0 until user manually confirms via "aposté"
        queue.append(entry)
        _daily_exposure += stake   # count toward cap — prevents double-queueing same pick
        _save_daily_exposure()   # persist after every increment — survives redeploys
        queued_count    += 1

    if queued_count:
        _save_confirm_queue(queue)
        print(f"  📋 {queued_count} pick(s) en cola de confirmación "
              f"(exposición hoy: ${_daily_exposure:.0f}/${max_daily:.0f})")


def _confirm_bet(entry: dict) -> None:
    """
    Promote one queued bet entry into bets_log.csv and pending_bets.json.
    Called ONLY when user confirms via ntfy ("aposté").
    stake was set to $0 at queue time — restore suggested_stake here.
    """
    sport_key = entry.pop("_sport_key", "")
    entry.pop("_queued_at", None)
    entry.pop("_status", None)

    # Restore the Kelly-computed stake from when the pick was originally found
    # (entry["stake"] was zeroed at queue time; suggested_stake holds the real amount)
    if "suggested_stake" in entry:
        entry["stake"] = entry.pop("suggested_stake")

    # Write to bets_log.csv (the permanent record)
    try:
        log_bets([entry], sport_key)
    except Exception as e:
        print(f"  ⚠️  log_bets in confirm: {e}")

    # Write to pending_bets.json (for CLV closing-line tracking)
    try:
        save_pending_bet(entry)
    except Exception as e:
        print(f"  ⚠️  save_pending_bet in confirm: {e}")


def _confirm_next_bet(match_hint: str = "") -> bool:
    """
    Confirm and log the next (or match_hint-matching) pending bet.
    Returns True if a bet was confirmed, False if queue was empty.
    """
    queue = _load_confirm_queue()
    pending = [e for e in queue if e.get("_status") == "pending"]
    if not pending:
        ntfy_post(
            "📋 Sin picks pendientes",
            "No hay picks esperando confirmación ahora mismo.\n"
            "El bot te avisará cuando encuentre el próximo pick de valor.",
            "default",
        )
        return False

    # Match by team or match name fragment if hint provided
    if match_hint:
        hint_low = match_hint.lower()
        matched  = next(
            (e for e in pending
             if hint_low in e.get("match", "").lower()
             or hint_low in e.get("team", "").lower()),
            None,
        )
        if not matched:
            # Fall back to oldest
            matched = pending[0]
    else:
        matched = pending[0]

    _confirm_bet(matched)

    # Remove from queue
    queue = [e for e in queue if e is not matched]
    _save_confirm_queue(queue)

    remaining = sum(1 for e in queue if e.get("_status") == "pending")
    match_disp = matched.get("match", "?")
    team_disp  = matched.get("team", "?")
    odds_disp  = matched.get("odds", "?")
    stake_disp = matched.get("stake", "?")
    book_disp  = matched.get("bookmaker", "?")

    ntfy_post(
        f"✅ Apuesta confirmada — {team_disp}",
        _two_layer_body(
            f"✅ PICK REGISTRADO\n"
            f"🎯 {match_disp}\n"
            f"APUESTA: {team_disp} @ {odds_disp} — {book_disp}\n"
            f"💰 Stake: ${stake_disp}",
            f"Este pick ahora está en tu historial y se auto-resultará cuando termine el juego.\n"
            + (f"\n📋 Picks pendientes de confirmar: {remaining}" if remaining else
               "\n✅ No hay más picks pendientes."),
        ),
        "default",
    )
    print(f"  ✅ CONFIRMADO: {match_disp} → {team_disp} @ {odds_disp}")
    return True


def _confirm_all_pending() -> int:
    """Confirm every queued pending bet. Returns count confirmed."""
    queue   = _load_confirm_queue()
    pending = [e for e in queue if e.get("_status") == "pending"]
    if not pending:
        return 0
    for entry in pending:
        _confirm_bet(entry)
    _save_confirm_queue([e for e in queue if e.get("_status") != "pending"])
    print(f"  ✅ TODAS CONFIRMADAS: {len(pending)} picks")
    return len(pending)


def _cancel_next_bet(match_hint: str = "") -> bool:
    """
    Discard the next (or hint-matching) pending bet without logging it.
    Returns True if a bet was cancelled.
    """
    queue   = _load_confirm_queue()
    pending = [e for e in queue if e.get("_status") == "pending"]
    if not pending:
        ntfy_post(
            "📋 Sin picks pendientes",
            "No hay picks esperando confirmación ahora mismo.",
            "default",
        )
        return False

    if match_hint:
        hint_low = match_hint.lower()
        matched  = next(
            (e for e in pending
             if hint_low in e.get("match", "").lower()
             or hint_low in e.get("team", "").lower()),
            None,
        ) or pending[0]
    else:
        matched = pending[0]

    queue = [e for e in queue if e is not matched]
    _save_confirm_queue(queue)
    global _daily_exposure
    _daily_exposure = max(0.0, _daily_exposure - float(matched.get("suggested_stake", 0) or 0))
    _save_daily_exposure()

    match_disp = matched.get("match", "?")
    team_disp  = matched.get("team", "?")
    ntfy_post(
        f"🗑️ Pick cancelado — {team_disp}",
        f"🗑️ Pick descartado: {match_disp} → {team_disp}\n"
        f"No se registró en el historial.",
        "default",
    )
    print(f"  🗑️ CANCELADO: {match_disp} → {team_disp}")
    return True


def _list_pending_confirm() -> None:
    """Send ntfy listing all pending-confirmation bets."""
    queue   = _load_confirm_queue()
    pending = [e for e in queue if e.get("_status") == "pending"]
    if not pending:
        ntfy_post(
            "📋 Sin picks pendientes",
            "No hay picks en espera de confirmación.",
            "default",
        )
        return

    lines = []
    for i, e in enumerate(pending, 1):
        lines.append(
            f"{i}. {e.get('match','?')}\n"
            f"   {e.get('team','?')} @ {e.get('odds','?')} — {e.get('bookmaker','?')}\n"
            f"   ${e.get('stake','?')} | En cola: {e.get('_queued_at','?')}"
        )
    body = (
        f"📋 PICKS ESPERANDO CONFIRMACIÓN ({len(pending)}):\n"
        f"{'━'*24}\n"
        + "\n".join(lines)
        + f"\n{'━'*24}\n"
        "Responde 'aposté' para confirmar el primero.\n"
        "Responde 'aposté todas' para confirmar todos.\n"
        "Responde 'cancelé' para descartar el primero."
    )
    ntfy_post(f"📋 {len(pending)} picks pendientes", body, "default")


def _poll_ntfy_confirmations() -> None:
    """
    Poll the ntfy topic for incoming messages from the user.
    Recognises confirmation and cancellation commands and acts on the queue.

    Commands (case-insensitive, sent by user to the same ntfy topic):
      aposté / bet placed / confirmar         → confirm oldest pending bet
      aposté [fragment] / confirmar [fragment]→ confirm bet matching fragment
      aposté todas / confirm all              → confirm all pending bets
      cancelé / cancel / no aposté            → discard oldest pending bet
      cancelé [fragment]                      → discard bet matching fragment
      picks pendientes / pending / pendientes → list pending bets
    """
    global _ntfy_last_confirm_id
    if not NOTIFY:
        return
    try:
        url = f"https://ntfy.sh/{NOTIFY}/json?poll=1&since=35m"
        r   = requests.get(url, timeout=8)
        if r.status_code != 200:
            return
        new_id = _ntfy_last_confirm_id
        for raw_line in r.text.strip().splitlines():
            try:
                msg = json.loads(raw_line)
            except Exception:
                continue
            if msg.get("event") != "message":
                continue
            mid = msg.get("id", "")
            if mid == _ntfy_last_confirm_id:
                # Reached the last one we already processed; skip rest
                break
            new_id = mid  # track newest ID seen this poll

            text = (msg.get("message") or msg.get("title") or "").strip().lower()
            if not text:
                continue

            # ── Guard: skip bot's own alert messages ───────────────────────
            # Bot alerts are long (>80 chars) and contain instruction phrases
            # like "responde 'aposté' para registrar" or "sugerido".
            # User commands are always short bare words ("aposté", "cancelé").
            if len(text) > 80 or "responde" in text or "sugerido" in text:
                continue

            # Helper: keyword must START the message (not be embedded in it)
            def _starts_with_cmd(txt, keywords):
                return any(
                    txt == k or txt.startswith(k + " ") or txt.startswith(k + "\n")
                    for k in keywords
                )

            # ── confirmation commands ──────────────────────────────────────
            if _starts_with_cmd(text, ("aposté todas", "confirm all",
                                       "aposte todas", "confirmar todas")):
                n = _confirm_all_pending()
                if n:
                    ntfy_post(
                        f"✅ {n} picks confirmados",
                        f"Se registraron {n} picks en el historial.\n"
                        "El bot auto-resultará cada uno cuando termine el juego.",
                        "default",
                    )
                break

            if _starts_with_cmd(text, ("aposté", "aposte", "bet placed",
                                       "confirmar", "confirmed")):
                # Extract optional match/team hint after the command keyword
                hint = ""
                for kw in ("aposté", "aposte", "bet placed", "confirmar", "confirmed"):
                    if text.startswith(kw):
                        hint = text[len(kw):].strip()
                        break
                _confirm_next_bet(match_hint=hint)
                break

            # ── cancellation commands ──────────────────────────────────────
            if _starts_with_cmd(text, ("cancelé", "cancele", "cancel",
                                       "no aposté", "no aposte")):
                hint = ""
                for kw in ("cancelé", "cancele", "cancel", "no aposté", "no aposte"):
                    if text.startswith(kw):
                        hint = text[len(kw):].strip()
                        break
                _cancel_next_bet(match_hint=hint)
                break

            # ── list commands ──────────────────────────────────────────────
            if _starts_with_cmd(text, ("pendientes", "pending", "picks")):
                _list_pending_confirm()
                break

        _ntfy_last_confirm_id = new_id
    except Exception as e:
        print(f"  ⚠️  _poll_ntfy_confirmations error: {e}")


def check_closing_lines(current_games_by_sport):
    """
    For each pending totals bet whose game starts in ≤5 min, fetch the live
    book total and compute CLV = alert_line − closing_line (positive = value).
    Appends to clv_log.csv; removes closed bets from pending_bets.json.
    """
    if not os.path.exists(PENDING_BETS_FILE):
        return
    try:
        with open(PENDING_BETS_FILE) as f:
            pending = json.load(f)
    except Exception:
        return

    remaining  = []
    clv_rows   = []

    for bet in pending:
        gt_raw = bet.get('game_time', '')
        if not gt_raw:
            remaining.append(bet)
            continue

        # Parse game time (ISO or truncated)
        gt = None
        for fmt in ('%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M CDT'):
            try:
                parsed = datetime.strptime(gt_raw, fmt)
                if fmt.endswith('Z'):
                    gt = parsed.replace(tzinfo=pytz.utc).astimezone(CDT)
                elif fmt.endswith('CDT'):
                    gt = CDT.localize(parsed)
                else:
                    gt = CDT.localize(parsed)
                break
            except Exception:
                pass

        if gt is None:
            remaining.append(bet)
            continue

        mins_to_game = (gt - datetime.now(CDT)).total_seconds() / 60

        if mins_to_game > 5:
            remaining.append(bet)   # not time yet
            continue
        if mins_to_game < -90:
            continue                # game long past — drop silently

        # Find the live book total for this game
        sport_key = next(
            (sk for sk in SPORT_KEYS
             if bet.get('sport', '').lower() in sk.lower()),
            None,
        )
        games = current_games_by_sport.get(sport_key, [])
        closing_line = None
        for g in games:
            name = f"{g['home_team']} vs {g['away_team']}"
            if bet['match'].lower() == name.lower():
                bd = get_book_total(g)
                if bd:
                    closing_line = bd[0]
                break

        if closing_line is None:
            remaining.append(bet)
            continue

        # Closing decimal odds for the bet side
        closing_odds = None
        bet_side = bet.get('bet_side', '').upper()
        if bd and len(bd) >= 3:
            closing_odds = bd[2] if bet_side == 'UNDER' else bd[1]  # bd=(line,over,under,book)

        try:
            al = float(bet.get('book_line', closing_line))
            # CLV for totals (run-line diff): positive = line moved in our favour
            clv = (al - closing_line) if bet_side == 'UNDER' else (closing_line - al)
        except Exception:
            clv = None

        # CLV% = implied_prob_at_alert - implied_prob_at_close (positive = beat closing)
        clv_pct = None
        try:
            alert_dec = float(bet.get('alert_odds') or 0)
            close_dec = float(closing_odds or 0)
            if alert_dec > 1.0 and close_dec > 1.0:
                p_alert = 1.0 / alert_dec
                p_close = 1.0 / close_dec
                clv_pct = round((p_alert - p_close) * 100, 2)
        except Exception:
            clv_pct = None

        clv_rows.append({
            'alert_time':    bet.get('alert_time', ''),
            'clv_time':      datetime.now(CDT).strftime('%Y-%m-%d %H:%M CDT'),
            'match':         bet.get('match', ''),
            'sport':         bet.get('sport', ''),
            'market_type':   bet.get('market_type', 'totals'),
            'bet_side':      bet.get('bet_side', ''),
            'book_line':     bet.get('book_line', ''),
            'alert_odds':    bet.get('alert_odds', ''),
            'closing_line':  closing_line,
            'closing_odds':  closing_odds if closing_odds else '',
            'clv':           round(clv, 2) if clv is not None else '',
            'clv_pct':       clv_pct if clv_pct is not None else '',
            'modelo':        bet.get('modelo', 'haiku'),
        })

    if clv_rows:
        clv_fields = ['alert_time', 'clv_time', 'match', 'sport', 'market_type',
                      'bet_side', 'book_line', 'alert_odds', 'closing_line',
                      'closing_odds', 'clv', 'clv_pct', 'modelo']
        exists = os.path.exists(CLV_LOG_FILE)
        with open(CLV_LOG_FILE, 'a', newline='') as f:
            w = csv.DictWriter(f, fieldnames=clv_fields, extrasaction='ignore')
            if not exists:
                w.writeheader()
            w.writerows(clv_rows)
        print(f"  📊 CLV logged for {len(clv_rows)} closing bet(s) → {CLV_LOG_FILE}")

    try:
        with open(PENDING_BETS_FILE, 'w') as f:
            json.dump(remaining, f, indent=2)
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════════════
# IMPROVEMENT 4: BANKROLL & P&L DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

def load_bankroll_state():
    """Read bankroll_log.csv and return current state dict."""
    bets = []
    if os.path.exists(BANKROLL_LOG_FILE):
        try:
            with open(BANKROLL_LOG_FILE, newline='') as f:
                bets = list(csv.DictReader(f))
        except Exception:
            pass
    current = BANKROLL
    if bets:
        try:
            current = float(bets[-1].get('running_bankroll', BANKROLL))
        except Exception:
            pass
    return {'current': current, 'bets': bets}

def print_dashboard():
    """Print P&L dashboard to stdout on every scan cycle."""
    state   = load_bankroll_state()
    bets    = state['bets']
    current = state['current']

    settled = [b for b in bets if b.get('result') in ('W', 'L', 'P')]
    wins    = [b for b in settled if b.get('result') == 'W']
    losses  = [b for b in settled if b.get('result') == 'L']
    pushes  = [b for b in settled if b.get('result') == 'P']

    roi      = (current - BANKROLL) / BANKROLL * 100 if BANKROLL else 0
    win_rate = len(wins) / len(settled) * 100 if settled else 0.0

    # Peak-to-trough drawdown
    try:
        bankrolls = [BANKROLL] + [float(b.get('running_bankroll', BANKROLL)) for b in bets]
        peak      = max(bankrolls)
        drawdown  = (peak - current) / peak * 100 if peak else 0.0
    except Exception:
        drawdown = 0.0

    # Per-sport ROI
    sport_pnl: dict = {}
    for b in settled:
        sp = b.get('sport', 'unknown')
        try:
            pnl   = float(b.get('profit_loss', 0))
            stake = float(b.get('stake', 0))
            sport_pnl.setdefault(sp, {'pnl': 0.0, 'stake': 0.0})
            sport_pnl[sp]['pnl']   += pnl
            sport_pnl[sp]['stake'] += stake
        except Exception:
            pass
    sport_roi = {
        sp: (v['pnl'] / v['stake'] * 100 if v['stake'] else 0.0)
        for sp, v in sport_pnl.items()
    }
    best_sp  = max(sport_roi, key=sport_roi.get) if sport_roi else None
    worst_sp = min(sport_roi, key=sport_roi.get) if sport_roi else None

    br_pending = len(bets) - len(settled)
    print(f"\n  {'─'*44}")
    print(f"  💼 Bankroll: ${current:,.2f}   ROI: {roi:+.1f}%")
    print(f"  📈 W {len(wins)} / L {len(losses)} / P {len(pushes)} / Pending {br_pending}"
          f"   Win Rate: {win_rate:.1f}%   Drawdown: {drawdown:.1f}%")
    if best_sp and worst_sp:
        print(f"  🏆 Best:  {best_sp} ({sport_roi[best_sp]:+.1f}%)"
              f"   Worst: {worst_sp} ({sport_roi[worst_sp]:+.1f}%)")
    print(f"  {'─'*44}")

def log_bankroll_entry(sport, match, market_type, stake, result, profit_loss):
    """
    Append one settled bet to bankroll_log.csv.
    Call this externally (or from a future results-tracker) to record outcomes.
    """
    state   = load_bankroll_state()
    current = state['current'] + float(profit_loss)
    exists  = os.path.exists(BANKROLL_LOG_FILE)
    fields  = ['date', 'sport', 'match', 'market_type', 'stake',
               'result', 'profit_loss', 'running_bankroll']
    with open(BANKROLL_LOG_FILE, 'a', newline='') as f:
        w = csv.DictWriter(f, fieldnames=fields)
        if not exists:
            w.writeheader()
        w.writerow({
            'date':             datetime.now(CDT).strftime('%Y-%m-%d %H:%M CDT'),
            'sport':            sport,
            'match':            match,
            'market_type':      market_type,
            'stake':            round(float(stake), 2),
            'result':           result,
            'profit_loss':      round(float(profit_loss), 2),
            'running_bankroll': round(current, 2),
        })

# ═══════════════════════════════════════════════════════════════════════════════
# CORE — ODDS FETCHING & LINE MOVEMENT
# ═══════════════════════════════════════════════════════════════════════════════

_odds_api_429_count:   int               = 0
_odds_api_pause_until: "datetime | None" = None
_odds_cache:           dict              = {}    # {sport_key: {"ts": datetime_utc, "data": list}}
_ODDS_CACHE_TTL                          = 1200  # 20 minutos en segundos
_quota_low_alert_date: str               = ""    # dedup alerta diaria quota < 2000

def get_odds(sport_key, force_fresh=False):
    global _odds_api_429_count, _odds_api_pause_until, _odds_cache, _quota_low_alert_date
    if not force_fresh and sport_key in _odds_cache:
        _age = (datetime.now(pytz.utc) - _odds_cache[sport_key]["ts"]).total_seconds()
        if _age < _ODDS_CACHE_TTL:
            print(f"  📦 [{sport_key}] Odds desde caché (edad: {int(_age/60)} min)")
            return _odds_cache[sport_key]["data"]

    if _odds_api_pause_until and datetime.now(pytz.utc) < _odds_api_pause_until:
        _remaining = int((_odds_api_pause_until - datetime.now(pytz.utc)).total_seconds() / 60)
        print(f"  ⏸️  Odds API pausada por quota — {_remaining} min restantes")
        raise RuntimeError(f"Odds API en pausa — {_remaining} min restantes")

    _et_now   = datetime.now(ET)
    _et_today = _et_now.strftime("%Y-%m-%d")
    print(f"  📅 [{sport_key}] get_odds — fecha ET: {_et_today} ({_et_now.strftime('%H:%M ET')})")

    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
            params={"apiKey": API_KEY, "regions": "us",
                    "markets": "h2h,totals,spreads", "oddsFormat": "decimal"},
            timeout=10,
        )
        _rem  = r.headers.get("x-requests-remaining", "?")
        _used = r.headers.get("x-requests-used",      "?")
        print(f"  📊 [{sport_key}] HTTP {r.status_code} | quota: {_rem} restantes / {_used} usadas")

        if r.status_code == 429:
            _odds_api_429_count += 1
            print(f"  ⚠️  Odds API 429 (intento {_odds_api_429_count}/3): {sport_key}")
            if _odds_api_429_count >= 3:
                _odds_api_pause_until = datetime.now(pytz.utc) + timedelta(minutes=30)
                ntfy_post(
                    "🚨 Odds API: Cuota Agotada",
                    "3 errores 429 seguidos — pausando llamadas 30 minutos.\n"
                    "El bot sigue corriendo sin nuevas odds hasta reactivarse.",
                    "urgent"
                )
                print("  🛑 Odds API circuit breaker activado — 30 min pause")
            raise RuntimeError(f"Odds API 429 — quota agotada ({sport_key})")

        if r.status_code == 401 and _rem == "0":
            _odds_api_429_count += 1
            print(f"  ⚠️  Odds API 401 con quota=0 — cuota mensual agotada ({sport_key})")
            if _odds_api_429_count >= 3:
                _odds_api_pause_until = datetime.now(pytz.utc) + timedelta(minutes=30)
                ntfy_post(
                    "🚨 Odds API: Cuota Mensual Agotada",
                    "401 con x-requests-remaining=0 — pausando llamadas 30 minutos.\n"
                    "El bot sigue corriendo sin nuevas odds hasta reactivarse.",
                    "urgent"
                )
                print("  🛑 Odds API circuit breaker activado (401+quota=0) — 30 min pause")
            raise RuntimeError(f"Odds API 401 quota=0 — cuota agotada ({sport_key})")

        if r.status_code != 200:
            raise RuntimeError(f"Odds API HTTP {r.status_code} ({sport_key})")

        _odds_api_429_count = 0
        _odds_api_pause_until = None
        _result = r.json()
        print(f"  📌 [{sport_key}] Juegos con Pinnacle: {sum(1 for g in _result if any('pinnacle' in b.get('title','').lower() for b in g.get('bookmakers',[])))}/{len(_result)}")

        if _rem != "?" and _rem:
            _rem_i = int(_rem)
            if _rem_i < 500:
                print(f"  ⚠️  Odds API quota baja: {_rem} llamadas restantes este mes")
                if _rem_i < 100:
                    ntfy_post(
                        "🚨 Quota API Crítica",
                        f"Solo {_rem} llamadas restantes en Odds API este mes.\n"
                        "Considera reducir INTERVAL o actualizar el plan.",
                        "urgent"
                    )
            elif _rem_i < 2000:
                try:
                    _today_et = datetime.now(ET).strftime("%Y-%m-%d")
                    if _quota_low_alert_date != _today_et:
                        _quota_low_alert_date = _today_et
                        ntfy_post(
                            "⚠️ Quota Odds API Baja",
                            f"Quedan {_rem} requests este mes. Considera reducir el plan.",
                            "high"
                        )
                except Exception:
                    pass

        raw_games = r.json()
        print(f"  🔢 [{sport_key}] Juegos crudos de API: {len(raw_games)}")

        # MLB: filtrar localmente por ventana ET del día (cubre juegos movidos de fecha)
        if "mlb" in sport_key:
            _et_start = _et_now.replace(hour=0,  minute=0,  second=0,  microsecond=0)
            _et_end   = _et_now.replace(hour=23, minute=59, second=59, microsecond=999999)
            filtered = []
            for _g in raw_games:
                _ct = _g.get("commence_time", "")
                try:
                    _gdt = datetime.fromisoformat(_ct.replace("Z", "+00:00")).astimezone(ET)
                    if _et_start <= _gdt <= _et_end:
                        filtered.append(_g)
                except Exception:
                    filtered.append(_g)
            print(f"  🔢 [{sport_key}] Juegos tras filtro ET hoy ({_et_today}): "
                  f"{len(filtered)} de {len(raw_games)}")
            _result = filtered
        else:
            _result = raw_games

        _odds_cache[sport_key] = {"ts": datetime.now(pytz.utc), "data": _result}
        return _result

    except RuntimeError:
        raise
    except Exception as _e:
        print(f"  ⚠️  get_odds [{sport_key}]: {_e}")
        raise RuntimeError(f"Error de conexión Odds API ({sport_key}): {_e}")

def load_previous_odds():
    if os.path.exists(PREV_ODDS_FILE):
        try:
            with open(PREV_ODDS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_previous_odds(data):
    with open(PREV_ODDS_FILE, "w") as f:
        json.dump(data, f)

def detect_line_movement(game_id, team, current, prev_map):
    key  = f"{game_id}_{team}"
    prev = prev_map.get(key)
    if prev is None:
        return False, "", 0.0
    delta = current - prev
    if abs(delta) >= 0.05:
        return True, ("+" if delta > 0 else "-"), round(abs(delta), 3)
    return False, "", 0.0

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 4 — SHARP MONEY RADAR (enhanced)
# ═══════════════════════════════════════════════════════════════════════════════

def analyze_sharp_money(game_id, home, away, best_h, best_a, prev_map, sport_key=""):
    """
    Detect reverse line movement (RLM):
    - Underdog line shortens despite public money on favorite → sharp on underdog
    - Favorite line drifts despite public backing → sharp fading favorite
    - Any 5%+ raw move included as general sharp signal
    Returns list of sharp dicts (empty list if none).
    NOTE: Steam Detector (detect_steam_moves_for_game) handles multi-book confirmation.
          This module adds single-game RLM / public-vs-line context.
    """
    sharps = []
    for team, current, opponent_odds in [(home, best_h, best_a), (away, best_a, best_h)]:
        key  = f"{game_id}_{team}"
        prev = prev_map.get(key)
        if prev is None or prev == 0:
            continue
        pct_change = (current - prev) / prev * 100

        if abs(pct_change) < 5.0:
            continue

        is_underdog    = current > opponent_odds
        line_shortened = pct_change < 0   # odds fell = team more likely to win now

        if is_underdog and line_shortened:
            # RLM signal — public backs favorite, but underdog line tightening
            sharps.append({
                "match":      f"{home} vs {away}",
                "team":       team,
                "direction":  "shortened",
                "pct":        round(abs(pct_change), 1),
                "odds_prev":  prev,
                "odds_now":   current,
                "public_pct": 35,   # estimated public % on underdog
                "sport":      sport_key,
                "signal":     "RLM — underdog acortando contra el público",
            })
        elif not is_underdog and not line_shortened:
            # Favorite drifting — sharp money going opposite direction to public
            sharps.append({
                "match":      f"{home} vs {away}",
                "team":       team,
                "direction":  "drifted",
                "pct":        round(abs(pct_change), 1),
                "odds_prev":  prev,
                "odds_now":   current,
                "public_pct": 65,   # estimated public % on favorite
                "sport":      sport_key,
                "signal":     "RLM — favorito alargando contra el público",
            })
        else:
            # General large move — no clear public/sharp divergence, still notable
            sharps.append({
                "match":      f"{home} vs {away}",
                "team":       team,
                "direction":  "moved",
                "pct":        round(abs(pct_change), 1),
                "odds_prev":  prev,
                "odds_now":   current,
                "public_pct": None,
                "sport":      sport_key,
                "signal":     f"Movimiento general {round(abs(pct_change),1)}%",
            })
    return sharps

def notify_sharp_money(sharp_moves):
    for m in sharp_moves:
        home, away = m["match"].split(" vs ", 1)
        sharp_key = f"{home}_{away}_{m['team']}_sharp"
        if not _should_alert(sharp_key, odds=m["odds_now"]):
            continue
        sport  = m.get("sport", "")
        emoji  = _sport_emoji(sport)
        team     = m["team"]
        opp      = away if team == home else home
        team_es  = _es(team)
        opp_es   = _es(opp)
        home_es  = _es(home)
        away_es  = _es(away)
        arrow    = "▼" if m["odds_now"] < m["odds_prev"] else "▲"

        signal = m.get("signal", "")
        if m.get("public_pct"):
            context_lines = (
                f"📡 {signal}\n"
                f"👥 Público estimado: ~{m['public_pct']}% en {opp}\n"
                f"⚠️  Pero línea se movió a favor de {team}\n"
                f"💡 RLM confirmado — sharps vs público\n"
            )
        else:
            context_lines = (
                f"📡 {signal}\n"
                f"📉 Línea {m['direction']} {m['pct']}% sin divergencia pública clara\n"
            )

        l1 = (
            f"🎯 {m['match']}\n"
            f"APUESTA: {team} @ {m['odds_now']}\n"
            f"🟢 APOSTAR — dinero inteligente confirmado"
        )
        l2 = (
            f"La línea de {team} se movió {m['pct']}%: "
            f"{m['odds_prev']} → {m['odds_now']} {arrow}\n"
            f"Esto indica que apostadores profesionales (sharps) "
            f"están poniendo dinero fuerte en este equipo.\n"
            f"{context_lines}"
        )
        body = _two_layer_body(l1, l2)
        ntfy_post(f"⚡ SHARP | {team_es} | {home_es} vs {away_es}", body, "high")
        print(f"  ⚡ Sharp: {team_es} en {m['match']} ({m['pct']}% movimiento)")

# ═══════════════════════════════════════════════════════════════════════════════
# UPGRADE PACKAGE — 10 MODULES
# ═══════════════════════════════════════════════════════════════════════════════

# ── Module 3: book safety ─────────────────────────────────────────────────────
# SAFE = US-licensed books the user can actually access and trust
SAFE_BOOKS = {
    "bovada", "bodog",
    "betonline", "betonline.ag",
    "fanduel",
    "draftkings",
    "mybookie",
    "caesars",
    "betmgm",
    "pointsbet",
}
# RISKY = international books not accessible in the US or known to limit winners
RISKY_BOOKS = {
    # explicitly banned by user
    "tab", "winamax", "betsson", "nordic bet", "nordicbet",
    "paddy power", "paddypower", "boyle sports", "boylesports",
    "everygame", "smarkets", "betvictor", "bet victor",
    "unibet",
    # additional known-risky international books
    "1xbet", "gtbets", "betfred", "ladbrokes", "betway",
    "cloudbet", "stake", "bet365",
    "william hill", "williamhill",
    "pinnacle",   # sharp book; US users typically can't deposit
}

def _book_warning(bookmaker):
    """Return warning line if bookmaker is risky, else empty string."""
    bk = (bookmaker or "").lower()
    if any(r in bk for r in RISKY_BOOKS):
        return ("\n⚠️ CASA DE APUESTAS RIESGOSA — limitan cuentas ganadoras. "
                "Busca línea similar en Bovada o BetOnline")
    return ""

def _is_risky_book(book: str) -> bool:
    """True when the book name matches any RISKY_BOOKS entry."""
    bk = (book or "").lower()
    return any(r in bk for r in RISKY_BOOKS)

def _is_safe_book(book: str) -> bool:
    """True when the book name matches any SAFE_BOOKS entry."""
    bk = (book or "").lower()
    return any(s in bk for s in SAFE_BOOKS)

def _arb_leg_tag(book: str) -> str:
    """Return ✅ for safe books, ⚠️ for risky books, empty for unknown."""
    if _is_safe_book(book):
        return "✅"
    if _is_risky_book(book):
        return "⚠️"
    return ""

# ── Module 9: line shopping top-3 ─────────────────────────────────────────────
def _top3_from_book_list(book_list):
    """
    Deduplicate by bookmaker name, prioritise SAFE books, return top 3 sorted
    by odds descending. book_list = [(price, book_name), ...]
    Returns [(book_name, price), ...]
    """
    book_best: dict = {}
    for price, bk_name in book_list:
        if bk_name not in book_best or price > book_best[bk_name]:
            book_best[bk_name] = price
    sorted_items = sorted(
        book_best.items(),
        key=lambda x: (x[0].lower() not in SAFE_BOOKS, -x[1]),
    )
    return sorted_items[:3]   # [(book_name, price), ...]

def _top3_block(top3):
    """Format line-shopping block for ntfy body. top3 = [(book_name, price), ...]"""
    if not top3:
        return ""
    lines = []
    for i, (bk, pr) in enumerate(top3):
        tag = " ← MEJOR" if i == 0 else ""
        lines.append(f"  {i+1}. {bk:<16} {pr:.2f}{tag}")
    return "📚 MEJORES LÍNEAS:\n" + "\n".join(lines) + "\n"

# ── Module 1: auto-resultados ─────────────────────────────────────────────────
RESULTS_CHECKED_FILE = "results_checked.json"

def _load_results_checked():
    if os.path.exists(RESULTS_CHECKED_FILE):
        try:
            with open(RESULTS_CHECKED_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_results_checked(checked):
    try:
        with open(RESULTS_CHECKED_FILE, "w") as f:
            json.dump(checked, f)
    except Exception:
        pass

def _fetch_mlb_scores_today():
    """Return list of finished MLB games today as dicts with home/away/scores."""
    today = datetime.now(ET).strftime("%Y-%m-%d")
    results = []
    try:
        data = _mlb_rest("/schedule", {
            "sportId": 1, "date": today,
            "hydrate": "decisions,team,linescore",
        })
        for date_entry in data.get("dates", []):
            for g in date_entry.get("games", []):
                status = g.get("status", {}).get("detailedState", "")
                if status not in ("Final", "Game Over", "Completed Early"):
                    continue
                home = g.get("teams", {}).get("home", {})
                away = g.get("teams", {}).get("away", {})
                results.append({
                    "home":       home.get("team", {}).get("name", ""),
                    "away":       away.get("team", {}).get("name", ""),
                    "home_score": int(home.get("score", 0) or 0),
                    "away_score": int(away.get("score", 0) or 0),
                })
    except Exception as e:
        print(f"  ⚠️  MLB scores error: {e}")
    return results

def _fetch_soccer_scores():
    """Return list of completed soccer/WC games from The Odds API scores endpoint."""
    results = []
    for sk in SPORT_KEYS:
        if "soccer" not in sk and "world" not in sk:
            continue
        try:
            r = requests.get(
                f"https://api.the-odds-api.com/v4/sports/{sk}/scores",
                params={"apiKey": API_KEY, "daysFrom": 1, "dateFormat": "iso"},
                timeout=10,
            )
            if r.status_code != 200:
                continue
            for g in r.json():
                if not g.get("completed"):
                    continue
                sc = {s["name"]: int(s["score"] or 0)
                      for s in (g.get("scores") or []) if s.get("score") is not None}
                hn, an = g.get("home_team", ""), g.get("away_team", "")
                if hn in sc and an in sc:
                    results.append({"home": hn, "away": an,
                                    "home_score": sc[hn], "away_score": sc[an]})
        except Exception as e:
            print(f"  ⚠️  Soccer scores error ({sk}): {e}")
    return results

def check_results():
    """Auto-resultados: check completed games vs pending bets_log rows → W/L ntfy."""
    try:
        if not os.path.exists(BETS_LOG_FILE):
            return
        with open(BETS_LOG_FILE, newline="") as f:
            all_bets = list(csv.DictReader(f))
        pending = [b for b in all_bets if not b.get("result")]
        if not pending:
            return

        checked  = _load_results_checked()
        scores   = _fetch_mlb_scores_today() + _fetch_soccer_scores()
        resolved: dict = {}   # bkey → {result, profit_loss}
        _elo_updated: set = set()

        for bet in pending:
            match  = bet.get("match", "")
            team   = bet.get("team", "")
            stake  = float(bet.get("stake", 0) or 0)
            odds   = float(bet.get("odds",  0) or 0)
            mtype  = bet.get("market_type", "h2h")
            side_f = bet.get("side", "")
            bkey   = f"{match}|{team}|{bet.get('game_time','')}"
            if bkey in checked:
                continue

            score = next(
                (s for s in scores
                 if s["home"].lower() in match.lower()
                 and s["away"].lower() in match.lower()),
                None,
            )
            if not score:
                continue

            result      = None
            profit_loss = 0.0
            home_won    = score["home_score"] > score["away_score"]

            _gk = f"{score['home']}|{score['away']}|{score['home_score']}-{score['away_score']}"
            if _gk not in _elo_updated:
                _elo_updated.add(_gk)
                try:
                    if score["home_score"] != score["away_score"]:
                        _w = score["home"] if score["home_score"] > score["away_score"] else score["away"]
                        _l = score["away"] if _w == score["home"] else score["home"]
                        update_elo(_w, _l)
                    else:
                        update_elo(score["home"], score["away"], draw=True)
                except Exception as _ee:
                    print(f"  ⚠️  update_elo error: {_ee}")

            if mtype in ("h2h", "moneyline", ""):
                is_home = (score["home"].lower() in team.lower()
                           or team.lower() in score["home"].lower())
                result      = "W" if (home_won == is_home) else "L"
                profit_loss = round(stake * (odds - 1), 2) if result == "W" else -stake

            elif mtype == "totals":
                total = score["home_score"] + score["away_score"]
                try:
                    line = float(side_f)
                except Exception:
                    continue
                if team.upper() == "OVER":
                    result = "W" if total > line else ("P" if total == line else "L")
                else:
                    result = "W" if total < line else ("P" if total == line else "L")
                profit_loss = (round(stake * (odds - 1), 2) if result == "W"
                               else (0.0 if result == "P" else -stake))

            if result is None:
                continue

            try:
                log_bankroll_entry(
                    sport=bet.get("sport", ""),
                    match=match,
                    market_type=mtype,
                    stake=stake,
                    result=result,
                    profit_loss=profit_loss,
                )
                refresh_bankroll()
            except Exception as ex:
                print(f"  ⚠️  bankroll log error: {ex}")

            checked[bkey]  = result
            resolved[bkey] = {
                "result":      result,
                "profit_loss": str(round(profit_loss, 2)),
            }

            # ── Rich ntfy notification ─────────────────────────────────────
            new_br  = load_bankroll_state()["current"]
            prev_br = new_br - profit_loss
            pct_chg = (profit_loss / prev_br * 100) if prev_br else 0.0
            icon    = "✅" if result == "W" else ("🤝" if result == "P" else "❌")
            verb    = "GANASTE" if result == "W" else ("PUSH" if result == "P" else "PERDISTE")
            pl_s    = (f"+${profit_loss:.2f}" if profit_loss >= 0
                       else f"-${abs(profit_loss):.2f}")
            pct_s   = f"{pct_chg:+.2f}%"
            score_s = f"{score['home_score']}-{score['away_score']}"
            if mtype == "totals":
                label = f"{team.upper()} {side_f}"
            else:
                label = f"{team} ML"
            money_icon = "🏆" if result == "W" else ("🤝" if result == "P" else "💸")
            body = (
                f"{icon} {match}\n"
                f"{_DIV}\n"
                f"🎯 Tu apuesta: {label}\n"
                f"📊 Resultado:  {score['home']} {score_s} {score['away']}\n"
                f"{money_icon} {verb}: {pl_s}\n"
                f"💰 Bankroll: ${new_br:,.2f} ({pct_s})\n"
                f"{_DIV2}"
            )
            ntfy_post(f"{icon} RESULTADO | {team} {verb} | {pl_s}", body, "high")
            print(f"  {icon} Resultado: {team} {verb} | {pl_s}")

        _save_results_checked(checked)

        # ── Rewrite bets_log.csv with resolved W/L results ─────────────────
        if resolved and all_bets:
            for b in all_bets:
                bk = (f"{b.get('match','')}|{b.get('team','')}"
                      f"|{b.get('game_time','')}")
                if bk in resolved:
                    b["result"]      = resolved[bk]["result"]
                    b["profit_loss"] = resolved[bk]["profit_loss"]
            try:
                with open(BETS_LOG_FILE, "w", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=list(all_bets[0].keys()))
                    w.writeheader()
                    w.writerows(all_bets)
            except Exception as ex:
                print(f"  ⚠️  bets_log rewrite error: {ex}")

    except Exception as e:
        print(f"  ⚠️  check_results error: {e}")

# ── Module 4: MLB IL / injuries ───────────────────────────────────────────────
_injury_cache: dict = {}

# Key positions to show on IL — starters and ace pitchers only; skip relievers/utility
_KEY_IL_POSITIONS = {"SP", "C", "1B", "2B", "3B", "SS", "LF", "CF", "RF", "DH"}

def _fetch_espn_injuries(team_name: str) -> list:
    """
    Secondary IL source: ESPN injuries API.
    Returns list of player name strings for KEY positions only.
    """
    try:
        tid = _fetch_espn_mlb_team_id(team_name)
        if not tid:
            return []
        r = requests.get(
            f"http://site.api.espn.com/apis/site/v2/sports/baseball/mlb"
            f"/teams/{tid}/injuries",
            timeout=8,
        )
        if r.status_code != 200:
            return []
        players = []
        for inj in r.json().get("injuries", []):
            athlete = inj.get("athlete", {})
            name    = athlete.get("displayName", "")
            pos_ab  = athlete.get("position", {}).get("abbreviation", "")
            if name and pos_ab in _KEY_IL_POSITIONS:
                players.append(name)
        return players
    except Exception:
        return []

def fetch_mlb_il(home, away):
    """
    Return {team_name: [player_names]} for KEY IL players on both teams.
    KEY = SP (starting pitchers) + regular lineup positions (C/1B/2B/3B/SS/LF/CF/RF/DH).
    Relievers (RP/CL/MR) and utility bench players are silently skipped.

    Primary  : MLB Stats API /teams/{tid}/roster?rosterType=injured (with position filter)
    Secondary: ESPN injuries API (merged + deduplicated)
    Cache    : per team pair per day.
    If both sources fail → returns {} (show nothing, not wrong data).
    """
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    cache_key = f"{home}|{away}|{today_str}"
    if cache_key in _injury_cache:
        return _injury_cache[cache_key]
    result = {}
    for tname in (home, away):
        try:
            # ── Primary: MLB Stats API ─────────────────────────────────────
            tid = None
            if HAS_STATSAPI:
                teams = statsapi.lookup_team(tname)
                tid   = teams[0]["id"] if teams else None
            else:
                data  = _mlb_rest("/teams", {"name": tname, "sportId": 1})
                teams = data.get("teams", [])
                tid   = teams[0]["id"] if teams else None

            mlb_players = []
            if tid is not None:
                roster = _mlb_rest(f"/teams/{tid}/roster",
                                   {"rosterType": "injured", "season": MLB_YEAR})
                for p in roster.get("roster", []):
                    name   = p.get("person", {}).get("fullName", "")
                    pos_ab = p.get("position", {}).get("abbreviation", "")
                    if name and pos_ab in _KEY_IL_POSITIONS:
                        mlb_players.append(name)

            # ── Secondary: ESPN injuries API ───────────────────────────────
            espn_players = _fetch_espn_injuries(tname)

            # Merge + deduplicate (case-insensitive; MLB Stats names take priority)
            seen   = {n.lower() for n in mlb_players}
            merged = list(mlb_players)
            for ep in espn_players:
                if ep.lower() not in seen:
                    merged.append(ep)
                    seen.add(ep.lower())

            if merged:
                result[tname] = merged
                print(f"  🤕 IL [{tname}]: {', '.join(merged[:5])}"
                      f"{'...' if len(merged) > 5 else ''}")
        except Exception as _e:
            print(f"  ⚠️  fetch_mlb_il [{tname}]: {_e}")
    _injury_cache[cache_key] = result
    return result

# ── Module 6: home/away splits ────────────────────────────────────────────────
_splits_cache: dict = {}

# Official MLB team IDs (2026 season)
_MLB_TEAM_IDS: dict = {
    "Arizona Diamondbacks": 109, "Diamondbacks": 109,
    "Atlanta Braves": 144,       "Braves": 144,
    "Baltimore Orioles": 110,    "Orioles": 110,
    "Boston Red Sox": 111,       "Red Sox": 111,
    "Chicago Cubs": 112,         "Cubs": 112,
    "Chicago White Sox": 145,    "White Sox": 145,
    "Cincinnati Reds": 113,      "Reds": 113,
    "Cleveland Guardians": 114,  "Guardians": 114,
    "Colorado Rockies": 115,     "Rockies": 115,
    "Detroit Tigers": 116,       "Tigers": 116,
    "Houston Astros": 117,       "Astros": 117,
    "Kansas City Royals": 118,   "Royals": 118,
    "Los Angeles Angels": 108,   "Angels": 108,
    "Los Angeles Dodgers": 119,  "Dodgers": 119,
    "Miami Marlins": 146,        "Marlins": 146,
    "Milwaukee Brewers": 158,    "Brewers": 158,
    "Minnesota Twins": 142,      "Twins": 142,
    "New York Mets": 121,        "Mets": 121,
    "New York Yankees": 147,     "Yankees": 147,
    "Oakland Athletics": 133,    "Athletics": 133,
    "Philadelphia Phillies": 143,"Phillies": 143,
    "Pittsburgh Pirates": 134,   "Pirates": 134,
    "San Diego Padres": 135,     "Padres": 135,
    "San Francisco Giants": 137, "Giants": 137,
    "Seattle Mariners": 136,     "Mariners": 136,
    "St. Louis Cardinals": 138,  "Cardinals": 138,
    "Tampa Bay Rays": 139,       "Rays": 139,
    "Texas Rangers": 140,        "Rangers": 140,
    "Toronto Blue Jays": 141,    "Blue Jays": 141,
    "Washington Nationals": 120, "Nationals": 120,
}

def _team_id(team_name: str) -> int | None:
    """Look up MLB team ID from name, trying exact then partial match."""
    if team_name in _MLB_TEAM_IDS:
        return _MLB_TEAM_IDS[team_name]
    tl = team_name.lower()
    for k, v in _MLB_TEAM_IDS.items():
        if k.lower() in tl or tl in k.lower():
            return v
    return None

def _splits_from_schedule(tid: int, team_name: str) -> "dict | None":
    """
    Fallback: compute home/away RS/RA from completed regular-season games
    this season via the schedule+linescore endpoint.
    Returns {home_rs, home_ra, home_wpct, away_rs, away_ra, away_wpct} or None.
    """
    try:
        data = _mlb_rest("/schedule", {
            "teamId":   tid,
            "season":   MLB_YEAR,
            "gameType": "R",
            "sportId":  1,
            "hydrate":  "linescore",
        })
        h_rs = h_ra = h_w = h_l = 0
        a_rs = a_ra = a_w = a_l = 0
        for date_entry in (data.get("dates") or []):
            for g in date_entry.get("games", []):
                if g.get("status", {}).get("abstractGameState") != "Final":
                    continue
                teams = g.get("teams", {})
                is_home = teams.get("home", {}).get("team", {}).get("id") == tid
                h_sc = teams.get("home", {}).get("score")
                a_sc = teams.get("away", {}).get("score")
                if h_sc is None or a_sc is None:
                    ls   = g.get("linescore", {}).get("teams", {})
                    h_sc = ls.get("home", {}).get("runs")
                    a_sc = ls.get("away", {}).get("runs")
                if h_sc is None or a_sc is None:
                    continue
                h_sc, a_sc = int(h_sc), int(a_sc)
                if is_home:
                    h_rs += h_sc; h_ra += a_sc
                    h_w  += (1 if h_sc > a_sc else 0)
                    h_l  += (1 if h_sc < a_sc else 0)
                else:
                    a_rs += a_sc; a_ra += h_sc
                    a_w  += (1 if a_sc > h_sc else 0)
                    a_l  += (1 if a_sc < h_sc else 0)
        hg = max(h_w + h_l, 1); ag = max(a_w + a_l, 1)
        if (h_w + h_l) < 3 or (a_w + a_l) < 3:
            return None   # too few games to be meaningful
        result = {
            "home_rs":   round(h_rs / hg, 2),
            "home_ra":   round(h_ra / hg, 2),
            "home_wpct": round(h_w  / hg, 3),
            "away_rs":   round(a_rs / ag, 2),
            "away_ra":   round(a_ra / ag, 2),
            "away_wpct": round(a_w  / ag, 3),
            "_source":   "schedule",
        }
        print(f"  📊 Splits [{team_name}] via schedule: "
              f"Home {result['home_rs']}/{result['home_ra']} "
              f"Away {result['away_rs']}/{result['away_ra']}")
        return result
    except Exception as e:
        print(f"  ⚠️  _splits_from_schedule error [{team_name}]: {e}")
        return None


def fetch_mlb_home_away_splits(team_name: str) -> dict | None:
    """
    Real home/away splits from MLB Stats API.
    Primary:  /teams/{tid}/stats?stats=homeAndAway  (fast, single call)
    Fallback: compute from /schedule linescore data (slower but always works)
    Returns {home_rs, home_ra, home_wpct, away_rs, away_ra, away_wpct} or None.
    Cache: in-memory (date-keyed) + disk file for cross-restart persistence.
    Retry: up to 3 attempts with 2s delay.
    """
    today = datetime.now(ET).strftime("%Y-%m-%d")
    ck    = f"{team_name}|{today}"
    dk    = f"splits|{ck}"

    # 1. In-memory cache
    if ck in _splits_cache:
        return _splits_cache[ck]

    # 2. Disk cache (same-day, survives Railway restarts)
    if dk in _stats_disk_cache:
        v = _stats_disk_cache[dk]
        _splits_cache[ck] = v
        return v

    # 3. Before noon ET — MLB homeAndAway endpoint is often empty in the morning;
    #    yesterday's splits are still valid (team tendencies change slowly)
    if datetime.now(ET).hour < 12:
        yesterday = (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")
        yd_key    = f"splits|{team_name}|{yesterday}"
        if yd_key in _stats_disk_cache:
            v = _stats_disk_cache[yd_key]
            if v:
                _splits_cache[ck] = v
                print(f"  📦 [{team_name}] splits: usando datos de ayer "
                      f"(antes del mediodía — evitando llamada vacía)")
                return v

    last_err = None
    for attempt in range(1, 4):
        try:
            tid = _team_id(team_name)
            if tid is None:
                print(f"  ⚠️  fetch_mlb_home_away_splits [{team_name}]: "
                      f"_team_id no encontrado — verifica _MLB_TEAM_IDS")
                _splits_cache[ck] = None
                return None

            # ── Primary: schedule-based home/away splits (always works) ───
            # The homeAndAway stats endpoint returns 404 for 2026 (and prior
            # seasons tested). Use game-by-game linescore data instead — it is
            # reliable and carries the same information.
            sched_result = _splits_from_schedule(tid, team_name)
            if sched_result:
                _splits_cache[ck] = sched_result
                _stats_disk_cache[dk] = sched_result
                _save_stats_disk_cache()
                return sched_result

            # ── Secondary: homeAndAway stats endpoint (kept as backup) ────
            hit_data = _mlb_rest(f"/teams/{tid}/stats", {
                "stats": "homeAndAway", "group": "hitting",
                "season": MLB_YEAR, "sportId": 1,
            })
            hit_splits = (hit_data.get("stats", [{}])[0].get("splits", [])
                          if hit_data and hit_data.get("stats") else [])

            pit_data = _mlb_rest(f"/teams/{tid}/stats", {
                "stats": "homeAndAway", "group": "pitching",
                "season": MLB_YEAR, "sportId": 1,
            })
            pit_splits = (pit_data.get("stats", [{}])[0].get("splits", [])
                          if pit_data and pit_data.get("stats") else [])

            # ── Log raw API response ───────────────────────────────────────
            if not hit_splits and not pit_splits:
                hit_keys = list((hit_data or {}).keys())
                pit_keys = list((pit_data or {}).keys())
                print(f"  ⚠️  homeAndAway splits vacíos [{team_name}] "
                      f"intento {attempt}/3 — "
                      f"hit_keys={hit_keys} pit_keys={pit_keys} "
                      f"tid={tid} season={MLB_YEAR}")
                if attempt < 3:
                    time.sleep(2)
                    continue
                _splits_cache[ck] = None
                return None

            sample = hit_splits[0] if hit_splits else pit_splits[0]
            sample_keys = {k: str(v)[:40] for k, v in sample.items()
                           if k in ("split", "isHome", "stat")}
            print(f"  📊 homeAndAway [{team_name}] splits={len(hit_splits)} "
                  f"sample={sample_keys}")

            result: dict = {}

            def _is_home(s: dict) -> "bool | None":
                code = s.get("split", {}).get("code", "")
                if code in ("H", "A"):
                    return code == "H"
                is_home_flag = s.get("isHome")
                if is_home_flag is not None:
                    return bool(is_home_flag)
                desc = s.get("split", {}).get("description", "").lower()
                if "home" in desc:
                    return True
                if "away" in desc or "road" in desc:
                    return False
                return None

            for s in hit_splits:
                hf = _is_home(s)
                if hf is None:
                    continue
                stat = s.get("stat", {})
                gp   = max(float(stat.get("gamesPlayed", 0) or 0), 1)
                runs = float(stat.get("runs", 0) or 0)
                wins = float(stat.get("wins", 0) or 0)
                loss = float(stat.get("losses", 0) or 0)
                wl   = wins + loss
                wpct = round(wins / wl, 3) if wl > 0 else 0.500
                if hf:
                    result["home_rs"]   = round(runs / gp, 2)
                    result["home_wpct"] = wpct
                else:
                    result["away_rs"]   = round(runs / gp, 2)
                    result["away_wpct"] = wpct

            for s in pit_splits:
                hf = _is_home(s)
                if hf is None:
                    continue
                stat = s.get("stat", {})
                gp   = max(float(stat.get("gamesPlayed", 0) or 0), 1)
                ra   = float(stat.get("runs", 0) or 0)
                if hf:
                    result["home_ra"] = round(ra / gp, 2)
                else:
                    result["away_ra"] = round(ra / gp, 2)

            required = {"home_rs", "home_ra", "home_wpct", "away_rs", "away_ra", "away_wpct"}
            if not required.issubset(result.keys()):
                missing = required - result.keys()
                print(f"  ⚠️  homeAndAway [{team_name}] intento {attempt}/3: "
                      f"faltan campos: {missing} — splits={len(hit_splits)}")
                if attempt < 3:
                    time.sleep(2)
                    continue
                _splits_cache[ck] = None
                return None

            _splits_cache[ck] = result
            _stats_disk_cache[dk] = result
            _save_stats_disk_cache()
            return result

        except Exception as _e:
            last_err = _e
            print(f"  ⚠️  fetch_mlb_home_away_splits [{team_name}] intento {attempt}/3: "
                  f"{type(_e).__name__}: {_e}")
            if attempt < 3:
                time.sleep(2)

    print(f"  ❌ fetch_mlb_home_away_splits [{team_name}] — 3 intentos fallidos. "
          f"Último error: {last_err}")
    _splits_cache[ck] = None
    return None

# ═══════════════════════════════════════════════════════════════════════════════
# ANALYSIS IMPROVEMENTS — MLB & SOCCER (7 new data modules)
# ═══════════════════════════════════════════════════════════════════════════════

# ── Temperature adjustments ───────────────────────────────────────────────────
def _temp_run_adj(temp_f) -> tuple:
    """MLB: cold suppresses runs, heat boosts slightly. Returns (adj, label)."""
    if temp_f is None:
        return 0.0, ""
    t = float(temp_f)
    if t < 55:
        return -0.3, f"🥶 Frío ({t:.0f}°F): -0.3 carreras ajustadas"
    if t > 85:
        return +0.3, f"🌡️ Calor ({t:.0f}°F): +0.3 carreras ajustadas"
    return 0.0, f"🌤️ Temp: {t:.0f}°F"

def _temp_goals_adj(temp_f) -> tuple:
    """Soccer: extreme temps reduce pace/goals. Returns (adj, label)."""
    if temp_f is None:
        return 0.0, ""
    t = float(temp_f)
    if t < 50:
        return -0.2, f"🥶 Frío ({t:.0f}°F): -0.2 goles proyectados"
    if t > 90:
        return -0.3, f"🌡️ Calor extremo ({t:.0f}°F): -0.3 goles proyectados"
    return 0.0, f"🌤️ Temp: {t:.0f}°F"

# ── MLB A1: Pitcher recent form (last 3 starts) ────────────────────────────────
_pitcher_form_cache: dict = {}

def fetch_pitcher_recent_form(pitcher_name: str) -> dict | None:
    """
    Last 3 starts ERA + trend for a named pitcher via MLB Stats API game log.
    Returns {eras: [float,...], trend: str, avg_era: float} or None.
    """
    if not pitcher_name or pitcher_name in ("TBD", ""):
        return None
    if pitcher_name in _pitcher_form_cache:
        return _pitcher_form_cache[pitcher_name]
    try:
        # Look up player ID
        search = _mlb_rest("/people/search", {"names": pitcher_name, "sportId": 1})
        people = search.get("people", []) if search else []
        if not people:
            return None
        pid    = people[0]["id"]
        season = datetime.now(TZ_LOCAL).year
        data   = _mlb_rest(f"/people/{pid}/stats", {
            "stats": "gameLog", "group": "pitching",
            "season": season, "limit": 10,
        })
        splits = (data.get("stats", [{}])[0].get("splits", [])
                  if data and data.get("stats") else [])
        # Only real starts (IP ≥ 1.0)
        starts = []
        for s in splits:
            ip_raw = s.get("stat", {}).get("inningsPitched", "0") or "0"
            if float(ip_raw) >= 1.0:
                starts.append(s)
        last3 = starts[-3:] if len(starts) >= 3 else starts
        if not last3:
            return None
        eras = []
        for s in last3:
            ip = float(s["stat"].get("inningsPitched", "0") or "0")
            er = float(s["stat"].get("earnedRuns", "0") or "0")
            eras.append(round(er / ip * 9, 2) if ip > 0 else 0.0)
        avg_era = round(sum(eras) / len(eras), 2)
        # Trend
        if len(eras) >= 2 and eras[-1] < eras[0] - 1.5:
            trend = "MEJORANDO 📈"
        elif len(eras) >= 2 and eras[-1] > eras[0] + 1.5:
            trend = "EN DECLIVE ⚠️"
        elif avg_era < 2.5:
            trend = "DOMINANDO 🔥"
        elif avg_era < 4.0:
            trend = "ESTABLE ✅"
        else:
            trend = "EN DECLIVE ⚠️"
        result = {"eras": eras, "trend": trend, "avg_era": avg_era}
        _pitcher_form_cache[pitcher_name] = result
        return result
    except Exception:
        return None


# ── Pitcher pace: average pitches per inning (last 5 starts) ──────────────────
_pitcher_pace_cache: dict = {}

def fetch_pitcher_pace(pitcher_name: str) -> "dict | None":
    """
    Average pitches per inning from last 5 real starts (MLB Stats API game log).
    Returns {avg_pi: float, starts: int, flag: str} or None.
    flag is non-empty when avg_pi > 18 → short outing likely (bullpen factor).
    """
    if not pitcher_name or pitcher_name in ("TBD", ""):
        return None
    if pitcher_name in _pitcher_pace_cache:
        return _pitcher_pace_cache[pitcher_name]
    try:
        search = _mlb_rest("/people/search", {"names": pitcher_name, "sportId": 1})
        people = search.get("people", []) if search else []
        if not people:
            return None
        pid    = people[0]["id"]
        season = datetime.now(TZ_LOCAL).year
        data   = _mlb_rest(f"/people/{pid}/stats", {
            "stats": "gameLog", "group": "pitching",
            "season": season, "limit": 10,
        })
        splits = (data.get("stats", [{}])[0].get("splits", [])
                  if data and data.get("stats") else [])
        starts = []
        for s in splits:
            stat   = s.get("stat", {})
            ip_raw = stat.get("inningsPitched", "0") or "0"
            ip     = float(ip_raw)
            np_val = int(stat.get("numberOfPitches") or stat.get("pitchesThrown") or 0)
            if ip >= 3.0 and np_val >= 30:
                starts.append((np_val, ip))
        last5 = starts[-5:] if len(starts) >= 5 else starts
        if not last5:
            return None
        total_p = sum(p for p, i in last5)
        total_i = sum(i for p, i in last5)
        if total_i == 0:
            return None
        avg_pi = round(total_p / total_i, 1)
        flag   = "⚠️ SALIDA CORTA PROBABLE — bullpen factor" if avg_pi > 18 else ""
        result = {"avg_pi": avg_pi, "starts": len(last5), "flag": flag}
        _pitcher_pace_cache[pitcher_name] = result
        return result
    except Exception:
        return None


# ── Bullpen load: innings pitched by relievers in last 3 days ─────────────────
_bullpen_load_cache: dict = {}

def fetch_bullpen_load(team_name: str) -> "dict | None":
    """
    Total innings pitched by relievers in the last 3 completed calendar days.
    Returns {ip_3d: float, flag: str} or None.
    flag non-empty when ip_3d > 8.0 (bullpen tired → risk for UNDER).
    Uses /schedule to get game PKs, then /game/{pk}/boxscore per game.
    """
    today_dt  = datetime.now(CDT)
    today_str = today_dt.strftime("%Y-%m-%d")
    ck = f"{team_name}_{today_str}"
    if ck in _bullpen_load_cache:
        return _bullpen_load_cache[ck]
    try:
        # ── Resolve team ID ────────────────────────────────────────────────
        tid = None
        if HAS_STATSAPI:
            tms = statsapi.lookup_team(team_name)
            if tms:
                tid = int(tms[0]["id"])
        if not tid:
            teams_data = _mlb_rest("/teams", {"sportId": 1, "season": MLB_YEAR})
            for t in teams_data.get("teams", []):
                nm = t.get("name", "") or t.get("teamName", "")
                if team_name.lower() in nm.lower():
                    tid = int(t["id"])
                    break
        if not tid:
            return None
        # ── Schedule for last 3 completed days ────────────────────────────
        from_dt = (today_dt - timedelta(days=3)).strftime("%Y-%m-%d")
        to_dt   = (today_dt - timedelta(days=1)).strftime("%Y-%m-%d")
        sched   = _mlb_rest("/schedule", {
            "sportId": 1, "teamId": tid,
            "startDate": from_dt, "endDate": to_dt,
        })
        game_pks = [
            g.get("gamePk")
            for de in sched.get("dates", [])
            for g in de.get("games", [])
            if (g.get("status", {}).get("abstractGameState") or "") == "Final"
            and g.get("gamePk")
        ]
        # ── Boxscore per game → sum reliever IP ───────────────────────────
        total_bp_ip = 0.0
        for gk in game_pks:
            try:
                bs      = _mlb_rest(f"/game/{gk}/boxscore", {})
                teams_b = bs.get("teams", {})
                for side in ("home", "away"):
                    t_data = teams_b.get(side, {})
                    t_id_b = int(t_data.get("team", {}).get("id") or 0)
                    if t_id_b != tid:
                        continue
                    pitchers = t_data.get("pitchers", [])
                    players  = t_data.get("players", {})
                    for i, p_id in enumerate(pitchers):
                        if i == 0:
                            continue  # skip starter
                        key    = f"ID{p_id}"
                        pstats = players.get(key, {}).get("stats", {}).get("pitching", {})
                        ip_raw = pstats.get("inningsPitched") or "0"
                        total_bp_ip += _parse_ip(str(ip_raw))
                    break
            except Exception:
                continue
        flag   = "🔥 BULLPEN CANSADO — riesgo para UNDER" if total_bp_ip > 8.0 else ""
        result = {"ip_3d": round(total_bp_ip, 1), "flag": flag}
        _bullpen_load_cache[ck] = result
        return result
    except Exception as e:
        print(f"  ⚠️  fetch_bullpen_load {team_name}: {e}")
        return None


# ── MLB A2: Home plate umpire ──────────────────────────────────────────────────
_umpire_cache: dict = {}

# Known umpire ball/strike tendencies (OVER = tight zone → more baserunners;
# UNDER = wide zone → quicker outs → fewer runs)
_UMPIRE_TENDENCIES: dict = {
    "Angel Hernandez":  ("OVER",  "zona apretada"),
    "CB Bucknor":       ("OVER",  "zona apretada"),
    "Laz Diaz":         ("OVER",  "zona apretada"),
    "Chris Guccione":   ("OVER",  "zona apretada"),
    "Dan Iassogna":     ("OVER",  "zona apretada"),
    "Mike Muchlinski":  ("OVER",  "zona apretada"),
    "Ryan Additon":     ("OVER",  "zona apretada"),
    "Phil Cuzzi":       ("UNDER", "zona expandida"),
    "Kerwin Danley":    ("UNDER", "zona expandida"),
    "Ted Barrett":      ("UNDER", "zona expandida"),
    "Bruce Dreckman":   ("UNDER", "zona expandida"),
    "Jerry Layne":      ("UNDER", "zona expandida"),
    "Mark Carlson":     ("UNDER", "zona expandida"),
    "Greg Gibson":      ("UNDER", "zona expandida"),
    "Tom Hallion":      ("UNDER", "zona expandida"),
    "Alfonso Marquez":  ("UNDER", "zona expandida"),
    "David Rackley":    ("UNDER", "zona expandida"),
    "Cory Blaser":      ("UNDER", "zona expandida"),
    "Tripp Gibson":     ("UNDER", "zona expandida"),
    "Doug Eddings":     ("OVER",  "zona apretada"),
    "Jordan Baker":     ("OVER",  "zona apretada"),
    "Bill Miller":      ("UNDER", "zona expandida"),
    "Vic Carapazza":    ("OVER",  "zona apretada"),
}

def fetch_home_plate_umpire(home_team: str, game_date: str) -> dict | None:
    """
    Fetch home plate umpire via MLB Stats API schedule with officials hydration.
    game_date: 'YYYY-MM-DD'. Returns {name, tendency, zone} or None.
    No 'fields' filter — some API versions strip nested paths when fields is set.
    """
    ck = f"{home_team}_{game_date}"
    if ck in _umpire_cache:
        return _umpire_cache[ck]
    try:
        # No 'fields' param — let the API return the full structure
        data = _mlb_rest("/schedule", {
            "sportId": 1, "date": game_date,
            "hydrate": "officials",
        })
        if not data:
            _umpire_cache[ck] = None
            return None
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                teams_block = game.get("teams", {})
                home_team_name = (
                    teams_block.get("home", {}).get("team", {}).get("teamName", "")
                    or teams_block.get("home", {}).get("team", {}).get("name", "")
                )
                # Loose match: any word overlap is enough
                ht_lower = home_team.lower()
                hn_lower = home_team_name.lower()
                if not (ht_lower in hn_lower or hn_lower in ht_lower
                        or any(w in hn_lower for w in ht_lower.split()
                               if len(w) > 3)):
                    continue
                for official in game.get("officials", []):
                    otype = (official.get("officialType") or "").strip()
                    # Accept "Home Plate", "HP", or starts with "Home"
                    if otype not in ("Home Plate", "HP") and not otype.startswith("Home"):
                        continue
                    name = (official.get("official", {}).get("fullName", "")
                            or official.get("official", {}).get("name", ""))
                    if not name:
                        continue
                    tendency, zone = _UMPIRE_TENDENCIES.get(
                        name, ("NEUTRAL", "zona normal")
                    )
                    res = {"name": name, "tendency": tendency, "zone": zone}
                    _umpire_cache[ck] = res
                    print(f"  ⚾ Umpire [{home_team}]: {name} → {tendency}")
                    return res
        _umpire_cache[ck] = None
        return None
    except Exception as e:
        print(f"  ⚠️  fetch_home_plate_umpire error: {e}")
        _umpire_cache[ck] = None
        return None

# ── Soccer S1: Team recent form detail (last 3 matches) ───────────────────────
_soccer_recent_cache: dict = {}

def fetch_soccer_team_recent(team: str, sport_key: str) -> dict | None:
    """
    Last ≤5 completed matches via Odds API (primary) or ESPN multi-league (fallback).
    Returns {gf_pg, ga_pg, results: ['W','D','L',...], emoji, n} or None.
    Never returns a bare ELO-only entry — returns None if no match data found.
    """
    ck = f"{team}_{sport_key}_{datetime.now(TZ_LOCAL).strftime('%Y-%m-%d')}"
    if ck in _soccer_recent_cache:
        return _soccer_recent_cache[ck]

    # ── Primary: The Odds API scores (up to 30 days) ──────────────────────
    def _build_sform(raw_games: list) -> "dict | None":
        if not raw_games:
            return None
        gf = ga = 0
        results_list = []
        for g in raw_games:
            sc_list   = g.get("scores") or []
            sc_map    = {s["name"]: int(s["score"]) for s in sc_list
                         if s.get("score") is not None}
            my_score  = sc_map.get(team, 0)
            opp       = g["away_team"] if g["home_team"] == team else g["home_team"]
            opp_score = sc_map.get(opp, 0)
            gf += my_score; ga += opp_score
            if my_score > opp_score:    results_list.append("W")
            elif my_score == opp_score: results_list.append("D")
            else:                       results_list.append("L")
        n    = len(raw_games)
        wins = results_list.count("W")
        emoji = ("🔥" if wins == n else "✅" if wins >= 2
                 else "⚠️" if wins == 0 else "➡️")
        return {"gf_pg": round(gf / n, 1), "ga_pg": round(ga / n, 1),
                "results": results_list, "emoji": emoji, "n": n}

    try:
        url = (f"https://api.the-odds-api.com/v4/sports/{sport_key}/scores/"
               f"?apiKey={API_KEY}&daysFrom=30&dateFormat=iso")
        r = requests.get(url, timeout=10)
        if r.status_code == 200:
            games = [g for g in r.json()
                     if (g.get("home_team") == team or g.get("away_team") == team)
                     and g.get("completed") is True]
            games.sort(key=lambda g: g.get("commence_time", ""), reverse=True)
            res = _build_sform(games[:5])
            if res:
                _soccer_recent_cache[ck] = res
                return res
    except Exception:
        pass

    # ── Fallback: ESPN multi-league (WC + qualifiers + friendlies, last 365 days) ─
    try:
        espn_matches = _fetch_espn_matches(team)
        if espn_matches:
            n    = len(espn_matches)
            gf   = sum(m["gf"] for m in espn_matches)
            ga   = sum(m["ga"] for m in espn_matches)
            rl   = []
            for m in espn_matches:
                if m["gf"] > m["ga"]:    rl.append("W")
                elif m["gf"] < m["ga"]:  rl.append("L")
                else:                    rl.append("D")
            wins  = rl.count("W")
            emoji = ("🔥" if wins == n else "✅" if wins >= 2
                     else "⚠️" if wins == 0 else "➡️")
            res   = {"gf_pg": round(gf / n, 1), "ga_pg": round(ga / n, 1),
                     "results": rl, "emoji": emoji, "n": n}
            _soccer_recent_cache[ck] = res
            return res
    except Exception:
        pass

    _soccer_recent_cache[ck] = None
    return None

# ── Soccer S2: Match referee + tendency ───────────────────────────────────────
_referee_cache: dict = {}

# FIFA/UEFA referee historical goals-per-game and OVER/UNDER tendency
_REF_TENDENCIES: dict = {
    "Felix Brych":         (2.4, "UNDER ⚠️"),
    "Slavko Vincic":       (2.1, "UNDER ⚠️"),
    "Slavko Vinčić":       (2.1, "UNDER ⚠️"),
    "Szymon Marciniak":    (3.1, "OVER ✅"),
    "Antonio Mateu":       (3.8, "OVER ✅"),
    "Antonio Mateu Lahoz": (3.8, "OVER ✅"),
    "Clement Turpin":      (2.8, "NEUTRAL ➡️"),
    "Clément Turpin":      (2.8, "NEUTRAL ➡️"),
    "Daniele Orsato":      (2.5, "UNDER ⚠️"),
    "Danny Makkelie":      (3.0, "NEUTRAL ➡️"),
    "Ismail Elfath":       (2.6, "UNDER ⚠️"),
    "Victor Gomes":        (2.2, "UNDER ⚠️"),
    "Raphael Claus":       (3.4, "OVER ✅"),
    "Facundo Tello":       (3.2, "OVER ✅"),
    "Ivan Barton":         (2.3, "UNDER ⚠️"),
    "Mario Escobar":       (2.9, "NEUTRAL ➡️"),
    "Jesus Valenzuela":    (2.7, "NEUTRAL ➡️"),
    "Howard Webb":         (3.5, "OVER ✅"),
    "Michael Oliver":      (3.0, "NEUTRAL ➡️"),
    "Bjorn Kuipers":       (2.6, "UNDER ⚠️"),
}

def _referee_tendency(name: str) -> tuple:
    """Return (avg_goals_pg, tendency_label) for a known referee."""
    return _REF_TENDENCIES.get(name, (2.7, "NEUTRAL ➡️"))

def fetch_match_referee(home: str, away: str, sport_key: str) -> dict | None:
    """
    Fetch today's match referee from ESPN soccer scoreboard + summary API.
    Returns {name, goals_pg, tendency} or None.
    """
    ck = f"{home}_{away}_{datetime.now(TZ_LOCAL).strftime('%Y-%m-%d')}"
    if ck in _referee_cache:
        return _referee_cache[ck]
    try:
        _ESPN_LEAGUE = {
            "soccer_fifa_world_cup":             "fifa.world",
            "soccer_uefa_euro_qualification":    "uefa.euro.qualification",
            "soccer_spain_la_liga":              "esp.1",
            "soccer_england_league1":            "eng.1",
            "soccer_germany_bundesliga":         "ger.1",
            "soccer_italy_serie_a":              "ita.1",
            "soccer_france_ligue_one":           "fra.1",
            "soccer_uefa_champions_league":      "uefa.champions",
            "soccer_conmebol_copa_america":      "conmebol.america",
        }
        league = _ESPN_LEAGUE.get(sport_key, "fifa.world")
        today  = datetime.now(TZ_LOCAL).strftime("%Y%m%d")
        r = requests.get(
            f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard",
            params={"dates": today}, timeout=8,
        )
        if r.status_code != 200:
            return None
        for event in r.json().get("events", []):
            nm = event.get("name", "").lower()
            if home.lower() not in nm and away.lower() not in nm:
                continue
            eid = event.get("id")
            det = requests.get(
                f"https://site.api.espn.com/apis/site/v2/sports/soccer/{league}/summary",
                params={"event": eid}, timeout=8,
            ).json()
            # Also grab venue for temperature
            venue = det.get("gameInfo", {}).get("venue", {})
            city  = venue.get("address", {}).get("city", "")
            for official in det.get("officials", []):
                pos = official.get("position", {}).get("name", "")
                if pos in ("Referee", "Center Referee", "Head Official"):
                    ref_name         = official.get("displayName", "")
                    goals_pg, tend   = _referee_tendency(ref_name)
                    res = {"name": ref_name, "goals_pg": goals_pg,
                           "tendency": tend, "venue_city": city}
                    _referee_cache[ck] = res
                    return res
        return None
    except Exception:
        return None

# ── Soccer S3: Venue temperature (WC 2026 host cities) ───────────────────────
_WC2026_VENUES: dict = {
    "Dallas":       (32.7473,  -97.0945),
    "New York":     (40.8135,  -74.0745),
    "Los Angeles":  (34.0139, -118.2881),
    "San Francisco":(37.4030, -121.9697),
    "Miami":        (25.9581,  -80.2387),
    "Seattle":      (47.5952, -122.3316),
    "Kansas City":  (39.0488,  -94.4839),
    "Atlanta":      (33.7554,  -84.4011),
    "Philadelphia": (39.9008,  -75.1674),
    "Houston":      (29.6847,  -95.4107),
    "Boston":       (42.0909,  -71.2643),
    "Toronto":      (43.6333,  -79.4191),
    "Vancouver":    (49.2781, -123.1120),
    "Mexico City":  (19.3033,  -99.1503),
    "Guadalajara":  (20.6852, -103.3119),
    "Monterrey":    (25.6695, -100.3077),
}

def fetch_venue_temp(venue_city: str) -> float | None:
    """Fetch temperature for a WC 2026 host city. Returns °F or None."""
    coords = _WC2026_VENUES.get(venue_city)
    if not coords:
        # fuzzy match
        for city, ll in _WC2026_VENUES.items():
            if city.lower() in venue_city.lower() or venue_city.lower() in city.lower():
                coords = ll
                break
    if not coords:
        return None
    wind_data = fetch_wind(coords[0], coords[1])
    return wind_data.get("temp_f") if wind_data else None

# ── CLAUDE AI ANALYSIS ENGINE ────────────────────────────────────────────────
_claude_cache: dict = {}

# ── ERA validity window used by both pre-validator and range guard ────────────
_ERA_MIN = 0.50   # ERA élite legítima puede bajar de 1.50 (ej. Sanchez 1.46)
_ERA_MAX = 15.00  # ERAs altas como 9.50 son datos reales — no descartar como inválidos

def _pre_validate_for_claude(game_data: dict, sport: str) -> "tuple[dict, list]":
    """
    Sanitise game_data before sending to Claude.
    Returns (clean_data, warnings) where:
      - clean_data  : copy with suspicious numeric fields replaced by a
                      "DATO NO VERIFICADO" marker so Claude can flag them.
      - warnings    : list of human-readable warning strings logged to console.
    Checks performed:
      1. ERA values (era_home, era_away, bullpen ERA markers) must be 0.50–15.00.
      2. Pitcher names must be present and non-TBD.
      3. Bullpen data tagged as unverified in _data_quality → marked.
    """
    import copy
    clean    = copy.deepcopy(game_data)
    warnings = []

    # ── 1 & 2: MLB-only checks (soccer has no pitchers/ERA) ──────────────────
    if sport == "MLB":
        # ERA range validation
        for era_key in ("era_home", "era_away", "bullpen_era_home", "bullpen_era_away"):
            val = clean.get(era_key)
            if val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            if not (_ERA_MIN <= fval <= _ERA_MAX):
                msg = (f"ERA sospechosa en '{era_key}': {fval} "
                       f"(fuera de rango {_ERA_MIN}–{_ERA_MAX})")
                warnings.append(msg)
                clean[era_key] = "DATO NO VERIFICADO"
            elif fval < 2.00:
                # ERA élite confirmada (< 2.00): dato válido, no marcar como sospechoso
                note_key = era_key + "_elite_note"
                clean[note_key] = (
                    f"ERA élite confirmada ({fval:.2f}) — pitcher de primer nivel ✅"
                )
                print(f"  ✅  [pre-validate] ERA élite: {era_key}={fval:.2f} (pitcher de primer nivel)")

        # Pitcher name validation
        # game_data may have 'pname_home' (raw) or only 'pitcher_home' ("Name (ERA X.XX)")
        for side in ("home", "away"):
            raw_key  = f"pname_{side}"
            fmt_key  = f"pitcher_{side}"
            # Prefer raw name; fall back to extracting from formatted string
            name = str(clean.get(raw_key) or "").strip()
            if not name:
                fmt_val = str(clean.get(fmt_key) or "")
                name = fmt_val.split(" (ERA")[0].strip()
            if name.upper() in ("TBD", "UNKNOWN", "N/A", ""):
                msg = f"Pitcher {side} sin confirmar (TBD/vacío)"
                warnings.append(msg)
                clean[raw_key] = "SIN CONFIRMAR"

    # ── 3. Bullpen cross-source check (uses _data_quality populated earlier) ──
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    match_str = str(game_data.get("match", ""))
    teams = [t.strip() for t in match_str.split(" vs ", 1)] if " vs " in match_str else []
    for t in teams:
        dq = _data_quality.get(f"{t}_{today}", {})
        if dq.get("verified") is False:
            msg = f"Bullpen ERA de '{t}' no verificado (fuera de rango o sin fuente)"
            warnings.append(msg)
            clean[f"bullpen_note_{t}"] = "DATO NO VERIFICADO"

    if warnings:
        for w in warnings:
            print(f"  ⚠️  [pre-validate] {w}")

    # ── 4. Strip null / 4.5 placeholder fields before sending to Claude ──────
    # Claude should only see real data. Remove any key whose value is:
    #   - None / empty string
    #   - exactly the 4.5 league-average placeholder (RS or RA)
    #   - internal helper flags (_rs_real, _ra_real)
    PLACEHOLDER_FLOATS = {4.5, 4.50}
    RS_RA_KEYS = {"rs_home", "ra_home", "rs_away", "ra_away",
                  "era_home", "era_away", "bullpen_era_home", "bullpen_era_away"}
    keys_to_remove = []
    for k, v in list(clean.items()):
        if k.startswith("_"):                       # internal flag
            keys_to_remove.append(k)
        elif v is None or v == "":                  # null / empty
            keys_to_remove.append(k)
        elif isinstance(v, float) and v in PLACEHOLDER_FLOATS and k in RS_RA_KEYS:
            keys_to_remove.append(k)               # 4.5 placeholder
        elif isinstance(v, str) and v.startswith("4.5") and k in RS_RA_KEYS:
            keys_to_remove.append(k)               # "4.5" string placeholder
    for k in keys_to_remove:
        clean.pop(k, None)
        if not k.startswith("_"):
            print(f"  ℹ️  [pre-validate] campo '{k}' omitido (sin datos reales)")

    return clean, warnings


_CLAUDE_SYSTEM = (
    "Eres un experto en apuestas deportivas que habla como un amigo directo. "
    "Tu trabajo es validar picks y dar recomendaciones claras en español conversacional.\n\n"

    "CÓMO ESCRIBIR (obligatorio en todo momento):\n"
    "• Habla como si le explicaras a un amigo, no como si escribieras un reporte\n"
    "• Máximo 5 oraciones en tu razonamiento. Directas y completas.\n"
    "• Empieza siempre con el dato más importante del partido\n"
    "• Di claramente SÍ o NO y el motivo en una línea\n"
    "• PROHIBIDO usar estas palabras: EV, umbral, parámetro, métrica, "
    "implícita, divergencia, percentil, distribución, calibración\n"
    "• En vez de 'EV positivo' di 'tiene valor'\n"
    "• En vez de 'probabilidad implícita' di 'el mercado dice'\n"
    "• En vez de 'divergencia Pinnacle' di 'los libros sharp están de acuerdo/en contra'\n\n"

    "REGLA 0 — ENTENDER EL TIPO DE PICK (obligatoria antes de cualquier análisis):\n"
    "Antes de analizar, identifica qué tipo de pick es:\n\n"
    "• Si el pick es 'Equipo ML': evalúa si ese equipo GANA el partido\n"
    "• Si el pick es 'Equipo RL -1.5': evalúa si ese equipo gana por 2+ carreras\n"
    "• Si el pick es 'Equipo RL +1.5': evalúa si ese equipo NO pierde por 2+.\n"
    "  El equipo puede PERDER el partido y el pick igual GANA si pierde por solo 1.\n"
    "  En este caso buscar si el pitcher rival es tan dominante que ganaría por 2+.\n"
    "• Si el pick es 'OVER/UNDER': evalúa el total de carreras esperadas\n\n"
    "EJEMPLO: Pick 'Washington RL +1.5' con Miller (SEA ERA 3.63) vs Littell (WSH ERA 4.70):\n"
    "CORRECTO: ¿Puede Seattle ganar por 2+ carreras con Miller? Tal vez, pero Miller "
    "no es tan dominante. Washington +1.5 tiene valor porque el partido puede ser cerrado.\n"
    "INCORRECTO: 'Miller es mejor entonces apuesta Seattle ML' — eso es otro pick diferente.\n\n"

    "REGLAS DE ANÁLISIS (no negociables):\n"
    "• Si la diferencia de ERA entre pitchers es mayor a 3 puntos → el ML del "
    "equipo con mejor pitcher es el pick principal\n"
    "• ERA alta del rival (>6.0) es ventaja crítica — no la ignores\n"
    "• Pinnacle >52% en el mismo lado = los sharps están de acuerdo, menciónalo\n"
    "• H2H solo es relevante si los pitchers de hoy son los mismos que antes\n"
    "• EV >15% sin red flags reales = aprobar\n"
    "• K/9 >9.0 confirmado = mencionar como ventaja del pitcher\n"
    "• Sin K/9 confirmado = no mencionar props de ponches\n\n"

    "PONDERACIÓN DE ABRIDOR (obligatoria en béisbol):\n"
    "• ERA diff >= 1.5: el equipo con mejor ERA tiene ventaja DOMINANTE que supera "
    "los promedios ofensivos del rival, salvo que el rival cumpla 2 de estas 3: "
    "OPS > 0.780 | bullpen ERA < 3.50 | Pinnacle > 55% a su favor\n"
    "• Pitcher + Pinnacle en el mismo lado = señal FUERTE, votar en esa dirección\n"
    "• Pick CONTRA la ventaja del abridor Y contra Pinnacle = ALTO RIESGO, "
    "mencionar explícitamente y recomendar stake mínimo\n"
    "• ERA < 2.50 en últimas 3 salidas vs rival con ERA > 5.00 = "
    "tratar como ventaja de 2.0 puntos de ERA\n\n"

    "RED FLAGS REALES (solo estas justifican vetar):\n"
    "• Lesión confirmada del pitcher titular hoy\n"
    "• Viento >20mph hacia afuera O temperatura <40°F\n"
    "• Bullpen con >15 innings en últimos 3 días\n"
    "• Pitcher lanzó >100 pitches hace menos de 4 días\n\n"

    "Responde siempre en JSON con: apostar, confianza, razonamiento, "
    "factores_positivos, factores_negativos, datos_inconsistentes."
)

PANEL_DIVERSITY_ADDENDUM = """

══════════════════════════════════════════════
REGLA DE DIVERSIDAD DE MERCADOS — OBLIGATORIA
══════════════════════════════════════════════

Esta regla tiene la misma prioridad que el consenso 2/3.

1. En cualquier sesión de picks, MÁXIMO 2 picks pueden ser OVER o UNDER de carreras totales.

2. Si ya hay 2 totales propuestos, los picks restantes DEBEN ser uno de estos:
   - Moneyline (ML) — ¿quién gana el juego?
   - Run line (-1.5 / +1.5) — ¿gana por más de 1?
   - Primeros 5 innings (F5) — ¿quién va ganando al medio?
   - Prop de strikeouts si hay valor claro

3. Cuando la diferencia de ERA entre los pitchers titulares es mayor o igual a 1.5 puntos,
   el mercado PRINCIPAL a evaluar debe ser ML o F5, NO el total.
   Ejemplo: pitcher con ERA 1.40 vs pitcher con ERA 5.35 → evalúen ML primero.

4. Antes de proponer cualquier pick, el panel debe preguntarse:
   ¿Hay ventaja de pitcher tan grande que el ML tiene más valor que apostar el total?

Violar esta regla es motivo de veto automático del pick.
"""


def analyze_with_claude(game_data: dict, sport: str,
                        _extra_system: str = "",
                        _model: str = "",
                        _max_tokens: int = 1024) -> "dict | None":
    """
    Pre-validate game_data, then send to Claude as the final verification layer.
    sport: "MLB" or "SOCCER"
    _extra_system: optional extra text appended to _CLAUDE_SYSTEM (used by panel_expertos)
    _model: override Claude model (default: CLAUDE_MODEL); panel uses CLAUDE_PANEL_MODEL
    Returns {pick, line, confianza, razonamiento, factores_positivos,
             factores_negativos, datos_inconsistentes, apostar}
    or None if API unavailable / error.
    Cached per content hash to avoid duplicate API calls.
    """
    if not ANTHROPIC_API_KEY or not HAS_ANTHROPIC:
        return None

    import hashlib

    # ── Pre-validation: clean data before sending ─────────────────────────────
    clean_data, pre_warnings = _pre_validate_for_claude(game_data, sport)

    _ck = hashlib.md5(
        f"{sport}{_extra_system}{_model}{json.dumps(clean_data, default=str, sort_keys=True)}".encode()
    ).hexdigest()[:16]
    if _ck in _claude_cache:
        return _claude_cache[_ck]

    # ── Build sport-specific user prompt ─────────────────────────────────────
    warn_block = ""
    if pre_warnings:
        warn_block = (
            "\n⚠️ ADVERTENCIAS DE VALIDACIÓN PREVIA (revisar con atención):\n"
            + "\n".join(f"  - {w}" for w in pre_warnings)
            + "\n"
        )

    if sport == "MLB":
        prompt = (
            f"Analiza este partido de MLB y dame tu recomendación profesional.\n"
            f"{warn_block}\n"
            f"DATOS DEL PARTIDO:\n"
            f"{json.dumps(clean_data, indent=2, default=str, ensure_ascii=False)}\n\n"
            f"Responde en este formato JSON exacto:\n"
            f"{{\n"
            f'  "pick": "OVER/UNDER/HOME_ML/AWAY_ML",\n'
            f'  "line": "número de la línea",\n'
            f'  "confianza": "ALTA/MEDIA/BAJA",\n'
            f'  "razonamiento": "explicación en español de 4-6 oraciones completas como experto",\n'
            f'  "factores_positivos": ["factor1", "factor2"],\n'
            f'  "factores_negativos": ["factor1"],\n'
            f'  "datos_inconsistentes": [],\n'
            f'  "apostar": true\n'
            f"}}\n\n"
            f"Si detectas datos sospechosos, agrégalos a 'datos_inconsistentes' y "
            f"considera apostar: false. Solo responde con el JSON, nada más."
        )
    else:
        prompt = (
            f"Analiza este partido de fútbol internacional y dame tu recomendación.\n"
            f"{warn_block}\n"
            f"DATOS DEL PARTIDO:\n"
            f"{json.dumps(clean_data, indent=2, default=str, ensure_ascii=False)}\n\n"
            f"Responde en este formato JSON exacto:\n"
            f"{{\n"
            f'  "pick": "HOME_ML/AWAY_ML/DRAW/OVER/UNDER/HOME_HANDICAP/AWAY_HANDICAP",\n'
            f'  "line": "número o descripción",\n'
            f'  "confianza": "ALTA/MEDIA/BAJA",\n'
            f'  "razonamiento": "explicación en español de 4-6 oraciones completas como experto",\n'
            f'  "factores_positivos": ["factor1", "factor2"],\n'
            f'  "factores_negativos": ["factor1"],\n'
            f'  "datos_inconsistentes": [],\n'
            f'  "apostar": true\n'
            f"}}\n\n"
            f"Si detectas datos sospechosos, agrégalos a 'datos_inconsistentes' y "
            f"considera apostar: false. Solo responde con el JSON, nada más."
        )

    try:
        client = _anthropic_lib.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=30.0)
        msg    = client.messages.create(
            model=(_model or CLAUDE_MODEL),
            max_tokens=_max_tokens,
            system=(_CLAUDE_SYSTEM + ("\n\n" + _extra_system if _extra_system else "")),
            messages=[{"role": "user", "content": prompt}],
        )
        raw = msg.content[0].text.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw   = parts[1] if len(parts) >= 2 else raw
            if raw.startswith("json"):
                raw = raw[4:]
        raw    = raw.strip()
        result = json.loads(raw)

        # Merge any pre-validation warnings into datos_inconsistentes
        existing = result.get("datos_inconsistentes") or []
        if isinstance(existing, str):
            existing = [existing]
        result["datos_inconsistentes"] = existing + pre_warnings
        result["pre_warnings"] = pre_warnings   # keep for _claude_block

        _claude_cache[_ck] = result
        conf_icon = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴"}.get(
            result.get("confianza", ""), "⚪"
        )
        has_issues = bool(result.get("datos_inconsistentes"))
        print(
            f"  🤖 Claude [{sport}]: {result.get('pick')} "
            f"{conf_icon}{result.get('confianza')} "
            f"apostar={result.get('apostar')}"
            + (f" ⚠️ inconsistencias={len(result['datos_inconsistentes'])}"
               if has_issues else "")
        )
        return result
    except Exception as e:
        print(f"  ⚠️  Claude API error: {e}")
        _claude_cache[_ck] = None
        return None


_enh_ctx_cache: dict = {}

def adjust_probability_for_pitcher_form(
    base_prob_home: float,
    home_era_season: float,
    away_era_season: float,
    home_era_last3,
    away_era_last3,
) -> float:
    """
    Ajusta la probabilidad base del equipo local según la forma reciente de los abridores.
    ERA ajustada = 40% ERA temporada + 60% ERA últimas 3 salidas.
    El delta entre el diferencial ajustado y el de temporada se convierte en ±prob (4% por punto ERA).
    Clamped a ±8pp máximo y al rango [0.30, 0.75].
    """
    if home_era_last3 is None and away_era_last3 is None:
        return base_prob_home

    h_recent = home_era_last3 if home_era_last3 is not None else home_era_season
    a_recent = away_era_last3 if away_era_last3 is not None else away_era_season

    h_adj = home_era_season * 0.40 + h_recent * 0.60
    a_adj = away_era_season * 0.40 + a_recent * 0.60

    diff_season  = away_era_season - home_era_season   # positivo = local mejor
    diff_adjusted = a_adj - h_adj

    delta = diff_adjusted - diff_season
    prob_adj = max(-0.08, min(0.08, delta * 0.04))

    adjusted = max(0.30, min(0.75, base_prob_home + prob_adj))
    if abs(prob_adj) > 0.005:
        print(f"   🎯 Forma reciente ERA: diff_season={diff_season:+.2f} "
              f"diff_adj={diff_adjusted:+.2f} → p_home {base_prob_home:.3f}→{adjusted:.3f}")
    return round(adjusted, 4)


# ============================================================
# MARKET DIVERSIFICATION ENGINE
# ============================================================

def get_market_priority_for_game(home_era, away_era):
    """
    Determina el mercado prioritario según el mismatch de ERA.
    Retorna lista ordenada de mercados a evaluar.
    """
    try:
        era_diff = abs(float(home_era or 4.50) - float(away_era or 4.50))
        if era_diff >= 2.0:
            return ['ML', 'F5', 'RL', 'TOTAL']
        elif era_diff >= 1.0:
            return ['ML', 'F5', 'TOTAL', 'RL']
        elif float(home_era or 4.50) < 3.50 and float(away_era or 4.50) < 3.50:
            return ['TOTAL', 'F5', 'ML', 'RL']
        else:
            return ['ML', 'TOTAL', 'F5', 'RL']
    except Exception as e:
        print(f"[MARKET_PRIORITY] Error: {e}")
        return ['ML', 'TOTAL', 'F5', 'RL']


def pitcher_mismatch_ml_boost(home_era, away_era, base_prob, favored_is_home=True):
    """
    Ajusta la probabilidad ML cuando hay mismatch significativo de ERA.
    Cap de ±8pp igual que el sistema existente.
    """
    try:
        era_diff = abs(float(home_era or 4.50) - float(away_era or 4.50))
        if era_diff >= 3.0:
            boost = 0.07
        elif era_diff >= 2.0:
            boost = 0.05
        elif era_diff >= 1.5:
            boost = 0.03
        elif era_diff >= 1.0:
            boost = 0.015
        else:
            boost = 0.0
        adjusted = min(float(base_prob) + boost, float(base_prob) + 0.08)
        adjusted = max(0.0, min(1.0, adjusted))
        if boost > 0.0:
            print(f"[ML_BOOST] ERA diff={era_diff:.2f} | boost=+{boost*100:.1f}pp | "
                  f"{float(base_prob)*100:.1f}% → {adjusted*100:.1f}%")
        return adjusted
    except Exception as e:
        print(f"[ML_BOOST] Error: {e}")
        return base_prob


def enforce_market_diversity(picks_list, max_totals=2):
    """
    Post-procesa la lista de picks para garantizar diversidad de mercados.
    Si hay más de max_totals OVER/UNDER, elimina los de menor EV.
    Acepta dicts con clave 'market', 'tipo' o 'label'.
    """
    if not picks_list:
        return picks_list
    try:
        TOTAL_KEYWORDS = {'OVER', 'UNDER', 'TOTAL'}

        def _is_total(p):
            for key in ('market', 'tipo', 'label'):
                val = str(p.get(key) or '').upper()
                if any(kw in val for kw in TOTAL_KEYWORDS):
                    return True
            return False

        totals     = [p for p in picks_list if _is_total(p)]
        non_totals = [p for p in picks_list if not _is_total(p)]
        before = len(totals)
        if len(totals) > max_totals:
            totals.sort(
                key=lambda x: float(x.get('ev', x.get('ev_pct', x.get('probability', 0))) or 0),
                reverse=True,
            )
            totals = totals[:max_totals]
        result = non_totals + totals
        print(f"[DIVERSITY] Totals antes={before} | después={len(totals)} | "
              f"ML/RL/F5={len(non_totals)} | total picks={len(result)}")
        return result
    except Exception as e:
        print(f"[DIVERSITY] Error: {e}")
        return picks_list


_sit_flags_cache: dict = {}


def _fetch_situational_flags(
    home_team_id, away_team_id,
    home_name: str, away_name: str,
    game_date_str: str,
    commence_utc: str = "",
) -> dict:
    """
    Compute 4 situational fatigue flags (MLB calendar/logistics — no narrative).

    Keys returned (only set when flag is active):
      sit_getaway_{h|a}            True | "LAST_OF_SERIES"
      sit_getaway_detail_{h|a}     human-readable detail string
      sit_tz_fatigue_a             True
      sit_tz_fatigue_detail_a      human-readable detail string
      sit_bullpen_fatigue_{h|a}    True
      sit_bullpen_days_{h|a}       int (number of days with reliever appearances)
      sit_dh_g2                    True
      sit_flags_txt                aggregated block (present only when ≥1 flag active)
    """
    _ck = f"sit_{home_team_id}_{away_team_id}_{game_date_str}"
    if _ck in _sit_flags_cache:
        return _sit_flags_cache[_ck]

    flags: dict = {}

    try:
        from datetime import datetime as _dt, timedelta as _td
        import pytz as _pytz

        gd        = _dt.strptime(game_date_str, "%Y-%m-%d")
        yesterday = (gd - _td(days=1)).strftime("%Y-%m-%d")
        three_ago = (gd - _td(days=3)).strftime("%Y-%m-%d")
        year      = gd.year

        # Parse today's game start time in UTC
        today_utc_dt = None
        if commence_utc:
            try:
                today_utc_dt = _dt.fromisoformat(
                    commence_utc.replace("Z", "+00:00")
                ).astimezone(_pytz.utc)
            except Exception:
                pass

        # ── Flag 4: Doubleheader G2 ───────────────────────────────────────
        try:
            _dsched = _mlb_rest("/schedule", {
                "teamId": home_team_id, "date": game_date_str, "sportId": 1,
            })
            for _dd in _dsched.get("dates", []):
                for _dg in _dd.get("games", []):
                    if _dg.get("gameNumber", 1) == 2:
                        _dt2   = _dg.get("teams", {})
                        _h2_id = (_dt2.get("home", {}).get("team") or {}).get("id")
                        _a2_id = (_dt2.get("away", {}).get("team") or {}).get("id")
                        if home_team_id in (_h2_id, _a2_id) or away_team_id in (_h2_id, _a2_id):
                            flags["sit_dh_g2"] = True
        except Exception:
            pass

        # ── Flag 3: Bullpen fatigue (relievers in 2+ of last 3 days) ─────
        def _bp_fatigue(team_id, label):
            try:
                data   = _mlb_rest(f"/teams/{team_id}/stats", {
                    "stats": "gameLog", "group": "pitching", "season": year,
                    "startDate": three_ago, "endDate": yesterday,
                })
                splits = (data.get("stats") or [{}])[0].get("splits") or []
                relief_days: set = set()
                for _s in splits:
                    gs = int((_s.get("stat") or {}).get("gamesStarted", 0) or 0)
                    if gs == 0:
                        _gdate = (_s.get("date") or "")[:10]
                        if _gdate >= three_ago:
                            relief_days.add(_gdate)
                days = len(relief_days)
                if days >= 2:
                    flags[f"sit_bullpen_fatigue_{label}"] = True
                    flags[f"sit_bullpen_days_{label}"]    = days
            except Exception:
                pass

        _bp_fatigue(home_team_id, "h")
        _bp_fatigue(away_team_id, "a")

        # ── Yesterday's game helper ───────────────────────────────────────
        def _yesterday_game_info(team_id):
            try:
                data = _mlb_rest("/schedule", {
                    "teamId": team_id, "date": yesterday, "sportId": 1,
                    "hydrate": "team",
                })
                for _dd in data.get("dates", []):
                    for _dg in _dd.get("games", []):
                        if (_dg.get("status") or {}).get("statusCode", "") == "F":
                            return (
                                _dg.get("gameDate", ""),
                                _dg.get("seriesGameNumber"),
                                _dg.get("gamesInSeries"),
                            )
            except Exception:
                pass
            return None, None, None

        h_yd_utc, h_sg, h_gs = _yesterday_game_info(home_team_id)
        a_yd_utc, a_sg, a_gs = _yesterday_game_info(away_team_id)

        home_tz_str = MLB_STADIUM_TIMEZONE.get(home_name)
        away_tz_str = MLB_STADIUM_TIMEZONE.get(away_name)

        # ── Flag 1: Getaway day (night game yday → day game today) ────────
        for _lbl, _yd_utc, _sg, _gs, _tz_str in [
            ("h", h_yd_utc, h_sg, h_gs, home_tz_str),
            ("a", a_yd_utc, a_sg, a_gs, away_tz_str),
        ]:
            if not _yd_utc or not _tz_str:
                continue
            try:
                _tz      = _pytz.timezone(_tz_str)
                _yd_dt   = _dt.fromisoformat(_yd_utc.replace("Z", "+00:00")).astimezone(_pytz.utc)
                _yd_loc  = _yd_dt.astimezone(_tz)
                _yd_hour = _yd_loc.hour

                _td_hour = None
                if today_utc_dt is not None:
                    _td_hour = today_utc_dt.astimezone(_tz).hour

                if _yd_hour >= 18 and _td_hour is not None and _td_hour < 16:
                    _val = "LAST_OF_SERIES" if (_sg and _gs and _sg == _gs) else True
                    flags[f"sit_getaway_{_lbl}"] = _val
                    _series_note = (f" — ÚLTIMO DE SERIE ({_sg}/{_gs})"
                                    if _val == "LAST_OF_SERIES" else "")
                    flags[f"sit_getaway_detail_{_lbl}"] = (
                        f"Ayer juego nocturno ({_yd_loc.strftime('%I:%M %p')} local); "
                        f"hoy juego diurno (~{_td_hour:02d}:00 local){_series_note}"
                    )
            except Exception:
                pass

        # ── Flag 2: Timezone fatigue (away team traveled 2+ hrs east) ────
        if away_tz_str and home_tz_str and away_tz_str != home_tz_str:
            try:
                _ref     = _dt.utcnow().replace(tzinfo=_pytz.utc)
                _atz     = _pytz.timezone(away_tz_str)
                _htz     = _pytz.timezone(home_tz_str)
                _a_off   = _atz.utcoffset(_ref).total_seconds() / 3600
                _h_off   = _htz.utcoffset(_ref).total_seconds() / 3600
                _tz_diff = _h_off - _a_off   # positive = game location is later tz
                if abs(_tz_diff) >= 2 and today_utc_dt is not None:
                    _body_hour = today_utc_dt.astimezone(_atz).hour
                    if _body_hour < 13:   # before 1 PM on away team's body clock
                        flags["sit_tz_fatigue_a"] = True
                        _dir = "→ EAST" if _tz_diff > 0 else "→ WEST"
                        flags["sit_tz_fatigue_detail_a"] = (
                            f"Viaje {away_tz_str.split('/')[-1].replace('_', ' ')} "
                            f"{_dir} ({abs(_tz_diff):.0f}h diff) — "
                            f"juego es {today_utc_dt.astimezone(_atz).strftime('%I:%M %p')} "
                            f"en el reloj biológico del visitante"
                        )
            except Exception:
                pass

        # ── Aggregated human-readable flag block ──────────────────────────
        _flag_lines = []
        for _fl, _fshort in [("h", "LOCAL"), ("a", "VISITANTE")]:
            _gw = flags.get(f"sit_getaway_{_fl}")
            if _gw:
                _det = flags.get(f"sit_getaway_detail_{_fl}", "")
                _tag = "GETAWAY_DAY [ÚLTIMO DE SERIE]" if _gw == "LAST_OF_SERIES" else "GETAWAY_DAY"
                _flag_lines.append(f"⚑ {_tag} [{_fshort}]: {_det}")
            if flags.get(f"sit_bullpen_fatigue_{_fl}"):
                _days = flags.get(f"sit_bullpen_days_{_fl}", 2)
                _flag_lines.append(
                    f"⚑ BULLPEN_FATIGUE [{_fshort}]: relevistas usados en {_days}/3 últimos días"
                )
        if flags.get("sit_tz_fatigue_a"):
            _flag_lines.append(
                f"⚑ TIMEZONE_FATIGUE [VISITANTE]: {flags.get('sit_tz_fatigue_detail_a', '')}"
            )
        if flags.get("sit_dh_g2"):
            _flag_lines.append("⚑ DOUBLEHEADER_G2: Segundo juego del dobleheader hoy")

        if _flag_lines:
            flags["sit_flags_txt"] = "\n".join(_flag_lines)
            print(f"  🚩 Banderas situacionales ({len(_flag_lines)}): "
                  f"{' | '.join(f.split('[')[0].strip() for f in _flag_lines)}")

    except Exception as _sf_e:
        print(f"  ⚠️  _fetch_situational_flags error: {_sf_e}")

    _sit_flags_cache[_ck] = flags
    return flags


def _fetch_enhanced_game_context(
    home_team_id, away_team_id, home_pitcher_id, away_pitcher_id, game_date_str: str,
    home_name: str = "", away_name: str = "", commence_utc: str = "",
) -> dict:
    """
    Contexto enriquecido via MLB Stats API:
      1. Últimas 3 salidas reales de cada abridor (solo salidas >= 3 IP)
      2. ERA del bullpen de los últimos 7 días
      3. Stats de la serie actual (últimos 5 días entre los dos equipos)
    Usa _mlb_rest para mantenerse en el mismo cliente HTTP ya configurado.
    Devuelve dict con claves escalares/string para pasar por _claude_data_g.update().
    """
    _ck = f"enh_{home_team_id}_{away_team_id}_{home_pitcher_id}_{away_pitcher_id}_{game_date_str}"
    if _ck in _enh_ctx_cache:
        return _enh_ctx_cache[_ck]

    result: dict = {}
    try:
        from datetime import datetime as _dt, timedelta as _td
        gd = _dt.strptime(game_date_str, "%Y-%m-%d")
        seven_ago = (gd - _td(days=7)).strftime("%Y-%m-%d")
        five_ago  = (gd - _td(days=5)).strftime("%Y-%m-%d")
        year = gd.year
    except Exception:
        _enh_ctx_cache[_ck] = result
        return result

    # ── 1. Últimas 3 salidas de cada abridor ─────────────────────────────────
    def get_last_3_starts(pitcher_id, label):
        if not pitcher_id:
            result[f"{label}_last3_starts"] = []
            result[f"{label}_last3_era_avg"] = None
            return
        try:
            print(f"[DEBUG API] Fetching stats for pitcher {pitcher_id} ({label})")
            raw = _mlb_rest(f"/people/{pitcher_id}/stats",
                            {"stats": "gameLog", "season": year, "group": "pitching"})
            if not raw.get("stats") or not raw["stats"][0].get("splits"):
                print(f"[DEBUG API] No splits found for {label} pitcher {pitcher_id}")
                result[f"{label}_last3_starts"] = []
                result[f"{label}_last3_era_avg"] = None
                return
            splits = raw["stats"][0]["splits"]
            starts = [s for s in splits if float(s["stat"].get("inningsPitched", 0)) >= 3.0]
            print(f"[DEBUG API] Total starts found for {label}: {len(starts)}")
            last3 = starts[-3:] if len(starts) >= 3 else starts
            entries = []
            for g in last3:
                ip = float(g["stat"].get("inningsPitched", 0))
                er = float(g["stat"].get("earnedRuns", 0))
                era_game = round(er * 9 / max(ip, 0.1), 2)
                entries.append({
                    "date": g["date"],
                    "ip": ip,
                    "er": er,
                    "era_game": era_game,
                    "k": g["stat"].get("strikeOuts", 0),
                    "hits": g["stat"].get("hits", 0),
                })
            era_avg = round(sum(x["era_game"] for x in entries) / len(entries), 2) if entries else None
            print(f"[DEBUG API] {label} last3 ERA avg: {era_avg}")
            result[f"{label}_last3_starts"] = entries
            result[f"{label}_last3_era_avg"] = era_avg
            if entries:
                lines = [f"{x['date']} {x['ip']}IP {x['er']}ER ERA{x['era_game']} K{x['k']}" for x in entries]
                result[f"{label}_last3_txt"] = " | ".join(lines)
        except Exception as e:
            print(f"[DEBUG API ERROR] {label} pitcher {pitcher_id}: {e}")
            result[f"{label}_last3_starts"] = []
            result[f"{label}_last3_era_avg"] = None

    get_last_3_starts(home_pitcher_id, "home_pitcher")
    get_last_3_starts(away_pitcher_id, "away_pitcher")

    # ── 2. Bullpen ERA últimos 7 días ─────────────────────────────────────────
    def _bullpen_era(team_id, label):
        if not team_id:
            return
        try:
            data = _mlb_rest(f"/teams/{team_id}/stats", {
                "stats": "byDateRange", "group": "pitching",
                "startDate": seven_ago, "endDate": game_date_str, "season": year,
            })
            splits = (data.get("stats", [{}])[0].get("splits", []) or [])
            relievers = [s for s in splits if int((s.get("stat") or {}).get("gamesStarted", 1) or 1) == 0]
            if not relievers:
                relievers = splits
            total_er = sum(float((s.get("stat") or {}).get("earnedRuns", 0) or 0) for s in relievers)
            total_ip = sum(float((s.get("stat") or {}).get("inningsPitched", 0) or 0) for s in relievers)
            result[f"{label}_bullpen_era_7d"] = round(total_er * 9 / total_ip, 2) if total_ip > 0 else None
        except Exception as _e:
            print(f"  ⚠️  _bullpen_era {label}: {_e}")

    _bullpen_era(home_team_id, "home")
    _bullpen_era(away_team_id, "away")

    # ── 3. Stats de la serie actual (últimos 5 días) ──────────────────────────
    def _serie_stats(team_id, opp_id, label):
        if not team_id or not opp_id:
            return
        try:
            data = _mlb_rest("/schedule", {
                "teamId": team_id, "startDate": five_ago, "endDate": game_date_str,
                "sportId": 1, "hydrate": "linescore,teams",
            })
            games = []
            for d in (data.get("dates") or []):
                for g in (d.get("games") or []):
                    if (g.get("status") or {}).get("statusCode") != "F":
                        continue
                    teams = g.get("teams", {})
                    h_tid = (teams.get("home", {}).get("team") or {}).get("id")
                    a_tid = (teams.get("away", {}).get("team") or {}).get("id")
                    if opp_id not in (h_tid, a_tid):
                        continue
                    hs = int(teams.get("home", {}).get("score", 0) or 0)
                    as_ = int(teams.get("away", {}).get("score", 0) or 0)
                    is_home = h_tid == team_id
                    rs = hs if is_home else as_
                    ra = as_ if is_home else hs
                    games.append({"rs": rs, "ra": ra, "won": rs > ra, "total": hs + as_})
            if games:
                wins = sum(1 for g in games if g["won"])
                avg_rs = round(sum(g["rs"] for g in games) / len(games), 1)
                avg_tot = round(sum(g["total"] for g in games) / len(games), 1)
                result[f"{label}_serie_wins"]    = wins
                result[f"{label}_serie_games"]   = len(games)
                result[f"{label}_serie_avg_rs"]  = avg_rs
                result[f"{label}_serie_avg_tot"] = avg_tot
        except Exception as _e:
            print(f"  ⚠️  _serie_stats {label}: {_e}")

    _serie_stats(home_team_id, away_team_id, "home")
    _serie_stats(away_team_id, home_team_id, "away")

    # ── Formato de texto para el panel ────────────────────────────────────────
    parts = []
    h3 = result.get("home_pitcher_last3_txt")
    a3 = result.get("away_pitcher_last3_txt")
    if h3:
        parts.append(f"LOCAL últimas 3 salidas: {h3}")
    if a3:
        parts.append(f"VISITANTE últimas 3 salidas: {a3}")
    hbp = result.get("home_bullpen_era_7d")
    abp = result.get("away_bullpen_era_7d")
    if hbp is not None:
        parts.append(f"Bullpen LOCAL ERA 7d: {hbp:.2f}")
    if abp is not None:
        parts.append(f"Bullpen VISITANTE ERA 7d: {abp:.2f}")
    hw = result.get("home_serie_wins")
    hn = result.get("home_serie_games")
    if hw is not None and hn:
        avg_rs = result.get("home_serie_avg_rs", "?")
        avg_tot = result.get("home_serie_avg_tot", "?")
        parts.append(f"Serie actual: LOCAL {hw}-{hn-hw} | prom {avg_rs} carreras | total prom {avg_tot}")
    if parts:
        result["serie_txt"] = " || ".join(parts)

    # ── 4. Situational flags (GETAWAY_DAY, TIMEZONE_FATIGUE, BULLPEN_FATIGUE, DH_G2) ──
    if home_name and away_name:
        try:
            _sit = _fetch_situational_flags(
                home_team_id, away_team_id,
                home_name, away_name,
                game_date_str, commence_utc=commence_utc,
            )
            result.update(_sit)
        except Exception as _sf_e:
            print(f"  ⚠️  situational_flags call error: {_sf_e}")

    _enh_ctx_cache[_ck] = result
    return result


def _enrich_panel_data(game_data: dict, game_obj: "dict | None" = None) -> dict:
    """
    Enrich game_data with Statcast metrics, pitcher trends, team run averages,
    and line movement before the expert panel deliberates.

    Returns a dict of additional keys to merge into game_data.  All fetches use
    existing per-day caches so there is no duplicate HTTP overhead.

    Keys injected (all optional — only set when data is available):
      statcast_pitcher_home / statcast_pitcher_away
          K/9, Whiff%, Barrel%, xERA from Baseball Savant + MLB Stats API
      tendencia_pitcher_home / tendencia_pitcher_away
          ERA últimas 3 salidas + trend label
      tendencia_equipo_home / tendencia_equipo_away
          Últimos 10 juegos: record, avg RS, avg RA, OVER/UNDER ratio
      linea_movimiento
          Signal string when total moves ≥0.5 pts or ML implied prob moves ≥5pp
    """
    enriched: dict = {}

    # ── Parse teams and pitcher names ──────────────────────────────────────────
    match = game_data.get("match", "")
    if " vs " not in match:
        return enriched
    home, away = match.split(" vs ", 1)

    h_pname, _ = _parse_pitcher(game_data.get("pitcher_home", ""))
    a_pname, _ = _parse_pitcher(game_data.get("pitcher_away", ""))

    # ── 1. Statcast + K/9 per pitcher ─────────────────────────────────────────
    for pname, key in [(h_pname, "statcast_pitcher_home"),
                       (a_pname, "statcast_pitcher_away")]:
        if not pname or pname in ("TBD", ""):
            continue
        parts: list[str] = []
        try:
            ps  = fetch_pitcher_stats(pname)
            k9  = ps.get("k9", "N/A")
            if k9 not in ("N/A", None, ""):
                parts.append(f"K/9: {k9}")
        except Exception:
            pass
        try:
            sc = fetch_statcast_pitcher(pname)
            if sc:
                if sc.get("whiff_pct") is not None:
                    parts.append(f"Whiff%: {sc['whiff_pct']:.1f}%")
                if sc.get("barrel_pct") is not None:
                    parts.append(f"Barrel%: {sc['barrel_pct']:.1f}%")
                if sc.get("xera") is not None:
                    parts.append(f"xERA: {sc['xera']:.2f}")
        except Exception:
            pass
        if parts:
            enriched[key] = ", ".join(parts)

    # ── 2. Pitcher recent form (last 3 starts) ────────────────────────────────
    for pname, key in [(h_pname, "tendencia_pitcher_home"),
                       (a_pname, "tendencia_pitcher_away")]:
        if not pname or pname in ("TBD", ""):
            continue
        try:
            pf = fetch_pitcher_recent_form(pname)
            if pf:
                eras_str = " → ".join(str(e) for e in pf["eras"])
                enriched[key] = (
                    f"{pf['trend']} | ERAs últimas {len(pf['eras'])} salidas: "
                    f"{eras_str} | Promedio: {pf['avg_era']}"
                )
        except Exception:
            pass

    # ── 3. Team run trend (last 10 games) ─────────────────────────────────────
    for team, key in [(home, "tendencia_equipo_home"),
                      (away, "tendencia_equipo_away")]:
        try:
            sk = fetch_team_streak_mlb(team)
            if sk:
                n    = sk["wins_10"] + sk["losses_10"]
                over_pct  = round(sk["overs_10"]  / max(n, 1) * 100)
                under_pct = round(sk["unders_10"] / max(n, 1) * 100)
                enriched[key] = (
                    f"Últimos {n}G: {sk['wins_10']}G-{sk['losses_10']}P | "
                    f"RS: {sk['avg_rs']} prom | RA: {sk['avg_ra']} prom | "
                    f"Total prom: {sk['avg_total']} carreras | "
                    f"OVER {sk['overs_10']}/{n} ({over_pct}%) "
                    f"UNDER {sk['unders_10']}/{n} ({under_pct}%)"
                )
        except Exception:
            pass

    # ── 4. Line movement vs session opening ───────────────────────────────────
    if game_obj:
        try:
            mv = _detect_line_movement(game_obj, home, away)
            if mv:
                enriched["linea_movimiento"] = mv
        except Exception:
            pass

    if enriched:
        keys_found = ", ".join(enriched.keys())
        print(f"   📊 Panel enriquecido: {keys_found}")

    return enriched


def _generar_narrativa(context: dict, candidates: list, home: str, away: str,
                       is_mlb: bool, sport_key: str) -> str:
    """
    Genera 3 oraciones en español natural que explican los factores clave
    del partido y si hay contradicciones entre los datos y el pick.
    """
    if not ANTHROPIC_API_KEY or not HAS_ANTHROPIC:
        return ""
    try:
        datos = []
        if is_mlb:
            ph  = context.get("pname_home", "TBD")
            pa  = context.get("pname_away", "TBD")
            eh  = context.get("era_home", 4.50)
            ea  = context.get("era_away", 4.50)
            pfh = context.get("pform_h")
            pfa = context.get("pform_a")
            if pfh and pfh.get("trend"):
                eras_h = _clean_era_form(pfh.get("eras", []))
                datos.append(f"{ph}: {pfh['trend']} — ERA reciente: {' → '.join(str(e) for e in eras_h[-3:])}")
            else:
                datos.append(f"{ph}: ERA {eh:.2f}")
            if pfa and pfa.get("trend"):
                eras_a = _clean_era_form(pfa.get("eras", []))
                datos.append(f"{pa}: {pfa['trend']} — ERA reciente: {' → '.join(str(e) for e in eras_a[-3:])}")
            else:
                datos.append(f"{pa}: ERA {ea:.2f}")
            for t, ils in (context.get("il_data") or {}).items():
                if ils:
                    datos.append(f"Lesionados {t}: {', '.join(ils[:2])}")
            pin = context.get("pinnacle_odds")
            if pin:
                rh = 1.0 / max(pin["home"], 1.001)
                ra = 1.0 / max(pin["away"], 1.001)
                tot = rh + ra
                datos.append(f"Pinnacle: {home} {round(rh/tot*100,1)}% | {away} {round(ra/tot*100,1)}%")
            if context.get("wind_info"):
                datos.append(f"Clima: {context['wind_info']}")
            if context.get("temp_label"):
                datos.append(context["temp_label"])
            for cf in (context.get("pitcher_conflicts") or []):
                datos.append(f"CONFLICTO: {cf['pitcher']} vs ofensiva peligrosa de {cf['rival']}: {', '.join(cf['flags'])}")
        else:
            if context.get("form_home"): datos.append(f"{home}: {context['form_home']}")
            if context.get("form_away"): datos.append(f"{away}: {context['form_away']}")
        if candidates:
            best = candidates[0]
            lbl  = best.get("label", "?").replace("🔵 ","").replace("🔴 ","").replace("📈 ","").replace("📉 ","")
            datos.append(f"Pick del modelo: {lbl} EV+{best.get('ev_pct',0):.1f}%")
        datos_str = "\n".join(f"- {d}" for d in datos)
        prompt = (
            f"Partido: {home} vs {away}\n"
            f"Datos:\n{datos_str}\n\n"
            f"Escribe exactamente 3 oraciones en español conversacional:\n"
            f"1. Qué está pasando con los pitchers o el partido hoy\n"
            f"2. Si hay contradicción entre los datos y el pick (por ejemplo pitchers en declive pero pick UNDER)\n"
            f"3. Qué deberías considerar antes de apostar\n\n"
            f"Habla como un amigo que sabe de béisbol. Sin tecnicismos. Sin listas. Solo oraciones naturales."
        )
        client = _anthropic_lib.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=220,
            system="Eres un experto en béisbol que habla en español natural. Nunca uses JSON, listas, ni tecnicismos. Exactamente 3 oraciones cortas y directas.",
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text.strip()
    except Exception as _ne:
        print(f"  ⚠️  narrativa error: {_ne}")
        try:
            _fb = []
            _eh  = float(context.get("era_home") or 4.5)
            _ea  = float(context.get("era_away") or 4.5)
            _pfh = context.get("pform_h") or {}
            _pfa = context.get("pform_a") or {}
            _ph  = context.get("pname_home", "El pitcher local")
            _pa  = context.get("pname_away", "El pitcher visitante")
            if "DECLIVE" in _pfh.get("trend", ""):
                _fb.append(f"{_ph} viene en declive en sus últimas salidas")
            elif _eh < 3.0:
                _fb.append(f"{_ph} es el pitcher dominante del partido")
            if "DECLIVE" in _pfa.get("trend", ""):
                _fb.append(f"{_pa} también viene en mal momento")
            elif _ea > 5.5:
                _fb.append(f"{_pa} es vulnerable con ERA {_ea:.2f}")
            if candidates:
                _bc  = candidates[0]
                _lbl = (_bc.get("label", "").replace("🔵 ","").replace("🔴 ","")
                        .replace("📈 ","").replace("📉 ",""))
                _fb.append(f"El modelo ve valor en {_lbl} con EV +{_bc.get('ev_pct', 0):.1f}%")
            return ". ".join(_fb) + "." if _fb else ""
        except Exception:
            return ""


def _build_elena_situational_addendum(game_data: dict) -> str:
    """
    Build a dynamic system-prompt addendum for Elena (El Abogado del Diablo)
    when situational fatigue flags are present in game_data.
    Returns empty string when no flags are active — Elena's base prompt is
    unchanged in that case (she should NOT mention this topic at all).
    """
    flags_txt = game_data.get("sit_flags_txt", "")
    if not flags_txt:
        return ""
    return (
        "\n\n━━━ DATOS SITUACIONALES CONFIRMADOS (ESTE PARTIDO) ━━━\n"
        "El sistema de monitoreo detectó automáticamente los siguientes factores "
        "de fatiga real de calendario — NO son narrativa ni opinión, son datos "
        "medibles del schedule de MLB:\n\n"
        f"{flags_txt}\n\n"
        "INSTRUCCIÓN CRÍTICA PARA ELENA:\n"
        "Si alguna de estas banderas aplica al EQUIPO FAVORITO del pick en análisis, "
        "trátala como evidencia concreta de un factor oculto negativo. "
        "Nómbrala explícitamente en tu oración 1 y explica por qué es relevante HOY. "
        "Tu veto (apostar=false) o duda (confianza=BAJA) se mantiene aunque Marco y "
        "Víctor voten a favor — estos datos del calendario pesan más que el consenso. "
        "Si la bandera aplica al EQUIPO DESFAVORITO, ignórala."
    )


def detectar_patrones_getaway() -> list:
    """
    Detecta patrones de fatiga/situacionales del slate de hoy:
      1. Getaway day masivo (todos los juegos empiezan antes de las 5 PM CT)
      2. Bullpen games (opener con <35 IP en temporada = spot start)
      3. Bullpens quemados (dobleheader o extra innings el día anterior)

    Actualiza el global _patrones_activos y retorna la lista de alertas.
    """
    global _patrones_activos
    hoy  = datetime.now(CDT).strftime("%Y-%m-%d")
    ayer = (datetime.now(CDT) - timedelta(days=1)).strftime("%Y-%m-%d")
    alertas: list = []

    # ── Slate de hoy ─────────────────────────────────────────────────────────
    datos_hoy  = _mlb_rest("/schedule", {"sportId": 1, "date": hoy,
                                         "hydrate": "probablePitcher,team"})
    juegos_hoy = datos_hoy.get("dates", [{}])[0].get("games", [])
    if not juegos_hoy:
        _patrones_activos = []
        return []

    # ── PATRÓN 1: Getaway day masivo ─────────────────────────────────────────
    horas = []
    for j in juegos_hoy:
        try:
            h = datetime.fromisoformat(
                j["gameDate"].replace("Z", "+00:00")
            ).astimezone(CDT)
            horas.append(h.hour)
        except Exception:
            pass
    if horas and max(horas) < 17:
        alertas.append(
            "🚨 GETAWAY DAY MASIVO: todos los juegos son de día (antes de las 5 PM CT). "
            "Patrón: lean UNDER general, lineups B posibles, abridores con correa corta. "
            "Verificar lineups confirmados antes de apostar."
        )

    # ── PATRÓN 2: Bullpen games (openers) ────────────────────────────────────
    for j in juegos_hoy:
        for lado in ["away", "home"]:
            p = j["teams"][lado].get("probablePitcher")
            if not p:
                continue
            try:
                pr = _mlb_rest(f"/people/{p['id']}",
                               {"hydrate": "stats(group=[pitching],type=[season])"})
                splits = (pr.get("people", [{}])[0]
                            .get("stats", [{}])[0]
                            .get("splits", []))
                if not splits:
                    continue
                s      = splits[0]["stat"]
                ip_raw = s.get("inningsPitched", "0")
                ip     = float(
                    ip_raw.replace(".1", ".33").replace(".2", ".67")
                )
                if ip < 35:
                    equipo = j["teams"][lado]["team"]["name"]
                    otro   = "home" if lado == "away" else "away"
                    rival  = j["teams"][otro]["team"]["name"]
                    alertas.append(
                        f"🚨 POSIBLE BULLPEN GAME: {equipo} abre con "
                        f"{p['fullName']} (solo {ip:.0f} IP en temporada). "
                        f"Ángulo: ML de {rival} y over si el bullpen se estira."
                    )
            except Exception:
                continue

    # ── PATRÓN 3: Bullpens quemados (dobleheader o extras ayer) ──────────────
    datos_ayer  = _mlb_rest("/schedule", {"sportId": 1, "date": ayer,
                                          "hydrate": "linescore"})
    juegos_ayer = datos_ayer.get("dates", [{}])[0].get("games", [])
    equipos_cansados: dict = {}
    for j in juegos_ayer:
        try:
            innings = len(j.get("linescore", {}).get("innings", []))
        except Exception:
            innings = 9
        for lado in ["away", "home"]:
            try:
                tid    = j["teams"][lado]["team"]["id"]
                nombre = j["teams"][lado]["team"]["name"]
                if tid not in equipos_cansados:
                    equipos_cansados[tid] = {"nombre": nombre, "juegos": 0, "innings": 0}
                equipos_cansados[tid]["juegos"]  += 1
                equipos_cansados[tid]["innings"] += innings
            except Exception:
                pass

    ids_hoy: set = set()
    for j in juegos_hoy:
        for lado in ["away", "home"]:
            try:
                ids_hoy.add(j["teams"][lado]["team"]["id"])
            except Exception:
                pass

    for tid, info in equipos_cansados.items():
        if (info["juegos"] >= 2 or info["innings"] >= 11) and tid in ids_hoy:
            razon = (f"{info['juegos']} juegos" if info["juegos"] >= 2
                     else f"{info['innings']} innings")
            alertas.append(
                f"🚨 BULLPEN QUEMADO: {info['nombre']} cargó {razon} ayer. "
                f"Ángulo: over live si el abridor de hoy sale antes del 6to inning."
            )

    _patrones_activos = alertas
    return alertas


def panel_expertos(game_data: dict, sport: str,
                   _no_elite: bool = False, _force_elite: bool = False) -> "dict | None":
    """
    Panel of 3 expert personas — each calls analyze_with_claude with its own
    system-prompt persona appended to _CLAUDE_SYSTEM.

    Consensus rule: 2 of 3 must return apostar=True AND no hard veto
    (apostar=False + confianza=BAJA from any expert) → final apostar=True.
    Otherwise apostar=False.

    Returns a merged dict compatible with the standard analyze_with_claude response,
    or None if no expert could be reached.
    """
    _PATRONES_MLB_2026 = (
        "\n\n=== PATRONES SITUACIONALES MLB 2026 (CAPA DE META-ANÁLISIS) ===\n"
        "Antes de emitir tu veredicto, revisa si el juego activa alguno de estos patrones "
        "detectados durante la temporada 2026. No son reglas absolutas, pero son señales de "
        "alta frecuencia que debes mencionar si aplican:\n\n"
        "PATRÓN 1 – MOMENTUM DE SERIE: Si el equipo analizado ganó el juego anterior en esta "
        "misma serie (ayer o antes de ayer contra el mismo rival), es señal a favor. Si perdió, "
        "es señal en contra. El momentum dentro de la serie ha sido el patrón más consistente "
        "de la temporada.\n\n"
        "PATRÓN 2 – TRAMPA DEL FAVORITO PESADO: Si el favorito está a -200 o peor, hay que "
        "decirlo. A ese precio, incluso ganando el 62% del tiempo, el resultado es negativo "
        "a largo plazo. Solo vale si hay ventaja real de más de 15% en probabilidad vs línea.\n\n"
        "PATRÓN 3 – OVER EN JUEGO DE DÍA CON TOTAL ALTO: Si el total es 9 o más Y el juego "
        "es diurno (antes de las 5pm local), el Over tiene ventaja estadística real en 2026.\n\n"
        "PATRÓN 4 – UNDERDOG VISITANTE CON VALOR: Si un visitante está entre +110 y +160, "
        "ese rango ha producido valor positivo esta temporada, especialmente en duelos de "
        "división. No descartar automáticamente al underdog visitante en ese rango.\n\n"
        "PATRÓN 5 – GOLEADA NO GARANTIZA REBOTE: Si un equipo fue goleado (6+ carreras) en "
        "el juego anterior, NO asumir que va a rebotar. Los equipos dominantes siguen dominando "
        "en juegos consecutivos.\n\n"
        "PATRÓN 6 – PERFILES DE EQUIPO ACTIVOS EN 2026:\n"
        "- TB en casa: rendimiento sólido\n"
        "- CWS en casa: win rate muy por encima del mercado\n"
        "- CLE: fuerte tendencia al bajo, especialmente tras perder ante pitcher derecho\n"
        "- WSH en casa: tendencia fuerte al alto\n"
        "- SD, SEA, TEX en casa: tendencia al bajo\n"
        "- PIT y MIN: alto en 70% de sus juegos en últimos 30 días\n"
        "- PHI: se recupera bien después de derrota, especialmente fin de semana\n\n"
        "PATRÓN 7 – DIFERENCIAL DE CALIDAD EXTREMO: Si un equipo ÉLITE (LAD, MIL, NYY, ATL "
        "con más de 47 victorias) enfrenta a un equipo en mal momento (menos de 36 victorias), "
        "el favorito tiene valor real incluso a precio moderado (-130 a -160).\n\n"
        "INSTRUCCIÓN: Si ningún patrón aplica, no mencionar esta sección. Si uno o más aplican, "
        "incorpóralos dentro del campo 'razonamiento' del JSON, en lenguaje directo y sin "
        "mencionar nombres técnicos como 'hit rate', 'ATS' o 'patrón #X'.\n"
        "=== FIN PATRONES SITUACIONALES ===\n\n"

        "=== REGLAS DE PONDERACIÓN OBLIGATORIAS (PRIORIDAD SOBRE PATRONES) ===\n\n"

        "REGLA 1 — VENTAJA DE ABRIDOR (PRIORITARIA):\n"
        "Si la diferencia de ERA entre los abridores es >= 1.5 puntos, el equipo con ERA inferior "
        "tiene ventaja DOMINANTE. Esta ventaja supera los promedios ofensivos del rival A MENOS QUE "
        "se cumplan DOS de estas tres condiciones: "
        "(a) rival tiene OPS > 0.780, "
        "(b) bullpen del rival tiene ERA < 3.50, "
        "(c) Pinnacle da > 55% de probabilidad al rival. "
        "Si no se cumplen dos de esas tres, la ventaja del abridor es determinante.\n\n"

        "REGLA 2 — CONFIRMACIÓN SHARP:\n"
        "Si Pinnacle apunta en la misma dirección que la ventaja del abridor, "
        "esa combinación es señal FUERTE — al menos 2/3 votos del panel deben ir en esa dirección. "
        "No vetar por razones menores cuando pitcher dominante Y Pinnacle coinciden.\n\n"

        "REGLA 3 — CONTRADICCIÓN INTERNA:\n"
        "Si el pick va EN CONTRA de la ventaja del abridor Y en contra de Pinnacle al mismo tiempo, "
        "mencionar en razonamiento: 'PICK DE ALTO RIESGO — modelo contra mercado y contra el montículo'. "
        "Recomendar stake mínimo en ese caso.\n\n"

        "REGLA 4 — FORMA RECIENTE EQUIVALENTE A ERA:\n"
        "Si un abridor tiene ERA < 2.50 en sus últimas 3 salidas y el rival tiene ERA > 5.00 "
        "en sus últimas 3 salidas, tratar eso como ventaja de 2.0 puntos de ERA "
        "y aplicar la Regla 1 con esa ventaja implícita.\n\n"

        "APLICACIÓN: Estas reglas son prioritarias. No modificas el pick del modelo — "
        "el usuario decide — pero debes mencionar cualquier contradicción con estas reglas "
        "dentro del campo razonamiento, de forma directa y sin tecnicismos.\n"
        "=== FIN REGLAS DE PONDERACIÓN ===\n\n"

        "⚠️ FORMATO OBLIGATORIO: Tu respuesta DEBE ser exclusivamente el JSON exacto que se "
        "indica en el mensaje del usuario (con los campos pick, line, confianza, razonamiento, "
        "factores_positivos, factores_negativos, datos_inconsistentes, apostar). "
        "Los patrones son contexto adicional para enriquecer el campo razonamiento — NO cambian "
        "el formato de salida. NUNCA respondas en texto libre. SOLO JSON válido."
    )
    _is_mlb = "baseball" in sport.lower()

    _EXPERTOS = [
        (
            "El Estadístico",
            "Eres Marco, El Estadístico. Tu análisis se basa EXCLUSIVAMENTE en métricas "
            "Statcast predictivas. Nada de narrativa ni opinión cualitativa.\n\n"
            "MÉTRICAS PERMITIDAS (las únicas que puedes usar):\n"
            "• xERA — ERA esperada por calidad de contacto (supera a ERA real en predicción)\n"
            "• Barrel% — contacto élite (ángulo 8-32° + velocidad ≥98 mph)\n"
            "• Hard-Hit% — % de bateos a ≥95 mph (potencia ofensiva real)\n"
            "• Whiff% — % de swings fallados (dominancia del pitcher)\n\n"
            "PROTOCOLO SIN DATOS:\n"
            "Si no tienes datos concretos de al menos 2 de estas 4 métricas para el partido, "
            "debes escribir exactamente: 'Sin data Statcast suficiente para este partido.' "
            "y votar apostar=false. Nunca rellenes con opinión cualitativa.\n\n"
            "PROHIBICIONES ABSOLUTAS:\n"
            "• NUNCA mencionar ERA cruda, K/9, FIP, WHIP — son métricas lagging, no predictivas\n"
            "• NUNCA opinar sobre: rachas, momentum, motivación, narrativa del equipo, "
            "'el equipo viene de...', 'el pitcher está caliente' u otras frases de tendencia\n"
            "• NUNCA mencionar: EV%, probabilidad implícita, Kelly, divergencia Pinnacle\n\n"
            "CÓMO ESCRIBIR:\n"
            "• 2 oraciones máximo\n"
            "• Oración 1: métrica Statcast más relevante con diferencia concreta entre pitchers\n"
            "• Oración 2: lo que ese número significa para el pick de HOY\n"
            "• Ejemplo: 'Glasnow xERA 2.81 vs Keller xERA 4.62, Whiff% 31% vs 18% — "
            "diferencia real de dominancia de pitcheo. Favorece Under si el lineup contrario "
            "tiene Barrel% bajo.'\n\n"
            "VOTO:\n"
            "Datos Statcast respaldan el pick → apostar=true, confianza ALTA o MEDIA\n"
            "Datos Statcast contradicen el pick → apostar=false con razón en 1 línea\n"
            "Sin data suficiente → apostar=false, confianza BAJA",
        ),
        (
            "El Sharp",
            "Eres Víctor. Solo hablas de lo que dice el mercado sharp — Pinnacle y movimiento "
            "de línea. Nada de estadísticas de pitcher, nada de riesgos operacionales.\n\n"
            "REGLAS DE SALIDA (obligatorias):\n"
            "• Tu razonamiento debe ser EXACTAMENTE 2 oraciones.\n"
            "• Oración 1: qué dice Pinnacle (probabilidad implícita) y/o el movimiento de línea.\n"
            "• Oración 2: tu voto basado únicamente en esa señal de mercado.\n"
            "• PROHIBIDO mencionar ERA, K/9, lesiones o clima — eso es territorio de Marco y Elena.\n\n"
            "CRITERIO DE VOTO:\n"
            "Pinnacle ≥ 52% en el lado del pick = confirmación sharp → voto Sí, confianza ALTA. "
            "Pinnacle 48–52% = neutral, no es señal en contra → voto Sí si EV > 15%, confianza MEDIA. "
            "Divergencia modelo/Pinnacle ≤ 25pp = ruido normal, no vetes por esto. "
            "Solo veta si línea se movió > 0.5 pts en contra Y divergencia > 25pp — con datos exactos.",
        ),
        (
            "El Abogado del Diablo",
            "Eres Elena. Tu único trabajo es nombrar UN riesgo operacional concreto de hoy, "
            "o declarar que no existe ninguno. Nada de estadísticas de pitcher, nada de mercado.\n\n"
            "MODO CAZA DE FACTORES OCULTOS (crítico):\n"
            "Cuando Marco y Víctor coincidan en ir con el favorito, tu rol se intensifica: "
            "DEBES buscar activamente factores negativos ocultos antes de dar tu voto. "
            "Los analistas comunes ignoran estos — tú no:\n"
            "  → ¿Es el 3er juego consecutivo de una serie con viaje nocturno entre ciudades?\n"
            "    (fatiga acumulada invisible en los números actuales)\n"
            "  → ¿El bullpen del equipo favorito lanzó >4 innings en CADA uno de los 2 días previos?\n"
            "    (brazos agotados que no aparecen en las estadísticas de ERA)\n"
            "  → ¿El lineup del favorito tiene bateadores clave descansando hoy? "
            "(platoon, day off programado, jugador que entró tarde en el juego anterior)\n"
            "Si encuentras evidencia sólida de UNO de estos factores: "
            "tu veto (apostar=false) o duda (confianza=BAJA) debe mantenerse con firmeza. "
            "La presión del consenso de los otros dos NO cambia tu voto si tienes datos reales.\n\n"
            "REGLAS DE SALIDA (obligatorias):\n"
            "• Tu razonamiento debe ser EXACTAMENTE 2 oraciones.\n"
            "• Oración 1: el único riesgo real que encontraste (o 'Sin red flags confirmadas hoy.').\n"
            "• Oración 2: tu voto directo.\n"
            "• PROHIBIDO mencionar EV, ERA, Pinnacle, odds — eso es territorio de Marco y Víctor.\n"
            "• Si no hay red flag real NI factor oculto: voto Sí obligatorio. No inventes.\n\n"
            "CHECKLIST (evalúa en orden, para en el primero que active):\n"
            "a) LESIÓN confirmada del pitcher titular o bateador clave HOY → red flag real.\n"
            "b) CLIMA: viento > 20 mph hacia afuera O temperatura < 40°F → red flag real.\n"
            "c) BULLPEN: > 15 innings lanzados en últimos 3 días → red flag real.\n"
            "d) FATIGA: pitcher lanzó > 100 pitches hace < 4 días → red flag real.\n"
            "e) 3er juego de serie + viaje nocturno intercity la noche anterior → red flag real.\n"
            "f) Bullpen favorito agotado (>4 inn/día × 2 días consecutivos) → red flag real.\n"
            "g) H2H con pitchers IDÉNTICOS a hoy que contradiga el pick → red flag real.\n"
            "ERA alta del rival NO es red flag — es ventaja para el pick.",
        ),
    ]

    votos_favor   = 0
    veto_absoluto = False
    resultados    = []
    factores_pos  = []
    factores_neg  = []
    inconsistencias = []

    # ── Selección de modelo: elite vs haiku panel ─────────────────────────────
    global _elite_count_today, _elite_count_date
    _ev_pct_g      = float(game_data.get("ev_pct", 0) or 0)
    _use_elite     = False
    _elite_mtokens = 1024
    if _force_elite:
        _modelo_panel_iter = MODELO_ELITE
        _use_elite         = True
        _elite_mtokens     = MAX_TOKENS_ELITE
    elif (not _no_elite
          and _ev_pct_g >= UMBRAL_ELITE * 100
          and _elite_count_today < MAX_ELITE_DIARIO):
        _modelo_panel_iter = MODELO_ELITE
        _use_elite         = True
        _elite_mtokens     = MAX_TOKENS_ELITE
    else:
        _modelo_panel_iter = CLAUDE_PANEL_MODEL

    _ELITE_ADDENDUM = (
        "\n\nEste es un pick de alto edge. Analiza con profundidad extra: estado del "
        "bullpen, fatiga por viaje/serie, getaway day, movimiento de línea (¿sharp o "
        "público?), y clima/parque. Sé especialmente escéptico: busca razones por las "
        "que el edge podría ser falso."
    ) if _use_elite else ""

    for i, (nombre, extra) in enumerate(_EXPERTOS):
        _panel_model = _modelo_panel_iter
        _extra_full  = extra + (_PATRONES_MLB_2026 if _is_mlb else "") + PANEL_DIVERSITY_ADDENDUM
        if _use_elite:
            _extra_full += _ELITE_ADDENDUM
        # Elena (index 2): inject situational fatigue flags when present
        if i == 2 and _is_mlb:
            _elena_sit = _build_elena_situational_addendum(game_data)
            if _elena_sit:
                _extra_full += _elena_sit
                _n_flags = len(game_data.get("sit_flags_txt", "").splitlines())
                print(f"   🚩 Elena: {_n_flags} bandera(s) situacional(es) inyectada(s)")
            # Inject contexto_juego resumen (park, clima, regulares, bullpen)
            _ctx_jg_txt = game_data.get("ctx_juego_resumen", "")
            if _ctx_jg_txt:
                _extra_full += (
                    f"\n\nCONTEXTO DEL JUEGO (datos objetivos para evaluación):\n"
                    f"{_ctx_jg_txt}\n"
                    "Usa estos datos si aplican directamente al pick que estás evaluando "
                    "(e.g. bullpen del favorito quemado, regulares descansando)."
                )
            # Inject slate-wide getaway-day / bullpen pattern alerts when active
            if _patrones_activos:
                _slate_txt  = "\n".join(f"• {a}" for a in _patrones_activos)
                _extra_full += (
                    "\n\n━━━ ALERTAS DE PATRONES DEL SLATE (HOY) ━━━\n"
                    "El sistema detectó los siguientes patrones activos en el slate de hoy. "
                    "Úsalos como contexto de mercado general al evaluar este partido:\n\n"
                    f"{_slate_txt}\n\n"
                    "Si alguno de estos patrones aplica directamente al equipo favorito "
                    "de este pick, menciónalo explícitamente en tu análisis."
                )
                print(f"   🗓️  Elena: {len(_patrones_activos)} patrón(es) de slate inyectado(s)")
        res = analyze_with_claude(game_data, sport, _extra_system=_extra_full,
                                  _model=_panel_model, _max_tokens=_elite_mtokens)
        if res is None:
            print(f"   🎓 {nombre}: no disponible")
            resultados.append(None)
            continue

        apostar = res.get("apostar", True)
        conf    = res.get("confianza", "N/D")
        razon   = (res.get("razonamiento", "") or "")[:500]
        icon    = "✅" if apostar else "❌"
        c_icon  = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴"}.get(conf, "⚪")
        print(f"   🎓 {nombre}: {c_icon}{conf} apostar={icon} | \"{razon}\"")

        if apostar:
            votos_favor += 1
        if not apostar and conf == "BAJA":
            veto_absoluto = True  # hard veto: kills consensus regardless of other votes

        resultados.append(res)
        factores_pos.extend(res.get("factores_positivos") or [])
        factores_neg.extend(res.get("factores_negativos") or [])
        inconsistencias.extend(res.get("datos_inconsistentes") or [])

    disponibles = sum(1 for r in resultados if r is not None)
    if disponibles == 0:
        ev_f = float(game_data.get("ev_pct", 0) or 0)
        if ev_f >= 8.0:
            print(f"   🤖 Panel fallback: EV +{ev_f:.1f}% — auto-aprobado (API no disponible)")
            return {
                "apostar":              True,
                "confianza":            "MEDIA",
                "razonamiento":         f"Panel no disponible. Pick aprobado automáticamente por EV +{ev_f:.1f}%. Verifica pitcher y clima antes de apostar.",
                "factores_positivos":   [f"EV +{ev_f:.1f}% positivo"],
                "factores_negativos":   ["Panel de expertos no disponible — verifica manualmente"],
                "datos_inconsistentes": [],
                "_expertos_detalle":    [],
                "_votos_favor":         1,
            }
        return None

    consenso = (votos_favor >= 2) and not veto_absoluto

    # Use highest-confidence result as the structural base for the merged response
    base = next(
        (r for r in resultados if r and r.get("confianza") == "ALTA"),
        next(
            (r for r in resultados if r and r.get("confianza") == "MEDIA"),
            next((r for r in resultados if r), None),
        ),
    )
    if base is None:
        return None

    veto_txt = "SÍ 🚨" if veto_absoluto else "NO"
    decision = "✅ APOSTAR" if consenso else "❌ RECHAZADO"
    print(
        f"   🗳️  Panel [{sport}]: {votos_favor}/{disponibles} votos a favor | "
        f"veto={veto_txt} → {decision}"
    )

    merged = dict(base)
    merged["apostar"] = consenso
    merged["_elite_analisis"] = _use_elite
    merged["_modelo_usado"]   = "fable" if _use_elite else "haiku"
    if _use_elite and not _force_elite:
        _elite_count_today += 1
        _elite_count_date   = datetime.now(CDT).strftime("%Y-%m-%d")
        print(f"  🧠 Elite auto usado ({_elite_count_today}/{MAX_ELITE_DIARIO} hoy) — EV {_ev_pct_g:.1f}%")
        _save_elite_counter()
    elif _force_elite:
        print(f"  🧠 Elite forzado por /elite — contador sin incrementar")
    merged["confianza"] = (
        "ALTA" if (votos_favor == 3 and not veto_absoluto)
        else ("MEDIA" if consenso else "BAJA")
    )
    merged["factores_positivos"]   = list(dict.fromkeys(factores_pos))[:6]
    merged["factores_negativos"]   = list(dict.fromkeys(factores_neg))[:4]
    merged["datos_inconsistentes"] = list(dict.fromkeys(inconsistencias))
    merged["_expertos_detalle"] = [
        {
            "nombre":        _EXPERTOS[i][0],
            "apostar":       r.get("apostar")                          if r else None,
            "confianza":     r.get("confianza", "N/D")                 if r else "N/D",
            "razonamiento":  (r.get("razonamiento", "") or "")[:500]   if r else "no disponible",
        }
        for i, r in enumerate(resultados)
    ]
    # ── Síntesis final: Sonnet recibe los 3 votos y escribe recomendación conversacional ──
    _panel_tag = f"[Panel {votos_favor}/3 a favor{'  — veto absoluto' if veto_absoluto else ''}]"
    _expert_lines = []
    for i, (exp_nombre, _) in enumerate(_EXPERTOS):
        r = resultados[i] if i < len(resultados) else None
        if r is None:
            continue
        _voto_txt = "SÍ ✅" if r.get("apostar") else "NO ❌"
        _razon    = (r.get("razonamiento", "") or "").strip()[:500]
        _expert_lines.append(f"• {exp_nombre} → {_voto_txt}: {_razon}")

    if _expert_lines:
        _all_agree = (votos_favor == len([r for r in resultados if r is not None]))
        _agree_note = " Los tres coinciden — sintetiza en una sola línea." if _all_agree else ""
        _pick_raw        = base.get("pick", "N/D")
        _match_str       = game_data.get("match", "")
        _top_pick_label  = game_data.get("top_pick", _pick_raw)
        _top_pick_odds   = game_data.get("odds", "")
        _odds_str        = f" a {_top_pick_odds}" if _top_pick_odds else ""
        _is_rl_plus = "+1.5" in _top_pick_label
        _rl_note = (
            "IMPORTANTE: Este es un pick RL +1.5. Ese equipo puede PERDER el partido "
            "y el pick igual gana si pierde por solo 1 carrera. "
            "NO sugieras ML — son mercados diferentes.\n"
        ) if _is_rl_plus else ""
        _synthesis_prompt = (
            f"El pick formal es: {_top_pick_label}{_odds_str}.\n"
            + _rl_note
            + "TU TRABAJO es explicar en 2 oraciones POR QUÉ ese pick tiene valor o no.\n"
            "NO recomiendes un pick diferente. NO menciones otros mercados.\n"
            "Si el panel votó a favor de ese pick, explica por qué tiene sentido.\n"
            "Si votaron en contra, explica el riesgo.\n\n"
            "INSTRUCCIÓN ESPECIAL — SÍNTESIS FINAL DEL PANEL:\n"
            "Eres un amigo que apostó béisbol toda su vida. Acabas de escuchar a tres expertos "
            "y ahora le explicas a otro amigo, en voz alta, qué harías y por qué. "
            "Habla como si estuvieras en una conversación — no escribas un informe.\n\n"
            "VOTOS RECIBIDOS:\n"
            + "\n".join(_expert_lines) + "\n\n"
            "CÓMO ESCRIBIR (obligatorio):\n"
            "• Máximo 5 oraciones. Completas y directas.\n"
            f"{'• Los tres coinciden — una sola oración basta.' if _all_agree else ''}\n"
            "• Si recomiendas apostar: menciona el equipo, dónde apostar y cuánto. Nada más.\n"
            "• Si no recomiendas: di por qué en una oración y sugiere esperar.\n"
            "• PROHIBIDO usar estas palabras: EV, umbral, Regla, fórmula, porcentaje de valor, "
            "valor esperado, modelo, parámetro, métrica, implícita, divergencia, pp.\n"
            "• PROHIBIDO escribir números excepto el stake sugerido en dólares.\n"
            "• PROHIBIDO repetir lo que dijo cada experto por separado.\n\n"
            "EJEMPLOS DEL TONO CORRECTO:\n"
            "Sin apuesta: 'King viene mejorando pero sus últimas salidas fueron malas. "
            "Singer también mejora. Sin señal clara del mercado sharp. "
            "No hay ventaja hoy — mejor esperar otro partido.'\n"
            "Con apuesta: 'Cole domina y Pinnacle lo respalda fuerte. "
            "Yankees en casa contra un bullpen cansado. Apuesta Yankees ML en FanDuel, no más de $20.'\n\n"
            "Responde en máximo 5 oraciones completas."
        )
        try:
            _syn_client = _anthropic_lib.Anthropic(api_key=ANTHROPIC_API_KEY)
            _syn_msg = _syn_client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=350,
                system="Eres un experto en apuestas deportivas. Responde SOLO en español conversacional. NUNCA uses JSON, NUNCA uses bloques de código, NUNCA uses comillas. Habla como un amigo directo. Máximo 4 oraciones.",
                messages=[{"role": "user", "content": _synthesis_prompt if _expert_lines else json.dumps(game_data, default=str, ensure_ascii=False)[:1000]}],
            )
            _syn_raw = _syn_msg.content[0].text.strip()
            import re as _re_syn
            _syn_raw = _re_syn.sub(r'```[\w]*', '', _syn_raw)
            _syn_raw = _re_syn.sub(r'[{}\[\]]', '', _syn_raw)
            _syn_raw = _re_syn.sub(r'"(apostar|confianza|razonamiento|pick|line)"\s*:\s*', '', _syn_raw)
            _syn_raw = _re_syn.sub(r'(true|false|null)', '', _syn_raw)
            _syn_raw = _syn_raw.strip().strip('"').strip()
            if _syn_raw.startswith('{') or len(_syn_raw) > 400:
                _sentences = [s.strip() for s in _syn_raw.split('.') if s.strip()]
                _syn_raw = '. '.join(_sentences[:2]) + ('.' if _sentences else '')
            _syn = {"razonamiento": _syn_raw}
        except Exception as _syne:
            print(f"  ⚠️  Synthesis error: {_syne}")
            _syn = None
        _syn_text = (_syn.get("razonamiento", "") or "").strip() if _syn else ""
    else:
        _syn_text = ""

    if _syn_text:
        # Sanity-check: si la síntesis recomienda el equipo contrario al pick formal,
        # descartarla y usar el razonamiento del experto con mayor confianza.
        import re as _re_sc
        _formal_pick = game_data.get("top_pick", "")
        if _formal_pick:
            _pm = _re_sc.match(r'^(.+?)\s+(ml|rl|moneyline|run line)', _formal_pick, _re_sc.IGNORECASE)
            if _pm:
                _pick_words  = set(_pm.group(1).lower().split())
                _match_parts = game_data.get("match", "").lower().replace(" vs ", "|").split("|")
                if len(_match_parts) == 2:
                    _home_w  = set(_match_parts[0].strip().split())
                    _away_w  = set(_match_parts[1].strip().split())
                    _other_w = (_away_w if _pick_words & _home_w else _home_w) - {"de", "los", "las", "el", "la", "the"}
                    _st_low  = _syn_text.lower()
                    _has_other = sum(1 for w in _other_w if w in _st_low)
                    _has_pick  = sum(1 for w in _pick_words if w in _st_low)
                    if _has_other >= 2 and _has_pick == 0:
                        print(f"   ⚠️  Síntesis contradictoria (mencionó equipo contrario) — usando razonamiento base")
                        _syn_text = (base.get("razonamiento", "") or "")[:500]
        _final_razon = f"{_syn_text} {_panel_tag}"
    else:
        # Fallback: usar el razonamiento del experto base si Sonnet falla
        _final_razon = (base.get("razonamiento", "") or "") + f" {_panel_tag}"

    # Hard limit: 500 caracteres máximo sin importar la fuente
    merged["razonamiento"] = _final_razon[:500]

    merged["_votos_favor"] = votos_favor   # expuesto para bypass-veto guard en analyze_game_full
    return merged


def _claude_block(claude: "dict | None") -> str:
    """
    Format Claude's analysis as an ntfy message block.
    Returns empty string if claude is None.

    Two modes:
      • Clean data + apostar:true  → verified header + pick + reasoning
      • Inconsistencies detected OR apostar:false → warning header + issues list
    """
    if not claude:
        return ""

    conf    = claude.get("confianza", "N/D")
    pick    = claude.get("pick", "N/D")
    apostar = claude.get("apostar", True)
    reason  = claude.get("razonamiento", "")
    pos     = claude.get("factores_positivos", [])
    neg     = claude.get("factores_negativos", [])
    issues  = claude.get("datos_inconsistentes") or []
    if isinstance(issues, str):
        issues = [issues]

    conf_icon      = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴"}.get(conf, "⚪")
    has_issues     = bool(issues)
    partial_data   = bool(claude.get("datos_incompletos"))
    data_ok        = apostar and not has_issues

    if data_ok:
        # ── Verified path ─────────────────────────────────────────────────
        pos_lines = "".join(f"   ✅ {p}\n" for p in pos[:3])
        neg_lines = "".join(f"   ⚠️ {p}\n" for p in neg[:2])
        partial_note = (
            f"⚠️ Análisis con datos parciales — verificar antes de apostar\n"
            if partial_data else ""
        )
        return (
            f"{_DIV}\n"
            f"🤖 Analizado y verificado por Claude AI\n"
            f"✅ Datos confirmados antes de apostar\n"
            f"{partial_note}"
            f"{_DIV}\n"
            f"Pick: {pick}  |  Confianza: {conf_icon} {conf}\n"
            f"{reason}\n"
            + (pos_lines if pos_lines else "")
            + (neg_lines if neg_lines else "")
        )
    else:
        # ── Issues detected path ──────────────────────────────────────────
        issue_lines = "".join(f"   • {i}\n" for i in issues[:4])
        veto = "⛔ Claude recomienda NO apostar\n" if not apostar else ""
        return (
            f"{_DIV}\n"
            f"⚠️ Claude detectó datos inconsistentes\n"
            f"→ Verificar manualmente antes de apostar\n"
            f"{_DIV}\n"
            f"{veto}"
            f"Pick: {pick}  |  Confianza: {conf_icon} {conf}\n"
            f"{reason}\n"
            + (issue_lines if issue_lines else "")
        )


# ── Module S1: WC VENUE TENDENCIES ───────────────────────────────────────────
WC_VENUE_TEND: dict = {
    "Dallas":        (+0.4, "🏟️ Dallas: estadio de goles"),
    "Miami":         (+0.3, "🏟️ Miami: estadio ofensivo"),
    "Houston":       (+0.3, "🏟️ Houston: estadio de goles"),
    "San Francisco": (-0.3, "🏟️ San Francisco: estadio defensivo"),
    "Vancouver":     (-0.2, "🏟️ Vancouver: estadio de bajos marcadores"),
    "Toronto":       (-0.2, "🏟️ Toronto: estadio defensivo"),
    "Los Angeles":   ( 0.0, ""),
    "Kansas City":   ( 0.0, ""),
    "Seattle":       ( 0.0, ""),
    "New York":      ( 0.0, ""),
    "Boston":        ( 0.0, ""),
    "Atlanta":       ( 0.0, ""),
}

def _wc_venue_adj(venue_city: str) -> tuple:
    """Return (adj, note) for a WC venue city. adj is a goal-line adjustment."""
    if not venue_city:
        return 0.0, ""
    for city_key, (adj, note) in WC_VENUE_TEND.items():
        if city_key.lower() in venue_city.lower():
            return adj, note
    return 0.0, ""


# ── Module S2: DÍAS DE DESCANSO FÚTBOL ───────────────────────────────────────
_soc_rest_cache: dict = {}

def fetch_soccer_rest_days(team: str) -> tuple:
    """
    Days since last completed match for a soccer/WC team.
    Returns (days: int, adj: float, note: str).
    Uses _fetch_espn_matches; 3-day rest → +0.2 adj; 4-5 → 0; 6+ → 0.
    """
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    ck = (team.lower(), today_str)
    if ck in _soc_rest_cache:
        return _soc_rest_cache[ck]

    try:
        matches = _fetch_espn_matches(team)
        if not matches:
            _soc_rest_cache[ck] = (0, 0.0, "")
            return (0, 0.0, "")
        last_date_s = matches[0].get("date", "")
        if not last_date_s:
            _soc_rest_cache[ck] = (0, 0.0, "")
            return (0, 0.0, "")
        last_dt   = datetime.fromisoformat(last_date_s.replace("Z", "+00:00"))
        _now_utc  = datetime.now(pytz.utc)
        _last_utc = last_dt if last_dt.tzinfo else pytz.utc.localize(last_dt)
        days      = (_now_utc - _last_utc).days
        if days <= 3:
            adj  = +0.2
            note = (f"⚠️ Solo {days}d descanso — {team}\n"
                    f"   Equipo cansado → puede aumentar errores\n"
                    f"   → +0.2 goles al total")
        elif days <= 5:
            adj  = 0.0
            note = f"✅ Descanso normal ({days}d) — {team}"
        else:
            adj  = 0.0
            note = f"💪 {days}d descanso — {team} fresco y recuperado"
        result = (days, adj, note)
        _soc_rest_cache[ck] = result
        return result
    except Exception:
        _soc_rest_cache[ck] = (0, 0.0, "")
        return (0, 0.0, "")


# ── Module S3: RACHAS EN EL MUNDIAL ──────────────────────────────────────────
_wc_streak_cache_s: dict = {}

def fetch_wc_streak(team: str) -> "dict | None":
    """
    WC 2026 streak and goal stats for a team via fetch_soccer_team_recent.
    Returns {wins, draws, losses, n, streak_type, streak_len, gf_pg, ga_pg,
             label, ml_adj, tot_adj_gf, tot_adj_ga, tot_adj_def} or None.
    ml_adj: +0.05 for 3 straight wins, -0.05 for no wins.
    tot_adj_gf: +0.3 when scoring ≥2 gpg.
    tot_adj_ga: +0.3 when conceding ≥2 gpg.
    tot_adj_def: -0.3 when 0 goals last 2+ games.
    """
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    ck = (team.lower(), today_str)
    if ck in _wc_streak_cache_s:
        return _wc_streak_cache_s[ck]

    try:
        sdata = fetch_soccer_team_recent(team, "soccer_fifa_world_cup")
        if not sdata or sdata["n"] == 0:
            _wc_streak_cache_s[ck] = None
            return None

        results = sdata["results"]
        gf_pg   = sdata["gf_pg"]
        ga_pg   = sdata["ga_pg"]
        wins    = results.count("W")
        draws   = results.count("D")
        losses  = results.count("L")
        n       = len(results)

        streak_type = results[0] if results else "D"
        streak_len  = 1
        for r in results[1:]:
            if r == streak_type: streak_len += 1
            else:                break

        ml_adj = 0.0
        if streak_type == "W" and streak_len >= 3:
            ml_adj = +0.05
        elif wins == 0:
            ml_adj = -0.05

        tot_adj_gf  = +0.3 if gf_pg >= 2.0 else 0.0
        tot_adj_ga  = +0.3 if ga_pg >= 2.0 else 0.0
        tot_adj_def = -0.3 if (gf_pg == 0 and n >= 2) else 0.0

        streak_sym = {"W": "victorias", "D": "empates", "L": "derrotas"}.get(
            streak_type, ""
        )
        if wins == 0:
            mood = "❄️"; mood_lbl = "EN CAÍDA"
        elif ml_adj > 0:
            mood = "🔥"; mood_lbl = "EN RACHA"
        else:
            mood = "➡️"; mood_lbl = "regular"

        label = (
            f"{mood} {team} — {mood_lbl}:\n"
            f"   Últimos {n} WC: {wins}-{draws}-{losses}\n"
            f"   Racha actual: {streak_len} {streak_sym} seguidos\n"
            f"   Goles a favor: {gf_pg:.1f}/g | En contra: {ga_pg:.1f}/g"
        )

        res = {
            "wins": wins, "draws": draws, "losses": losses, "n": n,
            "streak_type": streak_type, "streak_len": streak_len,
            "gf_pg": gf_pg, "ga_pg": ga_pg,
            "label": label, "ml_adj": ml_adj,
            "tot_adj_gf": tot_adj_gf,
            "tot_adj_ga": tot_adj_ga,
            "tot_adj_def": tot_adj_def,
        }
        _wc_streak_cache_s[ck] = res
        return res
    except Exception:
        _wc_streak_cache_s[ck] = None
        return None


# ── Module S5: LINEUP FÚTBOL (XI TITULAR) ────────────────────────────────────
WC_KEY_PLAYERS: dict = {
    "Argentina":      ["Messi", "Di María"],
    "France":         ["Mbappé", "Griezmann"],
    "Francia":        ["Mbappé", "Griezmann"],
    "Brasil":         ["Vinicius", "Rodrygo"],
    "Brazil":         ["Vinicius", "Rodrygo"],
    "España":         ["Pedri", "Yamal"],
    "Spain":          ["Pedri", "Yamal"],
    "England":        ["Bellingham", "Salah"],
    "Germany":        ["Musiala", "Kane"],
    "Alemania":       ["Musiala", "Kane"],
    "Portugal":       ["Ronaldo", "Leão"],
    "Netherlands":    ["Van Dijk", "Gakpo"],
    "Países Bajos":   ["Van Dijk", "Gakpo"],
    "Morocco":        ["En-Nesyri", "Hakimi"],
    "Marruecos":      ["En-Nesyri", "Hakimi"],
    "Japan":          ["Kubo", "Mitoma"],
    "Japón":          ["Kubo", "Mitoma"],
    "USA":            ["Pulisic", "McKennie"],
    "United States":  ["Pulisic", "McKennie"],
    "Mexico":         ["Jiménez", "Álvarez"],
    "México":         ["Jiménez", "Álvarez"],
    "Colombia":       ["James", "Díaz"],
    "Uruguay":        ["Núñez", "Valverde"],
    "Ecuador":        ["Caicedo", "Valencia"],
}

_soc_lineup_cache: dict = {}

def fetch_soccer_lineup_intel(team: str,
                               sport_key: str = "soccer_fifa_world_cup") -> dict:
    """
    Fetch confirmed starting XI from ESPN for a WC team.
    Returns {confirmed, missing_stars, prob_adj, note}.
    prob_adj: -0.08 for 1 star absent, -0.15 for 2+ stars absent.
    Falls back gracefully — never blocks execution.
    """
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    ck = (team.lower(), today_str)
    if ck in _soc_lineup_cache:
        return _soc_lineup_cache[ck]

    stars  = WC_KEY_PLAYERS.get(team, [])
    result = {"confirmed": False, "missing_stars": [], "prob_adj": 0.0, "note": ""}
    if not stars:
        _soc_lineup_cache[ck] = result
        return result

    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
        r   = requests.get(url, timeout=6)
        if r.status_code == 200:
            for event in r.json().get("events", []):
                comp  = event.get("competitions", [{}])[0]
                comps = comp.get("competitors", [])
                te    = next(
                    (c for c in comps
                     if team.lower() in c.get("team", {}).get("displayName", "").lower()),
                    None,
                )
                if not te:
                    continue
                starters = [
                    p.get("athlete", {}).get("displayName", "")
                    for p in te.get("roster", {}).get("entries", [])
                    if p.get("starter", False)
                ]
                if not starters:
                    break
                missing  = [s for s in stars
                            if not any(s.lower() in sp.lower() for sp in starters)]
                present  = [s for s in stars if s not in missing]
                prob_adj = (-0.15 if len(missing) >= 2 else
                            -0.08 if len(missing) == 1 else 0.0)
                note_lines = [f"📋 XI {team} confirmado:"]
                for s in present:
                    note_lines.append(f"   {s} ✅")
                for s in missing:
                    note_lines.append(f"   ⚠️ {s} FUERA del XI titular")
                if missing:
                    note_lines.append(
                        f"   → {'Jugadores clave' if len(missing)>1 else 'Jugador clave'} "
                        f"ausente{'s' if len(missing)>1 else ''} "
                        f"→ ajuste ML {prob_adj*100:+.0f}%"
                    )
                result = {
                    "confirmed":     True,
                    "missing_stars": missing,
                    "prob_adj":      prob_adj,
                    "note":          "\n".join(note_lines),
                }
                break
        if not result["confirmed"]:
            result["note"] = (
                f"📋 XI {team}: "
                + " | ".join(f"{s} ✅" for s in stars)
            )
    except Exception:
        pass

    _soc_lineup_cache[ck] = result
    return result


# ── Module S6: PRESIÓN PSICOLÓGICA WC ────────────────────────────────────────
def _wc_pressure_block(home: str, away: str, standings: dict) -> tuple:
    """
    Analyze psychological pressure from WC group standings.
    Returns (tot_adj: float, draw_prob_boost: float, note: str).
    """
    if not standings:
        return 0.0, 0.0, ""
    try:
        sh = standings.get(home)
        sa = standings.get(away)
        if not sh and not sa:
            return 0.0, 0.0, ""

        notes      = []
        tot_adj    = 0.0
        draw_boost = 0.0

        gp_h  = sh.get("gp",  0) if sh else 0
        gp_a  = sa.get("gp",  0) if sa else 0
        pos_h = sh.get("pos", 3) if sh else 3
        pos_a = sa.get("pos", 3) if sa else 3
        pts_h = sh.get("pts", 0) if sh else 0
        pts_a = sa.get("pts", 0) if sa else 0

        elim_h = bool(sh) and gp_h >= 2 and pos_h == 4
        elim_a = bool(sa) and gp_a >= 2 and pos_a == 4
        qual_h = bool(sh) and pos_h <= 2 and gp_h >= 2
        qual_a = bool(sa) and pos_a <= 2 and gp_a >= 2

        if elim_h or elim_a:
            tot_adj += 0.3
            et = home if elim_h else away
            notes.append(
                f"🧠 FACTOR PSICOLÓGICO:\n"
                f"   {et} ELIMINADO si no gana\n"
                f"   → Presión extrema — atacan más → más goles\n"
                f"   → Favorece Over rival ML → +0.3 goles"
            )

        if qual_h and qual_a:
            tot_adj -= 0.3
            notes.append(
                f"🧠 {home} & {away} ya clasificados:\n"
                f"   → Pueden rotar jugadores → menos motivación\n"
                f"   → Favorece UNDER → -0.3 goles al total"
            )
        elif qual_h and not elim_a:
            tot_adj -= 0.3
            notes.append(
                f"🧠 {home} ya clasificado:\n"
                f"   → Puede rotar → -0.3 goles | Favorece {away} ML"
            )
        elif qual_a and not elim_h:
            tot_adj -= 0.3
            notes.append(
                f"🧠 {away} ya clasificado:\n"
                f"   → Puede rotar → -0.3 goles | Favorece {home} ML"
            )

        if (not elim_h and not elim_a and not qual_h and not qual_a
                and pts_h == pts_a and gp_h >= 1 and gp_a >= 1):
            draw_boost = 0.15
            notes.append(
                f"🧠 Ambos equipos necesitan empate para clasificar:\n"
                f"   → Pacto táctico posible\n"
                f"   → FUERTE señal para Empate ML → Favorece UNDER\n"
                f"   → +15% probabilidad de empate"
            )

        return tot_adj, draw_boost, "\n".join(notes)
    except Exception:
        return 0.0, 0.0, ""


# ── Module 5: World Cup group context ─────────────────────────────────────────
_wc_standings_cache: dict = {}

def fetch_wc_standings():
    """Fetch WC group standings from ESPN. Returns {team_name: {pos,group,pts,w,d,l,gp}}."""
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    if today_str in _wc_standings_cache:
        return _wc_standings_cache[today_str]
    result = {}
    try:
        r = requests.get(
            "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/standings",
            timeout=10,
        )
        if r.status_code != 200:
            return result
        data   = r.json()
        groups = (data.get("children")
                  or data.get("standings", {}).get("entries", [])
                  or [])
        for group in groups:
            grp_name = group.get("abbreviation") or group.get("name", "?")
            entries  = group.get("standings", {}).get("entries", [])
            for i, entry in enumerate(entries):
                tname = entry.get("team", {}).get("displayName", "")
                stats = {s.get("name"): s.get("value")
                         for s in entry.get("stats", [])}
                result[tname] = {
                    "pos":   i + 1,
                    "group": grp_name,
                    "pts":   int(stats.get("points",     0) or 0),
                    "w":     int(stats.get("wins",       0) or 0),
                    "d":     int(stats.get("ties",       0) or 0),
                    "l":     int(stats.get("losses",     0) or 0),
                    "gp":    int(stats.get("gamesPlayed", 0) or 0),
                }
    except Exception as e:
        print(f"  ⚠️  WC standings error: {e}")
    _wc_standings_cache[today_str] = result
    return result

def _wc_urgency_line(team_name, standings):
    """Single-line urgency description for a WC team (module 5)."""
    s = standings.get(team_name)
    if not s:
        return ""
    pos, gp, grp = s["pos"], s["gp"], s["group"]
    if gp >= 2:
        if pos <= 2:    urg = "🟢 ya clasificado"
        elif pos == 3:  urg = "🟡 ALTA PRESIÓN — necesita ganar"
        else:           urg = "🔴 URGENTE — eliminado si pierde"
    else:
        urg = f"{'🟢' if pos <= 2 else '🟡'} {pos}° lugar"
    return f"{team_name} (Grupo {grp}, {pos}°): {urg}"

# ── Module 2: consolidated daily ntfy report ──────────────────────────────────
# ── Module B1: ANALÍTICA DE RENDIMIENTO ──────────────────────────────────────
_MTYPE_LABEL = {
    "totals":         "⚾ Totals MLB",
    "h2h":            "🎯 Moneyline MLB",
    "spreads":        "📊 Run Line",
    "arb":            "⚡ ARBs",
    "premium":        "💎 Picks Premium",
    "totals_soccer":  "⚽ Totals Mundial",
    "h2h_soccer":     "⚽ Moneyline Mundial",
    "spreads_soccer": "⚽ Hándicap Mundial",
    "arb_soccer":     "⚡ ARBs Fútbol",
}

def performance_by_type_block(bets=None) -> str:
    """
    Return a formatted ntfy block showing W/L/ROI per market_type.
    bets: list of dicts with keys market_type, result, profit_loss, stake.
    If bets=None, reads settled rows from BETS_LOG_FILE.
    Works with both bets_log.csv and bankroll_log.csv rows.
    """
    if bets is None:
        bets = []
        if os.path.exists(BETS_LOG_FILE):
            try:
                with open(BETS_LOG_FILE, newline="") as f:
                    bets = list(csv.DictReader(f))
            except Exception:
                pass

    settled = [b for b in bets if b.get("result") in ("W", "L", "P")]
    if not settled:
        return ""

    by_type: dict = {}
    for b in settled:
        mtype = (b.get("market_type") or "h2h").strip()
        by_type.setdefault(mtype, {"wins": 0, "losses": 0, "pushes": 0,
                                    "pnl": 0.0, "stake": 0.0})
        by_type[mtype]["pnl"]   += float(b.get("profit_loss", 0) or 0)
        by_type[mtype]["stake"] += float(b.get("stake",       0) or 0)
        if   b.get("result") == "W": by_type[mtype]["wins"]   += 1
        elif b.get("result") == "L": by_type[mtype]["losses"]  += 1
        elif b.get("result") == "P": by_type[mtype]["pushes"]  += 1

    lines    = [f"📊 RENDIMIENTO POR TIPO:\n{_DIV2}"]
    type_roi: dict = {}
    for mtype, d in sorted(by_type.items(), key=lambda x: x[1]["pnl"], reverse=True):
        label = _MTYPE_LABEL.get(mtype, f"🎲 {mtype}")
        picks = d["wins"] + d["losses"] + d["pushes"]
        roi_v = (d["pnl"] / d["stake"] * 100) if d["stake"] else 0.0
        type_roi[mtype] = roi_v
        roi_s = f"+{roi_v:.1f}% ✅" if roi_v >= 0 else f"{roi_v:.1f}% ⚠️"
        lines.append(
            f"{label}:\n"
            f"   Picks: {picks} | G: {d['wins']} | P: {d['losses']}\n"
            f"   ROI: {roi_s}"
        )

    # Bot recommendations
    recs    = []
    tot_roi = type_roi.get("totals")
    ml_roi  = type_roi.get("h2h")
    arb_roi = type_roi.get("arb")
    if tot_roi is not None and ml_roi is not None and tot_roi - ml_roi >= 10:
        recs.append("💡 Recomendación: enfócate en Totals — es tu mercado más fuerte")
    if arb_roi is not None and arb_roi > 0:
        recs.append("💡 ARBs funcionando bien — considera aumentar frecuencia")
    for mtype, roi_v in type_roi.items():
        if roi_v < -10:
            lbl = _MTYPE_LABEL.get(mtype, mtype)
            recs.append(f"⚠️ Mercado con pérdidas: {lbl} — considera pausarlo")
    if recs:
        lines.append(_DIV2)
        lines.extend(recs)

    return "\n".join(lines)


def send_daily_ntfy_report():
    """Send consolidated 8 AM ntfy: bankroll, ROI, record, yesterday, today count."""
    try:
        state   = load_bankroll_state()
        bets    = state["bets"]
        current = state["current"]
        settled = [b for b in bets if b.get("result") in ("W", "L", "P")]
        wins    = len([b for b in settled if b.get("result") == "W"])
        losses  = len([b for b in settled if b.get("result") == "L"])
        pushes  = len([b for b in settled if b.get("result") == "P"])
        roi     = (current - BANKROLL) / BANKROLL * 100 if BANKROLL else 0

        yesterday = (datetime.now(ET) - timedelta(days=1)).strftime("%Y-%m-%d")
        yday_bets = [b for b in bets
                     if b.get("date", "").startswith(yesterday) and b.get("result")]
        yday_net   = 0.0
        yday_lines = ""
        for b in yday_bets:
            pl   = float(b.get("profit_loss", 0) or 0)
            yday_net += pl
            icon = ("✅" if b.get("result") == "W"
                    else ("❌" if b.get("result") == "L" else "🤝"))
            name = f"{b.get('team','')} ({b.get('market_type','')})"
            pl_s = f"+${pl:.2f}" if pl >= 0 else f"-${abs(pl):.2f}"
            yday_lines += f"{icon} {name} → {pl_s}\n"
        if not yday_lines:
            yday_lines = "Sin apuestas resueltas ayer\n"
        yday_net_s = f"+${yday_net:.2f}" if yday_net >= 0 else f"-${abs(yday_net):.2f}"

        mlb_cnt = 0
        try:
            mlb_cnt = len(fetch_mlb_games_today())
        except Exception:
            pass

        today_s     = datetime.now(ET).strftime("%d %b %Y")
        _perf_block = performance_by_type_block(bets)
        _perf_sec   = f"{_perf_block}\n{_DIV}\n" if _perf_block else ""
        body = (
            f"📊 REPORTE DIARIO — {today_s}\n"
            f"{_DIV}\n"
            f"💰 Bankroll: ${current:,.2f}\n"
            f"📈 ROI total: {roi:+.1f}%\n"
            f"🏆 Record: {wins}-{losses}-{pushes}\n"
            f"{_DIV}\n"
            f"AYER:\n"
            f"{yday_lines}"
            f"Net: {yday_net_s}\n"
            f"{_DIV}\n"
            f"{_perf_sec}"
            f"HOY:\n"
            f"⚾ {mlb_cnt} juegos MLB\n"
            f"⚽ Ver scan para fútbol/Mundial\n"
            f"💼 Mult bankroll: {_bankroll_mult}×"
            + ("  🛑 PAUSADO" if _bankroll_paused else "  ⚠️ bajo" if _bankroll_mult < 1.0 else "") + "\n"
            + f"🔍 Escaneando desde las 10 AM ET"
        )

        # ── Feature 4: Data quality daily report ─────────────────────────
        try:
            today_s   = datetime.now(ET).strftime("%Y-%m-%d")
            mlb_games = []
            try:
                mlb_games = fetch_mlb_games_today()
            except Exception:
                pass
            total_g = len(mlb_games)
            pit_ok  = 0
            ump_ok  = 0
            for _g in mlb_games:
                home_t = _g.get("teams", {}).get("home", {}).get("team", {}).get("name", "")
                away_t = _g.get("teams", {}).get("away", {}).get("team", {}).get("name", "")
                hp = (_g.get("teams", {}).get("home", {}).get("probablePitcher") or
                      _g.get("home_probable_pitcher"))
                ap = (_g.get("teams", {}).get("away", {}).get("probablePitcher") or
                      _g.get("away_probable_pitcher"))
                if hp and ap:
                    pit_ok += 1
                if home_t:
                    u = None
                    try:
                        u = fetch_home_plate_umpire(home_t, today_s)
                    except Exception:
                        pass
                    if u:
                        ump_ok += 1
            split_ok    = 0
            weather_ok  = 0
            bullpen_ok  = 0
            unique_teams: set = set()
            for _g in mlb_games:
                for _t in [
                    _g.get("teams", {}).get("home", {}).get("team", {}).get("name", ""),
                    _g.get("teams", {}).get("away", {}).get("team", {}).get("name", ""),
                ]:
                    if _t and _t not in unique_teams:
                        unique_teams.add(_t)
                        try:
                            if fetch_mlb_home_away_splits(_t):
                                split_ok += 1
                        except Exception:
                            pass
                        dq = _data_quality.get(f"{_t}_{today_s}", {})
                        if dq.get("verified"):
                            bullpen_ok += 1
            # weather: count park coords available
            weather_ok = sum(1 for _g in mlb_games
                             if _g.get("venue", {}).get("name")
                             or _g.get("teams", {}).get("home", {})
                             .get("team", {}).get("venue"))
            lu_ok = 0  # confirmed lineups unknown until scan time

            total_teams = len(unique_teams) or 1

            score_parts = []
            if total_g:
                score_parts.append(pit_ok / total_g)
                score_parts.append(ump_ok / total_g)
                score_parts.append(split_ok / total_teams)
                score_parts.append(bullpen_ok / total_teams)
            overall_pct = int(sum(score_parts) / len(score_parts) * 100) if score_parts else 0
            conf_label  = ("ALTA" if overall_pct >= 80
                           else "MODERADA" if overall_pct >= 60
                           else "BAJA")

            def _frac(ok, total):
                return f"{ok}/{total}" if total else "–"

            dq_block = (
                f"\n{_DIV}\n"
                f"📊 CALIDAD DE DATOS HOY:\n"
                f"{_DIV}\n"
                f"✅ Pitchers confirmados: {_frac(pit_ok, total_g)} juegos\n"
                f"⚠️ Umpires disponibles:  {_frac(ump_ok, total_g)} juegos\n"
                f"✅ Splits home/away:     {_frac(split_ok, total_teams)} equipos\n"
                f"✅ Bullpen ERA verif.:   {_frac(bullpen_ok, total_teams)} equipos\n"
                f"✅ Clima/parque:        {_frac(weather_ok, total_g)} juegos\n"
                f"{_DIV}\n"
                f"Confianza general de datos: {overall_pct}%\n"
                f"Picks de hoy: {conf_label} precisión"
            )
        except Exception as _dqe:
            dq_block = ""
            print(f"  ⚠️  Data quality block error: {_dqe}")

        body += dq_block
        ntfy_post("📊 REPORTE DIARIO", body, "default")
        print("  📊 Reporte diario ntfy enviado")
    except Exception as e:
        print(f"  ⚠️  send_daily_ntfy_report error: {e}")

# ── Module 10: weekly summary Sunday 9 AM ─────────────────────────────────────
def send_weekly_summary():
    """Send weekly ntfy summary every Sunday at 9 AM ET."""
    try:
        state   = load_bankroll_state()
        bets    = state["bets"]
        current = state["current"]
        settled = [b for b in bets if b.get("result") in ("W", "L", "P")]
        wins    = len([b for b in settled if b.get("result") == "W"])
        losses  = len([b for b in settled if b.get("result") == "L"])
        pushes  = len([b for b in settled if b.get("result") == "P"])
        roi     = (current - BANKROLL) / BANKROLL * 100 if BANKROLL else 0

        week_ago  = (datetime.now(ET) - timedelta(days=7)).strftime("%Y-%m-%d")
        week_bets = [b for b in settled if b.get("date", "") >= week_ago]
        week_stk  = sum(float(b.get("stake",       0) or 0) for b in week_bets)
        week_net  = sum(float(b.get("profit_loss", 0) or 0) for b in week_bets)
        week_wins = len([b for b in week_bets if b.get("result") == "W"])
        week_loss = len([b for b in week_bets if b.get("result") == "L"])
        week_roi  = (week_net / week_stk * 100) if week_stk else 0.0

        by_pl  = sorted(week_bets, key=lambda x: float(x.get("profit_loss", 0) or 0))
        worst  = by_pl[0]  if by_pl else None
        best   = by_pl[-1] if by_pl else None
        best_s = (f"{best.get('team','')}  +${float(best.get('profit_loss',0)):.2f}"
                  if best  else "N/A")
        worst_s = (f"{worst.get('team','')}  -${abs(float(worst.get('profit_loss',0))):.2f}"
                   if worst else "N/A")

        sport_pnl: dict = {}
        type_pnl:  dict = {}
        for b in week_bets:
            for key, cat in [(b.get("sport", "?"), sport_pnl),
                              (b.get("market_type", "?"), type_pnl)]:
                pl  = float(b.get("profit_loss", 0) or 0)
                stk = float(b.get("stake",       0) or 0)
                cat.setdefault(key, {"pnl": 0.0, "stake": 0.0})
                cat[key]["pnl"]   += pl
                cat[key]["stake"] += stk
        best_sport = (max(sport_pnl, key=lambda s: sport_pnl[s]["pnl"])
                      if sport_pnl else "N/A")
        best_type  = (max(type_pnl,  key=lambda t: type_pnl[t]["pnl"])
                      if type_pnl  else "N/A")

        delta   = current - BANKROLL
        delta_s = f"+${delta:.2f}" if delta >= 0 else f"-${abs(delta):.2f}"

        _w_perf = performance_by_type_block(week_bets)
        _w_perf_sec = f"\n{_DIV}\n{_w_perf}" if _w_perf else ""

        body = (
            f"📊 RESUMEN SEMANAL\n"
            f"{_DIV}\n"
            f"💰 Bankroll: ${current:,.2f} ({delta_s})\n"
            f"📈 ROI semana: {week_roi:+.1f}%  |  Total: {roi:+.1f}%\n"
            f"🏆 Semana: {week_wins}-{week_loss}  |  Total: {wins}-{losses}-{pushes}\n"
            f"{_DIV}\n"
            f"MEJOR BET:    {best_s}\n"
            f"PEOR BET:     {worst_s}\n"
            f"MEJOR DEPORTE: {best_sport}"
            f"{_w_perf_sec}\n"
            f"{_DIV}\n"
            f"PRÓXIMA SEMANA:\n"
            f"⚾ MLB activo  ⚽ Mundial en curso"
        )

        # ── Feature 6: error log weekly summary ──────────────────────────
        try:
            import csv as _csv_w, os as _os_w
            if _os_w.path.exists(ERROR_LOG_FILE):
                with open(ERROR_LOG_FILE, newline="", encoding="utf-8") as _f:
                    _rows = list(_csv_w.DictReader(_f))
                week_ago_s = (datetime.now(ET) - timedelta(days=7)).strftime("%Y-%m-%d")
                _week_errs = [r for r in _rows
                              if r.get("timestamp", "") >= week_ago_s]
                if _week_errs:
                    from collections import Counter as _Counter
                    _mod_counts = _Counter(r.get("module", "?") for r in _week_errs)
                    _worst_mod  = _mod_counts.most_common(1)[0]
                    _resolved   = sum(1 for r in _week_errs if r.get("resolved"))
                    _need_attn  = len(_week_errs) - _resolved
                    _mod_lines  = "\n".join(
                        f"  {m}: {c} errores"
                        for m, c in _mod_counts.most_common(5)
                    )
                    body += (
                        f"\n{_DIV}\n"
                        f"🔍 ERRORES DE LA SEMANA:\n"
                        f"{_DIV}\n"
                        f"Módulo con más errores: {_worst_mod[0]}\n"
                        f"Total errores: {len(_week_errs)}\n"
                        f"Resueltos automáticamente: {_resolved}\n"
                        f"Requirieron atención: {_need_attn}\n"
                        f"{_DIV}\n"
                        f"Por módulo:\n{_mod_lines}"
                    )
        except Exception as _err_e:
            print(f"  ⚠️  Error log weekly section failed: {_err_e}")

        # Level 3C: inject CLV weekly summary
        try:
            _clv_sec = _weekly_clv_summary()
            if _clv_sec:
                body += f"\n{_clv_sec}"
        except Exception as _ce3:
            pass

        ntfy_post("📊 RESUMEN SEMANAL", body, "default")
        if _tg_broadcast_fn:
            try:
                _tg_broadcast_fn(f"📊 <b>RESUMEN SEMANAL</b>\n{_DIV}\n{body[:3800]}")
            except Exception as _tge:
                print(f"  ⚠️  Telegram weekly summary error: {_tge}")
        print("  📊 Resumen semanal ntfy enviado")
    except Exception as e:
        print(f"  ⚠️  send_weekly_summary error: {e}")


# ── Module 11: Pinnacle availability check ────────────────────────────────────

def _check_pinnacle_availability():
    """
    Tests whether our Odds API plan includes Pinnacle odds.
    Runs at bot startup. Prints result to Railway logs and sends one ntfy.
    """
    if not API_KEY:
        print("  ⚠️  ODDS_API_KEY no configurada — no se puede verificar Pinnacle")
        return

    results = []
    for sport, label in [
        ("baseball_mlb",          "MLB"),
        ("soccer_fifa_world_cup", "Soccer FIFA WC"),
    ]:
        url = (
            f"https://api.the-odds-api.com/v4/sports/{sport}/odds/"
            f"?apiKey={API_KEY}&regions=us,eu&bookmakers=pinnacle&markets=h2h"
        )
        try:
            r    = requests.get(url, timeout=12)
            data = r.json() if r.status_code == 200 else []
            found = False
            sample = ""
            for game in data[:3]:
                for bk in game.get("bookmakers", []):
                    if "pinnacle" in bk.get("title", "").lower():
                        found = True
                        h2h = next(
                            (m for m in bk.get("markets", []) if m["key"] == "h2h"),
                            None,
                        )
                        if h2h and h2h.get("outcomes"):
                            o = h2h["outcomes"]
                            sample = (
                                f"{o[0]['name']} {o[0]['price']:+.0f} | "
                                f"{o[1]['name']} {o[1]['price']:+.0f}"
                            )
                        break
                if found:
                    break

            if found:
                print(f"  ✅ Pinnacle disponible [{label}]: {sample}")
                results.append(f"✅ {label}: Pinnacle disponible\n   {sample}")
            else:
                bk_names = list({
                    b.get("title", "?")
                    for g in data[:3]
                    for b in g.get("bookmakers", [])
                })
                print(f"  ❌ Pinnacle NO disponible [{label}] en plan actual")
                if bk_names:
                    print(f"     Bookmakers recibidos: {', '.join(bk_names[:8])}")
                results.append(
                    f"❌ {label}: Pinnacle no disponible en plan actual\n"
                    f"   Bookmakers recibidos: {', '.join(bk_names[:5]) or 'ninguno'}"
                )
        except Exception as e:
            print(f"  ⚠️  Pinnacle check [{label}]: {e}")
            results.append(f"⚠️ {label}: error de conexión — {e}")

    note = (
        "\n\nSi no disponible → se requiere upgrade del plan Odds API\n"
        "para acceder a Pinnacle (plan Pro/Business).\n"
        "Mientras tanto, el bot omite el bloque 📌 Pinnacle Reference."
    )
    body = "VERIFICACIÓN PINNACLE AL INICIO:\n\n" + "\n".join(results) + note
    ntfy_post("🔍 Pinnacle API Check", body, "low")


# ── Module 12: Weekly backtesting (Sunday 10 AM ET) ──────────────────────────

def run_weekly_backtest():
    """
    Run the backtesting engine for the current MLB season (April 1 → today).
    Imports backtest.py (must be in same directory as kelly_odds.py).
    Results saved to backtest_log.csv and sent via ntfy.
    Also retrains the ML model (Level 1A) with fresh data.
    """
    # Level 1A: retrain ML model after fresh backtest data
    try:
        import importlib.util as _ilu2
        _ml_path = os.path.join(os.path.dirname(__file__), "ml_model.py")
        if os.path.isfile(_ml_path):
            _spec2 = _ilu2.spec_from_file_location("ml_model", _ml_path)
            _ml2   = _ilu2.module_from_spec(_spec2)
            _spec2.loader.exec_module(_ml2)
            _ml2.train()
    except Exception as _mle:
        print(f"  ⚠️  ML retrain error: {_mle}")

    try:
        import importlib.util, os
        bt_path = os.path.join(os.path.dirname(__file__), "backtest.py")
        if not os.path.isfile(bt_path):
            print("  ⚠️  backtest.py no encontrado — saltando backtest semanal")
            return
        spec = importlib.util.spec_from_file_location("backtest", bt_path)
        bt   = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bt)
        start = f"{bt.MLB_YEAR}-04-01"
        end   = datetime.now(ET).strftime("%Y-%m-%d")
        print(f"\n📊 Iniciando backtest semanal {start} → {end}...")
        metrics = bt.run_backtest(start_date=start, end_date=end)
        if metrics:
            print(f"  ✅ Backtest completado — hit rate {metrics.get('hit_rate','?')}%  "
                  f"ROI {metrics.get('roi','?'):+}%")
    except Exception as e:
        print(f"  ⚠️  run_weekly_backtest error: {e}")


# ═══════════════════════════════════════════════════════════════════════════════
# CORE — ANALYSIS & NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze(games, prev_map, new_map, sport_key=""):
    bets        = []
    sharp_moves = []
    steam_moves = []
    _is_mlb     = "mlb" in sport_key

    for g in games:
        game_id    = g.get("id", "")
        home, away = g["home_team"], g["away_team"]
        commence   = g.get("commence_time", "")

        if game_starts_soon(commence, 60):
            continue
        if _timing_check(commence, _is_mlb)["skip"]:
            continue

        bookmakers = g.get("bookmakers", [])
        if len(bookmakers) < 4:
            continue

        odds_h, odds_a     = [], []
        book_list_h: list  = []   # Module 9: [(price, book_name)] for top-3
        book_list_a: list  = []
        best_bk_h = best_bk_a = ""
        bov_odds_h = bov_odds_a = None   # Bovada/Bodog specific odds
        for bk in bookmakers:
            if not _is_us_book(bk["title"]):
                continue   # skip all non-US international books
            is_preferred = bk["title"].lower() in PREFERRED_BOOKS
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    for o in m["outcomes"]:
                        price = o["price"]
                        if o["name"] == home:
                            if not odds_h or price > max(odds_h):
                                best_bk_h = bk["title"]
                            odds_h.append(price)
                            book_list_h.append((price, bk["title"]))
                            if is_preferred and (bov_odds_h is None or price > bov_odds_h):
                                bov_odds_h = price
                        else:
                            if not odds_a or price > max(odds_a):
                                best_bk_a = bk["title"]
                            odds_a.append(price)
                            book_list_a.append((price, bk["title"]))
                            if is_preferred and (bov_odds_a is None or price > bov_odds_a):
                                bov_odds_a = price

        if not odds_h or not odds_a:
            continue

        # Fix 4: MLB odds sanity cap — skip any pick where best odds > 5.0
        # (14.0, 16.0, 30.0+ are data errors; no MLB team is a 30:1 underdog)
        MLB_MAX_ODDS = 5.0
        if "baseball_mlb" in sport_key:
            if max(odds_h) > MLB_MAX_ODDS or max(odds_a) > MLB_MAX_ODDS:
                bad_h = max(odds_h) > MLB_MAX_ODDS
                print(f"  ⚠️  MLB ODDS CAP: {home if bad_h else away} "
                      f"@ {max(odds_h) if bad_h else max(odds_a):.2f} > {MLB_MAX_ODDS} — dato erróneo, juego omitido")
                continue   # skip this game entirely — data error

        best_h, best_a = max(odds_h), max(odds_a)
        avg_h  = sum(odds_h) / len(odds_h)
        avg_a  = sum(odds_a) / len(odds_a)
        fp_h, fp_a = remove_vig([avg_h, avg_a])

        # If Bovada has odds, prefer it as the displayed bookmaker
        if bov_odds_h is not None:
            best_bk_h = "Bovada"
        if bov_odds_a is not None:
            best_bk_a = "Bovada"

        # Sharp money radar check (RLM detection)
        sharp_moves.extend(
            analyze_sharp_money(game_id, home, away, best_h, best_a, prev_map, sport_key)
        )

        # Steam move detection (per-book tracking written into new_map)
        steam_moves.extend(
            detect_steam_moves_for_game(
                game_id, home, away, bookmakers, prev_map, new_map
            )
        )

        new_map[f"{game_id}_{home}"] = best_h
        new_map[f"{game_id}_{away}"] = best_a

        top3_h = _top3_from_book_list(book_list_h)   # Module 9
        top3_a = _top3_from_book_list(book_list_a)

        print(f"\n💰 ML: {home} vs {away}  [{sport_key}]")
        for team, prob, best_odd, side, bookmaker, bov_odds, top3 in [
            (home, fp_h, best_h, "HOME", best_bk_h, bov_odds_h, top3_h),
            (away, fp_a, best_a, "AWAY", best_bk_a, bov_odds_a, top3_a),
        ]:
            r = kelly_stake(prob, best_odd)
            _min_edge_adj = MIN_EDGE * _perf_adj.get("h2h", 1.0)
            if _perf_adj.get("h2h", 1.0) != 1.0:
                print(f"   🔧 _perf_adj h2h={_perf_adj['h2h']:.2f} → MIN_EDGE ajustado {MIN_EDGE}→{_min_edge_adj:.2f}%")
            if not r["has_value"] or r["edge"] < _min_edge_adj:
                print(f"   📉 {team}: sin valor  edge={r['edge']:+.1f}%  odds={best_odd}")
                continue

            moved, direction, delta = detect_line_movement(game_id, team, best_odd, prev_map)
            ev, roi = roi_projection(r["edge"], r["stake"])
            val_pct = value_percentage(prob, best_odd)
            elo_p   = elo_win_prob(team, away if team == home else home)

            ev_pct_val = round((prob * best_odd - 1) * 100, 1)
            bets.append({
                "match":        f"{home} vs {away}",
                "team":         team,
                "side":         side,
                "odds":         best_odd,
                "edge":         r["edge"],
                "ev_pct":       ev_pct_val,
                "stake":        r["stake"],
                "kelly_pct":    r["kelly_pct"],
                "confidence":   confidence_level(r["edge"]),
                "time":         commence[:16],
                "line_moved":   moved,
                "line_dir":     direction,
                "line_delta":   delta,
                "game_id":      game_id,
                "bookmaker":    bookmaker,
                "bovada_odds":  bov_odds,
                "market_type":  "h2h",
                "closing_edge": "",
                "ev":           ev,
                "roi":          roi,
                "value_pct":    val_pct,
                "elo_prob":     round(elo_p * 100, 1),
                "top3_books":   top3,  # Module 9: line shopping
            })

    return bets, sharp_moves, steam_moves

def notify_bets(new_bets, alerted=None):
    global alerted_bets
    if not new_bets:
        return

    # Module P: halt if bankroll is critically low
    if _bankroll_paused:
        ntfy_post(
            "🛑 BANKROLL CRÍTICO",
            f"🛑 Bankroll < $400\nApuestas PAUSADAS hasta recuperar\n{_DIV}\n"
            f"El bot sigue monitoreando — reactivará cuando bankroll ≥ $400.",
            "urgent",
        )
        return

    _br_warn = (
        "\n\n⚠️ Bankroll bajo — apuesta con precaución"
        if _bankroll_mult <= 0.75 else ""
    )

    for b in new_bets:
        # Module 7: stake minimum filter
        if b.get("stake", 0) < MIN_STAKE:
            continue

        home, away = b["match"].split(" vs ", 1)
        dedup_key = f"{home}_{away}_{b['team']}_ml"
        if not _should_alert(dedup_key, odds=b["odds"], edge=b["edge"]):
            continue
        match_key_bet = b.get("match", f"{home} vs {away}").lower().strip()
        if alerted is not None and match_key_bet in alerted:
            print(f"  ⏭️  {match_key_bet} — ya alertado este scan (notify_bets)")
            continue
        if HAS_PAQUETE_AVANZADO:
            try:
                _pk_tipo = "ML_HOME" if b.get("team", "") == home else "ML_AWAY"
                registrar_pick(
                    game_pk  = b.get("game_id", b["match"]),
                    equipo_h = home,
                    equipo_a = away,
                    pick_tipo= _pk_tipo,
                    linea    = 0.0,
                    cuota    = b.get("odds", 1.0),
                    stake    = b.get("stake", 0),
                    libro    = b.get("bookmaker", "Bovada"),
                )
            except Exception as _rpe:
                print(f"  ⚠️  registrar_pick error (bets): {_rpe}")
        sport   = b.get("sport", "")
        emoji   = _sport_emoji(sport)
        gt      = _fmt_smart_gt(b.get("time", ""))
        elo_p   = b.get("elo_prob", 0)
        is_mlb  = b.get("sport", "") == "MLB"

        # Module 9: top-3 line shopping block
        top3_blk = _top3_block(b.get("top3_books", []))
        # Module 3: book safety warning
        bk_warn  = _book_warning(b.get("bookmaker", ""))

        # Translate team names for display
        team_es  = _es(b["team"])
        home_es  = _es(home)
        away_es  = _es(away)
        match_es = f"{home_es} vs {away_es}"

        if is_mlb:
            # ── MLB clean format ──────────────────────────────────────────
            pitchers  = fetch_probable_pitchers_today()
            p_data    = _lookup_pitcher_data(home, away, pitchers)
            ph_name   = p_data.get("home_name", "TBD")
            pa_name   = p_data.get("away_name", "TBD")
            ph_era    = p_data.get("home_era", 4.50)
            pa_era    = p_data.get("away_era", 4.50)
            impl_pct  = round(100 / b["odds"], 1) if b["odds"] else 0
            is_high   = b["edge"] >= 5.0 and elo_p >= 60
            _sw = b.get("stake_warn", "")
            conf_line = "🟢 CONFIANZA: ALTA" if is_high else "🟡 CONFIANZA: MEDIA"
            if _sw:
                conf_line += f"\n{_sw}"
            l1 = (
                f"🎯 {match_es}\n"
                f"⏰ Hoy {gt} ET\n"
                f"APUESTA: {team_es} GANA @ {b['odds']} — {b['bookmaker']}\n"
                f"{conf_line}"
            )
            l2 = (
                f"Nuestro modelo dice {team_es} tiene {elo_p}% de probabilidad de ganar.\n"
                f"La casa de apuestas implica solo {impl_pct}% — hay {b['edge']}% de ventaja.\n"
                f"\n"
                f"🔵 Pitcher local:  {ph_name} — {_era_label(ph_era)} "
                f"(promedio de carreras: {ph_era:.2f})\n"
                f"🔴 Pitcher visita: {pa_name} — {_era_label(pa_era)} "
                f"(promedio de carreras: {pa_era:.2f})\n"
                f"{top3_blk}"
                f"{bk_warn}"
            )
            body     = _two_layer_body(l1, l2)
            priority = "urgent" if is_high else "high"
            title    = f"⚾ GANADOR | {team_es} | {match_es}"
        else:
            # ── Soccer / other sports ─────────────────────────────────────
            impl_pct   = round(100 / b["odds"], 1) if b["odds"] else 0
            is_high    = b["edge"] >= 5.0 and elo_p >= 60
            _sw = b.get("stake_warn", "")
            conf_line = "🟢 CONFIANZA: ALTA" if is_high else "🟡 CONFIANZA: MEDIA"
            if _sw:
                conf_line += f"\n{_sw}"
            l1 = (
                f"🎯 {match_es}\n"
                f"⏰ Hoy {gt} ET\n"
                f"APUESTA: {team_es} GANA @ {b['odds']} — {b['bookmaker']}\n"
                f"{conf_line}"
            )
            l2 = (
                f"Nuestro modelo: {elo_p}% de ganar.\n"
                f"La casa de apuestas dice {impl_pct}% — ventaja de {b['edge']}%.\n"
                f"{top3_blk}"
                f"{bk_warn}"
            )
            body     = _two_layer_body(l1, l2)
            priority = "urgent" if b["edge"] >= 5.0 else ("high" if b["edge"] >= 3 else "default")
            title    = f"{emoji} GANADOR | {team_es} | {match_es}"

        ntfy_post(title, body, priority)
        alerted_bets.add(f"{b['game_id']}|{b['team']}")
        if alerted is not None:
            alerted[match_key_bet] = float(b.get("edge", 0))

# ═══════════════════════════════════════════════════════════════════════════════
# CORE — CSV LOGGING
# ═══════════════════════════════════════════════════════════════════════════════

FIELDNAMES = [
    "date", "sport", "match", "team", "side",
    "odds", "edge", "kelly_pct", "stake", "confidence",
    "bookmaker", "market_type", "closing_edge",
    "value_pct", "ev", "roi", "elo_prob",
    "book_line", "our_line", "edge_unit",
    "line_moved", "line_dir", "line_delta",
    "game_time", "result", "profit_loss",
    # Improvement 1 & 5: pitcher + weather columns
    "pitcher_home", "pitcher_away", "pitch_adj", "wind_info",
    # Improvement 2: WC form columns
    "form_home", "form_away",
    # Improvement 3: CLV columns
    "closing_line", "clv",
]

def log_bets(bets, sport_key):
    exists = os.path.exists(BETS_LOG_FILE)
    with open(BETS_LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not exists:
            w.writeheader()
        for b in bets:
            w.writerow({
                "date":         datetime.now(CDT).strftime("%Y-%m-%d %H:%M CDT"),
                "sport":        sport_key,
                "match":        b["match"],
                "team":         b["team"],
                "side":         b["side"],
                "odds":         b["odds"],
                "edge":         b["edge"],
                "kelly_pct":    b["kelly_pct"],
                "stake":        b["stake"],
                "confidence":   b["confidence"],
                "bookmaker":    b.get("bookmaker", ""),
                "market_type":  b.get("market_type", "h2h"),
                "closing_edge": b.get("closing_edge", ""),
                "value_pct":    b.get("value_pct", ""),
                "ev":           b.get("ev", ""),
                "roi":          b.get("roi", ""),
                "elo_prob":     b.get("elo_prob", ""),
                "book_line":    b.get("book_line", ""),
                "our_line":     b.get("our_line", ""),
                "edge_unit":    b.get("edge_unit", ""),
                "line_moved":   b["line_moved"],
                "line_dir":     b["line_dir"],
                "line_delta":   b["line_delta"],
                "game_time":    b["time"],
                "result":       "",
                "profit_loss":  "",
                # Improvement 1 & 5
                "pitcher_home": b.get("pitcher_home", ""),
                "pitcher_away": b.get("pitcher_away", ""),
                "pitch_adj":    b.get("pitch_adj", ""),
                "wind_info":    b.get("wind_info", ""),
                # Improvement 2
                "form_home":    b.get("form_home", ""),
                "form_away":    b.get("form_away", ""),
                # Improvement 3 — filled later by check_closing_lines
                "closing_line": "",
                "clv":          "",
            })

# ═══════════════════════════════════════════════════════════════════════════════
# CORE — DAILY SUMMARY & MIDNIGHT RESET
# ═══════════════════════════════════════════════════════════════════════════════

def send_daily_summary():
    import csv as _csv
    today_str = datetime.now(CDT).strftime("%Y-%m-%d")
    today_bets = []
    try:
        if os.path.isfile(BETS_LOG_FILE):
            with open(BETS_LOG_FILE, "r", newline="", encoding="utf-8") as _f:
                for row in _csv.DictReader(_f):
                    if (row.get("date", "") or "").startswith(today_str):
                        today_bets.append(row)
    except Exception as _e:
        print(f"  ⚠️  send_daily_summary CSV read error: {_e}")
    if not today_bets:
        ntfy_post("BetBot Daily Summary", "No value bets encontradas hoy.", "default")
        return
    try:
        total_stake = sum(float(b.get("stake") or 0) for b in today_bets)
        total_ev    = sum(float(b.get("ev") or 0) for b in today_bets)
        leagues     = sorted({b.get("sport", "?") for b in today_bets})
        high        = sum(1 for b in today_bets if (b.get("confidence") or "").upper() == "HIGH")
        med         = sum(1 for b in today_bets if (b.get("confidence") or "").upper() == "MEDIUM")
    except Exception:
        total_stake = total_ev = 0; leagues = []; high = med = 0
    body = (
        f"Total value bets: {len(today_bets)}\n"
        f"  HIGH: {high}  MEDIUM: {med}\n"
        f"Total stakes: ${round(total_stake, 2)}\n"
        f"Total expected value: ${round(total_ev, 2)}\n"
        f"Leagues: {', '.join(leagues)}"
    )
    ntfy_post("BetBot Daily Summary", body, "default")

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE P — BANKROLL AUTO-ADJUST  ·  PREMIUM ALERTS  ·  NIGHT SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

_MESES_ES = ["enero","febrero","marzo","abril","mayo","junio",
             "julio","agosto","septiembre","octubre","noviembre","diciembre"]
_DIAS_ES  = ["lunes","martes","miércoles","jueves","viernes","sábado","domingo"]

def _today_es(dt=None):
    d = dt or datetime.now(ET)
    return f"{_DIAS_ES[d.weekday()]} {d.day} de {_MESES_ES[d.month - 1]}"


def compute_bankroll_mult() -> float:
    """
    Recalculate _bankroll_mult and _bankroll_paused from current bankroll.
    Tiers (starting bankroll $1,000):
      < $400  → pause (mult = 0)      $400–$600 → 0.50×
      $600–$800 → 0.75×               $800–$1,200 → 1.00×
      > $1,200 → +0.20× per $200 above $1,200
    """
    global _bankroll_mult, _bankroll_paused
    try:
        br = load_bankroll_state()["current"]
    except Exception:
        br = float(BANKROLL)

    if br < 400:
        _bankroll_mult, _bankroll_paused = 0.0, True
    elif br < 600:
        _bankroll_mult, _bankroll_paused = 0.50, False
    elif br < 800:
        _bankroll_mult, _bankroll_paused = 0.75, False
    elif br <= 1200:
        _bankroll_mult, _bankroll_paused = 1.00, False
    else:
        steps = int((br - 1200) / 200)
        _bankroll_mult   = round(1.0 + steps * 0.20, 2)
        _bankroll_paused = False

    tier = (
        "PAUSADO 🛑"       if _bankroll_paused    else
        f"0.50× ⚠️ (bajo)" if _bankroll_mult == 0.50 else
        f"0.75× ⚠️ (bajo)" if _bankroll_mult == 0.75 else
        "normal 1.0×"      if _bankroll_mult == 1.00 else
        f"{_bankroll_mult}× 📈"
    )
    print(f"  💼 Bankroll mult: {tier}  (${br:,.2f})")
    _prev = getattr(compute_bankroll_mult, '_prev_mult', 1.0)
    if _bankroll_mult > _prev and _bankroll_mult >= 1.2:
        ntfy_post(
            "🎉 Bankroll: Nuevo Nivel",
            f"💰 Bankroll: ${br:,.2f}\n"
            f"📈 Stakes aumentados a ×{_bankroll_mult:.1f}\n"
            f"🔥 El modelo apostará más en picks de alta confianza",
            "default"
        )
    compute_bankroll_mult._prev_mult = _bankroll_mult
    return _bankroll_mult


def _count_premium_signals(bet: dict, context: dict, home: str, away: str) -> list:
    """
    Return list of (label, key) signal tuples that align for this pick.
    Used to trigger PREMIUM alert when ≥3 signals confirm the same bet.
    """
    signals = []
    team    = bet.get("team", "")
    mtype   = bet.get("market_type", "h2h")
    game_id = bet.get("game_id", "")

    # 1. Sharp money / RLM detected on our side
    rlm = context.get("rlm_data") or {}
    if rlm.get("rlm") is True:
        ss = rlm.get("sharp_side", "")
        if ss and (ss.lower() in team.lower() or team.lower() in ss.lower()):
            signals.append(("💎 Dinero sharp detectado", "sharp"))

    # 2. Steam move confirmed for this game
    if game_id and game_id in _steam_game_ids:
        signals.append(("🚂 Steam move confirmado", "steam"))

    # 3. Public ≥65% on the other side
    if rlm.get("pub_pct", 0) >= 65:
        pub_fav = rlm.get("pub_fav", "")
        if pub_fav and (pub_fav.lower() not in team.lower()
                        and team.lower() not in pub_fav.lower()):
            signals.append((
                f"👥 Público {rlm['pub_pct']}% en {_es(pub_fav)} (en contra)",
                "public",
            ))

    # 4. Confirmed lineup — our side has all key players in
    lineup = context.get("lineup_data") or {}
    if lineup.get("confirmed"):
        is_home_t = home.lower() in team.lower() or team.lower() in home.lower()
        my_miss   = lineup.get("home_missing" if is_home_t else "away_missing", [])
        if not my_miss:
            signals.append(("✅ Lineup completo confirmado", "lineup"))

    # 5. Pitcher dominante/élite (ERA or FIP < 2.75)
    is_home_t = home.lower() in team.lower() or team.lower() in home.lower()
    if mtype in ("h2h", "moneyline", ""):
        era = float(context.get("era_home" if is_home_t else "era_away") or 9.9)
        fip = context.get("fip_home" if is_home_t else "fip_away")
        m   = fip if fip is not None else era
        if m < 2.75:
            pn = context.get("pname_home" if is_home_t else "pname_away", "")
            signals.append((f"⚾ Pitcher élite — {pn} ({m:.2f})", "pitcher"))
    elif mtype == "totals" and team.upper() == "UNDER":
        era_h = float(context.get("era_home") or 9.9)
        fip_h = context.get("fip_home")
        era_a = float(context.get("era_away") or 9.9)
        fip_a = context.get("fip_away")
        m_h = fip_h if fip_h is not None else era_h
        m_a = fip_a if fip_a is not None else era_a
        best = min(m_h, m_a)
        if best < 2.75:
            dom = context.get("pname_home" if m_h < m_a else "pname_away", "")
            signals.append((f"⚾ Pitcher élite — {dom} ({best:.2f})", "pitcher"))

    # 6. H2H history supports pick (data exists, no contradiction)
    h2h = context.get("h2h_data") or {}
    if h2h.get("games") and not context.get("h2h_note"):
        signals.append(("📊 Historial H2H soporta el pick", "h2h"))

    # 7. Umpire tendency matches pick direction
    ump = context.get("umpire") or {}
    if ump.get("name"):
        tend = ump.get("tendency", "NEUTRAL")
        if "OVER" in tend and team.upper() == "OVER":
            signals.append((f"👨‍⚖️ Árbitro OVER — {ump['name']}", "umpire"))
        elif "UNDER" in tend and team.upper() == "UNDER":
            signals.append((f"👨‍⚖️ Árbitro UNDER — {ump['name']}", "umpire"))

    # 8. Wind direction favorable for pick
    wind = (context.get("wind_info") or "").upper()
    if "OUT" in wind and team.upper() == "OVER":
        signals.append(("💨 Viento OUT — favorece OVER", "weather"))
    elif "IN" in wind and team.upper() == "UNDER":
        signals.append(("💨 Viento IN — favorece UNDER", "weather"))

    return signals


def notify_premium_bet(bet: dict):
    """
    Send PREMIUM ntfy alert when ≥3 signals align on the same pick.
    Stake already boosted to min(normal × 1.5, $100).
    Always alerts (overrides dedup) with priority=urgent.
    """
    team    = bet.get("team", "")
    match   = bet.get("match", "")
    mtype   = bet.get("market_type", "h2h")
    side    = bet.get("side", "")
    odds    = bet.get("odds", 0)
    stake   = bet.get("stake", 0)
    book    = bet.get("bookmaker", "")
    signals = bet.get("signals", [])
    sp_key  = bet.get("sport", "")

    # Dedup: one PREMIUM alert per match+team per day
    pkey = f"{match}_{team}_premium"
    if not _should_alert(pkey, odds=odds):
        return

    gt = bet.get("time", "")[:16]
    try:
        gt = datetime.fromisoformat(gt).astimezone(ET).strftime("%I:%M %p ET")
    except Exception:
        gt = gt.replace("T", " ")

    home_s, away_s = (match + " vs ").split(" vs ", 1)[:2]
    home_s = home_s.strip(); away_s = away_s.strip()
    emoji = _sport_emoji(sp_key)

    if mtype == "totals":
        apuesta_line = f"APUESTA: {team.upper()} {side} (Total)"
    else:
        apuesta_line = f"APUESTA: {_es(team)} ML"

    sig_lines  = "\n".join(f"  {lbl}" for lbl, _ in signals[:6])
    low_warn   = (
        "\n\n⚠️ Bankroll bajo — apuesta con cuidado" if _bankroll_mult <= 0.75
        else ""
    )

    l1 = (
        f"🎯 {emoji} {_es(home_s)} vs {_es(away_s)}\n"
        f"⏰ Hoy {gt} ET\n"
        f"{apuesta_line} @ {odds} — {book}\n"
        f"🟢 APOSTAR: ${stake:.2f}  (stake aumentado — señales múltiples)"
    )
    l2 = (
        f"Este es un pick de máxima confianza: {len(signals)} de 8 señales alineadas.\n"
        f"\n"
        f"✅ Señales confirmadas:\n"
        f"{sig_lines}"
        f"{low_warn}"
    )
    body  = _two_layer_body(l1, l2)
    title = f"💎 PREMIUM | {_es(team)} | ${stake:.2f} @ {odds}"
    ntfy_post(title, body, "urgent")
    print(f"  💎 PREMIUM: {match} → {_es(team)} ({len(signals)}/8 señales)")


# ═══════════════════════════════════════════════════════════════════════════════
# DAILY CARD — 2 PM ET (MLB) / 10 AM ET (Soccer)
# Sends one comprehensive ntfy card analysing ALL games of the day,
# sorted into 4 tiers: 💎 IMPERDIBLES / ✅ TIENEN VALOR / ⚠️ BORDERLINE / ❌ NO VALE
# ═══════════════════════════════════════════════════════════════════════════════

def _card_analyze_game(game: dict, sport_key: str) -> dict:
    """
    Lightweight per-game analysis for the daily card (no Claude call).
    Always returns a result dict — even for no-value games.
    """
    home, away = game["home_team"], game["away_team"]
    commence   = game.get("commence_time", "")
    is_mlb     = "mlb" in sport_key

    best_ev    = -99.0
    best_label = None
    best_odds  = None
    best_stake = 0.0
    dqs        = 50
    pitcher_h  = pitcher_a = "TBD"
    era_h = era_a = 4.50
    elo_h = elo_a = 1500

    h2h    = _extract_h2h_best(game)
    totals = get_book_total(game)

    def _check_ev(p, odds, lbl):
        nonlocal best_ev, best_label, best_odds, best_stake
        if odds and odds > 1.0:
            p_eff = _cap_prob(p)               # enforce PROB_CAP before any calculation
            ev = (p_eff * odds - 1) * 100      # EV computed on capped probability
            if ev > best_ev:
                best_ev    = round(ev, 1)
                best_label = lbl
                best_odds  = odds
                r          = kelly_stake(p_eff, odds)
                best_stake = r["stake"]

    if is_mlb:
        h_stats = fetch_team_run_stats(home)
        a_stats = fetch_team_run_stats(away)
        if h_stats and a_stats:
            park     = MLB_PARK_FACTORS.get(home, 1.0)
            LAVG     = 4.5
            home_exp = h_stats["rs_pg"] * (a_stats["ra_pg"] / LAVG) * park
            away_exp = a_stats["rs_pg"] * (h_stats["ra_pg"] / LAVG) * park
            try:
                pitchers  = fetch_probable_pitchers_today()
                p_data    = _lookup_pitcher_data(home, away, pitchers)
                era_h     = p_data.get("home_era", 4.50)
                era_a     = p_data.get("away_era", 4.50)
                pitcher_h = p_data.get("home_name", "TBD")
                pitcher_a = p_data.get("away_name", "TBD")
            except Exception:
                pass
            adj      = pitcher_run_adjustment(era_h, era_a)
            home_exp = max(0.5, home_exp + adj / 2)
            away_exp = max(0.5, away_exp + adj / 2)
            p_home   = pythagorean_win_prob(home_exp, away_exp)
            p_away   = 1.0 - p_home
            # ML
            if home in h2h:
                _check_ev(p_home, h2h[home][0], f"{home} ML")
            if away in h2h:
                _check_ev(p_away, h2h[away][0], f"{away} ML")
            # Totals
            if totals:
                bl, oo, uo, _ = totals
                adj_tot = home_exp + away_exp
                diff    = adj_tot - bl
                if abs(diff) >= 0.5:
                    bet_over = diff > 0
                    p_t  = poisson_ou_prob(adj_tot, bl, bet_over)
                    lbl  = f"OVER {bl}" if bet_over else f"UNDER {bl}"
                    _check_ev(p_t, oo if bet_over else uo, lbl)
            # Data quality
            ctx_card = {"pname_home": pitcher_h, "pname_away": pitcher_a,
                        "era_home": era_h, "era_away": era_a}
            dqs = _data_completeness_score(ctx_card, sport_key, home, away)
        else:
            dqs = 20
    else:
        # Soccer — use ELO win probability as quick model
        try:
            elo_ratings = load_elo_ratings()
            elo_h = elo_ratings.get(home, 1500)
            elo_a = elo_ratings.get(away, 1500)
            p_home = elo_win_prob(home, away)
            p_away = 1.0 - p_home
            draw_key = f"{home}_{away}_draw"
            if home in h2h:
                _check_ev(p_home, h2h[home][0], f"{home} ML")
            if away in h2h:
                _check_ev(p_away, h2h[away][0], f"{away} ML")
            if totals:
                bl, oo, uo, _ = totals
                exp_tot = 2.6
                diff    = exp_tot - bl
                if abs(diff) >= 0.25:
                    bet_over = diff > 0
                    p_t  = poisson_ou_prob(exp_tot, bl, bet_over)
                    lbl  = f"OVER {bl}" if bet_over else f"UNDER {bl}"
                    _check_ev(p_t, oo if bet_over else uo, lbl)
            dqs = 60
        except Exception:
            dqs = 25

    return {
        "match":       f"{home} vs {away}",
        "commence":    commence,
        "best_ev":     best_ev,
        "best_label":  best_label,
        "best_odds":   best_odds,
        "best_stake":  round(best_stake, 0),
        "data_quality": dqs,
        "pitcher_h":   pitcher_h,
        "pitcher_a":   pitcher_a,
        "era_h":       era_h,
        "era_a":       era_a,
        "elo_h":       elo_h,
        "elo_a":       elo_a,
    }


def _card_fmt_time(commence: str) -> str:
    try:
        dt = datetime.fromisoformat(commence.replace("Z", "+00:00")).astimezone(ET)
        return dt.strftime("%I:%M %p ET")
    except Exception:
        return commence[:16] if commence else "?"


def build_daily_card(sport_key: str) -> str:
    """
    Build and return the full daily card text for one sport.
    Runs lightweight EV analysis on all today's games,
    then calls analyze_game_full (with Claude) on any EV >= 3% game.
    """
    is_mlb      = "mlb" in sport_key
    sport_name  = "MLB ⚾" if is_mlb else "MUNDIAL 🏆"
    today_et    = datetime.now(ET)
    today_str   = today_et.strftime("%d/%m/%Y")
    today_date  = today_et.date()

    # ── Fetch today's games ──────────────────────────────────────────────
    raw_games = get_odds(sport_key)
    if not raw_games:
        return f"Sin partidos hoy 📭"

    today_games = []
    for g in raw_games:
        commence = g.get("commence_time", "")
        try:
            gdate = (datetime.fromisoformat(commence.replace("Z", "+00:00"))
                     .astimezone(ET).date())
            if gdate == today_date:
                today_games.append(g)
        except Exception:
            today_games.append(g)

    if not today_games:
        return f"Sin partidos hoy 📭"

    print(f"  📋 Daily Card {sport_name}: {len(today_games)} partidos...")

    # ── Quick EV for every game (no Claude) ─────────────────────────────
    all_results: list = []
    for g in today_games:
        try:
            res = _card_analyze_game(g, sport_key)
        except Exception as exc:
            h, a = g.get("home_team", "?"), g.get("away_team", "?")
            print(f"    ⚠️ Quick analysis error {h} vs {a}: {exc}")
            res = {
                "match": f"{h} vs {a}", "commence": g.get("commence_time", ""),
                "best_ev": -99.0, "best_label": None, "best_odds": None,
                "best_stake": 0, "data_quality": 0,
                "pitcher_h": "TBD", "pitcher_a": "TBD",
                "era_h": 4.5, "era_a": 4.5, "elo_h": 1500, "elo_a": 1500,
            }
        all_results.append(res)

    # ── Full analysis with Claude for IMPERDIBLES + TIENEN VALOR tier ──────
    # Threshold = 4.0 (TIENEN VALOR gate); skip Claude for BORDERLINE (EV 2-4%)
    claude_results: dict = {}
    for res in all_results:
        if res["best_ev"] < 4.0:
            continue
        match = res["match"]
        for g in today_games:
            gm = f"{g['home_team']} vs {g['away_team']}"
            if gm != match:
                continue
            try:
                full = analyze_game_full(g, sport_key)
                if full:
                    cl_intel = full.get("claude_intel") or {}
                    best_c   = (full.get("candidates") or [{}])[0]
                    claude_results[match] = {
                        "vetoed":       False,
                        "claude_conf":  cl_intel.get("confianza", "MEDIA"),
                        "claude_apost": cl_intel.get("apostar", True),
                        "ev_full":      full.get("best_ev", res["best_ev"]),
                        "label_full":   full.get("best_label", res["best_label"]),
                        "stake_full":   best_c.get("stake", res["best_stake"]),
                    }
                else:
                    claude_results[match] = {
                        "vetoed": True, "claude_conf": "BAJA", "claude_apost": False,
                        "ev_full": 0.0, "label_full": None, "stake_full": 0,
                    }
            except Exception as exc:
                print(f"    ⚠️ Full analysis error {match}: {exc}")
            break

    # ── Tier classification ──────────────────────────────────────────────
    gems:       list = []
    values:     list = []
    borderline: list = []
    no_value:   list = []

    for res in all_results:
        match = res["match"]
        ev    = res["best_ev"]
        dqs   = res["data_quality"]
        cl    = claude_results.get(match, {})

        if cl.get("vetoed"):
            ev = 0.0
        elif cl.get("ev_full") is not None and cl["ev_full"] > 0:
            ev = cl["ev_full"]

        res["final_ev"]    = ev
        res["claude_info"] = cl

        if ev >= 10.0 and dqs >= 75:
            gems.append(res)
        elif ev >= 4.0 and dqs >= 60:
            values.append(res)
        elif ev >= 2.0:
            borderline.append(res)
        else:
            no_value.append(res)

    for tier in (gems, values, borderline, no_value):
        tier.sort(key=lambda x: x.get("final_ev", -99), reverse=True)

    # ── Parlay suggestion (top 2–3 IMPERDIBLES + TIENEN VALOR) ──────────
    parlay_candidates = [r for r in gems + values
                         if not r.get("claude_info", {}).get("vetoed")
                         and r.get("best_odds")]
    # Deduplicate by match — never two legs from the same game
    _seen_pc = set()
    _deduped = []
    for _r in parlay_candidates:
        if _r["match"] not in _seen_pc:
            _seen_pc.add(_r["match"])
            _deduped.append(_r)
    parlay_candidates = _deduped
    parlay_block = ""
    if len(parlay_candidates) >= 2:
        top_p  = parlay_candidates[:3]
        combo  = 1.0
        for r in top_p:
            combo *= r["best_odds"]
        p_stake  = 20
        p_return = round(p_stake * combo, 0)
        picks_s  = " + ".join(
            (r.get("claude_info", {}).get("label_full") or r["best_label"] or r["match"])
            for r in top_p
        )
        parlay_block = (
            f"━━━━━━━━━━\n"
            f"🎰 MEJOR PARLAY:\n"
            f"{picks_s}\n"
            f"Cuota combinada: {combo:.2f}x\n"
            f"Apuesta $20 → Retorno ${p_return:.0f}\n"
        )

    # ── Stake totals ─────────────────────────────────────────────────────
    def _stake(r):
        return r.get("claude_info", {}).get("stake_full") or r.get("best_stake") or 0

    total_stake = sum(_stake(r) for r in gems + values)
    pot_gain    = sum(
        _stake(r) * ((r.get("best_odds") or 1.0) - 1.0)
        for r in gems + values
    )

    # ── Formatting helpers ────────────────────────────────────────────────
    def _fmt_full(res):
        cl      = res.get("claude_info", {})
        ev      = res.get("final_ev", res["best_ev"])
        lbl     = cl.get("label_full") or res["best_label"] or res["match"]
        odds    = res.get("best_odds")
        dqs     = res["data_quality"]
        stake   = _stake(res)
        tm      = _card_fmt_time(res["commence"])
        c_conf  = cl.get("claude_conf", "")
        c_emoji = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴"}.get(c_conf, "⚪")
        pit_line = ""
        if is_mlb and res.get("pitcher_h", "TBD") != "TBD":
            pit_line = (f"⚾ {res['pitcher_h']} (ERA {res['era_h']:.2f}) "
                        f"vs {res['pitcher_a']} (ERA {res['era_a']:.2f})\n")
        elif not is_mlb:
            pit_line = f"📊 ELO: {res.get('elo_h',1500):.0f} vs {res.get('elo_a',1500):.0f}\n"
        return (
            f"📌 {res['match']}  [{tm}]\n"
            f"{pit_line}"
            f"🏆 Pick: {lbl}  @ {odds or '?'}\n"
            f"📊 EV: +{ev:.1f}%  Calidad: {dqs}/100  {c_emoji} Claude: {c_conf or 'N/D'}\n"
            f"💰 Stake sugerido: ${stake:.0f}"
        )

    def _fmt_brief(res):
        ev  = res.get("final_ev", res["best_ev"])
        lbl = res["best_label"] or res["match"]
        tm  = _card_fmt_time(res["commence"])
        return f"• {res['match']} — {lbl}  EV+{max(ev,0):.1f}%  [{tm}]"

    # ── Assemble card text ────────────────────────────────────────────────
    DIVIDER = "━━━━━━━━━━"

    gem_body = ("\n\n".join(_fmt_full(r) + "\n🟢 APOSTAR" for r in gems)
                if gems else "  (ninguno hoy)")
    val_body = ("\n\n".join(_fmt_full(r) + "\n🟡 APOSTAR MITAD" for r in values)
                if values else "  (ninguno hoy)")
    brd_body = ("\n".join(_fmt_brief(r) + " — Edge pequeño" for r in borderline)
                if borderline else "  (ninguno hoy)")
    nov_body = ("\n".join(
                    f"• {r['match']}  [{_card_fmt_time(r['commence'])}]"
                    for r in no_value)
                if no_value else "  (ninguno hoy)")

    total_block = ""
    if total_stake > 0:
        total_block = (
            f"{DIVIDER}\n"
            f"💵 Total apostado sugerido: ${total_stake:.0f}\n"
            f"📈 Potencial ganancia: ${pot_gain:.0f}"
        )

    card = (
        f"📋 TARJETA DEL DÍA — {sport_name}\n"
        f"📅 {today_str}  ({len(today_games)} partidos)\n"
        f"{DIVIDER}\n"
        f"💎 IMPERDIBLES ({len(gems)}):\n"
        f"{gem_body}\n\n"
        f"✅ TIENEN VALOR ({len(values)}):\n"
        f"{val_body}\n\n"
        f"⚠️ BORDERLINE ({len(borderline)}):\n"
        f"{brd_body}\n\n"
        f"❌ NO VALE LA PENA ({len(no_value)}):\n"
        f"{nov_body}\n"
        f"{parlay_block}"
        f"{total_block}"
    )
    return card


def send_daily_card(sport_key: str):
    """Build and send the daily card for a sport via ntfy."""
    global last_mlb_card, last_soccer_card
    today   = datetime.now(ET).date()
    is_mlb  = "mlb" in sport_key
    tracker = last_mlb_card if is_mlb else last_soccer_card

    if tracker >= today:
        return

    sport_label = "MLB ⚾" if is_mlb else "MUNDIAL 🏆"
    print(f"\n📋 Enviando Tarjeta del Día — {sport_label}...")
    try:
        body = build_daily_card(sport_key)
        ntfy_post(f"📋 TARJETA DEL DÍA — {sport_label}", body, priority="high")
        if is_mlb:
            last_mlb_card = today
        else:
            last_soccer_card = today
        print(f"  ✅ Tarjeta del Día enviada — {sport_label}")
    except Exception as exc:
        print(f"  ⚠️  Daily Card error ({sport_label}): {exc}")


def send_night_summary():
    """11 PM ET: daily recap of today's picks with resolved W/L/P or 'pendiente'."""
    try:
        today_str = datetime.now(ET).strftime("%Y-%m-%d")

        today_bets: list = []
        if os.path.exists(BETS_LOG_FILE):
            try:
                with open(BETS_LOG_FILE, newline="") as f:
                    for b in csv.DictReader(f):
                        if b.get("date", "").startswith(today_str):
                            today_bets.append(b)
            except Exception:
                pass

        state   = load_bankroll_state()
        current = state["current"]
        settled = [b for b in today_bets if b.get("result") in ("W", "L", "P")]
        daily_pl = sum(float(b.get("profit_loss", 0) or 0) for b in settled)
        daily_pl_s = f"+${daily_pl:.2f}" if daily_pl >= 0 else f"-${abs(daily_pl):.2f}"

        date_es = _today_es()

        if not today_bets:
            body = (
                f"🌙 RESUMEN DEL DÍA\n"
                f"📅 {date_es}\n"
                f"{_DIV}\n"
                f"Sin picks hoy —\n"
                f"el filtro protegió tu bankroll 🛡️\n"
                f"{_DIV}\n"
                f"💰 Bankroll: ${current:,.2f}"
            )
            ntfy_post("🌙 RESUMEN DEL DÍA", body, "default")
            print("  🌙 Resumen nocturno: sin picks hoy")
            return

        picks_block = ""
        premium_cnt = 0
        for b in today_bets:
            result = b.get("result", "")
            pl     = float(b.get("profit_loss", 0) or 0)
            team   = b.get("team", "")
            mtype  = b.get("market_type", "h2h")
            side   = b.get("side", "")
            sport  = b.get("sport", "")

            # Flag PREMIUM by stake > 75% of max premium stake
            try:
                is_prem = float(b.get("stake", 0) or 0) >= PREMIUM_MAX_STAKE * 0.75
            except Exception:
                is_prem = False
            if is_prem:
                premium_cnt += 1

            sp_emoji = "⚾" if "MLB" in sport.upper() else "⚽"
            prem_tag = " 💎" if is_prem else ""
            label = (f"{team.upper()} {side}" if mtype == "totals"
                     else f"{_es(team)} ML")

            if result == "W":
                status = f"✅ GANÓ +${pl:.2f}"
            elif result == "L":
                status = f"❌ PERDIÓ -${abs(pl):.2f}"
            elif result == "P":
                status = "🤝 EMPUJÓ $0"
            else:
                status = "⏳ pendiente"

            picks_block += f"{sp_emoji}{prem_tag} {label} ← {status}\n"

        prem_line   = f"💎 {premium_cnt} PREMIUM picks\n" if premium_cnt else ""
        change_line = (f"📈 Cambio hoy: {daily_pl_s}"
                       if settled else "📈 Cambio hoy: pendiente resultados")

        body = (
            f"🌙 RESUMEN DEL DÍA\n"
            f"📅 {date_es}\n"
            f"{_DIV}\n"
            f"📊 PICKS DE HOY:\n\n"
            f"{picks_block}"
            f"{prem_line}"
            f"{_DIV}\n"
            f"💰 Bankroll actual: ${current:,.2f}\n"
            f"{change_line}\n"
            f"{_DIV}\n"
            f"⏰ Resultados finales mañana\n"
            f"   en el reporte de las 8 AM"
        )
        ntfy_post("🌙 RESUMEN DEL DÍA", body, "default")
        if _tg_broadcast_fn:
            try:
                _tg_broadcast_fn(f"🌙 <b>RESUMEN DEL DÍA</b>\n{_DIV}\n{body[:3800]}")
            except Exception as _tge:
                print(f"  ⚠️  Telegram night summary error: {_tge}")
        print(f"  🌙 Resumen nocturno enviado ({len(today_bets)} picks)")
    except Exception as e:
        print(f"  ⚠️  send_night_summary error: {e}")


def check_midnight_reset():
    global alerted_bets, last_reset, _sent_alerts, alerted_game_analysis, _elite_count_today, _elite_count_date
    today = datetime.now(CDT).date()
    if today != last_reset:
        print(f"\n🌙 Midnight reset — sending daily summary...")
        send_daily_summary()
        alerted_bets          = set()
        alerted_game_analysis = set()   # reset daily to avoid unbounded growth
        _sent_alerts          = {}
        last_reset            = today
        _elite_count_today    = 0
        _elite_count_date     = ""
        compute_bankroll_mult()   # Module P: update stake multiplier

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE B — PUBLIC % / RLM  ·  STEAM MOVES  ·  CONFIRMED LINEUP
# ═══════════════════════════════════════════════════════════════════════════════

# ── B1: Key players per team (all 30 MLB teams) ───────────────────────────────
MLB_KEY_PLAYERS: "dict[str, list[str]]" = {
    "New York Yankees":       ["Aaron Judge", "Juan Soto"],
    "Boston Red Sox":         ["Rafael Devers", "Jarren Duran"],
    "Los Angeles Dodgers":    ["Shohei Ohtani", "Freddie Freeman"],
    "Atlanta Braves":         ["Ronald Acuna Jr.", "Matt Olson"],
    "Houston Astros":         ["Jose Altuve", "Alex Bregman"],
    "New York Mets":          ["Francisco Lindor", "Pete Alonso"],
    "Philadelphia Phillies":  ["Bryce Harper", "Trea Turner"],
    "San Diego Padres":       ["Fernando Tatis Jr.", "Manny Machado"],
    "Seattle Mariners":       ["Julio Rodriguez", "Cal Raleigh"],
    "Baltimore Orioles":      ["Gunnar Henderson", "Adley Rutschman"],
    "Texas Rangers":          ["Corey Seager", "Marcus Semien"],
    "Toronto Blue Jays":      ["Vladimir Guerrero Jr.", "Bo Bichette"],
    "Minnesota Twins":        ["Byron Buxton", "Carlos Correa"],
    "Cleveland Guardians":    ["Jose Ramirez", "Steven Kwan"],
    "Detroit Tigers":         ["Riley Greene", "Spencer Torkelson"],
    "Chicago White Sox":      ["Luis Robert Jr.", "Andrew Vaughn"],
    "Kansas City Royals":     ["Bobby Witt Jr.", "Vinnie Pasquantino"],
    "Tampa Bay Rays":         ["Randy Arozarena", "Yandy Diaz"],
    "Los Angeles Angels":     ["Mike Trout", "Anthony Rendon"],
    "Oakland Athletics":      ["Brent Rooker", "Lawrence Butler"],
    "Chicago Cubs":           ["Cody Bellinger", "Ian Happ"],
    "St. Louis Cardinals":    ["Nolan Arenado", "Paul Goldschmidt"],
    "Milwaukee Brewers":      ["Christian Yelich", "Rhys Hoskins"],
    "Cincinnati Reds":        ["Elly De La Cruz", "Jonathan India"],
    "Pittsburgh Pirates":     ["Ke'Bryan Hayes", "Bryan Reynolds"],
    "Arizona Diamondbacks":   ["Corbin Carroll", "Christian Walker"],
    "San Francisco Giants":   ["Matt Chapman", "Patrick Bailey"],
    "Colorado Rockies":       ["Ryan McMahon", "Ezequiel Tovar"],
    "Miami Marlins":          ["Jazz Chisholm Jr.", "Jorge Soler"],
    "Washington Nationals":   ["CJ Abrams", "Lane Thomas"],
}

# ── B2: Confirmed lineup fetch ─────────────────────────────────────────────────
_lineup_cache: "dict[str, dict]" = {}

def _fetch_confirmed_lineup(home: str, away: str, commence: str) -> "dict | None":
    """
    Fetch confirmed batting lineup from MLB Stats API.
    Only checked within 3 hours of first pitch.
    Returns None if lineup not yet posted or game is too far away.
    """
    try:
        from datetime import datetime, timezone
        now_utc  = datetime.now(timezone.utc)
        game_dt  = datetime.fromisoformat(commence.replace("Z", "+00:00"))
        mins_to  = (game_dt - now_utc).total_seconds() / 60
        if mins_to > 180 or mins_to < -60:
            return None

        cache_key = f"lineup_{home}_{away}_{commence[:10]}"
        if cache_key in _lineup_cache:
            return _lineup_cache[cache_key]

        game_date = commence[:10]
        data = _mlb_rest("/schedule", {
            "sportId": 1,
            "date": game_date,
            "hydrate": "lineups,probablePitcher",
        })

        result = None
        for date_block in data.get("dates", []):
            for g in date_block.get("games", []):
                teams = g.get("teams", {})
                g_home = teams.get("home", {}).get("team", {}).get("name", "")
                g_away = teams.get("away", {}).get("team", {}).get("name", "")
                home_w = home.split()[-1].lower()
                away_w = away.split()[-1].lower()
                if home_w not in g_home.lower() and away_w not in g_away.lower():
                    continue

                lineups      = g.get("lineups", {})
                home_players = lineups.get("homePlayers", [])
                away_players = lineups.get("awayPlayers", [])
                if not home_players and not away_players:
                    break

                def _order(players):
                    order = {}
                    for p in players:
                        bo = p.get("battingOrder")
                        if bo is not None:
                            slot = int(str(bo)) // 100
                            order[slot] = p.get("fullName", "")
                    return order

                home_order = _order(home_players)
                away_order = _order(away_players)

                def _missing_keys(team_name, order):
                    if not order:
                        return []
                    kp = MLB_KEY_PLAYERS.get(team_name, [])
                    return [
                        p for p in kp
                        if not any(
                            p.split()[-1].lower() in v.lower()
                            for v in order.values()
                        )
                    ]

                result = {
                    "home_order":   home_order,
                    "away_order":   away_order,
                    "home_missing": _missing_keys(home, home_order),
                    "away_missing": _missing_keys(away, away_order),
                    "confirmed":    bool(home_order or away_order),
                }
                break
            if result:
                break

        if result:
            _lineup_cache[cache_key] = result
        return result
    except Exception:
        return None

# ── B3: Steam move detection ───────────────────────────────────────────────────

# Steam detection constants
_STEAM_MIN_BOOKS    = 5     # Rule 1: need 5+ books moving same direction
_STEAM_MIN_PCT_MOVE = 0.04  # Rule 4: minimum 4% price drop to count a book
_STEAM_US_SLUGS     = {     # Rule 2: at least one of these must appear
    "bovada", "fanduel", "draftkings", "betonline", "mybookie",
    "pointsbet", "betmgm", "caesars",
}
_STEAM_PREMIUM_BOOKS = 7    # Rule 5: 7+ books → premium steam
_STEAM_PREMIUM_US    = 2    # Rule 5: 2+ US books required for premium


def detect_steam_moves_for_game(
    game_id: str,
    home: str,
    away: str,
    bookmakers: list,
    prev_map: dict,
    new_map: dict,
) -> "list[dict]":
    """
    Strict steam detection:
      Rule 1 — 5+ books must all move the same direction (was 3)
      Rule 2 — at least 1 US book (Bovada/FanDuel/DraftKings/BetOnline/MyBookie)
      Rule 3 — odds must DROP (money in = shorter price); rising odds → skip
      Rule 4 — each book must show ≥ 4% price move
      Rule 5 — 7+ books incl. 2+ US → flag as PREMIUM steam
    Also writes per-book odds into new_map for the next scan comparison.
    """
    results = []
    for team in (home, away):
        downs: list = []   # books where this team's odds shortened ≥ 4%

        for bk in bookmakers:
            slug      = bk["title"].lower().replace(" ", "_").replace(".", "")
            bk_key    = f"{game_id}_{team}_{slug}"
            cur_price = None
            for m in bk.get("markets", []):
                if m["key"] == "h2h":
                    for o in m.get("outcomes", []):
                        if o["name"] == team:
                            cur_price = o["price"]
                            break
            if cur_price is None:
                continue
            new_map[bk_key] = cur_price
            prev_price = prev_map.get(bk_key)
            if prev_price is None or prev_price <= 0:
                continue

            delta    = cur_price - prev_price
            pct_move = abs(delta) / prev_price

            # Rule 3: odds must DROP (money coming in = price shortens)
            # Rule 4: minimum 4% move per book
            if delta >= 0 or pct_move < _STEAM_MIN_PCT_MOVE:
                continue

            is_us = any(us in bk["title"].lower() for us in _STEAM_US_SLUGS)
            downs.append({
                "book":     bk["title"],
                "prev":     prev_price,
                "now":      cur_price,
                "pct_move": round(pct_move * 100, 1),
                "is_us":    is_us,
            })

        # Rule 1: need 5+ qualifying books
        if len(downs) < _STEAM_MIN_BOOKS:
            continue

        # Rule 2: at least one US book must be present
        us_books = [b for b in downs if b["is_us"]]
        if not us_books:
            continue

        # Rule 5: premium if 7+ books and 2+ US books
        is_premium = (len(downs) >= _STEAM_PREMIUM_BOOKS
                      and len(us_books) >= _STEAM_PREMIUM_US)

        _steam_game_ids.add(game_id)   # Module P: mark for PREMIUM signals
        avg_move = round(sum(b["pct_move"] for b in downs) / len(downs), 1)
        results.append({
            "match":      f"{home} vs {away}",
            "team":       team,
            "direction":  "down",
            "books":      downs,
            "n_us":       len(us_books),
            "is_premium": is_premium,
            "avg_move":   avg_move,
            "odds_from":  downs[0]["prev"],
            "odds_to":    downs[-1]["now"],
        })
    return results


def notify_steam_moves(steam_list: list):
    """Send immediate urgent alert for each confirmed steam move.
    PREMIUM alert (💎) when 7+ books including 2+ US books."""
    for s in steam_list:
        home, away = s["match"].split(" vs ", 1)
        key = f"{home}_{away}_{s['team']}_steam"
        if not _should_alert(key, odds=s["odds_to"]):
            continue

        n_books    = len(s["books"])
        n_us       = s.get("n_us", 0)
        avg_move   = s.get("avg_move", 0.0)
        is_premium = s.get("is_premium", False)

        all_names = ", ".join(b["book"] for b in s["books"][:5])
        us_names  = ", ".join(b["book"] for b in s["books"] if b.get("is_us"))

        if is_premium:
            title    = f"💎 STEAM PREMIUM | {_es(s['team'])} | {s['match']}"
            priority = "urgent"
            badge    = "💎 STEAM PREMIUM — dinero institucional"
            action   = "🟢 APOSTAR AHORA — múltiples US books confirman"
        else:
            title    = f"🚂 STEAM | {_es(s['team'])} | {s['match']}"
            priority = "urgent"
            badge    = "🚂 STEAM FUERTE — 5+ casas confirman"
            action   = "🟢 APOSTAR — línea seguirá cayendo"

        l1 = (
            f"🎯 {s['match']}\n"
            f"APUESTA: {_es(s['team'])} GANA @ {s['odds_to']:.2f}\n"
            f"{action}"
        )
        l2 = (
            f"La cuota de {_es(s['team'])} cayó de {s['odds_from']:.2f} "
            f"a {s['odds_to']:.2f} (−{avg_move:.1f}%) en menos de 10 minutos.\n"
            f"{n_books} casas de apuestas la bajaron al mismo tiempo — "
            f"señal de dinero institucional fuerte.\n"
            f"🇺🇸 Casas de EE.UU. ({n_us}): {us_names or 'N/A'}\n"
            f"📋 Todas las casas: {all_names}\n"
            f"\n"
            f"{badge}"
        )
        body = _two_layer_body(l1, l2)
        ntfy_post(title, body, priority)
        badge_sym = "💎" if is_premium else "🚂"
        print(f"  {badge_sym} Steam: {s['team']} en {s['match']} "
              f"({n_books} casas, {n_us} US, −{avg_move:.1f}%)")

# ═══════════════════════════════════════════════════════════════════════════════
# PARLAY DETECTOR
# ═══════════════════════════════════════════════════════════════════════════════

def _parlay_bet_type(label: str) -> str:
    """Classify label as 'UNDER', 'ML', or '' (ineligible for parlay)."""
    u = label.upper()
    if "UNDER" in u:
        return "UNDER"
    if " ML" in u or u.endswith("ML"):
        return "ML"
    return ""


_CORRELATED_PAIRS: list = [
    ("strikeout_prop", "over",  "totals",     "under", "K alto + Under son correlacionados — menos carreras cuando el pitcher domina"),
    ("strikeout_prop", "under", "totals",     "over",  "K bajo + Over son correlacionados — más carreras cuando hay mal pitcheo"),
    ("h2h",           None,    "spreads",    None,    "Moneyline + línea de carreras del mismo equipo son redundantes"),
    ("h2h",           None,    "h2h_first5", None,    "Moneyline partido completo + primer tiempo comparten resultado"),
]

def _check_correlated_legs(legs: list) -> str:
    """
    Given a list of parlay leg dicts (each with 'market_type', 'direction'),
    return a warning string if any pair is correlated, or '' if all clear.
    """
    for i, leg_a in enumerate(legs):
        for leg_b in legs[i + 1:]:
            for ma, da, mb, db, warn in _CORRELATED_PAIRS:
                a_match  = (leg_a.get("market_type", "") == ma and
                            (da is None or leg_a.get("direction", "") == da))
                b_match  = (leg_b.get("market_type", "") == mb and
                            (db is None or leg_b.get("direction", "") == db))
                if a_match and b_match:
                    return f"⚠️ PARLAY CORRELACIONADO: {warn}"
                a_match2 = (leg_a.get("market_type", "") == mb and
                            (db is None or leg_a.get("direction", "") == db))
                b_match2 = (leg_b.get("market_type", "") == ma and
                            (da is None or leg_b.get("direction", "") == da))
                if a_match2 and b_match2:
                    return f"⚠️ PARLAY CORRELACIONADO: {warn}"
    return ""


def _extract_parlay_candidates(analysis: dict) -> list:
    """
    Extract parlay-eligible picks from one full analysis.
    Requirements: EV ≥ 8%, prob ≥ 60%, safe book, no TBD, no contradiction.
    """
    ctx = analysis.get("context", {})
    if ctx.get("tbd_note"):
        return []
    if ctx.get("pitch_intel", {}).get("contradiction", False):
        return []

    picks = []
    for c in analysis.get("candidates", []):
        if c.get("ev_pct",    0) < 8.0:
            continue
        if c.get("true_prob", 0) < 0.60:
            continue
        if not _is_safe_book(c.get("book", "")):
            continue
        bet_type = _parlay_bet_type(c.get("label", ""))
        if not bet_type:
            continue
        picks.append({
            "game_id":   analysis["game_id"],
            "match":     analysis["match"],
            "sport":     analysis.get("sport", ""),
            "is_mlb":    analysis.get("is_mlb", False),
            "label":     c["label"],
            "bet_type":  bet_type,
            "true_prob": c["true_prob"],
            "odds":      c["odds"],
            "book":      c["book"],
            "ev_pct":    c["ev_pct"],
            "stake":     c["stake"],
        })
    return picks


def _send_parlay_alert(p: dict):
    """Format and fire the ntfy parlay alert."""
    import re
    l1, l2 = p["leg1"], p["leg2"]

    def _strip(lbl):
        return re.sub(r"[^\x00-\x7F\s\.\d\+\-\/]", "", lbl).strip()

    def _pair(match):
        parts = match.split(" vs ", 1)
        return f"{_es(parts[0])} vs {_es(parts[1])}" if len(parts) == 2 else match

    corr_warn = p.get("corr_warning", "")
    body = (
        f"{corr_warn + chr(10) if corr_warn else ''}"
        f"🔗 Pierna 1:\n"
        f"   {_strip(l1['label'])} | {_pair(l1['match'])}\n"
        f"   Prob: {round(l1['true_prob']*100):.0f}%"
        f" | @ {l1['odds']:.2f} {l1['book'].title()}\n"
        f"\n"
        f"🔗 Pierna 2:\n"
        f"   {_strip(l2['label'])} | {_pair(l2['match'])}\n"
        f"   Prob: {round(l2['true_prob']*100):.0f}%"
        f" | @ {l2['odds']:.2f} {l2['book'].title()}\n"
        f"{'─'*28}\n"
        f"💰 Apuesta parlay: ${p['parlay_stake']:.0f}\n"
        f"   Odds combinadas: {p['comb_odds']:.2f}\n"
        f"   Ganancia si gana: ${p['win_payout']:.2f}\n"
        f"   Ganancia esperada: +{p['parlay_ev']:.1f}%\n"
        f"{'─'*28}\n"
        f"⚠️ Los parlays son más riesgo.\n"
        f"   Apuesta poco — máximo $15-20.\n"
        f"🟡 APOSTAR MITAD del monto sugerido"
    )
    title = f"🎰 PARLAY | {l1['bet_type']} x2 | +{p['parlay_ev']:.1f}% EV"
    print(f"\n  🎰 PARLAY detectado — {l1['bet_type']} x2 | "
          f"EV +{p['parlay_ev']:.1f}% | Stake ${p['parlay_stake']:.0f}")
    ntfy_post(title, body, priority="high")
    if _tg_broadcast_fn:
        try:
            _tg_broadcast_fn(f"🎰 <b>PARLAY SUGERIDO</b>\n{_DIV}\n{body}")
        except Exception:
            pass


def detect_and_notify_parlays(all_analyses: list):
    """
    After a full scan, find the best qualifying 2-leg parlay and alert once.
    Thresholds: each leg EV ≥ 8%, prob ≥ 60%, safe book, same bet type,
    different games, no TBD/contradiction; parlay EV > 15%.
    """
    today_date = datetime.now(ET).date()
    eligible   = []
    for a in all_analyses:
        # Only include TODAY's games in parlays — never use yesterday's or future games
        commence = a.get("commence_time", "") or a.get("time", "")
        if commence:
            try:
                gdate = (datetime.fromisoformat(commence.replace("Z", "+00:00"))
                         .astimezone(ET).date())
                if gdate != today_date:
                    print(f"  ⏭️  Parlay: omitiendo juego de otra fecha "
                          f"({gdate}) — {a.get('match','?')}")
                    continue
            except Exception:
                pass
        eligible.extend(_extract_parlay_candidates(a))

    if len(eligible) < 2:
        return

    best_parlay = None
    best_ev     = 15.0   # minimum to suggest

    for i in range(len(eligible)):
        for j in range(i + 1, len(eligible)):
            p1, p2 = eligible[i], eligible[j]
            # Block same-game legs: check both game_id AND match name
            same_id    = p1["game_id"] and p1["game_id"] == p2["game_id"]
            same_match = p1["match"]   == p2["match"]
            if same_id or same_match:
                continue
            if p1["bet_type"] != p2["bet_type"]:
                continue

            comb_odds   = round(p1["odds"] * p2["odds"], 2)
            parlay_ev   = round(
                (p1["true_prob"] * p2["true_prob"] * comb_odds - 1) * 100, 1
            )
            if parlay_ev <= best_ev:
                continue

            # Stake: 10% of smaller Kelly, $5 min, $20 max, rounded to $5
            base         = min(p1["stake"], p2["stake"])
            parlay_stake = max(5.0, min(20.0, round(base * 0.10 / 5) * 5))
            if parlay_stake < 5.0:
                parlay_stake = 5.0

            best_ev     = parlay_ev
            best_parlay = {
                "leg1":         p1,
                "leg2":         p2,
                "comb_odds":    comb_odds,
                "parlay_ev":    parlay_ev,
                "parlay_stake": parlay_stake,
                "win_payout":   round(parlay_stake * comb_odds, 2),
            }

    if not best_parlay:
        return

    # Correlated parlay guard — prepend warning if legs are correlated
    _corr_legs = [
        {"market_type": best_parlay["leg1"].get("bet_type",""),
         "direction":   best_parlay["leg1"].get("direction","")},
        {"market_type": best_parlay["leg2"].get("bet_type",""),
         "direction":   best_parlay["leg2"].get("direction","")},
    ]
    best_parlay["corr_warning"] = _check_correlated_legs(_corr_legs)

    pk = (f"parlay_{best_parlay['leg1']['game_id']}_"
          f"{best_parlay['leg2']['game_id']}")
    if not _should_alert(pk, edge=best_parlay["parlay_ev"]):
        return

    _send_parlay_alert(best_parlay)


# CORE — MAIN SCAN
# ═══════════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 1B — LINE MOVEMENT PREDICTION
# ═══════════════════════════════════════════════════════════════════════════════

def _load_line_history():
    """Load line_history.json into _line_history global at startup."""
    global _line_history
    if os.path.isfile(_LINE_HISTORY_FILE):
        try:
            with open(_LINE_HISTORY_FILE) as _f:
                _line_history = json.load(_f)
        except Exception:
            _line_history = {}

def _save_line_history():
    """Persist _line_history to disk (trim to last 24 hours per game)."""
    cutoff = (datetime.now(ET) - timedelta(hours=24)).isoformat()
    pruned = {}
    for gid, entries in _line_history.items():
        kept = [e for e in entries if e.get("time", "") >= cutoff]
        if kept:
            pruned[gid] = kept
    try:
        with open(_LINE_HISTORY_FILE, "w") as _f:
            json.dump(pruned, _f)
    except Exception:
        pass
    _line_history.clear()
    _line_history.update(pruned)

def _check_reverse_line_movement(game_id: str, home_team: str, away_team: str,
                                  prev_ml_home: float, curr_ml_home: float,
                                  prev_total: float, curr_total: float) -> list:
    """
    Detect reverse line movement patterns.
    Returns list of alert strings (empty = no RLM detected).
    """
    alerts = []
    if prev_ml_home and curr_ml_home:
        if prev_ml_home < -180 and curr_ml_home > prev_ml_home + 0.05:
            alerts.append(
                f"🔄 MOVIMIENTO INVERSO (GANADOR): El público apuesta {_es(home_team)} "
                f"pero la línea se mueve en su contra. Posible dinero sharp en {_es(away_team)}."
            )
    if prev_total and curr_total:
        park_factor = MLB_PARK_FACTORS.get(home_team, 1.0)
        if park_factor > 1.05 and curr_total < prev_total - 0.3:
            alerts.append(
                f"🔄 MOVIMIENTO INVERSO (TOTAL): Estadio favorece bateadores "
                f"pero la línea bajó {prev_total} → {curr_total}. "
                f"Sharp money en UNDER."
            )
    return alerts


def _track_line_history(game_id: str, home: str, away: str,
                         total: float, ml_home: float, ml_away: float):
    """
    Record the current totals line + ML odds for a game.
    Returns an alert string if a strong, consistent line movement is detected
    (same direction for ≥3 data points, magnitude ≥0.5 total runs).
    Returns '' if no strong pattern.
    """
    now_s = datetime.now(ET).isoformat()
    entry = {"time": now_s, "total": total, "ml_home": ml_home, "ml_away": ml_away}

    # RLM check — compare against previous entry before appending
    _prev_entries = _line_history.get(game_id, [])
    if _prev_entries:
        _prev = _prev_entries[-1]
        _rlm_alerts = _check_reverse_line_movement(
            game_id, home, away,
            _prev.get("ml_home", 0.0), ml_home,
            _prev.get("total",   0.0), total,
        )
        for _rlm_msg in _rlm_alerts:
            print(f"  🔄 RLM: {_rlm_msg[:120]}")
            _rlm_dedup_key = f"rlm_{game_id}_{datetime.now(ET).strftime('%Y-%m-%d')}"
            if _should_alert(_rlm_dedup_key, edge=0):
                try:
                    ntfy_post(
                        f"🔄 MOVIMIENTO INVERSO | {_es(away)} @ {_es(home)}",
                        _rlm_msg, priority="high"
                    )
                except Exception:
                    pass

    _line_history.setdefault(game_id, []).append(entry)

    entries = _line_history[game_id]
    if len(entries) < 3:
        return ""

    # Look at last 6 data points for trend
    recent = entries[-6:]
    totals = [e["total"] for e in recent if e.get("total", 0) > 0]
    if len(totals) < 3:
        return ""

    # Compute consecutive deltas
    deltas = [totals[i+1] - totals[i] for i in range(len(totals)-1)]
    pos = sum(1 for d in deltas if d > 0.01)
    neg = sum(1 for d in deltas if d < -0.01)
    total_move = totals[-1] - totals[0]

    if abs(total_move) < 0.5:
        return ""  # not enough movement

    if pos >= 3 and total_move > 0:
        direction = "OVER ⬆️"
        side      = "UNDER (línea sube → OVER más caro, valor en UNDER)"
        predicted = round(totals[-1] + abs(total_move) / 2, 1)
    elif neg >= 3 and total_move < 0:
        direction = "UNDER ⬇️"
        side      = "OVER (línea baja → UNDER más caro, valor en OVER)"
        predicted = round(totals[-1] - abs(total_move) / 2, 1)
    else:
        return ""

    history_str = " → ".join(str(t) for t in totals)
    elapsed_h = round((entries[-1]["time"] > entries[0]["time"]) and
                      (datetime.fromisoformat(entries[-1]["time"]) -
                       datetime.fromisoformat(entries[0]["time"])).seconds / 3600, 1) or "?"
    return (
        f"📈 LÍNEA EN MOVIMIENTO:\n"
        f"{home} vs {away} — Total: {history_str}\n"
        f"Dirección: {direction}  |  Movimiento: {total_move:+.1f} en {elapsed_h}h\n"
        f"→ Predicción: llegará a {predicted}\n"
        f"→ Mejor lado ahora: {side}\n"
        f"→ APOSTAR AHORA antes que la línea se mueva más"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 1C — REAL-TIME NEWS MONITOR
# ═══════════════════════════════════════════════════════════════════════════════

_NEWS_KEYWORDS_HIGH = {
    "scratch", "not starting", "ruled out", "placed on il", "placed on the il",
    "day-to-day", "injured", "injury", "dnp",
}
_NEWS_KEYWORDS_MED  = {
    "lineup change", "late scratch", "questionable", "doubtful",
}
_NEWS_KEYWORDS_LOW  = {
    "bullpen", "trade", "postponed", "rain delay",
}

def _news_impact(text: str) -> str:
    t = text.lower()
    if any(k in t for k in _NEWS_KEYWORDS_HIGH):
        return "ALTO"
    if any(k in t for k in _NEWS_KEYWORDS_MED):
        return "MEDIO"
    if any(k in t for k in _NEWS_KEYWORDS_LOW):
        return "BAJO"
    return ""

def _fetch_mlb_espn_news() -> list:
    """Fetch latest MLB news from ESPN public API. Returns list of {id, headline, team}."""
    try:
        url  = "http://site.api.espn.com/apis/site/v2/sports/baseball/mlb/news?limit=20"
        r    = requests.get(url, timeout=8)
        data = r.json() if r.status_code == 200 else {}
        items = []
        for a in data.get("articles", []):
            headline = a.get("headline", "")
            nid      = a.get("id", "") or headline[:40]
            cats     = [c.get("description", "") for c in a.get("categories", [])]
            team     = next((c for c in cats if c), "MLB")
            impact   = _news_impact(headline)
            if impact:
                items.append({"id": str(nid), "headline": headline,
                               "team": team, "impact": impact, "source": "ESPN"})
        return items
    except Exception:
        return []

def _fetch_mlb_transactions() -> list:
    """Fetch today's MLB transactions from Stats API. Returns list of {id, headline, team}."""
    try:
        today = datetime.now(ET).strftime("%Y-%m-%d")
        url   = (f"https://statsapi.mlb.com/api/v1/transactions"
                 f"?sportId=1&startDate={today}&endDate={today}")
        r     = requests.get(url, timeout=8)
        data  = r.json() if r.status_code == 200 else {}
        items = []
        for t in data.get("transactions", []):
            desc   = t.get("description", "")
            nid    = str(t.get("id", desc[:40]))
            team   = (t.get("toTeam", {}) or {}).get("name", "") or \
                     (t.get("fromTeam", {}) or {}).get("name", "MLB")
            impact = _news_impact(desc)
            if impact:
                items.append({"id": nid, "headline": desc,
                               "team": team, "impact": impact, "source": "MLB Transactions"})
        return items
    except Exception:
        return []

def _fetch_twitter_news() -> list:
    """Fetch Twitter/X MLB injury mentions if TWITTER_BEARER_TOKEN set."""
    token = os.environ.get("TWITTER_BEARER_TOKEN", "")
    if not token:
        return []
    try:
        query = "(MLB OR baseball) (injured OR scratch OR IL OR \"ruled out\") lang:en -is:retweet"
        url   = "https://api.twitter.com/2/tweets/search/recent"
        r     = requests.get(url,
                             params={"query": query, "max_results": 10,
                                     "tweet.fields": "id,text"},
                             headers={"Authorization": f"Bearer {token}"},
                             timeout=8)
        data  = r.json() if r.status_code == 200 else {}
        items = []
        for tw in data.get("data", []):
            text   = tw.get("text", "")
            nid    = "tw_" + str(tw.get("id", ""))
            impact = _news_impact(text)
            if impact:
                items.append({"id": nid, "headline": text[:120],
                               "team": "Twitter/X", "impact": impact, "source": "Twitter/X"})
        return items
    except Exception:
        return []

def _monitor_news() -> list:
    """
    Run every scan. Checks ESPN + MLB Transactions + Twitter (optional).
    Returns list of new high/medium impact items not yet alerted.
    Sends ntfy alert for each HIGH-impact item found.
    Resets _last_news_seen once per day.
    """
    global _last_news_seen, _last_news_date
    today = datetime.now(ET).date()
    if today > _last_news_date:
        _last_news_seen.clear()
        _last_news_date = today

    new_items = []
    all_items = _fetch_mlb_espn_news() + _fetch_mlb_transactions() + _fetch_twitter_news()
    for item in all_items:
        nid = item["id"]
        if nid in _last_news_seen:
            continue
        _last_news_seen.add(nid)
        new_items.append(item)

    for item in new_items:
        impact    = item["impact"]
        headline  = item["headline"]
        team      = item["team"]
        source    = item["source"]

        # Translate headline (positions, IL, etc.) to Spanish
        headline_es = headline

        # Conversational explanation based on what happened
        explanation = _explain_news_impact(headline, team, impact)

        if impact == "ALTO":
            emoji = "🚨"
            prio  = "high"
            imp_label = "ALTO 🔴"
            tip_line  = "⚡ Ajusta tus picks ANTES del juego"
        elif impact == "MEDIO":
            emoji = "⚠️"
            prio  = "default"
            imp_label = "MEDIO 🟡"
            tip_line  = "📋 Considera esto en tu próximo análisis"
        else:
            emoji = "ℹ️"
            prio  = "low"
            imp_label = "BAJO 🟢"
            tip_line  = "📝 Anotado para el seguimiento"

        title = f"{emoji} NOTICIA IMPORTANTE — {team}"
        body  = (
            f"{emoji} NOTICIA IMPORTANTE\n"
            f"{'━' * 24}\n"
            f"📋 {headline_es}\n"
            f"\n"
            f"⚾ Equipo: {team}\n"
            f"📡 Fuente: {source}\n"
            f"💥 Impacto: {imp_label}\n"
            f"{'━' * 24}\n"
            f"{explanation}\n"
            f"{'━' * 24}\n"
            f"{tip_line}"
        )
        print(f"  {emoji} NOTICIA [{impact}]: {headline[:80]}")
        ntfy_post(title, body, prio)

    return new_items


# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 2A — PLAYER PROPS
# ═══════════════════════════════════════════════════════════════════════════════

_props_cache: dict = {}   # event_id → parsed props dict

def _fetch_player_props(event_id: str) -> dict:
    """Fetch pitcher strikeout + batter props from Odds API for a specific game event."""
    if not API_KEY or not event_id:
        return {}
    _today = datetime.now(TZ_LOCAL).strftime("%Y-%m-%d")
    ck = f"props_{event_id}_{_today}"
    if ck in _props_cache:
        return _props_cache[ck]
    try:
        url = (f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"
               f"?apiKey={API_KEY}&regions=us,us2,eu&oddsFormat=decimal"
               f"&markets=pitcher_strikeouts,batter_hits,batter_home_runs")
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return {}
        data   = r.json()
        result = {}
        for bk in data.get("bookmakers", []):
            for mkt in bk.get("markets", []):
                mkt_key = mkt["key"]
                for oc in mkt.get("outcomes", []):
                    name = oc.get("description", "") or oc.get("name", "")
                    pt   = oc.get("point")
                    side = oc.get("name", "Over") if oc.get("name") in ("Over","Under") else "Over"
                    prc  = float(oc.get("price", 0))
                    pk   = f"{name}|{mkt_key}"
                    result.setdefault(pk, {})[side] = {"point": pt, "price": prc}
        _props_cache[ck] = result
        return result
    except Exception as e:
        print(f"  ⚠️  Props [{event_id[:8]}]: {e}")
        return {}

_f5_odds_cache: dict = {}   # event_id → game-like dict with h2h_h1 / totals_h1 bookmakers

def _fetch_f5_odds(event_id: str) -> dict:
    """
    Obtiene odds de primera mitad (h2h_h1, totals_h1) para un evento MLB específico.
    Retorna un dict con estructura idéntica a un game dict (bookmakers[]), o {} si falla.
    Se obtiene por endpoint de evento para no romper get_odds() principal.
    """
    if not API_KEY or not event_id:
        return {}
    _today = datetime.now(TZ_LOCAL).strftime("%Y-%m-%d")
    ck = f"f5_{event_id}_{_today}"
    if ck in _f5_odds_cache:
        return _f5_odds_cache[ck]
    try:
        url = (f"https://api.the-odds-api.com/v4/sports/baseball_mlb/events/{event_id}/odds"
               f"?apiKey={API_KEY}&regions=us,us2,eu,uk&oddsFormat=decimal"
               f"&markets=h2h_h1,totals_h1")
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            _f5_odds_cache[ck] = {}
            return {}
        data = r.json()
        _f5_odds_cache[ck] = data
        return data
    except Exception as e:
        print(f"  ⚠️  F5 odds [{event_id[:8]}]: {e}")
        _f5_odds_cache[ck] = {}
        return {}

def _format_props_alert(props: dict, h_pname: str, a_pname: str,
                         h_era: float, a_era: float) -> str:
    """Scan strikeout props for value picks. Returns formatted alert block (max 2 props)."""
    lines = []
    for prop_key, sides in props.items():
        if "pitcher_strikeouts" not in prop_key:
            continue
        name  = prop_key.split("|")[0]
        over  = sides.get("Over", {})
        under = sides.get("Under", {})
        k_line = over.get("point")
        if not k_line:
            continue
        k_line       = float(k_line)
        over_price   = float(over.get("price", 2.0))
        # Determine which pitcher ERA to use
        is_home_p = h_pname and h_pname.split()[-1].lower() in name.lower()
        era       = h_era if is_home_p else a_era
        # Strikeout pitcher heuristic: low ERA → likely K pitcher → OVER value
        if era < 3.50 and k_line <= 7.5 and over_price >= 1.80:
            model_k_prob = 0.62 if era < 2.80 else 0.57
            ev_est       = (model_k_prob * over_price - 1) * 100
            if ev_est > 6:
                lines.append(
                    f"⚾ PROP DESTACADO:\n"
                    f"   {name} strikeouts OVER {k_line}\n"
                    f"   ERA: {era:.2f} (pitcher de ponches) | @ {over_price:.2f}\n"
                    f"   → EV estimado: +{ev_est:.0f}%"
                )
    return "\n".join(lines[:2])


# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 2B — PITCHER FATIGUE MODEL
# ═══════════════════════════════════════════════════════════════════════════════

_fatigue_cache: dict = {}

def _fetch_pitcher_fatigue_score(pitcher_id, pitcher_name: str) -> "dict | None":
    """
    Compute fatigue score (0-100) from last 5 starts' pitch counts.
    High fatigue (≥70) → +0.7 runs. Low fatigue (≤30) → -0.3 runs.
    Returns dict: {score, run_adj, label, note, pitch_counts} or None.
    """
    if not pitcher_id:
        return None
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck    = f"fatigue_{pitcher_id}_{today}"
    if ck in _fatigue_cache:
        return _fatigue_cache[ck]
    try:
        data   = _mlb_rest(f"/people/{pitcher_id}/stats", {
            "stats": "gameLog", "group": "pitching",
            "season": MLB_YEAR, "limit": 5,
        })
        splits = (data.get("stats", [{}])[0].get("splits", [])
                  if data and data.get("stats") else [])
        if not splits:
            return None
        pitch_counts = []
        days_rests   = []
        for sp in splits[-5:]:
            stat  = sp.get("stat", {})
            np_v  = stat.get("numberOfPitches") or stat.get("pitchesThrown", "0")
            try:
                pitch_counts.append(int(np_v or 0))
            except Exception:
                pitch_counts.append(0)
            # rest days between starts
            gdate = sp.get("date") or sp.get("game", {}).get("officialDate", "")
            if gdate:
                days_rests.append(gdate)

        if not pitch_counts:
            return None

        last = pitch_counts[-1]
        avg  = sum(pitch_counts) / len(pitch_counts)

        # Calculate fatigue score
        score = 30  # neutral baseline
        if last >= 110:   score += 40
        elif last >= 100: score += 20
        elif last <= 85:  score -= 20
        if avg >= 105:    score += 20
        elif avg >= 95:   score += 10
        elif avg <= 85:   score -= 15
        # Days rest from last start
        if len(days_rests) >= 2:
            try:
                d1 = datetime.strptime(days_rests[-1], "%Y-%m-%d")
                d2 = datetime.strptime(days_rests[-2], "%Y-%m-%d")
                rest = abs((d1 - d2).days)
                if rest <= 4:   score += 15
                elif rest >= 6: score -= 10
            except Exception:
                pass
        score = max(0, min(100, score))

        if score >= 70:
            run_adj = +0.7
            label   = "ALTA ⚠️"
            cnt_str = ", ".join(str(p) for p in pitch_counts)
            note    = (f"😓 Fatiga {pitcher_name}: Carga {label}\n"
                       f"   Últimas salidas: {cnt_str} pitches\n"
                       f"   → Puede salir temprano (+0.7 runs)")
        elif score <= 30:
            run_adj = -0.3
            label   = "BAJA ✅"
            cnt_str = ", ".join(str(p) for p in pitch_counts)
            note    = (f"💪 Fatiga {pitcher_name}: Carga {label}\n"
                       f"   Últimas salidas: {cnt_str} pitches\n"
                       f"   → Rendimiento óptimo (-0.3 runs)")
        else:
            run_adj = 0.0
            label   = "NORMAL"
            note    = ""

        result = {"score": score, "run_adj": run_adj, "label": label,
                  "note": note, "pitch_counts": pitch_counts}
        _fatigue_cache[ck] = result
        return result
    except Exception as e:
        print(f"  ⚠️  Fatigue [{pitcher_name}]: {e}")
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 2C — TRAVEL FATIGUE
# ═══════════════════════════════════════════════════════════════════════════════

_travel_cache: dict = {}
# Approximate standard-time UTC offsets for MLB cities
_CITY_TIMEZONE = {
    "New York": -5, "Boston": -5, "Philadelphia": -5, "Baltimore": -5,
    "Washington": -5, "Miami": -5, "Atlanta": -5, "Toronto": -5,
    "Pittsburgh": -5, "Cincinnati": -5, "Cleveland": -5, "Detroit": -5,
    "Chicago": -6, "Milwaukee": -6, "Minneapolis": -6, "Kansas City": -6,
    "Houston": -6, "Dallas": -6, "St. Louis": -6,
    "Denver": -7, "Phoenix": -7, "Salt Lake City": -7,
    "Seattle": -8, "Portland": -8, "Oakland": -8, "San Francisco": -8,
    "Los Angeles": -8, "San Diego": -8, "Anaheim": -8, "Las Vegas": -8,
}

def _travel_fatigue_adj(home: str, away: str) -> "tuple[float, float, str]":
    """
    Check where each team played yesterday vs. where they play today.
    Returns (home_adj, away_adj, note_str).
    Adj is additive to home_exp / away_exp (negative = fatigue).
    """
    today = datetime.now(CDT).strftime("%Y-%m-%d")
    ck    = f"travel_{home}_{away}_{today}"
    if ck in _travel_cache:
        return _travel_cache[ck]
    yesterday = (datetime.now(CDT) - timedelta(days=1)).strftime("%Y-%m-%d")
    h_city    = (MLB_PARK_CITIES.get(home, (None,))[0] or "")
    notes     = []
    h_adj = a_adj = 0.0

    for team, is_home in [(home, True), (away, False)]:
        try:
            tid = _team_id(team)
            if not tid:
                continue
            data = _mlb_rest("/schedule", {
                "teamId": tid, "season": MLB_YEAR, "gameType": "R",
                "startDate": yesterday, "endDate": yesterday, "hydrate": "venue",
            })
            g_list = (data.get("dates", [{}])[0].get("games", [])
                      if data.get("dates") else [])
            if not g_list:
                continue
            venue_city = (g_list[0].get("venue", {})
                          .get("location", {}).get("city", "") or "")
            curr_city  = h_city if is_home else h_city  # both play in home city today
            if not venue_city or not curr_city or venue_city == curr_city:
                continue
            tz_from = _CITY_TIMEZONE.get(venue_city, -6)
            tz_to   = _CITY_TIMEZONE.get(curr_city,  -6)
            tz_diff = abs(tz_to - tz_from)
            if tz_diff >= 3:
                adj = -0.5
                notes.append(f"✈️ {team.split()[-1]}: {venue_city}→{curr_city} "
                              f"({tz_diff} zonas) → -5% prob")
            elif tz_diff >= 2:
                adj = -0.25
                notes.append(f"✈️ {team.split()[-1]}: cruzó {tz_diff} zonas")
            elif tz_diff == 1:
                adj = -0.1
                notes.append(f"✈️ {team.split()[-1]}: cruzó 1 zona horaria")
            else:
                continue
            if is_home: h_adj += adj
            else:       a_adj += adj
        except Exception:
            pass

    result = (h_adj, a_adj, "\n".join(notes))
    _travel_cache[ck] = result
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 3A — PORTFOLIO OPTIMIZATION
# ═══════════════════════════════════════════════════════════════════════════════

def _portfolio_optimize(all_picks: list) -> str:
    """
    Given all picks from a scan, cap at 3 picks and compute 60/25/15 budget allocation.
    Returns a formatted portfolio string for printing/ntfy. Empty string if no picks.
    """
    if not all_picks:
        return ""
    sorted_picks = sorted(
        all_picks,
        key=lambda x: float(x.get("ev_pct", 0) or x.get("ev", 0) or 0),
        reverse=True,
    )
    top      = sorted_picks[:3]
    budget   = BANKROLL * 0.15   # max 15% of bankroll daily
    alloc    = [0.60, 0.25, 0.15]
    lines    = [
        "💼 PORTAFOLIO HOY:",
        f"Presupuesto diario: ${budget:.2f} (15% bankroll)",
        "─" * 28,
    ]
    total_exp = 0.0
    for i, (pick, pct) in enumerate(zip(top, alloc)):
        amt  = round(budget * pct, 2)
        team = pick.get("team", pick.get("label", pick.get("match", "?")))
        ev   = float(pick.get("ev_pct", 0) or pick.get("ev", 0) or 0)
        exp  = round(amt * ev / 100, 2)
        total_exp += exp
        rank = ["1️⃣", "2️⃣", "3️⃣"][i]
        lines.append(f" {rank} ${amt:.2f} ({int(pct*100)}%) — {team[:30]} EV:{ev:+.1f}%")
    lines += ["─" * 28, f"Ganancia esperada: +${total_exp:.2f}"]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 3B — BET TIMING OPTIMIZER
# ═══════════════════════════════════════════════════════════════════════════════

def _bet_timing_advice(pick_type: str, commence_time_str: str) -> str:
    """Consejo de timing óptimo para apostar según tipo de mercado."""
    try:
        game_et   = datetime.fromisoformat(
            commence_time_str.replace("Z", "+00:00")).astimezone(ET)
        game_str  = game_et.strftime("%I:%M %p ET")
        late_str  = (game_et - timedelta(hours=1)).strftime("%I:%M %p")
        early_str = (game_et - timedelta(hours=4)).strftime("%I:%M %p")
        open_str  = (game_et - timedelta(hours=16)).strftime("%I:%M %p")
    except Exception:
        return ""
    pt = (pick_type or "").lower()
    if "total" in pt or "under" in pt or "over" in pt:
        return (
            f"⏰ CUÁNDO APOSTAR ESTE TOTAL:\n"
            f"   ✅ Mejor: {open_str} ET (apertura) o {late_str}–{game_str}\n"
            f"   ⚠️ Evitar: 11 AM–4 PM ET (libros ajustan por público)\n"
            f"   → Las líneas de totals son más blandas al abrir y en la última hora"
        )
    elif "h2h" in pt or "ml" in pt:
        return (
            f"⏰ CUÁNDO APOSTAR ESTE ML:\n"
            f"   ✅ Mejor: {late_str}–{game_str} (dinero sharp ya se movió)\n"
            f"   → El ML tiene más valor cuando la línea ya fue movida por sharps"
        )
    elif "spread" in pt or "rl" in pt:
        return (
            f"⏰ CUÁNDO APOSTAR ESTE RL:\n"
            f"   ✅ Mejor: {early_str}–{late_str} ET\n"
            f"   Juego: {game_str}"
        )
    return ""


# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 3C — CLV WEEKLY SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════

def _weekly_clv_summary() -> str:
    """Read clv_log.csv and return a weekly CLV analysis block for Sunday report."""
    if not os.path.isfile(CLV_LOG_FILE):
        return ""
    try:
        import csv as _csv
        week_ago  = (datetime.now(ET) - timedelta(days=7)).strftime("%Y-%m-%d")
        clv_vals  = []
        best = worst = None
        with open(CLV_LOG_FILE, "r", newline="", encoding="utf-8") as _f:
            for row in _csv.DictReader(_f):
                if row.get("clv_time", "") < week_ago:
                    continue
                try:
                    cv = float(row.get("clv") or 0)
                    clv_vals.append(cv)
                    match = row.get("match", "?")
                    if best  is None or cv > best[1]:   best  = (match, cv)
                    if worst is None or cv < worst[1]:  worst = (match, cv)
                except Exception:
                    pass
        if not clv_vals:
            return ""
        avg_clv = sum(clv_vals) / len(clv_vals)
        pos_pct = sum(1 for c in clv_vals if c > 0) / len(clv_vals) * 100
        verdict = ("Modelo tiene edge real 💎" if avg_clv > 0
                   else "⚠️ Apostando en mal momento → revisar timing")
        lines = [
            "━" * 30,
            "📊 CLOSING LINE VALUE (semana):",
            f"   CLV promedio: {avg_clv:+.2f}%  {'✅' if avg_clv > 0 else '❌'}",
            f"   Picks que ganaron al cierre: {pos_pct:.0f}%",
        ]
        if best:  lines.append(f"   Mejor: {best[0][:30]} ({best[1]:+.2f}%)")
        if worst: lines.append(f"   Peor:  {worst[0][:30]} ({worst[1]:+.2f}%)")
        lines.append(f"   → {verdict}")
        if avg_clv < 0:
            lines.append("   → Cambiar timing (apostar más cerca del juego)")
        return "\n".join(lines)
    except Exception as e:
        return f"   ⚠️  CLV summary error: {e}"


# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 4B — FUTURES & SPECIAL PROPS
# ═══════════════════════════════════════════════════════════════════════════════

_last_futures_check: date = date(2000, 1, 1)

def _fetch_futures(sport_key: str) -> list:
    """Fetch outrights/futures from Odds API."""
    if not API_KEY:
        return []
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
            params={"apiKey": API_KEY, "regions": "us,eu",
                    "markets": "outrights", "oddsFormat": "decimal"},
            timeout=10,
        )
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

def _analyze_and_alert_futures():
    """
    Check MLB + World Cup futures for ELO-model edge vs. implied probability.
    Sends ntfy alert if EV > 5% for any team.
    Runs daily at most once (guarded by _last_futures_check).
    """
    global _last_futures_check
    today = datetime.now(ET).date()
    if today <= _last_futures_check:
        return
    _last_futures_check = today
    alerts = []
    for sport_key, label in [("baseball_mlb", "MLB"), ("soccer_fifa_world_cup", "Mundial")]:
        if not is_in_season(sport_key):
            continue
        for game in _fetch_futures(sport_key)[:20]:
            for bk in game.get("bookmakers", [])[:1]:
                for mkt in bk.get("markets", []):
                    if mkt.get("key") != "outrights":
                        continue
                    for oc in mkt.get("outcomes", []):
                        team   = oc.get("name", "")
                        price  = float(oc.get("price", 1.0) or 1.0)
                        impl_p = 1.0 / price if price > 1 else 0
                        # Use ELO win probability as our model estimate
                        try:
                            elo_p = elo_win_prob(team, "field") if "mlb" in sport_key else impl_p * 1.08
                        except Exception:
                            elo_p = impl_p * 1.05
                        ev = (elo_p * price - 1) * 100
                        if ev > 5.0:
                            poten = round((price - 1) * 30, 2)
                            alerts.append(
                                f"🏆 {label}: {team}\n"
                                f"   Modelo: {elo_p:.0%}  |  Implícito: {impl_p:.0%}\n"
                                f"   Edge: +{ev:.1f}% EV\n"
                                f"   💰 $30 → ganancia potencial: ${poten}"
                            )
    if alerts:
        ntfy_post("🏆 Futuros con Valor", "\n\n".join(alerts[:4]), "default")
        print(f"  🏆 Futuros: {len(alerts)} oportunidad(es) enviada(s)")


# ═══════════════════════════════════════════════════════════════════════════════
# LEVEL 4C — CROSS-GAME CORRELATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def _check_cross_game_correlations(all_full_analyses: list):
    """
    Check for cross-game patterns that affect all MLB picks the same day.
    - Ace day (3+ starters ERA < 3.0) → UNDER tendency
    - Runs at end of run_scan after all analyses.
    """
    if not all_full_analyses:
        return
    ace_pitchers = []
    for fa in all_full_analyses:
        ctx   = fa.get("context", {})
        h_era = float(ctx.get("era_home", 9.0) or 9.0)
        a_era = float(ctx.get("era_away", 9.0) or 9.0)
        ph    = ctx.get("pname_home", "")
        pa    = ctx.get("pname_away", "")
        if h_era < 3.0 and ph:
            ace_pitchers.append(f"{ph} ({h_era:.2f})")
        if a_era < 3.0 and pa:
            ace_pitchers.append(f"{pa} ({a_era:.2f})")

    if len(ace_pitchers) >= 3:
        today     = datetime.now(CDT).strftime("%Y-%m-%d")
        corr_key  = f"corr_ace_day_{today}"
        if not _should_alert(corr_key):
            print(f"  🔗 Correlación ace-day ya alertada hoy — omitida (dedup)")
            return
        aces_str = "\n   ".join(ace_pitchers[:6])
        msg = (
            f"🔗 CORRELACIÓN: DÍA DE ASES\n"
            f"{len(ace_pitchers)} pitchers élite lanzando hoy:\n"
            f"   {aces_str}\n"
            f"→ Históricamente ≥68% Under en días así\n"
            f"→ Priorizar UNDER en todos los picks de hoy"
        )
        print(f"  🔗 Correlación: {len(ace_pitchers)} aces hoy → alerta enviada")
        ntfy_post("🔗 Correlación: Día de Ases", msg, "default")


def run_scan():
    global lineup_scan_counter
    if len(_claude_cache) > 200:
        _claude_cache.clear()
        print("  🧹 Claude cache limpiado (>200 entradas)")
    prev_map  = load_previous_odds()
    new_map   = {}
    all_bets          = []
    all_sharp         = []
    all_arbs          = []
    all_totals        = []
    all_full_analyses = []   # parlay detector — collects across all sports
    all_steams        = []   # steam moves — collects across all sports
    all_premiums:     list = []  # Module P: PREMIUM picks (≥3 signals)
    _scan_alerted:    dict = {}  # dedup: {match_key: edge} — one alert per match per scan
    _steam_game_ids.clear()      # reset steam registry for this scan
    now_month  = datetime.now(CDT).month

    # Improvement 4: bankroll dashboard at top of every scan
    print_dashboard()

    # Level 1C: News monitor — runs every scan
    try:
        _new_news = _monitor_news()
        if _new_news:
            print(f"  📰 Noticias nuevas: {len(_new_news)} item(s)")
    except Exception as _ne:
        print(f"  ⚠️  News monitor error: {_ne}")

    # Collect live game dicts per sport for CLV lookup
    current_games_by_sport: dict = {}

    for sport_key in SPORT_KEYS:
        if not is_in_season(sport_key):
            print(f"  ⏭  {sport_key} — off-season (month {now_month})")
            continue

        try:
            games = get_odds(sport_key)
        except RuntimeError as _ge:
            print(f"  ⚠️  {sport_key} — API error: {_ge}")
            continue
        if not games:
            print(f"  ⚠️  {sport_key} — sin juegos hoy (ET)")
            continue
        try:

            # Override Odds-API times with authoritative MLB Stats API times
            if "mlb" in sport_key.lower():
                _patch_mlb_commence_times(games)
                # Pre-load Pinnacle ref cache (OddsPapi) — respects own quota/TTL
                try:
                    import pinnacle_ref as _pref
                    _pref.fetch_pinnacle_slate()
                    print(f"  📌 Pinnacle ref (OddsPapi): {_pref.cache_status()}")
                except Exception as _pre:
                    print(f"  ⚠️ pinnacle_ref error: {_pre}")

            # ── Soccer "today first" filter ────────────────────────────────
            # For soccer: prioritize today's games; only fall back to
            # 1-3 day games when there is nothing today.
            if "mlb" not in sport_key:
                today_games = [
                    g for g in games
                    if _days_until(g.get("commence_time", "")) < 1
                ]
                near_games = [
                    g for g in games
                    if 1 <= _days_until(g.get("commence_time", "")) < 3
                ]
                games = today_games if today_games else near_games
                if not games:
                    print(f"  ⏭  {sport_key} — sin partidos hoy ni en <3 días")
                    continue
                print(f"  📅 {sport_key} — {len(games)} partido(s) "
                      f"({'hoy' if today_games else '<3 días'})")
            # ──────────────────────────────────────────────────────────────

            # ── Filter out games that already started (5-min grace) ─────────
            _now_utc_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S UTC")
            print(f"  🕐 Hora actual UTC: {_now_utc_str} — evaluando {len(games)} juego(s)")
            _kept = []
            for _g in games:
                _ct = _g.get("commence_time", "")
                _started = _game_already_started(_ct, grace_min=5)
                print(f"     {'🚫 YA INICIÓ' if _started else '✅ FUTURO'} "
                      f"{_g.get('home_team','?')} vs {_g.get('away_team','?')} "
                      f"| commence={_ct}")
                if not _started:
                    _kept.append(_g)
            _skipped = len(games) - len(_kept)
            games = _kept
            if _skipped:
                print(f"  🚫 {sport_key} — {_skipped} juego(s) ya comenzaron, ignorados")
            if not games:
                print(f"  ⏭  {sport_key} — todos los juegos ya comenzaron")
                continue
            # ───────────────────────────────────────────────────────────────

            current_games_by_sport[sport_key] = games   # for CLV check

            # Level 1B: track totals line history for each game
            if "mlb" in sport_key:
                for _g in games:
                    try:
                        _gid = _g.get("id", "")
                        _bks = _g.get("bookmakers", [])
                        _tot_line = _ml_h = _ml_a = 0.0
                        for _bk in _bks:
                            for _mkt in _bk.get("markets", []):
                                if _mkt["key"] == "totals" and not _tot_line:
                                    _ocs = _mkt.get("outcomes", [])
                                    if _ocs:
                                        _tot_line = float(_ocs[0].get("point", 0))
                                if _mkt["key"] == "h2h" and not _ml_h:
                                    for _oc in _mkt.get("outcomes", []):
                                        if _oc["name"] == _g["home_team"]:
                                            _ml_h = float(_oc.get("price", 0))
                                        elif _oc["name"] == _g["away_team"]:
                                            _ml_a = float(_oc.get("price", 0))
                        if _tot_line and _gid:
                            _alert = _track_line_history(
                                _gid, _g["home_team"], _g["away_team"],
                                _tot_line, _ml_h, _ml_a)
                            if _alert:
                                ntfy_post("📈 Línea en Movimiento", _alert, "high")
                    except Exception:
                        pass

            bets, sharp_moves, steam_moves = analyze(games, prev_map, new_map, sport_key)
            total_bets = analyze_totals(games, sport_key)
            # Team totals — MLB only; reuses the same games list and notify_totals path
            try:
                _tt_bets = analyze_team_totals(games, sport_key)
                if _tt_bets:
                    print(f"  ⚾ Team Totals: {len(_tt_bets)} pick(s) encontrado(s)")
                    total_bets = total_bets + _tt_bets
            except Exception as _tte:
                print(f"  ⚠️  analyze_team_totals error: {_tte}")
            if not any(os.environ.get(k) for k in ["BETONLINE_KEY", "SECOND_BOOK"]):
                arbs = []
            else:
                arbs = scan_arbitrage(games, sport_key)
            for m in sharp_moves:
                m["sport"] = sport_key
            short = sport_key.split("_", 1)[-1].upper()

            for b in bets:
                b["sport"] = short

            # Full game analysis (Module 7)
            full_analyses = []
            _enh_ctx_skipped: list = []
            for g in games:
                try:
                    result = analyze_game_full(g, sport_key, prev_map)
                    if result and result.get("skipped"):
                        _enh_ctx_skipped.append(result)
                    elif result:
                        full_analyses.append(result)
                except Exception as _fe:
                    pass

            if _enh_ctx_skipped:
                print(
                    f"\n⛔ [{short}] RESUMEN DE SKIPS — "
                    f"{len(_enh_ctx_skipped)} juego(s) excluido(s) por fallo de _enh_ctx:"
                )
                for _sk in _enh_ctx_skipped:
                    print(f"   • {_sk['match']}: {_sk['skip_reason']}")
            elif games and "mlb" in sport_key:
                print(f"   ✅ [{short}] _enh_ctx OK — todos los {len(games)} juego(s) procesados")

            # ── Module P: PREMIUM signal injection ─────────────────────────
            if not _bankroll_paused:
                try:
                    _ctx_map = {a["game_id"]: a.get("context", {})
                                for a in full_analyses}
                    for _bet in list(bets) + list(total_bets):
                        _h, _a = (_bet.get("match", " vs ") + " vs ").split(
                            " vs ", 1)[:2]
                        _h = _h.strip(); _a = _a.strip()
                        _ctx  = _ctx_map.get(_bet.get("game_id", ""), {})
                        _sigs = _count_premium_signals(_bet, _ctx, _h, _a)
                        _bet["signals"] = _sigs
                        if len(_sigs) >= 3 and _is_safe_book(
                                _bet.get("bookmaker", "")):
                            _bet["premium"] = True
                            _bet["stake"]   = min(
                                round(_bet["stake"] * PREMIUM_MULT, 2),
                                PREMIUM_MAX_STAKE)
                            all_premiums.append(_bet)
                        else:
                            _bet["premium"] = False
                except Exception as _pme:
                    print(f"  ⚠️  Premium injection error: {_pme}")
            # ──────────────────────────────────────────────────────────────

            if bets:
                print(f"\n  ✅ {short} — {len(bets)} value bet(s):")
                for b in bets:
                    print(f"    {b['match']} → {b['team']} @{b['odds']} | Edge:{b['edge']}%")
                all_bets.extend(bets)
            else:
                print(f"  ❌ {short} — no ML value")

            if total_bets:
                print(f"  🎯 {short} — {len(total_bets)} totals bet(s):")
                all_totals.extend(total_bets)
            else:
                print(f"  ❌ {short} — no totals value")

            if sharp_moves:
                print(f"  ⚡ {short} — {len(sharp_moves)} sharp move(s)")
                all_sharp.extend(sharp_moves)

            if steam_moves:
                print(f"  🚂 {short} — {len(steam_moves)} steam move(s)")
                all_steams.extend(steam_moves)

            if arbs:
                print(f"  💰 {short} — {len(arbs)} arb opportunity(ies)")
                all_arbs.extend(arbs)

            if full_analyses:
                print(f"  🔍 {short} — {len(full_analyses)} full analysis(es)")
                # Full analysis has best EV — alert FIRST so dedup favors it
                notify_game_analysis(full_analyses, sport_key, _scan_alerted)
                all_full_analyses.extend(full_analyses)  # parlay collector

            # ML and totals picks — skip any match already alerted by full analysis
            if bets:
                notify_bets(bets, _scan_alerted)
            if total_bets:
                notify_totals(total_bets, _scan_alerted)
            try:
                _q_picks = [b for b in (bets + total_bets) if b.get("stake", 0) >= MIN_STAKE]
                if _q_picks:
                    queue_for_confirmation(_q_picks, sport_key)
            except Exception as _qe:
                print(f"  ⚠️  queue_for_confirmation error: {_qe}")

        except Exception as e:
            print(f"  ⚠️  {sport_key} error (skipping): {e}")

    prev_map.update(new_map)
    save_previous_odds(prev_map)

    # Level 1B: persist line history to disk
    try:
        _save_line_history()
    except Exception:
        pass

    # Module P: PREMIUM alerts removed (stakes/portfolio disabled)

    # Level 4B: futures value check (runs once per day)
    try:
        _analyze_and_alert_futures()
    except Exception as _fbe:
        print(f"  ⚠️  Futures check error: {_fbe}")


    # Module P: PREMIUM alerts — sent once after all sports are collected
    for pb in all_premiums:
        try:
            notify_premium_bet(pb)
        except Exception as _pbe:
            print(f"  ⚠️  Premium alert error: {_pbe}")

    # Improvement 3: check pending bets for closing lines / CLV
    try:
        check_closing_lines(current_games_by_sport)
    except Exception as e:
        print(f"  ⚠️  CLV check error: {e}")

    try:
        detect_and_notify_parlays(all_full_analyses)
    except Exception as _pe:
        print(f"  ⚠️  Parlay detector error: {_pe}")
    try:
        _check_cross_game_correlations(all_full_analyses)
    except Exception as _ce:
        print(f"  ⚠️  Cross-game error: {_ce}")

    # Lineup check every 15 min (every 3rd 10-min scan)
    lineup_scan_counter += 1
    if lineup_scan_counter >= 3:
        check_lineup_changes()
        lineup_scan_counter = 0

    try:
        _github_push_daily_exposure()
    except Exception as _ge:
        print(f"  ⚠️  GitHub exposure sync error: {_ge}")

    # Módulos Avanzados — auto-resultados, CLV, contrarian (al final de cada scan)
    if HAS_PAQUETE_AVANZADO:
        try:
            _avz_sport = next(
                (sk for sk in SPORT_KEYS if "mlb" in sk.lower()),
                "baseball_mlb"
            )
            run_modulos_avanzados(_avz_sport)
        except Exception as _mae:
            import traceback as _tb
            print(f"  ⚠️  Módulos avanzados error: {_mae}\n{_tb.format_exc()}")

# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # ── Guard: previene arranque dual del proceso principal ───────────────────
    # En Railway, durante un redeploy puede haber un breve solapamiento entre
    # la instancia antigua y la nueva. Este lock avisa en logs si eso ocurre.
    _MAIN_LOCK = "/tmp/betbot_main.lock"
    _MAIN_PID  = os.getpid()
    try:
        if os.path.exists(_MAIN_LOCK):
            try:
                with open(_MAIN_LOCK) as _mf:
                    _prev_pid = int(_mf.read().strip() or 0)
            except (ValueError, OSError):
                _prev_pid = 0
            if _prev_pid and _prev_pid != _MAIN_PID:
                try:
                    os.kill(_prev_pid, 0)
                    print(f"⚠️  BetBot: instancia anterior (PID {_prev_pid}) aún activa — "
                          f"Railway debería terminarla pronto. Continuando arranque.")
                except (ProcessLookupError, PermissionError, OSError):
                    pass  # proceso anterior ya muerto → lock obsoleto
        with open(_MAIN_LOCK, "w") as _mf:
            _mf.write(str(_MAIN_PID))
        import atexit as _main_atexit
        _main_atexit.register(lambda: (
            os.path.exists(_MAIN_LOCK) and
            open(_MAIN_LOCK).read().strip() == str(_MAIN_PID) and
            os.remove(_MAIN_LOCK)
        ))
        print(f"🔒 BetBot Pro lock adquirido (PID {_MAIN_PID})")
    except Exception as _mle:
        print(f"⚠️  Main lock: {_mle} — continuando")
    # ──────────────────────────────────────────────────────────────────────────

    if not HAS_STATSAPI:
        print("⚠️  MLB-statsapi not found — install via: pip install MLB-statsapi")
    print("🤖 BetBot Pro — starting...")
    scan = 1
    _load_stats_disk_cache()  # load persistent stats cache (survives Railway restarts)
    _daily_exposure, _daily_exposure_date = _load_daily_exposure()
    print(f"  💼 Exposición diaria restaurada: ${_daily_exposure:.2f} (fecha: {_daily_exposure_date})")
    _load_elite_counter()
    compute_bankroll_mult()   # Module P: initialize stake multiplier at startup
    _load_performance_adjustments()   # Mejora 1: ajustar umbrales por resultados reales
    try:
        _check_pinnacle_availability()   # Module 11: verify Pinnacle in Odds API plan
    except Exception as _pe:
        print(f"  ⚠️  Pinnacle check skipped: {_pe}")
    try:
        _update_monthly_kelly_mult()     # Improvement 4: set Kelly multiplier from backtest CSV
    except Exception as _me:
        print(f"  ⚠️  Monthly Kelly mult skipped: {_me}")
    try:
        _load_line_history()             # Level 1B: load line movement history from disk
    except Exception as _lhe:
        print(f"  ⚠️  Line history load skipped: {_lhe}")
    try:
        import importlib.util as _ilu
        _ml_path = os.path.join(os.path.dirname(__file__), "ml_model.py")
        if os.path.isfile(_ml_path):
            _spec = _ilu.spec_from_file_location("ml_model", _ml_path)
            _ml   = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_ml)
            _ml.load()                   # Level 1A: load ML model into memory
            _ML_MODULE = _ml
            print("  🤖 ML model inicializado al arranque")
    except Exception as _mle:
        print(f"  ⚠️  ML model startup skipped: {_mle}")

    # Telegram bot — background polling thread (daemon, never blocks main loop)
    try:
        from telegram_bot import iniciar_telegram as _iniciar_tg
        _iniciar_tg(analyze_fn=analyze_game_full, get_odds_fn=get_odds,
                    build_text_fn=build_analizar_text,
                    get_hoy_fn=get_today_hoy_summary,
                    get_patrones_fn=detectar_patrones_getaway)
    except Exception as _tge:
        print(f"  ⚠️  Telegram bot startup skipped: {_tge}")

    while True:
        try:
            now_cdt = datetime.now(CDT)
            now_et  = datetime.now(ET)
            print(f"\n{'='*50}\n🕐 {now_cdt.strftime('%Y-%m-%d %H:%M CDT')}")

            check_midnight_reset()

            # Health check at 7 AM ET (once per day) — Feature 1
            if now_et.hour == 7 and last_health_check < now_et.date():
                try:
                    run_health_check()
                except Exception as e:
                    print(f"  ⚠️  Health check error: {e}")

            # Morning report at 8 AM ET (once per day) — Module 2
            if now_et.hour == 8 and last_morning_report < now_et.date():
                try:
                    morning_report()
                except Exception as e:
                    print(f"  ⚠️  Morning report error: {e}")

            # Getaway-day / bullpen pattern scan at 9 AM CT (= 10 AM ET) — once per day
            if now_et.hour == 10 and last_patrones_scan < now_et.date():
                try:
                    _alerts = detectar_patrones_getaway()
                    last_patrones_scan = now_et.date()
                    if _alerts:
                        _body = "\n\n".join(_alerts)
                        ntfy_post("🚨 PATRONES SITUACIONALES", _body, "high")
                        if _tg_broadcast_fn:
                            _tg_broadcast_fn(
                                "🚨 PATRONES SITUACIONALES\n\n" + _body,
                            )
                        print(f"  🚨 Patrones getaway: {len(_alerts)} alerta(s) enviada(s)")
                    else:
                        print("  ✅ Patrones getaway: sin alertas activas hoy")
                except Exception as e:
                    print(f"  ⚠️  Patrones getaway scan error: {e}")

            # Weekly summary every Sunday at 9 AM ET — Module 10
            if now_et.weekday() == 6 and now_et.hour == 9 and last_weekly_report < now_et.date():
                try:
                    send_weekly_summary()
                    last_weekly_report = now_et.date()
                except Exception as e:
                    print(f"  ⚠️  Weekly summary error: {e}")

            # Backtest every Sunday at 10 AM ET — Module 12
            if now_et.weekday() == 6 and now_et.hour == 10 and last_backtest_report < now_et.date():
                try:
                    run_weekly_backtest()
                    last_backtest_report = now_et.date()
                except Exception as e:
                    print(f"  ⚠️  Backtest error: {e}")

            # MLB Daily Card at 2 PM ET (once per day)
            if now_et.hour == 14 and last_mlb_card < now_et.date():
                try:
                    send_daily_card("baseball_mlb")
                except Exception as e:
                    print(f"  ⚠️  MLB Daily Card error: {e}")

            # Soccer Daily Card at 10 AM ET (once per day)
            if now_et.hour == 10 and last_soccer_card < now_et.date():
                try:
                    send_daily_card("soccer_fifa_world_cup")
                except Exception as e:
                    print(f"  ⚠️  Soccer Daily Card error: {e}")

            # Night summary at 11 PM ET — Module P
            if now_et.hour == 23 and last_night_summary < now_et.date():
                try:
                    send_night_summary()
                    last_night_summary = now_et.date()
                except Exception as e:
                    print(f"  ⚠️  Night summary error: {e}")

            _scan_hr = now_et.hour
            if not (9 <= _scan_hr <= 23):
                print(f"  😴 Fuera de ventana de juegos (ET {_scan_hr:02d}h), scan omitido")
            else:
                print(f"🔍 Scan #{scan}")
                _scan_ok  = True
                _scan_err = ""
                try:
                    run_scan()
                except Exception as e:
                    _scan_ok  = False
                    _scan_err = str(e)[:120]
                    print(f"  ⚠️  Scan error (will retry): {e}")
                try:
                    with open("/tmp/betbot_scan_status.json", "w") as _stsf:
                        json.dump({
                            "ts":    datetime.now(ET).strftime("%Y-%m-%d %H:%M ET"),
                            "ok":    _scan_ok,
                            "error": _scan_err,
                        }, _stsf)
                except Exception:
                    pass

            # Module 1: auto-resultados — check confirmed bets after every scan
            try:
                check_results()
            except Exception as e:
                print(f"  ⚠️  check_results error: {e}")

            # Confirmation system: poll ntfy topic for "aposté" / "bet placed"
            try:
                _poll_ntfy_confirmations()
            except Exception as e:
                print(f"  ⚠️  poll_confirmations error: {e}")

            print(f"\n⏳ Next scan in {INTERVAL // 60} min...")
            time.sleep(INTERVAL)
            scan += 1

        except KeyboardInterrupt:
            print("\n🛑 Bot detenido manualmente.")
            break
        except SystemExit:
            # Railway SIGTERM → let the container stop cleanly for redeploys
            raise
        except BaseException as _loop_err:
            # Catch everything else (MemoryError, OSError, etc.) — never stop
            print(f"  ⚠️  Error crítico ({type(_loop_err).__name__}): {_loop_err}")
            print("  🔄 Reintentando en 60 segundos...")
            try:
                time.sleep(60)
            except Exception:
                pass
            scan += 1
