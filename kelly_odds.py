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

# ── CONFIG ────────────────────────────────────────────────────────────────────
API_KEY  = os.environ.get("ODDS_API_KEY", "")
BANKROLL = 1000
FRACTION = 0.25
MIN_EDGE  = 2.0
MIN_STAKE = 10.00     # Module 7: never alert if Kelly stake < $10
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

_pitcher_cache: dict = {}   # date_str → {team_key: {home_era, away_era, ...}}
_wc_form_cache: dict = {}   # (team_name, date_str) → {goals_for, goals_against, matches}
_weather_cache: dict = {}   # "lat,lon" → {speed, deg, label, fetched_at}

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
    stake = BANKROLL * min(k * FRACTION, 0.05)
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
    """Return dict with AVG and OPS for a team."""
    empty = {"avg": "N/A", "ops": "N/A", "rs_pg": 4.5, "ra_pg": 4.5}
    try:
        data = _mlb_rest(f"/teams/{team_id}/stats",
                         {"stats": "season", "group": "hitting", "season": MLB_YEAR})
        splits = data.get("stats", [{}])
        if not splits: return empty
        stat = splits[0].get("splits", [{}])[-1].get("stat", {}) if splits else {}
        return {
            "avg": stat.get("avg",  "N/A"),
            "ops": stat.get("ops",  "N/A"),
            "rs_pg": float(stat.get("runsPerGame", 4.5)),
            "ra_pg": 4.5,  # need pitching stats for this
        }
    except Exception:
        return empty

def fetch_team_pitching_ra(team_id):
    """Return runs allowed per game from team pitching stats."""
    try:
        data = _mlb_rest(f"/teams/{team_id}/stats",
                         {"stats": "season", "group": "pitching", "season": MLB_YEAR})
        splits = data.get("stats", [{}])
        stat   = splits[0].get("splits", [{}])[-1].get("stat", {}) if splits else {}
        return float(stat.get("runsAllowed", 0)) / max(float(stat.get("gamesPlayed", 1)), 1)
    except Exception:
        return 4.5

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
        if i > 0:
            time.sleep(2)
        sport  = arb.get("sport", "")
        emoji  = _sport_emoji(sport)
        match  = arb["match"]
        profit = arb["profit"]
        pct    = arb["profit_pct"]
        gt     = _fmt_et(arb.get("game_time", ""))

        verdict = _verdict_line(pct)

        if arb.get("legs") == 3:
            total_stake = round(arb["stake_a"] + arb["stake_b"] + arb["stake_c"], 2)
            body = (
                f"{emoji} {match}\n"
                f"💰 Ganancia garantizada: ${profit} ({pct}%)\n"
                f"{_DIV}\n"
                f"🔵 ${arb['stake_a']:>8} → {arb['team_a']} @ {arb['odds_a']} — {arb['book_a']}\n"
                f"🤝 ${arb['stake_b']:>8} → Empate @ {arb['odds_b']} — {arb['book_b']}\n"
                f"🔴 ${arb['stake_c']:>8} → {arb['team_c']} @ {arb['odds_c']} — {arb['book_c']}\n"
                f"{_DIV}\n"
                f"💵 Total apostado: ${total_stake}\n"
                f"⏰ {gt}\n"
                f"{verdict}\n"
                f"{_DIV2}"
            )
        else:
            total_stake = round(arb["stake_a"] + arb["stake_b"], 2)
            body = (
                f"{emoji} {match}\n"
                f"💰 Ganancia garantizada: ${profit} ({pct}%)\n"
                f"{_DIV}\n"
                f"🔵 ${arb['stake_a']:>8} → {arb['team_a']} @ {arb['odds_a']} — {arb['book_a']}\n"
                f"🔴 ${arb['stake_b']:>8} → {arb['team_b']} @ {arb['odds_b']} — {arb['book_b']}\n"
                f"{_DIV}\n"
                f"💵 Total apostado: ${total_stake}\n"
                f"⏰ {gt}\n"
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

        rs_pg = float(h_stat.get("runsPerGame", 4.5))
        games_p = max(float(p_stat.get("gamesPlayed", 162)), 1)
        ra_pg   = float(p_stat.get("runsAllowed", 4.5 * games_p)) / games_p

        result = {"rs_pg": round(rs_pg, 2), "ra_pg": round(ra_pg, 2)}
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

# ── IMPROVEMENT 2: WORLD CUP 2026 LIVE FORM ───────────────────────────────────

def fetch_wc_team_form(team_name):
    """
    Fetch last ≤5 completed WC matches for a team from ESPN's free API.
    Returns {'goals_for': float, 'goals_against': float, 'matches': int} or None.
    Cached per team per calendar day.
    """
    today_str = datetime.now(CDT).strftime('%Y-%m-%d')
    ck = (team_name.lower(), today_str)
    if ck in _wc_form_cache:
        return _wc_form_cache[ck]

    goals_for:     list = []
    goals_against: list = []

    try:
        for days_back in range(1, 20):
            if len(goals_for) >= 5:
                break
            dt = (datetime.now(CDT) - timedelta(days=days_back)).strftime('%Y%m%d')
            url = (f'https://site.api.espn.com/apis/site/v2/sports/'
                   f'soccer/fifa.world/scoreboard?dates={dt}')
            resp = requests.get(url, timeout=8)
            if resp.status_code != 200:
                continue
            for event in resp.json().get('events', []):
                comp       = event.get('competitions', [{}])[0]
                completed  = comp.get('status', {}).get('type', {}).get('completed', False)
                if not completed:
                    continue
                competitors = comp.get('competitors', [])
                for c in competitors:
                    cname = c.get('team', {}).get('displayName', '')
                    if (team_name.lower() not in cname.lower() and
                            cname.lower() not in team_name.lower()):
                        continue
                    opp = next((x for x in competitors if x is not c), {})
                    goals_for.append(int(c.get('score', 0)))
                    goals_against.append(int(opp.get('score', 0)))
                    break   # found this team in this match
    except Exception:
        pass

    if not goals_for:
        _wc_form_cache[ck] = None
        return None

    res = {
        'goals_for':     round(sum(goals_for) / len(goals_for), 2),
        'goals_against': round(sum(goals_against) / len(goals_against), 2),
        'matches':       len(goals_for),
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
    """
    if wind is None or wind['speed'] <= 15:
        return 0.0, ''
    lbl = wind['label']
    spd = wind['speed']
    if lbl == 'OUT':
        return +0.8, f"Wind: {spd}mph OUT → +0.8 runs"
    if lbl == 'IN':
        return -0.8, f"Wind: {spd}mph IN  → -0.8 runs"
    return 0.0, f"Wind: {spd}mph CROSS → no adj"

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

        book_data = get_book_total(g)
        if not book_data:
            continue
        book_line, over_odds, under_odds, bookmaker = book_data

        # ── Project our total ──────────────────────────────────────────────────
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

            our_line = round(base_line + pitch_adj + w_adj, 1)
            extra = {
                "pitcher_home": f"{h_pname} (ERA {h_era:.2f})",
                "pitcher_away": f"{a_pname} (ERA {a_era:.2f})",
                "era_home":     h_era,
                "era_away":     a_era,
                "pitch_adj":    pitch_adj,
                "wind_info":    w_label or "Wind: N/A",
                "form_home":    "",
                "form_away":    "",
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
            extra = {
                "form_home":    form_note_h,
                "form_away":    form_note_a,
                "pitcher_home": "",
                "pitcher_away": "",
                "pitch_adj":    0.0,
                "wind_info":    "",
            }

        diff = our_line - book_line
        if abs(diff) < threshold:
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
            **extra,
        })

    return total_bets

def notify_totals(total_bets):
    global alerted_bets
    for b in total_bets:
        # Module 7: stake minimum filter
        if b.get("stake", 0) < MIN_STAKE:
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
        gt      = _fmt_et(b.get("time", ""))

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
            body = (
                f"{emoji} {b['match']}\n"
                f"⏰ Hoy {gt}\n"
                f"{_DIV}\n"
                f"🎯 APUESTA: {side} {line} carreras (Total)\n\n"
                f"💰 ${b['stake']} @ {b['odds']} — {b['bookmaker']}{bk_warn_tot}\n"
                f"{_DIV}\n"
                f"📊 POR QUÉ:\n"
                f"Modelo proyecta: {b['our_line']} carreras\n"
                f"El libro pone:   {line} carreras\n"
                f"Diferencia:      {b['edge']} carreras de edge\n\n"
                f"🔵 Pitcher local:  {ph_name} — {_era_label(ph_era)} (ERA {ph_era:.2f})\n"
                f"🔴 Pitcher visita: {pa_name} — {_era_label(pa_era)} (ERA {pa_era:.2f})\n"
                f"{wind_line}"
                f"{_DIV}\n"
                f"{action}\n"
                f"{_DIV2}"
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
            if form_h or form_a:
                form_block = (
                    f"\n📋 Forma local:   {form_h or 'N/A'}\n"
                    f"📋 Forma visita:  {form_a or 'N/A'}"
                )
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
                f"El libro pone:   {line} {unit}\n"
                f"Diferencia:      {b['edge']} {unit} de edge"
                f"{form_block}\n"
                f"{_DIV}\n"
                f"{action}\n"
                f"{_DIV2}"
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

    if game_starts_soon(commence, 60):
        return None

    candidates = []   # {label, true_prob, odds, book, ev_pct, kelly_pct, stake, safest}
    context    = {}

    h2h_odds    = _extract_h2h_best(game)
    spread_odds = _extract_spread_best(game)
    totals_data = get_book_total(game)

    # ── MLB ───────────────────────────────────────────────────────────────────
    if is_mlb:
        h_stats = fetch_team_run_stats(home)
        a_stats = fetch_team_run_stats(away)
        if h_stats is None or a_stats is None:
            return None

        LEAGUE_AVG = 4.5
        park       = MLB_PARK_FACTORS.get(home, 1.0)
        home_exp   = h_stats["rs_pg"] * (a_stats["ra_pg"] / LEAGUE_AVG) * park
        away_exp   = a_stats["rs_pg"] * (h_stats["ra_pg"] / LEAGUE_AVG) * park

        pitchers  = fetch_probable_pitchers_today()
        p_data    = _lookup_pitcher_data(home, away, pitchers)
        h_era     = p_data.get("home_era", 4.50)
        a_era     = p_data.get("away_era", 4.50)
        h_pname   = p_data.get("home_name", "TBD")
        a_pname   = p_data.get("away_name", "TBD")
        pitch_adj = pitcher_run_adjustment(h_era, a_era)

        park_city      = MLB_PARK_CITIES.get(home)
        wind           = fetch_wind(park_city[1], park_city[2]) if park_city else None
        w_adj, w_label = wind_run_adj(wind)

        half_adj = pitch_adj / 2
        home_exp = max(0.1, home_exp + half_adj + w_adj / 2)
        away_exp = max(0.1, away_exp + half_adj + w_adj / 2)

        p_home = pythagorean_win_prob(home_exp, away_exp)
        p_away = 1.0 - p_home

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
            if ev >= EV_MIN_PCT and r["stake"] > 0:
                candidates.append({"label": lbl, "true_prob": true_p, "odds": odds,
                                   "book": book, "ev_pct": round(ev, 1),
                                   "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # Totals
        if totals_data:
            book_line, over_odds, under_odds, bk_name = totals_data
            adj_total = home_exp + away_exp
            for side_label, p, odds in [
                (f"📈 OVER {book_line} carreras",  poisson_ou_prob(adj_total, book_line, True),  over_odds),
                (f"📉 UNDER {book_line} carreras", poisson_ou_prob(adj_total, book_line, False), under_odds),
            ]:
                ev = (p * odds - 1) * 100
                r  = kelly_stake(p, odds)
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
            if ev >= EV_MIN_PCT and r["stake"] > 0:
                candidates.append({"label": lbl, "true_prob": p_cover, "odds": odds,
                                   "book": book, "ev_pct": round(ev, 1),
                                   "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # Line movement flag
        h_cur = h2h_odds.get(home, (0,))[0]
        a_cur = h2h_odds.get(away, (0,))[0]
        moved_h, dir_h, dlt_h = detect_line_movement(game_id, home, h_cur, prev_map)
        moved_a, dir_a, dlt_a = detect_line_movement(game_id, away, a_cur, prev_map)

        # Module 4: injury check
        il_data = {}
        try:
            il_data = fetch_mlb_il(home, away)
        except Exception:
            pass

        # Module 6: home/away splits
        h_splits = {"home_rs": 0, "home_ra": 0, "home_wpct": 0}
        a_splits = {"away_rs": 0, "away_ra": 0, "away_wpct": 0}
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
        }

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
            if ev >= EV_MIN_PCT and r["stake"] > 0:
                candidates.append({"label": lbl, "true_prob": true_p, "odds": odds,
                                   "book": book, "ev_pct": round(ev, 1),
                                   "stake": r["stake"], "kelly_pct": r["kelly_pct"]})

        # Totals
        if totals_data:
            book_line, over_odds, under_odds, bk_name = totals_data
            exp_total = blend_h + blend_a
            for side_label, p, odds in [
                (f"📈 OVER {book_line} goles",  poisson_ou_prob(exp_total, book_line, True),  over_odds),
                (f"📉 UNDER {book_line} goles", poisson_ou_prob(exp_total, book_line, False), under_odds),
            ]:
                ev = (p * odds - 1) * 100
                r  = kelly_stake(p, odds)
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

        # Module 5: WC group standings
        wc_standings = {}
        try:
            wc_standings = fetch_wc_standings()
        except Exception:
            pass

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
            "wc_standings": wc_standings,  # Module 5
            "sform_h":   sform_h,          # Soccer S1
            "sform_a":   sform_a,          # Soccer S1
            "referee":   referee,          # Soccer S2
            "temp_label_s": t_label_s,     # Soccer S3
        }

    # Drop any pick whose true probability is below the minimum threshold
    candidates = [c for c in candidates if c["true_prob"] >= PROB_MIN]

    if not candidates:
        return None

    # Rank by EV%, keep top 3, tag safest (prob ≥ 60%)
    candidates.sort(key=lambda x: x["ev_pct"], reverse=True)
    top3 = candidates[:3]
    for c in top3:
        c["safest"] = c["true_prob"] >= 0.60

    return {
        "game_id":    game_id,
        "match":      f"{home} vs {away}",
        "time":       commence,
        "sport":      sport_key,
        "is_mlb":     is_mlb,
        "candidates": top3,
        "context":    context,
        "best_label": top3[0]["label"],
        "best_ev":    top3[0]["ev_pct"],
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
        analysis_key = f"{home}_{away}_analysis"
        if not _should_alert(analysis_key, edge=a["best_ev"]):
            continue
        if i > 0:
            time.sleep(2)

        gt  = _fmt_et(a["time"])
        ctx = a["context"]

        # Module 7: filter candidates below MIN_STAKE
        a["candidates"] = [c for c in a["candidates"] if c.get("stake", 0) >= MIN_STAKE]
        if not a["candidates"]:
            continue

        # Context block
        if is_mlb:
            ctx_lines = (
                f"🔵 Pitcher local:  {ctx['pitcher_home']}\n"
                f"🔴 Pitcher visita: {ctx['pitcher_away']}\n"
                f"📊 RS/RA local:    {ctx['rs_home']} / {ctx['ra_home']} por juego\n"
                f"📊 RS/RA visita:   {ctx['rs_away']} / {ctx['ra_away']} por juego\n"
                f"🏟️  Factor parque:  x{ctx['park_factor']:.2f}\n"
            )
            # MLB A1: pitcher recent form
            pf_h = ctx.get("pform_h")
            pf_a = ctx.get("pform_a")
            if pf_h:
                eras_str = ", ".join(f"{e:.1f}" for e in pf_h["eras"])
                ctx_lines += f"📈 {home_es} últ. 3: {eras_str} → {pf_h['trend']}\n"
            if pf_a:
                eras_str = ", ".join(f"{e:.1f}" for e in pf_a["eras"])
                ctx_lines += f"📈 {away_es} últ. 3: {eras_str} → {pf_a['trend']}\n"
            # MLB A2: umpire
            ump = ctx.get("umpire")
            if ump and ump.get("name"):
                tend_icon = "📈 OVER" if ump["tendency"] == "OVER" else ("📉 UNDER" if ump["tendency"] == "UNDER" else "➡️ NEUTRAL")
                ctx_lines += f"👨‍⚖️ Ump: {ump['name']} — {ump['zone']} → Favorece: {tend_icon}\n"
            # MLB A3: temperature
            if ctx.get("temp_label"):
                ctx_lines += f"{ctx['temp_label']}\n"
            if ctx.get("wind_info"):
                ctx_lines += f"💨 {ctx['wind_info']}\n"
            # Module 4: injury warnings
            for tname, il_list in ctx.get("il_data", {}).items():
                if il_list:
                    ctx_lines += f"⚠️ IL {tname}: {', '.join(il_list[:3])}\n"
            # Module 6: home/away splits
            hs = ctx.get("h_splits", {})
            as_ = ctx.get("a_splits", {})
            if hs.get("home_rs"):
                ctx_lines += (
                    f"🏠 {home_es} en casa: RS {hs['home_rs']} | RA {hs['home_ra']} | "
                    f"{hs['home_wpct']*100:.0f}% win\n"
                    f"🚗 {away_es} de visita: RS {as_['away_rs']} | RA {as_['away_ra']} | "
                    f"{as_['away_wpct']*100:.0f}% win\n"
                )
        else:
            sign = "+" if ctx["elo_diff"] >= 0 else ""
            ctx_lines = (
                f"🔵 ELO local:    {ctx['elo_home']} ({sign}{ctx['elo_diff']})\n"
                f"🤝 Prob. empate: {ctx['p_draw']}%\n"
            )
            # Show legacy form only if real data exists (not ELO-only fallback)
            if ctx.get("form_home"):
                ctx_lines += f"📊 Forma local:  {ctx['form_home']}\n"
                if ctx.get("conceded_home"):
                    ctx_lines += f"   Concedidos:   {ctx['conceded_home']} / partido\n"
            if ctx.get("form_away"):
                ctx_lines += f"📊 Forma visita: {ctx['form_away']}\n"
                if ctx.get("conceded_away"):
                    ctx_lines += f"   Concedidos:   {ctx['conceded_away']} / partido\n"
            # Soccer S1: last 3 match detail
            sf_h = ctx.get("sform_h")
            sf_a = ctx.get("sform_a")
            if sf_h:
                res_str = " ".join(sf_h["results"])
                ctx_lines += (
                    f"🔵 {home_es} últ. {sf_h['n']}:\n"
                    f"   ⚽ {sf_h['gf_pg']}/juego | 🛡️ {sf_h['ga_pg']} rec.\n"
                    f"   Forma: {res_str} {sf_h['emoji']}\n"
                )
            if sf_a:
                res_str = " ".join(sf_a["results"])
                ctx_lines += (
                    f"🔴 {away_es} últ. {sf_a['n']}:\n"
                    f"   ⚽ {sf_a['gf_pg']}/juego | 🛡️ {sf_a['ga_pg']} rec.\n"
                    f"   Forma: {res_str} {sf_a['emoji']}\n"
                )
            # Soccer S2: referee
            ref = ctx.get("referee")
            if ref and ref.get("name"):
                ctx_lines += (
                    f"🟨 Árbitro: {ref['name']}\n"
                    f"   → Promedio: {ref['goals_pg']:.1f} goles/partido\n"
                    f"   → Favorece: {ref['tendency']}\n"
                )
            # Soccer S3: temperature
            if ctx.get("temp_label_s"):
                ctx_lines += f"{ctx['temp_label_s']}\n"
            # Module 5: WC group urgency (enhanced)
            standings = ctx.get("wc_standings", {})
            if standings:
                for tname, tname_es in ((home, home_es), (away, away_es)):
                    urg = _wc_urgency_line(tname, standings)
                    if urg:
                        # Enhanced: detect must-win urgency
                        if "necesita" in urg.lower() or "eliminado" in urg.lower():
                            ctx_lines += (
                                f"🔴 URGENTE {tname_es}: necesitan ganar\n"
                                f"   → Atacarán desde el inicio\n"
                                f"   → Favorece OVER y ML rival (contraataque)\n"
                            )
                        else:
                            ctx_lines += f"📋 {urg}\n"

        if ctx.get("line_moved") and ctx.get("line_note"):
            ctx_lines += f"📉 {ctx['line_note']}\n"

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
            safe_tag   = " ✅ SEGURO" if c["safest"] else ""

            # Fix 5: flag suspiciously high EV (likely a soft/stale line)
            if c["ev_pct"] > 30:
                high_ev_flag = "⚠️ Edge muy alto — verificar línea antes de apostar\n"

            # Fix 2: book warning for risky books (applies to MLB and soccer)
            bk_warn_pick = _book_warning(c.get("book", ""))

            # Fix 3: omit Kelly% for soccer alerts
            if is_mlb:
                odds_line = (f"   💰 ${c['stake']} @ {c['odds']} "
                             f"({c['kelly_pct']}% Kelly) — {c['book']}{bk_warn_pick}\n")
            else:
                odds_line = (f"   💰 ${c['stake']} @ {c['odds']} "
                             f"— {c['book']}{bk_warn_pick}\n")

            picks_lines += (
                f"{rank_emoji} {c['label']} | EV +{c['ev_pct']}% → ${ev_d} por ${c['stake']}"
                f" | Prob {round(c['true_prob']*100):.0f}%{safe_tag}\n"
                f"{odds_line}"
            )

        # Verdict based on best pick
        verdict = _verdict_line(best["ev_pct"], best["true_prob"])

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
            f"{verdict}\n"
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
            f"⭐ ACCIÓN: Apostar {team} en el mejor libro disponible\n"
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
    "bovada", "bodog", "betmgm", "fanduel", "draftkings", "mybookie",
    "pointsbet", "caesars", "unibet", "williamhill", "william hill",
    "betonline.ag", "betonline",
}
RISKY_BOOKS = {
    "1xbet", "gtbets", "betfred", "smarkets", "ladbrokes", "betway",
}

def _book_warning(bookmaker):
    """Return warning line if bookmaker is risky, else empty string."""
    bk = (bookmaker or "").lower()
    if any(r in bk for r in RISKY_BOOKS):
        return ("\n⚠️ LIBRO RIESGOSO — limitan cuentas ganadoras. "
                "Busca línea similar en Bovada o BetOnline")
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

        checked = _load_results_checked()
        scores  = _fetch_mlb_scores_today() + _fetch_soccer_scores()

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

            checked[bkey] = result
            new_br = load_bankroll_state()["current"]
            icon  = "✅" if result == "W" else ("🤝" if result == "P" else "❌")
            verb  = "GANÓ" if result == "W" else ("PUSH" if result == "P" else "perdió")
            pl_s  = f"+${profit_loss:.2f}" if profit_loss >= 0 else f"-${abs(profit_loss):.2f}"
            body  = (
                f"{icon} {team} {verb} | {pl_s}\n"
                f"Bankroll: ${new_br:,.2f}\n"
                f"Partido: {match}\n"
                f"Resultado: {score['home']} {score['home_score']} – "
                f"{score['away_score']} {score['away']}"
            )
            ntfy_post(f"{icon} RESULTADO | {team} {verb} | {pl_s}", body, "high")
            print(f"  {icon} Resultado: {team} {verb} | {pl_s}")

        _save_results_checked(checked)

    except Exception as e:
        print(f"  ⚠️  check_results error: {e}")

# ── Module 4: MLB IL / injuries ───────────────────────────────────────────────
_injury_cache: dict = {}

def fetch_mlb_il(home, away):
    """Return {team_name: [player_names]} for IL players on both teams today."""
    today_str = datetime.now(ET).strftime("%Y-%m-%d")
    if today_str in _injury_cache:
        return _injury_cache[today_str]
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
    _injury_cache[today_str] = result
    return result

# ── Module 6: home/away splits ────────────────────────────────────────────────
_splits_cache: dict = {}

def fetch_mlb_home_away_splits(team_name):
    """Return home/away RS, RA, win% from MLB Stats API homeAndAway group."""
    if team_name in _splits_cache:
        return _splits_cache[team_name]
    empty = {"home_rs": 4.5, "home_ra": 4.5, "home_wpct": 0.500,
             "away_rs": 4.5, "away_ra": 4.5, "away_wpct": 0.500}
    try:
        if HAS_STATSAPI:
            teams = statsapi.lookup_team(team_name)
            tid   = teams[0]["id"] if teams else None
        else:
            data  = _mlb_rest("/teams", {"name": team_name, "sportId": 1})
            teams = data.get("teams", [])
            tid   = teams[0]["id"] if teams else None
        if tid is None:
            return empty
        data   = _mlb_rest(f"/teams/{tid}/stats", {
            "stats": "homeAndAway", "group": "hitting",
            "season": MLB_YEAR, "sportId": 1,
        })
        splits = (data.get("stats", [{}])[0].get("splits", [])
                  if data.get("stats") else [])
        result = dict(empty)
        for s in splits:
            loc  = s.get("split", {}).get("code", "")
            stat = s.get("stat", {})
            gp   = max(float(stat.get("gamesPlayed", 1) or 1), 1)
            runs = float(stat.get("runs",        0) or 0)
            ra   = float(stat.get("earnedRuns",  0) or 0)
            wins = float(stat.get("wins",        0) or 0)
            if loc == "H":
                result["home_rs"]   = round(runs / gp, 2)
                result["home_ra"]   = round(ra   / gp, 2)
                result["home_wpct"] = round(wins / gp, 3)
            elif loc == "A":
                result["away_rs"]   = round(runs / gp, 2)
                result["away_ra"]   = round(ra   / gp, 2)
                result["away_wpct"] = round(wins / gp, 3)
        _splits_cache[team_name] = result
        return result
    except Exception:
        return empty

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
    """
    ck = f"{home_team}_{game_date}"
    if ck in _umpire_cache:
        return _umpire_cache[ck]
    try:
        data = _mlb_rest("/schedule", {
            "sportId": 1, "date": game_date,
            "hydrate": "officials",
            "fields": "dates,games,gamePk,teams,home,teamName,officials,officialType,official,fullName",
        })
        if not data:
            return None
        for date_entry in data.get("dates", []):
            for game in date_entry.get("games", []):
                home_nm = (game.get("teams", {}).get("home", {})
                           .get("team", {}).get("teamName", ""))
                if home_nm.lower() not in home_team.lower() and home_team.lower() not in home_nm.lower():
                    continue
                for official in game.get("officials", []):
                    if official.get("officialType") == "Home Plate":
                        name      = official.get("official", {}).get("fullName", "")
                        tendency, zone = _UMPIRE_TENDENCIES.get(name, ("NEUTRAL", "zona normal"))
                        res = {"name": name, "tendency": tendency, "zone": zone}
                        _umpire_cache[ck] = res
                        return res
        return None
    except Exception:
        return None

# ── Soccer S1: Team recent form detail (last 3 matches) ───────────────────────
_soccer_recent_cache: dict = {}

def fetch_soccer_team_recent(team: str, sport_key: str) -> dict | None:
    """
    Last 3 completed matches via Odds API scores endpoint.
    Returns {gf_pg, ga_pg, results: ['W','D','L',...], emoji, n} or None.
    """
    ck = f"{team}_{sport_key}_{datetime.now().strftime('%Y-%m-%d')}"
    if ck in _soccer_recent_cache:
        return _soccer_recent_cache[ck]
    try:
        url = (f"https://api.the-odds-api.com/v4/sports/{sport_key}/scores/"
               f"?apiKey={API_KEY}&daysFrom=30&dateFormat=iso")
        r = requests.get(url, timeout=10)
        if r.status_code != 200:
            return None
        games = [g for g in r.json()
                 if (g.get("home_team") == team or g.get("away_team") == team)
                 and g.get("completed") is True]
        games.sort(key=lambda g: g.get("commence_time", ""), reverse=True)
        last3 = games[:3]
        if not last3:
            return None
        gf = ga = 0
        results = []
        for g in last3:
            is_home  = g["home_team"] == team
            sc_list  = g.get("scores") or []
            scores   = {s["name"]: int(s["score"]) for s in sc_list
                        if s.get("score") is not None}
            my_score  = scores.get(team, 0)
            opp       = g["away_team"] if is_home else g["home_team"]
            opp_score = scores.get(opp, 0)
            gf += my_score
            ga += opp_score
            if my_score > opp_score:   results.append("W")
            elif my_score == opp_score: results.append("D")
            else:                       results.append("L")
        n    = len(last3)
        wins = results.count("W")
        emoji = ("🔥" if wins == 3 else
                 "✅" if wins >= 2 else
                 "⚠️" if wins == 0 else "➡️")
        res = {"gf_pg": round(gf / n, 1), "ga_pg": round(ga / n, 1),
               "results": results, "emoji": emoji, "n": n}
        _soccer_recent_cache[ck] = res
        return res
    except Exception:
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

        today_s = datetime.now(ET).strftime("%d %b %Y")
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
            f"HOY:\n"
            f"⚾ {mlb_cnt} juegos MLB\n"
            f"⚽ Ver scan para fútbol/Mundial\n"
            f"🔍 Escaneando desde las 10 AM ET"
        )
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

        body = (
            f"📊 RESUMEN SEMANAL\n"
            f"{_DIV}\n"
            f"💰 Bankroll: ${current:,.2f} ({delta_s})\n"
            f"📈 ROI semana: {week_roi:+.1f}%  |  Total: {roi:+.1f}%\n"
            f"🏆 Semana: {week_wins}-{week_loss}  |  Total: {wins}-{losses}-{pushes}\n"
            f"{_DIV}\n"
            f"MEJOR BET: {best_s}\n"
            f"PEOR BET:  {worst_s}\n"
            f"{_DIV}\n"
            f"MEJOR DEPORTE: {best_sport}\n"
            f"MEJOR TIPO:    {best_type}\n"
            f"{_DIV}\n"
            f"PRÓXIMA SEMANA:\n"
            f"⚾ MLB activo  ⚽ Mundial en curso"
        )
        ntfy_post("📊 RESUMEN SEMANAL", body, "default")
        print("  📊 Resumen semanal ntfy enviado")
    except Exception as e:
        print(f"  ⚠️  send_weekly_summary error: {e}")

# ═══════════════════════════════════════════════════════════════════════════════
# CORE — ANALYSIS & NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════════════════════

def analyze(games, prev_map, new_map):
    bets        = []
    sharp_moves = []

    for g in games:
        game_id    = g.get("id", "")
        home, away = g["home_team"], g["away_team"]
        commence   = g.get("commence_time", "")

        if game_starts_soon(commence, 60):
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

        new_map[f"{game_id}_{home}"] = best_h
        new_map[f"{game_id}_{away}"] = best_a

        top3_h = _top3_from_book_list(book_list_h)   # Module 9
        top3_a = _top3_from_book_list(book_list_a)

        for team, prob, best_odd, side, bookmaker, bov_odds, top3 in [
            (home, fp_h, best_h, "HOME", best_bk_h, bov_odds_h, top3_h),
            (away, fp_a, best_a, "AWAY", best_bk_a, bov_odds_a, top3_a),
        ]:
            r = kelly_stake(prob, best_odd)
            if not r["has_value"] or r["edge"] < MIN_EDGE:
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

    return bets, sharp_moves

def notify_bets(new_bets):
    global alerted_bets
    if not new_bets:
        return

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
        gt      = _fmt_et(b.get("time", ""))
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
                f"Modelo → {elo_p}% de ganar\n"
                f"Libro  → {impl_pct}% implícito\n"
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
                f"Nuestro modelo: {elo_p}% | Libro: {impl_pct}% → Edge {b['edge']}%\n"
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

# ═══════════════════════════════════════════════════════════════════════════════
# CORE — MAIN SCAN
# ═══════════════════════════════════════════════════════════════════════════════

def run_scan():
    global daily_bets, lineup_scan_counter
    prev_map  = load_previous_odds()
    new_map   = {}
    all_bets   = []
    all_sharp  = []
    all_arbs   = []
    all_totals = []
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

            current_games_by_sport[sport_key] = games   # for CLV check

            bets, sharp_moves = analyze(games, prev_map, new_map)
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

            if arbs:
                print(f"  💰 {short} — {len(arbs)} arb opportunity(ies)")
                all_arbs.extend(arbs)

            if full_analyses:
                print(f"  🔍 {short} — {len(full_analyses)} full analysis(es)")
                notify_game_analysis(full_analyses, sport_key)

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
    if all_arbs:
        notify_arbitrage(all_arbs)

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

    while True:
        now_cdt = datetime.now(CDT)
        now_et  = datetime.now(ET)
        print(f"\n{'='*50}\n🕐 {now_cdt.strftime('%Y-%m-%d %H:%M CDT')}")

        check_midnight_reset()

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
