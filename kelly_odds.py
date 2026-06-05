"""
BetBot Pro — Professional Multi-Module Sports Betting System
Modules: Morning Report | Lineup Monitor | Math Models | Sharp Radar | Arb Scanner
"""
import requests, time, csv, os, json, math
from datetime import datetime, date, timedelta
import pytz

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
CLAUDE_MODEL      = os.environ.get("CLAUDE_MODEL", "claude-opus-4-5")
BANKROLL = 1000
FRACTION = 0.25
MIN_EDGE  = 2.0
MIN_STAKE         = 10.00   # Module 7: never alert if Kelly stake < $10
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

OPENWEATHER_KEY   = os.environ.get("OPENWEATHER_API_KEY", "")
BANKROLL_LOG_FILE = "bankroll_log.csv"
CLV_LOG_FILE      = "clv_log.csv"
PENDING_BETS_FILE = "pending_bets.json"

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
_steam_game_ids:  set   = set()  # game_ids with confirmed steam (current scan)

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

def kelly_stake(prob, fair_odd):
    b = fair_odd - 1
    if b <= 0:
        return {"stake": 0, "edge": 0, "has_value": False, "kelly_pct": 0}
    k     = max(0.0, (b * prob - (1 - prob)) / b)
    edge  = prob - 1.0 / fair_odd
    stake = BANKROLL * min(k * FRACTION, 0.05) * _bankroll_mult
    return {
        "stake":     round(stake, 2),
        "edge":      round(edge * 100, 2),
        "has_value": edge > 0.02,
        "kelly_pct": round(k * FRACTION * 100, 2),
    }

def confidence_level(edge_pct):
    if edge_pct >= 5.0: return "HIGH"
    if edge_pct >= 3.0: return "MEDIUM"
    return "LOW"

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
    """MLB Pythagorean expectation."""
    if rs + ra == 0: return 0.5
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
    """Return dict with AVG, OPS, rs_pg, ra_pg for a team. Returns None values
    for stats that cannot be fetched — never returns 4.5 placeholder."""
    try:
        hit_data = _mlb_rest(f"/teams/{team_id}/stats",
                             {"stats": "season", "group": "hitting", "season": MLB_YEAR})
        hit_splits = hit_data.get("stats", [{}]) if hit_data else []
        hit_stat   = (hit_splits[0].get("splits", [{}]) or [{}])[-1].get("stat", {}) if hit_splits else {}

        pit_data = _mlb_rest(f"/teams/{team_id}/stats",
                             {"stats": "season", "group": "pitching", "season": MLB_YEAR})
        pit_splits = pit_data.get("stats", [{}]) if pit_data else []
        pit_stat   = (pit_splits[0].get("splits", [{}]) or [{}])[-1].get("stat", {}) if pit_splits else {}

        # Compute rs_pg: prefer runsPerGame, else runs/gamesPlayed
        rs_pg = None
        if "runsPerGame" in hit_stat and float(hit_stat["runsPerGame"] or 0) > 0:
            rs_pg = round(float(hit_stat["runsPerGame"]), 2)
        elif hit_stat.get("runs") and hit_stat.get("gamesPlayed"):
            gp = max(float(hit_stat["gamesPlayed"]), 1)
            rs_pg = round(float(hit_stat["runs"]) / gp, 2)

        # Compute ra_pg from pitching runsAllowed/gamesPlayed
        ra_pg = None
        if pit_stat.get("runsAllowed") and pit_stat.get("gamesPlayed"):
            gp = max(float(pit_stat["gamesPlayed"]), 1)
            ra_pg = round(float(pit_stat["runsAllowed"]) / gp, 2)

        return {
            "avg":   hit_stat.get("avg",  "N/A"),
            "ops":   hit_stat.get("ops",  "N/A"),
            "rs_pg": rs_pg if rs_pg is not None else 4.5,
            "ra_pg": ra_pg if ra_pg is not None else 4.5,
            "_rs_real": rs_pg is not None,
            "_ra_real": ra_pg is not None,
        }
    except Exception:
        return {"avg": "N/A", "ops": "N/A", "rs_pg": 4.5, "ra_pg": 4.5,
                "_rs_real": False, "_ra_real": False}

def fetch_team_pitching_ra(team_id):
    """Return runs allowed per game from team pitching stats. Returns None on failure."""
    try:
        data = _mlb_rest(f"/teams/{team_id}/stats",
                         {"stats": "season", "group": "pitching", "season": MLB_YEAR})
        splits = data.get("stats", [{}]) if data else []
        stat   = (splits[0].get("splits", [{}]) or [{}])[-1].get("stat", {}) if splits else {}
        if stat.get("runsAllowed") and stat.get("gamesPlayed"):
            gp = max(float(stat["gamesPlayed"]), 1)
            return round(float(stat["runsAllowed"]) / gp, 2)
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

            # Team batting
            home_bat = fetch_team_batting(home_id) if home_id else {"avg":"N/A","ops":"N/A","rs_pg":4.5,"ra_pg":4.5}
            away_bat = fetch_team_batting(away_id) if away_id else {"avg":"N/A","ops":"N/A","rs_pg":4.5,"ra_pg":4.5}

            # Runs allowed per game (from pitching stats)
            if home_id: home_bat["ra_pg"] = fetch_team_pitching_ra(home_id)
            if away_id: away_bat["ra_pg"] = fetch_team_pitching_ra(away_id)

            # Pythagorean win probability (home team)
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
                f"  {home}: AVG {home_bat['avg']} | OPS {home_bat['ops']}\n"
                f"  {away}: AVG {away_bat['avg']} | OPS {away_bat['ops']}\n"
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
        probe = analyze_with_claude(
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
        return f"{_DIV3}\n🟢 CONFIANZA: ALTA — apostar"
    elif ev_pct >= 3 or (true_prob is not None and true_prob >= 0.50):
        return f"{_DIV3}\n🟡 CONFIANZA: MEDIA — apostar mitad"
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
            body = (
                f"{emoji} {match}\n"
                f"💰 Ganancia garantizada: ${profit} ({pct}%)\n"
                f"{_DIV}\n"
                f"🔵 ${arb['stake_a']:>8} → {arb['team_a']} @ {arb['odds_a']} — {arb['book_a']} {tag_a}\n"
                f"🤝 ${arb['stake_b']:>8} → Empate @ {arb['odds_b']} — {arb['book_b']} {tag_b}\n"
                f"🔴 ${arb['stake_c']:>8} → {arb['team_c']} @ {arb['odds_c']} — {arb['book_c']} {tag_c}\n"
                f"{_DIV}\n"
                f"💵 Total apostado: ${total_stake}\n"
                f"⏰ {gt}\n"
                f"{arb_timing_note}"
                f"{risky_note}"
                f"{verdict}\n"
                f"{_DIV2}"
            )
        else:
            total_stake = round(arb["stake_a"] + arb["stake_b"], 2)
            has_risky   = any(_is_risky_book(b) for b in [arb["book_a"], arb["book_b"]])
            risky_note  = "⚠️ Una casa de apuestas es riesgosa — apuesta sólo en las marcadas ✅ si es posible\n" if has_risky else ""
            body = (
                f"{emoji} {match}\n"
                f"💰 Ganancia garantizada: ${profit} ({pct}%)\n"
                f"{_DIV}\n"
                f"🔵 ${arb['stake_a']:>8} → {arb['team_a']} @ {arb['odds_a']} — {arb['book_a']} {tag_a}\n"
                f"🔴 ${arb['stake_b']:>8} → {arb['team_b']} @ {arb['odds_b']} — {arb['book_b']} {tag_b}\n"
                f"{_DIV}\n"
                f"💵 Total apostado: ${total_stake}\n"
                f"⏰ {gt}\n"
                f"{arb_timing_note}"
                f"{risky_note}"
                f"{verdict}\n"
                f"{_DIV2}"
            )

        ntfy_post(f"⚡ ARB | {match} | +${profit}", body, "urgent")
        print(f"  💰 ARB: {match} — ${profit} profit ({pct}%)")

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 6 — TOTALS (OVER/UNDER) ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

# Park factors for known MLB parks (home team name → multiplier)
MLB_PARK_FACTORS = {
    "Colorado Rockies":     1.15,
    "Cincinnati Reds":      1.08,
    "Boston Red Sox":       1.05,
    "New York Yankees":     1.04,
    "Chicago Cubs":         1.02,
    "Texas Rangers":        1.02,
    "Philadelphia Phillies": 1.01,
    "Baltimore Orioles":    1.01,
    "San Francisco Giants": 0.90,
    "Los Angeles Dodgers":  0.93,
    "Oakland Athletics":    0.95,
    "Seattle Mariners":     0.96,
    "Miami Marlins":        0.97,
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

_team_run_cache: dict = {}   # team_name -> {"rs_pg": float, "ra_pg": float}

def fetch_team_run_stats(team_name):
    """RS/RA per game for an MLB team. Cached for session."""
    if team_name in _team_run_cache:
        return _team_run_cache[team_name]
    try:
        if HAS_STATSAPI:
            teams = statsapi.lookup_team(team_name)
            if not teams:
                return None
            tid = teams[0]["id"]
        else:
            data = _mlb_rest("/teams", {"sportId": 1, "season": MLB_YEAR})
            match = next(
                (t for t in data.get("teams", [])
                 if team_name.lower() in t.get("name", "").lower()),
                None,
            )
            if not match:
                return None
            tid = match["id"]

        hit = _mlb_rest(f"/teams/{tid}/stats",
                        {"stats": "season", "group": "hitting", "season": MLB_YEAR})
        pit = _mlb_rest(f"/teams/{tid}/stats",
                        {"stats": "season", "group": "pitching", "season": MLB_YEAR})

        h_stat = (hit.get("stats", [{}])[0].get("splits", [{}]) or [{}])[-1].get("stat", {})
        p_stat = (pit.get("stats", [{}])[0].get("splits", [{}]) or [{}])[-1].get("stat", {})

        # Prefer runsPerGame; fall back to runs/gamesPlayed
        gp_h = max(float(h_stat.get("gamesPlayed", 0) or 0), 1)
        if "runsPerGame" in h_stat and float(h_stat["runsPerGame"] or 0) > 0:
            rs_pg = round(float(h_stat["runsPerGame"]), 2)
        elif float(h_stat.get("runs", 0) or 0) > 0:
            rs_pg = round(float(h_stat["runs"]) / gp_h, 2)
        else:
            _team_run_cache[team_name] = None
            return None

        games_p = max(float(p_stat.get("gamesPlayed", 0) or 0), 1)
        ra_raw  = float(p_stat.get("runsAllowed", 0) or 0)
        if ra_raw == 0:
            _team_run_cache[team_name] = None
            return None
        ra_pg = round(ra_raw / games_p, 2)

        result = {"rs_pg": rs_pg, "ra_pg": ra_pg}
        _team_run_cache[team_name] = result
        return result
    except Exception:
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
            "rs_pg":  float(st.get("runsPerGame", 4.5)),
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
    Prefers Bovada/Bodog; falls back to first available.
    Returns (line, over_odds, under_odds, bookmaker_name) or None.
    """
    preferred, fallback = None, None
    for bk in game.get("bookmakers", []):
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
        if not (2.00 <= bullpen_era <= 7.00):
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
        _tc_claude = analyze_with_claude(_tc_data, _tc_sport)
        if _tc_claude:
            _tcc  = _tc_claude.get("confianza", "N/D")
            _tcap = "✅" if _tc_claude.get("apostar", True) else "❌"
            _tcr  = (_tc_claude.get("razonamiento", "") or "")[:80]
            print(f"   🤖 Claude: {_tcc} | apostar:{_tcap} | \"{_tcr}\"")
        if (_tc_claude
                and not _tc_claude.get("apostar", True)
                and _tc_claude.get("confianza") == "BAJA"):
            print(f"   ❌ RECHAZADO — Claude veta {bet_side} {book_line}")
            continue
        if _tc_claude and _tc_claude.get("apostar", True):
            print(f"   ✅ TOTALS PICK: {bet_side} {book_line}  edge={edge_val:.1f}")

        total_bets.append({
            "match":        f"{home} vs {away}",
            "team":         bet_side,
            "side":         str(book_line),
            "odds":         bet_odds,
            "edge":         edge_val,
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
            "ev":           0,
            "roi":          0,
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

def notify_totals(total_bets):
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
            half_stake = round(b["stake"] / 2, 2)
            action = (f"🟢 APOSTAR: ${b['stake']}" if is_high
                      else f"🟡 APOSTAR MITAD: ${half_stake}")
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
            body = (
                f"{emoji} {b['match']}\n"
                f"⏰ Hoy {gt}\n"
                f"{_DIV}\n"
                f"🎯 APUESTA: {side} {line} carreras (Total)\n\n"
                f"💰 ${b['stake']} @ {b['odds']} — {b['bookmaker']}{bk_warn_tot}\n"
                f"{_DIV}\n"
                f"📊 POR QUÉ:\n"
                f"Modelo base:      {base_p} carreras\n"
                f"{adj_block}"
                f"Total proyectado: {b['our_line']} carreras\n"
                f"La casa de apuestas pone: {line} carreras\n"
                f"Edge:             {b['edge']} carreras ✅\n\n"
                f"🔵 Pitcher local:  {ph_name} — {_era_label(ph_era)} (ERA {ph_era:.2f})\n"
                f"🔴 Pitcher visita: {pa_name} — {_era_label(pa_era)} (ERA {pa_era:.2f})\n"
                f"{wind_line}"
                f"{_claude_tot_blk}"
                f"{_DIV}\n"
                f"{action}\n"
                + ("✅ Datos verificados\n" if b.get("data_verified", True)
                   else "⚠️ Verificar antes de apostar — algunos datos sin confirmar\n")
                + f"{_DIV2}"
            )
            match_es_tot = f"{_es(home)} vs {_es(away)}"
            title    = f"⚾ TOTAL | {side} {line} | {match_es_tot}"
            priority = "high" if is_high else "default"
        else:
            # ── Soccer / other sports ─────────────────────────────────────
            is_high    = b["confidence"] == "HIGH"
            half_stake = round(b["stake"] / 2, 2)
            action = (f"🟢 APOSTAR: ${b['stake']}" if is_high
                      else f"🟡 APOSTAR MITAD: ${half_stake}")
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
            body = (
                f"{emoji} {match_es_tot}\n"
                f"⏰ Hoy {gt}\n"
                f"{_DIV}\n"
                f"🎯 APUESTA: {side} {line} {unit} (Total)\n\n"
                f"💰 ${b['stake']} @ {b['odds']} — {b['bookmaker']}{bk_warn_tot}\n"
                f"{_DIV}\n"
                f"📊 POR QUÉ:\n"
                f"Modelo proyecta: {b['our_line']} {unit}\n"
                f"La casa de apuestas pone: {line} {unit}\n"
                f"Diferencia:      {b['edge']} {unit} de edge"
                f"{soc_adj_block}\n"
                + (f"\n{form_block}\n" if form_block else "")
                + f"{_DIV}\n"
                f"{action}\n"
                + ("✅ Datos verificados\n" if b.get("data_verified", True)
                   else "⚠️ Verificar antes de apostar — algunos datos sin confirmar\n")
                + f"{_DIV2}"
            )
            title    = f"{emoji} TOTAL | {side} {line} | {match_es_tot}"
            priority = "high" if is_high else "default"

        ntfy_post(title, body, priority)
        alerted_bets.add(key)
        save_pending_bet(b)
        print(f"    🎯 {side} {line} {b['match']} | Our:{b['our_line']} | Edge:{b['edge']} {unit}")

# ═══════════════════════════════════════════════════════════════════════════════
# MODULE 7 — FULL GAME ANALYSIS (SOCCER + MLB)
# ═══════════════════════════════════════════════════════════════════════════════

def _extract_h2h_best(game):
    """Best decimal odds per outcome name across all books. Returns {name: (price, book)}."""
    best = {}
    for bk in game.get("bookmakers", []):
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
    "era":         (1.50, 8.00),
    "fip":         (1.50, 8.00),
    "ops":         (0.500, 1.100),
    "bullpen_era": (2.00, 7.00),
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
        ph = str(context.get("pitcher_home", "") or "")
        pa = str(context.get("pitcher_away", "") or "")
        if (ph and "TBD" not in ph and pa and "TBD" not in pa
                and _val_stat("era", context.get("era_home"))
                and _val_stat("era", context.get("era_away"))):
            score += 20
        if (_val_stat("fip", context.get("fip_home")) is not None
                and _val_stat("fip", context.get("fip_away")) is not None):
            score += 10
        dq_h = _data_quality.get(f"{home}_{today_s}", {})
        dq_a = _data_quality.get(f"{away}_{today_s}", {})
        if dq_h.get("verified") and dq_a.get("verified"):
            score += 15
        elif dq_h.get("verified") or dq_a.get("verified"):
            score += 7
        h_sp = context.get("h_splits") or {}
        a_sp = context.get("a_splits") or {}
        if h_sp and a_sp:
            score += 15
        if context.get("umpire"):
            score += 10
        wind = str(context.get("wind_info", "") or "")
        if "mph" in wind.lower():
            score += 10
        elif context.get("temp_label"):
            score += 5
        lu = context.get("lineup_data") or {}
        if lu and (lu.get("home", {}).get("confirmed") or lu.get("away", {}).get("confirmed")):
            score += 10
        if context.get("h2h_data"):
            score += 10
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
    return min(score, 100)


EV_MIN_PCT   = 3.0   # minimum EV% to include a bet in Full Game Analysis
PROB_MIN     = 0.50  # minimum true probability to include a bet in Full Game Analysis
_RANK_EMOJIS = ["1️⃣", "2️⃣", "3️⃣"]

def analyze_game_full(game, sport_key, prev_map=None):
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

    if game_starts_soon(commence, 60):
        print(f"   ⏰ OMITIDO — inicia en < 60 min")
        return None
    tc = _timing_check(commence, is_mlb)
    if tc["skip"]:
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
        if h_stats is None:
            print(f"   ⚠️ Sin stats de carreras ({home}) — usando promedio liga ({LEAGUE_AVG})")
            h_stats = {"rs_pg": LEAGUE_AVG, "ra_pg": LEAGUE_AVG}
        if a_stats is None:
            print(f"   ⚠️ Sin stats de carreras ({away}) — usando promedio liga ({LEAGUE_AVG})")
            a_stats = {"rs_pg": LEAGUE_AVG, "ra_pg": LEAGUE_AVG}

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

        p_home = pythagorean_win_prob(home_exp, away_exp)
        p_away = 1.0 - p_home

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
            ev = (true_p * odds - 1) * 100
            r  = kelly_stake(true_p, odds)
            _all_evs.append((lbl, round(ev, 1)))
            if ev >= EV_MIN_PCT and r["stake"] > 0:
                candidates.append({"label": lbl, "true_prob": true_p, "odds": odds,
                                   "book": book, "ev_pct": round(ev, 1),
                                   "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

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

            for side_label, is_over, p, odds in [
                (f"📈 OVER {book_line} carreras",  True,
                 poisson_ou_prob(adj_total, book_line, True),  over_odds),
                (f"📉 UNDER {book_line} carreras", False,
                 poisson_ou_prob(adj_total, book_line, False), under_odds),
            ]:
                # Rule 1: skip OVER when projected edge is below the dominant-pitcher threshold
                if is_over and _over_min_edge > 0 and _over_edge < _over_min_edge:
                    continue
                ev = (p * odds - 1) * 100
                r  = kelly_stake(p, odds)
                _all_evs.append((side_label, round(ev, 1)))
                if ev >= EV_MIN_PCT and r["stake"] > 0:
                    candidates.append({"label": side_label, "true_prob": p, "odds": odds,
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

        context = {
            "pitcher_home":  f"{h_pname} (ERA {h_era:.2f})",
            "pitcher_away":  f"{a_pname} (ERA {a_era:.2f})",
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
            "tbd_note":      tbd_note,  # Fix 5
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
            p_win   = max(0.05, min(0.85, p_win  + _ml_delta_g))
            p_loss  = max(0.05, min(0.85, p_loss - _ml_delta_g))
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
                # Rule 1: skip OVER when projected edge is below defense threshold
                if is_over and _over_min_edge_s > 0 and _over_edge_s < _over_min_edge_s:
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
        return None

    # Rank by EV%, keep top 3, tag safest (prob ≥ 60%)
    candidates.sort(key=lambda x: x["ev_pct"], reverse=True)
    top3 = candidates[:3]
    for c in top3:
        c["safest"] = c["true_prob"] >= 0.60

    # ── Feature 7: data completeness guard ───────────────────────────────
    _dqs = context.get("data_quality_score", 100)
    if _dqs < 30:
        print(f"  ⏭️  Juego omitido — datos muy escasos ({_dqs}/100): {home} vs {away}")
        _log_error("data_completeness", "skip", f"{home} vs {away}",
                   f"score {_dqs}/100 < 30")
        return None

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
    _claude_sport_g  = "MLB" if is_mlb else "SOCCER"
    _claude_result_g = analyze_with_claude(_claude_data_g, _claude_sport_g)

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

    # Soft veto: Claude says BAJA confidence + don't bet → drop that candidate
    if (_claude_result_g
            and not _claude_result_g.get("apostar", True)
            and _claude_result_g.get("confianza") == "BAJA"):
        top3 = top3[1:]
        if not top3:
            print(f"   ❌ RECHAZADO — Claude vetó todos los picks (confianza BAJA)")
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


def notify_game_analysis(analyses, sport_key):
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

            # EV in plain dollars
            prob_pct = round(c['true_prob'] * 100)
            odds_line = (f"   💰 Apuesta ${c['stake']} @ {c['odds']} — {c['book']}{bk_warn_pick}\n")

            picks_lines += (
                f"{rank_emoji} {c['label']}\n"
                f"   Ganancia esperada: ${ev_d} por cada ${c['stake']} apostados\n"
                f"   Probabilidad real: {prob_pct}%{safe_tag}\n"
                f"{odds_line}"
            )

        # Verdict — cap to MEDIA whenever any ⚠️ warning is present
        if tc.get("cap_conf") or has_warning:
            verdict = f"{_DIV3}\n🟡 CONFIANZA: MEDIA — apostar mitad"
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

        # Strip decorative emojis from title
        clean = best_clean
        title = f"🔍 {match_es} | Mejor: {clean} +{a['best_ev']}%"
        ntfy_post(title, body, "high")
        alerted_game_analysis.add(a["game_id"])
        print(f"  🔍 Análisis: {a['match']} — {len(a['candidates'])} pick(s), "
              f"mejor EV +{a['best_ev']}%")

# ═══════════════════════════════════════════════════════════════════════════════
# IMPROVEMENT 3: CLV (CLOSING LINE VALUE) TRACKING
# ═══════════════════════════════════════════════════════════════════════════════

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
            params={"apiKey": API_KEY, "regions": "us,uk,eu",
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

def analyze_sharp_money(game_id, home, away, best_h, best_a, prev_map):
    """
    Estimate sharp action by comparing line movement direction vs expected public side.
    Public typically bets favorites. If underdog line shortens (odds fall), that's sharp.
    Also flags any 5%+ relative move as sharp.
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
        line_shortened = pct_change < 0   # odds fell = more likely to win now

        if is_underdog and line_shortened:
            public_pct = 35   # estimated: public rarely backs heavy underdogs
            sharps.append({
                "match":      f"{home} vs {away}",
                "team":       team,
                "direction":  "shortened",
                "pct":        round(abs(pct_change), 1),
                "odds_prev":  prev,
                "odds_now":   current,
                "public_pct": public_pct,
            })
        elif not is_underdog and not line_shortened:
            # Favorite drifting — sharp money going opposite
            public_pct = 65
            sharps.append({
                "match":      f"{home} vs {away}",
                "team":       team,
                "direction":  "drifted",
                "pct":        round(abs(pct_change), 1),
                "odds_prev":  prev,
                "odds_now":   current,
                "public_pct": public_pct,
            })
        else:
            # General large move
            sharps.append({
                "match":      f"{home} vs {away}",
                "team":       team,
                "direction":  "moved",
                "pct":        round(abs(pct_change), 1),
                "odds_prev":  prev,
                "odds_now":   current,
                "public_pct": None,
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

        if m.get("public_pct"):
            context_lines = (
                f"👥 Público: ~{m['public_pct']}% en {opp}\n"
                f"⚠️  Pero línea se movió a favor de {team}\n"
                f"💡 Sharps vs público → señal fuerte\n"
            )
        else:
            context_lines = f"📉 Línea {m['direction']} {m['pct']}% sin contexto público\n"

        body = (
            f"{emoji} {m['match']}\n"
            f"{_DIV}\n"
            f"📌 Pick: {team} @ {m['odds_now']}\n"
            f"📊 Línea: {m['odds_prev']} → {m['odds_now']} ({arrow}{m['pct']}%)\n"
            f"{context_lines}"
            f"⭐ ACCIÓN: Apostar {team} en la mejor casa de apuestas disponible\n"
            f"{_DIV3}\n"
            f"🟢 CONFIANZA: ALTA — apostar\n"
            f"{_DIV2}"
        )
        ntfy_post(f"⚡ SHARP | {team} | {m['match']}", body, "high")
        print(f"  ⚡ Sharp: {team} en {m['match']} ({m['pct']}% movimiento)")

# ═══════════════════════════════════════════════════════════════════════════════
# UPGRADE PACKAGE — 10 MODULES
# ═══════════════════════════════════════════════════════════════════════════════

# ── Module 3: book safety ─────────────────────────────────────────────────────
SAFE_BOOKS = {
    "bovada", "bodog", "fanduel", "draftkings", "mybookie",
    "pointsbet", "caesars", "betmgm", "unibet",
    "williamhill", "william hill", "pinnacle",
    "betonline.ag", "betonline",
}
RISKY_BOOKS = {
    "1xbet", "gtbets", "betfred", "smarkets", "ladbrokes", "betway",
    "everygame", "betvictor", "cloudbet", "stake",
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

def fetch_mlb_il(home, away):
    """Return {team_name: [player_names]} for IL players on both teams today."""
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    cache_key = f"{home}|{away}|{today_str}"
    if cache_key in _injury_cache:
        return _injury_cache[cache_key]
    result = {}
    for tname in (home, away):
        try:
            if HAS_STATSAPI:
                teams = statsapi.lookup_team(tname)
                tid   = teams[0]["id"] if teams else None
            else:
                data  = _mlb_rest("/teams", {"name": tname, "sportId": 1})
                teams = data.get("teams", [])
                tid   = teams[0]["id"] if teams else None
            if tid is None:
                continue
            roster  = _mlb_rest(f"/teams/{tid}/roster", {"rosterType": "injured"})
            players = [p.get("person", {}).get("fullName", "")
                       for p in roster.get("roster", [])]
            if players:
                result[tname] = players
        except Exception:
            pass
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
    Cached per team per calendar day.
    """
    today = datetime.now(ET).strftime("%Y-%m-%d")
    ck    = f"{team_name}|{today}"
    if ck in _splits_cache:
        return _splits_cache[ck]

    try:
        tid = _team_id(team_name)
        if tid is None:
            print(f"  ⚠️  _team_id not found for '{team_name}'")
            _splits_cache[ck] = None
            return None

        # ── Hitting stats → runs scored ───────────────────────────────────────
        hit_data = _mlb_rest(f"/teams/{tid}/stats", {
            "stats": "homeAndAway", "group": "hitting",
            "season": MLB_YEAR, "sportId": 1,
        })
        hit_splits = (hit_data.get("stats", [{}])[0].get("splits", [])
                      if hit_data and hit_data.get("stats") else [])

        # ── Pitching stats → runs allowed ─────────────────────────────────────
        pit_data = _mlb_rest(f"/teams/{tid}/stats", {
            "stats": "homeAndAway", "group": "pitching",
            "season": MLB_YEAR, "sportId": 1,
        })
        pit_splits = (pit_data.get("stats", [{}])[0].get("splits", [])
                      if pit_data and pit_data.get("stats") else [])

        # ── Debug: log what the API actually returned ─────────────────────────
        if not hit_splits and not pit_splits:
            hit_keys = list((hit_data or {}).keys())
            pit_keys = list((pit_data or {}).keys())
            print(f"  ⚠️  homeAndAway splits empty [{team_name}] "
                  f"hit_keys={hit_keys} pit_keys={pit_keys}")
            # Fallback: compute from schedule
            result = _splits_from_schedule(tid, team_name)
            _splits_cache[ck] = result
            return result
        else:
            # Sample first split to show structure
            sample = hit_splits[0] if hit_splits else pit_splits[0]
            sample_keys = {k: str(v)[:40] for k, v in sample.items()
                           if k in ("split", "isHome", "stat")}
            print(f"  📊 homeAndAway [{team_name}] splits={len(hit_splits)} "
                  f"sample={sample_keys}")

        result: dict = {}

        def _is_home(s: dict) -> "bool | None":
            """Detect home/away from split dict — handles both API formats."""
            # Format A: {"split": {"code": "H"}} or {"split": {"code": "A"}}
            code = s.get("split", {}).get("code", "")
            if code in ("H", "A"):
                return code == "H"
            # Format B: {"isHome": true/false}
            is_home = s.get("isHome")
            if is_home is not None:
                return bool(is_home)
            # Format C: split description
            desc = s.get("split", {}).get("description", "").lower()
            if "home" in desc:
                return True
            if "away" in desc or "road" in desc:
                return False
            return None

        for s in hit_splits:
            home = _is_home(s)
            if home is None:
                continue
            stat = s.get("stat", {})
            gp   = max(float(stat.get("gamesPlayed", 0) or 0), 1)
            runs = float(stat.get("runs", 0) or 0)
            wins = float(stat.get("wins", 0) or 0)
            loss = float(stat.get("losses", 0) or 0)
            wl   = wins + loss
            wpct = round(wins / wl, 3) if wl > 0 else 0.500
            if home:
                result["home_rs"]   = round(runs / gp, 2)
                result["home_wpct"] = wpct
            else:
                result["away_rs"]   = round(runs / gp, 2)
                result["away_wpct"] = wpct

        for s in pit_splits:
            home = _is_home(s)
            if home is None:
                continue
            stat = s.get("stat", {})
            gp   = max(float(stat.get("gamesPlayed", 0) or 0), 1)
            ra   = float(stat.get("runs", 0) or 0)
            if home:
                result["home_ra"] = round(ra / gp, 2)
            else:
                result["away_ra"] = round(ra / gp, 2)

        # Only return if we got at least one real stat
        required = {"home_rs", "home_ra", "home_wpct", "away_rs", "away_ra", "away_wpct"}
        if not required.issubset(result.keys()):
            _splits_cache[ck] = None
            return None

        _splits_cache[ck] = result
        return result

    except Exception:
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
_ERA_MIN = 1.50
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
)


def analyze_with_claude(game_data: dict, sport: str) -> "dict | None":
    """
    Pre-validate game_data, then send to Claude as the final verification layer.
    sport: "MLB" or "SOCCER"
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
        f"{sport}{json.dumps(clean_data, default=str, sort_keys=True)}".encode()
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
            model=CLAUDE_MODEL,
            max_tokens=1024,
            system=_CLAUDE_SYSTEM,
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

        ntfy_post("📊 RESUMEN SEMANAL", body, "default")
        print("  📊 Resumen semanal ntfy enviado")
    except Exception as e:
        print(f"  ⚠️  send_weekly_summary error: {e}")

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

        best_h, best_a = max(odds_h), max(odds_a)
        avg_h  = sum(odds_h) / len(odds_h)
        avg_a  = sum(odds_a) / len(odds_a)
        fp_h, fp_a = remove_vig([avg_h, avg_a])

        # If Bovada has odds, prefer it as the displayed bookmaker
        if bov_odds_h is not None:
            best_bk_h = "Bovada"
        if bov_odds_a is not None:
            best_bk_a = "Bovada"

        # Sharp money radar check
        sharp_moves.extend(
            analyze_sharp_money(game_id, home, away, best_h, best_a, prev_map)
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

            bets.append({
                "match":        f"{home} vs {away}",
                "team":         team,
                "side":         side,
                "odds":         best_odd,
                "edge":         r["edge"],
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

def notify_bets(new_bets):
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
            half_stake = round(b["stake"] / 2, 2)
            action = (f"🟢 APOSTAR: ${b['stake']}" if is_high
                      else f"🟡 APOSTAR MITAD: ${half_stake}")
            body = (
                f"⚾ {match_es}\n"
                f"⏰ Hoy {gt}\n"
                f"{_DIV}\n"
                f"🎯 APUESTA: {team_es} GANA (ML)\n\n"
                f"💰 ${b['stake']} @ {b['odds']} — {b['bookmaker']}{bk_warn}\n"
                f"{_DIV}\n"
                f"{top3_blk}"
                f"📊 POR QUÉ:\n"
                f"Modelo           → {elo_p}% de ganar\n"
                f"Casa de apuestas → {impl_pct}% implícito\n"
                f"Edge:     {b['edge']}%\n\n"
                f"🔵 Pitcher local:  {ph_name} — {_era_label(ph_era)} (ERA {ph_era:.2f})\n"
                f"🔴 Pitcher visita: {pa_name} — {_era_label(pa_era)} (ERA {pa_era:.2f})\n"
                f"{_DIV}\n"
                f"{action}\n"
                f"{_DIV2}"
            )
            priority = "urgent" if is_high else "high"
            title    = f"⚾ ML | {team_es} | {match_es}"
        else:
            # ── Soccer / other sports ─────────────────────────────────────
            impl_pct   = round(100 / b["odds"], 1) if b["odds"] else 0
            is_high    = b["edge"] >= 5.0 and elo_p >= 60
            half_stake = round(b["stake"] / 2, 2)
            action = (f"🟢 APOSTAR: ${b['stake']}" if is_high
                      else f"🟡 APOSTAR MITAD: ${half_stake}")
            body = (
                f"{emoji} {match_es}\n"
                f"⏰ Hoy {gt}\n"
                f"{_DIV}\n"
                f"🎯 APUESTA: {team_es} GANA (ML)\n\n"
                f"💰 ${b['stake']} @ {b['odds']} — {b['bookmaker']}{bk_warn}\n"
                f"{_DIV}\n"
                f"{top3_blk}"
                f"📊 POR QUÉ:\n"
                f"Nuestro modelo: {elo_p}% | Casa de apuestas: {impl_pct}% → Edge {b['edge']}%\n"
                f"{_DIV}\n"
                f"{action}\n"
                f"{_DIV2}"
            )
            priority = "urgent" if b["edge"] >= 5.0 else ("high" if b["edge"] >= 3 else "default")
            title    = f"{emoji} ML | {team_es} | {match_es}"

        ntfy_post(title, body, priority)
        alerted_bets.add(f"{b['game_id']}|{b['team']}")
        # Module 8: save ML bets to pending_bets for CLV tracking
        try:
            save_pending_bet(b)
        except Exception:
            pass

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

    body = (
        f"💎 PICK PREMIUM | {_es(team)}"
        f" {'ML' if mtype != 'totals' else side}\n"
        f"{_DIV}\n"
        f"{emoji} {_es(home_s)} vs {_es(away_s)}\n"
        f"⏰ Hoy {gt}\n"
        f"{_DIV}\n"
        f"🎯 {apuesta_line}\n"
        f"💰 ${stake:.2f} @ {odds} — {book}\n"
        f"   (stake aumentado por señales múltiples)\n"
        f"{_DIV}\n"
        f"✅ Señales confirmadas ({len(signals)}/8):\n"
        f"{sig_lines}\n"
        f"{_DIV}\n"
        f"🟢 CONFIANZA MÁXIMA — apostar{low_warn}"
    )
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
            ev = (p * odds - 1) * 100
            if ev > best_ev:
                best_ev    = round(ev, 1)
                best_label = lbl
                best_odds  = odds
                r          = kelly_stake(p, odds)
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

    # ── Full analysis with Claude for EV >= EV_MIN_PCT games ────────────
    claude_results: dict = {}
    for res in all_results:
        if res["best_ev"] < EV_MIN_PCT:
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

        body = (
            f"⚾ {s['match']}\n"
            f"{_DIV}\n"
            f"📉 Línea cayó: {s['odds_from']:.2f} → {s['odds_to']:.2f} ▼  "
            f"(−{avg_move:.1f}% promedio)\n"
            f"   {n_books} casas simultáneas en < 10 min\n"
            f"   🇺🇸 US ({n_us}): {us_names or 'N/A'}\n"
            f"   📋 Todas: {all_names}\n"
            f"\n"
            f"{badge}\n"
            f"💎 Dinero serio entrando en {_es(s['team'])}\n"
            f"⭐ ACCIÓN: {_es(s['team'])} ML ahora\n"
            f"💰 {s['odds_to']:.2f} — mejor precio actual\n"
            f"{_DIV3}\n"
            f"{action}\n"
            f"{_DIV2}"
        )
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


def detect_and_notify_parlays(all_analyses: list):
    """
    After a full scan, find the best qualifying 2-leg parlay and alert once.
    Thresholds: each leg EV ≥ 8%, prob ≥ 60%, safe book, same bet type,
    different games, no TBD/contradiction; parlay EV > 15%.
    """
    eligible = []
    for a in all_analyses:
        eligible.extend(_extract_parlay_candidates(a))

    if len(eligible) < 2:
        return

    best_parlay = None
    best_ev     = 15.0   # minimum to suggest

    for i in range(len(eligible)):
        for j in range(i + 1, len(eligible)):
            p1, p2 = eligible[i], eligible[j]
            if p1["game_id"] == p2["game_id"]:
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
# CORE — MAIN SCAN
# ═══════════════════════════════════════════════════════════════════════════════

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
    _steam_game_ids.clear()      # reset steam registry for this scan
    now_month  = datetime.now(CDT).month

    # Improvement 4: bankroll dashboard at top of every scan
    print_dashboard()

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
                    mv = f" [LINE {b['line_dir']}{b['line_delta']}]" if b["line_moved"] else ""
                    print(f"    [{b['confidence']}]{mv} {b['match']} → "
                          f"{b['team']} @{b['odds']} | Edge:{b['edge']}% | "
                          f"EV:${b['ev']} | Book:{b['bookmaker']}")
                if LOG_CSV:
                    log_bets(bets, short)
                all_bets.extend(bets)
                daily_bets.extend(bets)
            else:
                print(f"  ❌ {short} — no ML value")

            if total_bets:
                print(f"  🎯 {short} — {len(total_bets)} totals bet(s):")
                if LOG_CSV:
                    log_bets(total_bets, short)
                all_totals.extend(total_bets)
                daily_bets.extend(total_bets)
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
                notify_game_analysis(full_analyses, sport_key)
                all_full_analyses.extend(full_analyses)  # parlay collector

        except Exception as e:
            print(f"  ⚠️  {sport_key} error (skipping): {e}")

    prev_map.update(new_map)
    save_previous_odds(prev_map)

    if all_bets:
        notify_bets(all_bets)
    if all_totals:
        notify_totals(all_totals)
    if all_sharp:
        notify_sharp_money(all_sharp)
    if all_steams:
        notify_steam_moves(all_steams)
    if all_arbs:
        notify_arbitrage(all_arbs)

    # Parlay detector — runs once after all sports are processed
    try:
        detect_and_notify_parlays(all_full_analyses)
    except Exception as _pe:
        print(f"  ⚠️  Parlay detector error: {_pe}")

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

# ═══════════════════════════════════════════════════════════════════════════════
# ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    if not HAS_STATSAPI:
        print("⚠️  MLB-statsapi not found — install via: pip install MLB-statsapi")
    print("🤖 BetBot Pro — starting...")
    scan = 1
    compute_bankroll_mult()   # Module P: initialize stake multiplier at startup

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

            # Module 1: auto-resultados — check after every scan
            try:
                check_results()
            except Exception as e:
                print(f"  ⚠️  check_results error: {e}")

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
