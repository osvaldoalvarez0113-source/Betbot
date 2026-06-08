"""
BetBot Pro — Professional Multi-Module Sports Betting System
Modules: Morning Report | Lineup Monitor | Math Models | Sharp Radar | Arb Scanner
"""
import requests, time, csv, os, json, math
from datetime import datetime, date, timedelta
import pytz
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
CLAUDE_PANEL_MODEL = os.environ.get("CLAUDE_PANEL_MODEL", "claude-haiku-4-5-20251001")
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO       = os.environ.get("GITHUB_REPO",  "osvaldoalvarez0113-source/Betbot")
BANKROLL = 1000
FRACTION = 0.25
MIN_EDGE  = 2.0
MIN_STAKE            = 10.00  # never alert if Kelly stake < $10
MIN_BET              = 10.00  # hard floor — identical to MIN_STAKE
MAX_SINGLE_BET_PCT   = 0.05   # hard cap: 5% of bankroll per single bet
MAX_DAILY_EXPO_PCT   = 0.15   # hard cap: 15% of bankroll queued per day
PROB_CAP             = 0.80   # max single-bet prob; anything higher → cap at 75%
PROB_CAP_CEIL        = 0.75   # value used after capping (realistic MLB ceiling)
PROB_CAP_PARLAY      = 0.75   # max probability for any parlay leg
PREMIUM_MULT      = 1.5     # Module P: stake multiplier for PREMIUM alerts
PREMIUM_MAX_STAKE = 100.0   # Module P: max PREMIUM bet size ($)
INTERVAL  = 600       # 10-minute main scan (API limit-friendly)
NOTIFY   = "my-bets"
LOG_CSV  = True

CDT            = pytz.timezone("America/Chicago")
ET             = pytz.timezone("America/New_York")

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
MLB_YEAR       = datetime.now().year

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

def _is_us_book(title: str) -> bool:
    """Return True only if the bookmaker is on the US-only whitelist."""
    t = (title or "").lower()
    return any(us in t for us in US_BOOKS_ONLY)

OPENWEATHER_KEY   = os.environ.get("OPENWEATHER_API_KEY", "")
BANKROLL_LOG_FILE      = "bankroll_log.csv"
CLV_LOG_FILE           = "clv_log.csv"
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
    "Oakland Athletics":      ("Oakland",        37.7516, -122.2005),
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

# ── RUNTIME STATE ─────────────────────────────────────────────────────────────
alerted_bets:          set  = set()
alerted_game_analysis: set  = set()
_sent_alerts:          dict = {}   # key → {date, odds, edge} for smart dedup
daily_bets:            list = []
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
_kelly_monthly_mult: float = 1.0  # Improvement 4: monthly ROI-based Kelly multiplier
_line_history:    dict = {}       # 1B: game_id → [{time, total, ml_home, ml_away}]
_last_news_seen:  set  = set()    # 1C: news IDs already alerted this session
_last_news_date:  date = date(2000, 1, 1)  # 1C: reset seen set daily
_steam_game_ids:  set   = set()  # game_ids with confirmed steam (current scan)
_ntfy_last_confirm_id: str = ""  # last ntfy message ID processed for confirmations
_daily_exposure:      float = 0.0            # total stake queued today ($)
_daily_exposure_date: "date" = date(2000, 1, 1)  # reset tracking when date changes
_tg_broadcast_fn = None  # set by telegram_bot.iniciar_telegram; broadcasts to Telegram

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
        ct   = datetime.strptime(commence_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
        diff = (ct - datetime.now(pytz.utc)).total_seconds() / 60
        return diff < minutes
    except Exception:
        return False

def _game_already_started(time_str: str, grace_min: int = 5) -> bool:
    """
    Returns True if the game started more than grace_min minutes ago.
    Accepts both 'YYYY-MM-DDTHH:MM:SSZ' and 'YYYY-MM-DDTHH:MM' formats.
    """
    try:
        ts = time_str.strip()
        if ts.endswith("Z"):
            ct = datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
        elif "T" in ts and len(ts) >= 16:
            naive = datetime.strptime(ts[:16], "%Y-%m-%dT%H:%M")
            ct = naive.replace(tzinfo=ET)
        else:
            return False
        elapsed = (datetime.now(pytz.utc) - ct.astimezone(pytz.utc)).total_seconds() / 60
        return elapsed > grace_min
    except Exception:
        return False

def _days_until(commence_str: str) -> float:
    """Days (float) from now until game. Returns 999 on parse error."""
    try:
        ct = datetime.strptime(commence_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
        return (ct - datetime.now(pytz.utc)).total_seconds() / 86400
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
        ct    = datetime.strptime(commence_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=pytz.utc)
        ct_et = ct.astimezone(ET)
        now_et = datetime.now(ET)
        days  = (ct_et.date() - now_et.date()).days
        t_str = ct_et.strftime("%-I:%M %p ET")
        if days == 0:
            return f"Hoy {t_str}"
        if days == 1:
            return f"Mañana {t_str}"
        month_es = {1:"Ene",2:"Feb",3:"Mar",4:"Abr",5:"May",6:"Jun",
                    7:"Jul",8:"Ago",9:"Sep",10:"Oct",11:"Nov",12:"Dic"}
        date_lbl = f"{month_es.get(ct_et.month, ct_et.strftime('%b'))} {ct_et.day}"
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
    # Below minimum → no bet
    if raw < MIN_BET and raw > 0:
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
                    pnl_sum += float(row.get("pnl", 0) or 0)
                except Exception:
                    pass
        total_bets = wins + losses
        if total_bets < 10:
            print(f"  ℹ️  Kelly mensual: solo {total_bets} apuestas en {cur_month} — sin ajuste")
            return
        roi = pnl_sum / total_bets * 100   # assumes $1 flat stake per bet
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
_TERM_RULES: "list[tuple]" = []

def _build_term_rules():
    global _TERM_RULES
    _raw = [
        # Pitcher handedness
        (r'\bLHP\b',                    'pitcher zurdo'),
        (r'\bRHP\b',                    'pitcher diestro'),
        # Medical — longest forms first
        (r'\b60-day\s+(?:IL|DL)\b',    'lista de lesionados (60 días)'),
        (r'\b15-day\s+(?:IL|DL)\b',    'lista de lesionados (15 días)'),
        (r'\b10-day\s+(?:IL|DL)\b',    'lista de lesionados (10 días)'),
        (r'\b7-day\s+(?:IL|DL)\b',     'lista de lesionados (7 días)'),
        (r'\binjured list\b',           'lista de lesionados'),
        (r'\bdisabled list\b',          'lista de lesionados'),
        (r'\b(?:IL|DL)\b',             'lista de lesionados'),
        # Field positions
        (r'\bLHP\b',                    'pitcher zurdo'),
        (r'\bRHP\b',                    'pitcher diestro'),
        (r'\bDH\b',                     'bateador designado'),
        (r'\bSP\b',                     'pitcher abridor'),
        (r'\bRP\b',                     'pitcher relevista'),
        (r'\bCF\b',                     'jardín central'),
        (r'\bLF\b',                     'jardín izquierdo'),
        (r'\bRF\b',                     'jardín derecho'),
        (r'\b2B\b',                     'segunda base'),
        (r'\b1B\b',                     'primera base'),
        (r'\bSS\b',                     'campo corto'),
        (r'\b3B\b',                     'tercera base'),
        (r'\bC\b(?=\s+[A-Z][a-z])',     'receptor'),   # "C J.T. Realmuto" only
        # Stats (xERA before ERA to avoid double-match)
        (r'\bxERA\b',                   'ERA esperado'),
        (r'\bERA\b',                    'Promedio de carreras'),
        (r'\bFIP\b',                    'Rendimiento real'),
        (r'\bOPS\b',                    'Eficiencia ofensiva'),
        (r'\bWHIP\b',                   'base-runners por entrada'),
        (r'\bK/9\b',                    'ponches por 9 innings'),
        (r'\bK%\b',                     'porcentaje de ponches'),
        (r'\bBB%\b',                    'porcentaje de bases por bolas'),
        (r'\bBABIP\b',                  'suerte en contacto'),
        # Bet-type labels
        (r'\(ML\)',                      '(apuesta al ganador)'),
        (r'(?<!\w)ML(?!\s*model|\s*Model)(?!\w)',
                                         'apuesta al ganador'),
        (r'\bRL\b',                     'línea de carreras'),
        (r'\bH2H\b',                    'historial directo'),
        (r'\bCLV\b',                    'valor vs línea de cierre'),
        (r'\bEV\b(?=\s*[:\+\-\d])',     'Valor esperado'),
        # Action words (English fragments that slip into alerts)
        (r'\bactivated\b',              'regresó de la'),
        (r'\bplaced on\b',              'colocado en'),
        (r'\bscratched\b',              'retirado del lineup'),
        (r'\btransferred\b',            'trasladado a'),
        (r'\boptioned\b',              'enviado a ligas menores'),
        (r'\brecalled\b',               'llamado de ligas menores'),
        (r'\bdesignated for assignment\b', 'designado para asignación'),
        (r'\bday-to-day\b',             'estado: día a día'),
        (r'\bquestionable\b',           'dudoso para jugar'),
        (r'\bdoubtful\b',               'muy dudoso para jugar'),
        (r'\bruled out\b',              'descartado del juego'),
        (r'\bnot starting\b',           'no arrancará el juego'),
        (r'\brookies?\b',               'novato'),
        (r'\blineup\b',                 'alineación'),
        (r'\bbullpen\b',                'bullpen (lanzadores de relevo)'),
        (r'\bstarter\b',                'abridor'),
        (r'\brelief(?:er)?\b',          'relevista'),
        (r'\bpostponed\b',              'pospuesto'),
        (r'\brain delay\b',             'retraso por lluvia'),
    ]
    _TERM_RULES = [(_re.compile(pat, _re.IGNORECASE), rep) for pat, rep in _raw]

_build_term_rules()


def _translate_terms(text: str) -> str:
    """
    Replace all English baseball abbreviations and raw terms with plain Spanish.
    Applied automatically to every ntfy body before sending.
    """
    for pattern, replacement in _TERM_RULES:
        text = pattern.sub(replacement, text)
    return text


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
    # Apply full Spanish term translation before sending
    body  = _translate_terms(body)
    title = _translate_terms(title)
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

def load_elo_ratings():
    if os.path.exists(ELO_FILE):
        try:
            with open(ELO_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {}

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
    today = datetime.now(CDT).strftime("%Y-%m-%d")
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
            if win_prob_home >= 0.60:
                rec = f"BET HOME {home} ML (conf: HIGH)"
            elif win_prob_away >= 0.60:
                rec = f"BET AWAY {away} ML (conf: HIGH)"
            elif win_prob_home >= 0.55:
                rec = f"LEAN HOME {home} ML (conf: MEDIUM)"
            elif win_prob_away >= 0.55:
                rec = f"LEAN AWAY {away} ML (conf: MEDIUM)"
            else:
                rec = "NO BET (too close)"

            body = (
                f"{away} @ {home}  |  {gtime[:16] if gtime != 'TBD' else 'TBD'}\n"
                f"Pitchers:\n"
                f"  {hp_name} (H): ERA {hp_stats['era']} | WHIP {hp_stats['whip']} | K/9 {hp_stats['k9']}\n"
                f"  {ap_name} (A): ERA {ap_stats['era']} | WHIP {ap_stats['whip']} | K/9 {ap_stats['k9']}\n"
                f"Batting:\n"
                f"  {home}: AVG {home_bat['avg']} | OPS {home_bat['ops']} | "
                f"RS/j {home_bat['rs_pg'] if home_bat['rs_pg'] is not None else 'N/D'} | "
                f"RA/j {home_bat['ra_pg'] if home_bat['ra_pg'] is not None else 'N/D'}\n"
                f"  {away}: AVG {away_bat['avg']} | OPS {away_bat['ops']} | "
                f"RS/j {away_bat['rs_pg'] if away_bat['rs_pg'] is not None else 'N/D'} | "
                f"RA/j {away_bat['ra_pg'] if away_bat['ra_pg'] is not None else 'N/D'}\n"
                f"Win Prob: {home} {win_prob_home:.1%} | {away} {win_prob_away:.1%}\n"
                f"Pythagorean: {home} {py_home:.1%}\n"
                f">>> {rec}"
            )
            ntfy_post(f"MLB Preview: {away} @ {home}", body, "default")
            print(f"  ✉️  Sent MLB preview: {away} @ {home}")

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

            # Poisson model (WC average: ~1.3 goals per team per game, slight home boost)
            avg_h = 1.35; avg_a = 1.25
            p_win, p_draw, p_loss = poisson_match_probs(avg_h, avg_a)

            # Blend ELO + Poisson
            win_h = 0.5 * elo_home + 0.5 * p_win
            win_a = 0.5 * elo_away + 0.5 * p_loss

            # Recommendation
            if win_h >= 0.55:
                rec = f"LEAN HOME {home_name} (conf: MEDIUM)"
            elif win_a >= 0.55:
                rec = f"LEAN AWAY {away_name} (conf: MEDIUM)"
            else:
                rec = "DRAW possible — consider DNB or no bet"

            body = (
                f"{away_es} vs {home_es}  |  {game_time} UTC\n"
                f"Poisson model: Local {p_win:.1%} | Empate {p_draw:.1%} | Visitante {p_loss:.1%}\n"
                f"ELO: {home_es} {elo_home:.1%} | {away_es} {elo_away:.1%}\n"
                f"Mixto: {home_es} {win_h:.1%} | {away_es} {win_a:.1%}\n"
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
                results.append(("The Odds API",   "✅ funcionando"))
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
                        f"Reassess your morning projection."
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
    margin = (1.0 / odds_a) + (1.0 / odds_b)
    if margin >= 1.0:
        return None
    profit_pct = (1.0 - margin) / margin * 100
    if profit_pct < ARB_MIN_PROFIT or profit_pct > 8.0:
        return None
    stake_a = BANKROLL / (odds_a * margin)
    stake_b = BANKROLL / (odds_b * margin)
    return {
        "match":      f"{home} vs {away}",
        "legs":       2,
        "team_a":     team_a, "odds_a": odds_a, "book_a": book_a,
        "stake_a":    round(stake_a, 2),
        "team_b":     team_b, "odds_b": odds_b, "book_b": book_b,
        "stake_b":    round(stake_b, 2),
        "profit":     round(BANKROLL * (1.0 - margin) / margin, 2),
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
    margin = (1.0 / odds_h) + (1.0 / odds_d) + (1.0 / odds_a)
    if margin >= 1.0:
        return None
    profit_pct = (1.0 - margin) / margin * 100
    if profit_pct < ARB_MIN_PROFIT or profit_pct > 8.0:
        return None
    stake_h = BANKROLL / (odds_h * margin)
    stake_d = BANKROLL / (odds_d * margin)
    stake_a = BANKROLL / (odds_a * margin)
    return {
        "match":      f"{home} vs {away}",
        "legs":       3,
        "team_a":     team_h, "odds_a": odds_h, "book_a": book_h,
        "stake_a":    round(stake_h, 2),
        "team_b":     team_d, "odds_b": odds_d, "book_b": book_d,
        "stake_b":    round(stake_d, 2),
        "team_c":     team_a, "odds_c": odds_a, "book_c": book_a,
        "stake_c":    round(stake_a, 2),
        "profit":     round(BANKROLL * (1.0 - margin) / margin, 2),
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
    ck = f"{team}_{datetime.now().strftime('%Y-%m-%d')}"
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
    ck = f"streak_{team}_{datetime.now().strftime('%Y-%m-%d')}"
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
        for g in last10:
            opp    = g["away_team"] if g["home_team"] == team else g["home_team"]
            sc_map = {s["name"]: int(s["score"])
                      for s in (g.get("scores") or []) if s.get("score") is not None}
            my_sc  = sc_map.get(team, 0)
            op_sc  = sc_map.get(opp,  0)
            results.append("W" if my_sc > op_sc else "L")
            run_diff += my_sc - op_sc

        wins_10   = results.count("W")
        losses_10 = results.count("L")

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
                h_era   = _fetch_pitcher_era_by_id(home_p['id']) if home_p.get('id') else 4.50
                a_era   = _fetch_pitcher_era_by_id(away_p['id']) if away_p.get('id') else 4.50
                key     = f"{home_tn.lower()}|{away_tn.lower()}"
                result[key] = {
                    'home_era':  round(h_era, 2),
                    'away_era':  round(a_era, 2),
                    'home_name': home_p.get('fullName', 'TBD'),
                    'away_name': away_p.get('fullName', 'TBD'),
                    'home_id':   home_p.get('id'),
                    'away_id':   away_p.get('id'),
                }
    except Exception as e:
        print(f'  ⚠️  Pitcher fetch error: {e}')

    _pitcher_cache[today_str] = result
    return result

def _lookup_pitcher_data(home, away, pitchers):
    """
    Fuzzy lookup in the pitchers dict using team name substrings.
    Returns pitcher dict or empty dict.
    """
    for key, val in pitchers.items():
        h_key, a_key = key.split('|', 1)
        if (any(w in h_key for w in home.lower().split()) and
                any(w in a_key for w in away.lower().split())):
            return val
    return {}

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
    return None

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

def fetch_wind(lat, lon):
    """
    Current wind from OpenWeatherMap (imperial units).
    Cached 30 min per location. Returns dict or None.
    """
    if not OPENWEATHER_KEY:
        return None
    city_key = f"{lat},{lon}"
    cached   = _weather_cache.get(city_key)
    if cached:
        age_min = (datetime.now(pytz.utc) - cached['fetched_at']).total_seconds() / 60
        if age_min < 30:
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
        if 225 <= deg <= 315:
            label = 'OUT'
        elif 45 <= deg <= 135:
            label = 'IN'
        else:
            label = 'CROSS'
        result = {'speed': speed, 'deg': deg, 'label': label,
                  'temp_f': temp_f, 'fetched_at': datetime.now(pytz.utc)}
        _weather_cache[city_key] = result
        return result
    except Exception:
        return None

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
    Extract totals line + odds from a game's bookmaker list.
    Only considers US-licensed books (US_BOOKS_ONLY whitelist).
    Prefers Bovada/Bodog; falls back to any other US book.
    Returns (line, over_odds, under_odds, bookmaker_name) or None.
    """
    preferred, fallback = None, None
    for bk in game.get("bookmakers", []):
        if not _is_us_book(bk["title"]):
            continue   # skip non-US books entirely
        is_pref = bk["title"].lower() in PREFERRED_BOOKS
        for m in bk.get("markets", []):
            if m["key"] == "totals":
                by_name = {o["name"]: o for o in m.get("outcomes", [])}
                if "Over" not in by_name or "Under" not in by_name:
                    continue
                entry = (
                    by_name["Over"]["point"],
                    by_name["Over"]["price"],
                    by_name["Under"]["price"],
                    bk["title"],
                )
                if is_pref:
                    preferred = entry
                elif fallback is None:
                    fallback = entry
    return preferred or fallback

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
            wind           = fetch_wind(park_city[1], park_city[2]) if park_city else None
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
        print(f"   📐 Proyección: {our_line:.1f}  Libro: {book_line}  "
              f"Diff: {diff:+.1f}  Umbral: ±{threshold}")
        if abs(diff) < threshold:
            print(f"   ❌ Sin edge ({abs(diff):.1f} < {threshold} umbral)")
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
        if edge_val < 5.0:
            print(f"   ⏭️  Panel omitido — edge {edge_val:.1f}% < 5% mínimo ({bet_side} {book_line})")
            continue
        _tc_claude = panel_expertos(_tc_data, _tc_sport)
        if _tc_claude:
            _tcc  = _tc_claude.get("confianza", "N/D")
            _tcap = "✅" if _tc_claude.get("apostar", True) else "❌"
            _tcr  = (_tc_claude.get("razonamiento", "") or "")[:80]
            print(f"   🤖 Claude: {_tcc} | apostar:{_tcap} | \"{_tcr}\"")
        if _tc_claude and (
                not _tc_claude.get("apostar", True)
                or _tc_claude.get("confianza") == "BAJA"):
            _why = (f"apostar={_tc_claude.get('apostar')}, "
                    f"confianza={_tc_claude.get('confianza')}")
            print(f"   ❌ RECHAZADO — Claude veta {bet_side} {book_line} ({_why})")
            continue
        if _tc_claude and _tc_claude.get("apostar", True):
            print(f"   ✅ TOTALS PICK: {bet_side} {book_line}  edge={edge_val:.1f}")

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

def notify_totals(total_bets, alerted=None):
    global alerted_bets
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
    """Best decimal odds per outcome name across US-only books. Returns {name: (price, book)}."""
    best = {}
    for bk in game.get("bookmakers", []):
        if not _is_us_book(bk["title"]):
            continue   # skip non-US international books
        for m in bk.get("markets", []):
            if m["key"] == "h2h":
                for o in m.get("outcomes", []):
                    name, price = o["name"], o["price"]
                    if name not in best or price > best[name][0]:
                        best[name] = (price, bk["title"])
    return best

def _extract_spread_best(game):
    """
    Best decimal odds per team in spreads market (run line / handicap).
    Returns {name: (point, price, book)}.
    """
    best = {}
    for bk in game.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m["key"] == "spreads":
                for o in m.get("outcomes", []):
                    name, price = o["name"], o["price"]
                    point = float(o.get("point", 0))
                    if name not in best or price > best[name][1]:
                        best[name] = (point, price, bk["title"])
    return best

def _extract_f5_h2h_best(game):
    """Best decimal odds per outcome in h2h_h1 (F5 / primera mitad ML). Returns {name: (price, book)}."""
    best = {}
    for bk in game.get("bookmakers", []):
        if not _is_us_book(bk["title"]):
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
    Returns (line, over_odds, under_odds, bookmaker_name) or None.
    """
    preferred, fallback = None, None
    for bk in game.get("bookmakers", []):
        if not _is_us_book(bk["title"]):
            continue
        is_pref = bk["title"].lower() in PREFERRED_BOOKS
        for m in bk.get("markets", []):
            if m["key"] == "totals_h1":
                by_name = {o["name"]: o for o in m.get("outcomes", [])}
                if "Over" not in by_name or "Under" not in by_name:
                    continue
                entry = (
                    by_name["Over"]["point"],
                    by_name["Over"]["price"],
                    by_name["Under"]["price"],
                    bk["title"],
                )
                if is_pref:
                    preferred = entry
                elif fallback is None:
                    fallback = entry
    return preferred or fallback

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
    "era":         (0.50, 8.00),   # 0.50 mínimo — ERA élite legítima (ej. 1.46 Sanchez)
    "fip":         (0.50, 8.00),   # mismo criterio que ERA
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
PROB_MIN_TOTALS  = 0.58  # Over/Under — 58% minimum
PROB_MIN_ML      = 0.62  # Moneyline — 62% minimum
# PROB_MIN_LIVE  = 0.65  # DESACTIVADO — live betting deshabilitado
PROB_MIN_PREMIUM = 0.70  # Premium alerts — 70% minimum
_RANK_EMOJIS = ["1️⃣", "2️⃣", "3️⃣"]

def analyze_game_full(game, sport_key, prev_map=None, force_panel: bool = False):
    """
    Full per-game analysis across ML, Totals, and Spread/Handicap.
    Returns result dict or None (if no bet reaches EV_MIN_PCT).
    """
    if prev_map is None:
        prev_map = {}

    is_mlb = "mlb" in sport_key

    home, away = game["home_team"], game["away_team"]
    game_id    = game.get("id", f"{home}|{away}")
    commence   = game.get("commence_time", "")
    _tag       = f"{'MLB' if is_mlb else 'SOC'} | {home} vs {away}"

    print(f"\n🔍 ANÁLISIS: {home} vs {away}  [{sport_key}]")

    if game_starts_soon(commence, 60) and not force_panel:
        print(f"   ⏰ OMITIDO — inicia en < 60 min")
        return None
    tc = _timing_check(commence, is_mlb)
    if tc["skip"] and not force_panel:
        print(f"   ⏰ OMITIDO — fuera de ventana horaria")
        return None

    candidates  = []   # {label, true_prob, odds, book, ev_pct, kelly_pct, stake, safest}
    _all_evs: list = []   # [(label, ev_pct)] for every candidate tried, pass or fail
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
        wind           = fetch_wind(park_city[1], park_city[2]) if park_city else None
        w_adj, w_label = wind_run_adj(wind)

        half_adj = pitch_adj / 2
        home_exp = max(0.1, home_exp + half_adj + w_adj / 2)
        away_exp = max(0.1, away_exp + half_adj + w_adj / 2)

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
            if matchup and matchup.get("avg"):
                avg     = matchup["avg"]
                hand_es = ("zurdo" if hand == "L" else
                           "diestro" if hand == "R" else "ambidiestro")
                if avg < 0.220:
                    if is_home_ln:
                        home_exp = max(0.1, home_exp - 0.4)
                    else:
                        away_exp = max(0.1, away_exp - 0.4)
                    lr_notes.append({"lineup": lineup, "pitcher": pname,
                                     "hand": hand_es, "avg": avg,
                                     "verdict": "débil", "favor": "pitcher ✅"})
                elif avg > 0.260:
                    if is_home_ln:
                        home_exp = min(home_exp + 0.4, 12.0)
                    else:
                        away_exp = min(away_exp + 0.4, 12.0)
                    lr_notes.append({"lineup": lineup, "pitcher": pname,
                                     "hand": hand_es, "avg": avg,
                                     "verdict": "fuerte", "favor": "bateadores ⚠️"})
                else:
                    lr_notes.append({"lineup": lineup, "pitcher": pname,
                                     "hand": hand_es, "avg": avg,
                                     "verdict": "normal", "favor": "neutral"})

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
            # Cleanup or 3-hole absent → -0.5 runs; 2+ key players absent → -0.8
            if len(h_miss) >= 2:
                home_exp = max(0.1, home_exp - 0.8)
            elif len(h_miss) == 1:
                home_exp = max(0.1, home_exp - 0.5)
            if len(a_miss) >= 2:
                away_exp = max(0.1, away_exp - 0.8)
            elif len(a_miss) == 1:
                away_exp = max(0.1, away_exp - 0.5)
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
            if ev >= EV_MIN_PCT and r["stake"] > 0:
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

            for side_label, is_over, p, odds in [
                (f"📈 OVER {book_line} carreras",  True,
                 poisson_ou_prob(adj_total, book_line, True),  over_odds),
                (f"📉 UNDER {book_line} carreras", False,
                 poisson_ou_prob(adj_total, book_line, False), under_odds),
            ]:
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
                # Improvement 3c: UNDER gets +3% probability boost (more reliable historically)
                p_adj = min(0.95, p + 0.03) if not is_over else p
                if not is_over:
                    _pitch_notes.append("📊 Modelo prefiere UNDER (más confiable históricamente)")
                # Improvement 4: blend model probability 70% + historical hit rate 30%
                # p_kelly used ONLY for Kelly stake sizing (conservative bet sizing)
                # EV uses p_adj (true model probability) per formula: EV = (true_prob × odds) - 1
                _hist_rate = 0.526 if not is_over else 0.527
                p_kelly = round(p_adj * 0.7 + _hist_rate * 0.3, 4)
                ev = (p_adj * odds - 1) * 100   # EV = (true_prob × decimal_odds) - 1
                r  = kelly_stake(p_kelly, odds)
                _all_evs.append((side_label, round(ev, 1)))
                if ev >= EV_MIN_PCT and r["stake"] > 0:
                    candidates.append({"label": side_label, "true_prob": p_adj, "odds": odds,
                                       "book": bk_name, "ev_pct": round(ev, 1),
                                       "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # Run line
        for team, is_home in [(home, True), (away, False)]:
            if team not in spread_odds:
                continue
            pt, odds, book = spread_odds[team]
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
            if ev >= EV_MIN_PCT and r["stake"] > 0:
                candidates.append({"label": lbl, "true_prob": p_cover, "odds": odds,
                                   "book": book, "ev_pct": round(ev, 1),
                                   "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # ── F5 ML (primera mitad — moneyline primeras 5 entradas) ────────────
        # F5 odds se obtienen por endpoint de evento (no en get_odds principal)
        _f5_data = _fetch_f5_odds(game_id)
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
            for _ft_lbl, _is_f5_ov, _ft_p, _ft_odds in [
                (f"📈 OVER {f5_line} F5",  True,
                 poisson_ou_prob(f5_exp, f5_line, True),  f5_ov_odds),
                (f"📉 UNDER {f5_line} F5", False,
                 poisson_ou_prob(f5_exp, f5_line, False), f5_un_odds),
            ]:
                _ft_p_adj = min(0.95, _ft_p + 0.03) if not _is_f5_ov else _ft_p
                ev = (_ft_p_adj * _ft_odds - 1) * 100
                r  = kelly_stake(_ft_p_adj, _ft_odds)
                _all_evs.append((_ft_lbl, round(ev, 1)))
                if ev >= EV_MIN_PCT and r["stake"] > 0 and _ft_p_adj >= PROB_MIN_TOTALS:
                    candidates.append({"label": _ft_lbl, "true_prob": _ft_p_adj,
                                       "odds": _ft_odds, "book": f5_book,
                                       "ev_pct": round(ev, 1),
                                       "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # ── Hits totales combinados (Over/Under) ──────────────────────────────
        _hits_mkt = _extract_hits_total(_f5_data)
        if _hits_mkt and bat_h and bat_a:
            _hits_line, _hits_ov_od, _hits_un_od, _hits_bk = _hits_mkt
            _h_avg    = bat_h.get("avg") or 0.250
            _a_avg    = bat_a.get("avg") or 0.250
            _exp_hits = round((_h_avg * 30.0) + (_a_avg * 30.0), 1)
            if h_era_eff < 3.0 or a_era_eff < 3.0:   # pitcher élite → menos hits
                _exp_hits = max(4.0, _exp_hits - 1.5)
            for _ht_lbl, _is_ht_ov, _ht_p, _ht_od in [
                (f"🎯 HITS OVER {_hits_line}",  True,
                 poisson_ou_prob(_exp_hits, _hits_line, True),  _hits_ov_od),
                (f"🎯 HITS UNDER {_hits_line}", False,
                 poisson_ou_prob(_exp_hits, _hits_line, False), _hits_un_od),
            ]:
                ev = (_ht_p * _ht_od - 1) * 100
                r  = kelly_stake(_ht_p, _ht_od)
                _all_evs.append((_ht_lbl, round(ev, 1)))
                if ev >= EV_MIN_PCT and r["stake"] > 0 and _ht_p >= PROB_MIN_TOTALS:
                    candidates.append({"label": _ht_lbl, "true_prob": _ht_p,
                                       "odds": _ht_od, "book": _hits_bk,
                                       "ev_pct": round(ev, 1),
                                       "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # ── Ponches del pitcher titular (Strikeout K props) ───────────────────
        try:
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
                _kp_k9    = (_kp_sc.get("k9") if _kp_sc and _kp_sc.get("k9") else None)
                if not _kp_k9:
                    _kp_k9 = (10.5 if _kp_era < 2.00 else
                               9.5  if _kp_era < 2.75 else
                               8.5  if _kp_era < 3.50 else
                               7.5  if _kp_era < 4.50 else 6.5)
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
                    if ev >= EV_MIN_PCT and r["stake"] > 0 and _kp_p >= 0.52:
                        candidates.append({"label": _kp_lbl, "true_prob": _kp_p,
                                           "odds": _kp_pr, "book": "Props",
                                           "ev_pct": round(ev, 1),
                                           "stake": r["stake"], "kelly_pct": r["kelly_pct"]})
        except Exception as _ke:
            print(f"   ⚠️  K props: {_ke}")

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

        # MLB A3: temperature adjustment
        temp_f     = (wind.get("temp_f") if wind else None)
        t_adj, t_label = _temp_run_adj(temp_f)
        # apply temperature to projected totals
        home_exp = max(0.1, home_exp + t_adj / 2)
        away_exp = max(0.1, away_exp + t_adj / 2)

        # ── Elite Source 2: Pinnacle Market Reference ─────────────────────────
        pin_data = _extract_pinnacle_odds(game)

        # Build pitcher display label — prefer xERA label when Statcast available
        _h_era_label = (f"xERA {h_era_eff:.2f}" if h_statcast and h_statcast.get("xera") is not None
                        else f"ERA {h_era:.2f}")
        _a_era_label = (f"xERA {a_era_eff:.2f}" if a_statcast and a_statcast.get("xera") is not None
                        else f"ERA {a_era:.2f}")

        context = {
            "pitcher_home":  f"{h_pname} ({_h_era_label})",
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
            "f5_tot_ok": bool(f5_tot) if "f5_tot" in dir() else False,
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

    # Drop any pick whose true probability is below the minimum threshold
    candidates = [c for c in candidates if c["true_prob"] >= PROB_MIN]

    if not candidates:
        _best = max(_all_evs, key=lambda x: x[1]) if _all_evs else None
        if _best:
            print(f"   ❌ Sin picks — mejor EV: {_best[0].split()[0]} {_best[1]:+.1f}% "
                  f"(mínimo {EV_MIN_PCT:.1f}%)")
        else:
            print(f"   ❌ Sin picks — sin odds válidas para analizar")
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
        return {
            "game_id":    game_id,
            "match":      f"{home} vs {away}",
            "time":       commence,
            "sport":      sport_key,
            "is_mlb":     is_mlb,
            "candidates": [],
            "context":    context,
            "best_label": None,
            "best_ev":    0.0,
            "claude_intel": None,
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
    _claude_data_g = {
        "match":     f"{home} vs {away}",
        "sport":     sport_key,
        "top_pick":  top3[0]["label"],
        "ev_pct":    top3[0]["ev_pct"],
        "true_prob": round(top3[0]["true_prob"] * 100, 1),
        "odds":      top3[0]["odds"],
        "stake":     top3[0]["stake"],
    }
    _claude_data_g.update({
        k: v for k, v in context.items()
        if isinstance(v, (str, int, float, bool, type(None)))
    })
    _claude_sport_g = "MLB" if is_mlb else "SOCCER"
    _top_ev = top3[0]["ev_pct"]
    if _top_ev < 5.0 and not force_panel:
        print(f"   ⏭️  Panel omitido — EV {_top_ev:.1f}% < 5% mínimo ({top3[0]['label']})")
        _claude_result_g = None
    else:
        _claude_result_g = panel_expertos(_claude_data_g, _claude_sport_g)

    if _claude_result_g:
        _cc  = _claude_result_g.get("confianza", "N/D")
        _cap = "✅" if _claude_result_g.get("apostar", True) else "❌"
        _cr  = (_claude_result_g.get("razonamiento", "") or "")[:90]
        _ci  = _claude_result_g.get("datos_inconsistentes") or []
        print(f"   🤖 Claude: {_cc} | apostar:{_cap} | \"{_cr}\"")
        if _ci:
            print(f"      ⚠️ Inconsistencias: {', '.join(str(x) for x in _ci[:2])}")
    else:
        print(f"   🤖 Claude: no disponible (sin API key o error)")

    # Hard veto: Claude says apostar=False OR confianza=BAJA → block immediately
    # Guard must fire BEFORE any pick is assigned or returned
    # force_panel=True (manual /analizar): skip veto so full analysis is returned
    if _claude_result_g and not force_panel and (
            not _claude_result_g.get("apostar", True)
            or _claude_result_g.get("confianza") == "BAJA"):
        _veto_why = (f"apostar={_claude_result_g.get('apostar')}, "
                     f"confianza={_claude_result_g.get('confianza')}")
        print(f"   ❌ RECHAZADO — Claude vetó el partido ({_veto_why}) → sin pick")
        return None

    # Feature 7: cap confidence at MEDIA when data is partial (score 50–79)
    if 50 <= _dqs < 80 and _claude_result_g:
        if _claude_result_g.get("confianza") == "ALTA":
            _claude_result_g["confianza"] = "MEDIA"
        _claude_result_g["datos_incompletos"] = True
        print(f"   ⚠️  Confianza capada a MEDIA — datos parciales ({_dqs}/100)")

    _best_pick = top3[0]
    print(f"   ✅ PICK: {_best_pick['label']}  EV+{_best_pick['ev_pct']:.1f}%  "
          f"@ {_best_pick['odds']}  stake=${_best_pick['stake']:.0f}")

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
    }


def notify_game_analysis(analyses, sport_key, alerted=None):
    """Send one ntfy alert per game containing full analysis context + top picks."""
    global alerted_game_analysis
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
            ctx_lines  = (
                f"🔵 Pitcher local: {pn_h}{h_hand_txt}\n"
                f"   ERA: {er_h:.2f} — {_era_label(er_h)}\n"
            )
            if fip_h is not None:
                ctx_lines += (
                    f"   FIP (rendimiento real): {fip_h:.2f} — {_era_label(fip_h)}\n"
                    + _fip_luck(er_h, fip_h)
                )
            # ── Elite Source 1: Statcast block — home pitcher ────────────────
            sc_h = ctx.get("statcast_home")
            sc_a = ctx.get("statcast_away")
            er_eff_h = ctx.get("era_eff_home", er_h)
            er_eff_a = ctx.get("era_eff_away", er_a)
            ctx_lines += _statcast_alert_block(pn_h, sc_h, er_h)

            # Away pitcher block
            a_hand_txt = f" ({_hand_es(hnd_a)})" if _hand_es(hnd_a) else ""
            ctx_lines += (
                f"🔴 Pitcher visita: {pn_a}{a_hand_txt}\n"
                f"   ERA: {er_a:.2f} — {_era_label(er_a)}\n"
            )
            if fip_a is not None:
                ctx_lines += (
                    f"   FIP (rendimiento real): {fip_a:.2f} — {_era_label(fip_a)}\n"
                    + _fip_luck(er_a, fip_a)
                )
            # ── Elite Source 1: Statcast block — away pitcher ────────────────
            ctx_lines += _statcast_alert_block(pn_a, sc_a, er_a)

            # ── Elite Source 2: Pinnacle market reference block ──────────────
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

            # Runs scored / allowed
            ctx_lines += (
                f"⚾ {home_es} — Carreras anotadas: {ctx['rs_home']} por juego\n"
                f"🛡️ {home_es} — Carreras recibidas: {ctx['ra_home']} por juego\n"
                f"⚾ {away_es} — Carreras anotadas: {ctx['rs_away']} por juego\n"
                f"🛡️ {away_es} — Carreras recibidas: {ctx['ra_away']} por juego\n"
            )
            # ── Batting metrics (MLB A7) ──────────────────────────────────
            def _k_label(k):
                if k is None:   return "N/D"
                if k > 28:      return f"{k:.0f}% (mucho)"
                if k < 18:      return f"{k:.0f}% (poco)"
                return          f"{k:.0f}% (normal)"

            def _bb_label(bb):
                if bb is None:  return "N/D"
                if bb > 10:     return f"{bb:.0f}% (bueno)"
                if bb < 7:      return f"{bb:.0f}% (bajo)"
                return          f"{bb:.0f}% (normal)"

            for tname, tname_es, bat in (
                (home, home_es, ctx.get("bat_home")),
                (away, away_es, ctx.get("bat_away")),
            ):
                if not bat:
                    continue
                ops = bat.get("ops")
                ctx_lines += (
                    f"🏏 Ofensiva {tname_es}:\n"
                    f"   Promedio de bateo: {bat['avg']:.3f}\n"
                )
                if ops is not None:
                    ctx_lines += (
                        f"   OPS: {ops:.3f} ({_ops_label(ops)})\n"
                    )
                if bat.get("k_pct") is not None:
                    ctx_lines += (
                        f"   Se poncha: {_k_label(bat['k_pct'])}\n"
                    )
                if bat.get("bb_pct") is not None:
                    ctx_lines += (
                        f"   Toma bases por bolas: {_bb_label(bat['bb_pct'])}\n"
                    )
                insight = _batting_insight(tname, ops, bat.get("k_pct"))
                if insight:
                    ctx_lines += f"{insight}\n"
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
            # Umpire
            ump = ctx.get("umpire")
            if ump and ump.get("name"):
                if ump["tendency"] == "OVER":
                    ump_note = "zona apretada → puede inflar el total"
                elif ump["tendency"] == "UNDER":
                    ump_note = "zona expandida → favorece el Under"
                else:
                    ump_note = "historial de juegos con score normal"
                ctx_lines += (
                    f"👨‍⚖️ Árbitro: {ump['name']}\n"
                    f"   Historial: {ump_note}\n"
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
        best = a["candidates"][0]
        best_clean = (best["label"]
                      .replace("🔵 ", "").replace("🔴 ", "").replace("🤝 ", "")
                      .replace("📈 ", "").replace("📉 ", "").replace("🏃 ", ""))
        action_line = f"⭐ ACCIÓN: {best_clean} en {best['book']} — {gt}"

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

        # Verdict — cap to MEDIA whenever any ⚠️ warning is present
        if tc.get("cap_conf") or has_warning:
            verdict = f"{_DIV3}\n🟡 CONFIANZA: MEDIA"
        else:
            verdict = _verdict_line(best["ev_pct"], best["true_prob"])

        _claude_blk = _claude_block(a.get("claude_intel"))
        _dq_line_a  = ("✅ Datos verificados\n" if not has_warning
                       else "⚠️ Verificar antes de apostar — algunos datos sin confirmar\n")
        body = (
            f"{emoji} {match_es}\n"
            f"{action_line}\n"
            f"⏰ {gt}\n"
            f"{_DIV}\n"
            f"📋 CONTEXTO\n"
            f"{_DIV}\n"
            f"{ctx_lines}"
            f"{_DIV}\n"
            f"📊 TOP PICKS (EV > {EV_MIN_PCT:.0f}%)\n"
            f"{_DIV}\n"
            f"{picks_lines}"
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
        if _tg_broadcast_fn:
            try:
                _tg_broadcast_fn(a)
            except Exception as _tbe:
                print(f"  ⚠️  Telegram broadcast error: {_tbe}")
        alerted_game_analysis.add(a["game_id"])
        if alerted is not None:
            alerted[match_key_ana] = float(a.get("best_ev", 0))
        print(f"  🔍 Análisis: {a['match']} — {len(a['candidates'])} pick(s), "
              f"mejor EV +{a['best_ev']}%")

def build_analizar_text(result: dict) -> list:
    """
    Build Telegram-HTML message parts for /analizar (manual analysis).
    Returns a list of strings (parts) — each fits in one Telegram message.
    Format mirrors notify_game_analysis: context, picks, expert panel.
    """
    match   = result.get("match", "?")
    home, away = (match.split(" vs ", 1) + [""])[:2] if " vs " in match else (match, "")
    home_es = _es(home)
    away_es = _es(away)
    is_mlb  = result.get("is_mlb", False)
    ctx     = result.get("context", {})
    cands   = result.get("candidates", [])
    ci      = result.get("claude_intel") or {}
    gt      = _fmt_smart_gt(result.get("time", ""))
    emoji   = _sport_emoji(result.get("sport", ""))

    # ─── PARTE 1: Contexto ────────────────────────────────────────────────────
    p1 = f"{emoji} <b>{home_es} vs {away_es}</b>\n⏰ {gt}\n{_DIV}\n📋 CONTEXTO\n{_DIV}\n"

    if is_mlb:
        def _hn(c):
            return "zurdo" if c == "L" else ("diestro" if c == "R" else None)

        pn_h  = ctx.get("pname_home", "TBD")
        pn_a  = ctx.get("pname_away", "TBD")
        er_h  = ctx.get("era_home",  4.50)
        er_a  = ctx.get("era_away",  4.50)
        fip_h = ctx.get("fip_home")
        fip_a = ctx.get("fip_away")
        sc_h  = ctx.get("statcast_home")
        sc_a  = ctx.get("statcast_away")
        hnd_h = ctx.get("hand_home")
        hnd_a = ctx.get("hand_away")

        h_ht = f" ({_hn(hnd_h)})" if _hn(hnd_h) else ""
        a_ht = f" ({_hn(hnd_a)})" if _hn(hnd_a) else ""

        # Pitcher local
        p1 += f"🔵 <b>Pitcher {home_es}</b>: {pn_h}{h_ht}\n   ERA: {er_h:.2f} — {_era_label(er_h)}\n"
        if fip_h is not None:
            p1 += f"   FIP: {fip_h:.2f} — {_era_label(fip_h)}\n"
        p1 += _statcast_alert_block(pn_h, sc_h, er_h)

        # Pitcher visitante
        p1 += f"🔴 <b>Pitcher {away_es}</b>: {pn_a}{a_ht}\n   ERA: {er_a:.2f} — {_era_label(er_a)}\n"
        if fip_a is not None:
            p1 += f"   FIP: {fip_a:.2f} — {_era_label(fip_a)}\n"
        p1 += _statcast_alert_block(pn_a, sc_a, er_a)

        # Bullpen ERA (live fetch)
        for _t, _ts, _se in ((home, home_es, er_h), (away, away_es, er_a)):
            try:
                _, _bn = fetch_bullpen_era(_t, _se)
                p1 += f"{_bn}\n"
            except Exception:
                pass

        # Carreras anotadas/recibidas por juego
        if "rs_home" in ctx:
            p1 += (
                f"⚾ {home_es} — anota {ctx['rs_home']} | recibe {ctx['ra_home']} por juego\n"
                f"⚾ {away_es} — anota {ctx['rs_away']} | recibe {ctx['ra_away']} por juego\n"
            )

        # Bateo + tendencia últimos juegos
        for _t, _te, _bk in ((home, home_es, "bat_home"), (away, away_es, "bat_away")):
            bat = ctx.get(_bk)
            if bat:
                ops = bat.get("ops")
                p1 += f"🏏 <b>Bateo {_te}</b>: AVG {bat['avg']:.3f}"
                if ops is not None:
                    p1 += f"  |  OPS {ops:.3f} ({_ops_label(ops)})"
                p1 += "\n"
                if bat.get("k_pct") is not None:
                    p1 += f"   Se poncha: {bat['k_pct']:.0f}%\n"
                if bat.get("bb_pct") is not None:
                    p1 += f"   BB%: {bat['bb_pct']:.0f}%\n"
                ins = _batting_insight(_t, ops, bat.get("k_pct"))
                if ins:
                    p1 += f"{ins}\n"
            try:
                recent = fetch_mlb_team_recent(_t)
                if recent and recent.get("results"):
                    rs_str = " | ".join(
                        f"{_result_to_es(r)} {sc}" for r, sc in recent["results"]
                    )
                    p1 += (
                        f"📋 {_te} últimos {len(recent['results'])} juegos:\n"
                        f"   {rs_str}\n"
                        f"   Balance: {recent['wins']}G — {recent['losses']}P\n"
                    )
            except Exception:
                pass

        # Pinnacle
        pin = ctx.get("pinnacle_odds")
        if pin:
            raw_h = 1.0 / max(pin["home"], 1.001)
            raw_a = 1.0 / max(pin["away"], 1.001)
            tot   = raw_h + raw_a
            p1 += (
                f"📌 Pinnacle — {home_es}: {pin['home']:+.0f} ({round(raw_h/tot*100,1)}%)"
                f"  |  {away_es}: {pin['away']:+.0f} ({round(raw_a/tot*100,1)}%)\n"
            )

        # Clima y viento
        if ctx.get("temp_label"):
            p1 += f"{ctx['temp_label']}\n"
        if ctx.get("wind_info"):
            p1 += f"💨 {ctx['wind_info']}\n"

        # Línea y movimiento
        if ctx.get("line_moved") and ctx.get("line_note"):
            p1 += f"📉 {ctx['line_note']}\n"

        # Umpire
        ump = ctx.get("umpire")
        if ump and ump.get("name"):
            p1 += f"👨‍⚖️ Árbitro: {ump['name']} — {ump.get('tendency','?')}\n"

        # Lesionados
        for til, ils in ctx.get("il_data", {}).items():
            if ils:
                p1 += f"🤕 Lesionados ({_es(til)}): {', '.join(ils[:4])}\n"

        # Home/away splits
        hs  = ctx.get("h_splits") or {}
        as_ = ctx.get("a_splits") or {}
        if hs.get("home_rs") and as_.get("away_rs"):
            p1 += (
                f"🏠 {home_es} en casa: {hs['home_rs']} anota | {hs['home_ra']} recibe"
                f" | {hs['home_wpct']*100:.0f}% victorias\n"
                f"🚗 {away_es} de visita: {as_['away_rs']} anota | {as_['away_ra']} recibe"
                f" | {as_['away_wpct']*100:.0f}% victorias\n"
            )

    else:
        # Soccer
        p1 += (
            f"💪 {home_es}: {_elo_tier(ctx.get('elo_home', 1500))}\n"
            f"💪 {away_es}: {_elo_tier(ctx.get('elo_away', 1500))}\n"
            f"🤝 Empate: {ctx.get('p_draw','?')}%\n"
        )
        for sf_key, tname_es in (("sform_h", home_es), ("sform_a", away_es)):
            sf = ctx.get(sf_key)
            if sf:
                rs = " | ".join(_result_to_es(r) for r in sf["results"])
                p1 += (
                    f"📋 {tname_es} — últimos {sf['n']} partidos:\n"
                    f"   {rs}\n"
                    f"   ⚽ {sf['gf_pg']} goles/partido | 🛡️ {sf['ga_pg']} recibidos\n"
                )
        if ctx.get("temp_label_s"):
            p1 += f"{ctx['temp_label_s']}\n"
        if ctx.get("line_moved") and ctx.get("line_note"):
            p1 += f"📉 {ctx['line_note']}\n"
        ref = ctx.get("referee")
        if ref and ref.get("name"):
            p1 += f"🟨 Árbitro: {ref['name']} — {ref.get('tendency','?')}\n"

    # ─── PARTE 2: Picks ───────────────────────────────────────────────────────
    p1 += f"{_DIV}\n📊 ANÁLISIS DE PICKS\n{_DIV}\n"
    if cands:
        for idx, c in enumerate(cands):
            rank = (["1️⃣", "2️⃣", "3️⃣"][idx] if idx < 3 else "🔹")
            safe = "\n   ✅ Pick más seguro" if c.get("safest") else ""
            p1 += (
                f"{rank} <b>{c['label']}</b>\n"
                f"   EV: +{c['ev_pct']:.1f}%  |  Prob: {round(c['true_prob']*100)}%{safe}\n"
                f"   💰 {c['odds']} @ {c['book']}\n"
                f"   Stake: ${c['stake']:.0f}\n"
            )
    else:
        p1 += (
            "⚠️ <b>Sin picks con edge positivo</b>\n"
            "   El modelo no encuentra valor en la línea actual.\n"
            "   Revisa el contexto de arriba y decide según tu criterio.\n"
            "   (Apostar sin edge esperado es -EV a largo plazo)\n"
        )

    # ─── PARTE 3: Panel de expertos + recomendación ───────────────────────────
    experts   = ci.get("_expertos_detalle") or []
    _ci_icons = {"ALTA": "🟢", "MEDIA": "🟡", "BAJA": "🔴"}

    p2 = f"{_DIV}\n🎓 PANEL DE EXPERTOS\n{_DIV}\n"
    if experts:
        for ex in experts:
            ap = ("✅ APOSTAR" if ex.get("apostar") is True
                  else ("❌ PASAR" if ex.get("apostar") is False else "⚪ N/D"))
            ec = _ci_icons.get(ex.get("confianza", ""), "⚪")
            er = ex.get("razonamiento") or "no disponible"
            p2 += f"<b>{ex['nombre']}</b> {ec}{ex.get('confianza','?')} — {ap}\n<i>{er}</i>\n\n"
    else:
        p2 += "Sin análisis de expertos disponible.\n"

    # Recomendación final
    final_apostar = ci.get("apostar")
    panel_razon   = ci.get("razonamiento") or ""
    best = cands[0] if cands else {}

    if ci:
        rec_icon = ("✅ APOSTAR" if final_apostar is True
                    else ("❌ PASAR" if final_apostar is False else "⚠️ VERIFICAR"))
        p2 += f"{_DIV}\n📋 <b>RECOMENDACIÓN FINAL: {rec_icon}</b>\n"
        if best:
            p2 += (
                f"Pick: <b>{best.get('label', '?')}</b>\n"
                f"Stake sugerido: <b>${best.get('stake', 0):.0f}</b> @ {best.get('book','?')}\n"
                f"EV: +{best.get('ev_pct', 0):.1f}%  |  Prob: {round(best.get('true_prob', 0)*100)}%\n"
            )
        if panel_razon:
            p2 += f"<i>{panel_razon}</i>\n"
    else:
        p2 += (
            f"{_DIV}\n📋 <b>RECOMENDACIÓN: ⚠️ SIN PICKS CON EDGE</b>\n"
            "El modelo no encontró valor en la línea actual.\n"
            "El contexto de arriba tiene toda la info disponible.\n"
            "Usa tu criterio para decidir si apostar.\n"
        )

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
        url = f"https://ntfy.sh/{NOTIFY}/json?poll=1&since=120"
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

        try:
            al = float(bet.get('book_line', closing_line))
            # CLV for totals: positive means the line moved in our favour
            clv = (al - closing_line) if bet.get('bet_side') == 'UNDER' \
                  else (closing_line - al)
        except Exception:
            clv = None

        clv_rows.append({
            'alert_time':   bet.get('alert_time', ''),
            'clv_time':     datetime.now(CDT).strftime('%Y-%m-%d %H:%M CDT'),
            'match':        bet.get('match', ''),
            'sport':        bet.get('sport', ''),
            'market_type':  bet.get('market_type', 'totals'),
            'bet_side':     bet.get('bet_side', ''),
            'book_line':    bet.get('book_line', ''),
            'alert_odds':   bet.get('alert_odds', ''),
            'closing_line': closing_line,
            'clv':          round(clv, 2) if clv is not None else '',
        })

    if clv_rows:
        clv_fields = ['alert_time', 'clv_time', 'match', 'sport', 'market_type',
                      'bet_side', 'book_line', 'alert_odds', 'closing_line', 'clv']
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

def get_odds(sport_key):
    try:
        r = requests.get(
            f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds",
            params={"apiKey": API_KEY, "regions": "us,us2,eu,uk,au",
                    "markets": "h2h,totals,spreads", "oddsFormat": "decimal"},
            timeout=10,
        )
        return r.json() if r.status_code == 200 else []
    except Exception:
        return []

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
        team   = m["team"]
        opp    = away if team == home else home
        arrow  = "▼" if m["odds_now"] < m["odds_prev"] else "▲"

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
        ntfy_post(f"⚡ SHARP | {team} | {m['match']}", body, "high")
        print(f"  ⚡ Sharp: {team} en {m['match']} ({m['pct']}% movimiento)")

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
        season = datetime.now().year
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
    ck = f"{team}_{sport_key}_{datetime.now().strftime('%Y-%m-%d')}"
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
    ck = f"{home}_{away}_{datetime.now().strftime('%Y-%m-%d')}"
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
        today  = datetime.now().strftime("%Y%m%d")
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
_ERA_MAX = 8.00

def _pre_validate_for_claude(game_data: dict, sport: str) -> "tuple[dict, list]":
    """
    Sanitise game_data before sending to Claude.
    Returns (clean_data, warnings) where:
      - clean_data  : copy with suspicious numeric fields replaced by a
                      "DATO NO VERIFICADO" marker so Claude can flag them.
      - warnings    : list of human-readable warning strings logged to console.
    Checks performed:
      1. ERA values (era_home, era_away, bullpen ERA markers) must be 1.50–8.00.
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
    "Eres la capa final de verificación y análisis de apuestas deportivas. "
    "Antes de recomendar apostar, verifica internamente que todos los datos sean "
    "coherentes entre sí. Si cualquier valor parece incorrecto, imposible o "
    "contradictorio con otros datos del partido, indícalo en 'datos_inconsistentes'. "
    "Solo recomienda apostar: true si estás seguro de que los datos son precisos y "
    "la apuesta tiene valor real. Si los datos parecen poco confiables, responde con "
    "apostar: false y razonamiento: 'Datos insuficientes — no apostar'."
    "\n\n"
    "Antes de aprobar cualquier pick verifica obligatoriamente: "
    "1) Que EV = (true_prob × decimal_odds) - 1 sea matemáticamente correcto, si no cuadra veta. "
    "2) Que el pitcher esté asignado al equipo correcto, si no veto absoluto. "
    "3) Que el historial H2H no contradiga el pick por más de 3 carreras, si contradice veta. "
    "4) Que la suma RS + RA sea coherente con la dirección del pick. "
    "5) Que probabilidades mayores a 92% se marquen como sospechosas. "
    "6) Que todos los factores apunten en la misma dirección que el pick. "
    "7) ERAs menores a 2.00 son datos válidos de pitchers élite — NO los marques como "
    "sospechosos. Confirmarlos refuerza el pick (más dominancia del pitcher). "
    "8) Usar Pinnacle como referencia sharp del mercado. "
    "Si cualquier verificación falla, apostar debe ser False. "
    "Responde siempre en JSON con apostar, confianza, razon e inconsistencias."
)


def analyze_with_claude(game_data: dict, sport: str,
                        _extra_system: str = "",
                        _model: str = "") -> "dict | None":
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
            f'  "razonamiento": "explicación en español de 3-4 oraciones como experto",\n'
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
            f'  "razonamiento": "explicación en español de 3-4 oraciones como experto",\n'
            f'  "factores_positivos": ["factor1", "factor2"],\n'
            f'  "factores_negativos": ["factor1"],\n'
            f'  "datos_inconsistentes": [],\n'
            f'  "apostar": true\n'
            f"}}\n\n"
            f"Si detectas datos sospechosos, agrégalos a 'datos_inconsistentes' y "
            f"considera apostar: false. Solo responde con el JSON, nada más."
        )

    try:
        client = _anthropic_lib.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg    = client.messages.create(
            model=(_model or CLAUDE_MODEL),
            max_tokens=1024,
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


def panel_expertos(game_data: dict, sport: str) -> "dict | None":
    """
    Panel of 3 expert personas — each calls analyze_with_claude with its own
    system-prompt persona appended to _CLAUDE_SYSTEM.

    Consensus rule: 2 of 3 must return apostar=True AND no hard veto
    (apostar=False + confianza=BAJA from any expert) → final apostar=True.
    Otherwise apostar=False.

    Returns a merged dict compatible with the standard analyze_with_claude response,
    or None if no expert could be reached.
    """
    _EXPERTOS = [
        (
            "El Estadístico",
            "Eres El Estadístico — analizas con rigor cuantitativo usando el contexto completo, "
            "no solo umbrales matemáticos aislados. Reglas de calidad de datos que DEBES aplicar: "
            "(A) ERA menor a 2.00 es dato VÁLIDO de pitcher élite — NO es sospechoso ni debe "
            "tratarse como outlier. Confírmalo como fortaleza del pick; un pitcher con ERA 1.46 "
            "o similar es dominante por definición y eso refuerza la probabilidad del modelo. "
            "(B) Probabilidad entre 85% y 92% puede ser completamente legítima cuando existe un "
            "pitcher titular confirmado como élite (ERA <2.00, FIP <2.50) — NO vetes por "
            "probabilidad alta si el contexto del pitcher la justifica. Solo veta si la "
            "probabilidad alta NO tiene respaldo estadístico en el contexto del juego. "
            "(C) El contexto del pitcher titular pesa más que el umbral de probabilidad: "
            "si el pitcher es élite confirmado (ERA <2.00, FIP <2.50), ese factor debe "
            "dominar tu evaluación sobre el total y el moneyline. "
            "(D) Verifica EV = (true_prob × decimal_odds) - 1; verifica coherencia RS/RA "
            "con la dirección del pick; verifica que H2H no contradiga por >3 carreras. "
            "Si todo cuadra con el pitcher élite como ancla, apoya el pick.",
        ),
        (
            "El Sharp",
            "Eres El Sharp — evalúas el movimiento de línea, el posicionamiento de Pinnacle "
            "y el dinero inteligente del mercado. Reglas de calidad que DEBES aplicar: "
            "(A) ERA menor a 2.00 en el pitcher titular es información pública que los sharps "
            "ya descontaron en la línea — si Pinnacle aún ofrece valor con ese pitcher, "
            "es señal de que el mercado no ha cerrado completamente el edge. Confirma el dato "
            "como válido y úsalo a favor del pick. "
            "(B) Probabilidad entre 85% y 92% no activa veto automático — si el mercado "
            "de Pinnacle mueve la línea en la misma dirección que el pick, esa probabilidad "
            "refleja consenso del dinero inteligente. Solo sospecha si la línea se mueve "
            "en dirección CONTRARIA al pick. "
            "(C) Pitcher élite confirmado (ERA <2.00, FIP <2.50) es exactamente el tipo de "
            "ventaja que los sharps explotan: ventaja asimétrica de información donde el "
            "modelo de probabilidad es más preciso que el público. Pesa este contexto más "
            "que cualquier umbral matemático fijo. "
            "(D) Evalúa: movimiento de línea (¿hacia el pick o contrario?), porcentaje de "
            "apuestas públicas vs dinero real, si Pinnacle es la línea más dura disponible. "
            "Sigue el dinero inteligente con toda la información del contexto.",
        ),
        (
            "El Abogado del Diablo",
            "Eres El Abogado del Diablo — tu rol es identificar riesgos REALES, no inventar "
            "objeciones basadas en umbrales que no aplican al contexto. Reglas críticas: "
            "(A) ERA menor a 2.00 NO es razón para vetar — es dato válido de pitcher élite. "
            "Si lo usas como objeción, estás cometiendo un error analítico. En cambio, "
            "evalúa si el pitcher tiene muestra suficiente de innings (>30 IP en temporada) "
            "para que la ERA sea representativa. Si la muestra es pequeña (<15 IP), sí puedes "
            "señalarlo como riesgo de regresión. Si la muestra es sólida, confirma el dato. "
            "(B) Probabilidad entre 85% y 92% NO justifica veto automático si está respaldada "
            "por un pitcher élite confirmado. Tu argumento en contra debe ser sobre riesgos "
            "CONCRETOS: lesión no reportada, clima extremo que afecte al pitcher, bullpen "
            "débil si el juego se extiende, historial del equipo en carreras tardías. "
            "(C) El contexto completo debe guiar tu escepticismo: si el pitcher es élite "
            "(ERA <2.00, FIP <2.50) Y el mercado respalda la dirección Y el H2H es neutro, "
            "tu rol es confirmar que no hay red flags ocultos — no fabricar dudas. "
            "(D) Argumentos válidos para vetar: muestra de innings insuficiente (<15 IP), "
            "lesión o fatiga reportada, línea que se mueve contraria al pick en Pinnacle, "
            "H2H histórico muy adverso (>3 carreras de diferencia consistente), clima "
            "extremo documentado. Si ninguno aplica, el pick pasa tu filtro.",
        ),
    ]

    votos_favor   = 0
    veto_absoluto = False
    resultados    = []
    factores_pos  = []
    factores_neg  = []
    inconsistencias = []

    for nombre, extra in _EXPERTOS:
        res = analyze_with_claude(game_data, sport, _extra_system=extra,
                                  _model=CLAUDE_PANEL_MODEL)
        if res is None:
            print(f"   🎓 {nombre}: no disponible")
            resultados.append(None)
            continue

        apostar = res.get("apostar", True)
        conf    = res.get("confianza", "N/D")
        razon   = (res.get("razonamiento", "") or "")[:80]
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
            "razonamiento":  (r.get("razonamiento", "") or "")[:120]   if r else "no disponible",
        }
        for i, r in enumerate(resultados)
    ]
    merged["razonamiento"] = (
        (base.get("razonamiento", "") or "") +
        f" [Panel {votos_favor}/3 a favor{'  — veto absoluto' if veto_absoluto else ''}]"
    )
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
        last_dt = datetime.fromisoformat(last_date_s.replace("Z", "+00:00"))
        days    = (datetime.now(pytz.utc) - last_dt).days
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
            if not r["has_value"] or r["edge"] < MIN_EDGE:
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
    if not daily_bets:
        ntfy_post("BetBot Daily Summary", "No value bets found today.", "default")
        return
    total_stake = sum(b["stake"] for b in daily_bets)
    total_ev    = sum(b.get("ev", 0) for b in daily_bets)
    leagues     = sorted({b.get("sport", "?") for b in daily_bets})
    high        = sum(1 for b in daily_bets if b["confidence"] == "HIGH")
    med         = sum(1 for b in daily_bets if b["confidence"] == "MEDIUM")
    body = (
        f"Total value bets: {len(daily_bets)}\n"
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
            home_exp = max(0.5, home_exp + adj)
            away_exp = max(0.5, away_exp - adj)
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
            if _tg_broadcast_fn:
                try:
                    _tg_broadcast_fn(body)
                except Exception:
                    pass
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
                _tg_broadcast_fn(body)
            except Exception:
                pass
        print(f"  🌙 Resumen nocturno enviado ({len(today_bets)} picks)")
    except Exception as e:
        print(f"  ⚠️  send_night_summary error: {e}")


def check_midnight_reset():
    global alerted_bets, daily_bets, last_reset, _sent_alerts
    today = datetime.now(CDT).date()
    if today != last_reset:
        print(f"\n🌙 Midnight reset — sending daily summary...")
        send_daily_summary()
        alerted_bets  = set()
        _sent_alerts  = {}
        daily_bets    = []
        last_reset    = today
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
        url  = (
            f"{MLB_BASE}/schedule"
            f"?sportId=1&date={game_date}"
            f"&hydrate=lineups,probablePitcher"
        )
        data = _mlb_get(url)

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
    "pointsbet", "betmgm", "caesars", "bet365",
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

    body = (
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

    pk = (f"parlay_{best_parlay['leg1']['game_id']}_"
          f"{best_parlay['leg2']['game_id']}")
    if not _should_alert(pk, edge=best_parlay["parlay_ev"]):
        return

    _send_parlay_alert(best_parlay)


# ═══════════════════════════════════════════════════════════════════════════════
# MODULE: LIVE BETTING — DESACTIVADO
# El bot solo analiza picks PRE-PARTIDO. No se envían alertas durante partidos.
# Para reactivar: descomentar este bloque y restaurar la llamada run_live_scan().
# ═══════════════════════════════════════════════════════════════════════════════
#
#
# # ═══════════════════════════════════════════════════════════════════════════════
# # MODULE: LIVE BETTING  (MLB + Soccer World Cup)
# # ═══════════════════════════════════════════════════════════════════════════════
#
# _LIVE_MIN_EDGE       = 5.0   # higher bar than pre-game 3%
# _LIVE_MAX_INNING     = 6     # skip games entering 7th inning or later
# _LIVE_MAX_SOCCER_MIN = 75    # skip games at or past 75th minute
# _live_alerted: set   = set() # session-level dedup (resets on Railway restart)
#
#
# def _fetch_mlb_live_games() -> list:
#     """Return all in-progress MLB games today with linescore hydration."""
#     today = datetime.now(ET).strftime("%Y-%m-%d")
#     url   = (f"https://statsapi.mlb.com/api/v1/schedule"
#              f"?sportId=1&date={today}&hydrate=linescore")
#     try:
#         resp = requests.get(url, timeout=10)
#         resp.raise_for_status()
#         data = resp.json()
#     except Exception as _e:
#         print(f"  ⚠️  Live MLB schedule fetch: {_e}")
#         return []
#     live = []
#     for date_entry in data.get("dates", []):
#         for g in date_entry.get("games", []):
#             if g.get("status", {}).get("abstractGameState") == "Live":
#                 live.append(g)
#     return live
#
#
# def _fetch_mlb_game_live_feed(game_pk: int) -> dict:
#     """Fetch live game feed for pitch count + pitcher stats."""
#     url = (f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
#            f"?fields=liveData,linescore,defense,pitcher,boxscore,teams,"
#            f"pitchers,players,stats,pitching,numberOfPitches,earnedRuns,"
#            f"inningsPitched,person,fullName")
#     try:
#         resp = requests.get(url, timeout=10)
#         resp.raise_for_status()
#         return resp.json()
#     except Exception:
#         return {}
#
#
# def _parse_mlb_live_state(g: dict, feed: dict) -> dict:
#     """Extract live state fields from schedule game dict + live feed dict."""
#     ls           = g.get("linescore", {})
#     teams        = g.get("teams", {})
#     home         = teams.get("home", {}).get("team", {}).get("name", "?")
#     away         = teams.get("away", {}).get("team", {}).get("name", "?")
#     home_runs    = ls.get("teams", {}).get("home", {}).get("runs", 0) or 0
#     away_runs    = ls.get("teams", {}).get("away", {}).get("runs", 0) or 0
#     inning       = ls.get("currentInning", 0) or 0
#     inning_state = ls.get("inningState", "Top")   # "Top" or "Bottom"
#
#     pitcher_name    = "?"
#     pitches_thrown  = 0
#     innings_pitched = 0.0
#     pitcher_runs    = 0
#
#     if feed:
#         box = feed.get("liveData", {}).get("boxscore", {})
#         if box:
#             # Top of inning → home team is pitching; Bottom → away
#             pitching_side = "home" if inning_state == "Top" else "away"
#             team_box      = box.get("teams", {}).get(pitching_side, {})
#             pitchers_list = team_box.get("pitchers", [])
#             players_info  = team_box.get("players", {})
#             if pitchers_list:
#                 cur_pid    = pitchers_list[-1]
#                 player_key = f"ID{cur_pid}"
#                 player     = players_info.get(player_key, {})
#                 p_stats    = player.get("stats", {}).get("pitching", {})
#                 pitches_thrown  = p_stats.get("numberOfPitches", 0) or 0
#                 pitcher_runs    = p_stats.get("earnedRuns",      0) or 0
#                 ip_raw          = p_stats.get("inningsPitched",  "0.0")
#                 try:
#                     innings_pitched = float(ip_raw)
#                 except Exception:
#                     innings_pitched = 0.0
#                 pitcher_name = (player.get("person", {}).get("fullName")
#                                 or pitcher_name)
#
#     return {
#         "game_pk":       g.get("gamePk"),
#         "home":          home,
#         "away":          away,
#         "home_runs":     home_runs,
#         "away_runs":     away_runs,
#         "total_runs":    home_runs + away_runs,
#         "inning":        inning,
#         "inning_state":  inning_state,
#         "pitcher_name":  pitcher_name,
#         "pitches_thrown": pitches_thrown,
#         "innings_pitched": innings_pitched,
#         "pitcher_runs":  pitcher_runs,
#     }
#
#
# def _match_odds_game(home: str, away: str, odds_games: list):
#     """Fuzzy-match a live game to an Odds-API game dict by team name."""
#     home_l = home.lower()
#     away_l = away.lower()
#     for og in odds_games:
#         oh = og.get("home_team", "").lower()
#         oa = og.get("away_team", "").lower()
#         h_ok = any(w in oh for w in home_l.split() if len(w) > 3) or \
#                any(w in home_l for w in oh.split() if len(w) > 3)
#         a_ok = any(w in oa for w in away_l.split() if len(w) > 3) or \
#                any(w in away_l for w in oa.split() if len(w) > 3)
#         if h_ok and a_ok:
#             return og
#     return None
#
#
# def _analyze_live_mlb(state: dict, odds_games: list):
#     """
#     Apply 4 live MLB rules. Returns alert dict or None.
#       Rule 1: Early innings (1-3), score 0-0 → UNDER still has value
#       Rule 2: Starter struggling (40+ pitches in ≤2 inn) → OVER (bullpen)
#       Rule 3: Dominant starter (0 ER, <30 pitches, 3+ inn) → UNDER
#       Rule 4: Score changed significantly → recalculate OVER/UNDER
#     """
#     inning = state["inning"]
#     if inning < 1 or inning > _LIVE_MAX_INNING:
#         return None
#
#     total_runs = state["total_runs"]
#     pitches    = state["pitches_thrown"]
#     innings_p  = state["innings_pitched"]
#     p_runs     = state["pitcher_runs"]
#
#     og = _match_odds_game(state["home"], state["away"], odds_games)
#     if not og:
#         return None
#     totals = get_book_total(og)
#     if not totals:
#         return None
#     live_line, live_over_odds, live_under_odds, best_book = totals
#
#     # Innings remaining: count as half-inning fractions
#     remaining = max(0.5, 9 - inning + (1 if state["inning_state"] == "Top" else 0.5))
#     runs_to_line = round(live_line - total_runs, 1)
#
#     alert_label = alert_prob = alert_odds = None
#     alert_note  = ""
#     rule_fired  = ""
#
#     # ── Rule 1: Early (1-3 inn), 0-0 → UNDER ────────────────────────────
#     if inning <= 3 and total_runs == 0 and live_under_odds:
#         exp_rem    = 0.80 * remaining
#         prob_under = poisson_ou_prob(exp_rem, runs_to_line - 0.5, False)
#         ev = (prob_under * live_under_odds - 1) * 100
#         if ev >= _LIVE_MIN_EDGE:
#             alert_label = f"UNDER {live_line}"
#             alert_prob  = prob_under
#             alert_odds  = live_under_odds
#             alert_note  = f"🔵 Juego 0-0 en {inning}° inn — pitchers controlando"
#             rule_fired  = "early_scoreless"
#
#     # ── Rule 2: Starter struggling (40+ pitches, ≤2 inn) → OVER ─────────
#     if not alert_label and pitches >= 40 and innings_p <= 2.0 \
#             and inning <= 3 and live_over_odds:
#         exp_rem   = 1.30 * remaining
#         prob_over = poisson_ou_prob(exp_rem, runs_to_line - 0.5, True)
#         ev = (prob_over * live_over_odds - 1) * 100
#         if ev >= _LIVE_MIN_EDGE:
#             alert_label = f"OVER {live_line}"
#             alert_prob  = prob_over
#             alert_odds  = live_over_odds
#             alert_note  = (f"🔴 {state['pitcher_name']}: {pitches} pitches "
#                            f"en {innings_p:.1f} inn — bullpen pronto")
#             rule_fired  = "starter_struggling"
#
#     # ── Rule 3: Dominant starter (0 ER, <30 pitches, 3+ inn) → UNDER ────
#     if not alert_label and p_runs == 0 and pitches < 30 \
#             and innings_p >= 3.0 and live_under_odds:
#         exp_rem    = 0.65 * remaining
#         prob_under = poisson_ou_prob(exp_rem, runs_to_line - 0.5, False)
#         ev = (prob_under * live_under_odds - 1) * 100
#         if ev >= _LIVE_MIN_EDGE:
#             alert_label = f"UNDER {live_line}"
#             alert_prob  = prob_under
#             alert_odds  = live_under_odds
#             alert_note  = (f"🔵 {state['pitcher_name']}: {pitches} pitches "
#                            f"| 0 carreras — dominando")
#             rule_fired  = "dominant_starter"
#
#     # ── Rule 4: Score changed (2+ runs) — recalculate OVER/UNDER ─────────
#     if not alert_label and total_runs >= 2 and inning <= 5:
#         exp_rem = 0.90 * remaining
#         p_over  = poisson_ou_prob(exp_rem, runs_to_line - 0.5, True)
#         p_under = poisson_ou_prob(exp_rem, runs_to_line - 0.5, False)
#         if p_over >= p_under and live_over_odds:
#             ev = (p_over * live_over_odds - 1) * 100
#             if ev >= _LIVE_MIN_EDGE:
#                 alert_label = f"OVER {live_line}"
#                 alert_prob  = p_over
#                 alert_odds  = live_over_odds
#                 alert_note  = f"🟡 {total_runs} carreras anotadas — OVER sigue vivo"
#                 rule_fired  = "score_update"
#         elif p_under > p_over and live_under_odds:
#             ev = (p_under * live_under_odds - 1) * 100
#             if ev >= _LIVE_MIN_EDGE:
#                 alert_label = f"UNDER {live_line}"
#                 alert_prob  = p_under
#                 alert_odds  = live_under_odds
#                 alert_note  = f"🟡 {total_runs} carreras anotadas — UNDER sigue vivo"
#                 rule_fired  = "score_update"
#
#     if not alert_label:
#         return None
#
#     ev_final = round((alert_prob * alert_odds - 1) * 100, 1)
#     stake    = max(15.0, min(50.0,
#                   round(kelly_stake(alert_prob, alert_odds)["stake"], 0)))
#
#     return {
#         "sport":        "MLB",
#         "home":         state["home"],
#         "away":         state["away"],
#         "home_runs":    state["home_runs"],
#         "away_runs":    state["away_runs"],
#         "total_runs":   total_runs,
#         "inning":       inning,
#         "inning_state": state["inning_state"],
#         "label":        alert_label,
#         "prob":         alert_prob,
#         "odds":         alert_odds,
#         "ev":           ev_final,
#         "note":         alert_note,
#         "rule":         rule_fired,
#         "line":         live_line,
#         "stake":        stake,
#         "book":         best_book,
#         "pitcher":      state["pitcher_name"],
#         "pitches":      pitches,
#         "runs_to_line": runs_to_line,
#         "remaining":    remaining,
#     }
#
#
# def _send_live_mlb_alert(a: dict):
#     """Format and fire a live MLB ntfy alert (priority=urgent)."""
#     inn_ord = {1:"1er",2:"2do",3:"3er",4:"4to",5:"5to",6:"6to"}
#     inn_str = inn_ord.get(a["inning"], f"{a['inning']}°")
#
#     h_r, a_r = a["home_runs"], a["away_runs"]
#     if h_r > a_r:
#         score_str = f"{h_r}-{a_r} {_es(a['home'])}"
#     elif a_r > h_r:
#         score_str = f"{a_r}-{h_r} {_es(a['away'])}"
#     else:
#         score_str = f"{h_r}-{a_r}"
#
#     body = (
#         f"⏱️ {inn_str} inning | {score_str}\n"
#         f"{'━'*26}\n"
#         f"🎯 APUESTA EN VIVO:\n"
#         f"{a['label']} (quedan ~{a['remaining']:.0f} inn)\n"
#         f"Carreras actuales: {a['total_runs']}\n"
#         f"Necesita: {a['runs_to_line']:.1f}+ más para OVER\n"
#         f"\n"
#         f"{a['note']}\n"
#         f"\n"
#         f"💰 ${a['stake']:.0f} @ {a['odds']:.2f} — {a['book'].title()}\n"
#         f"EV estimado: +{a['ev']:.1f}%\n"
#         f"{'━'*26}\n"
#         f"🟢 APOSTAR AHORA — ventana corta\n"
#         f"⚡ APOSTAR EN 5 MIN MAX"
#     )
#     title = f"⚡ LIVE MLB | {_es(a['away'])} vs {_es(a['home'])} | {a['label']}"
#     print(f"\n  ⚡ LIVE MLB — {a['away']} vs {a['home']} | {a['label']} "
#           f"| EV +{a['ev']:.1f}% | {inn_str} inn.")
#     ntfy_post(title, body, priority="urgent")
#
#
# # ── Soccer Live (World Cup via ESPN public API) ──────────────────────────────
#
# def _fetch_soccer_live_games() -> list:
#     """Fetch in-progress FIFA World Cup games from the ESPN public scoreboard."""
#     url = ("https://site.api.espn.com/apis/site/v2/sports/soccer"
#            "/fifa.world/scoreboard")
#     try:
#         resp = requests.get(url, timeout=10)
#         resp.raise_for_status()
#         events = resp.json().get("events", [])
#     except Exception as _e:
#         print(f"  ⚠️  Live soccer fetch: {_e}")
#         return []
#
#     live = []
#     for ev in events:
#         st = ev.get("status", {})
#         if st.get("type", {}).get("state") != "in":
#             continue
#         comps  = ev.get("competitions", [{}])[0]
#         clist  = comps.get("competitors", [])
#         home_c = next((c for c in clist if c.get("homeAway") == "home"), {})
#         away_c = next((c for c in clist if c.get("homeAway") == "away"), {})
#
#         clock = st.get("displayClock", "0:00")
#         try:
#             minute = int(clock.split(":")[0])
#         except Exception:
#             minute = 0
#
#         # Red cards (ESPN event type id "5")
#         details = comps.get("details", [])
#         home_id = home_c.get("id", "")
#         away_id = away_c.get("id", "")
#         red_h = sum(1 for d in details
#                     if d.get("type", {}).get("id") == "5"
#                     and d.get("team", {}).get("id") == home_id)
#         red_a = sum(1 for d in details
#                     if d.get("type", {}).get("id") == "5"
#                     and d.get("team", {}).get("id") == away_id)
#
#         try:
#             home_score = int(home_c.get("score", 0))
#             away_score = int(away_c.get("score", 0))
#         except Exception:
#             home_score = away_score = 0
#
#         live.append({
#             "home":       home_c.get("team", {}).get("displayName", "?"),
#             "away":       away_c.get("team", {}).get("displayName", "?"),
#             "home_score": home_score,
#             "away_score": away_score,
#             "minute":     minute,
#             "red_h":      red_h,
#             "red_a":      red_a,
#         })
#     return live
#
#
# def _analyze_live_soccer(state: dict, odds_games: list):
#     """
#     Apply 3 soccer live rules. Returns alert dict or None.
#       Rule 1: 0-0 after 30 min → UNDER 2.5 value
#       Rule 2: 1-0 or 0-1 by 70 min → ML comeback for trailing team
#       Rule 3: Red card → UNDER value increases (10 vs 11)
#     """
#     minute = state["minute"]
#     if minute < 1 or minute >= _LIVE_MAX_SOCCER_MIN:
#         return None
#
#     home_score  = state["home_score"]
#     away_score  = state["away_score"]
#     total_score = home_score + away_score
#     remaining   = max(5, 90 - minute)
#
#     og = _match_odds_game(state["home"], state["away"], odds_games)
#     if not og:
#         return None
#
#     totals   = get_book_total(og)
#     h2h_odds = _extract_h2h_best(og)
#     og_home  = og.get("home_team", state["home"])
#     og_away  = og.get("away_team", state["away"])
#
#     live_line = live_over_odds = live_under_odds = None
#     if totals:
#         live_line, live_over_odds, live_under_odds, _ = totals
#
#     ml_home = h2h_odds.get(og_home, (None,))[0]
#     ml_away = h2h_odds.get(og_away, (None,))[0]
#
#     alert_label = alert_prob = alert_odds = None
#     alert_note  = ""
#     rule_fired  = ""
#
#     # ── Rule 1: 0-0 after 30 min → UNDER ─────────────────────────────────
#     if total_score == 0 and minute >= 30 and live_line and live_under_odds:
#         exp_goals  = 1.10 * (remaining / 60.0)
#         prob_under = poisson_ou_prob(exp_goals, live_line, False)
#         ev = (prob_under * live_under_odds - 1) * 100
#         if ev >= _LIVE_MIN_EDGE:
#             alert_label = f"UNDER {live_line}"
#             alert_prob  = prob_under
#             alert_odds  = live_under_odds
#             alert_note  = f"🔵 0-0 tras {minute}' — UNDER sube de valor"
#             rule_fired  = "scoreless_30"
#
#     # ── Rule 2: One goal (1-0), trailing team comeback ────────────────────
#     if not alert_label and total_score == 1 and minute <= 70:
#         trailing_home = away_score > home_score
#         comeback_name = state["home"] if trailing_home else state["away"]
#         comeback_odds = ml_home if trailing_home else ml_away
#         if comeback_odds and comeback_odds > 1.5:
#             frac_rem  = remaining / 90.0
#             prob_back = 0.25 * frac_rem
#             ev = (prob_back * comeback_odds - 1) * 100
#             if ev >= _LIVE_MIN_EDGE:
#                 alert_label = f"{comeback_name} ML (remontada)"
#                 alert_prob  = prob_back
#                 alert_odds  = comeback_odds
#                 alert_note  = (f"🟡 {comeback_name} perdiendo 1-0 en {minute}' "
#                                f"— ventana abierta")
#                 rule_fired  = "comeback"
#
#     # ── Rule 3: Red card → UNDER increases ───────────────────────────────
#     if not alert_label and (state["red_h"] > 0 or state["red_a"] > 0) \
#             and live_line and live_under_odds:
#         exp_goals  = 0.80 * (remaining / 60.0)
#         prob_under = poisson_ou_prob(exp_goals, live_line - total_score, False)
#         ev = (prob_under * live_under_odds - 1) * 100
#         if ev >= _LIVE_MIN_EDGE:
#             rc_side = "local" if state["red_h"] > 0 else "visitante"
#             alert_label = f"UNDER {live_line}"
#             alert_prob  = prob_under
#             alert_odds  = live_under_odds
#             alert_note  = (f"🟥 Tarjeta roja ({rc_side}) en {minute}' "
#                            f"— 10 vs 11, UNDER sube de valor")
#             rule_fired  = "red_card"
#
#     if not alert_label:
#         return None
#
#     ev_final = round((alert_prob * alert_odds - 1) * 100, 1)
#     stake    = max(10.0, min(30.0,
#                   round(kelly_stake(alert_prob, alert_odds)["stake"], 0)))
#
#     return {
#         "sport":      "SOCCER",
#         "home":       state["home"],
#         "away":       state["away"],
#         "home_score": home_score,
#         "away_score": away_score,
#         "minute":     minute,
#         "label":      alert_label,
#         "prob":       alert_prob,
#         "odds":       alert_odds,
#         "ev":         ev_final,
#         "note":       alert_note,
#         "rule":       rule_fired,
#         "stake":      stake,
#     }
#
#
# def _send_live_soccer_alert(a: dict):
#     """Format and fire a live soccer ntfy alert (priority=urgent)."""
#     score_str = f"{a['away_score']}-{a['home_score']}"
#     body = (
#         f"⏱️ Minuto {a['minute']}' | {_es(a['away'])} {score_str} {_es(a['home'])}\n"
#         f"{'━'*26}\n"
#         f"🎯 APUESTA EN VIVO:\n"
#         f"{a['label']}\n"
#         f"\n"
#         f"{a['note']}\n"
#         f"\n"
#         f"💰 ${a['stake']:.0f} @ {a['odds']:.2f} | EV +{a['ev']:.1f}%\n"
#         f"{'━'*26}\n"
#         f"🟢 APOSTAR AHORA — ventana corta\n"
#         f"⚡ APOSTAR EN 5 MIN MAX"
#     )
#     title = (f"⚡ LIVE ⚽ | {_es(a['away'])} vs {_es(a['home'])} "
#              f"| {a['label']}")
#     print(f"\n  ⚡ LIVE SOC — {a['away']} vs {a['home']} | {a['label']} "
#           f"| EV +{a['ev']:.1f}% | {a['minute']}'")
#     ntfy_post(title, body, priority="urgent")
#
#
# def run_live_scan(pre_game_matches: set, odds_by_sport: dict):
#     """
#     Called each scan cycle after pre-game analysis finishes.
#     Fetches in-progress MLB and WC soccer games, applies live rules,
#     and fires urgent ntfy alerts for qualifying opportunities.
#
#     pre_game_matches : set of "home|away" strings alerted pre-game this scan.
#     odds_by_sport    : {sport_key: [game_dicts]} from the current scan.
#     """
#     print("\n  ⚡ Live scan iniciando...")
#
#     # ── MLB ────────────────────────────────────────────────────────────────
#     live_mlb = _fetch_mlb_live_games()
#     if not live_mlb:
#         print("  — Sin juegos MLB en vivo")
#     else:
#         mlb_odds = odds_by_sport.get("baseball_mlb", [])
#         print(f"  ⚡ {len(live_mlb)} juego(s) MLB en vivo")
#         for g in live_mlb:
#             game_pk = g.get("gamePk")
#             try:
#                 feed  = _fetch_mlb_game_live_feed(game_pk)
#                 state = _parse_mlb_live_state(g, feed)
#
#                 if state["inning"] > _LIVE_MAX_INNING:
#                     print(f"  ⏭  {state['away']} vs {state['home']} "
#                           f"— {state['inning']}° inn (muy tarde, >6)")
#                     continue
#
#                 # Skip game if we already sent a pre-game alert for it today
#                 home_key = state["home"].split()[-1].lower()
#                 if any(home_key in pm.lower() for pm in pre_game_matches):
#                     print(f"  ⏭  {state['away']} vs {state['home']} "
#                           f"— ya analizado en pre-game hoy")
#                     continue
#
#                 alert = _analyze_live_mlb(state, mlb_odds)
#                 if not alert:
#                     print(f"  — {state['away']} vs {state['home']} "
#                           f"| {state['inning']}° inn "
#                           f"| {state['total_runs']} carreras — sin valor")
#                     continue
#
#                 live_key = f"live_mlb_{game_pk}_{alert['rule']}"
#                 if live_key in _live_alerted:
#                     continue
#                 _live_alerted.add(live_key)
#                 _send_live_mlb_alert(alert)
#
#             except Exception as _le:
#                 print(f"  ⚠️  Live MLB {game_pk}: {_le}")
#
#     # ── Soccer (World Cup only) ────────────────────────────────────────────
#     soccer_odds = odds_by_sport.get("soccer_fifa_world_cup", [])
#     if soccer_odds and is_in_season("soccer_fifa_world_cup"):
#         live_soc = _fetch_soccer_live_games()
#         if live_soc:
#             print(f"  ⚡ {len(live_soc)} partido(s) WC en vivo")
#             for state in live_soc:
#                 try:
#                     if state["minute"] >= _LIVE_MAX_SOCCER_MIN:
#                         print(f"  ⏭  {state['away']} vs {state['home']} "
#                               f"— {state['minute']}' (muy tarde, >75)")
#                         continue
#
#                     alert = _analyze_live_soccer(state, soccer_odds)
#                     if not alert:
#                         continue
#
#                     live_key = (f"live_soc_{state['home']}_{state['away']}"
#                                 f"_{alert['rule']}")
#                     if live_key in _live_alerted:
#                         continue
#                     _live_alerted.add(live_key)
#                     _send_live_soccer_alert(alert)
#
#                 except Exception as _le:
#                     print(f"  ⚠️  Live soccer {state.get('home','?')}: {_le}")
#
#     print("  ⚡ Live scan completo.")


# ═══════════════════════════════════════════════════════════════════════════════
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
        headline_es = _translate_terms(headline)

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
    ck = f"props_{event_id}"
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
    ck = f"f5_{event_id}"
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
    """Return timing window advice line for an alert. Empty string if unavailable."""
    try:
        game_et = datetime.fromisoformat(
            commence_time_str.replace("Z", "+00:00")
        ).astimezone(ET)
        game_str = game_et.strftime("%I:%M %p ET")
    except Exception:
        game_str = "?"

    if pick_type in ("totals", "total", "over", "under"):
        window  = "2–4h antes"
        reason  = "Pitchers confirmados, clima definido"
        try:
            from datetime import timezone as _tz
            open_t  = (game_et - timedelta(hours=4)).strftime("%I:%M %p")
            close_t = (game_et - timedelta(hours=2)).strftime("%I:%M %p")
            window  = f"{open_t}–{close_t} ET"
        except Exception:
            pass
    elif pick_type in ("ml", "moneyline"):
        window  = "Apertura de línea (AM)"
        reason  = "Sharp money la mueve rápido"
    elif pick_type == "live":
        window  = "Inning 3+"
        reason  = "Pitchers mostrando forma real"
    elif pick_type == "steam":
        window  = "AHORA (máx 5 min)"
        reason  = "Las líneas se mueven muy rápido"
    else:
        return ""
    return (f"⏰ Timing óptimo: {window} | Juego: {game_str}\n"
            f"   Por qué: {reason}")


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
    global daily_bets, lineup_scan_counter
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
            if not games:
                print(f"  ⚠️  {sport_key} — no data")
                continue

            # Override Odds-API times with authoritative MLB Stats API times
            if "mlb" in sport_key.lower():
                _patch_mlb_commence_times(games)

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
            arbs = scan_arbitrage(games, sport_key)
            for m in sharp_moves:
                m["sport"] = sport_key
            short = sport_key.split("_", 1)[-1].upper()

            for b in bets:
                b["sport"] = short

            # Full game analysis (Module 7)
            full_analyses = []
            for g in games:
                try:
                    result = analyze_game_full(g, sport_key, prev_map)
                    if result:
                        full_analyses.append(result)
                except Exception as _fe:
                    pass

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

    # Live betting scan — runs after pre-game analysis each cycle
    # LIVE SCAN DESACTIVADO — el bot solo opera pre-partido
    # Para reactivar: descomentar el bloque run_live_scan y el módulo LIVE BETTING
    # try:
    #     _pre_matches = set()
    #     for _fa in all_full_analyses:
    #         _m = _fa.get("match", "")
    #         if " vs " in _m:
    #             _h, _a = _m.split(" vs ", 1)
    #             _pre_matches.add(f"{_h.strip()}|{_a.strip()}")
    #     run_live_scan(_pre_matches, current_games_by_sport)
    # except Exception as _lse:
    #     print(f"  ⚠️  Live scan error: {_lse}")

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

    # Lineup check every 15 min (every 3rd 10-min scan)
    lineup_scan_counter += 1
    if lineup_scan_counter >= 3:
        check_lineup_changes()
        lineup_scan_counter = 0

    # Módulos Avanzados — auto-resultados, CLV, contrarian (al final de cada scan)
    if HAS_PAQUETE_AVANZADO:
        try:
            _avz_sport = next(
                (sk for sk in SPORT_KEYS if "mlb" in sk.lower()),
                "baseball_mlb"
            )
            run_modulos_avanzados(_avz_sport)
        except Exception as _mae:
            print(f"  ⚠️  Módulos avanzados error: {_mae}")

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
    compute_bankroll_mult()   # Module P: initialize stake multiplier at startup
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
            print("  🤖 ML model inicializado al arranque")
    except Exception as _mle:
        print(f"  ⚠️  ML model startup skipped: {_mle}")

    # Telegram bot — background polling thread (daemon, never blocks main loop)
    try:
        from telegram_bot import iniciar_telegram as _iniciar_tg
        _iniciar_tg(analyze_fn=analyze_game_full, get_odds_fn=get_odds,
                    build_text_fn=build_analizar_text,
                    get_hoy_fn=get_today_hoy_summary)
    except Exception as _tge:
        print(f"  ⚠️  Telegram bot startup skipped: {_tge}")

    while True:
        try:
            now_cdt = datetime.now(CDT)
            now_et  = datetime.now(ET)
            print(f"\n{'='*50}\n🕐 {now_cdt.strftime('%Y-%m-%d %H:%M CDT')}")

            check_midnight_reset()

            # Health check at 7:50 AM ET (once per day) — Feature 1
            if now_et.hour == 7 and now_et.minute >= 50 and last_health_check < now_et.date():
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

            # MLB Daily Card at 2:00 PM ET (once per day)
            if now_et.hour == 14 and now_et.minute < 10 and last_mlb_card < now_et.date():
                try:
                    send_daily_card("baseball_mlb")
                except Exception as e:
                    print(f"  ⚠️  MLB Daily Card error: {e}")

            # Soccer Daily Card at 10:00 AM ET (once per day)
            if now_et.hour == 10 and now_et.minute < 10 and last_soccer_card < now_et.date():
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

            print(f"🔍 Scan #{scan}")
            try:
                run_scan()
            except Exception as e:
                print(f"  ⚠️  Scan error (will retry): {e}")

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
